"""Strategy-level oil risk review, committee mandate and pre-trade enforcement."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .math_utils import clamp
from .oil_strategy_research import resolve_oil_strategy_research_profile
from .registry import load_registered_assets, sha256_json


OIL_STRATEGY_RISK_MODEL_VERSION = "asset-simulation-oil-strategy-risk-v0.3.0"
OIL_STRATEGY_RISK_CONTRACT_ID = "oil_strategy_risk_v1"
REGISTERED_STRATEGY_RISK_MANDATE_ID = "oil_directional_strategy_risk_mandate_v1"
RISK_STATUS_ORDER = ("normal", "watch", "restricted", "reduce_only")
POSITION_RISK_TIER_ORDER = ("light", "moderate", "heavy", "danger")


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("oil strategy risk contains a non-finite value")
        return round(value, 8)
    return value


def _assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_strategy_risk_config"]
    contract = assets["oil_strategy_risk_contract"]
    if config["model_version"] != OIL_STRATEGY_RISK_MODEL_VERSION:
        raise ValueError("registered oil strategy risk config version mismatch")
    if contract["contract_id"] != OIL_STRATEGY_RISK_CONTRACT_ID:
        raise ValueError("registered oil strategy risk contract id mismatch")
    if any(
        "capital_deployment" in dict(weights)
        for weights in config["strategy_pressure_weights"].values()
    ):
        raise ValueError(
            "PM capital deployment must not alter the independent strategy risk policy"
        )
    if config.get("mandate_owner") != "oil_directional_strategy_design":
        raise ValueError("oil strategy risk mandate owner mismatch")
    registered_tolerance = config.get("registered_risk_tolerance_scores", {})
    expected_tolerance = {
        "capital_tolerance",
        "volatility_tolerance",
        "drawdown_tolerance",
        "concentration_tolerance",
        "liquidity_tolerance",
        "roll_risk_tolerance",
    }
    if set(registered_tolerance) != expected_tolerance or any(
        not math.isfinite(float(value)) or not 0.0 <= float(value) <= 100.0
        for value in registered_tolerance.values()
    ):
        raise ValueError("registered oil strategy risk tolerance is invalid")
    position_mandate = config["position_dependent_mandate"]
    if position_mandate.get("owner") != "oil_directional_strategy_design":
        raise ValueError("oil strategy position risk must be owned by strategy design")
    if position_mandate.get("approval_owner") != "investment_decision_committee":
        raise ValueError("oil strategy position risk must be approved by committee")
    tiers = position_mandate["tiers"]
    if tuple(tiers) != POSITION_RISK_TIER_ORDER:
        raise ValueError("oil strategy position risk tiers are out of order")
    previous_upper = 0.0
    previous_completion = 1.0
    for tier_name in POSITION_RISK_TIER_ORDER:
        tier = tiers[tier_name]
        upper = float(tier["upper_utilization"])
        completion = float(tier["risk_increasing_gap_completion"])
        if not previous_upper < upper <= 1.0:
            raise ValueError("oil strategy position risk tier bounds are invalid")
        if not 0.0 <= completion <= previous_completion:
            raise ValueError("oil strategy position risk completion rates are invalid")
        previous_upper = upper
        previous_completion = completion
    if not math.isclose(previous_upper, 1.0):
        raise ValueError("oil strategy position risk tiers must end at full utilization")
    reduce_only_trigger = float(position_mandate["reduce_only_trigger"])
    if not float(tiers["heavy"]["upper_utilization"]) < reduce_only_trigger <= 1.0:
        raise ValueError("oil strategy position risk reduce-only trigger is invalid")
    return assets, config, contract


def _piecewise(anchor: Mapping[str, Any], score: float) -> float:
    value = clamp(float(score), 0.0, 100.0)
    if value <= 50.0:
        mix = value / 50.0
        return float(anchor["score_0"]) + mix * (
            float(anchor["score_50"]) - float(anchor["score_0"])
        )
    mix = (value - 50.0) / 50.0
    return float(anchor["score_50"]) + mix * (
        float(anchor["score_100"]) - float(anchor["score_50"])
    )


def _weighted_score(values: Mapping[str, float], weights: Mapping[str, Any]) -> float:
    total_weight = sum(float(weight) for weight in weights.values())
    if total_weight <= 0.0:
        raise ValueError("oil strategy risk pressure weights must be positive")
    return clamp(
        sum(float(values[key]) * float(weight) for key, weight in weights.items())
        / total_weight,
        0.0,
        100.0,
    )


def _allowance_score(
    *, tolerance_score: float, pressure_score: float, config: Mapping[str, Any]
) -> float:
    formula = config["review_score_formula"]
    neutral = float(formula["neutral"])
    return clamp(
        neutral
        + float(formula["risk_tolerance_weight"])
        * (float(tolerance_score) - neutral)
        - float(formula["strategy_pressure_weight"])
        * (float(pressure_score) - neutral),
        0.0,
        100.0,
    )


def _review_rationales(
    *, strategy_radar: Mapping[str, float], pressures: Mapping[str, float]
) -> list[str]:
    result: list[str] = []
    if pressures["capacity"] >= 60.0:
        result.append("快速响应或低选择性提高容量需求，建议保留额外缓冲")
    if pressures["volatility"] >= 60.0:
        result.append("信号反应或低选择性提高路径波动暴露")
    if pressures["liquidity"] >= 60.0:
        result.append("换手和近月使用提高流动性依赖")
    if pressures["drawdown"] >= 60.0:
        result.append("资金表达、持仓耐心或方向取向提高回撤滞后风险")
    if float(strategy_radar["near_month_focus"]) >= 65.0:
        result.append("近月集中需要单独检查移仓与到期容量")
    if not result:
        result.append("策略结构处于中等风险区间，采用常规风险授权")
    return result[:3]


def build_oil_strategy_risk_review(
    strategy_profile: Mapping[str, Any] | None,
    corporate_risk_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the registered single-strategy mandate from visible PM structure.

    ``corporate_risk_profile`` is accepted only as a legacy call-site shim.  It
    is deliberately ignored: the current single-strategy runtime has no CRO
    personnel seat and no personnel-dependent risk tolerance.
    """

    assets, config, contract = _assets()
    strategy = resolve_oil_strategy_research_profile(strategy_profile)
    _ = corporate_risk_profile
    radar = {key: float(value) for key, value in strategy["style_radar"].items()}
    risk_radar = {
        key: float(value)
        for key, value in config["registered_risk_tolerance_scores"].items()
    }
    derived = {
        **radar,
        "inverse_selectivity": 100.0 - radar["selectivity"],
        "inverse_forecast_horizon": 100.0 - radar["forecast_horizon"],
        "orientation_extremeness": min(
            100.0, 2.0 * abs(radar["continuation_reversion"] - 50.0)
        ),
    }
    pressures = {
        key: _weighted_score(derived, weights)
        for key, weights in config["strategy_pressure_weights"].items()
    }
    tolerance_owner = {
        "capacity": "capital_tolerance",
        "volatility": "volatility_tolerance",
        "drawdown": "drawdown_tolerance",
        "concentration": "concentration_tolerance",
        "liquidity": "liquidity_tolerance",
        "roll": "roll_risk_tolerance",
    }
    allowance = {
        key: _allowance_score(
            tolerance_score=risk_radar[tolerance_owner[key]],
            pressure_score=pressures[key],
            config=config,
        )
        for key in pressures
    }
    mapping = config["parameter_mapping"]
    registered_position_mandate = config["position_dependent_mandate"]
    capital_recommendation = _piecewise(
        mapping["recommended_capital_authorization_pct_of_company_equity"],
        allowance["capacity"],
    )
    proposed_policy = {
        "capacity": {
            "recommended_capital_authorization_pct_of_company_equity": capital_recommendation,
            "max_gross_market_limit_utilization": _piecewise(
                mapping["max_gross_market_limit_utilization"], allowance["capacity"]
            ),
            "max_initial_margin_pct_of_authorized_capital": _piecewise(
                mapping["max_initial_margin_pct_of_authorized_capital"],
                allowance["capacity"],
            ),
        },
        "volatility": {
            "annualized_volatility_budget_pct_of_authorized_capital": _piecewise(
                mapping[
                    "annualized_volatility_budget_pct_of_authorized_capital"
                ],
                allowance["volatility"],
            )
        },
        "drawdown": {
            "watch_pct": _piecewise(
                mapping["drawdown_watch_pct"], allowance["drawdown"]
            ),
            "restrict_pct": _piecewise(
                mapping["drawdown_restrict_pct"], allowance["drawdown"]
            ),
            "reduce_only_pct": _piecewise(
                mapping["drawdown_reduce_only_pct"], allowance["drawdown"]
            ),
            **{
                key: float(value)
                for key, value in config["drawdown_scaling"].items()
            },
        },
        "concentration": {
            "max_single_contract_share_of_strategy_gross": _piecewise(
                mapping["max_single_contract_share_of_strategy_gross"],
                allowance["concentration"],
            )
        },
        "liquidity": {
            "max_liquidation_half_turns": _piecewise(
                mapping["max_liquidation_half_turns"], allowance["liquidity"]
            )
        },
        "roll": {
            "roll_buffer_half_turns": _piecewise(
                mapping["roll_buffer_half_turns"], allowance["roll"]
            )
        },
        "positionUtilization": {
            "owner": str(registered_position_mandate["owner"]),
            "approval_owner": str(
                registered_position_mandate["approval_owner"]
            ),
            "utilization_basis": str(
                registered_position_mandate["utilization_basis"]
            ),
            "reduce_only_trigger": float(
                registered_position_mandate["reduce_only_trigger"]
            ),
            "tiers": {
                tier_name: {
                    "upper_utilization": float(
                        registered_position_mandate["tiers"][tier_name][
                            "upper_utilization"
                        ]
                    ),
                    "risk_increasing_gap_completion": float(
                        registered_position_mandate["tiers"][tier_name][
                            "risk_increasing_gap_completion"
                        ]
                    ),
                }
                for tier_name in POSITION_RISK_TIER_ORDER
            },
        },
    }
    mandate_identity = {
        "mandate_id": REGISTERED_STRATEGY_RISK_MANDATE_ID,
        "owner": str(config["mandate_owner"]),
        "personnel_id": None,
        "personnel_profile_hash": None,
        "registered_tolerance_scores": risk_radar,
    }
    mandate_identity["mandate_hash"] = sha256_json(mandate_identity)
    result = {
        "schemaVersion": "asset-simulation-oil-strategy-risk-review-v1",
        "strategy": {
            "strategy_id": strategy["signal_engine"]["strategy_id"],
            "personnel_id": strategy["appointment"]["personnel_id"],
            "display_name": strategy["appointment"]["display_name"],
            "profile_hash": strategy["profile_hash"],
        },
        "riskMandate": mandate_identity,
        "strategyRiskPressures": pressures,
        "reviewAllowanceScores": allowance,
        "proposedPolicy": proposed_policy,
        "rationales": _review_rationales(
            strategy_radar=radar, pressures=pressures
        ),
        "governance": {
            "proposal_owner": "oil_directional_strategy_design",
            "approval_owner": "investment_decision_committee",
            "capital_allocation_owner": "investment_decision_committee",
            "strategy_director_can_self_approve": False,
            "proposal_is_capital_allocation": False,
            "pm_capital_deployment_is_review_policy_input": False,
            "strategy_amount_is_review_policy_input": False,
            "position_risk_mandate_owner": "oil_directional_strategy_design",
            "risk_personnel_institution_enabled": False,
            "risk_personnel_input_used": False,
            "future_portfolio_risk_layer_status": "dormant_single_strategy",
            "market_hard_rules_unchanged": True,
        },
    }
    rounded_result = _round_nested(result)
    identity = {
        "model_version": OIL_STRATEGY_RISK_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_strategy_risk_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_strategy_risk_contract_hash"],
        "strategy_profile_hash": strategy["profile_hash"],
        "risk_mandate_id": mandate_identity["mandate_id"],
        "risk_mandate_hash": mandate_identity["mandate_hash"],
        "write_back": False,
        "result_hash": sha256_json(rounded_result),
    }
    return _round_nested({"identity": identity, **rounded_result})


