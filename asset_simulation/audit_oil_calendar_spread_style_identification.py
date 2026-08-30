"""Broad natural-event scan for calendar-spread PM style identification.

This diagnostic answers whether real deterministic market/forecast paths supply
sufficient observations to identify the three conditional dedicated style axes:
forecast-vs-visible-curve, curve continuation-vs-reversion, and holding patience.
It also reports component availability/magnitude so a lack of conflicts can be
distinguished from a degenerate visible-curve signal. It does not tune thresholds
and is not itself an acceptance gate.

Because a formal spread lifecycle scheduler does not yet exist, research-book
spread units are reset when the current Main/Adjacent-Next pair identity changes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .audit_oil_formal_account_calibration import _build_visible_path, _round_nested
from .audit_oil_calendar_spread_style_economic_acceptance import (
    _controlled_decision_with_reversal_guard,
)
from .audit_oil_calendar_spread_style_economics import _controlled_radar, _pair_ids
from .model.registry import sha256_json


IDENTIFICATION_VERSION = (
    "asset-simulation-oil-calendar-spread-style-identification-v0.1.2"
)
DEFAULT_DEVELOPMENT_SEEDS = tuple(range(16))
DEFAULT_VALIDATION_SEEDS = tuple(range(100, 116))
THRESHOLDS = (0.02, 0.05, 0.10, 0.15)


def _mean(values: Sequence[float]) -> float:
    sample = [float(value) for value in values]
    return 0.0 if not sample else sum(sample) / len(sample)


def _percentile(values: Sequence[float], p: float) -> float:
    sample = sorted(float(value) for value in values)
    if not sample:
        return 0.0
    if len(sample) == 1:
        return sample[0]
    x = (len(sample) - 1) * float(p)
    lo = int(x)
    hi = min(len(sample) - 1, lo + 1)
    w = x - lo
    return sample[lo] * (1.0 - w) + sample[hi] * w


def _scan_partition(seeds: Sequence[int], years: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    pair_reset_count = 0
    for seed in map(int, seeds):
        path = _build_visible_path(seed, years)
        current_units = 0
        previous_pair: tuple[str, str] | None = None
        for turn_index, item in enumerate(path):
            current_pair = _pair_ids(item["start_market"])
            if previous_pair is not None and current_pair != previous_pair:
                current_units = 0
                pair_reset_count += 1
            decision = _controlled_decision_with_reversal_guard(
                item["start_market"],
                item["forecast"],
                current_spread_units=current_units,
                dedicated_radar=_controlled_radar("forecast_vs_visible_curve", 50.0),
                authorized_strategy_capital_usd=10_000_000.0,
            )
            signal = decision["signal"]
            forecast_signal = float(signal["forecast_signal"])
            visible_signal = float(signal["visible_curve_signal"])
            momentum_signal = float(signal["curve_momentum_signal"])
            reversion_signal = float(signal["curve_mean_reversion_signal"])
            ideal = int(decision["ideal_target_spread_units"])
            current = int(decision["current_spread_units"])
            rows.append(
                {
                    "seed": seed,
                    "turn_index": turn_index,
                    "forecast_signal": forecast_signal,
                    "visible_curve_signal": visible_signal,
                    "momentum_signal": momentum_signal,
                    "reversion_signal": reversion_signal,
                    "mean_reversion_available": bool(signal["mean_reversion_available"]),
                    "historical_observation_count": int(signal["historical_observation_count"]),
                    "forecast_visible_opposite": forecast_signal * visible_signal < 0.0,
                    "momentum_reversion_opposite": momentum_signal * reversion_signal < 0.0,
                    "forecast_visible_min_strength": min(abs(forecast_signal), abs(visible_signal)),
                    "momentum_reversion_min_strength": min(abs(momentum_signal), abs(reversion_signal)),
                    "same_direction_shrink": (
                        current * ideal > 0 and abs(ideal) < abs(current)
                    ),
                }
            )
            current_units = int(decision["target_spread_units"])
            previous_pair = current_pair

    forecast_strengths = [
        row["forecast_visible_min_strength"]
        for row in rows
        if row["forecast_visible_opposite"]
    ]
    curve_strengths = [
        row["momentum_reversion_min_strength"]
        for row in rows
        if row["momentum_reversion_opposite"]
    ]
    threshold_counts = {
        str(threshold): {
            "forecast_vs_visible_curve": sum(
                row["forecast_visible_opposite"]
                and row["forecast_visible_min_strength"] >= threshold
                for row in rows
            ),
            "momentum_vs_reversion": sum(
                row["momentum_reversion_opposite"]
                and row["momentum_reversion_min_strength"] >= threshold
                for row in rows
            ),
        }
        for threshold in THRESHOLDS
    }
    component_diagnostics = {
        "mean_abs_forecast_signal": _mean([abs(row["forecast_signal"]) for row in rows]),
        "mean_abs_visible_curve_signal": _mean([abs(row["visible_curve_signal"]) for row in rows]),
        "mean_abs_momentum_signal": _mean([abs(row["momentum_signal"]) for row in rows]),
        "mean_abs_mean_reversion_signal": _mean([abs(row["reversion_signal"]) for row in rows]),
        "nonzero_forecast_signal_count": sum(abs(row["forecast_signal"]) > 1e-12 for row in rows),
        "nonzero_visible_curve_signal_count": sum(abs(row["visible_curve_signal"]) > 1e-12 for row in rows),
        "nonzero_momentum_signal_count": sum(abs(row["momentum_signal"]) > 1e-12 for row in rows),
        "nonzero_mean_reversion_signal_count": sum(abs(row["reversion_signal"]) > 1e-12 for row in rows),
        "mean_reversion_available_count": sum(row["mean_reversion_available"] for row in rows),
        "mean_historical_observation_count": _mean(
            [row["historical_observation_count"] for row in rows]
        ),
        "minimum_historical_observation_count": min(
            (row["historical_observation_count"] for row in rows), default=0
        ),
        "maximum_historical_observation_count": max(
            (row["historical_observation_count"] for row in rows), default=0
        ),
    }
    return {
        "seed_count": len(tuple(seeds)),
        "turn_observations": len(rows),
        "pair_identity_reset_count": pair_reset_count,
        "forecast_visible_opposite_any_strength": len(forecast_strengths),
        "momentum_reversion_opposite_any_strength": len(curve_strengths),
        "same_direction_shrink_count": sum(row["same_direction_shrink"] for row in rows),
        "component_diagnostics": component_diagnostics,
        "threshold_counts": threshold_counts,
        "forecast_visible_min_strength_when_opposite": {
            "median": _percentile(forecast_strengths, 0.50),
            "p90": _percentile(forecast_strengths, 0.90),
            "maximum": max(forecast_strengths, default=0.0),
        },
        "momentum_reversion_min_strength_when_opposite": {
            "median": _percentile(curve_strengths, 0.50),
            "p90": _percentile(curve_strengths, 0.90),
            "maximum": max(curve_strengths, default=0.0),
        },
    }


def build_oil_calendar_spread_style_identification_scan(
    *,
    development_seeds: Sequence[int] = DEFAULT_DEVELOPMENT_SEEDS,
    validation_seeds: Sequence[int] = DEFAULT_VALIDATION_SEEDS,
    horizon_years: int = 2,
) -> dict[str, Any]:
    if horizon_years <= 0:
        raise ValueError("identification scan horizon must be positive")
    if not development_seeds or not validation_seeds:
        raise ValueError("identification scan needs both partitions")
    result = {
        "identification_version": IDENTIFICATION_VERSION,
        "horizon_years": int(horizon_years),
        "thresholds": list(THRESHOLDS),
        "development_seeds": list(map(int, development_seeds)),
        "validation_seeds": list(map(int, validation_seeds)),
        "partitions": {
            "development": _scan_partition(development_seeds, horizon_years),
            "validation": _scan_partition(validation_seeds, horizon_years),
        },
        "interpretation": {
            "natural_frequency_only": True,
            "thresholds_are_diagnostic_not_tuned": True,
            "component_availability_is_reported": True,
            "pair_identity_change_resets_research_book": True,
            "no_gate_is_relaxed_by_this_scan": True,
        },
    }
    result["result_hash"] = sha256_json(result)
    return _round_nested(result)


def _parse_seed_list(value: str) -> tuple[int, ...]:
    items = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not items:
        raise argparse.ArgumentTypeError("seed list must not be empty")
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-seeds", type=_parse_seed_list, default=DEFAULT_DEVELOPMENT_SEEDS)
    parser.add_argument("--validation-seeds", type=_parse_seed_list, default=DEFAULT_VALIDATION_SEEDS)
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_oil_calendar_spread_style_identification_scan(
        development_seeds=args.development_seeds,
        validation_seeds=args.validation_seeds,
        horizon_years=args.years,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is None:
        print(text)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(args.output)


if __name__ == "__main__":
    main()
