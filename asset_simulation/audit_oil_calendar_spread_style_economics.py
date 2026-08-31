"""Controlled economic-behaviour audit for the calendar-spread PM style layer.

This audit identifies one *dedicated* calendar-spread style dimension at a time.
All other dedicated dimensions are held at 50, forecast research is fixed, the
market path is shared, and the default construction-capability profile is used
so construction error is zero.  The audit therefore checks whether registered
style semantics produce the intended economic behaviour rather than ranking PMs
by realized return.

There is intentionally no production execution simulation here.  For context we
report an idealized next-half-turn target markout that assumes the research target
is established at the decision reference price.  It is not formal account PnL,
does not include fills/costs/margin and is never an acceptance gate.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
from typing import Any, Mapping, Sequence

from .audit_oil_formal_account_calibration import _build_visible_path, _round_nested
from .model.oil_calendar_spread_research import (
    CALENDAR_SPREAD_STYLE_DIMENSIONS,
    build_oil_calendar_spread_reference_profile,
)
from .model.oil_calendar_spread_strategy import (
    _apply_spread_position_persistence,
    _paired_execution_mandate,
    _responsive_target,
    build_oil_calendar_spread_research_decision,
)
from .model.oil_calendar_spread_strategy_v2 import _dedicated_signal_mix
from .model.oil_strategy_research import (
    build_default_oil_strategy_research_profile,
    resolve_oil_strategy_runtime_policy,
)
from .model.registry import load_registered_assets, sha256_json


AUDIT_VERSION = "asset-simulation-oil-calendar-spread-style-economic-audit-v0.1.0"
DEFAULT_AXIS_SCORES = (10.0, 50.0, 90.0)
DEFAULT_DEVELOPMENT_SEEDS = (0, 42)
DEFAULT_VALIDATION_SEEDS = (99, 197)
CONFLICT_SIGNAL_FLOOR = 0.15


def _mean(values: Sequence[float]) -> float:
    sample = [float(value) for value in values]
    return 0.0 if not sample else statistics.fmean(sample)


def _safe_ratio(numerator: float, denominator: float) -> float:
    return 0.0 if abs(float(denominator)) <= 1e-12 else float(numerator) / float(denominator)


def _controlled_radar(axis: str, score: float) -> dict[str, float]:
    if axis not in CALENDAR_SPREAD_STYLE_DIMENSIONS:
        raise ValueError(f"unknown calendar-spread style axis: {axis}")
    score_value = float(score)
    if not 0.0 <= score_value <= 100.0:
        raise ValueError("calendar-spread controlled score must be in [0, 100]")
    radar = {name: 50.0 for name in CALENDAR_SPREAD_STYLE_DIMENSIONS}
    radar[axis] = score_value
    return radar


def _piecewise_score_anchors(anchors: Mapping[str, Any], score: float) -> float:
    normalized = max(0.0, min(1.0, float(score) / 100.0))
    low = float(anchors["score_0"])
    neutral = float(anchors["score_50"])
    high = float(anchors["score_100"])
    if normalized <= 0.5:
        return low + normalized * 2.0 * (neutral - low)
    return neutral + (normalized - 0.5) * 2.0 * (high - neutral)


def _runtime_bundle(
    dedicated_radar: Mapping[str, float],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build one audit-only controlled policy without creating a player-editable radar."""

    assets = load_registered_assets()
    source_profile = build_default_oil_strategy_research_profile()
    reference_profile = build_oil_calendar_spread_reference_profile(
        source_profile, dedicated_radar
    )
    _, reference_policy = resolve_oil_strategy_runtime_policy(reference_profile)
    research_config = assets["oil_calendar_spread_research_config"]
    forecast_weight = _piecewise_score_anchors(
        research_config["calendar_spread_specific_mapping"]["forecast_component_weight"],
        float(dedicated_radar["forecast_vs_visible_curve"]),
    )
    policy = {
        "signal": {
            **dict(reference_policy["signal"]),
            "forecast_component_weight": forecast_weight,
            "visible_curve_component_weight": 1.0 - forecast_weight,
            "forecast_vs_visible_curve_score": float(
                dedicated_radar["forecast_vs_visible_curve"]
            ),
            "component_mix_owner": "oil_calendar_spread_research_v1",
        },
        "risk": dict(reference_policy["risk"]),
        "execution": dict(reference_policy["execution"]),
        "construction": dict(reference_policy["construction"]),
    }
    return source_profile, reference_profile, policy