def build_investment_committee_strategy_approval(
    review: Mapping[str, Any],
    *,
    company_equity_usd: float,
    capital_authorization_pct_of_company_equity: float | None = None,
    approve_risk_policy: bool = True,
) -> dict[str, Any]:
    """Approve risk's mandate and independently authorize strategy capital."""

    assets, config, contract = _assets()
    equity = float(company_equity_usd)
    if not math.isfinite(equity) or equity <= 0.0:
        raise ValueError("company equity for strategy approval must be positive")
    if review.get("identity", {}).get("model_version") != OIL_STRATEGY_RISK_MODEL_VERSION:
        raise ValueError("strategy risk review model version mismatch")
    review_body = {key: value for key, value in review.items() if key != "identity"}
    if sha256_json(review_body) != review["identity"].get("result_hash"):
        raise ValueError("strategy risk review was modified after generation")
    if not isinstance(approve_risk_policy, bool):
        raise ValueError("approve_risk_policy must be boolean")
    default_pct = float(
        config["committee_proxy"][
            "default_capital_authorization_pct_of_company_equity"
        ]
    )
    authorization_pct = float(
        default_pct
        if capital_authorization_pct_of_company_equity is None
        else capital_authorization_pct_of_company_equity
    )
    if not math.isfinite(authorization_pct) or not 0.0 <= authorization_pct <= 100.0:
        raise ValueError("capital authorization must be between 0 and 100 percent")
    if not approve_risk_policy:
        authorization_pct = 0.0
    recommended_pct = float(
        review["proposedPolicy"]["capacity"][
            "recommended_capital_authorization_pct_of_company_equity"
        ]
    )
    result = {
        "schemaVersion": "asset-simulation-investment-committee-strategy-approval-v1",
        "status": "approved" if approve_risk_policy else "rejected",
        "strategy": dict(review["strategy"]),
        "riskMandate": dict(review["riskMandate"]),
        "riskPolicyDecision": {
            "method": config["committee_proxy"]["risk_policy_approval_method"],
            "approvedPolicy": (
                dict(review["proposedPolicy"]) if approve_risk_policy else None
            ),
            "review_hash": review["identity"]["result_hash"],
        },
        "capitalAuthorization": {
            "company_equity_usd": equity,
            "recommended_pct_of_company_equity": recommended_pct,
            "authorized_pct_of_company_equity": authorization_pct,
            "authorized_capital_usd": equity * authorization_pct / 100.0,
            "deviation_from_risk_recommendation_pct": (
                authorization_pct - recommended_pct
            ),
            "decision_method": config["committee_proxy"][
                "capital_authorization_method"
            ],
        },
        "governance": {
            "approval_owner": config["decision_owner"],
            "risk_mandate_owner": config["mandate_owner"],
            "risk_personnel_institution_enabled": False,
            "capital_authorization_is_committee_discretion": True,
            "risk_policy_and_capital_are_separate_decisions": True,
            "market_capacity_is_not_authorized_here": True,
        },
    }
    rounded_result = _round_nested(result)
    identity = {
        "model_version": OIL_STRATEGY_RISK_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_strategy_risk_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_strategy_risk_contract_hash"],
        "review_hash": review["identity"]["result_hash"],
        "strategy_profile_hash": review["strategy"]["profile_hash"],
        "risk_mandate_id": review["riskMandate"]["mandate_id"],
        "risk_mandate_hash": review["riskMandate"]["mandate_hash"],
        "write_back": False,
        "result_hash": sha256_json(rounded_result),
    }
    return _round_nested({"identity": identity, **rounded_result})


