"""Oil / Short-Horizon strategy-risk review v0.2.

This kernel keeps the validated v0.1.1 personnel, measurement, horizon, market,
liquidity, concentration and roll mechanics while correcting the governance
boundary exposed by path-dependent economic replay.

Investment Decision owns strategy capital authorization.  This strategy-risk
layer therefore binds risk relative to allocated strategy capital and hard
market/execution constraints.  Stress and margin as percentages of total company
equity remain visible diagnostics, but they do not create a second per-strategy
company-capital ceiling.  Binding company-wide materiality belongs to the future
cross-strategy corporate aggregate-risk layer.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

from .math_utils import clamp
from . import oil_short_horizon_risk as base
from .registry import PACKAGE_ROOT, load_json, sha256_json


OIL_SHORT_HORIZON_RISK_MODEL_VERSION = "asset-simulation-oil-short-horizon-risk-v0.2.0"
OIL_SHORT_HORIZON_RISK_CONTRACT_ID = "oil_short_horizon_risk_v2"
_CONFIG_PATH = PACKAGE_ROOT / "config" / "oil_short_horizon_risk_v0.2.json"
_CONTRACT_PATH = PACKAGE_ROOT / "contracts" / "oil_short_horizon_risk_v2.json"

# Personnel identity and soft-estimation mechanics are deliberately inherited
# from the already-validated v0.1.1 model; only the review governance/calibration
# layer changes here.
build_default_oil_short_horizon_risk_profile = (
    base.build_default_oil_short_horizon_risk_profile
)
generate_oil_short_horizon_risk_candidate = base.generate_oil_short_horizon_risk_candidate
generate_oil_short_horizon_risk_roster = base.generate_oil_short_horizon_risk_roster
resolve_oil_short_horizon_risk_profile = base.resolve_oil_short_horizon_risk_profile


def _v2_assets() -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_json(_CONFIG_PATH)
    contract = load_json(_CONTRACT_PATH)
    if config["model_version"] != OIL_SHORT_HORIZON_RISK_MODEL_VERSION:
        raise ValueError("registered oil short-horizon risk v2 config mismatch")
    if contract["contract_id"] != OIL_SHORT_HORIZON_RISK_CONTRACT_ID:
        raise ValueError("registered oil short-horizon risk v2 contract mismatch")
    return config, contract


def _resolved_policy(
    company_risk_appetite: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, float]]:
    """Return binding v2 policy and non-binding company reference limits."""

    _, base_config, _ = base._assets()
    overlay, _ = _v2_assets()
    legacy_policy = base._company_policy(company_risk_appetite, profile, base_config)
    radar = company_risk_appetite["risk_appetite_radar"]
    score = float(radar["strategy_stress_loss_tolerance"])
    old_anchor = base_config["risk_appetite_parameter_mapping"][
        "max_strategy_stress_loss_pct_of_allocated_capital"
    ]
    new_anchor = overlay["strategy_stress_parameter_mapping"][
        "max_strategy_stress_loss_pct_of_allocated_capital"
    ]
    old_base = base._piecewise(old_anchor, score)
    effective_style_monitoring_multiplier = (
        1.0
        if old_base <= 1e-12
        else float(legacy_policy["max_strategy_stress_loss_pct_of_allocated_capital"])
        / old_base
    )
    binding = {
        "max_strategy_stress_loss_pct_of_allocated_capital": (
            base._piecewise(new_anchor, score) * effective_style_monitoring_multiplier
        ),
        "max_margin_pct_of_allocated_capital": float(
            legacy_policy["max_margin_pct_of_allocated_capital"]
        ),
        "max_single_contract_share_of_market_gross": float(
            legacy_policy["max_single_contract_share_of_market_gross"]
        ),
        "max_liquidation_half_turns": float(
            legacy_policy["max_liquidation_half_turns"]
        ),
        "roll_buffer_half_turns": float(legacy_policy["roll_buffer_half_turns"]),
        "tail_stress_multiplier": float(legacy_policy["tail_stress_multiplier"]),
        "model_uncertainty_multiplier": float(
            legacy_policy["model_uncertainty_multiplier"]
        ),
    }
    diagnostic_reference = {
        "legacy_reference_max_company_stress_loss_pct_of_equity_per_strategy": float(
            legacy_policy["max_company_stress_loss_pct_of_equity_per_strategy"]
        ),
        "legacy_reference_max_company_margin_pct_of_equity_per_strategy": float(
            legacy_policy["max_company_margin_pct_of_equity_per_strategy"]
        ),
    }
    return binding, diagnostic_reference


def build_oil_short_horizon_risk_review(
    market: Mapping[str, Any],
    committee_position_mandate: Mapping[str, Any],
    *,
    company_equity_usd: float,
    allocated_strategy_capital_usd: float,
    current_positions: Mapping[str, int] | None = None,
    company_risk_appetite: Mapping[str, Any],
    risk_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Review committee targets with strategy-relative binding limits only."""

    base_assets, base_config, _ = base._assets()
    overlay, contract = _v2_assets()
    if (
        committee_position_mandate.get("identity", {}).get("object_type")
        != "position_mandate"
    ):
        raise ValueError(
            "oil short-horizon risk requires an actual committee position mandate"
        )
    scope = dict(committee_position_mandate.get("scope", {}))
    if scope.get("asset") != "oil" or scope.get("horizon") != "short_horizon":
        raise ValueError("risk mandate is outside oil / short-horizon coverage")
    strategy_type = str(scope.get("strategy_type"))
    if strategy_type not in set(base_config["supported_strategy_types"]):
        raise ValueError("unsupported short-horizon oil strategy type")

    equity = float(company_equity_usd)
    allocated = float(allocated_strategy_capital_usd)
    if (
        not math.isfinite(equity)
        or equity <= 0.0
        or not math.isfinite(allocated)
        or allocated <= 0.0
    ):
        raise ValueError("company equity and allocated strategy capital must be positive")
    if not math.isclose(
        allocated,
        float(committee_position_mandate["authorized_strategy_capital_usd"]),
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise ValueError("risk capital input must match the committee capital mandate")

    expected_raw = committee_position_mandate["committee_expected_targets"]
    if any(isinstance(value, bool) for value in expected_raw.values()):
        raise ValueError("committee expected targets must be integer lots")
    expected = {str(key): int(value) for key, value in expected_raw.items()}
    positions = {
        str(key): int(value)
        for key, value in dict(current_positions or {}).items()
    }
    profile = resolve_oil_short_horizon_risk_profile(risk_profile)
    policy, company_reference_limits = _resolved_policy(
        company_risk_appetite, profile
    )
    contracts = {
        str(item["contract_id"]): item for item in market["curve"]["contracts"]
    }
    market_gross_cap = int(
        market["participantLimitsPolicy"]["all_contract_gross_position_cap_lots"]
    )

    preliminary: dict[str, int] = {}
    per_contract_binding: dict[str, list[str]] = {}
    hard_facts: dict[str, Any] = {}
    for contract_id, target in expected.items():
        contract_item = contracts.get(contract_id)
        current = int(positions.get(contract_id, 0))
        binding: list[str] = []
        if contract_item is None:
            cap = 0
            hard_facts[contract_id] = {
                "contract_available": False,
                "current_position_lots": current,
                "committee_expected_target_lots": target,
            }
            binding.append("contract_unavailable")
        else:
            limits = contract_item["participantLimits"]
            market_cap = int(limits["single_contract_position_limit_lots"])
            turn_limit = int(limits["turn_trade_limit_lots"])
            concentration_cap = math.floor(
                market_gross_cap
                * policy["max_single_contract_share_of_market_gross"]
            )
            liquidity_cap = math.floor(
                turn_limit * policy["max_liquidation_half_turns"]
            )
            cap = min(market_cap, concentration_cap, liquidity_cap)
            if cap == concentration_cap:
                binding.append("risk_concentration_buffer")
            if cap == liquidity_cap:
                binding.append("risk_liquidity_buffer")
            half_turns = float(contract_item["half_turns_to_expiry"])
            roll_buffer = float(policy["roll_buffer_half_turns"])
            if roll_buffer > 0.0 and half_turns < roll_buffer:
                roll_cap = math.floor(market_cap * half_turns / roll_buffer)
                if roll_cap < cap:
                    cap = roll_cap
                    binding.append("risk_roll_buffer")
            if not bool(limits["new_trades_allowed"]):
                cap = min(cap, abs(current))
                binding.append("market_new_trades_closed")
            hard_facts[contract_id] = {
                "contract_available": True,
                "current_position_lots": current,
                "committee_expected_target_lots": target,
                "price_usd": float(contract_item["price_usd"]),
                "market_position_limit_lots": market_cap,
                "turn_trade_limit_lots": turn_limit,
                "half_turns_to_expiry": half_turns,
                "new_trades_allowed": bool(limits["new_trades_allowed"]),
            }
        approved = (
            int(math.copysign(min(abs(target), max(0, cap)), target))
            if target
            else 0
        )
        if abs(approved) < abs(target) and not binding:
            binding.append("risk_position_cap")
        preliminary[contract_id] = approved
        per_contract_binding[contract_id] = binding

    pre_estimates = base._risk_estimates(
        market=market,
        targets=preliminary,
        strategy_type=strategy_type,
        profile=profile,
        policy=policy,
        config=base_config,
    )
    stress = float(pre_estimates["estimated_stress_loss_usd"])
    margin = float(pre_estimates["initial_margin_usd"])
    stress_strategy_limit = (
        allocated
        * policy["max_strategy_stress_loss_pct_of_allocated_capital"]
        / 100.0
    )
    margin_strategy_limit = (
        allocated * policy["max_margin_pct_of_allocated_capital"] / 100.0
    )
    scale_candidates = {
        "strategy_stress": (
            1.0 if stress <= 1e-12 else stress_strategy_limit / stress
        ),
        "strategy_margin": (
            1.0 if margin <= 1e-12 else margin_strategy_limit / margin
        ),
    }
    minimum_scale = min(scale_candidates.values())
    portfolio_scale = clamp(min(1.0, minimum_scale), 0.0, 1.0)
    portfolio_binding = [
        key
        for key, value in scale_candidates.items()
        if value < 1.0
        and math.isclose(value, minimum_scale, rel_tol=1e-8, abs_tol=1e-10)
    ]

    approved_targets: dict[str, int] = {}
    for contract_id, prior in preliminary.items():
        approved = (
            int(math.copysign(math.floor(abs(prior) * portfolio_scale), prior))
            if prior
            else 0
        )
        expected_target = expected[contract_id]
        if abs(approved) > abs(expected_target) or approved * expected_target < 0:
            raise ValueError(
                "risk review expanded or reversed committee position mandate"
            )
        approved_targets[contract_id] = approved
        if approved != expected_target:
            per_contract_binding[contract_id] = sorted(
                set(per_contract_binding[contract_id] + portfolio_binding)
            )

    approved_estimates = base._risk_estimates(
        market=market,
        targets=approved_targets,
        strategy_type=strategy_type,
        profile=profile,
        policy=policy,
        config=base_config,
    )
    review_weeks = float(base_config["risk_horizon"]["review_horizon_weeks"])
    company_stress_pct = 100.0 * stress / equity
    company_margin_pct = 100.0 * margin / equity
    result = {
        "schemaVersion": "asset-simulation-oil-short-horizon-risk-review-v2",
        "strategy": {
            "strategy_id": str(committee_position_mandate["strategy_id"]),
            "strategy_type": strategy_type,
            "scope": scope,
        },
        "riskHorizon": {
            "review_horizon_weeks": review_weeks,
            "basis": "short_horizon_review_window",
            "annualized_volatility_is_rescaled": True,
        },
        "riskDepartment": {
            "coverage_scope": dict(profile["coverage_scope"]),
            "appointment": dict(profile["appointment"]),
            "style_radar": dict(profile["style_radar"]),
            "capability_radar": dict(profile["capability_radar"]),
            "profile_hash": profile["profile_hash"],
            "personnel_measurement_model_version": base.OIL_SHORT_HORIZON_RISK_MODEL_VERSION,
            "review_model_version": OIL_SHORT_HORIZON_RISK_MODEL_VERSION,
        },
        "companyRiskAppetite": {
            "policy_hash": company_risk_appetite["policy_hash"],
            "risk_appetite_radar": dict(
                company_risk_appetite["risk_appetite_radar"]
            ),
            "resolved_binding_strategy_limits": policy,
            "company_materiality_reference_limits": company_reference_limits,
        },
        "capitalContext": {
            "company_equity_usd": equity,
            "allocated_strategy_capital_usd": allocated,
            "allocation_pct_of_company_equity": 100.0 * allocated / equity,
            "capital_recommendation_produced": False,
        },
        "hardFacts": hard_facts,
        "softRiskEstimatesBeforePortfolioScale": pre_estimates,
        "materialityBeforePortfolioScale": {
            "stress_loss_pct_of_allocated_strategy_capital": 100.0 * stress / allocated,
            "stress_loss_pct_of_company_equity": company_stress_pct,
            "margin_pct_of_allocated_strategy_capital": 100.0 * margin / allocated,
            "margin_pct_of_company_equity": company_margin_pct,
        },
        "companyMaterialityDiagnostic": {
            "binding_in_strategy_review": False,
            "future_binding_owner": "corporate_aggregate_risk",
            "stress_loss_pct_of_company_equity": company_stress_pct,
            "margin_pct_of_company_equity": company_margin_pct,
            **company_reference_limits,
        },
        "portfolioScale": portfolio_scale,
        "portfolioBindingRules": sorted(portfolio_binding),
        "committeeExpectedTargets": expected,
        "riskApprovedTargets": approved_targets,
        "bindingRulesByContract": per_contract_binding,
        "softRiskEstimatesAfterApproval": approved_estimates,
        "governance": {
            "capital_allocation_owner": "investment_decision_committee",
            "risk_review_reads_actual_position_mandate": True,
            "capital_recommendation_produced": False,
            "can_expand_committee_mandate": False,
            "company_risk_appetite_owned_by_cro": False,
            "hard_facts_depend_on_capability": False,
            "hidden_future_used": False,
            "risk_horizon_matches_short_horizon_group": True,
            "strategy_relative_limits_binding": True,
            "company_materiality_binding_in_strategy_review": False,
            "future_company_materiality_owner": "corporate_aggregate_risk",
        },
    }
    rounded = base._round_nested(result)
    identity = {
        "model_version": OIL_SHORT_HORIZON_RISK_MODEL_VERSION,
        "config_id": overlay["config_id"],
        "config_hash": sha256_json(overlay),
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": sha256_json(contract),
        "base_measurement_config_hash": base_assets[
            "oil_short_horizon_risk_config_hash"
        ],
        "position_mandate_hash": committee_position_mandate["identity"][
            "result_hash"
        ],
        "company_risk_appetite_hash": company_risk_appetite["policy_hash"],
        "risk_profile_hash": profile["profile_hash"],
        "write_back": False,
        "result_hash": sha256_json(rounded),
    }
    return base._round_nested({"identity": identity, **rounded})
