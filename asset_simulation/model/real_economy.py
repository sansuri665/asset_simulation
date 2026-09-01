"""Compact actual/potential GDP and output-gap block."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal


def cycle_phase(cycle: float, momentum: float) -> str:
    """Describe the cycle without feeding the label back into the equations."""

    if abs(cycle) < 0.18 and abs(momentum) < 0.14:
        return "neutral"
    if cycle < 0.0 and momentum >= 0.0:
        return "recovery"
    if cycle >= 0.0 and momentum >= 0.0:
        return "expansion"
    if cycle > 0.0 and momentum < 0.0:
        return "late_cycle"
    return "contraction"


def initial_state(config: Mapping[str, Any]) -> dict[str, float]:
    initial = config["initial_conditions"]
    real_gdp = float(initial["real_gdp_trillion_usd"])
    gap = float(initial["output_gap_pct"])
    return {
        "real_gdp_trillion_usd": real_gdp,
        "potential_gdp_trillion_usd": real_gdp / math.exp(gap / 100.0),
        "potential_growth_pct": float(initial["potential_growth_pct"]),
        "productivity_trend_gap_pct": float(initial["productivity_trend_gap_pct"]),
        "ordinary_cycle_index": float(initial["ordinary_cycle_index"]),
        "ordinary_cycle_momentum_index": float(initial["ordinary_cycle_momentum_index"]),
        "output_gap_pct": gap,
    }


def step(
    state: Mapping[str, float],
    previous: Mapping[str, float],
    *,
    seed: int,
    year_index: int,
    config: Mapping[str, Any],
    impulses: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    anchors = config["anchors"]
    bounds = config["bounds"]
    potential_news = normal(seed, "potential_growth", year_index)
    cycle_news = normal(seed, "ordinary_cycle", year_index)
    impulses = {} if impulses is None else impulses

    productivity_gap = 0.94 * state["productivity_trend_gap_pct"] + 0.018 * potential_news
    potential_growth = clamp(
        0.94 * state["potential_growth_pct"]
        + 0.06 * float(anchors["potential_growth_pct"])
        + 0.35 * productivity_gap
        + float(impulses.get("potential_growth_impulse_pp", 0.0)),
        *map(float, bounds["potential_growth_pct"]),
    )
    real_policy_gap = float(previous["real_policy_rate_pct"]) - float(
        previous["neutral_real_policy_rate_pct"]
    )
    credit_gap = (float(previous["global_high_yield_spread_bps"]) - 420.0) / 100.0
    liquidity_gap = (float(previous["global_funding_liquidity_index"]) - 55.0) / 10.0
    energy_gap = (float(previous["energy_cost_pressure_index"]) - 50.0) / 10.0
    cycle_forcing = (
        - 0.12 * real_policy_gap
        - 0.08 * credit_gap
        + 0.08 * liquidity_gap
        - 0.07 * energy_gap
        + 1.08 * cycle_news
        + float(impulses.get("demand_growth_impulse_pp", 0.0))
    )
    cycle_momentum = clamp(
        0.55 * state["ordinary_cycle_momentum_index"]
        - 0.20 * state["ordinary_cycle_index"]
        + cycle_forcing,
        *map(float, bounds["ordinary_cycle_momentum_index"]),
    )
    ordinary_cycle = clamp(
        0.72 * state["ordinary_cycle_index"] + cycle_momentum,
        *map(float, bounds["ordinary_cycle_index"]),
    )
    previous_gap = state["output_gap_pct"]
    output_gap = clamp(
        0.68 * previous_gap + 0.58 * ordinary_cycle,
        *map(float, bounds["output_gap_pct"]),
    )
    potential_gdp = state["potential_gdp_trillion_usd"] * (1.0 + potential_growth / 100.0)
    real_gdp = potential_gdp * math.exp(output_gap / 100.0)
    realized_growth = (real_gdp / state["real_gdp_trillion_usd"] - 1.0) * 100.0

    next_state = {
        "real_gdp_trillion_usd": real_gdp,
        "potential_gdp_trillion_usd": potential_gdp,
        "potential_growth_pct": potential_growth,
        "productivity_trend_gap_pct": productivity_gap,
        "ordinary_cycle_index": ordinary_cycle,
        "ordinary_cycle_momentum_index": cycle_momentum,
        "output_gap_pct": output_gap,
    }
    diagnostics = {
        "potential_growth_news_pp": 0.018 * potential_news,
        "ordinary_cycle_news": 1.08 * cycle_news,
        "ordinary_cycle_forcing_index": cycle_forcing,
        "ordinary_cycle_growth_contribution_pct": output_gap - previous_gap,
        "ordinary_cycle_phase": cycle_phase(ordinary_cycle, cycle_momentum),
        "real_policy_gap_input_pct": real_policy_gap,
        "credit_gap_input": credit_gap,
        "liquidity_gap_input": liquidity_gap,
        "energy_gap_input": energy_gap,
        "gdp_level_identity_residual": real_gdp - potential_gdp * math.exp(output_gap / 100.0),
    }
    return next_state, {**diagnostics, "realized_growth_pct": realized_growth}
