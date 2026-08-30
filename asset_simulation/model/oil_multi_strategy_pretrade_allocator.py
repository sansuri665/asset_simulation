"""Development-only dual-strategy oil pre-trade limit allocator.

The allocator sits after strategy-specific risk approval and before Trading Desk
execution.  It keeps Directional and Calendar-Spread ownership separate while
checking the one formal account against published named-contract position and
turn limits.  Ordinary risk-increasing flow uses a fixed 50:50 development
entitlement.  Existing strategy-risk reductions have first priority but never
override exchange hard limits.

This module creates no fills and settles no cash or margin.  Internal netting is
reported only as a pre-trade market-footprint preview for the later settlement
ledger.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .registry import load_registered_assets, sha256_json


OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_MODEL_VERSION = (
    "asset-simulation-oil-multi-strategy-pretrade-allocator-v0.1.0"
)
OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_CONTRACT_ID = (
    "oil_multi_strategy_pretrade_allocator_v1"
)


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("pretrade allocator contains a non-finite value")
        return round(value, 8)
    return value


def _assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_multi_strategy_pretrade_allocator_config"]
    contract = assets["oil_multi_strategy_pretrade_allocator_contract"]
    if config["model_version"] != OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_MODEL_VERSION:
        raise ValueError("registered pretrade allocator config version mismatch")
    if contract["contract_id"] != OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_CONTRACT_ID:
        raise ValueError("registered pretrade allocator contract id mismatch")
    if contract["model_version"] != OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_MODEL_VERSION:
        raise ValueError("registered pretrade allocator contract version mismatch")
    entitlements = dict(config["strategy_entitlements"])
    if set(entitlements) != {"directional", "calendar_spread"}:
        raise ValueError("pretrade allocator requires exactly two strategy entitlements")
    if not all(math.isclose(float(value), 0.5) for value in entitlements.values()):
        raise ValueError("development pretrade allocator must retain fixed 50:50 entitlements")
    if not math.isclose(sum(float(value) for value in entitlements.values()), 1.0):
        raise ValueError("pretrade allocator entitlements must sum to one")
    return assets, config, contract


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer lot/unit value")
    return int(value)


def _canonical_positions(positions: Mapping[str, Any] | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, raw in dict(positions or {}).items():
        contract_id = str(key)
        lots = _integer(raw, f"account position {contract_id}")
        if lots:
            result[contract_id] = lots
    return dict(sorted(result.items()))


def _market_contracts(market: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if not bool(market.get("ok")):
        raise ValueError("pretrade allocator requires a successful oil market payload")
    result: dict[str, dict[str, Any]] = {}
    for raw in market.get("curve", {}).get("contracts", ()):
        item = dict(raw)
        contract_id = str(item.get("contract_id", ""))
        if not contract_id or contract_id in result:
            raise ValueError("pretrade allocator market contract ids must be unique")
        limits = dict(item.get("participantLimits", {}))
        for field in (
            "single_contract_position_limit_lots",
            "turn_trade_limit_lots",
            "new_trades_allowed",
        ):
            if field not in limits:
                raise ValueError(f"market participant limits missing {field}")
        result[contract_id] = item
    if not result:
        raise ValueError("pretrade allocator market has no named oil contracts")
    return result


def _adjacent_main_pair(market: Mapping[str, Any]) -> tuple[str, str]:
    contracts = [
        str(item["contract_id"])
        for item in market.get("curve", {}).get("contracts", ())
    ]
    main_id = str(market.get("curve", {}).get("main_contract_id", ""))
    if main_id not in contracts:
        raise ValueError("pretrade allocator cannot resolve current main contract")
    index = contracts.index(main_id)
    if index + 1 >= len(contracts):
        raise ValueError("pretrade allocator cannot resolve adjacent next-main contract")
    return main_id, contracts[index + 1]


def _strategy_flow_map(
    *,
    directional_contract_id: str,
    directional_delta_lots: int,
    spread_main_contract_id: str,
    spread_next_contract_id: str,
    spread_delta_units: int,
    directional_mandatory_delta_lots: int,
    spread_remediation_main_delta_lots: int,
    spread_remediation_next_delta_lots: int,
) -> dict[str, dict[str, int]]:
    directional: dict[str, int] = {}
    spread: dict[str, int] = {}
    if directional_mandatory_delta_lots or directional_delta_lots:
        directional[directional_contract_id] = (
            directional_mandatory_delta_lots + directional_delta_lots
        )
    if spread_remediation_main_delta_lots or spread_delta_units:
        spread[spread_main_contract_id] = (
            spread_remediation_main_delta_lots + spread_delta_units
        )
    if spread_remediation_next_delta_lots or spread_delta_units:
        spread[spread_next_contract_id] = (
            spread_remediation_next_delta_lots - spread_delta_units
        )
    return {
        "directional": {key: value for key, value in directional.items() if value},
        "calendar_spread": {key: value for key, value in spread.items() if value},
    }


def _contract_flow_summary(
    strategy_flows: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int]]:
    contract_ids = sorted(
        {
            contract_id
            for flows in strategy_flows.values()
            for contract_id in flows
        }
    )
    result: dict[str, dict[str, int]] = {}
    for contract_id in contract_ids:
        deltas = {
            strategy: int(flows.get(contract_id, 0))
            for strategy, flows in strategy_flows.items()
        }
        buys = sum(max(0, value) for value in deltas.values())
        sells = sum(max(0, -value) for value in deltas.values())
        internal_cross = min(buys, sells)
        external_delta = buys - sells
        result[contract_id] = {
            "gross_strategy_buy_lots": buys,
            "gross_strategy_sell_lots": sells,
            "gross_strategy_flow_lots": buys + sells,
            "internal_cross_lots": internal_cross,
            "external_delta_lots": external_delta,
            "external_gross_turnover_lots": abs(external_delta),
            "market_turnover_saved_lots": 2 * internal_cross,
        }
    return result


def _evaluate_hard_limits(
    market: Mapping[str, Any],
    account_positions: Mapping[str, int],
    strategy_flows: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    contracts = _market_contracts(market)
    flows = _contract_flow_summary(strategy_flows)
    relevant = sorted(set(account_positions) | set(flows))
    checks: dict[str, dict[str, Any]] = {}
    final_positions: dict[str, int] = {}
    violations: list[str] = []

    for contract_id in relevant:
        if contract_id not in contracts:
            raise ValueError(
                f"pretrade allocator cannot use unavailable contract {contract_id}"
            )
        limits = dict(contracts[contract_id]["participantLimits"])
        starting = int(account_positions.get(contract_id, 0))
        external_delta = int(flows.get(contract_id, {}).get("external_delta_lots", 0))
        external_gross = abs(external_delta)
        ending = starting + external_delta
        position_limit = int(limits["single_contract_position_limit_lots"])
        turn_limit = int(limits["turn_trade_limit_lots"])
        new_trades_allowed = bool(limits["new_trades_allowed"])
        position_ok = abs(ending) <= position_limit
        turn_ok = external_gross <= turn_limit
        new_trade_ok = new_trades_allowed or abs(ending) <= abs(starting)
        contract_violations: list[str] = []
        if not position_ok:
            contract_violations.append("single_contract_position_limit")
        if not turn_ok:
            contract_violations.append("external_turn_trade_limit")
        if not new_trade_ok:
            contract_violations.append("new_trades_disabled_increase")
        if contract_violations:
            violations.extend(
                f"{contract_id}:{value}" for value in contract_violations
            )
        if ending:
            final_positions[contract_id] = ending
        checks[contract_id] = {
            "starting_account_position_lots": starting,
            "external_delta_lots": external_delta,
            "ending_account_position_lots": ending,
            "single_contract_position_limit_lots": position_limit,
            "external_turn_trade_limit_lots": turn_limit,
            "new_trades_allowed": new_trades_allowed,
            "position_limit_ok": position_ok,
            "turn_limit_ok": turn_ok,
            "new_trade_rule_ok": new_trade_ok,
            "violations": contract_violations,
        }

    gross_cap = int(
        market.get("participantLimitsPolicy", {})[
            "all_contract_gross_position_cap_lots"
        ]
    )
    gross_position = sum(abs(value) for value in final_positions.values())
    gross_ok = gross_position <= gross_cap
    if not gross_ok:
        violations.append("all_contract_gross_position_cap")
    return {
        "ok": not violations,
        "contract_checks": checks,
        "final_account_positions": dict(sorted(final_positions.items())),
        "final_account_gross_position_lots": gross_position,
        "all_contract_gross_position_cap_lots": gross_cap,
        "all_contract_gross_position_cap_ok": gross_ok,
        "violations": violations,
        "contract_flow": flows,
    }


def _request_sign(value: int) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def allocate_oil_dual_strategy_pretrade(
    market: Mapping[str, Any],
    *,
    account_positions: Mapping[str, Any] | None,
    directional_request: Mapping[str, Any],
    calendar_spread_request: Mapping[str, Any],
) -> dict[str, Any]:
    """Allocate two risk-approved strategy requests against shared hard limits."""

    assets, config, contract = _assets()
    positions = _canonical_positions(account_positions)
    contracts = _market_contracts(market)
    expected_main, expected_next = _adjacent_main_pair(market)

    directional = dict(directional_request)
    spread = dict(calendar_spread_request)
    directional_strategy_id = str(directional.get("strategy_id", "")).strip()
    spread_strategy_id = str(spread.get("strategy_id", "")).strip()
    if not directional_strategy_id or not spread_strategy_id:
        raise ValueError("both pretrade strategy ids are required")
    if directional_strategy_id == spread_strategy_id:
        raise ValueError("pretrade strategy ids must be distinct")

    directional_contract_id = str(directional.get("contract_id", ""))
    if directional_contract_id not in contracts:
        raise ValueError("directional pretrade request contract is unavailable")
    spread_main_id = str(spread.get("main_contract_id", ""))
    spread_next_id = str(spread.get("next_main_contract_id", ""))
    if (spread_main_id, spread_next_id) != (expected_main, expected_next):
        raise ValueError("calendar-spread pretrade request must use current main and adjacent next")

    directional_request_delta = _integer(
        directional.get("requested_delta_lots", 0),
        "directional requested delta",
    )
    directional_mandatory = _integer(
        directional.get("mandatory_delta_lots", 0),
        "directional mandatory delta",
    )
    spread_request_units = _integer(
        spread.get("requested_pair_delta_units", 0),
        "calendar-spread requested pair delta",
    )
    remediation_main = _integer(
        spread.get("remediation_main_delta_lots", 0),
        "calendar-spread main remediation",
    )
    remediation_next = _integer(
        spread.get("remediation_next_main_delta_lots", 0),
        "calendar-spread next-main remediation",
    )

    mandatory_flows = _strategy_flow_map(
        directional_contract_id=directional_contract_id,
        directional_delta_lots=0,
        spread_main_contract_id=spread_main_id,
        spread_next_contract_id=spread_next_id,
        spread_delta_units=0,
        directional_mandatory_delta_lots=directional_mandatory,
        spread_remediation_main_delta_lots=remediation_main,
        spread_remediation_next_delta_lots=remediation_next,
    )
    mandatory_check = _evaluate_hard_limits(market, positions, mandatory_flows)
    if not bool(mandatory_check["ok"]):
        result = {
            "schemaVersion": "asset-simulation-oil-dual-strategy-pretrade-allocation-v1",
            "status": "mandatory_reduction_blocked_by_hard_market_limit",
            "allocationPolicy": {
                "policy_id": str(config["allocation_policy_id"]),
                "strategy_entitlements": dict(config["strategy_entitlements"]),
                "development_only": True,
            },
            "accountBefore": positions,
            "mandatoryPhase": mandatory_check,
            "ordinaryAllocation": {
                "directional_allocated_lots": 0,
                "calendar_spread_allocated_units": 0,
            },
            "escalation": {
                "required": True,
                "owner": "future_portfolio_risk",
                "reason": "hard_market_limit_blocks_strategy_level_mandatory_reduction",
            },
            "governance": {
                "fills_created": False,
                "formal_account_mutated": False,
                "strategy_books_mutated": False,
                "market_write_back": False,
            },
        }
        rounded = _round_nested(result)
        identity = {
            "model_version": OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_MODEL_VERSION,
            "allocation_policy_id": str(config["allocation_policy_id"]),
            "config_hash": assets["oil_multi_strategy_pretrade_allocator_config_hash"],
            "field_contract_id": str(contract["contract_id"]),
            "field_contract_hash": assets[
                "oil_multi_strategy_pretrade_allocator_contract_hash"
            ],
            "market_result_hash": str(market["identity"]["result_hash"]),
            "account_positions_hash": sha256_json(positions),
            "directional_request_hash": sha256_json(directional),
            "calendar_spread_request_hash": sha256_json(spread),
            "write_back": False,
            "result_hash": sha256_json(rounded),
        }
        identity["identity_hash"] = sha256_json(identity)
        return _round_nested({"identity": identity, **rounded})

    directional_sign = _request_sign(directional_request_delta)
    spread_sign = _request_sign(spread_request_units)
    directional_requested_abs = abs(directional_request_delta)
    spread_requested_abs = abs(spread_request_units)
    directional_alloc_abs = 0
    spread_alloc_abs = 0
    directional_block_reason: list[str] = []
    spread_block_reason: list[str] = []

    def evaluate_candidate(dir_abs: int, spread_abs: int) -> dict[str, Any]:
        flows = _strategy_flow_map(
            directional_contract_id=directional_contract_id,
            directional_delta_lots=directional_sign * int(dir_abs),
            spread_main_contract_id=spread_main_id,
            spread_next_contract_id=spread_next_id,
            spread_delta_units=spread_sign * int(spread_abs),
            directional_mandatory_delta_lots=directional_mandatory,
            spread_remediation_main_delta_lots=remediation_main,
            spread_remediation_next_delta_lots=remediation_next,
        )
        return _evaluate_hard_limits(market, positions, flows)

    # If the Directional order and one Calendar-Spread leg oppose on the same
    # contract, allocate matched units jointly first.  This makes the internal
    # cross explicit and avoids rejecting one side merely because the other side
    # has not yet been considered.
    opposing_shared_leg = False
    if directional_sign and spread_sign:
        spread_main_sign = spread_sign
        spread_next_sign = -spread_sign
        opposing_shared_leg = (
            directional_contract_id == spread_main_id
            and directional_sign == -spread_main_sign
        ) or (
            directional_contract_id == spread_next_id
            and directional_sign == -spread_next_sign
        )
    if opposing_shared_leg:
        joint_target = min(directional_requested_abs, spread_requested_abs)
        while directional_alloc_abs < joint_target and spread_alloc_abs < joint_target:
            candidate = evaluate_candidate(
                directional_alloc_abs + 1,
                spread_alloc_abs + 1,
            )
            if not bool(candidate["ok"]):
                break
            directional_alloc_abs += 1
            spread_alloc_abs += 1

    entitlements = {
        "directional": float(config["strategy_entitlements"]["directional"]),
        "calendar_spread": float(
            config["strategy_entitlements"]["calendar_spread"]
        ),
    }
    blocked = {"directional": False, "calendar_spread": False}
    requested = {
        "directional": directional_requested_abs,
        "calendar_spread": spread_requested_abs,
    }

    while True:
        active = [
            key
            for key in ("directional", "calendar_spread")
            if not blocked[key]
            and (
                directional_alloc_abs < requested[key]
                if key == "directional"
                else spread_alloc_abs < requested[key]
            )
        ]
        if not active:
            break
        active.sort(
            key=lambda key: (
                (
                    directional_alloc_abs
                    if key == "directional"
                    else spread_alloc_abs
                )
                / entitlements[key],
                key,
            )
        )
        progress = False
        for key in active:
            candidate_dir = directional_alloc_abs + (1 if key == "directional" else 0)
            candidate_spread = spread_alloc_abs + (
                1 if key == "calendar_spread" else 0
            )
            candidate = evaluate_candidate(candidate_dir, candidate_spread)
            if bool(candidate["ok"]):
                directional_alloc_abs = candidate_dir
                spread_alloc_abs = candidate_spread
                progress = True
                break
            blocked[key] = True
            reasons = list(candidate["violations"])
            if key == "directional":
                directional_block_reason = reasons
            else:
                spread_block_reason = reasons
        if not progress and all(blocked[key] for key in active):
            break

    directional_allocated_delta = directional_sign * directional_alloc_abs
    spread_allocated_units = spread_sign * spread_alloc_abs
    final_flows = _strategy_flow_map(
        directional_contract_id=directional_contract_id,
        directional_delta_lots=directional_allocated_delta,
        spread_main_contract_id=spread_main_id,
        spread_next_contract_id=spread_next_id,
        spread_delta_units=spread_allocated_units,
        directional_mandatory_delta_lots=directional_mandatory,
        spread_remediation_main_delta_lots=remediation_main,
        spread_remediation_next_delta_lots=remediation_next,
    )
    final_check = _evaluate_hard_limits(market, positions, final_flows)
    if not bool(final_check["ok"]):
        raise ValueError("pretrade allocator produced a hard-limit violation")

    directional_total_delta = int(
        final_flows["directional"].get(directional_contract_id, 0)
    )
    spread_main_total = int(final_flows["calendar_spread"].get(spread_main_id, 0))
    spread_next_total = int(final_flows["calendar_spread"].get(spread_next_id, 0))
    ordinary_spread_main = spread_allocated_units
    ordinary_spread_next = -spread_allocated_units
    if ordinary_spread_main + ordinary_spread_next != 0:
        raise ValueError("pretrade allocator broke calendar-spread ordinary atomicity")

    result = {
        "schemaVersion": "asset-simulation-oil-dual-strategy-pretrade-allocation-v1",
        "status": "allocated",
        "allocationPolicy": {
            "policy_id": str(config["allocation_policy_id"]),
            "strategy_entitlements": dict(config["strategy_entitlements"]),
            "unallocated_reserve_fraction": float(
                config["unallocated_reserve_fraction"]
            ),
            "ordinary_method": str(
                config["ordinary_risk_increase_policy"]["method"]
            ),
            "development_only": True,
        },
        "accountBefore": positions,
        "requests": {
            "directional": {
                "strategy_id": directional_strategy_id,
                "contract_id": directional_contract_id,
                "mandatory_delta_lots": directional_mandatory,
                "ordinary_requested_delta_lots": directional_request_delta,
            },
            "calendar_spread": {
                "strategy_id": spread_strategy_id,
                "main_contract_id": spread_main_id,
                "next_main_contract_id": spread_next_id,
                "remediation_main_delta_lots": remediation_main,
                "remediation_next_main_delta_lots": remediation_next,
                "ordinary_requested_pair_delta_units": spread_request_units,
            },
        },
        "mandatoryPhase": mandatory_check,
        "ordinaryAllocation": {
            "directional_allocated_lots": directional_allocated_delta,
            "calendar_spread_allocated_units": spread_allocated_units,
            "calendar_spread_allocated_main_lots": ordinary_spread_main,
            "calendar_spread_allocated_next_main_lots": ordinary_spread_next,
            "directional_unfilled_lots": directional_request_delta
            - directional_allocated_delta,
            "calendar_spread_unfilled_units": spread_request_units
            - spread_allocated_units,
            "directional_fill_ratio": (
                1.0
                if directional_requested_abs == 0
                else directional_alloc_abs / directional_requested_abs
            ),
            "calendar_spread_fill_ratio": (
                1.0
                if spread_requested_abs == 0
                else spread_alloc_abs / spread_requested_abs
            ),
            "directional_binding_reasons": directional_block_reason,
            "calendar_spread_binding_reasons": spread_block_reason,
            "unused_entitlement_reallocated": (
                directional_alloc_abs != spread_alloc_abs
                and directional_requested_abs > 0
                and spread_requested_abs > 0
            ),
            "opposing_shared_leg_joint_allocation_used": opposing_shared_leg,
        },
        "strategyAllocatedDeltas": {
            "directional": {
                "strategy_id": directional_strategy_id,
                "deltas": dict(final_flows["directional"]),
                "total_directional_delta_lots": directional_total_delta,
            },
            "calendar_spread": {
                "strategy_id": spread_strategy_id,
                "deltas": dict(final_flows["calendar_spread"]),
                "total_main_delta_lots": spread_main_total,
                "total_next_main_delta_lots": spread_next_total,
                "ordinary_pair_balance_ok": ordinary_spread_main
                + ordinary_spread_next
                == 0,
            },
        },
        "internalNettingPreview": {
            "enabled": True,
            "by_contract": dict(final_check["contract_flow"]),
            "strategy_ownership_preserved": True,
            "external_market_orders": {
                key: int(value["external_delta_lots"])
                for key, value in final_check["contract_flow"].items()
                if int(value["external_delta_lots"]) != 0
            },
            "fills_created": False,
            "transfer_price_assigned": False,
        },
        "hardLimitChecks": {
            "contracts": dict(final_check["contract_checks"]),
            "final_account_positions": dict(final_check["final_account_positions"]),
            "final_account_gross_position_lots": int(
                final_check["final_account_gross_position_lots"]
            ),
            "all_contract_gross_position_cap_lots": int(
                final_check["all_contract_gross_position_cap_lots"]
            ),
            "all_contract_gross_position_cap_ok": bool(
                final_check["all_contract_gross_position_cap_ok"]
            ),
            "all_hard_limits_ok": True,
        },
        "governance": {
            "position_limit_owner": "formal_account_aggregate",
            "turn_limit_owner": "external_market_order_after_internal_netting",
            "strategy_book_ownership_erased": False,
            "fills_created": False,
            "formal_account_mutated": False,
            "strategy_books_mutated": False,
            "cash_or_margin_settled": False,
            "market_write_back": False,
        },
    }
    rounded = _round_nested(result)
    identity = {
        "model_version": OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_MODEL_VERSION,
        "allocation_policy_id": str(config["allocation_policy_id"]),
        "config_hash": assets["oil_multi_strategy_pretrade_allocator_config_hash"],
        "field_contract_id": str(contract["contract_id"]),
        "field_contract_hash": assets[
            "oil_multi_strategy_pretrade_allocator_contract_hash"
        ],
        "market_result_hash": str(market["identity"]["result_hash"]),
        "account_positions_hash": sha256_json(positions),
        "directional_request_hash": sha256_json(directional),
        "calendar_spread_request_hash": sha256_json(spread),
        "write_back": False,
        "result_hash": sha256_json(rounded),
    }
    identity["identity_hash"] = sha256_json(identity)
    return _round_nested({"identity": identity, **rounded})
