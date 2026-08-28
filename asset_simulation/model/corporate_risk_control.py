"""Company-level risk appetite and pre-trade approval for the simulated oil book."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal
from .registry import load_registered_assets, sha256_json


CORPORATE_RISK_CONTROL_MODEL_VERSION = "asset-simulation-corporate-risk-control-v0.2.0"
CORPORATE_RISK_CONTROL_CONTRACT_ID = "corporate_risk_control_v2"
RISK_APPETITE_DIMENSIONS = (
    "capital_tolerance",
    "volatility_tolerance",
    "drawdown_tolerance",
    "concentration_tolerance",
    "liquidity_tolerance",
    "roll_risk_tolerance",
)
RISK_STATUS_ORDER = ("normal", "watch", "restricted", "reduce_only")


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("corporate risk control contains a non-finite value")
        return round(value, 8)
    return value


def _assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["corporate_risk_control_config"]
    contract = assets["corporate_risk_control_contract"]
    if config["model_version"] != CORPORATE_RISK_CONTROL_MODEL_VERSION:
        raise ValueError("registered corporate risk config version mismatch")
    if contract["contract_id"] != CORPORATE_RISK_CONTROL_CONTRACT_ID:
        raise ValueError("registered corporate risk contract id mismatch")
    if tuple(config["risk_appetite_dimensions"]) != RISK_APPETITE_DIMENSIONS:
        raise ValueError("corporate risk dimensions are out of contract order")
    return assets, config, contract


def _score(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"corporate risk {label} must be a continuous score")
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 100.0:
        raise ValueError(f"corporate risk {label} must be finite and between 0 and 100")
    return score


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


def _resolved_policy(radar: Mapping[str, float], config: Mapping[str, Any]) -> dict[str, Any]:
    mapping = config["parameter_mapping"]
    result = {
        "capital": {
            "max_gross_market_limit_utilization": _piecewise(
                mapping["max_gross_market_limit_utilization"], radar["capital_tolerance"]
            ),
            "max_initial_margin_pct_of_equity": _piecewise(
                mapping["max_initial_margin_pct_of_equity"], radar["capital_tolerance"]
            ),
        },
        "volatility": {
            "annualized_volatility_budget_pct_of_equity": _piecewise(
                mapping["annualized_volatility_budget_pct_of_equity"], radar["volatility_tolerance"]
            )
        },
        "drawdown": {
            "watch_pct": _piecewise(mapping["drawdown_watch_pct"], radar["drawdown_tolerance"]),
            "restrict_pct": _piecewise(mapping["drawdown_restrict_pct"], radar["drawdown_tolerance"]),
            "reduce_only_pct": _piecewise(mapping["drawdown_reduce_only_pct"], radar["drawdown_tolerance"]),
            **{key: float(value) for key, value in config["drawdown_scaling"].items()},
        },
        "concentration": {
            "max_single_contract_share_of_company_gross": _piecewise(
                mapping["max_single_contract_share_of_company_gross"], radar["concentration_tolerance"]
            )
        },
        "liquidity": {
            "max_liquidation_half_turns": _piecewise(
                mapping["max_liquidation_half_turns"], radar["liquidity_tolerance"]
            )
        },
        "roll": {
            "roll_buffer_half_turns": _piecewise(
                mapping["roll_buffer_half_turns"], radar["roll_risk_tolerance"]
            )
        },
    }
    return result


def _tags(radar: Mapping[str, float], config: Mapping[str, Any]) -> list[str]:
    labels = {
        "capital_tolerance": ("资本宽松", "资本审慎"),
        "volatility_tolerance": ("容忍波动", "波动敏感"),
        "drawdown_tolerance": ("耐受回撤", "快速降险"),
        "concentration_tolerance": ("允许集中", "强调分散"),
        "liquidity_tolerance": ("容忍慢退出", "流动性优先"),
        "roll_risk_tolerance": ("允许晚移仓", "提前控近月"),
    }
    high = float(config["tag_thresholds"]["high"])
    low = float(config["tag_thresholds"]["low"])
    ordered = sorted(RISK_APPETITE_DIMENSIONS, key=lambda key: abs(radar[key] - 50.0), reverse=True)
    result: list[str] = []
    for key in ordered:
        if radar[key] >= high:
            result.append(labels[key][0])
        elif radar[key] <= low:
            result.append(labels[key][1])
        if len(result) == 3:
            break
    return result or ["均衡风控"]


def _pack_profile(
    *, personnel_id: str, display_name: str, risk_appetite_radar: Mapping[str, Any],
    candidate_index: int | None, generation_seed: int | None, source: str,
) -> dict[str, Any]:
    assets, config, contract = _assets()
    if set(risk_appetite_radar) != set(RISK_APPETITE_DIMENSIONS):
        raise KeyError("corporate risk radar must exactly match registered dimensions")
    radar = {key: _score(risk_appetite_radar[key], key) for key in RISK_APPETITE_DIMENSIONS}
    result = {
        "schemaVersion": "asset-simulation-corporate-risk-profile-v2",
        "appointment": {
            "department": "corporate_risk", "role": config["appointment_role"],
            "personnel_id": str(personnel_id), "display_name": str(display_name),
            "source": str(source), "candidate_index": candidate_index,
            "generation_seed": generation_seed,
        },
        "risk_appetite_radar": radar,
        "risk_appetite_tags": _tags(radar, config),
        "risk_appetite_total_score": None,
        "resolved_policy": _resolved_policy(radar, config),
        "governance": {
            "scores_are_continuous": True, "neutral_baseline_score": 50.0,
            "higher_score_is_better": False, "player_can_edit_radar": False,
            "selection_method": "appoint_generated_cro",
            "scope": "company_level", "can_expand_strategy_intent": False,
            "can_override_market_hard_rules": False,
            "proposal_owner": config["policy_governance"]["proposal_owner"],
            "ratification_owner": config["policy_governance"]["ratification_owner"],
            "runtime_enforcement_owner": config["policy_governance"][
                "runtime_enforcement_owner"
            ],
        },
        "identity": {
            "model_version": CORPORATE_RISK_CONTROL_MODEL_VERSION,
            "config_id": config["config_id"],
            "config_hash": assets["corporate_risk_control_config_hash"],
            "field_contract_id": contract["contract_id"],
            "field_contract_hash": assets["corporate_risk_control_contract_hash"],
            "write_back": False,
        },
    }
    result["profile_hash"] = sha256_json(result)
    return _round_nested(result)


def build_default_corporate_risk_profile() -> dict[str, Any]:
    _, config, _ = _assets()
    item = config["default_officer"]
    return _pack_profile(
        personnel_id=item["personnel_id"], display_name=item["display_name"],
        risk_appetite_radar=item["risk_appetite_radar"], candidate_index=None,
        generation_seed=None, source="default_appointment",
    )


def _pool_index(seed: int, address: str, index: int, size: int) -> int:
    draw = normal(seed, address, index)
    uniform = 0.5 * (1.0 + math.erf(draw / math.sqrt(2.0)))
    return min(size - 1, int(uniform * size))


def generate_corporate_risk_candidate(*, seed: int, candidate_index: int) -> dict[str, Any]:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("corporate risk seed must be a non-negative integer")
    if isinstance(candidate_index, bool) or not isinstance(candidate_index, int) or candidate_index < 0:
        raise ValueError("corporate risk candidate index must be non-negative")
    _, config, _ = _assets()
    generation = config["candidate_generation"]
    traits = list(generation["latent_traits"])
    latents = {
        key: clamp(normal(seed, f"corporate_risk.candidate.{candidate_index}.latent.{key}", candidate_index), -1.8, 1.8)
        for key in traits
    }
    radar: dict[str, float] = {}
    for dimension_index, dimension in enumerate(RISK_APPETITE_DIMENSIONS):
        loading = generation["latent_loadings"][dimension]
        value = float(generation["dimension_center"]) + sum(float(loading[key]) * latents[key] for key in traits)
        value += float(generation["idiosyncratic_scale"]) * normal(
            seed, f"corporate_risk.candidate.{candidate_index}.dimension.{dimension}", dimension_index
        )
        radar[dimension] = round(clamp(value, float(generation["dimension_floor"]), float(generation["dimension_ceiling"])), 2)
    families = list(generation["family_names"])
    given_names = list(generation["given_names"])
    family = families[_pool_index(seed, f"corporate_risk.candidate.{candidate_index}.family", candidate_index, len(families))]
    given = given_names[_pool_index(seed, f"corporate_risk.candidate.{candidate_index}.given", candidate_index, len(given_names))]
    return _pack_profile(
        personnel_id=f"corporate_cro_{seed}_{candidate_index}", display_name=f"{family}{given}",
        risk_appetite_radar=radar, candidate_index=candidate_index,
        generation_seed=seed, source="generated_candidate",
    )


def generate_corporate_risk_roster(*, seed: int, candidate_count: int | None = None) -> dict[str, Any]:
    assets, config, contract = _assets()
    generation = config["candidate_generation"]
    count = int(generation["default_candidate_count"]) if candidate_count is None else candidate_count
    if isinstance(count, bool) or not isinstance(count, int) or not int(generation["minimum_candidate_count"]) <= count <= int(generation["maximum_candidate_count"]):
        raise ValueError("corporate risk candidate count is outside its bounds")
    candidates = [generate_corporate_risk_candidate(seed=seed, candidate_index=index) for index in range(count)]
    result = {
        "ok": True, "schemaVersion": "asset-simulation-corporate-risk-roster-v2",
        "seed": seed, "candidateCount": count, "appointmentRole": config["appointment_role"],
        "selectionPolicy": {
            "player_can_edit_radar": False, "scores_are_continuous": True,
            "risk_appetite_total_score_available": False,
            "method": "appoint_one_generated_cro",
        },
        "candidates": candidates,
    }
    identity = {
        "schema_version": "asset-simulation-corporate-risk-roster-identity-v2",
        "model_version": CORPORATE_RISK_CONTROL_MODEL_VERSION,
        "config_id": config["config_id"], "config_hash": assets["corporate_risk_control_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["corporate_risk_control_contract_hash"],
        "write_back": False, "result_hash": sha256_json(result),
    }
    return _round_nested({"identity": identity, **result})


def resolve_corporate_risk_profile(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    if profile is None:
        return build_default_corporate_risk_profile()
    supplied = dict(profile)
    appointment = dict(supplied.get("appointment", {}))
    rebuilt = _pack_profile(
        personnel_id=str(appointment.get("personnel_id", "")),
        display_name=str(appointment.get("display_name", "")),
        risk_appetite_radar=supplied.get("risk_appetite_radar", {}),
        candidate_index=appointment.get("candidate_index"),
        generation_seed=appointment.get("generation_seed"),
        source=str(appointment.get("source", "appointed_profile")),
    )
    if supplied.get("profile_hash") is not None and str(supplied["profile_hash"]) != rebuilt["profile_hash"]:
        raise ValueError("corporate risk profile was modified after generation")
    return rebuilt


def _visible_annualized_volatility(contract: Mapping[str, Any], config: Mapping[str, Any]) -> float:
    weeks: list[Mapping[str, Any]] = []
    for month in contract.get("monthly", ()):
        weeks.extend(month.get("weekly", ()))
    lookback = int(config["visible_volatility"]["lookback_weeks"])
    closes = [float(week["close"]) for week in weeks[-(lookback + 1):] if float(week["close"]) > 0.0]
    returns = [math.log(after / before) for before, after in zip(closes, closes[1:])]
    if not returns:
        realized = float(config["visible_volatility"]["minimum_annualized_volatility"])
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
    *, equity_usd: float, risk_state: Mapping[str, Any] | None, policy: Mapping[str, Any]
) -> dict[str, Any]:
    state = dict(risk_state or {})
    peak = max(float(equity_usd), float(state.get("peak_equity_usd", equity_usd)))
    drawdown_pct = 100.0 * max(0.0, 1.0 - float(equity_usd) / peak)
    drawdown = policy["drawdown"]
    watch = float(drawdown["watch_pct"])
    restrict = float(drawdown["restrict_pct"])
    reduce_only = float(drawdown["reduce_only_pct"])
    if not 0.0 < watch < restrict < reduce_only:
        raise ValueError("corporate risk drawdown thresholds must be increasing")
    if drawdown_pct <= watch:
        raw_scale = 1.0
        status = "normal"
    elif drawdown_pct <= restrict:
        mix = (drawdown_pct - watch) / (restrict - watch)
        raw_scale = 1.0 + mix * (float(drawdown["watch_scale"]) - 1.0)
        status = "watch"
    elif drawdown_pct <= reduce_only:
        mix = (drawdown_pct - restrict) / (reduce_only - restrict)
        raw_scale = float(drawdown["watch_scale"]) + mix * (
            float(drawdown["restrict_scale"]) - float(drawdown["watch_scale"])
        )
        status = "restricted"
    else:
        raw_scale = float(drawdown["reduce_only_scale"])
        status = "reduce_only"
    previous_scale = state.get("drawdown_scale")
    if previous_scale is None:
        effective_scale = raw_scale
    else:
        previous = clamp(float(previous_scale), 0.0, 1.0)
        recovery_cap = previous + float(drawdown["maximum_scale_recovery_per_turn"])
        effective_scale = min(raw_scale, recovery_cap) if raw_scale > previous else raw_scale
    return {
        "peak_equity_usd": peak,
        "current_equity_usd": float(equity_usd),
        "drawdown_pct": drawdown_pct,
        "raw_drawdown_scale": raw_scale,
        "drawdown_scale": effective_scale,
        "risk_status": status,
        "previous_drawdown_scale": previous_scale,
        "scale_recovery_capped": previous_scale is not None and raw_scale > effective_scale,
    }


def _nonincreasing_target(current_position: int, desired_target: int) -> int:
    if current_position == 0 or desired_target == 0 or current_position * desired_target <= 0:
        return 0
    return int(math.copysign(min(abs(current_position), abs(desired_target)), current_position))


def approve_oil_strategy_targets(
    market: Mapping[str, Any],
    targets: Mapping[str, Mapping[str, Any]],
    *,
    positions: Mapping[str, int],
    equity_usd: float,
    risk_profile: Mapping[str, Any] | None = None,
    risk_state: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Enforce the ratified company envelope after strategy-risk approval."""

    assets, config, contract = _assets()
    profile = resolve_corporate_risk_profile(risk_profile)
    policy = profile["resolved_policy"]
    contracts = {str(item["contract_id"]): item for item in market["curve"]["contracts"]}
    specification = market["contractSpecification"]
    contract_size = float(specification["contract_size_bbl"])
    initial_margin_rate = float(specification["initial_margin_rate_pct"]) / 100.0
    company_market_cap = int(
        market["participantLimitsPolicy"]["all_contract_gross_position_cap_lots"]
    )
    company_gross_cap = math.floor(
        company_market_cap * float(policy["capital"]["max_gross_market_limit_utilization"])
    )
    company_margin_budget = float(equity_usd) * float(
        policy["capital"]["max_initial_margin_pct_of_equity"]
    ) / 100.0
    company_volatility_budget = float(equity_usd) * float(
        policy["volatility"]["annualized_volatility_budget_pct_of_equity"]
    ) / 100.0
    drawdown = _drawdown_report(equity_usd=equity_usd, risk_state=risk_state, policy=policy)
    max_share = float(policy["concentration"]["max_single_contract_share_of_company_gross"])
    liquidation_turns = float(policy["liquidity"]["max_liquidation_half_turns"])
    roll_buffer = float(policy["roll"]["roll_buffer_half_turns"])

    approved: dict[str, dict[str, Any]] = {}
    for contract_id, raw_item in targets.items():
        item = dict(raw_item)
        strategy_target = int(item["target_position_lots"])
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
            concentration_cap = math.floor(company_gross_cap * max_share)
            liquidity_cap = math.floor(int(limits["turn_trade_limit_lots"]) * liquidation_turns)
            individual_cap = min(market_single_cap, concentration_cap, liquidity_cap)
            annualized_volatility = _visible_annualized_volatility(contract_item, config)
            if individual_cap == concentration_cap:
                binding.append("company_concentration")
            if individual_cap == liquidity_cap:
                binding.append("company_liquidity_exit")
            if not bool(limits["new_trades_allowed"]):
                individual_cap = min(individual_cap, abs(current_position))
                binding.append("market_new_trades_closed")
            half_turns_to_expiry = float(contract_item["half_turns_to_expiry"])
            if roll_buffer > 0.0 and half_turns_to_expiry < roll_buffer:
                roll_cap = math.floor(market_single_cap * half_turns_to_expiry / roll_buffer)
                if roll_cap < individual_cap:
                    individual_cap = roll_cap
                    binding.append("company_roll_buffer")
        scaled_target = int(round(strategy_target * float(drawdown["drawdown_scale"])))
        if drawdown["risk_status"] == "reduce_only":
            scaled_target = _nonincreasing_target(current_position, scaled_target)
            binding.append("company_drawdown_reduce_only")
        bounded_target = int(clamp(float(scaled_target), -float(individual_cap), float(individual_cap)))
        if abs(bounded_target) > abs(strategy_target) or bounded_target * strategy_target < 0:
            raise ValueError("corporate risk approval expanded or reversed strategy intent")
        if bounded_target != strategy_target and not binding:
            binding.append("company_drawdown_scale")
        price = 0.0 if contract_item is None else float(contract_item["price_usd"])
        item.update({
            "company_risk_input_target_lots": strategy_target,
            "company_risk_approved_target_position_lots": bounded_target,
            "company_risk_clip_lots": strategy_target - bounded_target,
            "strategy_target_position_lots": strategy_target,
            "risk_approved_target_position_lots": bounded_target,
            "target_position_lots": bounded_target,
            "risk_clip_lots": strategy_target - bounded_target,
            "risk_binding_rules": sorted(set(binding)),
            "visible_annualized_volatility": annualized_volatility,
            "estimated_annualized_risk_usd": abs(bounded_target) * price * contract_size * annualized_volatility,
            "estimated_initial_margin_usd": abs(bounded_target) * price * contract_size * initial_margin_rate,
        })
        approved[contract_id] = item

    gross_before_portfolio = sum(abs(int(item["target_position_lots"])) for item in approved.values())
    margin_before_portfolio = sum(float(item["estimated_initial_margin_usd"]) for item in approved.values())
    volatility_before_portfolio = sum(float(item["estimated_annualized_risk_usd"]) for item in approved.values())
    portfolio_scale = min(
        1.0,
        company_gross_cap / max(1, gross_before_portfolio),
        company_margin_budget / max(1e-9, margin_before_portfolio),
        company_volatility_budget / max(1e-9, volatility_before_portfolio),
    )
    portfolio_binding: list[str] = []
    if portfolio_scale < 1.0:
        if math.isclose(portfolio_scale, company_gross_cap / max(1, gross_before_portfolio), rel_tol=1e-9):
            portfolio_binding.append("company_gross_cap")
        if math.isclose(portfolio_scale, company_margin_budget / max(1e-9, margin_before_portfolio), rel_tol=1e-9):
            portfolio_binding.append("company_margin_budget")
        if math.isclose(portfolio_scale, company_volatility_budget / max(1e-9, volatility_before_portfolio), rel_tol=1e-9):
            portfolio_binding.append("company_volatility_budget")
        for item in approved.values():
            strategy_target = int(item["strategy_target_position_lots"])
            prior = int(item["target_position_lots"])
            adjusted = int(math.copysign(math.floor(abs(prior) * portfolio_scale), prior)) if prior else 0
            item["target_position_lots"] = adjusted
            item["company_risk_approved_target_position_lots"] = adjusted
            item["company_risk_clip_lots"] = strategy_target - adjusted
            item["risk_approved_target_position_lots"] = adjusted
            item["risk_clip_lots"] = strategy_target - adjusted
            item["risk_binding_rules"] = sorted(set(item["risk_binding_rules"] + portfolio_binding))
            contract_item = contracts.get(str(item["contract_id"]))
            price = 0.0 if contract_item is None else float(contract_item["price_usd"])
            item["estimated_annualized_risk_usd"] = abs(adjusted) * price * contract_size * float(item["visible_annualized_volatility"])
            item["estimated_initial_margin_usd"] = abs(adjusted) * price * contract_size * initial_margin_rate

    gross_after = sum(abs(int(item["target_position_lots"])) for item in approved.values())
    margin_after = sum(float(item["estimated_initial_margin_usd"]) for item in approved.values())
    volatility_after = sum(float(item["estimated_annualized_risk_usd"]) for item in approved.values())
    result = {
        "schemaVersion": "asset-simulation-corporate-risk-approval-v2",
        "profile": {
            "appointment": profile["appointment"],
            "risk_appetite_radar": profile["risk_appetite_radar"],
            "risk_appetite_tags": profile["risk_appetite_tags"],
            "risk_appetite_total_score": None,
            "profile_hash": profile["profile_hash"],
            "governance": profile["governance"],
        },
        "resolved_policy": policy,
        "policyGovernance": {
            **config["policy_governance"],
            "ratification_status": config["policy_governance"][
                "default_ratification_status"
            ],
            "scope": "company_aggregate_after_strategy_mandates",
        },
        "state": drawdown,
        "company_limits": {
            "market_hard_gross_cap_lots": company_market_cap,
            "company_gross_cap_lots": company_gross_cap,
            "company_initial_margin_budget_usd": company_margin_budget,
            "company_annualized_volatility_budget_usd": company_volatility_budget,
            "max_single_contract_share": max_share,
            "max_liquidation_half_turns": liquidation_turns,
            "roll_buffer_half_turns": roll_buffer,
        },
        "approval_summary": {
            "strategy_target_gross_lots": sum(abs(int(item["strategy_target_position_lots"])) for item in approved.values()),
            "pre_portfolio_approved_gross_lots": gross_before_portfolio,
            "approved_gross_lots": gross_after,
            "clipped_gross_lots": sum(abs(int(item["strategy_target_position_lots"])) - abs(int(item["target_position_lots"])) for item in approved.values()),
            "portfolio_scale": portfolio_scale,
            "portfolio_binding_rules": portfolio_binding,
            "approved_initial_margin_usd": margin_after,
            "approved_annualized_risk_usd": volatility_after,
            "approved_annualized_risk_pct_of_equity": 100.0 * volatility_after / float(equity_usd),
        },
        "information_policy": {
            "future_market_available": False,
            "visible_history_only": True,
            "can_expand_strategy_intent": False,
            "can_override_market_hard_rules": False,
        },
        "identity": {
            "model_version": CORPORATE_RISK_CONTROL_MODEL_VERSION,
            "config_id": config["config_id"],
            "config_hash": assets["corporate_risk_control_config_hash"],
            "field_contract_id": contract["contract_id"],
            "field_contract_hash": assets["corporate_risk_control_contract_hash"],
            "profile_hash": profile["profile_hash"],
            "write_back": False,
        },
    }
    result["identity"]["result_hash"] = sha256_json({key: value for key, value in result.items() if key != "identity"})
    return _round_nested(approved), _round_nested(result)