def _pair_ids(market: Mapping[str, Any]) -> tuple[str, str]:
    curve = dict(market["curve"])
    contracts = [dict(item) for item in curve["contracts"]]
    ids = [str(item["contract_id"]) for item in contracts]
    main_id = str(curve["main_contract_id"])
    if main_id not in ids or ids.index(main_id) + 1 >= len(ids):
        raise ValueError("calendar-spread audit market lacks an adjacent pair")
    return main_id, ids[ids.index(main_id) + 1]


def _positions_for_units(market: Mapping[str, Any], spread_units: int) -> dict[str, int]:
    main_id, next_id = _pair_ids(market)
    units = int(spread_units)
    if units == 0:
        return {}
    return {main_id: units, next_id: -units}


def _contract_price(market: Mapping[str, Any], contract_id: str) -> float | None:
    for value in market.get("curve", {}).get("contracts", ()):
        item = dict(value)
        if str(item.get("contract_id")) == str(contract_id):
            price = float(item["price_usd"])
            return price if math.isfinite(price) and price > 0.0 else None
    return None


def _idealized_markout(
    decision: Mapping[str, Any], end_market: Mapping[str, Any]
) -> float | None:
    main = dict(decision["legs"]["main"])
    next_main = dict(decision["legs"]["next_main"])
    main_end = _contract_price(end_market, str(main["contract_id"]))
    next_end = _contract_price(end_market, str(next_main["contract_id"]))
    if main_end is None or next_end is None:
        return None
    units = int(decision["target_spread_units"])
    start_spread = float(main["price_usd"]) - float(next_main["price_usd"])
    end_spread = main_end - next_end
    return units * (end_spread - start_spread) * 1000.0


def _controlled_decision(
    market: Mapping[str, Any],
    forecast: Mapping[str, Any],
    *,
    current_spread_units: int,
    dedicated_radar: Mapping[str, float],
    authorized_strategy_capital_usd: float,
) -> dict[str, Any]:
    _, reference_profile, policy = _runtime_bundle(dedicated_radar)
    positions = _positions_for_units(market, current_spread_units)
    reference = build_oil_calendar_spread_research_decision(
        market,
        forecast,
        authorized_strategy_capital_usd=float(authorized_strategy_capital_usd),
        positions=positions,
        strategy_research_profile=reference_profile,
        thesis_state={"status": "active", "last_signal": 0.0},
    )
    signal = _dedicated_signal_mix(reference["signal"], policy)
    capacity = dict(reference["strategyRiskAdapter"]["capacity"])
    risk_capacity = int(capacity["risk_capacity_units"])
    current = int(reference["strategyRiskAdapter"]["current"]["spread_units"])
    ideal = int(round(float(signal["signal"]) * risk_capacity))
    persistent = _apply_spread_position_persistence(
        current_spread_units=current,
        proposed_target_units=ideal,
        capacity_units=risk_capacity,
        position_persistence=float(policy["execution"]["position_persistence"]),
    )
    target = _responsive_target(
        current_units=current,
        target_units=persistent,
        adjustment_speed=float(policy["execution"]["adjustment_speed"]),
        capacity_units=risk_capacity,
    )
    current_position = {
        "spread_units": current,
        "residual_main_lots": 0,
        "residual_next_main_lots": 0,
    }
    strategy_config = load_registered_assets()["oil_calendar_spread_strategy_v2_config"]
    mandate = _paired_execution_mandate(
        current_position=current_position,
        target_spread_units=target,
        risk_capacity=capacity,
        strategy_policy=policy,
        config=strategy_config,
    )
    result = {
        "style_radar": dict(dedicated_radar),
        "policy": policy,
        "signal": signal,
        "capacity": capacity,
        "current_spread_units": current,
        "ideal_target_spread_units": ideal,
        "persistent_target_spread_units": persistent,
        "target_spread_units": target,
        "paired_execution_mandate": mandate,
        "legs": {
            "main": dict(reference["legs"]["main"]),
            "next_main": dict(reference["legs"]["next_main"]),
        },
    }
    return _round_nested(result)


