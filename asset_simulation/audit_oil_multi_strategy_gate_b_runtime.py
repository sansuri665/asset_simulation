"""First Directional-only versus fixed $5m/$5m Gate B trading report."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from .model.engine import run_global_macro
from .model.oil_multi_strategy_execution import (
    CALENDAR_SPREAD_STRATEGY_ID,
    DIRECTIONAL_STRATEGY_ID,
)
from .model.oil_multi_strategy_runtime import (
    OIL_MULTI_STRATEGY_RUNTIME_MODEL_VERSION,
    simulate_oil_multi_strategy_runtime,
)
from .model.registry import sha256_json


def _finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Gate B comparison contains a non-finite metric")
    return result


def _scenario_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    turns = list(report.get("turnReports", ()))
    margins = [
        item["settlement"]["formalAccount"]["accountAfter"].get(
            "margin_to_equity_pct"
        )
        for item in turns
    ]
    margins = [float(value) for value in margins if value is not None]
    performance = dict(report["performance"])
    return {
        "completed_turns": int(performance["completed_turns"]),
        "starting_equity_usd": _finite(performance["starting_equity_usd"]),
        "ending_equity_usd": _finite(performance["ending_equity_usd"]),
        "cumulative_return_pct": _finite(performance["cumulative_return_pct"]),
        "provisional_annualized_return_pct": _finite(
            performance["provisional_annualized_return_pct"]
        ),
        "annualized_volatility_pct": _finite(
            performance["annualized_volatility_pct"]
        ),
        "maximum_drawdown_pct": _finite(performance["maximum_drawdown_pct"]),
        "maximum_margin_to_equity_pct": max(margins, default=0.0),
        "external_execution_cost_usd": _finite(
            report["turnoverAndNetting"]["shared_external_execution_cost_usd"]
        ),
        "allocated_child_lot_sides": int(
            report["turnoverAndNetting"]["allocated_child_lot_sides"]
        ),
        "internalized_child_lot_sides": int(
            report["turnoverAndNetting"]["internalized_child_lot_sides"]
        ),
        "external_parent_turnover_lots": int(
            report["turnoverAndNetting"]["external_parent_turnover_lots"]
        ),
        "internalization_ratio_pct": _finite(
            report["turnoverAndNetting"]["internalization_ratio_pct"]
        ),
        "strategy_fully_loaded_pnl_usd": {
            strategy_id: _finite(
                report["strategySummary"][strategy_id][
                    "cumulative_fully_loaded_pnl_usd"
                ]
            )
            for strategy_id in (
                DIRECTIONAL_STRATEGY_ID,
                CALENDAR_SPREAD_STRATEGY_ID,
            )
        },
        "corporate_reserve_ending_usd": _finite(
            report["finalState"]["corporateReserveUsd"]
        ),
        "all_mechanical_hard_gates_pass": bool(
            report["all_mechanical_hard_gates_pass"]
        ),
        "long_horizon_economic_result_valid": bool(
            report["long_horizon_economic_result_valid"]
        ),
        "lifecycle_stop_reason": str(report["lifecycle"]["stop_reason"]),
    }


def _paired_difference(
    directional_only: Mapping[str, Any], dual: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "ending_equity_difference_usd": _finite(dual["ending_equity_usd"])
        - _finite(directional_only["ending_equity_usd"]),
        "cumulative_return_difference_pct_points": _finite(
            dual["cumulative_return_pct"]
        )
        - _finite(directional_only["cumulative_return_pct"]),
        "provisional_annualized_return_difference_pct_points": _finite(
            dual["provisional_annualized_return_pct"]
        )
        - _finite(directional_only["provisional_annualized_return_pct"]),
        "annualized_volatility_difference_pct_points": _finite(
            dual["annualized_volatility_pct"]
        )
        - _finite(directional_only["annualized_volatility_pct"]),
        "maximum_drawdown_difference_pct_points": _finite(
            dual["maximum_drawdown_pct"]
        )
        - _finite(directional_only["maximum_drawdown_pct"]),
        "execution_cost_difference_usd": _finite(
            dual["external_execution_cost_usd"]
        )
        - _finite(directional_only["external_execution_cost_usd"]),
        "external_parent_turnover_difference_lots": int(
            dual["external_parent_turnover_lots"]
        )
        - int(directional_only["external_parent_turnover_lots"]),
        "dual_internalized_child_lot_sides": int(
            dual["internalized_child_lot_sides"]
        ),
    }


def _aggregate_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    metric_names = (
        "ending_equity_difference_usd",
        "cumulative_return_difference_pct_points",
        "provisional_annualized_return_difference_pct_points",
        "annualized_volatility_difference_pct_points",
        "maximum_drawdown_difference_pct_points",
        "execution_cost_difference_usd",
        "external_parent_turnover_difference_lots",
        "dual_internalized_child_lot_sides",
    )
    result: dict[str, Any] = {}
    for metric in metric_names:
        values = sorted(_finite(row[metric]) for row in rows)
        result[metric] = {
            "minimum": values[0],
            "median": median(values),
            "maximum": values[-1],
        }
    return result


def audit_oil_multi_strategy_gate_b_runtime(
    *,
    seeds: Sequence[int] = (42,),
    maximum_turns: int = 6,
) -> dict[str, Any]:
    """Run the first mechanically valid fixed-amount two-strategy comparison."""

    scenario_rows: list[dict[str, Any]] = []
    difference_rows: list[dict[str, Any]] = []
    for seed_value in seeds:
        seed = int(seed_value)
        run = run_global_macro(seed, 7)
        control_report = simulate_oil_multi_strategy_runtime(
            run,
            strategy_authorizations_usd={
                DIRECTIONAL_STRATEGY_ID: 10_000_000.0,
                CALENDAR_SPREAD_STRATEGY_ID: 0.0,
            },
            maximum_turns=maximum_turns,
        )
        dual_report = simulate_oil_multi_strategy_runtime(
            run,
            strategy_authorizations_usd={
                DIRECTIONAL_STRATEGY_ID: 5_000_000.0,
                CALENDAR_SPREAD_STRATEGY_ID: 5_000_000.0,
            },
            maximum_turns=maximum_turns,
        )
        control = _scenario_summary(control_report)
        dual = _scenario_summary(dual_report)
        difference = _paired_difference(control, dual)
        scenario_rows.append(
            {
                "seed": seed,
                "directional_only": control,
                "fixed_5m_5m": dual,
                "difference_dual_minus_directional_only": difference,
                "report_hashes": {
                    "directional_only": control_report["identity"]["result_hash"],
                    "fixed_5m_5m": dual_report["identity"]["result_hash"],
                },
            }
        )
        difference_rows.append(difference)

    hard_gates = {
        "all_scenarios_completed_at_least_one_turn": all(
            min(
                int(row["directional_only"]["completed_turns"]),
                int(row["fixed_5m_5m"]["completed_turns"]),
            )
            >= 1
            for row in scenario_rows
        ),
        "all_directional_only_mechanical_gates_pass": all(
            bool(row["directional_only"]["all_mechanical_hard_gates_pass"])
            for row in scenario_rows
        ),
        "all_fixed_5m_5m_mechanical_gates_pass": all(
            bool(row["fixed_5m_5m"]["all_mechanical_hard_gates_pass"])
            for row in scenario_rows
        ),
        "all_reports_explicitly_lifecycle_incomplete": all(
            not bool(row["directional_only"]["long_horizon_economic_result_valid"])
            and not bool(row["fixed_5m_5m"]["long_horizon_economic_result_valid"])
            for row in scenario_rows
        ),
    }
    result = {
        "schemaVersion": "asset-simulation-oil-multi-strategy-gate-b-audit-v1",
        "model_version": OIL_MULTI_STRATEGY_RUNTIME_MODEL_VERSION,
        "scope": {
            "directional_only_authorization_usd": {
                DIRECTIONAL_STRATEGY_ID: 10_000_000.0,
                CALENDAR_SPREAD_STRATEGY_ID: 0.0,
            },
            "fixed_5m_5m_authorization_usd": {
                DIRECTIONAL_STRATEGY_ID: 5_000_000.0,
                CALENDAR_SPREAD_STRATEGY_ID: 5_000_000.0,
            },
            "maximum_turns": int(maximum_turns),
            "roll_scheduler_enabled": False,
            "round_trip_turnover_enabled": False,
            "economic_interpretation": (
                "mechanically valid pre-roll comparison; provisional annualization "
                "is not a long-horizon calibrated result"
            ),
        },
        "seeds": [int(value) for value in seeds],
        "scenarios": scenario_rows,
        "aggregateDifferenceDualMinusDirectionalOnly": _aggregate_rows(
            difference_rows
        ),
        "hardGates": hard_gates,
        "all_hard_gates_pass": all(hard_gates.values()),
    }
    rounded = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return {"identity": {"result_hash": sha256_json(rounded)}, **rounded}


def _parse_seeds(value: str) -> list[int]:
    result = [int(item.strip()) for item in str(value).split(",") if item.strip()]
    if not result or any(item < 0 for item in result):
        raise argparse.ArgumentTypeError("seeds must be non-negative comma-separated integers")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=_parse_seeds, default=[42])
    parser.add_argument("--maximum-turns", type=int, default=6)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_oil_multi_strategy_gate_b_runtime(
        seeds=args.seeds,
        maximum_turns=args.maximum_turns,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["all_hard_gates_pass"]:
        raise SystemExit("Gate B dual-strategy runtime audit failed")


if __name__ == "__main__":
    main()
