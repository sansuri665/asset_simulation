"""Appointed-personnel profiles for the oil strategy research department.

The player-facing choice is a person, not eight free-form sliders.  A stable
Seed and candidate index generate one coherent preference radar plus a narrow
strategy-construction capability radar.  Style determines what the PM wants to
do; construction capability only bounds deterministic implementation error
between ideal policy and the submitted strategy proposal.  Neither radar
creates an aggregate investment-quality or alpha score.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal
from .registry import load_registered_assets, sha256_json


OIL_STRATEGY_RESEARCH_MODEL_VERSION = (
    "asset-simulation-oil-strategy-research-v0.3.0"
)
LEGACY_OIL_STRATEGY_RESEARCH_MODEL_VERSION = (
    "asset-simulation-oil-strategy-research-v0.2.1"
)
OIL_STRATEGY_RESEARCH_CONTRACT_ID = "oil_strategy_research_v2"
STRATEGY_STYLE_DIMENSIONS = (
    "continuation_reversion",
    "capital_deployment",
    "responsiveness",
    "selectivity",
    "turnover_activity",
    "holding_patience",
    "near_month_focus",
    "forecast_horizon",
)
LATENT_TRAITS = (
    "capital_appetite",
    "tempo",
    "discipline",
    "curve_focus",
    "continuation_bias",
)
STRATEGY_CONSTRUCTION_CAPABILITY_DIMENSIONS = (
    "exposure_construction",
    "transition_planning",
    "contract_lifecycle_planning",
)
CONSTRUCTION_CAPABILITY_LATENT_TRAITS = (
    "technical_foundation",
    "planning_discipline",
    "operational_experience",
)


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("oil strategy research profile contains a non-finite value")
        return round(value, 8)
    return value


def _validate_registered_assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_strategy_research_config"]
    contract = assets["oil_strategy_research_contract"]
    if config["model_version"] != OIL_STRATEGY_RESEARCH_MODEL_VERSION:
        raise ValueError("registered oil strategy research config version mismatch")
    if contract["contract_id"] != OIL_STRATEGY_RESEARCH_CONTRACT_ID:
        raise ValueError("registered oil strategy research contract id mismatch")
    if tuple(config["style_dimensions"]) != STRATEGY_STYLE_DIMENSIONS:
        raise ValueError("oil strategy style dimensions are out of contract order")
    if (
        tuple(config.get("construction_capability_dimensions", ()))
        != STRATEGY_CONSTRUCTION_CAPABILITY_DIMENSIONS
    ):
        raise ValueError(
            "oil strategy construction capability dimensions are out of contract order"
        )

    generation = config["candidate_generation"]
    minimum_count = int(generation["minimum_candidate_count"])
    maximum_count = int(generation["maximum_candidate_count"])
    default_count = int(generation["default_candidate_count"])
    if not 1 <= minimum_count <= default_count <= maximum_count:
        raise ValueError("oil strategy research candidate-count bounds are invalid")
    floor = float(generation["dimension_floor"])
    ceiling = float(generation["dimension_ceiling"])
    center = float(generation["dimension_center"])
    if not 0.0 <= floor < center < ceiling <= 100.0:
        raise ValueError("oil strategy research radar bounds are invalid")
    loadings = generation["latent_loadings"]
    if tuple(generation.get("latent_traits", ())) != LATENT_TRAITS:
        raise ValueError("oil strategy research latent-trait contract is invalid")
    continuation_slots = list(generation.get("continuation_bias_slots", ()))
    if len(continuation_slots) < maximum_count or not all(
        math.isfinite(float(value)) for value in continuation_slots
    ):
        raise ValueError("oil strategy continuation-bias slots are invalid")
    continuation_random_scale = float(
        generation.get("continuation_bias_random_scale", math.nan)
    )
    if not 0.0 <= continuation_random_scale <= 1.0:
        raise ValueError("oil strategy continuation-bias random scale is invalid")
    continuation_score_slots = list(
        generation.get("continuation_score_slots", ())
    )
    if len(continuation_score_slots) < maximum_count or not all(
        floor <= float(value) <= ceiling for value in continuation_score_slots
    ):
        raise ValueError("oil strategy continuation-score slots are invalid")
    continuation_score_random_scale = float(
        generation.get("continuation_score_random_scale", math.nan)
    )
    if not 0.0 <= continuation_score_random_scale <= 10.0:
        raise ValueError("oil strategy continuation-score random scale is invalid")
    if set(loadings) != set(STRATEGY_STYLE_DIMENSIONS):
        raise ValueError("oil strategy research latent loadings do not cover the radar")
    for dimension in STRATEGY_STYLE_DIMENSIONS:
        if set(loadings[dimension]) != set(LATENT_TRAITS):
            raise ValueError(
                f"oil strategy research latent loadings are invalid for {dimension}"
            )
    if not generation["family_names"] or not generation["given_names"]:
        raise ValueError("oil strategy research personnel name pools must not be empty")

    capability_generation = config["construction_capability_generation"]
    capability_floor = float(capability_generation["dimension_floor"])
    capability_center = float(capability_generation["dimension_center"])
    capability_ceiling = float(capability_generation["dimension_ceiling"])
    if not 0.0 <= capability_floor < capability_center < capability_ceiling <= 100.0:
        raise ValueError("oil strategy construction capability bounds are invalid")
    if (
        tuple(capability_generation.get("latent_traits", ()))
        != CONSTRUCTION_CAPABILITY_LATENT_TRAITS
    ):
        raise ValueError("oil strategy construction capability latents are invalid")
    capability_loadings = capability_generation["latent_loadings"]
    if set(capability_loadings) != set(STRATEGY_CONSTRUCTION_CAPABILITY_DIMENSIONS):
        raise ValueError(
            "oil strategy construction capability loadings do not cover the radar"
        )
    for dimension in STRATEGY_CONSTRUCTION_CAPABILITY_DIMENSIONS:
        if set(capability_loadings[dimension]) != set(
            CONSTRUCTION_CAPABILITY_LATENT_TRAITS
        ):
            raise ValueError(
                f"oil strategy construction capability loadings are invalid for {dimension}"
            )
    capability_mapping = config["construction_capability_mapping"]
    expected_capability_mappings = {
        "exposure_max_abs_target_error_pct": 100.0,
        "transition_max_abs_gap_error_pct": 100.0,
        "lifecycle_max_abs_role_weight_error": 1.0,
    }
    if set(capability_mapping) != set(expected_capability_mappings):
        raise ValueError("oil strategy construction capability mapping is incomplete")
    for key, maximum in expected_capability_mappings.items():
        anchors = capability_mapping[key]
        score_0 = float(anchors["score_0"])
        score_50 = float(anchors["score_50"])
        score_100 = float(anchors["score_100"])
        if not 0.0 <= score_100 <= score_50 <= score_0 <= maximum:
            raise ValueError(
                f"oil strategy construction capability mapping is invalid: {key}"
            )

    mapping = config["parameter_mapping"]
    bounded = (
        ("adjustment_speed", 0.0, 1.0),
        ("signal_deadband_abs", 0.0, 0.95),
        ("minimum_trade_edge_pct", 0.0, math.inf),
        ("normal_net_trade_limit_utilization", 0.0, 1.0),
        ("gross_turnover_multiplier", 0.0, math.inf),
        ("expected_holding_turns", 0.0, math.inf),
        ("position_persistence", 0.0, 1.0),
        ("net_edge_floor_pct", 0.0, math.inf),
        ("main_role_weight", 0.0, 1.0),
    )
    for key, lower_bound, upper_bound in bounded:
        low = float(mapping[key]["minimum"])
        high = float(mapping[key]["maximum"])
        if not lower_bound <= low <= high <= upper_bound:
            raise ValueError(f"oil strategy research parameter bounds are invalid: {key}")
    for key, lower_bound, upper_bound in (
        ("continuation_weight", 0.0, 1.0),
        ("capital_deployment_pct_of_allocated_equity", 0.0, 100.0),
    ):
        values = mapping[key]
        low = float(values["minimum"])
        neutral = float(values["neutral"])
        high = float(values["maximum"])
        if not lower_bound <= low <= neutral <= high <= upper_bound:
            raise ValueError(f"oil strategy research centered bounds are invalid: {key}")
    for key in ("short_horizon_weights", "long_horizon_weights"):
        weights = [float(value) for value in mapping[key]]
        if len(weights) != 3 or any(value <= 0.0 for value in weights) or not math.isclose(
            sum(weights), 1.0
        ):
            raise ValueError(f"oil strategy research {key} are invalid")
    return assets, config, contract


def _linear(bounds: Mapping[str, Any], score: float) -> float:
    normalized = clamp(float(score) / 100.0, 0.0, 1.0)
    return float(bounds["minimum"]) + normalized * (
        float(bounds["maximum"]) - float(bounds["minimum"])
    )


def _curved(bounds: Mapping[str, Any], score: float) -> float:
    normalized = clamp(float(score) / 100.0, 0.0, 1.0)
    curve = str(bounds.get("curve", "linear"))
    if curve == "quadratic":
        normalized = normalized * normalized
    if curve == "logarithmic":
        low = float(bounds["minimum"])
        high = float(bounds["maximum"])
        return low * math.exp(math.log(high / low) * normalized)
    return float(bounds["minimum"]) + normalized * (
        float(bounds["maximum"]) - float(bounds["minimum"])
    )


def _centered_linear(bounds: Mapping[str, Any], score: float) -> float:
    """Map score 50 to an explicit neutral value without losing end ranges."""

    normalized = clamp(float(score) / 100.0, 0.0, 1.0)
    low = float(bounds["minimum"])
    neutral = float(bounds["neutral"])
    high = float(bounds["maximum"])
    if normalized <= 0.5:
        return low + normalized * 2.0 * (neutral - low)
    return neutral + (normalized - 0.5) * 2.0 * (high - neutral)


def _piecewise_score_anchors(anchors: Mapping[str, Any], score: float) -> float:
    """Interpolate explicit 0/50/100 capability anchors continuously."""

    normalized = clamp(float(score) / 100.0, 0.0, 1.0)
    score_0 = float(anchors["score_0"])
    score_50 = float(anchors["score_50"])
    score_100 = float(anchors["score_100"])
    if normalized <= 0.5:
        return score_0 + normalized * 2.0 * (score_50 - score_0)
    return score_50 + (normalized - 0.5) * 2.0 * (score_100 - score_50)


def _style_tags(radar: Mapping[str, float], config: Mapping[str, Any]) -> list[str]:
    threshold = config["style_tag_thresholds"]
    high = float(threshold["high"])
    low = float(threshold["low"])
    labels = {
        "continuation_reversion": ("顺势延续", "区间回归"),
        "capital_deployment": ("积极部署", "保守部署"),
        "responsiveness": ("快速调仓", "缓慢调仓"),
        "selectivity": ("严选信号", "广泛参与"),
        "turnover_activity": ("高换手", "低换手"),
        "holding_patience": ("耐心持有", "快速兑现"),
        "near_month_focus": ("近月集中", "主次均衡"),
        "forecast_horizon": ("偏长视野", "偏短视野"),
    }
    ordered = sorted(
        STRATEGY_STYLE_DIMENSIONS,
        key=lambda key: abs(float(radar[key]) - 50.0),
        reverse=True,
    )
    tags: list[str] = []
    for key in ordered:
        value = float(radar[key])
        if value >= high:
            tags.append(labels[key][0])
        elif value <= low:
            tags.append(labels[key][1])
        if len(tags) == 3:
            break
    return tags or ["均衡配置"]


def _construction_capability_tags(radar: Mapping[str, float]) -> list[str]:
    labels = {
        "exposure_construction": "仓位构造",
        "transition_planning": "调仓规划",
        "contract_lifecycle_planning": "合约周期",
    }
    ordered = sorted(
        STRATEGY_CONSTRUCTION_CAPABILITY_DIMENSIONS,
        key=lambda key: float(radar[key]),
        reverse=True,
    )
    return [f"{labels[key]}稳健" for key in ordered[:2] if float(radar[key]) >= 65.0]


def _resolved_construction_policy(
    capability_radar: Mapping[str, float], config: Mapping[str, Any]
) -> dict[str, Any]:
    mapping = config["construction_capability_mapping"]
    exposure_score = float(capability_radar["exposure_construction"])
    transition_score = float(capability_radar["transition_planning"])
    lifecycle_score = float(
        capability_radar["contract_lifecycle_planning"]
    )
    return {
        "exposure_construction_score": exposure_score,
        "transition_planning_score": transition_score,
        "contract_lifecycle_planning_score": lifecycle_score,
        "max_abs_target_error_ratio": _piecewise_score_anchors(
            mapping["exposure_max_abs_target_error_pct"], exposure_score
        )
        / 100.0,
        "max_abs_gap_error_ratio": _piecewise_score_anchors(
            mapping["transition_max_abs_gap_error_pct"], transition_score
        )
        / 100.0,
        "max_abs_role_weight_error": _piecewise_score_anchors(
            mapping["lifecycle_max_abs_role_weight_error"], lifecycle_score
        ),
        "error_process": "bounded_deterministic_visible_state_only",
        "alpha_or_future_truth_used": False,
    }


def _resolved_policy(
    radar: Mapping[str, float],
    config: Mapping[str, Any],
    *,
    turnover_override: float | None = None,
) -> dict[str, Any]:
    mapping = config["parameter_mapping"]
    orientation_score = float(radar["continuation_reversion"])
    deployment_score = float(radar["capital_deployment"])
    response_score = float(radar["responsiveness"])
    selection_score = float(radar["selectivity"])
    turnover_score = float(radar["turnover_activity"])
    patience_score = float(radar["holding_patience"])
    near_month_score = float(radar["near_month_focus"])
    horizon_score = float(radar["forecast_horizon"])
    if turnover_override is not None:
        if isinstance(turnover_override, bool):
            raise ValueError("turnover development override must be a number from 0 to 100")
        turnover_score = float(turnover_override)
        if not math.isfinite(turnover_score) or not 0.0 <= turnover_score <= 100.0:
            raise ValueError("turnover development override must be finite and between 0 and 100")

    main_weight = _linear(mapping["main_role_weight"], near_month_score)
    horizon_mix = clamp(horizon_score / 100.0, 0.0, 1.0)
    short_weights = [float(value) for value in mapping["short_horizon_weights"]]
    long_weights = [float(value) for value in mapping["long_horizon_weights"]]
    horizon_weights = [
        (1.0 - horizon_mix) * short + horizon_mix * long
        for short, long in zip(short_weights, long_weights, strict=True)
    ]
    total_horizon_weight = sum(horizon_weights)
    horizon_weights = [value / total_horizon_weight for value in horizon_weights]
    continuation_weight = _centered_linear(
        mapping["continuation_weight"], orientation_score
    )
    gross_turnover_multiplier = _curved(
        mapping["gross_turnover_multiplier"], turnover_score
    )
    expected_holding_turns = _curved(
        mapping["expected_holding_turns"], patience_score
    )
    position_persistence = _curved(
        mapping["position_persistence"], patience_score
    )
    return {
        "signal": {
            "horizon_weeks": [2, 4, 8],
            "horizon_weights": horizon_weights,
            "orientation_score": orientation_score,
            "continuation_weight": continuation_weight,
            "reversion_weight": 1.0 - continuation_weight,
            "continuation_overlay_intensity": continuation_weight,
            "signal_deadband_abs": _linear(
                mapping["signal_deadband_abs"], selection_score
            ),
        },
        "risk": {
            "role_weights": {
                "main": main_weight,
                "next_main": 1.0 - main_weight,
            },
            "capital_deployment_score": deployment_score,
            "capital_deployment_pct_of_allocated_equity": _centered_linear(
                mapping["capital_deployment_pct_of_allocated_equity"],
                deployment_score,
            ),
        },
        "execution": {
            "turnover_intensity": turnover_score,
            "normalized_intensity": turnover_score / 100.0,
            "adjustment_speed": _linear(
                mapping["adjustment_speed"], response_score
            ),
            "signal_deadband_abs": _linear(
                mapping["signal_deadband_abs"], selection_score
            ),
            "minimum_trade_edge_pct": _linear(
                mapping["minimum_trade_edge_pct"], selection_score
            ),
            "gross_turnover_multiplier": gross_turnover_multiplier,
            "normal_net_trade_limit_utilization": _linear(
                mapping["normal_net_trade_limit_utilization"], response_score
            ),
            "expected_holding_turns": expected_holding_turns,
            "position_persistence": position_persistence,
            "net_edge_floor_pct": _linear(
                mapping["net_edge_floor_pct"], selection_score
            ),
            "turnover_source": (
                "development_override"
                if turnover_override is not None
                else "appointed_personnel"
            ),
        },
    }


def _pack_profile(
    *,
    personnel_id: str,
    display_name: str,
    style_radar: Mapping[str, float],
    construction_capability_radar: Mapping[str, float] | None,
    candidate_index: int | None,
    generation_seed: int | None,
    source: str,
) -> dict[str, Any]:
    assets, config, contract = _validate_registered_assets()
    radar = {key: float(value) for key, value in dict(style_radar).items()}
    unknown = set(radar) - set(STRATEGY_STYLE_DIMENSIONS)
    if unknown:
        raise KeyError(f"unknown oil strategy style dimensions: {sorted(unknown)}")
    for key in STRATEGY_STYLE_DIMENSIONS:
        value = radar.get(key)
        if value is None or not math.isfinite(value) or not 0.0 <= value <= 100.0:
            raise ValueError(f"oil strategy style {key} must be between 0 and 100")
    default_capability = config["default_director"][
        "construction_capability_radar"
    ]
    capability_radar = {
        key: float(value)
        for key, value in dict(
            default_capability
            if construction_capability_radar is None
            else construction_capability_radar
        ).items()
    }
    unknown_capability = set(capability_radar) - set(
        STRATEGY_CONSTRUCTION_CAPABILITY_DIMENSIONS
    )
    if unknown_capability:
        raise KeyError(
            "unknown oil strategy construction capability dimensions: "
            f"{sorted(unknown_capability)}"
        )
    for key in STRATEGY_CONSTRUCTION_CAPABILITY_DIMENSIONS:
        value = capability_radar.get(key)
        if value is None or not math.isfinite(value) or not 0.0 <= value <= 100.0:
            raise ValueError(
                f"oil strategy construction capability {key} must be between 0 and 100"
            )
    if not str(personnel_id) or not str(display_name):
        raise ValueError("oil strategy personnel identity must not be empty")
    result = {
        "schemaVersion": "asset-simulation-oil-strategy-research-profile-v2",
        "appointment": {
            "department": "strategy_research",
            "role": str(config["appointment_role"]),
            "personnel_id": str(personnel_id),
            "display_name": str(display_name),
            "source": str(source),
            "candidate_index": candidate_index,
            "generation_seed": generation_seed,
        },
        "signal_engine": {
            "strategy_id": str(config["signal_engine_id"]),
            "status": "current_directional_baseline",
        },
        "style_radar": radar,
        "style_tags": _style_tags(radar, config),
        "preference_total_score": None,
        "construction_capability_radar": capability_radar,
        "construction_capability_tags": _construction_capability_tags(
            capability_radar
        ),
        "resolved_policy": _resolved_policy(radar, config),
        "resolved_construction_policy": _resolved_construction_policy(
            capability_radar, config
        ),
        "governance": {
            "player_can_edit_radar": False,
            "selection_method": "appoint_generated_personnel",
            "higher_score_is_better": False,
            "research_capability_score_used": False,
            "construction_capability_dimensions_are_higher_is_better": True,
            "aggregate_construction_capability_score_available": False,
            "construction_capability_can_create_alpha": False,
            "construction_capability_can_read_hidden_future": False,
            "investment_decision_owner": "system_proxy_pending_department",
            "trading_execution_owner": "neutral_execution_engine_pending_trader",
            "hard_risk_owner": "market_and_account_rules",
        },
        "identity": {
            "model_version": OIL_STRATEGY_RESEARCH_MODEL_VERSION,
            "config_id": str(config["config_id"]),
            "config_hash": assets["oil_strategy_research_config_hash"],
            "field_contract_id": str(contract["contract_id"]),
            "field_contract_hash": assets["oil_strategy_research_contract_hash"],
            "write_back": False,
        },
    }
    result["profile_hash"] = sha256_json(result)
    return _round_nested(result)


def build_default_oil_strategy_research_profile() -> dict[str, Any]:
    """Build the system's appointed default director without exposing sliders."""

    _, config, _ = _validate_registered_assets()
    default = config["default_director"]
    return _pack_profile(
        personnel_id=str(default["personnel_id"]),
        display_name=str(default["display_name"]),
        style_radar=default["style_radar"],
        construction_capability_radar=default[
            "construction_capability_radar"
        ],
        candidate_index=None,
        generation_seed=None,
        source="default_appointment",
    )