def _horizon_component(signal: Mapping[str, Any], horizon: int) -> dict[str, Any] | None:
    matches = [
        dict(item)
        for item in signal.get("horizon_components", ())
        if int(item.get("requested_horizon_weeks", -1)) == int(horizon)
    ]
    return matches[0] if len(matches) == 1 else None


def _turn_metrics(
    decision: Mapping[str, Any], end_market: Mapping[str, Any]
) -> dict[str, Any]:
    signal = dict(decision["signal"])
    current = int(decision["current_spread_units"])
    ideal = int(decision["ideal_target_spread_units"])
    persistent = int(decision["persistent_target_spread_units"])
    target = int(decision["target_spread_units"])
    forecast_signal = float(signal["forecast_signal"])
    visible_signal = float(signal["visible_curve_signal"])
    momentum_signal = float(signal["curve_momentum_signal"])
    reversion_signal = float(signal["curve_mean_reversion_signal"])
    raw_signal = float(signal["raw_signal"])

    forecast_curve_conflict = (
        abs(forecast_signal) >= CONFLICT_SIGNAL_FLOOR
        and abs(visible_signal) >= CONFLICT_SIGNAL_FLOOR
        and forecast_signal * visible_signal < 0.0
    )
    momentum_reversion_conflict = (
        abs(momentum_signal) >= CONFLICT_SIGNAL_FLOOR
        and abs(reversion_signal) >= CONFLICT_SIGNAL_FLOOR
        and momentum_signal * reversion_signal < 0.0
    )
    two = _horizon_component(signal, 2)
    four = _horizon_component(signal, 4)
    two_change = 0.0 if two is None else float(two["forecast_spread_change_usd_per_bbl"])
    four_change = 0.0 if four is None else float(four["forecast_spread_change_usd_per_bbl"])
    horizon_conflict = (
        abs(two_change) > 1e-9 and abs(four_change) > 1e-9 and two_change * four_change < 0.0
    )

    response_gap = persistent - current
    response_completion = (
        0.0
        if response_gap == 0
        else abs(target - current) / max(1.0, abs(response_gap))
    )
    shrink_event = current * ideal > 0 and abs(ideal) < abs(current)
    shrink_retention = 0.0
    if shrink_event:
        shrink_retention = (abs(persistent) - abs(ideal)) / max(
            1.0, abs(current) - abs(ideal)
        )

    markout = _idealized_markout(decision, end_market)
    markout_per_unit = (
        0.0
        if markout is None or target == 0
        else float(markout) / abs(target)
    )
    return {
        "active_signal": abs(float(signal["signal"])) > 1e-12,
        "abs_signal": abs(float(signal["signal"])),
        "risk_capacity_units": int(decision["capacity"]["risk_capacity_units"]),
        "abs_target_units": abs(target),
        "target_capacity_utilization": _safe_ratio(
            abs(target), max(1, int(decision["capacity"]["risk_capacity_units"]))
        ),
        "response_completion": response_completion,
        "advisory_pair_turnover_budget_units": int(
            decision["paired_execution_mandate"]["advisory_pair_turnover_budget_units"]
        ),
        "forecast_curve_conflict": forecast_curve_conflict,
        "forecast_alignment": (
            abs(raw_signal - visible_signal) - abs(raw_signal - forecast_signal)
        ),
        "momentum_reversion_conflict": momentum_reversion_conflict,
        "momentum_alignment": (
            abs(visible_signal - reversion_signal) - abs(visible_signal - momentum_signal)
        ),
        "horizon_conflict": horizon_conflict,
        "four_week_alignment": (
            abs(float(signal["forecast_spread_change_usd_per_bbl"]) - two_change)
            - abs(float(signal["forecast_spread_change_usd_per_bbl"]) - four_change)
        ),
        "four_week_weight": float(signal["horizon_weights"][1]),
        "forecast_component_weight": float(signal["forecast_component_weight"]),
        "visible_curve_component_weight": float(signal["visible_curve_component_weight"]),
        "continuation_weight": float(signal["continuation_weight"]),
        "signal_deadband_abs": float(signal["signal_deadband_abs"]),
        "adjustment_speed": float(decision["policy"]["execution"]["adjustment_speed"]),
        "gross_turnover_multiplier": float(
            decision["policy"]["execution"]["gross_turnover_multiplier"]
        ),
        "position_persistence": float(
            decision["policy"]["execution"]["position_persistence"]
        ),
        "capital_deployment_pct": float(
            decision["policy"]["risk"]["capital_deployment_pct_of_allocated_equity"]
        ),
        "shrink_event": shrink_event,
        "shrink_retention": shrink_retention,
        "idealized_target_markout_usd": 0.0 if markout is None else float(markout),
        "idealized_markout_per_target_unit_usd": markout_per_unit,
        "markout_available": markout is not None,
    }