def _visible_annualized_volatility(
    contract: Mapping[str, Any], config: Mapping[str, Any]
) -> float:
    weeks = [
        week
        for month in contract.get("monthly", ())
        for week in month.get("weekly", ())
    ]
    lookback = int(config["visible_volatility"]["lookback_weeks"])
    closes = [
        float(week["close"])
        for week in weeks[-(lookback + 1) :]
        if float(week["close"]) > 0.0
    ]
    returns = [math.log(after / before) for before, after in zip(closes, closes[1:])]
    if not returns:
        realized = float(
            config["visible_volatility"]["minimum_annualized_volatility"]
        )
    else:
        realized = math.sqrt(
            float(config["visible_volatility"]["annualization_weeks"])
            * sum(value * value for value in returns)
            / len(returns)
        )
    return clamp(
        realized,
        float(config["visible_volatility"]["minimum_annualized_volatility"]),
        float(config["visible_volatility"]["maximum_annualized_volatility"]),
    )


def _drawdown_report(
    *, strategy_equity_usd: float, risk_state: Mapping[str, Any] | None,
    policy: Mapping[str, Any]
) -> dict[str, Any]:
    state = dict(risk_state or {})
    peak = max(
        float(strategy_equity_usd),
        float(state.get("peak_strategy_equity_usd", strategy_equity_usd)),
    )
    drawdown_pct = 100.0 * max(0.0, 1.0 - float(strategy_equity_usd) / peak)
    drawdown = policy["drawdown"]
    watch = float(drawdown["watch_pct"])
    restrict = float(drawdown["restrict_pct"])
    reduce_only = float(drawdown["reduce_only_pct"])
    if not 0.0 < watch < restrict < reduce_only:
        raise ValueError("strategy risk drawdown thresholds must be increasing")
    if drawdown_pct <= watch:
        raw_scale, status = 1.0, "normal"
    elif drawdown_pct <= restrict:
        mix = (drawdown_pct - watch) / (restrict - watch)
        raw_scale = 1.0 + mix * (float(drawdown["watch_scale"]) - 1.0)
        status = "watch"
    elif drawdown_pct <= reduce_only:
        mix = (drawdown_pct - restrict) / (reduce_only - restrict)
        raw_scale = float(drawdown["watch_scale"]) + mix * (
            float(drawdown["restrict_scale"])
            - float(drawdown["watch_scale"])
        )
        status = "restricted"
    else:
        raw_scale, status = float(drawdown["reduce_only_scale"]), "reduce_only"
    previous_scale = state.get("strategy_drawdown_scale")
    if previous_scale is None:
        effective_scale = raw_scale
    else:
        previous = clamp(float(previous_scale), 0.0, 1.0)
        recovery_cap = previous + float(drawdown["maximum_scale_recovery_per_turn"])
        effective_scale = min(raw_scale, recovery_cap) if raw_scale > previous else raw_scale
    return {
        "peak_strategy_equity_usd": peak,
        "current_strategy_equity_usd": float(strategy_equity_usd),
        "strategy_drawdown_pct": drawdown_pct,
        "raw_strategy_drawdown_scale": raw_scale,
        "strategy_drawdown_scale": effective_scale,
        "risk_status": status,
        "previous_strategy_drawdown_scale": previous_scale,
        "scale_recovery_capped": previous_scale is not None and raw_scale > effective_scale,
    }


