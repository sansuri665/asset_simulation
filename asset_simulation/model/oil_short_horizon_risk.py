"""Oil / Short-Horizon risk coverage over actual committee position mandates.

Risk personnel are scoped by asset and horizon, not by strategy id. The same
team can review directional and calendar-spread mandates. Capital allocation is
an input from Investment Decision and is never recommended by this module.

The risk budget is measured over the configured short review horizon. Visible
annualized directional volatility is converted to that horizon by square-root
of time; calendar-spread weekly change volatility is converted directly to the
same horizon. Tail/model multipliers are applied exactly once.
"""

from __future__ import annotations

import math
from statistics import pstdev
from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal
from .registry import load_registered_assets, sha256_json


OIL_SHORT_HORIZON_RISK_MODEL_VERSION = "asset-simulation-oil-short-horizon-risk-v0.1.1"
OIL_SHORT_HORIZON_RISK_CONTRACT_ID = "oil_short_horizon_risk_v1"


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("oil short-horizon risk contains a non-finite value")
        return round(value, 8)
    return value


def _assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_short_horizon_risk_config"]
    contract = assets["oil_short_horizon_risk_contract"]
    if config["model_version"] != OIL_SHORT_HORIZON_RISK_MODEL_VERSION:
        raise ValueError("registered oil short-horizon risk config version mismatch")
    if contract["contract_id"] != OIL_SHORT_HORIZON_RISK_CONTRACT_ID:
        raise ValueError("registered oil short-horizon risk contract id mismatch")
    horizon = dict(config.get("risk_horizon", {}))
    review_weeks = float(horizon.get("review_horizon_weeks", 0.0))
    annualization_weeks = float(horizon.get("annualization_weeks", 0.0))
    if not 0.0 < review_weeks <= annualization_weeks:
        raise ValueError("oil short-horizon risk review horizon is invalid")
    return assets, config, contract


