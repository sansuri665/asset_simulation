"""Hidden forecast-research personnel architecture.

This module owns only candidate/personnel generation and the mapping from
research preferences into forecast behaviour.  It does not own market prices,
future truth, forecast scoring, recruitment presentation, or strategy sizing.

The game-facing recruitment layer is intentionally separate: capability/style
numbers here are runtime state and are not a promise that raw scores will be
shown to the player.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal


FORECAST_RESEARCH_STYLE_DIMENSIONS = (
    "trend_reversion_bias",
    "fundamental_market_bias",
    "confirmation_lead_bias",
    "confidence_style",
    "revision_style",
)


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(float(value) / math.sqrt(2.0)))


def _bounded_latent(
    *,
    seed: int,
    address: str,
    trait: str,
    lane: int,
    clip_sigma: float,
) -> float:
    return clamp(
        normal(int(seed), f"{address}.{trait}", int(lane)),
        -float(clip_sigma),
        float(clip_sigma),
    )


def _weighted_mean(
    values: Mapping[str, float],
    weights: Mapping[str, float],
) -> float:
    denominator = sum(float(weights[key]) for key in values)
    if denominator <= 0.0:
        raise ValueError("forecast research weights must be positive")
    return (
        sum(float(weights[key]) * float(values[key]) for key in values)
        / denominator
    )


def _recenter_capability_radar(
    radar: dict[str, float],
    *,
    target_total: float,
    weights: Mapping[str, float],
    floor: float,
    ceiling: float,
) -> dict[str, float]:
    """Preserve the requested broad ability band after specialization clipping."""

    result = dict(radar)
    for _ in range(16):
        current = _weighted_mean(result, weights)
        delta = float(target_total) - current
        if abs(delta) < 1e-10:
            break
        adjustable = [
            key
            for key, value in result.items()
            if (delta > 0.0 and value < ceiling - 1e-12)
            or (delta < 0.0 and value > floor + 1e-12)
        ]
        if not adjustable:
            break
        adjustable_weight = sum(float(weights[key]) for key in adjustable)
        if adjustable_weight <= 0.0:
            break
        shift = delta * sum(float(weights[key]) for key in result) / adjustable_weight
        for key in adjustable:
            result[key] = clamp(result[key] + shift, floor, ceiling)
    return result


def validate_research_style(
    research_style: Mapping[str, float] | None,
    *,
    default_style: Mapping[str, float] | None = None,
) -> dict[str, float]:
    source = (
        {key: 50.0 for key in FORECAST_RESEARCH_STYLE_DIMENSIONS}
        if research_style is None and default_style is None
        else dict(default_style if research_style is None else research_style)
    )
    unknown = set(source) - set(FORECAST_RESEARCH_STYLE_DIMENSIONS)
    if unknown:
        raise KeyError(
            f"unknown forecast research style dimensions: {sorted(unknown)}"
        )
    result: dict[str, float] = {}
    for key in FORECAST_RESEARCH_STYLE_DIMENSIONS:
        if key not in source:
            raise ValueError(f"forecast research style {key} is required")
        value = float(source[key])
        if not math.isfinite(value) or not 0.0 <= value <= 100.0:
            raise ValueError(
                f"forecast research style {key} must be between 0 and 100"
            )
        result[key] = value
    return result


def generate_forecast_research_profile(
    *,
    seed: int,
    score_min: float,
    score_max: float,
    capability_dimensions: tuple[str, ...],
    weights: Mapping[str, float],
    profile_generation: Mapping[str, Any],
    style_generation: Mapping[str, Any],
) -> dict[str, Any]:
    """Generate correlated hidden capabilities plus independent research style.

    The requested score range is retained only as a broad compatibility
    constraint for the current synthetic-demo API.  It is not the player-facing
    recruitment mechanic and does not force the six specialties to remain near
    one another.
    """

    lower = float(score_min)
    upper = float(score_max)
    if not math.isfinite(lower) or not math.isfinite(upper):
        raise ValueError("oil forecast requested score range must be finite")
    if lower < float(profile_generation["minimum_total_score"]) or upper > float(
        profile_generation["maximum_total_score"]
    ):
        raise ValueError(
            "oil forecast requested score range must stay between 0 and 100"
        )
    if lower > upper:
        raise ValueError(
            "oil forecast requested score minimum must not exceed maximum"
        )

    address = f"oil_short_forecast.profile.{lower:.2f}.{upper:.2f}"
    target_total = lower + (upper - lower) * _normal_cdf(
        normal(int(seed), f"{address}.total", 0)
    )

    latent_traits = tuple(profile_generation["latent_traits"])
    clip_sigma = float(profile_generation["latent_clip_sigma"])
    capability_latent = {
        trait: _bounded_latent(
            seed=int(seed),
            address=f"{address}.capability_latent",
            trait=str(trait),
            lane=index,
            clip_sigma=clip_sigma,
        )
        for index, trait in enumerate(latent_traits)
    }
    loadings = profile_generation["latent_loadings"]
    idiosyncratic_scale = float(
        profile_generation["idiosyncratic_scale_points"]
    )
    raw_offsets = {}
    for index, dimension in enumerate(capability_dimensions):
        dimension_loadings = loadings[dimension]
        raw_offsets[dimension] = sum(
            float(dimension_loadings.get(trait, 0.0))
            * capability_latent[trait]
            for trait in latent_traits
        ) + idiosyncratic_scale * normal(
            int(seed),
            f"{address}.capability_idio.{dimension}",
            index,
        )

    offset_center = _weighted_mean(raw_offsets, weights)
    maximum_distance = float(
        profile_generation["maximum_dimension_distance_points"]
    )
    floor = max(0.0, target_total - maximum_distance)
    ceiling = min(100.0, target_total + maximum_distance)
    radar = {
        dimension: clamp(
            target_total + raw_offsets[dimension] - offset_center,
            floor,
            ceiling,
        )
        for dimension in capability_dimensions
    }
    radar = _recenter_capability_radar(
        radar,
        target_total=target_total,
        weights=weights,
        floor=floor,
        ceiling=ceiling,
    )

    style_traits = tuple(style_generation["latent_traits"])
    style_clip = float(style_generation["latent_clip_sigma"])
    style_latent = {
        trait: _bounded_latent(
            seed=int(seed),
            address=f"{address}.style_latent",
            trait=str(trait),
            lane=index,
            clip_sigma=style_clip,
        )
        for index, trait in enumerate(style_traits)
    }
    style_loadings = style_generation["latent_loadings"]
    style_idio = float(style_generation["idiosyncratic_scale_points"])
    style_floor = float(style_generation["dimension_floor"])
    style_ceiling = float(style_generation["dimension_ceiling"])
    research_style = {}
    for index, dimension in enumerate(FORECAST_RESEARCH_STYLE_DIMENSIONS):
        dimension_loadings = style_loadings[dimension]
        value = 50.0 + sum(
            float(dimension_loadings.get(trait, 0.0)) * style_latent[trait]
            for trait in style_traits
        ) + style_idio * normal(
            int(seed),
            f"{address}.style_idio.{dimension}",
            index,
        )
        research_style[dimension] = clamp(value, style_floor, style_ceiling)

    return {
        "target_total_score": target_total,
        "capability_radar": radar,
        "research_style": research_style,
    }


def research_behavior(
    profile: Mapping[str, Any],
    error_config: Mapping[str, Any],
) -> dict[str, float]:
    """Map neutral-at-50 preferences into the existing forecast behaviour knobs."""

    baseline = {
        key: float(value)
        for key, value in error_config["baseline_behavior"].items()
    }
    style = validate_research_style(profile.get("research_style"))
    centered = {
        key: (float(value) - 50.0) / 50.0
        for key, value in style.items()
    }

    trend = centered["trend_reversion_bias"]
    market = centered["fundamental_market_bias"]
    lead = centered["confirmation_lead_bias"]
    conviction = centered["confidence_style"]
    revision = centered["revision_style"]

    behavior = dict(baseline)
    behavior["trend_extrapolation"] = clamp(
        baseline["trend_extrapolation"] + 0.10 * trend + 0.04 * market,
        0.0,
        0.30,
    )
    behavior["mean_reversion"] = clamp(
        baseline["mean_reversion"] - 0.08 * trend - 0.03 * market,
        0.0,
        0.30,
    )
    behavior["timing_lead_weeks"] = clamp(
        baseline["timing_lead_weeks"] + 0.90 * lead,
        -1.50,
        1.50,
    )
    behavior["revision_speed"] = clamp(
        baseline["revision_speed"] * (1.0 + 0.35 * revision),
        0.55,
        1.45,
    )
    behavior["thesis_persistence"] = clamp(
        baseline["thesis_persistence"] - 0.20 * revision,
        0.20,
        0.85,
    )
    behavior["confidence_bias_pct"] = clamp(
        baseline["confidence_bias_pct"] - 0.70 * conviction,
        -1.0,
        1.0,
    )
    return behavior