def _nonincreasing_target(current_position: int, desired_target: int) -> int:
    if current_position == 0 or desired_target == 0 or current_position * desired_target <= 0:
        return 0
    return int(math.copysign(min(abs(current_position), abs(desired_target)), current_position))


def _budget_usage(amount: float, budget: float) -> float:
    if budget <= 0.0:
        return 0.0 if amount <= 0.0 else 1.0
    return max(0.0, float(amount) / float(budget))


def _position_risk_usage(
    *,
    gross_lots: int,
    initial_margin_usd: float,
    annualized_risk_usd: float,
    gross_cap_lots: int,
    initial_margin_budget_usd: float,
    annualized_risk_budget_usd: float,
) -> dict[str, Any]:
    ratios = {
        "gross": _budget_usage(float(gross_lots), float(gross_cap_lots)),
        "initial_margin": _budget_usage(
            initial_margin_usd, initial_margin_budget_usd
        ),
        "visible_volatility": _budget_usage(
            annualized_risk_usd, annualized_risk_budget_usd
        ),
    }
    binding_dimension = max(ratios, key=ratios.__getitem__)
    return {
        "gross_lots": int(gross_lots),
        "initial_margin_usd": float(initial_margin_usd),
        "annualized_risk_usd": float(annualized_risk_usd),
        "utilization_by_dimension": ratios,
        "binding_dimension": binding_dimension,
        "maximum_utilization": float(ratios[binding_dimension]),
    }


