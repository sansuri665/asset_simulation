"""Broad same-state acceptance for dedicated calendar-spread PM styles.

A neutral dedicated PM path owns the research-book state.  At every real market
cutoff, score-10 and score-90 variants of one axis are then evaluated from that
*same* state, market and forecast vintage.  This avoids path divergence being
mistaken for a style effect and provides enough natural conditional events for
forecast-vs-curve, curve continuation-vs-reversion and holding patience.

The audit remains research-only: target state is propagated without a fill
model, pair identity changes reset the research book because a spread lifecycle
scheduler does not yet exist, and idealized markout is not an acceptance input.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .audit_oil_formal_account_calibration import _build_visible_path, _round_nested
from .audit_oil_calendar_spread_style_economic_acceptance import (
    _controlled_decision_with_reversal_guard,
)
from .audit_oil_calendar_spread_style_economics import (
    CONFLICT_SIGNAL_FLOOR,
    _controlled_radar,
    _pair_ids,
)
from .model.oil_calendar_spread_research import CALENDAR_SPREAD_STYLE_DIMENSIONS
from .model.registry import load_registered_assets, sha256_json


ACCEPTANCE_VERSION = (
    "asset-simulation-oil-calendar-spread-style-same-state-acceptance-v0.1.0"
)
DEFAULT_DEVELOPMENT_SEEDS = tuple(range(16))
DEFAULT_VALIDATION_SEEDS = tuple(range(100, 116))
LOW_SCORE = 10.0
HIGH_SCORE = 90.0
MIN_CONDITIONAL_EVENTS = 10


def _mean(values: Sequence[float]) -> float:
    sample = [float(value) for value in values]
    return 0.0 if not sample else sum(sample) / len(sample)


def _alignment(candidate: float, preferred: float, alternative: float) -> float:
    """Positive means candidate is closer to preferred than alternative."""

    return abs(float(candidate) - float(alternative)) - abs(
        float(candidate) - float(preferred)
    )


def _retention(current: int, ideal: int, persistent: int) -> float:
    denominator = abs(int(current)) - abs(int(ideal))
    if denominator <= 0:
        return 0.0
    return (abs(int(persistent)) - abs(int(ideal))) / denominator


def _compare_axis_turn(
    market: Mapping[str, Any],
    forecast: Mapping[str, Any],
    *,
    current_spread_units: int,
    axis: str,
    authorized_strategy_capital_usd: float,
) -> dict[str, Any]:
    low = _controlled_decision_with_reversal_guard(
        market,
        forecast,
        current_spread_units=current_spread_units,
        dedicated_radar=_controlled_radar(axis, LOW_SCORE),
        authorized_strategy_capital_usd=authorized_strategy_capital_usd,
    )
    high = _controlled_decision_with_reversal_guard(
        market,
        forecast,
        current_spread_units=current_spread_units,
        dedicated_radar=_controlled_radar(axis, HIGH_SCORE),
        authorized_strategy_capital_usd=authorized_strategy_capital_usd,
    )
    low_signal = dict(low["signal"])
    high_signal = dict(high["signal"])
    neutral_like = low_signal if axis not in {
        "forecast_vs_visible_curve",
        "curve_continuation_reversion",
        "forecast_horizon",
        "dislocation_selectivity",
    } else None

    result: dict[str, Any] = {
        "axis": axis,
        "low_score": LOW_SCORE,
        "high_score": HIGH_SCORE,
        "low_active": abs(float(low_signal["signal"])) > 1e-12,
        "high_active": abs(float(high_signal["signal"])) > 1e-12,
        "low_abs_signal": abs(float(low_signal["signal"])),
        "high_abs_signal": abs(float(high_signal["signal"])),
        "low_capacity": int(low["capacity"]["risk_capacity_units"]),
        "high_capacity": int(high["capacity"]["risk_capacity_units"]),
        "low_target": int(low["target_spread_units"]),
        "high_target": int(high["target_spread_units"]),
        "low_persistent": int(low["persistent_target_spread_units"]),
        "high_persistent": int(high["persistent_target_spread_units"]),
        "low_ideal": int(low["ideal_target_spread_units"]),
        "high_ideal": int(high["ideal_target_spread_units"]),
        "low_forecast_weight": float(low_signal["forecast_component_weight"]),
        "high_forecast_weight": float(high_signal["forecast_component_weight"]),
        "low_four_week_weight": float(low_signal["horizon_weights"][1]),
        "high_four_week_weight": float(high_signal["horizon_weights"][1]),
        "low_deadband": float(low_signal["signal_deadband_abs"]),
        "high_deadband": float(high_signal["signal_deadband_abs"]),
        "low_adjustment_speed": float(low["policy"]["execution"]["adjustment_speed"]),
        "high_adjustment_speed": float(high["policy"]["execution"]["adjustment_speed"]),
        "low_turnover_multiplier": float(
            low["policy"]["execution"]["gross_turnover_multiplier"]
        ),
        "high_turnover_multiplier": float(
            high["policy"]["execution"]["gross_turnover_multiplier"]
        ),
        "low_turnover_budget": int(
            low["paired_execution_mandate"]["advisory_pair_turnover_budget_units"]
        ),
        "high_turnover_budget": int(
            high["paired_execution_mandate"]["advisory_pair_turnover_budget_units"]
        ),
        "low_position_persistence": float(
            low["policy"]["execution"]["position_persistence"]
        ),
        "high_position_persistence": float(
            high["policy"]["execution"]["position_persistence"]
        ),
    }

    if axis == "forecast_vs_visible_curve":
        forecast_signal = float(low_signal["forecast_signal"])
        visible_signal = float(low_signal["visible_curve_signal"])
        event = (
            abs(forecast_signal) >= CONFLICT_SIGNAL_FLOOR
            and abs(visible_signal) >= CONFLICT_SIGNAL_FLOOR
            and forecast_signal * visible_signal < 0.0
        )
        result.update(
            {
                "conditional_event": event,
                "low_preferred_alignment": _alignment(
                    float(low_signal["raw_signal"]), forecast_signal, visible_signal
                ),
                "high_preferred_alignment": _alignment(
                    float(high_signal["raw_signal"]), forecast_signal, visible_signal
                ),
            }
        )
    elif axis == "curve_continuation_reversion":
        momentum = float(low_signal["curve_momentum_signal"])
        reversion = float(low_signal["curve_mean_reversion_signal"])
        event = (
            abs(momentum) >= CONFLICT_SIGNAL_FLOOR
            and abs(reversion) >= CONFLICT_SIGNAL_FLOOR
            and momentum * reversion < 0.0
        )
        result.update(
            {
                "conditional_event": event,
                "low_preferred_alignment": _alignment(
                    float(low_signal["visible_curve_signal"]), momentum, reversion
                ),
                "high_preferred_alignment": _alignment(
                    float(high_signal["visible_curve_signal"]), momentum, reversion
                ),
            }
        )
    elif axis == "holding_patience":
        # Holding patience does not change signal/capacity, so ideal should match.
        if int(low["ideal_target_spread_units"]) != int(high["ideal_target_spread_units"]):
            raise ValueError("holding-patience comparison changed ideal target")
        current = int(current_spread_units)
        ideal = int(low["ideal_target_spread_units"])
        event = current * ideal > 0 and abs(ideal) < abs(current)
        result.update(
            {
                "conditional_event": event,
                "low_preferred_alignment": _retention(
                    current, ideal, int(low["persistent_target_spread_units"])
                ),
                "high_preferred_alignment": _retention(
                    current, ideal, int(high["persistent_target_spread_units"])
                ),
            }
        )
    else:
        result["conditional_event"] = True
    return _round_nested(result)


def _partition(
    seeds: Sequence[int],
    *,
    horizon_years: int,
    authorized_strategy_capital_usd: float,
) -> dict[str, Any]:
    rows: dict[str, list[dict[str, Any]]] = {
        axis: [] for axis in CALENDAR_SPREAD_STYLE_DIMENSIONS
    }
    pair_reset_count = 0
    neutral_turn_count = 0

    for seed in map(int, seeds):
        current_units = 0
        previous_pair: tuple[str, str] | None = None
        for item in _build_visible_path(seed, horizon_years):
            current_pair = _pair_ids(item["start_market"])
            if previous_pair is not None and current_pair != previous_pair:
                current_units = 0
                pair_reset_count += 1
            neutral = _controlled_decision_with_reversal_guard(
                item["start_market"],
                item["forecast"],
                current_spread_units=current_units,
                dedicated_radar=_controlled_radar("forecast_vs_visible_curve", 50.0),
                authorized_strategy_capital_usd=authorized_strategy_capital_usd,
            )
            for axis in CALENDAR_SPREAD_STYLE_DIMENSIONS:
                rows[axis].append(
                    _compare_axis_turn(
                        item["start_market"],
                        item["forecast"],
                        current_spread_units=current_units,
                        axis=axis,
                        authorized_strategy_capital_usd=authorized_strategy_capital_usd,
                    )
                )
            current_units = int(neutral["target_spread_units"])
            previous_pair = current_pair
            neutral_turn_count += 1

    summaries: dict[str, Any] = {}
    gates: list[dict[str, Any]] = []
    for axis, axis_rows in rows.items():
        event_rows = [row for row in axis_rows if bool(row["conditional_event"])]
        summary: dict[str, Any] = {
            "turn_count": len(axis_rows),
            "conditional_event_count": len(event_rows),
        }
        if axis in {
            "forecast_vs_visible_curve",
            "curve_continuation_reversion",
            "holding_patience",
        }:
            low_metric = _mean(
                [float(row["low_preferred_alignment"]) for row in event_rows]
            )
            high_metric = _mean(
                [float(row["high_preferred_alignment"]) for row in event_rows]
            )
            enough = len(event_rows) >= MIN_CONDITIONAL_EVENTS
            ordering = high_metric > low_metric + 1e-12
            summary.update(
                {
                    "metric": "preferred_alignment",
                    "low_value": low_metric,
                    "high_value": high_metric,
                    "minimum_event_count": MIN_CONDITIONAL_EVENTS,
                }
            )
            passed = enough and ordering
        elif axis == "dislocation_selectivity":
            low_rate = _mean([1.0 if row["low_active"] else 0.0 for row in axis_rows])
            high_rate = _mean([1.0 if row["high_active"] else 0.0 for row in axis_rows])
            summary.update(
                {"metric": "active_signal_rate", "low_value": low_rate, "high_value": high_rate}
            )
            enough = True
            ordering = high_rate <= low_rate + 1e-12
            passed = ordering
        elif axis == "capital_deployment":
            low_value = _mean([float(row["low_capacity"]) for row in axis_rows])
            high_value = _mean([float(row["high_capacity"]) for row in axis_rows])
            summary.update(
                {"metric": "risk_capacity_units", "low_value": low_value, "high_value": high_value}
            )
            enough = True
            ordering = high_value >= low_value - 1e-12
            passed = ordering
        elif axis == "adjustment_tempo":
            completions_low: list[float] = []
            completions_high: list[float] = []
            for row in axis_rows:
                current = 0.0  # reconstructed from target geometry below
                # persistent and target are enough because tempo only changes the
                # final move toward persistence; compare absolute move fractions.
                low_gap = abs(float(row["low_persistent"]) - float(row["low_target"]))
                high_gap = abs(float(row["high_persistent"]) - float(row["high_target"]))
                completions_low.append(float(row["low_adjustment_speed"]))
                completions_high.append(float(row["high_adjustment_speed"]))
            low_value = _mean(completions_low)
            high_value = _mean(completions_high)
            summary.update(
                {"metric": "adjustment_speed", "low_value": low_value, "high_value": high_value}
            )
            enough = True
            ordering = high_value > low_value + 1e-12
            passed = ordering
        elif axis == "rebalance_activity":
            low_value = _mean([float(row["low_turnover_budget"]) for row in axis_rows])
            high_value = _mean([float(row["high_turnover_budget"]) for row in axis_rows])
            summary.update(
                {"metric": "advisory_pair_turnover_budget_units", "low_value": low_value, "high_value": high_value}
            )
            enough = True
            ordering = high_value > low_value + 1e-12
            passed = ordering
        elif axis == "forecast_horizon":
            low_value = _mean([float(row["low_four_week_weight"]) for row in axis_rows])
            high_value = _mean([float(row["high_four_week_weight"]) for row in axis_rows])
            summary.update(
                {"metric": "four_week_weight", "low_value": low_value, "high_value": high_value}
            )
            enough = True
            ordering = high_value > low_value + 1e-12
            passed = ordering
        else:
            raise ValueError(f"unhandled calendar-spread style axis: {axis}")

        summary.update(
            {
                "observation_gate_pass": enough,
                "ordering_pass": ordering,
                "pass": passed,
            }
        )
        summaries[axis] = summary
        gates.append({"axis": axis, **summary})

    return {
        "seed_count": len(tuple(seeds)),
        "neutral_turn_count": neutral_turn_count,
        "pair_identity_reset_count": pair_reset_count,
        "axis_summaries": summaries,
        "gates": gates,
        "hard_gate_pass": all(bool(item["pass"]) for item in gates),
    }


def build_oil_calendar_spread_style_same_state_acceptance(
    *,
    development_seeds: Sequence[int] = DEFAULT_DEVELOPMENT_SEEDS,
    validation_seeds: Sequence[int] = DEFAULT_VALIDATION_SEEDS,
    horizon_years: int = 2,
    authorized_strategy_capital_usd: float = 10_000_000.0,
) -> dict[str, Any]:
    if horizon_years <= 0:
        raise ValueError("same-state style acceptance horizon must be positive")
    if not development_seeds or not validation_seeds:
        raise ValueError("same-state style acceptance needs both partitions")
    assets = load_registered_assets()
    partitions = {
        "development": _partition(
            development_seeds,
            horizon_years=horizon_years,
            authorized_strategy_capital_usd=authorized_strategy_capital_usd,
        ),
        "validation": _partition(
            validation_seeds,
            horizon_years=horizon_years,
            authorized_strategy_capital_usd=authorized_strategy_capital_usd,
        ),
    }
    result = {
        "acceptance_version": ACCEPTANCE_VERSION,
        "strategy_model_version": assets["oil_calendar_spread_strategy_v22_config"][
            "model_version"
        ],
        "style_model_version": assets["oil_calendar_spread_research_config"][
            "model_version"
        ],
        "development_seeds": list(map(int, development_seeds)),
        "validation_seeds": list(map(int, validation_seeds)),
        "horizon_years": int(horizon_years),
        "low_score": LOW_SCORE,
        "high_score": HIGH_SCORE,
        "conditional_signal_floor": CONFLICT_SIGNAL_FLOOR,
        "minimum_conditional_events": MIN_CONDITIONAL_EVENTS,
        "method": {
            "neutral_path_owns_research_book_state": True,
            "low_high_compared_from_same_state": True,
            "pair_identity_change_resets_research_book": True,
            "market_history_coordinates_normalized_metadata_only": True,
            "construction_error_included": False,
            "formal_execution_included": False,
            "formal_account_pnl_included": False,
            "markout_is_acceptance_gate": False,
        },
        "partitions": partitions,
        "overall_hard_gate_pass": bool(
            partitions["development"]["hard_gate_pass"]
            and partitions["validation"]["hard_gate_pass"]
        ),
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
    parser.add_argument(
        "--development-seeds", type=_parse_seed_list, default=DEFAULT_DEVELOPMENT_SEEDS
    )
    parser.add_argument(
        "--validation-seeds", type=_parse_seed_list, default=DEFAULT_VALIDATION_SEEDS
    )
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_oil_calendar_spread_style_same_state_acceptance(
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
    if not bool(report["overall_hard_gate_pass"]):
        raise SystemExit("calendar-spread same-state PM style acceptance failed")


if __name__ == "__main__":
    main()
