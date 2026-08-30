"""Validate the institution shell and expose its authoritative capital base."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .registry import load_registered_assets


INSTITUTION_ORGANIZATION_MODEL_VERSION = "asset-simulation-institution-organization-v0.1.0"
INSTITUTION_ORGANIZATION_CONTRACT_ID = "institution_organization_v1"
EXPECTED_DEPARTMENT_IDS = (
    "forecast_research",
    "investment_strategy",
    "corporate_risk",
    "trading_execution",
    "administration",
)
EXPECTED_INVESTMENT_DECISION_SCOPE = {
    "strategy_charter_approval",
    "strategy_capital_authorization",
    "company_risk_appetite_approval",
    "strategy_position_mandate",
}


def resolve_institution_organization(
    assets: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return validated organization config and contract assets."""

    registered = dict(load_registered_assets() if assets is None else assets)
    config = dict(registered["institution_organization_config"])
    contract = dict(registered["institution_organization_contract"])
    if config.get("model_version") != INSTITUTION_ORGANIZATION_MODEL_VERSION:
        raise ValueError("institution organization model version mismatch")
    if contract.get("contract_id") != INSTITUTION_ORGANIZATION_CONTRACT_ID:
        raise ValueError("institution organization contract id mismatch")
    if config.get("institution_type") != "proprietary_trading_firm":
        raise ValueError("institution organization type is unsupported")

    capital = dict(config.get("capital_base", {}))
    initial_capital = float(capital.get("initial_proprietary_capital_usd", 0.0))
    if not math.isfinite(initial_capital) or initial_capital <= 0.0:
        raise ValueError("institution proprietary capital must be positive and finite")
    if capital.get("runtime_capital_owner") != INSTITUTION_ORGANIZATION_CONTRACT_ID:
        raise ValueError("institution capital owner mismatch")
    for disabled_field in (
        "external_aum_enabled",
        "fund_management_company_split_enabled",
        "management_fee_model_enabled",
        "operating_company_cash_model_enabled",
    ):
        if bool(capital.get(disabled_field)):
            raise ValueError(f"institution organization shell unexpectedly enables {disabled_field}")

    decision = dict(dict(config.get("governance_layers", {})).get("investment_decision", {}))
    if decision.get("layer_type") != "governance_not_department":
        raise ValueError("institution investment decision must be a governance layer")
    if float(decision.get("single_strategy_default_capital_authorization_pct", -1.0)) != 100.0:
        raise ValueError("institution single-strategy capital proxy is invalid")
    if set(decision.get("current_scope", ())) != EXPECTED_INVESTMENT_DECISION_SCOPE:
        raise ValueError("institution investment decision scope is invalid")
    for enabled_field in (
        "strategy_charter_enabled",
        "company_risk_appetite_enabled",
        "position_mandate_enabled",
    ):
        if not bool(decision.get(enabled_field)):
            raise ValueError(f"institution investment decision must enable {enabled_field}")
    if any(
        bool(decision.get(field))
        for field in (
            "member_roster_enabled",
            "voting_enabled",
            "personnel_capability_model_enabled",
            "player_interaction_enabled",
            "multi_strategy_allocation_enabled",
        )
    ):
        raise ValueError("institution investment decision candidate enables unsupported governance mechanics")

    departments = list(config.get("departments", ()))
    if tuple(str(item.get("department_id")) for item in departments) != EXPECTED_DEPARTMENT_IDS:
        raise ValueError("institution department order or membership is invalid")
    administration = dict(departments[-1])
    if administration.get("status") != "shell_only" or any(
        bool(administration.get(field))
        for field in (
            "runtime_logic_enabled",
            "personnel_model_enabled",
            "cost_model_enabled",
            "payroll_enabled",
            "recruiting_system_enabled",
        )
    ):
        raise ValueError("institution administration shell is invalid")
    if tuple(contract.get("department_ids", ())) != EXPECTED_DEPARTMENT_IDS:
        raise ValueError("institution organization contract departments mismatch")
    contract_scope = set(
        contract.get("governance_fields", {})
        .get("investment_decision", {})
        .get("current_responsibilities", ())
    )
    if contract_scope != EXPECTED_INVESTMENT_DECISION_SCOPE:
        raise ValueError("institution organization contract governance scope mismatch")
    return config, contract


def initial_proprietary_capital_usd(
    assets: Mapping[str, Any] | None = None,
) -> float:
    config, _ = resolve_institution_organization(assets)
    return float(config["capital_base"]["initial_proprietary_capital_usd"])


def validate_strategy_capital_reference(
    strategy_config: Mapping[str, Any],
    *,
    assets: Mapping[str, Any] | None = None,
) -> float:
    """Keep the legacy strategy field compatible with the organization owner."""

    if strategy_config.get("institution_organization_owner") != INSTITUTION_ORGANIZATION_CONTRACT_ID:
        raise ValueError("oil trading strategy organization owner mismatch")
    authoritative = initial_proprietary_capital_usd(assets)
    compatibility_value = float(strategy_config.get("initial_reference_equity_usd", 0.0))
    if not math.isclose(authoritative, compatibility_value):
        raise ValueError("oil trading strategy capital reference differs from organization owner")
    return authoritative
