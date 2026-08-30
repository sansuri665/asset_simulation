"""Explicit Investment Decision governance objects.

The committee owns strategy admission, capital mandates, company risk appetite,
and the conversion of PM proposals into company position mandates.  It never
creates alpha: a position mandate may only preserve or reduce PM intent.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .registry import load_registered_assets, sha256_json


INVESTMENT_DECISION_MODEL_VERSION = "asset-simulation-investment-decision-v0.1.0"
INVESTMENT_DECISION_CONTRACT_ID = "investment_decision_v1"


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("investment decision contains a non-finite value")
        return round(value, 8)
    return value


def _assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["investment_decision_config"]
    contract = assets["investment_decision_contract"]
    if config["model_version"] != INVESTMENT_DECISION_MODEL_VERSION:
        raise ValueError("registered investment decision config version mismatch")
    if contract["contract_id"] != INVESTMENT_DECISION_CONTRACT_ID:
        raise ValueError("registered investment decision contract id mismatch")
    return assets, config, contract


def _score(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 100.0:
        raise ValueError(f"{label} must be between 0 and 100")
    return result


def _identity(result: Mapping[str, Any], *, object_type: str, config: Mapping[str, Any], assets: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model_version": INVESTMENT_DECISION_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["investment_decision_config_hash"],
        "field_contract_id": INVESTMENT_DECISION_CONTRACT_ID,
        "field_contract_hash": assets["investment_decision_contract_hash"],
        "object_type": object_type,
        "write_back": False,
        "result_hash": sha256_json(result),
    }


def build_strategy_charter(
    *,
    asset: str,
    horizon: str,
    strategy_type: str,
    strategy_id: str,
    approved: bool = True,
) -> dict[str, Any]:
    """Approve or reject one strategy scope without generating an investment view."""

    assets, config, _ = _assets()
    if not isinstance(approved, bool):
        raise ValueError("strategy charter approved must be boolean")
    scope = {
        "asset": str(asset).strip(),
        "horizon": str(horizon).strip(),
        "strategy_type": str(strategy_type).strip(),
    }
    supported = {
        (str(item["asset"]), str(item["horizon"]), str(item["strategy_type"]))
        for item in config["supported_strategy_scopes"]
    }
    if tuple(scope[key] for key in ("asset", "horizon", "strategy_type")) not in supported:
        raise ValueError("investment decision strategy scope is unsupported")
    strategy_id = str(strategy_id).strip()
    if not strategy_id:
        raise ValueError("strategy charter requires a strategy id")
    result = {
        "schemaVersion": "asset-simulation-strategy-charter-v1",
        "strategy_id": strategy_id,
        "scope": scope,
        "status": "approved" if approved else "rejected",
        "governance": {
            "owner": config["governance_owner"],
            "creates_alpha": False,
        },
    }
    rounded = _round_nested(result)
    return {"identity": _identity(rounded, object_type="strategy_charter", config=config, assets=assets), **rounded}


def build_strategy_capital_mandate(
    charter: Mapping[str, Any],
    *,
    company_equity_usd: float,
    authorized_pct_of_company_equity: float | None = None,
) -> dict[str, Any]:
    """Allocate capital as an Investment Decision input to the PM."""

    assets, config, _ = _assets()
    equity = float(company_equity_usd)
    if not math.isfinite(equity) or equity <= 0.0:
        raise ValueError("company equity must be positive and finite")
    if charter.get("identity", {}).get("object_type") != "strategy_charter":
        raise ValueError("capital mandate requires an investment decision strategy charter")
    pct = (
        float(config["single_strategy_default_capital_authorization_pct"])
        if authorized_pct_of_company_equity is None
        else float(authorized_pct_of_company_equity)
    )
    if not math.isfinite(pct) or not 0.0 <= pct <= 100.0:
        raise ValueError("capital authorization must be between 0 and 100 percent")
    if charter.get("status") != "approved":
        pct = 0.0
    result = {
        "schemaVersion": "asset-simulation-strategy-capital-mandate-v1",
        "strategy_id": str(charter["strategy_id"]),
        "scope": dict(charter["scope"]),
        "company_equity_usd": equity,
        "authorized_pct_of_company_equity": pct,
        "authorized_capital_usd": equity * pct / 100.0,
        "governance": {
            "owner": config["governance_owner"],
            "risk_department_recommendation_used": False,
            "market_capacity_authorized_here": False,
        },
    }
    rounded = _round_nested(result)
    return {"identity": _identity(rounded, object_type="capital_mandate", config=config, assets=assets), **rounded}


def build_company_risk_appetite(
    risk_appetite_radar: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the committee-approved company policy, independent of CRO identity."""

    assets, config, _ = _assets()
    source = dict(config["default_company_risk_appetite"] if risk_appetite_radar is None else risk_appetite_radar)
    dimensions = tuple(str(item) for item in config["risk_appetite_dimensions"])
    if set(source) != set(dimensions):
        raise KeyError("company risk appetite must exactly match registered dimensions")
    radar = {key: _score(source[key], f"company risk appetite {key}") for key in dimensions}
    result = {
        "schemaVersion": "asset-simulation-company-risk-appetite-v1",
        "risk_appetite_radar": radar,
        "governance": {
            "owner": config["governance_owner"],
            "cro_identity_is_policy_input": False,
            "higher_score_is_better": False,
        },
    }
    rounded = _round_nested(result)
    identity = _identity(rounded, object_type="company_risk_appetite", config=config, assets=assets)
    return {"identity": identity, **rounded, "policy_hash": identity["result_hash"]}


