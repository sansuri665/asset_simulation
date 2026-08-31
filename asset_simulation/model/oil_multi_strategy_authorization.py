"""Amount-authoritative Investment Decision state for Gate B."""

from __future__ import annotations

from typing import Any, Mapping

from .oil_multi_strategy_gate_b_common import (
    OIL_MULTI_STRATEGY_GATE_B_MODEL_VERSION,
    _assets,
    _finite_nonnegative,
    _round_nested,
)
from .registry import sha256_json


def create_strategy_capital_authorization_state(
    *,
    decision_id: str,
    effective_turn: str,
    reference_company_equity_usd: float,
    strategy_authorizations_usd: Mapping[str, float],
    previous_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one immutable, amount-authoritative committee decision.

    A new decision cannot authorize more than the equity visible at its own
    decision cutoff. Later equity losses may create an authorization overhang;
    that status is reported without silently rescaling any strategy amount.
    """

    assets, config, contract = _assets()
    decision = str(decision_id).strip()
    turn = str(effective_turn).strip()
    if not decision or not turn:
        raise ValueError("authorization decision_id and effective_turn are required")
    reference_equity = _finite_nonnegative(
        reference_company_equity_usd, "reference company equity"
    )
    if reference_equity <= 0.0:
        raise ValueError("reference company equity must be positive")
    raw = dict(strategy_authorizations_usd)
    if not raw:
        raise ValueError("at least one strategy authorization is required")
    entries: dict[str, dict[str, Any]] = {}
    for raw_strategy_id, raw_amount in sorted(raw.items(), key=lambda item: str(item[0])):
        strategy_id = str(raw_strategy_id).strip()
        if not strategy_id or strategy_id in entries:
            raise ValueError("strategy authorization ids must be unique and nonempty")
        amount = _finite_nonnegative(raw_amount, f"{strategy_id} authorization")
        entries[strategy_id] = {
            "strategy_id": strategy_id,
            "authorized_capital_usd": amount,
            "reference_equity_share_pct": 100.0 * amount / reference_equity,
            "committee_status": "authorized" if amount > 0.0 else "disabled",
        }
    total = sum(float(item["authorized_capital_usd"]) for item in entries.values())
    if (
        bool(
            config["authorization"][
                "new_decision_total_authorization_must_not_exceed_reference_equity"
            ]
        )
        and total > reference_equity + 1e-6
    ):
        raise ValueError("new strategy authorizations exceed reference company equity")

    previous_hash = None
    previous_epoch = 0
    if previous_state is not None:
        previous_identity = dict(previous_state.get("identity", {}))
        previous_hash = str(previous_identity.get("state_hash", "")).strip() or None
        previous_epoch = int(previous_state.get("authorization_epoch", 0))
        if previous_hash is None:
            raise ValueError("previous authorization state lacks a state hash")
    payload = _round_nested(
        {
            "schemaVersion": "asset-simulation-strategy-capital-authorization-state-v1",
            "decision_id": decision,
            "effective_turn": turn,
            "authorization_epoch": previous_epoch + 1,
            "reference_company_equity_usd": reference_equity,
            "authorizations": entries,
            "total_authorized_capital_usd": total,
            "unallocated_reference_capital_usd": reference_equity - total,
            "previous_state_hash": previous_hash,
            "governance": {
                "authority_unit": "usd",
                "amount_is_authoritative": True,
                "percentages_are_diagnostic_only": True,
                "automatic_percentage_rebalancing_enabled": False,
                "company_leverage_authorization_enabled": False,
                "authorization_change_mutates_positions": False,
                "authorization_change_requires_market_execution_to_change_positions": True,
            },
        }
    )
    identity = {
        "schema_version": "asset-simulation-strategy-capital-authorization-identity-v1",
        "model_version": OIL_MULTI_STRATEGY_GATE_B_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_multi_strategy_gate_b_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_multi_strategy_gate_b_contract_hash"],
        "state_hash": sha256_json(payload),
    }
    return {**payload, "identity": identity}


def amend_strategy_capital_authorization_state(
    state: Mapping[str, Any],
    *,
    decision_id: str,
    effective_turn: str,
    reference_company_equity_usd: float,
    strategy_authorizations_usd: Mapping[str, float],
) -> dict[str, Any]:
    """Replace the amount vector through a new explicit committee decision."""

    return create_strategy_capital_authorization_state(
        decision_id=decision_id,
        effective_turn=effective_turn,
        reference_company_equity_usd=reference_company_equity_usd,
        strategy_authorizations_usd=strategy_authorizations_usd,
        previous_state=state,
    )


def evaluate_strategy_capital_authorization_status(
    state: Mapping[str, Any], *, current_company_equity_usd: float
) -> dict[str, Any]:
    """Report current fundability without changing authorized dollar amounts."""

    current_equity = _finite_nonnegative(
        current_company_equity_usd, "current company equity"
    )
    entries = dict(state.get("authorizations", {}))
    if not entries:
        raise ValueError("authorization state contains no strategy entries")
    amounts = {
        str(strategy_id): float(item["authorized_capital_usd"])
        for strategy_id, item in sorted(entries.items())
    }
    total = sum(amounts.values())
    overhang = max(0.0, total - current_equity)
    unallocated = max(0.0, current_equity - total)
    if total <= 0.0:
        status = "no_strategy_authorized"
    elif overhang > 1e-6:
        status = "authorization_overhang"
    else:
        status = "fully_fundable"
    result = {
        "schemaVersion": "asset-simulation-strategy-capital-authorization-status-v1",
        "authorization_state_hash": str(state["identity"]["state_hash"]),
        "current_company_equity_usd": current_equity,
        "total_authorized_capital_usd": total,
        "unallocated_company_capital_usd": unallocated,
        "authorization_overhang_usd": overhang,
        "status": status,
        "authorizations": {
            strategy_id: {
                "authorized_capital_usd": amount,
                "share_of_current_equity_pct": (
                    None if current_equity <= 0.0 else 100.0 * amount / current_equity
                ),
                "risk_increase_authorized_by_committee": amount > 0.0,
                "effective_authorized_capital_usd": amount,
            }
            for strategy_id, amount in amounts.items()
        },
        "governance": {
            "automatic_rescale_applied": False,
            "percentage_target_preserved": False,
            "amounts_changed_since_committee_decision": False,
            "formal_account_constraints_remain_final": True,
        },
    }
    rounded = _round_nested(result)
    return {**rounded, "status_hash": sha256_json(rounded)}
