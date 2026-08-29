"""Strategy-level oil risk review, committee mandate and pre-trade enforcement."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .corporate_risk_control import resolve_corporate_risk_profile
from .math_utils import clamp
from .oil_strategy_research import resolve_oil_strategy_research_profile
from .registry import load_registered_assets, sha256_json


OIL_STRATEGY_RISK_MODEL_VERSION = "asset-simulation-oil-strategy-risk-v0.1.1"
OIL_STRATEGY_RISK_CONTRACT_ID = "oil_strategy_risk_v1"
RISK_STATUS_ORDER = ("normal", "watch", "restricted", "reduce_only")


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
        raise ValueError("registered oil strategy risk model version mismatch")
    if contract["contract_id"] != OIL_STRATEGY_RISK_CONTRACT_ID:
        raise ValueError("registered oil strategy risk contract id mismatch")
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
        result.append("资金部署与调仓需求较高，容量建议保留额外缓冲")
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
    corporate_risk_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Let the appointed risk department review one appointed strategy."""

    assets, config, contract = _assets()
    strategy = resolve_oil_strategy_research_profile(strategy_profile)
    corporate = resolve_corporate_risk_profile(corporate_risk_profile)
    radar = {key: float(value) for key, value in strategy["style_radar"].items()}
    risk_radar = {
        key: float(value)
        for key, value in corporate["risk_appetite_radar"].items()
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
    }
    result = {
        "schemaVersion": "asset-simulation-oil-strategy-risk-review-v1",
        "strategy": {
            "strategy_id": strategy["signal_engine"]["strategy_id"],
            "personnel_id": strategy["appointment"]["personnel_id"],
            "display_name": strategy["appointment"]["display_name"],
            "profile_hash": strategy["profile_hash"],
        },
        "riskDepartment": {
            "personnel_id": corporate["appointment"]["personnel_id"],
            "display_name": corporate["appointment"]["display_name"],
            "profile_hash": corporate["profile_hash"],
        },
        "strategyRiskPressures": pressures,
        "reviewAllowanceScores": allowance,
        "proposedPolicy": proposed_policy,
        "rationales": _review_rationales(
            strategy_radar=radar, pressures=pressures
        ),
        "governance": {
            "proposal_owner": "risk_department",
            "approval_owner": "investment_decision_committee",
            "capital_allocation_owner": "investment_decision_committee",
            "strategy_director_can_self_approve": False,
            "proposal_is_capital_allocation": False,
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
        "corporate_risk_profile_hash": corporate["profile_hash"],
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
    if sha256_json(review_body) != review["identity"].get(
        "result_hash"
    ):
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
        "riskDepartment": dict(review["riskDepartment"]),
        "riskPolicyDecision": {
            "method": "accept_risk_department_recommendation",
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
            "risk_proposal_owner": "risk_department",
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
        "corporate_risk_profile_hash": review["riskDepartment"]["profile_hash"],
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
        clipped = int(clamp(float(scaled_target), -individual_cap, individual_cap))
        if clipped != scaled_target:
            binding.append("strategy_individual_cap")
        approved[contract_id] = {
            **item,
            "strategy_intent_target_position_lots": strategy_intent,
            "strategy_risk_approved_target_position_lots": clipped,
            "target_position_lots": clipped,
            "strategy_risk_individual_cap_lots": individual_cap,
            "strategy_visible_annualized_volatility": annualized_volatility,
            "strategy_risk_binding_rules": binding,
        }

    def aggregate() -> tuple[int, float, float]:
        gross = 0
        margin = 0.0
        volatility_risk = 0.0
        for contract_id, item in approved.items():
            target = int(item["target_position_lots"])
            contract_item = contracts.get(contract_id)
            if contract_item is None:
                continue
            price = float(contract_item["price_usd"])
            notional = abs(target) * price * contract_size
            gross += abs(target)
            margin += notional * initial_margin_rate
            volatility_risk += notional * float(
                item["strategy_visible_annualized_volatility"]
            )
        return gross, margin, volatility_risk

    gross_before_scale, margin_before_scale, volatility_before_scale = aggregate()
    scales = [1.0]
    if gross_before_scale > strategy_gross_cap > 0:
        scales.append(strategy_gross_cap / gross_before_scale)
    if margin_before_scale > strategy_margin_budget > 0.0:
        scales.append(strategy_margin_budget / margin_before_scale)
    if volatility_before_scale > strategy_volatility_budget > 0.0:
        scales.append(strategy_volatility_budget / volatility_before_scale)
    portfolio_scale = min(scales)
    if portfolio_scale < 1.0:
        for item in approved.values():
            desired = int(round(int(item["target_position_lots"]) * portfolio_scale))
            current = int(positions.get(str(item["contract_id"]), 0))
            if drawdown["risk_status"] == "reduce_only":
                desired = _nonincreasing_target(current, desired)
            item["target_position_lots"] = desired
            if desired != item["strategy_risk_approved_target_position_lots"]:
                item["strategy_risk_binding_rules"].append(
                    "strategy_portfolio_scale"
                )
            item["strategy_risk_approved_target_position_lots"] = desired
    gross_after_scale, margin_after_scale, volatility_after_scale = aggregate()
    binding_rules: list[str] = []
    if gross_before_scale > strategy_gross_cap:
        binding_rules.append("strategy_gross_cap")
    if margin_before_scale > strategy_margin_budget:
        binding_rules.append("strategy_margin_budget")
    if volatility_before_scale > strategy_volatility_budget:
        binding_rules.append("strategy_volatility_budget")
    if drawdown["risk_status"] != "normal":
        binding_rules.append("strategy_drawdown_state")
    state = {
        "peak_strategy_equity_usd": drawdown["peak_strategy_equity_usd"],
        "strategy_drawdown_scale": drawdown["strategy_drawdown_scale"],
        "risk_status": drawdown["risk_status"],
    }
    report = {
        "schemaVersion": "asset-simulation-oil-strategy-risk-approval-v1",
        "committeeApproval": committee_approval,
        "strategyLimits": {
            "authorized_capital_usd": authorized_capital,
            "strategy_gross_position_cap_lots": strategy_gross_cap,
            "strategy_initial_margin_budget_usd": strategy_margin_budget,
            "strategy_annualized_volatility_budget_usd": strategy_volatility_budget,
            "max_single_contract_share_of_strategy_gross": max_share,
            "max_liquidation_half_turns": liquidation_turns,
            "roll_buffer_half_turns": roll_buffer,
        },
        "drawdownState": drawdown,
        "approvalSummary": {
            "strategy_intent_gross_lots": sum(
                abs(int(item["strategy_intent_target_position_lots"]))
                for item in approved.values()
            ),
            "strategy_approved_gross_lots": gross_after_scale,
            "strategy_estimated_initial_margin_usd": margin_after_scale,
            "strategy_estimated_annualized_dollar_risk_usd": (
                volatility_after_scale
            ),
            "strategy_portfolio_scale": portfolio_scale,
            "binding_rules": binding_rules,
        },
        "stateAfter": state,
        "governance": {
            "strategy_risk_owner": "risk_department",
            "committee_owns_capital_authorization": True,
            "company_cro_still_applies_after_strategy_risk": True,
            "strategy_risk_never_expands_strategy_intent": True,
            "market_rules_remain_hard": True,
        },
    }
    rounded_report = _round_nested(report)
    identity = {
        "model_version": OIL_STRATEGY_RISK_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_strategy_risk_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_strategy_risk_contract_hash"],
        "write_back": False,
        "result_hash": sha256_json(rounded_report),
    }
    return approved, _round_nested({"identity": identity, **rounded_report})
