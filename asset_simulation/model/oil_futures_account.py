"""Formal marked-to-market account for the oil futures game runtime.

The trading strategy owns intent and the execution engine owns fills.  This
module is the final, style-free account owner: it restricts collateral, marks
cash through the supplied settlement statement, pays/charges funding, and
forces liquidation after a maintenance-margin breach.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .math_utils import clamp
from .registry import load_registered_assets, sha256_json


OIL_FUTURES_ACCOUNT_MODEL_VERSION = "asset-simulation-oil-futures-account-v0.1.0"
_ROLE_ORDER = ("main", "next_main", "legacy_exit")


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("oil futures account contains a non-finite value")
        return round(value, 6)
    return value


def _assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_futures_account_config"]
    contract = assets["oil_futures_account_contract"]
    if config["model_version"] != OIL_FUTURES_ACCOUNT_MODEL_VERSION:
        raise ValueError("oil futures account config/model version mismatch")
    return assets, config, contract


def _contract_map(market: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item["contract_id"]): item
        for item in market.get("curve", {}).get("contracts", ())
    }


def _validated_positions(value: Mapping[str, Any] | None) -> dict[str, int]:
    raw = dict(value or {})
    if any(isinstance(item, bool) or not isinstance(item, int) for item in raw.values()):
        raise ValueError("oil futures account positions must be integer lots")
    return {str(key): int(item) for key, item in raw.items() if int(item) != 0}


def _margin_requirements(
    positions: Mapping[str, int], market: Mapping[str, Any]
) -> dict[str, Any]:
    contracts = _contract_map(market)
    specification = market["contractSpecification"]
    contract_size = float(specification["contract_size_bbl"])
    initial_rate = float(specification["initial_margin_rate_pct"]) / 100.0
    maintenance_rate = float(specification["maintenance_margin_rate_pct"]) / 100.0
    by_contract: dict[str, dict[str, float | int]] = {}
    initial_margin = 0.0
    maintenance_margin = 0.0
    notional = 0.0
    for contract_id, position in sorted(positions.items()):
        contract = contracts.get(contract_id)
        if contract is None:
            raise ValueError(f"open account position is unavailable: {contract_id}")
        price = float(contract["price_usd"])
        contract_notional = abs(position) * price * contract_size
        contract_initial = contract_notional * initial_rate
        contract_maintenance = contract_notional * maintenance_rate
        by_contract[contract_id] = {
            "position_lots": int(position),
            "mark_price_usd": price,
            "notional_usd": contract_notional,
            "initial_margin_usd": contract_initial,
            "maintenance_margin_usd": contract_maintenance,
        }
        notional += contract_notional
        initial_margin += contract_initial
        maintenance_margin += contract_maintenance
    return {
        "gross_notional_usd": notional,
        "initial_margin_usd": initial_margin,
        "maintenance_margin_usd": maintenance_margin,
        "by_contract": by_contract,
    }


def _state_payload_without_identity(state: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key != "identity"}


def _finalize_state(state: Mapping[str, Any]) -> dict[str, Any]:
    assets, config, contract = _assets()
    payload = _round_nested(_state_payload_without_identity(state))
    identity = {
        "schema_version": "asset-simulation-oil-futures-account-state-identity-v1",
        "model_version": OIL_FUTURES_ACCOUNT_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_futures_account_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_futures_account_contract_hash"],
        "state_hash": sha256_json(payload),
    }
    return {**payload, "identity": identity}


def create_oil_futures_account(
    *, account_id: str, initial_cash_usd: float
) -> dict[str, Any]:
    initial_cash = float(initial_cash_usd)
    if not math.isfinite(initial_cash) or initial_cash <= 0.0:
        raise ValueError("oil futures account initial cash must be positive and finite")
    return _finalize_state(
        {
            "schemaVersion": "asset-simulation-oil-futures-account-state-v1",
            "account_id": str(account_id),
            "initial_equity_usd": initial_cash,
            "cash_balance_usd": initial_cash,
            "equity_usd": initial_cash,
            "positions": {},
            "status": "normal",
            "restriction_turns_remaining": 0,
            "ever_insolvent": False,
            "completed_turns": 0,
            "cumulative_trading_pnl_usd": 0.0,
            "cumulative_idle_cash_interest_usd": 0.0,
            "cumulative_margin_financing_cost_usd": 0.0,
            "cumulative_forced_liquidation_cost_usd": 0.0,
            "cumulative_account_net_pnl_usd": 0.0,
            "margin_call_count": 0,
            "forced_liquidation_count": 0,
            "cumulative_forced_liquidation_lots": 0,
            "insolvency_count": 0,
            "last_ledger_hash": None,
        }
    )


def oil_futures_account_snapshot(
    state: Mapping[str, Any], market: Mapping[str, Any]
) -> dict[str, Any]:
    positions = _validated_positions(state.get("positions"))
    cash = float(state["cash_balance_usd"])
    requirements = _margin_requirements(positions, market)
    initial_margin = float(requirements["initial_margin_usd"])
    maintenance_margin = float(requirements["maintenance_margin_usd"])
    free_collateral = cash - initial_margin
    equity = cash
    result = {
        "account_id": str(state["account_id"]),
        "status": str(state["status"]),
        "restriction_turns_remaining": int(state["restriction_turns_remaining"]),
        "cash_balance_usd": cash,
        "equity_usd": equity,
        "restricted_initial_margin_usd": initial_margin,
        "maintenance_margin_usd": maintenance_margin,
        "free_collateral_usd": free_collateral,
        "available_funds_usd": max(0.0, free_collateral),
        "initial_margin_deficit_usd": max(0.0, -free_collateral),
        "excess_liquidity_usd": cash - maintenance_margin,
        "margin_to_equity_pct": (
            None if equity <= 0.0 else 100.0 * initial_margin / equity
        ),
        "gross_notional_usd": float(requirements["gross_notional_usd"]),
        "positions": positions,
        "margin_by_contract": requirements["by_contract"],
    }
    return _round_nested(result)


def _margin_per_lot(market: Mapping[str, Any], contract_id: str) -> float:
    contract = _contract_map(market).get(contract_id)
    if contract is None:
        raise ValueError(f"account target contract is unavailable: {contract_id}")
    specification = market["contractSpecification"]
    return (
        float(contract["price_usd"])
        * float(specification["contract_size_bbl"])
        * float(specification["initial_margin_rate_pct"])
        / 100.0
    )


def _fit_positions_to_initial_margin(
    positions: Mapping[str, int], market: Mapping[str, Any], margin_cap_usd: float
) -> dict[str, int]:
    current = _validated_positions(positions)
    cap = max(0.0, float(margin_cap_usd))
    requirements = _margin_requirements(current, market)
    total = float(requirements["initial_margin_usd"])
    if total <= cap + 1e-6:
        return current
    if cap <= 0.0:
        return {}
    scale = clamp(cap / max(total, 1e-12), 0.0, 1.0)
    fitted = {
        key: int(math.copysign(math.floor(abs(value) * scale), value))
        for key, value in current.items()
    }
    fitted = {key: value for key, value in fitted.items() if value}
    while float(_margin_requirements(fitted, market)["initial_margin_usd"]) > cap + 1e-6:
        candidates = sorted(
            fitted,
            key=lambda key: (_margin_per_lot(market, key), abs(fitted[key]), key),
            reverse=True,
        )
        if not candidates:
            break
        key = candidates[0]
        fitted[key] -= int(math.copysign(1, fitted[key]))
        if fitted[key] == 0:
            del fitted[key]
    return fitted


def apply_oil_futures_account_constraints(
    state: Mapping[str, Any],
    market: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply cash/margin authority after committee and risk approval."""

    assets, config, _ = _assets()
    current_positions = _validated_positions(state.get("positions"))
    equity = float(state["equity_usd"])
    target_rows = [deepcopy(item) for item in decision.get("targets", ())]
    target_by_id = {str(item["contract_id"]): item for item in target_rows}
    desired = {
        contract_id: int(item["target_position_lots"])
        for contract_id, item in target_by_id.items()
    }
    locked = bool(state.get("ever_insolvent")) or int(
        state.get("restriction_turns_remaining", 0)
    ) > 0 or str(state.get("status")) in {
        "reduce_only",
        "forced_liquidation",
        "insolvent",
    }
    for contract_id in set(current_positions) | set(desired):
        current = int(current_positions.get(contract_id, 0))
        requested = int(desired.get(contract_id, 0))
        if locked:
            if current == 0 or current * requested <= 0:
                requested = 0
            else:
                requested = int(
                    math.copysign(min(abs(current), abs(requested)), current)
                )
        desired[contract_id] = requested

    base: dict[str, int] = {}
    for contract_id in set(current_positions) | set(desired):
        current = int(current_positions.get(contract_id, 0))
        requested = int(desired.get(contract_id, 0))
        if current != 0 and current * requested > 0:
            value = int(math.copysign(min(abs(current), abs(requested)), current))
        else:
            value = 0
        if value:
            base[contract_id] = value

    pretrade = config["pretrade"]
    maximum_margin_pct = min(
        float(pretrade["maximum_initial_margin_pct_of_equity"]),
        100.0 - float(pretrade["minimum_free_collateral_pct_of_equity"]),
    )
    margin_cap = max(0.0, equity * maximum_margin_pct / 100.0)
    approved = _fit_positions_to_initial_margin(base, market, margin_cap)
    remaining_margin = max(
        0.0,
        margin_cap
        - float(_margin_requirements(approved, market)["initial_margin_usd"]),
    )
    ordered_ids = sorted(
        desired,
        key=lambda key: (
            _ROLE_ORDER.index(str(target_by_id.get(key, {}).get("role")))
            if str(target_by_id.get(key, {}).get("role")) in _ROLE_ORDER
            else len(_ROLE_ORDER),
            key,
        ),
    )
    if not locked:
        for contract_id in ordered_ids:
            requested = int(desired[contract_id])
            base_value = int(approved.get(contract_id, 0))
            increase = requested - base_value
            if increase == 0:
                continue
            if base_value != 0 and base_value * requested <= 0:
                continue
            if base_value != 0 and abs(requested) <= abs(base_value):
                continue
            margin_per_lot = _margin_per_lot(market, contract_id)
            lots = min(abs(increase), math.floor(remaining_margin / margin_per_lot))
            if lots <= 0:
                continue
            value = base_value + int(math.copysign(lots, increase))
            if value:
                approved[contract_id] = value
            else:
                approved.pop(contract_id, None)
            remaining_margin -= lots * margin_per_lot

    constrained = deepcopy(dict(decision))
    clipped_lots = 0
    binding_contracts: list[str] = []
    new_rows = []
    for row in target_rows:
        contract_id = str(row["contract_id"])
        requested = int(row["target_position_lots"])
        value = int(approved.get(contract_id, 0))
        clip = abs(requested - value)
        clipped_lots += clip
        if clip:
            binding_contracts.append(contract_id)
        row["pre_account_target_position_lots"] = requested
        row["account_approved_target_position_lots"] = value
        row["account_clip_lots"] = clip
        row["account_binding_rule"] = (
            "account_reduce_only" if locked and clip else
            "initial_margin_and_free_collateral" if clip else
            "not_binding"
        )
        row["target_position_lots"] = value
        if locked and bool(pretrade["reduce_only_disables_round_trip_turnover"]):
            row["gross_turnover_budget_lots"] = abs(
                value - int(current_positions.get(contract_id, 0))
            )
            row["weekly_turnover_setups"] = []
        new_rows.append(row)
    constrained["targets"] = new_rows
    approved_requirements = _margin_requirements(approved, market)
    authorization = {
        "schemaVersion": "asset-simulation-oil-futures-account-authorization-v1",
        "account_id": str(state["account_id"]),
        "account_state_hash": str(state["identity"]["state_hash"]),
        "status_before": str(state["status"]),
        "reduce_only": locked,
        "equity_usd": equity,
        "maximum_initial_margin_pct_of_equity": maximum_margin_pct,
        "maximum_initial_margin_usd": margin_cap,
        "approved_initial_margin_usd": float(
            approved_requirements["initial_margin_usd"]
        ),
        "approved_available_funds_usd": max(
            0.0, equity - float(approved_requirements["initial_margin_usd"])
        ),
        "requested_positions": desired,
        "approved_positions": approved,
        "clipped_target_lots": clipped_lots,
        "binding_contracts": sorted(set(binding_contracts)),
        "account_can_expand_prior_approval": False,
    }
    authorization["authorization_hash"] = sha256_json(authorization)
    constrained["accountAuthorization"] = authorization
    prior_identity = dict(constrained.get("identity", {}))
    prior_hash = prior_identity.get("result_hash")
    prior_identity.update(
        {
            "schema_version": "asset-simulation-oil-strategy-decision-identity-v9",
            "pre_account_decision_hash": prior_hash,
            "account_config_hash": assets["oil_futures_account_config_hash"],
            "account_state_hash": state["identity"]["state_hash"],
            "account_authorization_hash": authorization["authorization_hash"],
        }
    )
    identity_payload = {
        **{key: value for key, value in constrained.items() if key != "identity"},
        "identity": {key: value for key, value in prior_identity.items() if key != "result_hash"},
    }
    prior_identity["result_hash"] = sha256_json(identity_payload)
    constrained["identity"] = prior_identity
    return _round_nested({"decision": constrained, "authorization": authorization})