def _position_risk_tier(
    utilization: float, policy: Mapping[str, Any]
) -> str:
    value = max(0.0, float(utilization))
    for tier_name in POSITION_RISK_TIER_ORDER:
        if value <= float(policy["tiers"][tier_name]["upper_utilization"]):
            return tier_name
    return "danger"


def _position_gap_completion(
    *, current_utilization: float, proposed_utilization: float,
    policy: Mapping[str, Any]
) -> float:
    """Resolve the risk-increasing gap rate from the actual current position."""

    current = max(0.0, float(current_utilization))
    proposed = max(0.0, float(proposed_utilization))
    if proposed <= current:
        return 1.0
    reduce_only_trigger = float(policy["reduce_only_trigger"])
    if current >= reduce_only_trigger:
        return 0.0
    current_tier = _position_risk_tier(current, policy)
    return float(
        policy["tiers"][current_tier]["risk_increasing_gap_completion"]
    )


def _apply_position_gap_completion(
    *, current_position: int, desired_target: int, completion: float
) -> int:
    """Let reductions pass while slowing only the risk-increasing target gap."""

    rate = clamp(float(completion), 0.0, 1.0)
    current = int(current_position)
    desired = int(desired_target)
    if rate >= 1.0 or current == desired:
        return desired
    if desired == 0:
        return 0
    if current == 0:
        return int(round(desired * rate))
    if current * desired > 0:
        if abs(desired) <= abs(current):
            return desired
        return current + int(round((desired - current) * rate))
    return int(round(desired * rate))


def _position_change_increases_risk(
    *, current_position: int, desired_target: int
) -> bool:
    current = int(current_position)
    desired = int(desired_target)
    if desired == 0:
        return False
    if current == 0 or current * desired < 0:
        return True
    return abs(desired) > abs(current)