def _run_axis_score(
    *,
    seed: int,
    path: Sequence[Mapping[str, Any]],
    axis: str,
    score: float,
    authorized_strategy_capital_usd: float,
) -> dict[str, Any]:
    radar = _controlled_radar(axis, score)
    current_units = 0
    turns: list[dict[str, Any]] = []
    for item in path:
        decision = _controlled_decision(
            item["start_market"],
            item["forecast"],
            current_spread_units=current_units,
            dedicated_radar=radar,
            authorized_strategy_capital_usd=authorized_strategy_capital_usd,
        )
        metrics = _turn_metrics(decision, item["end_market"])
        turns.append(metrics)
        # Research-only state propagation: assume the target becomes the next
        # research book so persistence/tempo can be observed.  This is not a fill model.
        current_units = int(decision["target_spread_units"])

    def selected(name: str, gate: str | None = None) -> list[float]:
        rows = turns if gate is None else [row for row in turns if bool(row[gate])]
        return [float(row[name]) for row in rows]

    active_count = sum(bool(row["active_signal"]) for row in turns)
    markout_rows = [row for row in turns if bool(row["markout_available"])]
    return {
        "seed": int(seed),
        "axis": axis,
        "score": float(score),
        "turn_count": len(turns),
        "active_signal_rate": _safe_ratio(active_count, len(turns)),
        "mean_abs_signal": _mean(selected("abs_signal")),
        "mean_risk_capacity_units": _mean(selected("risk_capacity_units")),
        "mean_abs_target_units": _mean(selected("abs_target_units")),
        "mean_target_capacity_utilization": _mean(selected("target_capacity_utilization")),
        "mean_response_completion": _mean(selected("response_completion")),
        "mean_advisory_pair_turnover_budget_units": _mean(
            selected("advisory_pair_turnover_budget_units")
        ),
        "forecast_curve_conflict_count": sum(
            bool(row["forecast_curve_conflict"]) for row in turns
        ),
        "mean_forecast_alignment_on_conflict": _mean(
            selected("forecast_alignment", "forecast_curve_conflict")
        ),
        "momentum_reversion_conflict_count": sum(
            bool(row["momentum_reversion_conflict"]) for row in turns
        ),
        "mean_momentum_alignment_on_conflict": _mean(
            selected("momentum_alignment", "momentum_reversion_conflict")
        ),
        "horizon_conflict_count": sum(bool(row["horizon_conflict"]) for row in turns),
        "mean_four_week_alignment_on_conflict": _mean(
            selected("four_week_alignment", "horizon_conflict")
        ),
        "mean_four_week_weight": _mean(selected("four_week_weight")),
        "mean_forecast_component_weight": _mean(selected("forecast_component_weight")),
        "mean_continuation_weight": _mean(selected("continuation_weight")),
        "mean_signal_deadband_abs": _mean(selected("signal_deadband_abs")),
        "mean_adjustment_speed": _mean(selected("adjustment_speed")),
        "mean_gross_turnover_multiplier": _mean(selected("gross_turnover_multiplier")),
        "mean_position_persistence": _mean(selected("position_persistence")),
        "mean_capital_deployment_pct": _mean(selected("capital_deployment_pct")),
        "shrink_event_count": sum(bool(row["shrink_event"]) for row in turns),
        "mean_shrink_retention": _mean(selected("shrink_retention", "shrink_event")),
        "idealized_target_markout_usd": sum(
            float(row["idealized_target_markout_usd"]) for row in markout_rows
        ),
        "mean_idealized_markout_per_target_unit_usd": _mean(
            [float(row["idealized_markout_per_target_unit_usd"]) for row in markout_rows]
        ),
        "markout_observation_count": len(markout_rows),
    }


