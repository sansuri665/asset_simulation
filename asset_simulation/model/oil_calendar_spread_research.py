"""Strategy-type-specific PM style projection for short-horizon oil calendar spreads.

One appointed oil strategy-research person can express different but correlated
preferences in the mature directional strategy and in the calendar-spread
strategy.  This module derives a dedicated calendar-spread radar from the
existing appointment.  It does not create a second person, a hidden alpha score,
or a second construction-capability system.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .math_utils import clamp
from .oil_strategy_research import (
    STRATEGY_STYLE_DIMENSIONS,
    resolve_oil_strategy_research_profile,
    resolve_oil_strategy_runtime_policy,
)
from .random_stream import normal
from .registry import load_registered_assets, sha256_json


OIL_CALENDAR_SPREAD_RESEARCH_MODEL_VERSION = (
    "asset-simulation-oil-calendar-spread-research-v0.1.0"
)
OIL_CALENDAR_SPREAD_RESEARCH_CONTRACT_ID = "oil_calendar_spread_research_v1"
CALENDAR_SPREAD_STYLE_DIMENSIONS = (
    "curve_continuation_reversion",
    "forecast_vs_visible_curve",
    "dislocation_selectivity",
    "capital_deployment",
    "adjustment_tempo",
    "rebalance_activity",
    "holding_patience",
    "forecast_horizon",
)


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("calendar spread research profile contains a non-finite value")
        return round(value, 8)
    return value


def _validate_registered_assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_calendar_spread_research_config"]
    contract = assets["oil_calendar_spread_research_contract"]
    if config["model_version"] != OIL_CALENDAR_SPREAD_RESEARCH_MODEL_VERSION:
        raise ValueError("registered calendar spread research config version mismatch")
    if contract["contract_id"] != OIL_CALENDAR_SPREAD_RESEARCH_CONTRACT_ID:
        raise ValueError("registered calendar spread research contract id mismatch")
    if contract["model_version"] != OIL_CALENDAR_SPREAD_RESEARCH_MODEL_VERSION:
        raise ValueError("registered calendar spread research contract version mismatch")
    if tuple(config["style_dimensions"]) != CALENDAR_SPREAD_STYLE_DIMENSIONS:
        raise ValueError("calendar spread style dimensions are out of contract order")

    projection = dict(config["projection"])
    if tuple(projection["base_dimensions"]) != STRATEGY_STYLE_DIMENSIONS:
        raise ValueError("calendar spread projection base dimensions are invalid")
    floor = float(projection["floor"])
    center = float(projection["center"])
    ceiling = float(projection["ceiling"])
    if not 0.0 <= floor < center < ceiling <= 100.0:
        raise ValueError("calendar spread projection bounds are invalid")
    if float(projection["idiosyncratic_scale_points"]) < 0.0:
        raise ValueError("calendar spread projection idiosyncratic scale is invalid")
    loadings = dict(projection["loadings"])
    if set(loadings) != set(CALENDAR_SPREAD_STYLE_DIMENSIONS):
        raise ValueError("calendar spread projection does not cover its style radar")
    for dimension in CALENDAR_SPREAD_STYLE_DIMENSIONS:
        if set(loadings[dimension]) != set(STRATEGY_STYLE_DIMENSIONS):
            raise ValueError(
                f"calendar spread projection loadings are invalid for {dimension}"
            )
        if not all(math.isfinite(float(value)) for value in loadings[dimension].values()):
            raise ValueError("calendar spread projection loadings must be finite")

    adapter = dict(config["reference_axis_adapter"])
    expected_adapter = {
        "curve_continuation_reversion": "continuation_reversion",
        "dislocation_selectivity": "selectivity",
        "capital_deployment": "capital_deployment",
        "adjustment_tempo": "responsiveness",
        "rebalance_activity": "turnover_activity",
        "holding_patience": "holding_patience",
        "forecast_horizon": "forecast_horizon",
    }
    for source, target in expected_adapter.items():
        if adapter.get(source) != target:
            raise ValueError("calendar spread reference-axis adapter is invalid")
    if float(adapter["near_month_focus_fixed_score"]) != 50.0:
        raise ValueError("calendar spread near-month adapter must stay neutral")

    weights = config["calendar_spread_specific_mapping"]["forecast_component_weight"]
    score_0 = float(weights["score_0"])
    score_50 = float(weights["score_50"])
    score_100 = float(weights["score_100"])
    if not 0.0 <= score_0 <= score_50 <= score_100 <= 1.0:
        raise ValueError("calendar spread forecast-component mapping is invalid")
    if not math.isclose(score_50, 0.70, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("calendar spread neutral forecast weight must preserve v0.1.2")
    return assets, config, contract


def _piecewise_score_anchors(anchors: Mapping[str, Any], score: float) -> float:
    normalized = clamp(float(score) / 100.0, 0.0, 1.0)
    score_0 = float(anchors["score_0"])
    score_50 = float(anchors["score_50"])
    score_100 = float(anchors["score_100"])
    if normalized <= 0.5:
        return score_0 + normalized * 2.0 * (score_50 - score_0)
    return score_50 + (normalized - 0.5) * 2.0 * (score_100 - score_50)


def _style_tags(radar: Mapping[str, float], config: Mapping[str, Any]) -> list[str]:
    thresholds = dict(config["style_tags"])
    high = float(thresholds["high_threshold"])
    low = float(thresholds["low_threshold"])
    labels = {
        "curve_continuation_reversion": ("价差顺势", "价差回归"),
        "forecast_vs_visible_curve": ("预测主导", "曲线证据主导"),
        "dislocation_selectivity": ("等待大偏离", "广泛参与"),
        "capital_deployment": ("积极部署", "保守部署"),
        "adjustment_tempo": ("快速调整", "缓慢调整"),
        "rebalance_activity": ("主动再平衡", "低频再平衡"),
        "holding_patience": ("耐心持有", "快速兑现"),
        "forecast_horizon": ("偏四周", "偏两周"),
    }
    ordered = sorted(
        CALENDAR_SPREAD_STYLE_DIMENSIONS,
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
    return tags or ["价差均衡"]


def _is_default_compatibility_appointment(source_profile: Mapping[str, Any]) -> bool:
    appointment = dict(source_profile["appointment"])
    radar = dict(source_profile["style_radar"])
    return (
        appointment.get("personnel_id") == "default_oil_strategy_director"
        and appointment.get("candidate_index") is None
        and all(math.isclose(float(radar[key]), 50.0) for key in STRATEGY_STYLE_DIMENSIONS)
    )


def _project_style_radar(
    source_profile: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, float]:
    projection = dict(config["projection"])
    center = float(projection["center"])
    floor = float(projection["floor"])
    ceiling = float(projection["ceiling"])
    if bool(projection["default_appointment_is_exactly_neutral"]) and _is_default_compatibility_appointment(source_profile):
        return {key: center for key in CALENDAR_SPREAD_STYLE_DIMENSIONS}

    source_radar = {key: float(value) for key, value in source_profile["style_radar"].items()}
    seed = int(str(source_profile["profile_hash"])[:16], 16)
    scale = float(projection["idiosyncratic_scale_points"])
    result: dict[str, float] = {}
    for index, dimension in enumerate(CALENDAR_SPREAD_STYLE_DIMENSIONS):
        loading = dict(projection["loadings"][dimension])
        structural = sum(
            float(loading[key]) * (source_radar[key] - center)
            for key in STRATEGY_STYLE_DIMENSIONS
        )
        draw = normal(
            seed,
            f"oil_calendar_spread_research.style_projection.{dimension}",
            index,
        )
        idiosyncratic = scale * math.tanh(draw)
        result[dimension] = round(
            clamp(center + structural + idiosyncratic, floor, ceiling), 2
        )
    return result


def _reference_style_radar(
    dedicated_radar: Mapping[str, float], config: Mapping[str, Any]
) -> dict[str, float]:
    adapter = dict(config["reference_axis_adapter"])
    result = {
        "continuation_reversion": float(dedicated_radar["curve_continuation_reversion"]),
        "capital_deployment": float(dedicated_radar["capital_deployment"]),
        "responsiveness": float(dedicated_radar["adjustment_tempo"]),
        "selectivity": float(dedicated_radar["dislocation_selectivity"]),
        "turnover_activity": float(dedicated_radar["rebalance_activity"]),
        "holding_patience": float(dedicated_radar["holding_patience"]),
        "near_month_focus": float(adapter["near_month_focus_fixed_score"]),
        "forecast_horizon": float(dedicated_radar["forecast_horizon"]),
    }
    if set(result) != set(STRATEGY_STYLE_DIMENSIONS):
        raise ValueError("calendar spread reference radar is incomplete")
    return result


def build_oil_calendar_spread_reference_profile(
    source_profile: Mapping[str, Any],
    dedicated_radar: Mapping[str, float],
) -> dict[str, Any]:
    """Build an unhashed compatibility profile for the hardened spread primitives."""

    canonical = resolve_oil_strategy_research_profile(source_profile)
    _, config, _ = _validate_registered_assets()
    return {
        "appointment": {
            **dict(canonical["appointment"]),
            "source": "calendar_spread_style_projection",
        },
        "style_radar": _reference_style_radar(dedicated_radar, config),
        "construction_capability_radar": dict(canonical["construction_capability_radar"]),
    }


def resolve_oil_calendar_spread_research_profile(
    profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Derive one deterministic calendar-spread style profile from an appointment."""

    assets, config, contract = _validate_registered_assets()
    source = resolve_oil_strategy_research_profile(profile)
    radar = _project_style_radar(source, config)
    reference_profile = build_oil_calendar_spread_reference_profile(source, radar)
    _, reference_policy = resolve_oil_strategy_runtime_policy(reference_profile)

    forecast_weight = _piecewise_score_anchors(
        config["calendar_spread_specific_mapping"]["forecast_component_weight"],
        radar["forecast_vs_visible_curve"],
    )
    visible_weight = 1.0 - forecast_weight
    policy = {
        "signal": {
            **dict(reference_policy["signal"]),
            "forecast_component_weight": forecast_weight,
            "visible_curve_component_weight": visible_weight,
            "forecast_vs_visible_curve_score": float(radar["forecast_vs_visible_curve"]),
            "component_mix_owner": "oil_calendar_spread_research_v1",
        },
        "risk": dict(reference_policy["risk"]),
        "execution": dict(reference_policy["execution"]),
        "construction": dict(reference_policy["construction"]),
    }
    result = {
        "schemaVersion": "asset-simulation-oil-calendar-spread-research-profile-v1",
        "appointment": dict(source["appointment"]),
        "strategy_id": str(config["strategy_id"]),
        "source_strategy_profile_hash": str(source["profile_hash"]),
        "style_radar": radar,
        "style_tags": _style_tags(radar, config),
        "preference_total_score": None,
        "alpha_score": None,
        "construction_capability_radar": dict(source["construction_capability_radar"]),
        "construction_capability_owner": "oil_strategy_research_v2",
        "reference_style_radar": dict(reference_profile["style_radar"]),
        "resolved_policy": policy,
        "governance": {
            "derived_from_appointed_personnel": True,
            "player_can_edit_dedicated_radar": False,
            "higher_score_is_better": False,
            "preference_total_score_available": False,
            "alpha_score_available": False,
            "construction_capability_duplicated": False,
            "execution_ability_used": False,
            "hidden_future_used": False,
            "realized_pnl_used": False,
        },
        "identity": {
            "model_version": OIL_CALENDAR_SPREAD_RESEARCH_MODEL_VERSION,
            "config_id": str(config["config_id"]),
            "config_hash": assets["oil_calendar_spread_research_config_hash"],
            "field_contract_id": str(contract["contract_id"]),
            "field_contract_hash": assets["oil_calendar_spread_research_contract_hash"],
            "source_strategy_profile_hash": str(source["profile_hash"]),
            "write_back": False,
        },
    }
    result["profile_hash"] = sha256_json(result)
    return _round_nested(result)


def resolve_oil_calendar_spread_runtime_policy(
    profile: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return dedicated profile, runtime policy and hardened-engine adapter profile."""

    dedicated = resolve_oil_calendar_spread_research_profile(profile)
    source = resolve_oil_strategy_research_profile(profile)
    reference_profile = build_oil_calendar_spread_reference_profile(
        source, dedicated["style_radar"]
    )
    return dedicated, dict(dedicated["resolved_policy"]), reference_profile