def _annual_reference_rate_pct(market: Mapping[str, Any]) -> float:
    try:
        return float(market["curve"]["inputs"]["funding_pct"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("oil futures account funding reference is unavailable") from exc


def _liquidation_cost(
    before: Mapping[str, int],
    after: Mapping[str, int],
    market: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[int, float, dict[str, Any]]:
    contracts = _contract_map(market)
    specification = market["contractSpecification"]
    contract_size = float(specification["contract_size_bbl"])
    policy = config["margin_call_and_liquidation"]
    slippage_bps = float(policy["forced_liquidation_slippage_bps_per_side"])
    fee_per_lot = float(policy["forced_liquidation_fee_usd_per_lot"])
    total_lots = 0
    total_cost = 0.0
    rows: dict[str, Any] = {}
    for contract_id in sorted(set(before) | set(after)):
        lots = abs(int(before.get(contract_id, 0)) - int(after.get(contract_id, 0)))
        if not lots:
            continue
        price = float(contracts[contract_id]["price_usd"])
        slippage = lots * price * contract_size * slippage_bps / 10_000.0
        fees = lots * fee_per_lot
        rows[contract_id] = {
            "position_before_lots": int(before.get(contract_id, 0)),
            "position_after_lots": int(after.get(contract_id, 0)),
            "liquidated_lots": lots,
            "mark_price_usd": price,
            "slippage_cost_usd": slippage,
            "fee_usd": fees,
            "total_cost_usd": slippage + fees,
        }
        total_lots += lots
        total_cost += slippage + fees
    return total_lots, total_cost, rows


def settle_oil_futures_account_turn(
    state: Mapping[str, Any],
    start_market: Mapping[str, Any],
    end_market: Mapping[str, Any],
    strategy_settlement: Mapping[str, Any],
) -> dict[str, Any]:
    """Post one strategy settlement and enforce the formal account waterfall."""

    assets, config, contract = _assets()
    start_positions = _validated_positions(state.get("positions"))
    reported_starts = {
        str(item["contract_id"]): int(item["starting_position_lots"])
        for item in strategy_settlement.get("contracts", ())
        if int(item["starting_position_lots"]) != 0
    }
    if reported_starts != start_positions:
        raise ValueError("strategy settlement starting positions do not match account")
    start_snapshot = oil_futures_account_snapshot(state, start_market)
    strategy_after = strategy_settlement["accountAfter"]
    positions_before_liquidation = _validated_positions(strategy_after["positions"])
    variation_margin = float(strategy_after["turn_pnl_usd"])
    execution_cost = float(strategy_after["execution_cost_usd"])
    cash_before = float(state["cash_balance_usd"])
    cash_after_variation = cash_before + variation_margin
    provisional_state = {
        **dict(state),
        "cash_balance_usd": cash_after_variation,
        "equity_usd": cash_after_variation,
        "positions": positions_before_liquidation,
    }
    provisional_snapshot = oil_futures_account_snapshot(provisional_state, end_market)

    cash_policy = config["cash_and_funding"]
    turns_per_year = float(config["turns_per_year"])
    reference_rate = _annual_reference_rate_pct(end_market)
    idle_rate = clamp(
        reference_rate + float(cash_policy["idle_cash_rate_spread_bps"]) / 100.0,
        float(cash_policy["idle_cash_rate_floor_pct"]),
        float(cash_policy["idle_cash_rate_cap_pct"]),
    )
    financing_rate = clamp(
        reference_rate + float(cash_policy["margin_financing_spread_bps"]) / 100.0,
        float(cash_policy["margin_financing_rate_floor_pct"]),
        float(cash_policy["margin_financing_rate_cap_pct"]),
    )
    average_idle_cash = 0.5 * (
        float(start_snapshot["available_funds_usd"])
        + float(provisional_snapshot["available_funds_usd"])
    )
    idle_cash_interest = average_idle_cash * idle_rate / 100.0 / turns_per_year
    self_funded_pct = float(
        cash_policy["self_funded_initial_margin_pct_of_equity"]
    ) / 100.0
    start_financed = max(
        0.0,
        float(start_snapshot["restricted_initial_margin_usd"])
        - max(0.0, float(start_snapshot["equity_usd"])) * self_funded_pct,
    )
    end_financed = max(
        0.0,
        float(provisional_snapshot["restricted_initial_margin_usd"])
        - max(0.0, float(provisional_snapshot["equity_usd"])) * self_funded_pct,
    )
    average_financed_margin = 0.5 * (start_financed + end_financed)
    financing_cost = average_financed_margin * financing_rate / 100.0 / turns_per_year
    cash_pre_liquidation = cash_after_variation + idle_cash_interest - financing_cost

    positions_after = dict(positions_before_liquidation)
    pre_liquidation_requirements = _margin_requirements(positions_after, end_market)
    maintenance_margin_pre = float(pre_liquidation_requirements["maintenance_margin_usd"])
    initial_margin_pre = float(pre_liquidation_requirements["initial_margin_usd"])
    margin_call_triggered = cash_pre_liquidation < maintenance_margin_pre - 0.01
    margin_call_amount = (
        max(0.0, initial_margin_pre - cash_pre_liquidation)
        if margin_call_triggered
        else 0.0
    )
    forced_lots = 0
    liquidation_cost = 0.0
    liquidation_rows: dict[str, Any] = {}
    liquidation_passes = 0
    liquidation_policy = config["margin_call_and_liquidation"]
    if margin_call_triggered or bool(state.get("ever_insolvent")):
        target_utilization = float(
            liquidation_policy[
                "forced_liquidation_target_initial_margin_pct_of_equity"
            ]
        ) / 100.0
        for liquidation_passes in range(
            1, int(liquidation_policy["maximum_liquidation_passes"]) + 1
        ):
            cash_after_cost = cash_pre_liquidation - liquidation_cost
            target_margin = max(0.0, cash_after_cost * target_utilization)
            fitted = _fit_positions_to_initial_margin(
                positions_before_liquidation, end_market, target_margin
            )
            next_lots, next_cost, next_rows = _liquidation_cost(
                positions_before_liquidation, fitted, end_market, config
            )
            positions_after = fitted
            forced_lots = next_lots
            liquidation_rows = next_rows
            if math.isclose(next_cost, liquidation_cost, abs_tol=0.01):
                liquidation_cost = next_cost
                break
            liquidation_cost = next_cost
        if bool(state.get("ever_insolvent")):
            positions_after = {}
            forced_lots, liquidation_cost, liquidation_rows = _liquidation_cost(
                positions_before_liquidation, positions_after, end_market, config
            )

    cash_after = cash_pre_liquidation - liquidation_cost
    final_requirements = _margin_requirements(positions_after, end_market)
    final_initial_margin = float(final_requirements["initial_margin_usd"])
    final_maintenance_margin = float(final_requirements["maintenance_margin_usd"])
    insolvency_floor = float(liquidation_policy["insolvency_equity_floor_usd"])
    insolvent = bool(state.get("ever_insolvent")) or cash_after <= insolvency_floor
    if insolvent and positions_after:
        extra_lots, extra_cost, extra_rows = _liquidation_cost(
            positions_after, {}, end_market, config
        )
        forced_lots += extra_lots
        liquidation_cost += extra_cost
        cash_after -= extra_cost
        liquidation_rows.update(extra_rows)
        positions_after = {}
        final_requirements = _margin_requirements({}, end_market)
        final_initial_margin = 0.0
        final_maintenance_margin = 0.0

    prior_restriction = int(state.get("restriction_turns_remaining", 0))
    if margin_call_triggered:
        restriction_remaining = int(
            config["pretrade"]["account_restriction_turns_after_margin_call"]
        )
    else:
        restriction_remaining = max(0, prior_restriction - 1)
    if insolvent:
        status = "insolvent"
    elif forced_lots > 0:
        status = "forced_liquidation"
    elif restriction_remaining > 0:
        status = "reduce_only"
    else:
        status = "normal"
    account_net_pnl = variation_margin + idle_cash_interest - financing_cost - liquidation_cost
    expected_cash_after = cash_before + account_net_pnl
    tolerance = float(config["accounting"]["rounding_tolerance_usd"])
    if not math.isclose(cash_after, expected_cash_after, abs_tol=tolerance):
        raise ValueError("oil futures account cash ledger does not reconcile")
    if not math.isclose(cash_after, cash_before + account_net_pnl, abs_tol=tolerance):
        raise ValueError("oil futures account equity bridge does not reconcile")
    if not insolvent and cash_after < final_maintenance_margin - tolerance:
        raise ValueError("oil futures account liquidation failed to restore maintenance")

    ledger = {
        "schemaVersion": "asset-simulation-oil-futures-account-turn-ledger-v1",
        "account_id": str(state["account_id"]),
        "from_as_of": dict(start_market["asOf"]),
        "to_as_of": dict(end_market["asOf"]),
        "cash_before_usd": cash_before,
        "variation_margin_usd": variation_margin,
        "gross_trading_pnl_before_cost_usd": float(
            strategy_after["gross_pnl_before_cost_usd"]
        ),
        "execution_cost_usd": execution_cost,
        "idle_cash_basis_usd": average_idle_cash,
        "idle_cash_annual_rate_pct": idle_rate,
        "idle_cash_interest_usd": idle_cash_interest,
        "financed_margin_basis_usd": average_financed_margin,
        "margin_financing_annual_rate_pct": financing_rate,
        "margin_financing_cost_usd": financing_cost,
        "cash_pre_liquidation_usd": cash_pre_liquidation,
        "initial_margin_before_liquidation_usd": initial_margin_pre,
        "maintenance_margin_before_liquidation_usd": maintenance_margin_pre,
        "margin_call_triggered": margin_call_triggered,
        "margin_call_amount_usd": margin_call_amount,
        "forced_liquidation_lots": forced_lots,
        "forced_liquidation_cost_usd": liquidation_cost,
        "forced_liquidation_passes": liquidation_passes,
        "forced_liquidations": liquidation_rows,
        "initial_margin_after_usd": final_initial_margin,
        "maintenance_margin_after_usd": final_maintenance_margin,
        "initial_margin_transfer_usd": final_initial_margin
        - float(start_snapshot["restricted_initial_margin_usd"]),
        "account_net_pnl_usd": account_net_pnl,
        "external_cash_flow_usd": 0.0,
        "cash_after_usd": cash_after,
        "cash_identity_error_usd": cash_after - expected_cash_after,
        "status_after": status,
    }
    ledger["ledger_hash"] = sha256_json(ledger)
    new_state = _finalize_state(
        {
            **_state_payload_without_identity(state),
            "cash_balance_usd": cash_after,
            "equity_usd": cash_after,
            "positions": positions_after,
            "status": status,
            "restriction_turns_remaining": restriction_remaining,
            "ever_insolvent": insolvent,
            "completed_turns": int(state["completed_turns"]) + 1,
            "cumulative_trading_pnl_usd": float(
                state["cumulative_trading_pnl_usd"]
            ) + variation_margin,
            "cumulative_idle_cash_interest_usd": float(
                state["cumulative_idle_cash_interest_usd"]
            ) + idle_cash_interest,
            "cumulative_margin_financing_cost_usd": float(
                state["cumulative_margin_financing_cost_usd"]
            ) + financing_cost,
            "cumulative_forced_liquidation_cost_usd": float(
                state["cumulative_forced_liquidation_cost_usd"]
            ) + liquidation_cost,
            "cumulative_account_net_pnl_usd": float(
                state["cumulative_account_net_pnl_usd"]
            ) + account_net_pnl,
            "margin_call_count": int(state["margin_call_count"])
            + int(margin_call_triggered),
            "forced_liquidation_count": int(state["forced_liquidation_count"])
            + int(forced_lots > 0),
            "cumulative_forced_liquidation_lots": int(
                state["cumulative_forced_liquidation_lots"]
            ) + forced_lots,
            "insolvency_count": int(state["insolvency_count"])
            + int(insolvent and not bool(state.get("ever_insolvent"))),
            "last_ledger_hash": ledger["ledger_hash"],
        }
    )
    ending_snapshot = oil_futures_account_snapshot(new_state, end_market)
    result = {
        "schemaVersion": "asset-simulation-oil-futures-account-settlement-v1",
        "accountBefore": start_snapshot,
        "strategyStatement": {
            "decision_id": strategy_settlement["decision_id"],
            "variation_margin_usd": variation_margin,
            "execution_cost_usd": execution_cost,
            "positions_after_strategy_execution": positions_before_liquidation,
        },
        "ledger": ledger,
        "accountAfter": ending_snapshot,
        "state": new_state,
        "invariants": {
            "cash_reconciled": True,
            "posted_margin_is_non_pnl": True,
            "maintenance_restored_or_insolvent": insolvent
            or cash_after >= final_maintenance_margin - tolerance,
            "external_capital_flow_usd": 0.0,
        },
    }
    identity = {
        "schema_version": "asset-simulation-oil-futures-account-settlement-identity-v1",
        "model_version": OIL_FUTURES_ACCOUNT_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_futures_account_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_futures_account_contract_hash"],
        "prior_account_state_hash": state["identity"]["state_hash"],
        "strategy_statement_hash": sha256_json(strategy_settlement),
        "result_hash": sha256_json(result),
    }
    return _round_nested({"identity": identity, **result})