def _aggregate(rows: Sequence[Mapping[str, Any]], axis: str, score: float) -> dict[str, Any]:
    selected = [
        row
        for row in rows
        if str(row["axis"]) == str(axis) and float(row["score"]) == float(score)
    ]
    if not selected:
        raise ValueError("calendar-spread audit aggregation has no rows")
    fields = [
        key
        for key, value in selected[0].items()
        if key not in {"seed", "axis", "score"} and isinstance(value, (int, float))
    ]
    return {key: _mean([float(row[key]) for row in selected]) for key in fields}


def _gate(
    *,
    name: str,
    low: Mapping[str, Any],
    high: Mapping[str, Any],
    metric: str,
    direction: str,
    minimum_observations_metric: str | None = None,
    minimum_observations: float = 0.0,
) -> dict[str, Any]:
    low_value = float(low[metric])
    high_value = float(high[metric])
    enough = True
    if minimum_observations_metric is not None:
        enough = (
            float(low[minimum_observations_metric]) >= minimum_observations
            and float(high[minimum_observations_metric]) >= minimum_observations
        )
    if direction == "higher":
        ordering = high_value > low_value + 1e-12
    elif direction == "lower":
        ordering = high_value < low_value - 1e-12
    elif direction == "not_lower":
        ordering = high_value + 1e-12 >= low_value
    elif direction == "not_higher":
        ordering = high_value <= low_value + 1e-12
    else:
        raise ValueError("unknown calendar-spread gate direction")
    return {
        "name": name,
        "metric": metric,
        "direction": direction,
        "low_value": low_value,
        "high_value": high_value,
        "observation_gate_pass": enough,
        "ordering_pass": ordering,
        "pass": enough and ordering,
    }