def _target_map(targets: Mapping[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for contract_id, value in targets.items():
        raw = value.get("target_position_lots") if isinstance(value, Mapping) else value
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError("position mandate targets must be integer lots")
        result[str(contract_id)] = int(raw)
    return result


def build_strategy_position_mandate(
    charter: Mapping[str, Any],
    capital_mandate: Mapping[str, Any],
    pm_proposed_targets: Mapping[str, Any],
    *,
    expected_targets: Mapping[str, Any] | None = None,
    approved: bool = True,
) -> dict[str, Any]:
    """Convert PM intent into a company mandate without generating alpha."""

    assets, config, _ = _assets()
    if not isinstance(approved, bool):
        raise ValueError("position mandate approved must be boolean")
    if charter.get("status") != "approved":
        approved = False
    if str(capital_mandate.get("strategy_id")) != str(charter.get("strategy_id")):
        raise ValueError("position mandate charter and capital mandate strategy mismatch")
    proposed = _target_map(pm_proposed_targets)
    expected = dict(proposed) if expected_targets is None else _target_map(expected_targets)
    all_contracts = sorted(set(proposed) | set(expected))
    normalized_proposed = {key: int(proposed.get(key, 0)) for key in all_contracts}
    normalized_expected = {key: int(expected.get(key, 0)) for key in all_contracts}
    if not approved:
        normalized_expected = {key: 0 for key in all_contracts}
    for contract_id in all_contracts:
        pm = normalized_proposed[contract_id]
        committee = normalized_expected[contract_id]
        if pm == 0 and committee != 0:
            raise ValueError("investment committee cannot create position from zero PM intent")
        if pm != 0 and committee * pm < 0:
            raise ValueError("investment committee cannot reverse PM direction")
        if abs(committee) > abs(pm):
            raise ValueError("investment committee cannot expand absolute PM intent")
    result = {
        "schemaVersion": "asset-simulation-strategy-position-mandate-v1",
        "strategy_id": str(charter["strategy_id"]),
        "scope": dict(charter["scope"]),
        "status": "approved" if approved else "rejected",
        "authorized_strategy_capital_usd": float(capital_mandate["authorized_capital_usd"]),
        "pm_proposed_targets": normalized_proposed,
        "committee_expected_targets": normalized_expected,
        "governance": {
            "owner": config["governance_owner"],
            "creates_alpha": False,
            "preserve_or_reduce_only": True,
            "risk_review_required_after_mandate": True,
        },
    }
    rounded = _round_nested(result)
    return {"identity": _identity(rounded, object_type="position_mandate", config=config, assets=assets), **rounded}
