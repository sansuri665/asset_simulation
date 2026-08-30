"""Vector-aware development dual-strategy pre-trade allocator.

v0.1 proved the shared-limit mechanism with one Directional contract and one
Calendar-Spread pair.  v0.2 accepts the mature Directional strategy's whole
named-contract ordinary request vector and applies one Formal Account's market
and initial-margin constraints before execution.

The allocator still creates no fills and owns no cash.  Internal netting is a
market-footprint preview only; transfer price and fill attribution belong to the
settlement owner.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .oil_multi_strategy_pretrade_allocator import (
    OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_MODEL_VERSION as V1_MODEL_VERSION,
    _adjacent_main_pair,
    _canonical_positions,
    _contract_flow_summary,
    _evaluate_hard_limits,
    _market_contracts,
    _round_nested,
)
from .registry import load_registered_assets, sha256_json


OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_V2_MODEL_VERSION = (
    "asset-simulation-oil-multi-strategy-pretrade-allocator-v0.2.0"
)
OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_V2_CONTRACT_ID = (
    "oil_multi_strategy_pretrade_allocator_v2"
)


def _assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_multi_strategy_pretrade_allocator_v2_config"]
    contract = assets["oil_multi_strategy_pretrade_allocator_v2_contract"]
    if config["model_version"] != OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_V2_MODEL_VERSION:
        raise ValueError("registered pretrade allocator v2 config version mismatch")
    if config["prior_model_version"] != V1_MODEL_VERSION:
        raise ValueError("pretrade allocator v2 prior model mismatch")
    if contract["contract_id"] != OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_V2_CONTRACT_ID:
        raise ValueError("registered pretrade allocator v2 contract id mismatch")
    if contract["model_version"] != OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_V2_MODEL_VERSION:
        raise ValueError("registered pretrade allocator v2 contract version mismatch")
    entitlements = {
        key: float(value)
        for key, value in dict(config["strategy_entitlements"]).items()
    }
    if entitlements != {"directional": 0.5, "calendar_spread": 0.5}:
        raise ValueError("development pretrade allocator v2 must remain fixed 50:50")
    formal = dict(config["formal_account_constraints"])
    if not math.isclose(float(formal["maximum_initial_margin_pct_of_equity"]), 90.0):
        raise ValueError("pretrade allocator v2 formal-account margin ceiling drifted")
    if not math.isclose(float(formal["minimum_free_collateral_pct_of_equity"]), 10.0):
        raise ValueError("pretrade allocator v2 free-collateral floor drifted")
    return assets, config, contract


def _integer_map(value: Mapping[str, Any] | None, label: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for raw_key, raw_value in dict(value or {}).items():
        contract_id = str(raw_key)
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise TypeError(f"{label} {contract_id} must be integer lots")
        lots = int(raw_value)
        if lots:
            result[contract_id] = lots
    return dict(sorted(result.items()))


def _merge_maps(*maps: Mapping[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for values in maps:
        for key, value in values.items():
            result[str(key)] = result.get(str(key), 0) + int(value)
    return {key: value for key, value in sorted(result.items()) if value}


def _allocate_integer(total: int, weights: Sequence[int]) -> list[int]:
    amount = int(total)
    if amount < 0:
        raise ValueError("pretrade vector allocation total cannot be negative")
    if not weights:
        return []
    positive = [max(0, int(value)) for value in weights]
    denominator = sum(positive)
    if denominator <= 0:
        return [0] * len(positive)
    raw = [amount * value / denominator for value in positive]
    allocated = [math.floor(value) for value in raw]
    remainder = amount - sum(allocated)
    order = sorted(
        range(len(raw)),
        key=lambda index: (raw[index] - allocated[index], -index),
        reverse=True,
    )
    for index in order[:remainder]:
        allocated[index] += 1
    return allocated


def _directional_vector_at_progress(
    requested: Mapping[str, int], progress_gross_lots: int
) -> dict[str, int]:
    ids = sorted(requested)
    weights = [abs(int(requested[key])) for key in ids]
    total = sum(weights)
    progress = max(0, min(int(progress_gross_lots), total))
    allocation = _allocate_integer(progress, weights)
    result: dict[str, int] = {}
    for contract_id, absolute in zip(ids, allocation, strict=True):
        if absolute:
            result[contract_id] = (
                absolute if int(requested[contract_id]) > 0 else -absolute
            )
    return result


def _spread_vector(
    main_id: str,
    next_id: str,
    spread_units: int,
) -> dict[str, int]:
    units = int(spread_units)
    result: dict[str, int] = {}
    if units:
        result[main_id] = units
        result[next_id] = -units
    return result


def _strategy_flows(
    *,
    directional_mandatory: Mapping[str, int],
    directional_ordinary: Mapping[str, int],
    spread_remediation: Mapping[str, int],
    spread_ordinary: Mapping[str, int],
) -> dict[str, dict[str, int]]:
    return {
        "directional": _merge_maps(directional_mandatory, directional_ordinary),
        "calendar_spread": _merge_maps(spread_remediation, spread_ordinary),
    }


def _initial_margin(
    positions: Mapping[str, int],
    market: Mapping[str, Any],
) -> float:
    contracts = _market_contracts(market)
    specification = dict(market["contractSpecification"])
    contract_size = float(specification["contract_size_bbl"])
    initial_rate = float(specification["initial_margin_rate_pct"]) / 100.0
    if not 0.0 < initial_rate < 1.0:
        raise ValueError("pretrade allocator v2 initial margin rate is invalid")
    total = 0.0
    for contract_id, lots in positions.items():
        contract = contracts.get(str(contract_id))
        if contract is None:
            raise ValueError(
                f"pretrade allocator v2 margin contract unavailable: {contract_id}"
            )
        total += (
            abs(int(lots))
            * float(contract["price_usd"])
            * contract_size
            * initial_rate
        )
    return total


def _evaluate(
    market: Mapping[str, Any],
    account_positions: Mapping[str, int],
    strategy_flows: Mapping[str, Mapping[str, int]],
    *,
    account_equity_usd: float,
    maximum_initial_margin_pct: float,
) -> dict[str, Any]:
    hard = _evaluate_hard_limits(market, account_positions, strategy_flows)
    final_positions = dict(hard["final_account_positions"])
    initial_margin = _initial_margin(final_positions, market)
    margin_cap = float(account_equity_usd) * float(maximum_initial_margin_pct) / 100.0
    margin_ok = initial_margin <= margin_cap + 0.01
    violations = list(hard["violations"])
    if not margin_ok:
        violations.append("formal_account_initial_margin_cap")
    return {
        **hard,
        "ok": bool(hard["ok"]) and margin_ok,
        "initial_margin_usd": initial_margin,
        "maximum_initial_margin_usd": margin_cap,
        "initial_margin_pct_of_equity": (
            0.0
            if float(account_equity_usd) <= 0.0
            else 100.0 * initial_margin / float(account_equity_usd)
        ),
        "initial_margin_cap_ok": margin_ok,
        "violations": violations,
    }


def _same_pair_remediation(
    *,
    main_id: str,
    next_id: str,
    request: Mapping[str, Any],
) -> dict[str, int]:
    main = request.get("remediation_main_delta_lots", 0)
    next_main = request.get("remediation_next_main_delta_lots", 0)
    for value, label in (
        (main, "calendar-spread main remediation"),
        (next_main, "calendar-spread next-main remediation"),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{label} must be integer lots")
    return _merge_maps(
        {main_id: int(main)} if int(main) else {},
        {next_id: int(next_main)} if int(next_main) else {},
    )


def allocate_oil_dual_strategy_pretrade_v2(
    market: Mapping[str, Any],
    *,
    account_positions: Mapping[str, Any] | None,
    account_equity_usd: float,
    directional_request: Mapping[str, Any],
    calendar_spread_request: Mapping[str, Any],
) -> dict[str, Any]:
    """Allocate one Directional vector and one atomic Calendar Spread request."""

    assets, config, contract = _assets()
    positions = _canonical_positions(account_positions)
    contracts = _market_contracts(market)
    equity = float(account_equity_usd)
    if not math.isfinite(equity) or equity <= 0.0:
        raise ValueError("pretrade allocator v2 account equity must be positive")

    directional = dict(directional_request)
    spread = dict(calendar_spread_request)
    directional_strategy_id = str(directional.get("strategy_id", "")).strip()
    spread_strategy_id = str(spread.get("strategy_id", "")).strip()
    if not directional_strategy_id or not spread_strategy_id:
        raise ValueError("pretrade allocator v2 requires two strategy ids")
    if directional_strategy_id == spread_strategy_id:
        raise ValueError("pretrade allocator v2 strategy ids must be distinct")

    directional_requested = _integer_map(
        directional.get("requested_deltas"), "directional requested delta"
    )
    directional_mandatory = _integer_map(
        directional.get("mandatory_deltas"), "directional mandatory delta"
    )
    for contract_id in set(directional_requested) | set(directional_mandatory):
        if contract_id not in contracts:
            raise ValueError(
                f"directional pretrade vector contains unavailable contract {contract_id}"
            )

    main_id, next_id = _adjacent_main_pair(market)
    if str(spread.get("main_contract_id", "")) != main_id or str(
        spread.get("next_main_contract_id", "")
    ) != next_id:
        raise ValueError(
            "calendar-spread pretrade v2 request must use current main and adjacent next"
        )
    raw_spread_units = spread.get("requested_pair_delta_units", 0)
    if isinstance(raw_spread_units, bool) or not isinstance(raw_spread_units, int):
        raise TypeError("calendar-spread requested pair delta must be integer units")
    spread_requested_units = int(raw_spread_units)
    spread_sign = (
        1 if spread_requested_units > 0 else -1 if spread_requested_units < 0 else 0
    )
    spread_requested_abs = abs(spread_requested_units)
    spread_remediation = _same_pair_remediation(
        main_id=main_id, next_id=next_id, request=spread
    )

    maximum_initial_margin_pct = float(
        config["formal_account_constraints"]["maximum_initial_margin_pct_of_equity"]
    )

    mandatory_flows = _strategy_flows(
        directional_mandatory=directional_mandatory,
        directional_ordinary={},
        spread_remediation=spread_remediation,
        spread_ordinary={},
    )
    mandatory_check = _evaluate(
        market,
        positions,
        mandatory_flows,
        account_equity_usd=equity,
        maximum_initial_margin_pct=maximum_initial_margin_pct,
    )
    if not bool(mandatory_check["ok"]):
        result = {
            "schemaVersion": "asset-simulation-oil-dual-strategy-pretrade-allocation-v2",
            "status": "mandatory_reduction_blocked_by_shared_hard_constraint",
            "allocationPolicy": {
                "policy_id": str(config["allocation_policy_id"]),
                "strategy_entitlements": dict(config["strategy_entitlements"]),
                "development_only": True,
            },
            "accountBefore": positions,
            "accountEquityUsd": equity,
            "mandatoryPhase": mandatory_check,
            "ordinaryAllocation": {
                "directional_allocated_deltas": {},
                "directional_allocated_gross_lots": 0,
                "calendar_spread_allocated_units": 0,
            },
            "escalation": {
                "required": True,
                "owner": "future_portfolio_risk",
                "reason": "shared_hard_constraint_blocks_mandatory_strategy_risk_reduction",
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
            "model_version": OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_V2_MODEL_VERSION,
            "allocation_policy_id": str(config["allocation_policy_id"]),
            "config_hash": assets[
                "oil_multi_strategy_pretrade_allocator_v2_config_hash"
            ],
            "field_contract_id": str(contract["contract_id"]),
            "field_contract_hash": assets[
                "oil_multi_strategy_pretrade_allocator_v2_contract_hash"
            ],
            "market_result_hash": str(market["identity"]["result_hash"]),
            "account_positions_hash": sha256_json(positions),
            "account_equity_usd": equity,
            "directional_request_hash": sha256_json(directional),
            "calendar_spread_request_hash": sha256_json(spread),
            "write_back": False,
            "result_hash": sha256_json(rounded),
        }
        identity["identity_hash"] = sha256_json(identity)
        return _round_nested({"identity": identity, **rounded})

    directional_total = sum(abs(value) for value in directional_requested.values())
    directional_progress = 0
    spread_progress = 0

    def flows_at(dir_progress: int, spread_progress_abs: int) -> dict[str, dict[str, int]]:
        dir_vector = _directional_vector_at_progress(
            directional_requested, dir_progress
        )
        spread_vector = _spread_vector(
            main_id, next_id, spread_sign * int(spread_progress_abs)
        )
        return _strategy_flows(
            directional_mandatory=directional_mandatory,
            directional_ordinary=dir_vector,
            spread_remediation=spread_remediation,
            spread_ordinary=spread_vector,
        )

    def evaluate_at(dir_progress: int, spread_progress_abs: int) -> dict[str, Any]:
        return _evaluate(
            market,
            positions,
            flows_at(dir_progress, spread_progress_abs),
            account_equity_usd=equity,
            maximum_initial_margin_pct=maximum_initial_margin_pct,
        )

    entitlements = {
        "directional": float(config["strategy_entitlements"]["directional"]),
        "calendar_spread": float(
            config["strategy_entitlements"]["calendar_spread"]
        ),
    }

    while True:
        available = []
        if directional_progress < directional_total:
            available.append("directional")
        if spread_progress < spread_requested_abs:
            available.append("calendar_spread")
        if not available:
            break

        def service(strategy: str) -> float:
            if strategy == "directional":
                fraction = (
                    1.0
                    if directional_total == 0
                    else directional_progress / directional_total
                )
            else:
                fraction = (
                    1.0
                    if spread_requested_abs == 0
                    else spread_progress / spread_requested_abs
                )
            return fraction / max(1e-12, entitlements[strategy])

        available.sort(key=lambda strategy: (service(strategy), strategy))
        progressed = False
        for strategy in available:
            next_dir = directional_progress + (1 if strategy == "directional" else 0)
            next_spread = spread_progress + (
                1 if strategy == "calendar_spread" else 0
            )
            candidate = evaluate_at(next_dir, next_spread)
            if bool(candidate["ok"]):
                directional_progress = next_dir
                spread_progress = next_spread
                progressed = True
                break

        if progressed:
            continue

        if (
            directional_progress < directional_total
            and spread_progress < spread_requested_abs
        ):
            joint = evaluate_at(directional_progress + 1, spread_progress + 1)
            if bool(joint["ok"]):
                directional_progress += 1
                spread_progress += 1
                continue
        break

    directional_allocated = _directional_vector_at_progress(
        directional_requested, directional_progress
    )
    spread_allocated_units = spread_sign * spread_progress
    spread_allocated = _spread_vector(main_id, next_id, spread_allocated_units)
    final_flows = _strategy_flows(
        directional_mandatory=directional_mandatory,
        directional_ordinary=directional_allocated,
        spread_remediation=spread_remediation,
        spread_ordinary=spread_allocated,
    )
    final_check = _evaluate(
        market,
        positions,
        final_flows,
        account_equity_usd=equity,
        maximum_initial_margin_pct=maximum_initial_margin_pct,
    )
    if not bool(final_check["ok"]):
        raise ValueError("pretrade allocator v2 produced a shared hard-constraint violation")

    flow_summary = _contract_flow_summary(final_flows)
    external_orders = {
        contract_id: int(item["external_delta_lots"])
        for contract_id, item in sorted(flow_summary.items())
        if int(item["external_delta_lots"]) != 0
    }
    directional_total_deltas = dict(final_flows["directional"])
    spread_total_deltas = dict(final_flows["calendar_spread"])

    if int(spread_allocated.get(main_id, 0)) + int(
        spread_allocated.get(next_id, 0)
    ) != 0:
        raise ValueError("pretrade allocator v2 broke ordinary spread pair atomicity")

    directional_fraction = (
        1.0 if directional_total == 0 else directional_progress / directional_total
    )
    spread_fraction = (
        1.0
        if spread_requested_abs == 0
        else spread_progress / spread_requested_abs
    )
    unused_reallocated = (
        (
            directional_progress < directional_total
            and spread_fraction > directional_fraction
        )
        or (
            spread_progress < spread_requested_abs
            and directional_fraction > spread_fraction
        )
    )

    result = {
        "schemaVersion": "asset-simulation-oil-dual-strategy-pretrade-allocation-v2",
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
            "service_metric": str(
                config["ordinary_risk_increase_policy"]["service_metric"]
            ),
            "development_only": True,
        },
        "accountBefore": positions,
        "accountEquityUsd": equity,
        "requests": {
            "directional": {
                "strategy_id": directional_strategy_id,
                "ordinary_requested_deltas": directional_requested,
                "ordinary_requested_gross_lots": directional_total,
                "mandatory_deltas": directional_mandatory,
            },
            "calendar_spread": {
                "strategy_id": spread_strategy_id,
                "main_contract_id": main_id,
                "next_main_contract_id": next_id,
                "ordinary_requested_pair_units": spread_requested_units,
                "remediation_deltas": spread_remediation,
            },
        },
        "mandatoryPhase": mandatory_check,
        "ordinaryAllocation": {
            "directional_allocated_deltas": directional_allocated,
            "directional_allocated_gross_lots": directional_progress,
            "directional_fulfillment_ratio": directional_fraction,
            "calendar_spread_allocated_units": spread_allocated_units,
            "calendar_spread_allocated_pair_lots": spread_progress,
            "calendar_spread_fulfillment_ratio": spread_fraction,
            "unused_entitlement_reallocated": unused_reallocated,
            "directional_final_request_vector_preserved": (
                directional_progress < directional_total
                or directional_allocated == directional_requested
            ),
        },
        "strategyAllocatedDeltas": {
            "directional": {
                "strategy_id": directional_strategy_id,
                "ordinary_deltas": directional_allocated,
                "mandatory_deltas": directional_mandatory,
                "deltas": directional_total_deltas,
            },
            "calendar_spread": {
                "strategy_id": spread_strategy_id,
                "ordinary_pair_units": spread_allocated_units,
                "ordinary_deltas": spread_allocated,
                "remediation_deltas": spread_remediation,
                "deltas": spread_total_deltas,
                "ordinary_pair_balance_ok": (
                    int(spread_allocated.get(main_id, 0))
                    + int(spread_allocated.get(next_id, 0))
                    == 0
                ),
            },
        },
        "internalNettingPreview": {
            "by_contract": flow_summary,
            "external_market_orders": external_orders,
            "transfer_price_assigned": False,
            "fills_created": False,
        },
        "hardLimitChecks": {
            **final_check,
            "all_hard_limits_ok": bool(final_check["ok"]),
        },
        "governance": {
            "fills_created": False,
            "formal_account_mutated": False,
            "strategy_books_mutated": False,
            "market_write_back": False,
            "directional_vector_scaled_as_one_strategy": True,
            "calendar_spread_pair_atomic": True,
            "formal_account_margin_checked_once": True,
        },
    }
    rounded = _round_nested(result)
    identity = {
        "model_version": OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_V2_MODEL_VERSION,
        "allocation_policy_id": str(config["allocation_policy_id"]),
        "config_hash": assets[
            "oil_multi_strategy_pretrade_allocator_v2_config_hash"
        ],
        "field_contract_id": str(contract["contract_id"]),
        "field_contract_hash": assets[
            "oil_multi_strategy_pretrade_allocator_v2_contract_hash"
        ],
        "market_result_hash": str(market["identity"]["result_hash"]),
        "account_positions_hash": sha256_json(positions),
        "account_equity_usd": equity,
        "directional_request_hash": sha256_json(directional),
        "calendar_spread_request_hash": sha256_json(spread),
        "write_back": False,
        "result_hash": sha256_json(rounded),
    }
    identity["identity_hash"] = sha256_json(identity)
    return _round_nested({"identity": identity, **rounded})