def _stable_pool_index(seed: int, address: str, index: int, size: int) -> int:
    draw = normal(seed, address, index)
    uniform = 0.5 * (1.0 + math.erf(draw / math.sqrt(2.0)))
    return min(size - 1, int(uniform * size))


def generate_oil_strategy_research_candidate(
    *, seed: int, candidate_index: int
) -> dict[str, Any]:
    """Generate one deterministic, correlated strategy-director candidate."""

    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("oil strategy research seed must be a non-negative integer")
    if (
        isinstance(candidate_index, bool)
        or not isinstance(candidate_index, int)
        or candidate_index < 0
    ):
        raise ValueError("oil strategy research candidate index must be non-negative")
    _, config, _ = _validate_registered_assets()
    generation = config["candidate_generation"]
    latents = {}
    for key in LATENT_TRAITS:
        draw = normal(
            seed,
            f"oil_strategy_research.candidate.{candidate_index}.latent.{key}",
            candidate_index,
        )
        if key == "continuation_bias":
            slots = list(generation["continuation_bias_slots"])
            draw = float(slots[candidate_index % len(slots)]) + float(
                generation["continuation_bias_random_scale"]
            ) * draw
        latents[key] = clamp(draw, -1.8, 1.8)
    center = float(generation["dimension_center"])
    floor = float(generation["dimension_floor"])
    ceiling = float(generation["dimension_ceiling"])
    idiosyncratic_scale = float(generation["idiosyncratic_scale"])
    radar: dict[str, float] = {}
    for dimension_index, dimension in enumerate(STRATEGY_STYLE_DIMENSIONS):
        loadings = generation["latent_loadings"][dimension]
        dimension_draw = normal(
            seed,
            f"oil_strategy_research.candidate.{candidate_index}.dimension.{dimension}",
            dimension_index,
        )
        if dimension == "continuation_reversion":
            slots = list(generation["continuation_score_slots"])
            value = float(slots[candidate_index % len(slots)]) + float(
                generation["continuation_score_random_scale"]
            ) * dimension_draw
        else:
            value = center + sum(
                float(loadings[key]) * latents[key] for key in LATENT_TRAITS
            )
            value += idiosyncratic_scale * dimension_draw
        radar[dimension] = round(clamp(value, floor, ceiling), 2)

    capability_generation = config["construction_capability_generation"]
    capability_latents = {
        key: clamp(
            normal(
                seed,
                f"oil_strategy_research.candidate.{candidate_index}.construction_latent.{key}",
                candidate_index,
            ),
            -1.8,
            1.8,
        )
        for key in CONSTRUCTION_CAPABILITY_LATENT_TRAITS
    }
    capability_center = float(capability_generation["dimension_center"])
    capability_floor = float(capability_generation["dimension_floor"])
    capability_ceiling = float(capability_generation["dimension_ceiling"])
    capability_idiosyncratic_scale = float(
        capability_generation["idiosyncratic_scale"]
    )
    capability_radar: dict[str, float] = {}
    for dimension_index, dimension in enumerate(
        STRATEGY_CONSTRUCTION_CAPABILITY_DIMENSIONS
    ):
        loadings = capability_generation["latent_loadings"][dimension]
        dimension_draw = normal(
            seed,
            f"oil_strategy_research.candidate.{candidate_index}.construction_dimension.{dimension}",
            dimension_index,
        )
        value = capability_center + sum(
            float(loadings[key]) * capability_latents[key]
            for key in CONSTRUCTION_CAPABILITY_LATENT_TRAITS
        )
        value += capability_idiosyncratic_scale * dimension_draw
        capability_radar[dimension] = round(
            clamp(value, capability_floor, capability_ceiling), 2
        )

    family_names = list(generation["family_names"])
    given_names = list(generation["given_names"])
    family_index = _stable_pool_index(
        seed,
        f"oil_strategy_research.candidate.{candidate_index}.family_name",
        candidate_index,
        len(family_names),
    )
    given_index = _stable_pool_index(
        seed,
        f"oil_strategy_research.candidate.{candidate_index}.given_name",
        candidate_index,
        len(given_names),
    )
    return _pack_profile(
        personnel_id=f"oil_strategy_director_{seed}_{candidate_index}",
        display_name=f"{family_names[family_index]}{given_names[given_index]}",
        style_radar=radar,
        construction_capability_radar=capability_radar,
        candidate_index=candidate_index,
        generation_seed=seed,
        source="generated_candidate",
    )