def apply_oil_strategy_risk_mandate(
    market: Mapping[str, Any],
    targets: Mapping[str, Mapping[str, Any]],
    *,
    positions: Mapping[str, int],
    strategy_equity_usd: float,
    committee_approval: Mapping[str, Any],
    risk_state: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Apply approved single-strategy limits before company-wide risk."""

    assets, config, contract = _assets()
    if committee_approval.get("identity", {}).get("model_version") != OIL_STRATEGY_RISK_MODEL_VERSION:
        raise ValueError("strategy risk committee approval model version mismatch")
    approval_body = {
        key: value for key, value in committee_approval.items() if key != "identity"
    }
    if sha256_json(approval_body) != committee_approval["identity"].get(
        "result_hash"
    ):
        raise ValueError("strategy risk committee approval was modified")
    approved_policy = committee_approval["riskPolicyDecision"].get("approvedPolicy")
    authorization = committee_approval["capitalAuthorization"]
    authorized_capital = float(authorization["authorized_capital_usd"])
    if approved_policy is None or committee_approval.get("status") != "approved":
        authorized_capital = 0.0
        approved_policy = committee_approval.get("riskPolicyDecision", {}).get(
            "approvedPolicy"
        ) or build_oil_strategy_risk_review(None, None)["proposedPolicy"]
    policy = approved_policy
    contracts = {
        str(item["contract_id"]): item for item in market["curve"]["contracts"]
    }
    specification = market["contractSpecification"]
    contract_size = float(specification["contract_size_bbl"])
    initial_margin_rate = float(specification["initial_margin_rate_pct"]) / 100.0
    market_gross_cap = int(
        market["participantLimitsPolicy"]["all_contract_gross_position_cap_lots"]
    )
    strategy_gross_cap = math.floor(
        market_gross_cap
        * float(policy["capacity"]["max_gross_market_limit_utilization"])
    )
    strategy_margin_budget = authorized_capital * float(
        policy["capacity"]["max_initial_margin_pct_of_authorized_capital"]
    ) / 100.0
    strategy_volatility_budget = authorized_capital * float(
        policy["volatility"][
            "annualized_volatility_budget_pct_of_authorized_capital"
        ]
    ) / 100.0
    drawdown = _drawdown_report(
        strategy_equity_usd=float(strategy_equity_usd),
        risk_state=risk_state,
        policy=policy,
    )
    max_share = float(
        policy["concentration"]["max_single_contract_share_of_strategy_gross"]
    )
    liquidation_turns = float(policy["liquidity"]["max_liquidation_half_turns"])
    roll_buffer = float(policy["roll"]["roll_buffer_half_turns"])

    approved: dict[str, dict[str, Any]] = {}
    for contract_id, raw_item in targets.items():
        item = dict(raw_item)
        strategy_intent = int(item["target_position_lots"])
        current_position = int(positions.get(contract_id, 0))
        contract_item = contracts.get(contract_id)
        binding: list[str] = []
        if contract_item is None:
            individual_cap = 0
            annualized_volatility = 0.0
            binding.append("contract_unavailable")
        else:
            limits = contract_item["participantLimits"]
            market_single_cap = int(limits["single_contract_position_limit_lots"])
            concentration_cap = math.floor(strategy_gross_cap * max_share)
            liquidity_cap = math.floor(
                int(limits["turn_trade_limit_lots"]) * liquidation_turns
            )
            individual_cap = min(
                market_single_cap, concentration_cap, liquidity_cap
            )
            annualized_volatility = _visible_annualized_volatility(
                contract_item, config
            )
            if individual_cap == concentration_cap:
                binding.append("strategy_concentration")
            if individual_cap == liquidity_cap:
                binding.append("strategy_liquidity_exit")
            if not bool(limits["new_trades_allowed"]):
                individual_cap = min(individual_cap, abs(current_position))
                binding.append("market_new_trades_closed")
            half_turns_to_expiry = float(contract_item["half_turns_to_expiry"])
            if roll_buffer > 0.0 and half_turns_to_expiry < roll_buffer:
                roll_cap = math.floor(
                    market_single_cap * half_turns_to_expiry / roll_buffer
                )
                if roll_cap < individual_cap:
                    individual_cap = roll_cap
                    binding.append("strategy_roll_buffer")
        scaled_target = int(
            round(strategy_intent * float(drawdown["strategy_drawdown_scale"]))
        )
        if drawdown["risk_status"] == "reduce_only":
            scaled_target = _nonincreasing_target(current_position, scaled_target)
            binding.append("strategy_drawdown_reduce_only")
        bounded_target = int(
            clamp(float(scaled_target), -float(individual_cap), float(individual_cap))
        )
        if abs(bounded_target) > abs(strategy_intent) or bounded_target * strategy_intent < 0:
            raise ValueError("strategy risk expanded or reversed strategy intent")
        if bounded_target != strategy_intent and not binding:
            binding.append("strategy_drawdown_scale")
        price = 0.0 if contract_item is None else float(contract_item["price_usd"])
        item.update(
            {
                "strategy_intent_target_position_lots": strategy_intent,
                "strategy_risk_approved_target_position_lots": bounded_target,
                "strategy_risk_clip_lots": strategy_intent - bounded_target,
                "strategy_risk_binding_rules": sorted(set(binding)),
                "target_position_lots": bounded_target,
                "strategy_visible_annualized_volatility": annualized_volatility,
                "strategy_estimated_annualized_risk_usd": (
                    abs(bounded_target) * price * contract_size * annualized_volatility
                ),
                "strategy_estimated_initial_margin_usd": (
                    abs(bounded_target) * price * contract_size * initial_margin_rate
                ),
            }
        )
        approved[contract_id] = item

    gross_before = sum(
        abs(int(item["target_position_lots"])) for item in approved.values()
    )
    margin_before = sum(
        float(item["strategy_estimated_initial_margin_usd"])
        for item in approved.values()
    )
    volatility_before = sum(
        float(item["strategy_estimated_annualized_risk_usd"])
        for item in approved.values()
    )
    portfolio_scale = min(
        1.0,
        strategy_gross_cap / max(1, gross_before),
        strategy_margin_budget / max(1e-9, margin_before),
        strategy_volatility_budget / max(1e-9, volatility_before),
    )
    portfolio_binding: list[str] = []
    if portfolio_scale < 1.0:
        if math.isclose(
            portfolio_scale, strategy_gross_cap / max(1, gross_before), rel_tol=1e-9
        ):
            portfolio_binding.append("strategy_gross_cap")
        if math.isclose(
            portfolio_scale, strategy_margin_budget / max(1e-9, margin_before), rel_tol=1e-9
        ):
            portfolio_binding.append("strategy_margin_budget")
        if math.isclose(
            portfolio_scale, strategy_volatility_budget / max(1e-9, volatility_before), rel_tol=1e-9
        ):
            portfolio_binding.append("strategy_volatility_budget")
        for item in approved.values():
            intent = int(item["strategy_intent_target_position_lots"])
            prior = int(item["target_position_lots"])
            adjusted = (
                int(math.copysign(math.floor(abs(prior) * portfolio_scale), prior))
                if prior
                else 0
            )
            item["target_position_lots"] = adjusted
            item["strategy_risk_approved_target_position_lots"] = adjusted
            item["strategy_risk_clip_lots"] = intent - adjusted
            item["strategy_risk_binding_rules"] = sorted(
                set(item["strategy_risk_binding_rules"] + portfolio_binding)
            )
            contract_item = contracts.get(str(item["contract_id"]))
            price = 0.0 if contract_item is None else float(contract_item["price_usd"])
            item["strategy_estimated_annualized_risk_usd"] = (
                abs(adjusted)
                * price
                * contract_size
                * float(item["strategy_visible_annualized_volatility"])
            )
            item["strategy_estimated_initial_margin_usd"] = (
                abs(adjusted) * price * contract_size * initial_margin_rate
            )

    pre_position_curve_gross = sum(
        abs(int(item["target_position_lots"])) for item in approved.values()
    )
    pre_position_curve_margin = sum(
        float(item["strategy_estimated_initial_margin_usd"])
        for item in approved.values()
    )
    pre_position_curve_volatility = sum(
        float(item["strategy_estimated_annualized_risk_usd"])
        for item in approved.values()
    )
    proposed_position_usage = _position_risk_usage(
        gross_lots=pre_position_curve_gross,
        initial_margin_usd=pre_position_curve_margin,
        annualized_risk_usd=pre_position_curve_volatility,
        gross_cap_lots=strategy_gross_cap,
        initial_margin_budget_usd=strategy_margin_budget,
        annualized_risk_budget_usd=strategy_volatility_budget,
    )
    current_gross = sum(abs(int(value)) for value in positions.values())
    current_margin = 0.0
    current_volatility = 0.0
    for contract_id, lots_value in positions.items():
        lots = abs(int(lots_value))
        contract_item = contracts.get(str(contract_id))
        if lots <= 0 or contract_item is None:
            continue
        price = float(contract_item["price_usd"])
        visible_volatility = _visible_annualized_volatility(contract_item, config)
        current_margin += lots * price * contract_size * initial_margin_rate
        current_volatility += (
            lots * price * contract_size * visible_volatility
        )
    current_position_usage = _position_risk_usage(
        gross_lots=current_gross,
        initial_margin_usd=current_margin,
        annualized_risk_usd=current_volatility,
        gross_cap_lots=strategy_gross_cap,
        initial_margin_budget_usd=strategy_margin_budget,
        annualized_risk_budget_usd=strategy_volatility_budget,
    )
    position_policy = policy["positionUtilization"]
    current_utilization = float(current_position_usage["maximum_utilization"])
    proposed_utilization = float(proposed_position_usage["maximum_utilization"])
    position_tier = _position_risk_tier(current_utilization, position_policy)
    current_reduce_only = (
        current_utilization >= float(position_policy["reduce_only_trigger"])
    )
    contract_risk_increase_requested = any(
        _position_change_increases_risk(
            current_position=int(positions.get(contract_id, 0)),
            desired_target=int(item["target_position_lots"]),
        )
        for contract_id, item in approved.items()
    )
    portfolio_risk_increase_requested = (
        proposed_utilization > current_utilization + 1e-12
    )
    risk_increase_requested = portfolio_risk_increase_requested or (
        current_reduce_only and contract_risk_increase_requested
    )
    position_gap_completion = (
        0.0
        if current_reduce_only and contract_risk_increase_requested
        else _position_gap_completion(
            current_utilization=current_utilization,
            proposed_utilization=proposed_utilization,
            policy=position_policy,
        )
    )
    position_binding_rules: list[str] = []
    if risk_increase_requested and position_gap_completion < 1.0:
        position_binding_rules.append(f"strategy_position_{position_tier}")
        if current_reduce_only:
            position_binding_rules.append("strategy_position_reduce_only")
        for contract_id, item in approved.items():
            intent = int(item["strategy_intent_target_position_lots"])
            prior = int(item["target_position_lots"])
            adjusted = _apply_position_gap_completion(
                current_position=int(positions.get(contract_id, 0)),
                desired_target=prior,
                completion=position_gap_completion,
            )
            if abs(adjusted) > abs(intent) or adjusted * intent < 0:
                raise ValueError(
                    "position-dependent strategy risk expanded or reversed intent"
                )
            item["target_position_lots"] = adjusted
            item["strategy_risk_approved_target_position_lots"] = adjusted
            item["strategy_risk_clip_lots"] = intent - adjusted
            if adjusted != prior:
                item["strategy_risk_binding_rules"] = sorted(
                    set(
                        item["strategy_risk_binding_rules"]
                        + position_binding_rules
                    )
                )
            contract_item = contracts.get(str(item["contract_id"]))
            price = (
                0.0 if contract_item is None else float(contract_item["price_usd"])
            )
            item["strategy_estimated_annualized_risk_usd"] = (
                abs(adjusted)
                * price
                * contract_size
                * float(item["strategy_visible_annualized_volatility"])
            )
            item["strategy_estimated_initial_margin_usd"] = (
                abs(adjusted) * price * contract_size * initial_margin_rate
            )
    for item in approved.values():
        item["strategy_position_risk_tier"] = position_tier
        item["strategy_position_risk_gap_completion"] = position_gap_completion
        item["strategy_position_risk_current_reduce_only"] = current_reduce_only

    gross_after = sum(
        abs(int(item["target_position_lots"])) for item in approved.values()
    )
    margin_after = sum(
        float(item["strategy_estimated_initial_margin_usd"])
        for item in approved.values()
    )
    volatility_after = sum(
        float(item["strategy_estimated_annualized_risk_usd"])
        for item in approved.values()
    )
    approved_position_usage = _position_risk_usage(
        gross_lots=gross_after,
        initial_margin_usd=margin_after,
        annualized_risk_usd=volatility_after,
        gross_cap_lots=strategy_gross_cap,
        initial_margin_budget_usd=strategy_margin_budget,
        annualized_risk_budget_usd=strategy_volatility_budget,
    )
    result = {
        "schemaVersion": "asset-simulation-oil-strategy-risk-enforcement-v1",
        "review": {
            "strategy": dict(committee_approval["strategy"]),
            "riskMandate": dict(committee_approval["riskMandate"]),
            "review_hash": committee_approval["identity"]["review_hash"],
        },
        "committeeApproval": committee_approval,
        "approvedPolicy": policy,
        "state": drawdown,
        "positionRisk": {
            "owner": position_policy["owner"],
            "approval_owner": position_policy["approval_owner"],
            "policy": position_policy,
            "current": current_position_usage,
            "proposed": proposed_position_usage,
            "approved": approved_position_usage,
            "effective_tier": position_tier,
            "risk_increase_requested": risk_increase_requested,
            "portfolio_risk_increase_requested": (
                portfolio_risk_increase_requested
            ),
            "contract_risk_increase_requested": (
                contract_risk_increase_requested
            ),
            "risk_increasing_gap_completion": position_gap_completion,
            "current_reduce_only": current_reduce_only,
            "binding_rules": position_binding_rules,
        },
        "strategyLimits": {
            "market_hard_gross_cap_lots": market_gross_cap,
            "strategy_gross_cap_lots": strategy_gross_cap,
            "authorized_capital_usd": authorized_capital,
            "strategy_initial_margin_budget_usd": strategy_margin_budget,
            "strategy_annualized_volatility_budget_usd": strategy_volatility_budget,
            "max_single_contract_share": max_share,
            "max_liquidation_half_turns": liquidation_turns,
            "roll_buffer_half_turns": roll_buffer,
        },
        "approvalSummary": {
            "strategy_intent_gross_lots": sum(
                abs(int(item["strategy_intent_target_position_lots"]))
                for item in approved.values()
            ),
            "pre_portfolio_approved_gross_lots": gross_before,
            "pre_position_curve_approved_gross_lots": pre_position_curve_gross,
            "approved_gross_lots": gross_after,
            "position_curve_clipped_gross_lots": (
                pre_position_curve_gross - gross_after
            ),
            "clipped_gross_lots": sum(
                abs(int(item["strategy_intent_target_position_lots"]))
                - abs(int(item["target_position_lots"]))
                for item in approved.values()
            ),
            "portfolio_scale": portfolio_scale,
            "portfolio_scale_semantics": "independent_limit_clip_not_policy_multiplier",
            "portfolio_binding_rules": portfolio_binding,
            "approved_initial_margin_usd": margin_after,
            "approved_annualized_risk_usd": volatility_after,
            "approved_annualized_risk_pct_of_authorized_capital": (
                0.0
                if authorized_capital <= 0.0
                else 100.0 * volatility_after / authorized_capital
            ),
        },
        "informationPolicy": {
            "future_market_available": False,
            "visible_history_only": True,
            "can_expand_strategy_intent": False,
            "pm_deployment_pct_used_as_risk_multiplier": False,
            "risk_limits_compare_against_strategy_intent": True,
            "position_risk_uses_current_and_proposed_positions": True,
            "position_risk_owner_is_strategy_design": True,
            "position_risk_can_slow_only_risk_increasing_gap": True,
            "can_override_company_or_market_rules": False,
        },
    }
    rounded_result = _round_nested(result)
    identity = {
        "model_version": OIL_STRATEGY_RISK_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_strategy_risk_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_strategy_risk_contract_hash"],
        "review_hash": committee_approval["identity"]["review_hash"],
        "approval_hash": committee_approval["identity"]["result_hash"],
        "write_back": False,
        "result_hash": sha256_json(rounded_result),
    }
    return _round_nested(approved), _round_nested(
        {"identity": identity, **rounded_result}
    )
