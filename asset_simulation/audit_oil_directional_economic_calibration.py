"""Reproducible economic calibration audit for the directional oil strategy.

The audit isolates one PM style axis at a time while holding the remaining
style dimensions, company risk, execution, capital authorization, market path,
and forecast vintage fixed.  It is deliberately separate from personnel roster
generation: correlated generated candidates are useful ecological tests, but
they cannot identify the economic effect of one style mapping.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
from typing import Any, Mapping, Sequence

from .audit_oil_formal_account_calibration import (
    _build_visible_path,
    _percentile,
    _round_nested,
    _run_scenario,
)
from .model.oil_strategy_research import (
    STRATEGY_STYLE_DIMENSIONS,
    generate_oil_strategy_research_candidate,
    resolve_oil_strategy_research_profile,
)
from .model.registry import load_registered_assets, sha256_json


CALIBRATION_AUDIT_VERSION = (
    "asset-simulation-oil-directional-economic-calibration-v0.1.0"
)
DEFAULT_FORECAST_BANDS: dict[str, tuple[float, float]] = {
    "low": (15.0, 25.0),
    "medium": (45.0, 55.0),
    "high": (65.0, 75.0),
    "elite": (80.0, 90.0),
}
DEFAULT_AXES = (
    "continuation_reversion",
    "turnover_activity",
    "responsiveness",
    "capital_deployment",
)
DEFAULT_AXIS_SCORES = (10.0, 30.0, 50.0, 70.0, 90.0)


def _controlled_profile(axis: str, score: float) -> dict[str, Any]:
    if axis not in STRATEGY_STYLE_DIMENSIONS:
        raise ValueError(f"unknown controlled PM axis: {axis}")
    if not 0.0 <= float(score) <= 100.0:
        raise ValueError("controlled PM score must be between zero and 100")
    radar = {name: 50.0 for name in STRATEGY_STYLE_DIMENSIONS}
    radar[axis] = float(score)
    return resolve_oil_strategy_research_profile(
        {
            "appointment": {
                "personnel_id": f"calibration_{axis}_{float(score):g}",
                "display_name": f"Calibration {axis} {float(score):g}",
                "source": "controlled_calibration_profile",
            },
            "style_radar": radar,
        }
    )


def _distribution(values: Sequence[float]) -> dict[str, float]:
    sample = list(map(float, values))
    if not sample:
        return {
            key: 0.0
            for key in (
                "minimum",
                "p10",
                "median",
                "p90",
                "p95",
                "maximum",
                "mean",
            )
        }
    return {
        "minimum": min(sample),
        "p10": _percentile(sample, 0.10),
        "median": _percentile(sample, 0.50),
        "p90": _percentile(sample, 0.90),
        "p95": _percentile(sample, 0.95),
        "maximum": max(sample),
        "mean": statistics.fmean(sample),
    }


def _paired_ratios(
    rows: Sequence[Mapping[str, Any]],
    *,
    axis: str,
    low_score: float,
    high_score: float,
    field: str,
) -> list[float]:
    selected = [row for row in rows if str(row["controlled_axis"]) == axis]
    grouped: dict[tuple[int, str], dict[float, Mapping[str, Any]]] = {}
    for row in selected:
        grouped.setdefault(
            (int(row["seed"]), str(row["forecast_band"])), {}
        )[float(row["controlled_score"])] = row
    ratios: list[float] = []
    for cell in grouped.values():
        if low_score not in cell or high_score not in cell:
            continue
        low = float(cell[low_score][field])
        high = float(cell[high_score][field])
        if low > 0.0:
            ratios.append(high / low)
    return ratios


def _generated_population_summary() -> dict[str, Any]:
    turnover: list[float] = []
    stale_exit_turns: list[float] = []
    for seed in range(256):
        for candidate_index in range(8):
            profile = generate_oil_strategy_research_candidate(
                seed=seed, candidate_index=candidate_index
            )
            execution = profile["resolved_policy"]["execution"]
            turnover.append(float(execution["gross_turnover_multiplier"]))
            speed = float(execution["adjustment_speed"])
            persistence = float(execution["position_persistence"])
            retention = 1.0 - speed * (1.0 - persistence)
            if retention <= 0.0:
                stale_exit_turns.append(1.0)
            elif retention >= 1.0:
                stale_exit_turns.append(math.inf)
            else:
                stale_exit_turns.append(math.log(0.10) / math.log(retention))
    return {
        "candidate_count": len(turnover),
        "turnover_multiplier": _distribution(turnover),
        "structural_half_turns_to_reduce_stale_position_90pct": _distribution(
            stale_exit_turns
        ),
    }


def _axis_score_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    axes: Sequence[str],
    scores: Sequence[float],
) -> dict[str, dict[str, dict[str, float]]]:
    metrics: dict[str, dict[str, dict[str, float]]] = {}
    fields = (
        "cagr_pct",
        "annualized_volatility_pct",
        "return_to_volatility_ratio",
        "maximum_drawdown_pct",
        "total_traded_lots",
        "execution_cost_bps_of_traded_notional",
    )
    for axis in axes:
        metrics[axis] = {}
        for score in scores:
            selected = [
                row
                for row in rows
                if str(row["controlled_axis"]) == axis
                and float(row["controlled_score"]) == float(score)
            ]
            metrics[axis][str(float(score))] = {
                field: _percentile(
                    [float(row[field]) for row in selected], 0.50
                )
                for field in fields
            }
    return metrics


def _risk_adjusted_winner_counts(
    rows: Sequence[Mapping[str, Any]],
    *,
    axis: str,
    scores: Sequence[float],
) -> dict[str, int]:
    counts = {str(float(score)): 0 for score in scores}
    cells: dict[tuple[int, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        if str(row["controlled_axis"]) != axis:
            continue
        cells.setdefault(
            (int(row["seed"]), str(row["forecast_band"])), []
        ).append(row)
    for cell in cells.values():
        winner = max(
            cell, key=lambda item: float(item["return_to_volatility_ratio"])
        )
        counts[str(float(winner["controlled_score"]))] += 1
    return counts


def build_oil_directional_economic_calibration_audit(
    *,
    seeds: Sequence[int] = tuple(range(8)),
    horizon_years: int = 3,
    forecast_bands: Mapping[str, tuple[float, float]] = DEFAULT_FORECAST_BANDS,
    axes: Sequence[str] = DEFAULT_AXES,
    axis_scores: Sequence[float] = DEFAULT_AXIS_SCORES,
    capital_authorization_pct: float = 60.0,
    include_rows: bool = True,
) -> dict[str, Any]:
    if horizon_years <= 0:
        raise ValueError("economic calibration horizon must be positive")
    seed_values = tuple(int(value) for value in seeds)
    if not seed_values:
        raise ValueError("economic calibration needs at least one seed")
    scores = tuple(float(value) for value in axis_scores)
    if len(scores) < 2 or scores != tuple(sorted(set(scores))):
        raise ValueError("economic calibration scores must be unique and sorted")
    unknown_axes = set(axes) - set(STRATEGY_STYLE_DIMENSIONS)
    if unknown_axes:
        raise ValueError(f"unknown controlled PM axes: {sorted(unknown_axes)}")
    bands = {str(key): tuple(map(float, value)) for key, value in forecast_bands.items()}
    for name, (minimum, maximum) in bands.items():
        if not name or not 0.0 <= minimum <= maximum <= 100.0:
            raise ValueError("forecast calibration band is invalid")

    profiles = {
        (axis, score): _controlled_profile(axis, score)
        for axis in axes
        for score in scores
    }
    rows: list[dict[str, Any]] = []
    for seed in seed_values:
        for band_name, score_range in bands.items():
            path = _build_visible_path(
                seed,
                horizon_years,
                forecast_score_range=score_range,
            )
            for axis in axes:
                for score in scores:
                    row = _run_scenario(
                        seed=seed,
                        path=path,
                        style_label=f"{axis}:{score:g}:{band_name}",
                        strategy_profile=profiles[(axis, score)],
                        authorization_pct=float(capital_authorization_pct),
                    )
                    rows.append(
                        {
                            **row,
                            "controlled_axis": axis,
                            "controlled_score": score,
                            "forecast_band": band_name,
                            "forecast_score_min": score_range[0],
                            "forecast_score_max": score_range[1],
                        }
                    )

    low_score = min(scores)
    high_score = max(scores)
    turnover_ratios = _paired_ratios(
        rows,
        axis="turnover_activity",
        low_score=low_score,
        high_score=high_score,
        field="total_traded_lots",
    )
    cost_ratios = _paired_ratios(
        rows,
        axis="turnover_activity",
        low_score=low_score,
        high_score=high_score,
        field="total_execution_cost_usd",
    )

    orientation_rows = [
        row for row in rows if row["controlled_axis"] == "continuation_reversion"
    ]
    winner_counts = {str(score): 0 for score in scores}
    orientation_cells: dict[tuple[int, str], list[Mapping[str, Any]]] = {}
    for row in orientation_rows:
        orientation_cells.setdefault(
            (int(row["seed"]), str(row["forecast_band"])), []
        ).append(row)
    for cell in orientation_cells.values():
        winner = max(cell, key=lambda item: float(item["cagr_pct"]))
        winner_counts[str(float(winner["controlled_score"]))] += 1
    winner_total = sum(winner_counts.values())
    largest_winner_share = (
        0.0 if winner_total <= 0 else max(winner_counts.values()) / winner_total
    )

    neutral_score = min(scores, key=lambda value: abs(value - 50.0))
    neutral_rows = [
        row
        for row in orientation_rows
        if float(row["controlled_score"]) == neutral_score
    ]
    invalidated_by_band = {
        band: _distribution(
            [
                float(row["thesis_status_share_pct"]["invalidated"])
                for row in neutral_rows
                if row["forecast_band"] == band
            ]
        )
        for band in bands
    }

    capital_low = [
        row
        for row in rows
        if row["controlled_axis"] == "capital_deployment"
        and float(row["controlled_score"]) == low_score
    ]
    capital_high = [
        row
        for row in rows
        if row["controlled_axis"] == "capital_deployment"
        and float(row["controlled_score"]) == high_score
    ]
    low_capital_vol = _percentile(
        [float(row["annualized_volatility_pct"]) for row in capital_low], 0.5
    ) if capital_low else 0.0
    high_capital_vol = _percentile(
        [float(row["annualized_volatility_pct"]) for row in capital_high], 0.5
    ) if capital_high else 0.0
    low_capital_drawdown = _percentile(
        [abs(float(row["maximum_drawdown_pct"])) for row in capital_low], 0.5
    ) if capital_low else 0.0
    high_capital_drawdown = _percentile(
        [abs(float(row["maximum_drawdown_pct"])) for row in capital_high], 0.5
    ) if capital_high else 0.0

    round_trip_shares = [
        float(row["round_trip_gross_positive_pnl_share"]) for row in rows
    ]
    axis_score_metrics = _axis_score_metrics(rows, axes=axes, scores=scores)
    turnover_risk_adjusted_winners = _risk_adjusted_winner_counts(
        rows,
        axis="turnover_activity",
        scores=scores,
    )
    capital_risk_adjusted_winners = _risk_adjusted_winner_counts(
        rows,
        axis="capital_deployment",
        scores=scores,
    )
    turnover_winner_total = sum(turnover_risk_adjusted_winners.values())
    capital_winner_total = sum(capital_risk_adjusted_winners.values())
    highest_score_key = str(float(high_score))
    generated_population = _generated_population_summary()
    medium_invalidated = invalidated_by_band.get("medium", {}).get("median", 0.0)
    low_invalidated = invalidated_by_band.get("low", {}).get("median", 0.0)
    high_invalidated = invalidated_by_band.get("high", {}).get("median", 0.0)
    turnover_distribution = _distribution(turnover_ratios)
    cost_distribution = _distribution(cost_ratios)
    round_trip_distribution = _distribution(round_trip_shares)
    gates = {
        "all_account_invariants_pass": all(
            int(row["hard_invariants"]["account_expansion_violations"]) == 0
            and int(row["hard_invariants"]["maintenance_violations"]) == 0
            and float(row["hard_invariants"]["maximum_cash_identity_error_usd"]) <= 0.01
            for row in rows
        ),
        "controlled_turnover_ratio_median_between_3_and_7": (
            bool(turnover_ratios)
            and 3.0 <= turnover_distribution["median"] <= 7.0
        ),
        "controlled_turnover_ratio_p90_not_above_10": (
            bool(turnover_ratios) and turnover_distribution["p90"] <= 10.0
        ),
        "controlled_execution_cost_ratio_p90_not_above_10": (
            bool(cost_ratios) and cost_distribution["p90"] <= 10.0
        ),
        "medium_forecast_invalidated_occupancy_guardrail_5_to_30_pct": (
            5.0 <= medium_invalidated <= 30.0
        ),
        "forecast_skill_orders_invalidated_occupancy": (
            low_invalidated > medium_invalidated > high_invalidated
        ),
        "higher_capital_deployment_increases_median_volatility": (
            high_capital_vol > low_capital_vol
        ),
        "higher_capital_deployment_increases_median_drawdown": (
            high_capital_drawdown > low_capital_drawdown
        ),
        "no_orientation_score_wins_more_than_half_of_cells": (
            largest_winner_share <= 0.50
        ),
        "round_trip_pnl_share_median_below_35_pct": (
            round_trip_distribution["median"] < 0.35
        ),
        "round_trip_pnl_share_p90_below_60_pct": (
            round_trip_distribution["p90"] < 0.60
        ),
        "generated_stale_exit_p95_not_above_10_half_turns": (
            generated_population[
                "structural_half_turns_to_reduce_stale_position_90pct"
            ]["p95"] <= 10.0
        ),
        "highest_turnover_not_universal_risk_adjusted_winner": (
            turnover_winner_total > 0
            and turnover_risk_adjusted_winners[highest_score_key]
            / turnover_winner_total
            < 0.75
        ),
        "highest_capital_deployment_not_universal_risk_adjusted_winner": (
            capital_winner_total > 0
            and capital_risk_adjusted_winners[highest_score_key]
            / capital_winner_total
            < 0.75
        ),
    }

    assets = load_registered_assets()
    result = {
        "ok": all(gates.values()),
        "schemaVersion": "asset-simulation-oil-directional-economic-calibration-v1",
        "scope": {
            "seeds": list(seed_values),
            "horizon_years": int(horizon_years),
            "forecast_bands": {key: list(value) for key, value in bands.items()},
            "controlled_axes": list(axes),
            "controlled_scores": list(scores),
            "capital_authorization_pct": float(capital_authorization_pct),
            "scenario_count": len(rows),
            "correlated_roster_used_for_axis_identification": False,
            "market_and_forecast_path_shared_within_seed_band": True,
        },
        "turnover90To10ActualLotsRatio": turnover_distribution,
        "executionCost90To10Ratio": cost_distribution,
        "orientationWinnerCounts": winner_counts,
        "largestOrientationWinnerShare": largest_winner_share,
        "thesisInvalidatedOccupancyPctByForecastBand": invalidated_by_band,
        "calibrationTargets": {
            "medium_forecast_invalidated_occupancy_target_pct": [5.0, 25.0],
            "medium_forecast_invalidated_occupancy_guardrail_pct": [5.0, 30.0],
            "medium_forecast_target_met": 5.0 <= medium_invalidated <= 25.0,
        },
        "capitalDeploymentComparison": {
            "low_score": low_score,
            "high_score": high_score,
            "low_median_volatility_pct": low_capital_vol,
            "high_median_volatility_pct": high_capital_vol,
            "low_median_absolute_drawdown_pct": low_capital_drawdown,
            "high_median_absolute_drawdown_pct": high_capital_drawdown,
        },
        "roundTripGrossPositivePnlShare": round_trip_distribution,
        "axisScoreMedianMetrics": axis_score_metrics,
        "turnoverRiskAdjustedWinnerCounts": turnover_risk_adjusted_winners,
        "capitalDeploymentRiskAdjustedWinnerCounts": (
            capital_risk_adjusted_winners
        ),
        "generatedPopulation": generated_population,
        "gates": gates,
    }
    if include_rows:
        result["scenarios"] = rows
    identity = {
        "audit_version": CALIBRATION_AUDIT_VERSION,
        "oil_strategy_config_hash": assets["oil_trading_strategy_config_hash"],
        "oil_strategy_research_config_hash": assets[
            "oil_strategy_research_config_hash"
        ],
        "result_hash": sha256_json(result),
    }
    return _round_nested({"identity": identity, **result})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", default="0,1,2,3,4,5,6,7")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    report = build_oil_directional_economic_calibration_audit(
        seeds=tuple(
            int(item.strip())
            for item in args.seeds.split(",")
            if item.strip()
        ),
        horizon_years=args.years,
        include_rows=not args.summary_only,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