def _evaluate_partition(
    rows: Sequence[Mapping[str, Any]],
    *,
    axis_scores: Sequence[float],
) -> dict[str, Any]:
    low_score = float(min(axis_scores))
    high_score = float(max(axis_scores))
    summaries = {
        axis: {
            str(float(score)): _aggregate(rows, axis, score)
            for score in axis_scores
        }
        for axis in CALENDAR_SPREAD_STYLE_DIMENSIONS
    }

    def pair(axis: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        return summaries[axis][str(low_score)], summaries[axis][str(high_score)]

    gates: list[dict[str, Any]] = []
    low, high = pair("forecast_vs_visible_curve")
    gates.append(
        _gate(
            name="forecast-vs-curve conflict shifts toward forecast",
            low=low,
            high=high,
            metric="mean_forecast_alignment_on_conflict",
            direction="higher",
            minimum_observations_metric="forecast_curve_conflict_count",
            minimum_observations=2.0,
        )
    )
    low, high = pair("curve_continuation_reversion")
    gates.append(
        _gate(
            name="curve orientation shifts toward momentum",
            low=low,
            high=high,
            metric="mean_momentum_alignment_on_conflict",
            direction="higher",
            minimum_observations_metric="momentum_reversion_conflict_count",
            minimum_observations=2.0,
        )
    )
    low, high = pair("dislocation_selectivity")
    gates.append(
        _gate(
            name="higher selectivity reduces active-signal frequency",
            low=low,
            high=high,
            metric="active_signal_rate",
            direction="not_higher",
        )
    )
    low, high = pair("capital_deployment")
    gates.append(
        _gate(
            name="higher deployment does not reduce spread capacity",
            low=low,
            high=high,
            metric="mean_risk_capacity_units",
            direction="not_lower",
        )
    )
    low, high = pair("adjustment_tempo")
    gates.append(
        _gate(
            name="faster tempo increases target-gap completion",
            low=low,
            high=high,
            metric="mean_response_completion",
            direction="higher",
        )
    )
    low, high = pair("rebalance_activity")
    gates.append(
        _gate(
            name="higher rebalance activity expands advisory turnover budget",
            low=low,
            high=high,
            metric="mean_advisory_pair_turnover_budget_units",
            direction="higher",
        )
    )
    low, high = pair("holding_patience")
    gates.append(
        _gate(
            name="higher patience retains more exposure during same-direction shrink",
            low=low,
            high=high,
            metric="mean_shrink_retention",
            direction="higher",
            minimum_observations_metric="shrink_event_count",
            minimum_observations=2.0,
        )
    )
    low, high = pair("forecast_horizon")
    gates.append(
        _gate(
            name="longer horizon shifts forecast toward four-week component",
            low=low,
            high=high,
            metric="mean_four_week_weight",
            direction="higher",
        )
    )
    return {
        "score_range": [low_score, high_score],
        "axis_summaries": summaries,
        "gates": gates,
        "hard_gate_pass": all(bool(item["pass"]) for item in gates),
        "markout_is_acceptance_gate": False,
    }


def build_oil_calendar_spread_style_economic_audit(
    *,
    development_seeds: Sequence[int] = DEFAULT_DEVELOPMENT_SEEDS,
    validation_seeds: Sequence[int] = DEFAULT_VALIDATION_SEEDS,
    horizon_years: int = 1,
    axis_scores: Sequence[float] = DEFAULT_AXIS_SCORES,
    authorized_strategy_capital_usd: float = 10_000_000.0,
    include_rows: bool = False,
) -> dict[str, Any]:
    if horizon_years <= 0:
        raise ValueError("calendar-spread style audit horizon must be positive")
    scores = tuple(float(value) for value in axis_scores)
    if len(scores) < 2 or scores != tuple(sorted(set(scores))):
        raise ValueError("calendar-spread style audit scores must be sorted and unique")
    if any(not 0.0 <= value <= 100.0 for value in scores):
        raise ValueError("calendar-spread style audit scores must be in [0, 100]")
    if not development_seeds or not validation_seeds:
        raise ValueError("calendar-spread style audit needs development and validation seeds")

    partitions: dict[str, Any] = {}
    all_rows: dict[str, list[dict[str, Any]]] = {}
    for partition_name, seeds in (
        ("development", development_seeds),
        ("validation", validation_seeds),
    ):
        rows: list[dict[str, Any]] = []
        for seed in map(int, seeds):
            path = _build_visible_path(seed, horizon_years)
            for axis in CALENDAR_SPREAD_STYLE_DIMENSIONS:
                for score in scores:
                    rows.append(
                        _run_axis_score(
                            seed=seed,
                            path=path,
                            axis=axis,
                            score=score,
                            authorized_strategy_capital_usd=float(
                                authorized_strategy_capital_usd
                            ),
                        )
                    )
        partitions[partition_name] = _evaluate_partition(rows, axis_scores=scores)
        all_rows[partition_name] = rows

    result = {
        "audit_version": AUDIT_VERSION,
        "strategy_model_version": load_registered_assets()[
            "oil_calendar_spread_strategy_v2_config"
        ]["model_version"],
        "style_model_version": load_registered_assets()[
            "oil_calendar_spread_research_config"
        ]["model_version"],
        "method": {
            "controlled_dedicated_axis": True,
            "all_other_dedicated_axes_score": 50.0,
            "construction_capability": "default_score_100_zero_error",
            "forecast_research_fixed_within_seed": True,
            "same_market_path_within_seed": True,
            "research_book_state": "prior_target_propagated_without_fill_model",
            "idealized_markout": "diagnostic_only_not_formal_strategy_pnl",
            "execution_costs_included": False,
            "portfolio_risk_included": False,
        },
        "development_seeds": list(map(int, development_seeds)),
        "validation_seeds": list(map(int, validation_seeds)),
        "horizon_years": int(horizon_years),
        "axis_scores": list(scores),
        "partitions": partitions,
        "overall_hard_gate_pass": bool(
            partitions["development"]["hard_gate_pass"]
            and partitions["validation"]["hard_gate_pass"]
        ),
    }
    if include_rows:
        result["rows"] = all_rows
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
    parser.add_argument("--years", type=int, default=1)
    parser.add_argument("--include-rows", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_oil_calendar_spread_style_economic_audit(
        development_seeds=args.development_seeds,
        validation_seeds=args.validation_seeds,
        horizon_years=args.years,
        include_rows=bool(args.include_rows),
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is None:
        print(text)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(args.output)
    if not bool(report["overall_hard_gate_pass"]):
        raise SystemExit("calendar-spread PM style economic audit failed")


if __name__ == "__main__":
    main()
