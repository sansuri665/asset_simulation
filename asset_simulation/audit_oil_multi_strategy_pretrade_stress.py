"""Stress the development 50:50 dual-strategy pre-trade allocator.

The audit never changes production capital or market-owner limits.  It creates
oversized risk-approved request fixtures relative to the real published limits,
then verifies deterministic arbitration, pair atomicity and internal-netting
semantics.  It is a limit-mechanism audit, not an economic strategy backtest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .model.engine import run_global_macro
from .model.oil_futures_overlay import oil_futures_payload
from .model.oil_multi_strategy_pretrade_allocator import (
    OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_MODEL_VERSION,
    allocate_oil_dual_strategy_pretrade,
)
from .model.registry import load_registered_assets, sha256_json


AUDIT_VERSION = "asset-simulation-oil-multi-strategy-pretrade-stress-v0.1.0"
DEFAULT_SEEDS = (0, 42, 99, 197)
DIRECTIONAL_ID = "forecast_continuation_reversion_directional_edge_sized_turnover_v7"
SPREAD_ID = "oil.short.relative_value.calendar_spread.v1"


def _main_next(market: Mapping[str, Any]) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    contracts = [dict(item) for item in market["curve"]["contracts"]]
    ids = [str(item["contract_id"]) for item in contracts]
    main_id = str(market["curve"]["main_contract_id"])
    index = ids.index(main_id)
    next_id = ids[index + 1]
    mapping = {str(item["contract_id"]): item for item in contracts}
    return main_id, next_id, mapping[main_id], mapping[next_id]


def _limits(contract: Mapping[str, Any]) -> tuple[int, int]:
    limits = dict(contract["participantLimits"])
    return (
        int(limits["single_contract_position_limit_lots"]),
        int(limits["turn_trade_limit_lots"]),
    )


def _positive_capacity(*values: int, cap: int = 1000) -> int:
    candidate = min(*(int(value) for value in values), int(cap))
    if candidate < 2:
        raise ValueError("stress audit requires at least two lots of usable capacity")
    return candidate


def _directional(main_id: str, delta: int, *, mandatory: int = 0) -> dict[str, Any]:
    return {
        "strategy_id": DIRECTIONAL_ID,
        "contract_id": main_id,
        "requested_delta_lots": int(delta),
        "mandatory_delta_lots": int(mandatory),
    }


def _spread(
    main_id: str,
    next_id: str,
    units: int,
    *,
    remediation_main: int = 0,
    remediation_next: int = 0,
) -> dict[str, Any]:
    return {
        "strategy_id": SPREAD_ID,
        "main_contract_id": main_id,
        "next_main_contract_id": next_id,
        "requested_pair_delta_units": int(units),
        "remediation_main_delta_lots": int(remediation_main),
        "remediation_next_main_delta_lots": int(remediation_next),
    }


def _scenario_position_collision(
    market: Mapping[str, Any],
    *,
    multiplier: int,
) -> dict[str, Any]:
    main_id, next_id, main, next_main = _main_next(market)
    main_position, main_turn = _limits(main)
    next_position, next_turn = _limits(next_main)
    headroom = _positive_capacity(
        max(2, main_position // 20),
        main_turn,
        max(2, next_position * 2),
        max(2, next_turn * 2),
    )
    account = {main_id: main_position - headroom}
    request = multiplier * headroom
    report = allocate_oil_dual_strategy_pretrade(
        market,
        account_positions=account,
        directional_request=_directional(main_id, request),
        calendar_spread_request=_spread(main_id, next_id, request),
    )
    directional = abs(int(report["ordinaryAllocation"]["directional_allocated_lots"]))
    spread = abs(int(report["ordinaryAllocation"]["calendar_spread_allocated_units"]))
    final_main = int(report["hardLimitChecks"]["final_account_positions"][main_id])
    passed = (
        final_main == main_position
        and abs(directional - spread) <= 1
        and directional + spread == headroom
        and bool(report["hardLimitChecks"]["all_hard_limits_ok"])
    )
    return {
        "scenario": "shared_position_limit_equal_split",
        "stress_request_multiplier": multiplier,
        "scarce_capacity_lots": headroom,
        "requested_each_lots": request,
        "directional_allocated_lots": directional,
        "calendar_spread_allocated_units": spread,
        "ending_main_position_lots": final_main,
        "main_position_limit_lots": main_position,
        "passed": passed,
    }


def _scenario_turn_collision(
    market: Mapping[str, Any],
    *,
    multiplier: int,
) -> dict[str, Any]:
    main_id, next_id, main, next_main = _main_next(market)
    main_position, main_turn = _limits(main)
    next_position, next_turn = _limits(next_main)
    capacity = _positive_capacity(
        main_turn,
        main_position,
        max(2, next_position * 2),
        max(2, next_turn * 2),
    )
    request = multiplier * capacity
    report = allocate_oil_dual_strategy_pretrade(
        market,
        account_positions={},
        directional_request=_directional(main_id, request),
        calendar_spread_request=_spread(main_id, next_id, request),
    )
    directional = abs(int(report["ordinaryAllocation"]["directional_allocated_lots"]))
    spread = abs(int(report["ordinaryAllocation"]["calendar_spread_allocated_units"]))
    main_flow = report["internalNettingPreview"]["by_contract"][main_id]
    external = int(main_flow["external_gross_turnover_lots"])
    binding_capacity = min(main_turn, main_position)
    expected = min(capacity, binding_capacity)
    passed = (
        external == expected
        and abs(directional - spread) <= 1
        and bool(report["hardLimitChecks"]["all_hard_limits_ok"])
    )
    return {
        "scenario": "shared_turn_or_position_capacity_equal_split",
        "stress_request_multiplier": multiplier,
        "effective_shared_capacity_lots": expected,
        "requested_each_lots": request,
        "directional_allocated_lots": directional,
        "calendar_spread_allocated_units": spread,
        "external_main_turnover_lots": external,
        "published_main_turn_limit_lots": main_turn,
        "published_main_position_limit_lots": main_position,
        "passed": passed,
    }


def _scenario_pair_leg_bottleneck(
    market: Mapping[str, Any],
    *,
    multiplier: int,
) -> dict[str, Any]:
    main_id, next_id, main, next_main = _main_next(market)
    main_position, main_turn = _limits(main)
    next_position, next_turn = _limits(next_main)
    pair_headroom = _positive_capacity(
        max(2, next_position // 20),
        next_turn,
        main_turn,
        max(2, main_position // 2),
        cap=500,
    )
    account = {next_id: -(next_position - pair_headroom)}
    request = multiplier * pair_headroom
    report = allocate_oil_dual_strategy_pretrade(
        market,
        account_positions=account,
        directional_request=_directional(main_id, request),
        calendar_spread_request=_spread(main_id, next_id, request),
    )
    directional = abs(int(report["ordinaryAllocation"]["directional_allocated_lots"]))
    spread = abs(int(report["ordinaryAllocation"]["calendar_spread_allocated_units"]))
    ending_next = int(report["hardLimitChecks"]["final_account_positions"][next_id])
    passed = (
        spread == pair_headroom
        and ending_next == -next_position
        and directional > spread
        and bool(
            report["strategyAllocatedDeltas"]["calendar_spread"][
                "ordinary_pair_balance_ok"
            ]
        )
        and bool(report["hardLimitChecks"]["all_hard_limits_ok"])
    )
    return {
        "scenario": "calendar_spread_second_leg_bottleneck_reallocation",
        "stress_request_multiplier": multiplier,
        "next_leg_headroom_lots": pair_headroom,
        "requested_each_lots": request,
        "directional_allocated_lots": directional,
        "calendar_spread_allocated_units": spread,
        "ending_next_position_lots": ending_next,
        "next_position_limit_lots": next_position,
        "unused_entitlement_reallocated": bool(
            report["ordinaryAllocation"]["unused_entitlement_reallocated"]
        ),
        "passed": passed,
    }


def _scenario_internal_netting(
    market: Mapping[str, Any],
) -> dict[str, Any]:
    main_id, next_id, main, next_main = _main_next(market)
    main_position, _ = _limits(main)
    next_position, next_turn = _limits(next_main)
    units = _positive_capacity(
        max(2, main_position // 4),
        max(2, next_position // 4),
        next_turn,
        cap=1000,
    )
    report = allocate_oil_dual_strategy_pretrade(
        market,
        account_positions={},
        directional_request=_directional(main_id, units),
        calendar_spread_request=_spread(main_id, next_id, -units),
    )
    main_flow = report["internalNettingPreview"]["by_contract"][main_id]
    external_orders = dict(report["internalNettingPreview"]["external_market_orders"])
    passed = (
        int(main_flow["internal_cross_lots"]) == units
        and int(main_flow["external_delta_lots"]) == 0
        and int(main_flow["market_turnover_saved_lots"]) == 2 * units
        and main_id not in external_orders
        and int(external_orders.get(next_id, 0)) == units
        and bool(report["hardLimitChecks"]["all_hard_limits_ok"])
    )
    return {
        "scenario": "opposing_main_flow_internal_netting",
        "requested_each_units": units,
        "internal_main_cross_lots": int(main_flow["internal_cross_lots"]),
        "external_main_delta_lots": int(main_flow["external_delta_lots"]),
        "market_turnover_saved_lots": int(main_flow["market_turnover_saved_lots"]),
        "external_market_orders": external_orders,
        "passed": passed,
    }


def _scenario_mandatory_priority(
    market: Mapping[str, Any],
    *,
    multiplier: int,
) -> dict[str, Any]:
    main_id, next_id, main, next_main = _main_next(market)
    main_position, main_turn = _limits(main)
    next_position, next_turn = _limits(next_main)
    mandatory = _positive_capacity(
        max(2, main_position // 20),
        max(2, main_turn // 2),
        max(2, next_position // 8),
        max(2, next_turn // 8),
        cap=300,
    )
    account = {main_id: mandatory}
    spread_request = multiplier * mandatory
    report = allocate_oil_dual_strategy_pretrade(
        market,
        account_positions=account,
        directional_request=_directional(main_id, 0, mandatory=-mandatory),
        calendar_spread_request=_spread(main_id, next_id, spread_request),
    )
    directional_delta = int(
        report["strategyAllocatedDeltas"]["directional"]["deltas"].get(
            main_id, 0
        )
    )
    main_flow = report["internalNettingPreview"]["by_contract"].get(main_id, {})
    passed = (
        directional_delta == -mandatory
        and bool(report["mandatoryPhase"]["ok"])
        and bool(report["hardLimitChecks"]["all_hard_limits_ok"])
        and int(main_flow.get("internal_cross_lots", 0)) >= 0
    )
    return {
        "scenario": "mandatory_risk_reduction_priority",
        "mandatory_directional_reduction_lots": mandatory,
        "allocated_directional_delta_lots": directional_delta,
        "oversized_spread_request_units": spread_request,
        "spread_allocated_units": int(
            report["ordinaryAllocation"]["calendar_spread_allocated_units"]
        ),
        "internal_main_cross_lots": int(main_flow.get("internal_cross_lots", 0)),
        "passed": passed,
    }


def _run_seed(seed: int, *, request_multiplier: int) -> dict[str, Any]:
    run = run_global_macro(int(seed), 8)
    market = oil_futures_payload(
        run,
        as_of_year=2030,
        as_of_month=1,
        as_of_half=1,
    )
    main_id, next_id, main, next_main = _main_next(market)
    main_position, main_turn = _limits(main)
    next_position, next_turn = _limits(next_main)
    scenarios = [
        _scenario_position_collision(market, multiplier=request_multiplier),
        _scenario_turn_collision(market, multiplier=request_multiplier),
        _scenario_pair_leg_bottleneck(market, multiplier=request_multiplier),
        _scenario_internal_netting(market),
        _scenario_mandatory_priority(market, multiplier=request_multiplier),
    ]
    return {
        "seed": int(seed),
        "cutoff": str(market["asOf"]["label"]),
        "main_contract_id": main_id,
        "next_main_contract_id": next_id,
        "published_limits": {
            "main_position_limit_lots": main_position,
            "main_turn_limit_lots": main_turn,
            "next_position_limit_lots": next_position,
            "next_turn_limit_lots": next_turn,
            "all_contract_gross_position_cap_lots": int(
                market["participantLimitsPolicy"][
                    "all_contract_gross_position_cap_lots"
                ]
            ),
        },
        "scenarios": scenarios,
        "passed": all(bool(item["passed"]) for item in scenarios),
    }


def build_oil_multi_strategy_pretrade_stress_audit(
    *,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    request_multiplier: int = 4,
) -> dict[str, Any]:
    if request_multiplier < 2:
        raise ValueError("stress request multiplier must be at least two")
    rows = [
        _run_seed(int(seed), request_multiplier=int(request_multiplier))
        for seed in seeds
    ]
    scenario_rows = [scenario for row in rows for scenario in row["scenarios"]]
    summary = {
        "seed_count": len(rows),
        "scenario_count": len(scenario_rows),
        "passed_scenarios": sum(bool(item["passed"]) for item in scenario_rows),
        "failed_scenarios": sum(not bool(item["passed"]) for item in scenario_rows),
        "all_seeds_passed": all(bool(row["passed"]) for row in rows),
        "position_collision_passed": all(
            bool(item["passed"])
            for item in scenario_rows
            if item["scenario"] == "shared_position_limit_equal_split"
        ),
        "turn_collision_passed": all(
            bool(item["passed"])
            for item in scenario_rows
            if item["scenario"] == "shared_turn_or_position_capacity_equal_split"
        ),
        "pair_bottleneck_passed": all(
            bool(item["passed"])
            for item in scenario_rows
            if item["scenario"]
            == "calendar_spread_second_leg_bottleneck_reallocation"
        ),
        "internal_netting_passed": all(
            bool(item["passed"])
            for item in scenario_rows
            if item["scenario"] == "opposing_main_flow_internal_netting"
        ),
        "mandatory_priority_passed": all(
            bool(item["passed"])
            for item in scenario_rows
            if item["scenario"] == "mandatory_risk_reduction_priority"
        ),
    }
    assets = load_registered_assets()
    result = {
        "auditVersion": AUDIT_VERSION,
        "allocatorModelVersion": OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_MODEL_VERSION,
        "allocationPolicyId": str(
            assets["oil_multi_strategy_pretrade_allocator_config"][
                "allocation_policy_id"
            ]
        ),
        "stressPolicy": {
            "strategy_entitlement": "50:50",
            "request_multiplier": int(request_multiplier),
            "production_capital_modified": False,
            "market_owner_limits_modified": False,
            "economic_return_gate": False,
        },
        "rows": rows,
        "summary": summary,
    }
    result["result_hash"] = sha256_json(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seeds",
        default=",".join(str(value) for value in DEFAULT_SEEDS),
    )
    parser.add_argument("--request-multiplier", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    seeds = tuple(int(value) for value in str(args.seeds).split(",") if value)
    report = build_oil_multi_strategy_pretrade_stress_audit(
        seeds=seeds,
        request_multiplier=int(args.request_multiplier),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if not bool(report["summary"]["all_seeds_passed"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