def generate_oil_strategy_research_roster(
    *, seed: int, candidate_count: int | None = None
) -> dict[str, Any]:
    """Generate the appointable roster; no endpoint accepts direct radar values."""

    assets, config, contract = _validate_registered_assets()
    generation = config["candidate_generation"]
    count = (
        int(generation["default_candidate_count"])
        if candidate_count is None
        else candidate_count
    )
    if isinstance(count, bool) or not isinstance(count, int):
        raise ValueError("oil strategy research candidate count must be an integer")
    if not int(generation["minimum_candidate_count"]) <= count <= int(
        generation["maximum_candidate_count"]
    ):
        raise ValueError("oil strategy research candidate count is outside its bounds")
    candidates = [
        generate_oil_strategy_research_candidate(seed=seed, candidate_index=index)
        for index in range(count)
    ]
    result = {
        "ok": True,
        "schemaVersion": "asset-simulation-oil-strategy-research-roster-v2",
        "seed": int(seed),
        "appointmentRole": str(config["appointment_role"]),
        "candidateCount": count,
        "selectionPolicy": {
            "player_can_edit_radar": False,
            "method": "appoint_one_generated_personnel",
            "preference_total_score_available": False,
            "construction_capability_total_score_available": False,
        },
        "candidates": candidates,
    }
    identity = {
        "schema_version": "asset-simulation-oil-strategy-research-roster-identity-v2",
        "model_version": OIL_STRATEGY_RESEARCH_MODEL_VERSION,
        "config_id": str(config["config_id"]),
        "config_hash": assets["oil_strategy_research_config_hash"],
        "field_contract_id": str(contract["contract_id"]),
        "field_contract_hash": assets["oil_strategy_research_contract_hash"],
        "write_back": False,
        "result_hash": sha256_json(result),
    }
    return _round_nested({"identity": identity, **result})


