"""Broad same-state acceptance for dedicated calendar-spread PM styles.

A neutral dedicated PM path owns the research-book state. At every real market
cutoff, score-10 and score-90 variants of one axis are compared against the same
visible signal primitives and the same current spread position. This prevents
path divergence from masquerading as a style effect.

Only capital deployment re-runs strategy capacity because it genuinely changes
the deployable capital budget. The other seven axes are evaluated directly from
one neutral reference decision. Pair identity changes reset the research book
because a formal spread lifecycle scheduler does not yet exist.
"""

from __future__ import annotations

import argparse
import json
import math
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
    _runtime_bundle,
)
from .model.oil_calendar_spread_research import CALENDAR_SPREAD_STYLE_DIMENSIONS
from .model.oil_calendar_spread_strategy import (
    _apply_spread_position_persistence,
    _responsive_target,
)
from .model.registry import load_registered_assets, sha256_json


ACCEPTANCE_VERSION = (
    "asset-simulation-oil-calendar-spread-style-same-state-acceptance-v0.2.0"
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
    return abs(float(candidate) - float(alternative)) - abs(
        float(candidate) - float(preferred)
    )


def _final_signal(raw_signal: float, deadband: float) -> float:
    raw = float(raw_signal)
    band = float(deadband)
    if abs(raw) <= band:
        return 0.0
    return math.copysign((abs(raw) - band) / max(1e-9, 1.0 - band), raw)


def _retention(current: int, ideal: int, persistent: int) -> float:
    denominator = abs(int(current)) - abs(int(ideal))
    if denominator <= 0:
        return 0.0
    return (abs(int(persistent)) - abs(int(ideal))) / denominator


def _policy(axis: str, score: float) -> dict[str, Any]:
    _, _, policy = _runtime_bundle(_controlled_radar(axis, score))
    return policy


def _compare_from_neutral(
    neutral: Mapping[str, Any],
    *,
    axis: str,
    current_spread_units: int,
    low_policy: Mapping[str, Any],
    high_policy: Mapping[str, Any],
) -> dict[str, Any]:
    signal = dict(neutral["signal"])
    current = int(current_spread_units)
    capacity = int(neutral["capacity"]["risk_capacity_units"])
    neutral_ideal = int(neutral["ideal_target_spread_units"])
    neutral_persistent = int(neutral["persistent_target_spread_units"])
    result: dict[str, Any] = {"axis": axis, "conditional_event": True}

    if axis == "forecast_vs_visible_curve":
        forecast = float(signal["forecast_signal"])
        visible = float(signal["visible_curve_signal"])
        event = (
            abs(forecast) >= CONFLICT_SIGNAL_FLOOR
            and abs(visible) >= CONFLICT_SIGNAL_FLOOR
            and forecast * visible < 0.0
        )
        low_weight = float(low_policy["signal"]["forecast_component_weight"])
        high_weight = float(high_policy["signal"]["forecast_component_weight"])
        low_raw = low_weight * forecast + (1.0 - low_weight) * visible
        high_raw = high_weight * forecast + (1.0 - high_weight) * visible
        result.update(
            {
                "conditional_event": event,
                "low_value": _alignment(low_raw, forecast, visible),
                "high_value": _alignment(high_raw, forecast, visible),
            }
        )
    elif axis == "curve_continuation_reversion":
        momentum = float(signal["curve_momentum_signal"])
        reversion = float(signal["curve_mean_reversion_signal"])
        event = (
            abs(momentum) >= CONFLICT_SIGNAL_FLOOR
            and abs(reversion) >= CONFLICT_SIGNAL_FLOOR
            and momentum * reversion < 0.0
        )
        low_cont = float(low_policy["signal"]["continuation_weight"])
        high_cont = float(high_policy["signal"]["continuation_weight"])
        low_visible = low_cont * momentum + (1.0 - low_cont) * reversion
        high_visible = high_cont * momentum + (1.0 - high_cont) * reversion
        result.update(
            {
                "conditional_event": event,
                "low_value": _alignment(low_visible, momentum, reversion),
                "high_value": _alignment(high_visible, momentum, reversion),
            }
        )
    elif axis == "dislocation_selectivity":
        raw = float(signal["raw_signal"])
        low_final = _final_signal(raw, float(low_policy["signal"]["signal_deadband_abs"]))
        high_final = _final_signal(raw, float(high_policy["signal"]["signal_deadband_abs"]))
        result.update(
            {
                "low_value": 1.0 if abs(low_final) > 1e-12 else 0.0,
                "high_value": 1.0 if abs(high_final) > 1e-12 else 0.0,
            }
        )
    elif axis == "adjustment_tempo":
        low_target = _responsive_target(
            current_units=current,
            target_units=neutral_persistent,
            adjustment_speed=float(low_policy["execution"]["adjustment_speed"]),
            capacity_units=capacity,
        )
        high_target = _responsive_target(
            current_units=current,
            target_units=neutral_persistent,
            adjustment_speed=float(high_policy["execution"]["adjustment_speed"]),
            capacity_units=capacity,
        )
        gap = abs(neutral_persistent - current)
        result.update(
            {
                "low_value": 0.0 if gap == 0 else abs(low_target - current) / gap,
                "high_value": 0.0 if gap == 0 else abs(high_target - current) / gap,
            }
        )
    elif axis == "rebalance_activity":
        reference = max(abs(current), abs(int(neutral["target_spread_units"])), 1)
        result.update(
            {
                "low_value": math.floor(
                    reference
                    * float(low_policy["execution"]["gross_turnover_multiplier"])
                ),
                "high_value": math.floor(
                    reference
                    * float(high_policy["execution"]["gross_turnover_multiplier"])
                ),
            }
        )
    elif axis == "holding_patience":
        event = current * neutral_ideal > 0 and abs(neutral_ideal) < abs(current)
        low_persistent = _apply_spread_position_persistence(
            current_spread_units=current,
            proposed_target_units=neutral_ideal,
            capacity_units=capacity,
            position_persistence=float(low_policy["execution"]["position_persistence"]),
        )
        high_persistent = _apply_spread_position_persistence(
            current_spread_units=current,
            proposed_target_units=neutral_ideal,
            capacity_units=capacity,
            position_persistence=float(high_policy["execution"]["position_persistence"]),
        )
        result.update(
            {
                "conditional_event": event,
                "low_value": _retention(current, neutral_ideal, low_persistent),
                "high_value": _retention(current, neutral_ideal, high_persistent),
            }
        )
    elif axis == "forecast_horizon":
        result.update(
            {
                "low_value": float(low_policy["signal"]["horizon_weights"][1]),
                "high_value": float(high_policy["signal"]["horizon_weights"][1]),
            }
        )
    else:
        raise ValueError(f"axis requires full capacity comparison: {axis}")
    return _round_nested(result)


def _partition(
    seeds: Sequence[int],
    *,
    horizon_years: int,
    authorized_strategy_capital_usd: float,
) -> dict[str, Any]:
    policies = {
        axis: {"low": _policy(axis, LOW_SCORE), "high": _policy(axis, HIGH_SCORE)}
        for axis in CALENDAR_SPREAD_STYLE_DIMENSIONS
        if axis != "capital_deployment"
    }
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
                if axis == "capital_deployment":
                    low = _controlled_decision_with_reversal_guard(
                        item["start_market"],
                        item["forecast"],
                        current_spread_units=current_units,
                        dedicated_radar=_controlled_radar(axis, LOW_SCORE),
                        authorized_strategy_capital_usd=authorized_strategy_capital_usd,
                    )
                    high = _controlled_decision_with_reversal_guard(
                        item["start_market"],
                        item["forecast"],
                        current_spread_units=current_units,
                        dedicated_radar=_controlled_radar(axis, HIGH_SCORE),
                        authorized_strategy_capital_usd=authorized_strategy_capital_usd,
                    )
                    rows[axis].append(
                        {
                            "axis": axis,
                            "conditional_event": True,
                            "low_value": int(low["capacity"]["risk_capacity_units"]),
                            "high_value": int(high["capacity"]["risk_capacity_units"]),
                        }
                    )
                else:
                    rows[axis].append(
                        _compare_from_neutral(
                            neutral,
                            axis=axis,
                            current_spread_units=current_units,
                            low_policy=policies[axis]["low"],
                            high_policy=policies[axis]["high"],
                        )
                    )

            current_units = int(neutral["target_spread_units"])
            previous_pair = current_pair
            neutral_turn_count += 1

    summaries: dict[str, Any] = {}
    gates: list[dict[str, Any]] = []
    conditional_axes = {
        "forecast_vs_visible_curve",
        "curve_continuation_reversion",
        "holding_patience",
    }
    for axis, axis_rows in rows.items():
        event_rows = [row for row in axis_rows if bool(row["conditional_event"])]
        low_value = _mean([float(row["low_value"]) for row in event_rows])
        high_value = _mean([float(row["high_value"]) for row in event_rows])
        minimum = MIN_CONDITIONAL_EVENTS if axis in conditional_axes else 1
        enough = len(event_rows) >= minimum
        if axis == "dislocation_selectivity":
            ordering = high_value <= low_value + 1e-12
        elif axis == "capital_deployment":
            ordering = high_value >= low_value - 1e-12
        else:
            ordering = high_value > low_value + 1e-12
        summary = {
            "turn_count": len(axis_rows),
            "conditional_event_count": len(event_rows),
            "minimum_event_count": minimum,
            "low_value": low_value,
            "high_value": high_value,
            "observation_gate_pass": enough,
            "ordering_pass": ordering,
            "pass": enough and ordering,
        }
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
            "single_neutral_reference_primitives_per_turn": True,
            "capital_deployment_recomputes_capacity": True,
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