def _score(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 100.0:
        raise ValueError(f"{label} must be between 0 and 100")
    return result


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


def _capability_error(anchor: Mapping[str, Any], score: float) -> float:
    value = clamp(float(score), 35.0, 70.0)
    mix = (value - 35.0) / 35.0
    return float(anchor["score_35"]) + mix * (
        float(anchor["score_70"]) - float(anchor["score_35"])
    )


def _signed_error(*parts: Any) -> float:
    digest = sha256_json([str(part) for part in parts])
    raw = int(digest[:12], 16) / float(16**12 - 1)
    return 2.0 * raw - 1.0


def _pack_profile(
    *,
    personnel_id: str,
    display_name: str,
    style_radar: Mapping[str, Any],
    capability_radar: Mapping[str, Any],
    candidate_index: int | None,
    generation_seed: int | None,
    source: str,
) -> dict[str, Any]:
    assets, config, contract = _assets()
    style_dimensions = tuple(config["style_dimensions"])
    capability_dimensions = tuple(config["capability_dimensions"])
    if set(style_radar) != set(style_dimensions):
        raise KeyError("risk style radar must exactly match registered dimensions")
    if set(capability_radar) != set(capability_dimensions):
        raise KeyError("risk capability radar must exactly match registered dimensions")
    styles = {key: _score(style_radar[key], key) for key in style_dimensions}
    capabilities = {
        key: _score(capability_radar[key], key) for key in capability_dimensions
    }
    result = {
        "schemaVersion": "asset-simulation-oil-short-horizon-risk-profile-v1",
        "appointment": {
            "department": "corporate_risk",
            "division": "oil_risk",
            "group": "short_horizon",
            "role": config["appointment_role"],
            "personnel_id": str(personnel_id),
            "display_name": str(display_name),
            "source": str(source),
            "candidate_index": candidate_index,
            "generation_seed": generation_seed,
        },
        "coverage_scope": dict(config["coverage_scope"]),
        "supported_strategy_types": list(config["supported_strategy_types"]),
        "style_radar": styles,
        "capability_radar": capabilities,
        "style_total_score": None,
        "capability_total_score": None,
        "governance": {
            "style_has_universal_ordering": False,
            "capability_is_lightweight": True,
            "player_can_edit_radars": False,
            "capital_allocation_owner": "investment_decision_committee",
            "can_expand_position_mandate": False,
            "can_override_market_hard_rules": False,
            "hidden_future_access": False,
        },
        "identity": {
            "model_version": OIL_SHORT_HORIZON_RISK_MODEL_VERSION,
            "config_id": config["config_id"],
            "config_hash": assets["oil_short_horizon_risk_config_hash"],
            "field_contract_id": contract["contract_id"],
            "field_contract_hash": assets["oil_short_horizon_risk_contract_hash"],
            "write_back": False,
        },
    }
    result["profile_hash"] = sha256_json(result)
    return _round_nested(result)


def build_default_oil_short_horizon_risk_profile() -> dict[str, Any]:
    _, config, _ = _assets()
    item = config["default_officer"]
    return _pack_profile(
        personnel_id=item["personnel_id"],
        display_name=item["display_name"],
        style_radar=item["style_radar"],
        capability_radar=item["capability_radar"],
        candidate_index=None,
        generation_seed=None,
        source="default_appointment",
    )


def generate_oil_short_horizon_risk_candidate(
    *, seed: int, candidate_index: int
) -> dict[str, Any]:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("risk seed must be a non-negative integer")
    if (
        isinstance(candidate_index, bool)
        or not isinstance(candidate_index, int)
        or candidate_index < 0
    ):
        raise ValueError("risk candidate index must be non-negative")
    _, config, _ = _assets()
    generation = config["candidate_generation"]
    style_latents = {
        key: clamp(
            normal(
                seed,
                f"oil_short_risk.{candidate_index}.style.{key}",
                candidate_index,
            ),
            -1.8,
            1.8,
        )
        for key in generation["style_latent_traits"]
    }
    capability_latents = {
        key: clamp(
            normal(
                seed,
                f"oil_short_risk.{candidate_index}.capability.{key}",
                candidate_index,
            ),
            -1.5,
            1.5,
        )
        for key in generation["capability_latent_traits"]
    }
    styles: dict[str, float] = {}
    for index, dimension in enumerate(config["style_dimensions"]):
        loading = generation["style_latent_loadings"][dimension]
        value = float(generation["style_center"]) + sum(
            float(loading[key]) * style_latents[key] for key in style_latents
        )
        value += float(generation["style_idiosyncratic_scale"]) * normal(
            seed,
            f"oil_short_risk.{candidate_index}.style_dimension.{dimension}",
            index,
        )
        styles[dimension] = round(
            clamp(
                value,
                float(generation["style_floor"]),
                float(generation["style_ceiling"]),
            ),
            2,
        )
    capabilities: dict[str, float] = {}
    for index, dimension in enumerate(config["capability_dimensions"]):
        loading = generation["capability_latent_loadings"][dimension]
        value = float(generation["capability_center"]) + sum(
            float(loading[key]) * capability_latents[key]
            for key in capability_latents
        )
        value += float(generation["capability_idiosyncratic_scale"]) * normal(
            seed,
            f"oil_short_risk.{candidate_index}.capability_dimension.{dimension}",
            index,
        )
        capabilities[dimension] = round(
            clamp(
                value,
                float(generation["capability_floor"]),
                float(generation["capability_ceiling"]),
            ),
            2,
        )
    families = list(generation["family_names"])
    names = list(generation["given_names"])
    family_index = int(
        abs(normal(seed, f"oil_short_risk.{candidate_index}.family", candidate_index))
        * 1000
    ) % len(families)
    name_index = int(
        abs(normal(seed, f"oil_short_risk.{candidate_index}.name", candidate_index))
        * 1000
    ) % len(names)
    return _pack_profile(
        personnel_id=f"oil_short_horizon_risk_{seed}_{candidate_index}",
        display_name=f"{families[family_index]}{names[name_index]}",
        style_radar=styles,
        capability_radar=capabilities,
        candidate_index=candidate_index,
        generation_seed=seed,
        source="generated_candidate",
    )


def generate_oil_short_horizon_risk_roster(
    *, seed: int, candidate_count: int | None = None
) -> dict[str, Any]:
    assets, config, contract = _assets()
    generation = config["candidate_generation"]
    count = (
        int(generation["default_candidate_count"])
        if candidate_count is None
        else candidate_count
    )
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not int(generation["minimum_candidate_count"])
        <= count
        <= int(generation["maximum_candidate_count"])
    ):
        raise ValueError("risk candidate count is outside bounds")
    candidates = [
        generate_oil_short_horizon_risk_candidate(
            seed=seed, candidate_index=index
        )
        for index in range(count)
    ]
    result = {
        "schemaVersion": "asset-simulation-oil-short-horizon-risk-roster-v1",
        "seed": seed,
        "candidateCount": count,
        "coverage_scope": dict(config["coverage_scope"]),
        "candidates": candidates,
    }
    identity = {
        "model_version": OIL_SHORT_HORIZON_RISK_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_short_horizon_risk_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_short_horizon_risk_contract_hash"],
        "write_back": False,
        "result_hash": sha256_json(result),
    }
    return _round_nested({"identity": identity, **result})