def resolve_oil_strategy_research_profile(
    profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Canonicalize one appointed profile and reject modified generated records."""

    if profile is None:
        return build_default_oil_strategy_research_profile()
    supplied = dict(profile)
    appointment = dict(supplied.get("appointment", {}))
    rebuilt = _pack_profile(
        personnel_id=str(appointment.get("personnel_id", "")),
        display_name=str(appointment.get("display_name", "")),
        style_radar=supplied.get("style_radar", {}),
        construction_capability_radar=supplied.get(
            "construction_capability_radar"
        ),
        candidate_index=appointment.get("candidate_index"),
        generation_seed=appointment.get("generation_seed"),
        source=str(appointment.get("source", "appointed_profile")),
    )
    supplied_hash = supplied.get("profile_hash")
    if supplied_hash is not None and str(supplied_hash) != str(rebuilt["profile_hash"]):
        supplied_identity = dict(supplied.get("identity", {}))
        is_legacy_profile = (
            "construction_capability_radar" not in supplied
            and supplied_identity.get("model_version")
            == LEGACY_OIL_STRATEGY_RESEARCH_MODEL_VERSION
        )
        if not is_legacy_profile:
            raise ValueError(
                "oil strategy research profile was modified after generation"
            )
        _, config, _ = _validate_registered_assets()
        legacy_result = {
            "schemaVersion": "asset-simulation-oil-strategy-research-profile-v2",
            "appointment": rebuilt["appointment"],
            "signal_engine": rebuilt["signal_engine"],
            "style_radar": rebuilt["style_radar"],
            "style_tags": rebuilt["style_tags"],
            "preference_total_score": None,
            "resolved_policy": _resolved_policy(rebuilt["style_radar"], config),
            "governance": {
                key: rebuilt["governance"][key]
                for key in (
                    "player_can_edit_radar",
                    "selection_method",
                    "higher_score_is_better",
                    "research_capability_score_used",
                    "investment_decision_owner",
                    "trading_execution_owner",
                    "hard_risk_owner",
                )
            },
            "identity": supplied_identity,
        }
        if str(supplied_hash) != sha256_json(legacy_result):
            raise ValueError(
                "oil strategy research profile was modified after generation"
            )
    return rebuilt


def build_oil_strategy_construction_adjustments(
    profile: Mapping[str, Any] | None,
    *,
    visible_state_hash: str,
    contract_ids: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Build bounded proposal errors from current visible state only.

    These adjustments model strategy-design craftsmanship, not alpha.  Scores
    only narrow zero-mean construction error around the PM's own ideal policy.
    The default compatibility director has zero error on all three axes.
    """

    canonical = resolve_oil_strategy_research_profile(profile)
    _, config, _ = _validate_registered_assets()
    state_hash = str(visible_state_hash)
    if not state_hash:
        raise ValueError("oil strategy construction requires a visible state hash")
    ordered_contracts = sorted(str(value) for value in contract_ids)
    if len(ordered_contracts) != len(set(ordered_contracts)) or any(
        not value for value in ordered_contracts
    ):
        raise ValueError("oil strategy construction contract ids must be unique")
    policy = _resolved_construction_policy(
        canonical["construction_capability_radar"], config
    )
    error_stream_identity = {
        "personnel_id": canonical["appointment"]["personnel_id"],
        "generation_seed": canonical["appointment"]["generation_seed"],
        "candidate_index": canonical["appointment"]["candidate_index"],
    }
    error_stream_identity_hash = sha256_json(error_stream_identity)
    personnel_seed = int(error_stream_identity_hash[:16], 16)

    def bounded_unit(dimension: str, contract_id: str, index: int) -> float:
        draw = normal(
            personnel_seed,
            (
                "oil_strategy_research.construction."
                f"{state_hash}.{dimension}.{contract_id}"
            ),
            index,
        )
        return math.tanh(draw)

    lifecycle_unit = bounded_unit("contract_lifecycle_planning", "portfolio", 0)
    result = {
        "schemaVersion": "asset-simulation-oil-strategy-construction-adjustment-v1",
        "strategy_profile_hash": canonical["profile_hash"],
        "error_stream_identity_hash": error_stream_identity_hash,
        "visible_state_hash": state_hash,
        "construction_capability_radar": dict(
            canonical["construction_capability_radar"]
        ),
        "resolved_construction_policy": policy,
        "portfolio": {
            "role_weight_error": lifecycle_unit
            * float(policy["max_abs_role_weight_error"]),
        },
        "contracts": {
            contract_id: {
                "target_scale_error": bounded_unit(
                    "exposure_construction", contract_id, index
                )
                * float(policy["max_abs_target_error_ratio"]),
                "transition_gap_error": bounded_unit(
                    "transition_planning", contract_id, index
                )
                * float(policy["max_abs_gap_error_ratio"]),
            }
            for index, contract_id in enumerate(ordered_contracts)
        },
        "informationPolicy": {
            "visible_state_only": True,
            "future_market_used": False,
            "forecast_truth_used": False,
            "can_create_or_reverse_signal": False,
            "aggregate_capability_score_used": False,
        },
    }
    return _round_nested(
        {
            "identity": {
                "model_version": OIL_STRATEGY_RESEARCH_MODEL_VERSION,
                "write_back": False,
                "result_hash": sha256_json(result),
            },
            **result,
        }
    )


def resolve_oil_strategy_runtime_policy(
    profile: Mapping[str, Any] | None,
    *,
    turnover_development_override: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a canonical appointment and its optional calibration-only policy."""

    canonical = resolve_oil_strategy_research_profile(profile)
    _, config, _ = _validate_registered_assets()
    policy = _resolved_policy(
        canonical["style_radar"],
        config,
        turnover_override=turnover_development_override,
    )
    return canonical, _round_nested(
        {
            **policy,
            "construction": _resolved_construction_policy(
                canonical["construction_capability_radar"], config
            ),
        }
    )
