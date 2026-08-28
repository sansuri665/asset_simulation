"""Continuous appointed-personnel model for the oil execution department."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .math_utils import clamp
from .random_stream import normal
from .registry import load_registered_assets, sha256_json


OIL_EXECUTION_DESK_MODEL_VERSION = "asset-simulation-oil-execution-desk-v0.1.0"
OIL_EXECUTION_DESK_CONTRACT_ID = "oil_execution_desk_v1"
CAPABILITY_DIMENSIONS = (
    "price_execution",
    "impact_control",
    "liquidity_scheduling",
    "completion_reliability",
    "roll_coordination",
    "fee_efficiency",
)
STYLE_DIMENSIONS = ("urgency", "passive_preference", "window_timing")


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("oil execution profile contains a non-finite value")
        return round(value, 8)
    return value


def _assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_execution_desk_config"]
    contract = assets["oil_execution_desk_contract"]
    if config["model_version"] != OIL_EXECUTION_DESK_MODEL_VERSION:
        raise ValueError("registered oil execution desk config version mismatch")
    if contract["contract_id"] != OIL_EXECUTION_DESK_CONTRACT_ID:
        raise ValueError("registered oil execution desk contract id mismatch")
    if tuple(config["capability_dimensions"]) != CAPABILITY_DIMENSIONS:
        raise ValueError("oil execution capability dimensions are out of order")
    if tuple(config["style_dimensions"]) != STYLE_DIMENSIONS:
        raise ValueError("oil execution style dimensions are out of order")
    weights = {key: float(value) for key, value in config["capability_weights"].items()}
    if set(weights) != set(CAPABILITY_DIMENSIONS) or not math.isclose(sum(weights.values()), 1.0):
        raise ValueError("oil execution capability weights must cover the radar and sum to one")
    return assets, config, contract


def _score(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"oil execution {label} must be a continuous score from 0 to 100")
    resolved = float(value)
    if not math.isfinite(resolved) or not 0.0 <= resolved <= 100.0:
        raise ValueError(f"oil execution {label} must be finite and between 0 and 100")
    return resolved


def _piecewise(anchor: Mapping[str, Any], score: float) -> float:
    """Continuous interpolation with score 50 exactly fixed at the neutral anchor."""

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


def _resolved_policy(
    capability: Mapping[str, float], style: Mapping[str, float], config: Mapping[str, Any]
) -> dict[str, Any]:
    mapping = config["parameter_mapping"]
    spread = _piecewise(mapping["spread_cost_multiplier"], capability["price_execution"])
    spread *= _piecewise(mapping["passive_spread_multiplier"], style["passive_preference"])
    slippage = _piecewise(mapping["slippage_multiplier"], capability["impact_control"])
    slippage *= _piecewise(mapping["urgency_multiplier"], style["urgency"])
    return {
        "price_execution": {"spread_cost_multiplier": spread},
        "impact_control": {"slippage_multiplier": slippage},
        "liquidity_scheduling": {
            "visible_liquidity_weight_exponent": _piecewise(
                mapping["visible_liquidity_weight_exponent"], capability["liquidity_scheduling"]
            ),
            "window_timing_tilt": _piecewise(mapping["window_timing_tilt"], style["window_timing"]),
        },
        "completion_reliability": {
            "normal_trade_completion_multiplier": _piecewise(
                mapping["normal_trade_completion_multiplier"], capability["completion_reliability"]
            )
        },
        "roll_coordination": {
            "roll_cost_multiplier": _piecewise(
                mapping["roll_cost_multiplier"], capability["roll_coordination"]
            )
        },
        "fee_efficiency": {
            "broker_fee_multiplier": _piecewise(
                mapping["broker_fee_multiplier"], capability["fee_efficiency"]
            ),
            "rebate_realization_multiplier": _piecewise(
                mapping["rebate_realization_multiplier"], capability["fee_efficiency"]
            ),
        },
    }


def adjust_visible_execution_weights(
    weights: Sequence[float], policy: Mapping[str, Any]
) -> list[float]:
    """Reweight already-visible liquidity only; score 50 returns exact input weights."""

    base = [max(0.0, float(value)) for value in weights]
    if not base or sum(base) <= 0.0:
        return base
    scheduling = policy["liquidity_scheduling"]
    exponent = float(scheduling["visible_liquidity_weight_exponent"])
    tilt = float(scheduling["window_timing_tilt"])
    center = (len(base) - 1) / 2.0
    scaled = []
    for index, value in enumerate(base):
        timing = 1.0 if center == 0.0 else 1.0 + tilt * (index - center) / center
        scaled.append(max(0.0, value**exponent * timing))
    total = sum(scaled)
    return [value / total for value in scaled]


def _tags(capability: Mapping[str, float], config: Mapping[str, Any]) -> list[str]:
    labels = {
        "price_execution": ("价格执行强", "价格执行弱"),
        "impact_control": ("冲击控制强", "冲击控制弱"),
        "liquidity_scheduling": ("流动性调度强", "调度粗糙"),
        "completion_reliability": ("完成可靠", "完成不稳"),
        "roll_coordination": ("移仓协调强", "移仓协调弱"),
        "fee_efficiency": ("费率议价强", "费率议价弱"),
    }
    high = float(config["tag_thresholds"]["high"])
    low = float(config["tag_thresholds"]["low"])
    ordered = sorted(CAPABILITY_DIMENSIONS, key=lambda key: abs(capability[key] - 50.0), reverse=True)
    result = []
    for key in ordered:
        if capability[key] >= high:
            result.append(labels[key][0])
        elif capability[key] <= low:
            result.append(labels[key][1])
        if len(result) == 3:
            break
    return result or ["均衡执行"]


def _pack(
    *, personnel_id: str, display_name: str, capability_radar: Mapping[str, Any],
    execution_style: Mapping[str, Any], candidate_index: int | None,
    generation_seed: int | None, source: str,
) -> dict[str, Any]:
    assets, config, contract = _assets()
    capability = {key: _score(capability_radar.get(key), key) for key in CAPABILITY_DIMENSIONS}
    style = {key: _score(execution_style.get(key), key) for key in STYLE_DIMENSIONS}
    if set(capability_radar) != set(CAPABILITY_DIMENSIONS) or set(execution_style) != set(STYLE_DIMENSIONS):
        raise KeyError("oil execution profile dimensions must exactly match the registered radar")
    weights = config["capability_weights"]
    total_score = sum(capability[key] * float(weights[key]) for key in CAPABILITY_DIMENSIONS)
    result = {
        "schemaVersion": "asset-simulation-oil-execution-desk-profile-v1",
        "appointment": {
            "department": "trading_execution", "role": str(config["appointment_role"]),
            "personnel_id": str(personnel_id), "display_name": str(display_name),
            "source": str(source), "candidate_index": candidate_index,
            "generation_seed": generation_seed,
        },
        "capability_radar": capability,
        "execution_style": style,
        "capability_total_score": total_score,
        "capability_weights": dict(weights),
        "capability_tags": _tags(capability, config),
        "resolved_policy": _resolved_policy(capability, style, config),
        "governance": {
            "scores_are_continuous": True, "neutral_baseline_score": 50.0,
            "player_can_edit_radar": False, "selection_method": "appoint_generated_personnel",
            "forecast_or_target_position_owner": False, "hard_market_rules_owner": False,
        },
        "identity": {
            "model_version": OIL_EXECUTION_DESK_MODEL_VERSION,
            "config_id": str(config["config_id"]),
            "config_hash": assets["oil_execution_desk_config_hash"],
            "field_contract_id": str(contract["contract_id"]),
            "field_contract_hash": assets["oil_execution_desk_contract_hash"],
            "write_back": False,
        },
    }
    result["profile_hash"] = sha256_json(result)
    return _round_nested(result)


def build_default_oil_execution_desk_profile() -> dict[str, Any]:
    _, config, _ = _assets()
    default = config["default_director"]
    return _pack(
        personnel_id=default["personnel_id"], display_name=default["display_name"],
        capability_radar=default["capability_radar"], execution_style=default["execution_style"],
        candidate_index=None, generation_seed=None, source="default_appointment",
    )


def _pool_index(seed: int, address: str, index: int, size: int) -> int:
    draw = normal(seed, address, index)
    uniform = 0.5 * (1.0 + math.erf(draw / math.sqrt(2.0)))
    return min(size - 1, int(uniform * size))


def generate_oil_execution_desk_candidate(*, seed: int, candidate_index: int) -> dict[str, Any]:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("oil execution seed must be a non-negative integer")
    if isinstance(candidate_index, bool) or not isinstance(candidate_index, int) or candidate_index < 0:
        raise ValueError("oil execution candidate index must be non-negative")
    _, config, _ = _assets()
    generation = config["candidate_generation"]
    traits = list(generation["latent_traits"])
    latents = {
        key: clamp(normal(seed, f"oil_execution.candidate.{candidate_index}.latent.{key}", candidate_index), -1.8, 1.8)
        for key in traits
    }
    radar: dict[str, float] = {}
    all_dimensions = CAPABILITY_DIMENSIONS + STYLE_DIMENSIONS
    for dimension_index, dimension in enumerate(all_dimensions):
        loading = generation["latent_loadings"][dimension]
        value = float(generation["dimension_center"]) + sum(float(loading[key]) * latents[key] for key in traits)
        value += float(generation["idiosyncratic_scale"]) * normal(
            seed, f"oil_execution.candidate.{candidate_index}.dimension.{dimension}", dimension_index
        )
        radar[dimension] = round(clamp(value, float(generation["dimension_floor"]), float(generation["dimension_ceiling"])), 2)
    families = list(generation["family_names"])
    given = list(generation["given_names"])
    family = families[_pool_index(seed, f"oil_execution.candidate.{candidate_index}.family", candidate_index, len(families))]
    name = given[_pool_index(seed, f"oil_execution.candidate.{candidate_index}.given", candidate_index, len(given))]
    return _pack(
        personnel_id=f"oil_execution_director_{seed}_{candidate_index}", display_name=f"{family}{name}",
        capability_radar={key: radar[key] for key in CAPABILITY_DIMENSIONS},
        execution_style={key: radar[key] for key in STYLE_DIMENSIONS},
        candidate_index=candidate_index, generation_seed=seed, source="generated_candidate",
    )


def generate_oil_execution_desk_roster(*, seed: int, candidate_count: int | None = None) -> dict[str, Any]:
    assets, config, contract = _assets()
    generation = config["candidate_generation"]
    count = int(generation["default_candidate_count"]) if candidate_count is None else candidate_count
    if isinstance(count, bool) or not isinstance(count, int) or not int(generation["minimum_candidate_count"]) <= count <= int(generation["maximum_candidate_count"]):
        raise ValueError("oil execution candidate count is outside its bounds")
    candidates = [generate_oil_execution_desk_candidate(seed=seed, candidate_index=index) for index in range(count)]
    result = {
        "ok": True, "schemaVersion": "asset-simulation-oil-execution-desk-roster-v1",
        "seed": seed, "candidateCount": count, "appointmentRole": config["appointment_role"],
        "selectionPolicy": {"player_can_edit_radar": False, "scores_are_continuous": True, "method": "appoint_one_generated_personnel"},
        "candidates": candidates,
    }
    identity = {
        "schema_version": "asset-simulation-oil-execution-desk-roster-identity-v1",
        "model_version": OIL_EXECUTION_DESK_MODEL_VERSION, "config_id": config["config_id"],
        "config_hash": assets["oil_execution_desk_config_hash"], "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_execution_desk_contract_hash"], "write_back": False,
        "result_hash": sha256_json(result),
    }
    return _round_nested({"identity": identity, **result})


def resolve_oil_execution_desk_profile(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    if profile is None:
        return build_default_oil_execution_desk_profile()
    supplied = dict(profile)
    appointment = dict(supplied.get("appointment", {}))
    rebuilt = _pack(
        personnel_id=str(appointment.get("personnel_id", "")),
        display_name=str(appointment.get("display_name", "")),
        capability_radar=supplied.get("capability_radar", {}),
        execution_style=supplied.get("execution_style", {}),
        candidate_index=appointment.get("candidate_index"), generation_seed=appointment.get("generation_seed"),
        source=str(appointment.get("source", "appointed_profile")),
    )
    if supplied.get("profile_hash") is not None and str(supplied["profile_hash"]) != rebuilt["profile_hash"]:
        raise ValueError("oil execution profile was modified after generation")
    return rebuilt


def resolve_oil_execution_runtime_policy(profile: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    canonical = resolve_oil_execution_desk_profile(profile)
    return canonical, dict(canonical["resolved_policy"])