def resolve_oil_short_horizon_risk_profile(
    profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if profile is None:
        return build_default_oil_short_horizon_risk_profile()
    supplied = dict(profile)
    appointment = dict(supplied.get("appointment", {}))
    rebuilt = _pack_profile(
        personnel_id=str(appointment.get("personnel_id", "")),
        display_name=str(appointment.get("display_name", "")),
        style_radar=supplied.get("style_radar", {}),
        capability_radar=supplied.get("capability_radar", {}),
        candidate_index=appointment.get("candidate_index"),
        generation_seed=appointment.get("generation_seed"),
        source=str(appointment.get("source", "appointed_profile")),
    )
    if (
        supplied.get("profile_hash") is not None
        and supplied["profile_hash"] != rebuilt["profile_hash"]
    ):
        raise ValueError("risk profile was modified after generation")
    return rebuilt


def _visible_weeks(contract: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        week
        for month in contract.get("monthly", ())
        for week in month.get("weekly", ())
    ]


def _visible_annualized_volatility(
    contract: Mapping[str, Any], config: Mapping[str, Any]
) -> float:
    lookback = int(config["visible_volatility"]["lookback_weeks"])
    closes = [
        float(item["close"])
        for item in _visible_weeks(contract)[-(lookback + 1) :]
        if float(item["close"]) > 0.0
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


def _spread_change_volatility(
    first: Mapping[str, Any], second: Mapping[str, Any], config: Mapping[str, Any]
) -> float:
    lookback = int(config["calendar_spread"]["spread_change_lookback_weeks"])
    left = {
        int(item["week_serial"]): float(item["close"])
        for item in _visible_weeks(first)
    }
    right = {
        int(item["week_serial"]): float(item["close"])
        for item in _visible_weeks(second)
    }
    common = sorted(set(left) & set(right))[-(lookback + 1) :]
    spreads = [left[key] - right[key] for key in common]
    changes = [after - before for before, after in zip(spreads, spreads[1:])]
    return 0.0 if len(changes) < 2 else pstdev(changes)


def _company_policy(
    risk_appetite: Mapping[str, Any],
    profile: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, float]:
    if risk_appetite.get("identity", {}).get("object_type") != "company_risk_appetite":
        raise ValueError("risk review requires committee-approved company risk appetite")
    radar = {
        key: float(value)
        for key, value in risk_appetite["risk_appetite_radar"].items()
    }
    mapping = config["risk_appetite_parameter_mapping"]
    style = profile["style_radar"]
    style_mapping = config["style_parameter_mapping"]
    intervention = min(
        1.0,
        _piecewise(
            style_mapping["intervention_limit_multiplier"],
            style["intervention_earliness"],
        ),
    )
    monitoring_error = _capability_error(
        config["capability_error_mapping"]["monitoring_abs_error"],
        profile["capability_radar"]["monitoring_discipline"],
    )
    monitoring_noise = (
        _signed_error(profile["profile_hash"], "monitoring_limit") * monitoring_error
    )
    intervention = min(1.0, max(0.5, intervention * (1.0 + monitoring_noise)))
    return {
        "max_strategy_stress_loss_pct_of_allocated_capital": _piecewise(
            mapping["max_strategy_stress_loss_pct_of_allocated_capital"],
            radar["strategy_stress_loss_tolerance"],
        )
        * intervention,
        "max_company_stress_loss_pct_of_equity_per_strategy": _piecewise(
            mapping["max_company_stress_loss_pct_of_equity_per_strategy"],
            radar["company_materiality_tolerance"],
        )
        * intervention,
        "max_margin_pct_of_allocated_capital": _piecewise(
            mapping["max_margin_pct_of_allocated_capital"],
            radar["margin_tolerance"],
        )
        * intervention,
        "max_company_margin_pct_of_equity_per_strategy": _piecewise(
            mapping["max_company_margin_pct_of_equity_per_strategy"],
            radar["margin_tolerance"],
        )
        * intervention,
        "max_single_contract_share_of_market_gross": _piecewise(
            mapping["max_single_contract_share_of_market_gross"],
            radar["concentration_tolerance"],
        )
        * min(
            1.0,
            _piecewise(
                style_mapping["concentration_limit_multiplier"],
                style["concentration_aversion"],
            ),
        ),
        "max_liquidation_half_turns": _piecewise(
            mapping["max_liquidation_half_turns"], radar["liquidity_tolerance"]
        )
        * min(
            1.0,
            _piecewise(
                style_mapping["liquidity_limit_multiplier"],
                style["liquidity_priority"],
            ),
        ),
        "roll_buffer_half_turns": _piecewise(
            mapping["roll_buffer_half_turns"], radar["roll_tolerance"]
        )
        + 2.0 * float(style["intervention_earliness"]) / 100.0,
        "tail_stress_multiplier": _piecewise(
            style_mapping["tail_stress_multiplier"], style["tail_risk_focus"]
        ),
        "model_uncertainty_multiplier": _piecewise(
            style_mapping["model_uncertainty_multiplier"],
            style["model_skepticism"],
        ),
    }


def _risk_estimates(
    *,
    market: Mapping[str, Any],
    targets: Mapping[str, int],
    strategy_type: str,
    profile: Mapping[str, Any],
    policy: Mapping[str, float],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    contracts = {
        str(item["contract_id"]): item for item in market["curve"]["contracts"]
    }
    specification = market["contractSpecification"]
    contract_size = float(specification["contract_size_bbl"])
    initial_margin_rate = float(specification["initial_margin_rate_pct"]) / 100.0
    review_weeks = float(config["risk_horizon"]["review_horizon_weeks"])
    annualization_weeks = float(config["risk_horizon"]["annualization_weeks"])
    horizon_scale = math.sqrt(review_weeks / annualization_weeks)
    measurement_amp = _capability_error(
        config["capability_error_mapping"]["risk_measurement_abs_error"],
        profile["capability_radar"]["risk_measurement"],
    )
    stress_amp = _capability_error(
        config["capability_error_mapping"]["stress_analysis_abs_error"],
        profile["capability_radar"]["stress_analysis"],
    )
    per_contract: dict[str, Any] = {}
    margin = 0.0
    directional_horizon_sigma = 0.0
    as_of = market.get("asOf", {})
    for contract_id, target in targets.items():
        contract = contracts.get(contract_id)
        if contract is None:
            per_contract[contract_id] = {
                "target_lots": target,
                "contract_available": False,
            }
            continue
        price = float(contract["price_usd"])
        observed_vol = _visible_annualized_volatility(contract, config)
        measurement_error = (
            _signed_error(profile["profile_hash"], contract_id, as_of, "vol")
            * measurement_amp
        )
        estimated_annualized_vol = max(
            0.0, observed_vol * (1.0 + measurement_error)
        )
        estimated_horizon_vol = estimated_annualized_vol * horizon_scale
        contract_margin = (
            abs(target) * price * contract_size * initial_margin_rate
        )
        annualized_proxy = (
            abs(target) * price * contract_size * estimated_annualized_vol
        )
        horizon_proxy = abs(target) * price * contract_size * estimated_horizon_vol
        margin += contract_margin
        directional_horizon_sigma += horizon_proxy
        per_contract[contract_id] = {
            "target_lots": target,
            "contract_available": True,
            "price_usd": price,
            "visible_annualized_volatility": observed_vol,
            "estimated_annualized_volatility": estimated_annualized_vol,
            "estimated_review_horizon_volatility": estimated_horizon_vol,
            "risk_horizon_weeks": review_weeks,
            "measurement_error_fraction": measurement_error,
            "initial_margin_usd": contract_margin,
            "directional_annualized_one_sigma_proxy_usd": annualized_proxy,
            "directional_horizon_one_sigma_proxy_usd": horizon_proxy,
            "directional_one_sigma_proxy_usd": horizon_proxy,
        }

    tail_multiplier = float(policy["tail_stress_multiplier"])
    model_multiplier = float(policy["model_uncertainty_multiplier"])
    base_horizon_risk = directional_horizon_sigma
    spread_report: dict[str, Any] | None = None
    stress_multiplier_already_applied = False
    active = [
        (key, value)
        for key, value in targets.items()
        if value != 0 and key in contracts
    ]
    if strategy_type == "calendar_spread" and len(active) == 2:
        (first_id, first_target), (second_id, second_target) = active
        if (
            first_target == -second_target
            and abs(first_target) == abs(second_target)
        ):
            spread_vol = _spread_change_volatility(
                contracts[first_id], contracts[second_id], config
            )
            horizon_spread_sigma = spread_vol * math.sqrt(review_weeks)
            stressed_move = max(
                float(
                    config["calendar_spread"][
                        "minimum_stressed_spread_move_usd_per_bbl"
                    ]
                ),
                horizon_spread_sigma * tail_multiplier * model_multiplier,
            )
            base_horizon_risk = (
                abs(first_target) * contract_size * stressed_move
            )
            stress_multiplier_already_applied = True
            spread_report = {
                "balanced_pair": True,
                "spread_units": abs(first_target),
                "risk_horizon_weeks": review_weeks,
                "visible_weekly_spread_change_volatility_usd_per_bbl": spread_vol,
                "review_horizon_spread_sigma_usd_per_bbl": horizon_spread_sigma,
                "stressed_spread_move_usd_per_bbl": stressed_move,
                "tail_model_multiplier_applied_once": True,
            }

    stress_error = (
        _signed_error(profile["profile_hash"], strategy_type, as_of, "stress")
        * stress_amp
    )
    if stress_multiplier_already_applied:
        stressed_before_error = base_horizon_risk
    else:
        stressed_before_error = (
            base_horizon_risk * tail_multiplier * model_multiplier
        )
    estimated_stress = max(
        0.0, stressed_before_error * (1.0 + stress_error)
    )
    return {
        "risk_horizon_weeks": review_weeks,
        "risk_horizon_basis": "short_horizon_review_window",
        "per_contract": per_contract,
        "initial_margin_usd": margin,
        "base_risk_proxy_usd": base_horizon_risk,
        "estimated_stress_loss_usd": estimated_stress,
        "stress_analysis_error_fraction": stress_error,
        "tail_stress_multiplier": tail_multiplier,
        "model_uncertainty_multiplier": model_multiplier,
        "tail_model_multiplier_application_count": 1,
        "calendar_spread": spread_report,
    }


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
    """Review actual committee expected positions and return preserve/reduce targets."""

    assets, config, contract = _assets()
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
    if strategy_type not in set(config["supported_strategy_types"]):
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
    expected = {
        str(key): int(value)
        for key, value in committee_position_mandate[
            "committee_expected_targets"
        ].items()
    }
    if any(
        isinstance(value, bool)
        for value in committee_position_mandate[
            "committee_expected_targets"
        ].values()
    ):
        raise ValueError("committee expected targets must be integer lots")
    positions = {
        str(key): int(value)
        for key, value in dict(current_positions or {}).items()
    }
    profile = resolve_oil_short_horizon_risk_profile(risk_profile)
    policy = _company_policy(company_risk_appetite, profile, config)
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
                roll_cap = math.floor(
                    market_cap * half_turns / roll_buffer
                )
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

    pre_estimates = _risk_estimates(
        market=market,
        targets=preliminary,
        strategy_type=strategy_type,
        profile=profile,
        policy=policy,
        config=config,
    )
    stress = float(pre_estimates["estimated_stress_loss_usd"])
    margin = float(pre_estimates["initial_margin_usd"])
    stress_strategy_limit = (
        allocated
        * policy["max_strategy_stress_loss_pct_of_allocated_capital"]
        / 100.0
    )
    stress_company_limit = (
        equity
        * policy["max_company_stress_loss_pct_of_equity_per_strategy"]
        / 100.0
    )
    margin_strategy_limit = (
        allocated * policy["max_margin_pct_of_allocated_capital"] / 100.0
    )
    margin_company_limit = (
        equity
        * policy["max_company_margin_pct_of_equity_per_strategy"]
        / 100.0
    )
    scale_candidates = {
        "strategy_stress": (
            1.0 if stress <= 1e-12 else stress_strategy_limit / stress
        ),
        "company_materiality": (
            1.0 if stress <= 1e-12 else stress_company_limit / stress
        ),
        "strategy_margin": (
            1.0 if margin <= 1e-12 else margin_strategy_limit / margin
        ),
        "company_margin_materiality": (
            1.0 if margin <= 1e-12 else margin_company_limit / margin
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
        if (
            abs(approved) > abs(expected_target)
            or approved * expected_target < 0
        ):
            raise ValueError(
                "risk review expanded or reversed committee position mandate"
            )
        approved_targets[contract_id] = approved
        if approved != expected_target:
            per_contract_binding[contract_id] = sorted(
                set(per_contract_binding[contract_id] + portfolio_binding)
            )
    approved_estimates = _risk_estimates(
        market=market,
        targets=approved_targets,
        strategy_type=strategy_type,
        profile=profile,
        policy=policy,
        config=config,
    )
    review_weeks = float(config["risk_horizon"]["review_horizon_weeks"])
    result = {
        "schemaVersion": "asset-simulation-oil-short-horizon-risk-review-v1",
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
        },
        "companyRiskAppetite": {
            "policy_hash": company_risk_appetite["policy_hash"],
            "risk_appetite_radar": dict(
                company_risk_appetite["risk_appetite_radar"]
            ),
            "resolved_limits": policy,
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
            "stress_loss_pct_of_allocated_strategy_capital": (
                100.0 * stress / allocated
            ),
            "stress_loss_pct_of_company_equity": 100.0 * stress / equity,
            "margin_pct_of_allocated_strategy_capital": (
                100.0 * margin / allocated
            ),
            "margin_pct_of_company_equity": 100.0 * margin / equity,
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
        },
    }
    rounded = _round_nested(result)
    identity = {
        "model_version": OIL_SHORT_HORIZON_RISK_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_short_horizon_risk_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_short_horizon_risk_contract_hash"],
        "position_mandate_hash": committee_position_mandate["identity"][
            "result_hash"
        ],
        "company_risk_appetite_hash": company_risk_appetite["policy_hash"],
        "risk_profile_hash": profile["profile_hash"],
        "write_back": False,
        "result_hash": sha256_json(rounded),
    }
    return _round_nested({"identity": identity, **rounded})
