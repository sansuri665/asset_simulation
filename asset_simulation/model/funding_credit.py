"""Compact dollar, upstream liquidity, credit and final FCI block."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal


def initial_state(config: Mapping[str, Any]) -> dict[str, float]:
    initial = config["initial_conditions"]
    return {
        "dollar_index": float(initial["dollar_index"]),
        "dollar_funding_stress_index": float(initial["dollar_funding_stress_index"]),
        "funding_liquidity_index": float(initial["funding_liquidity_index"]),
        "ig_spread_bps": float(initial["ig_spread_bps"]),
        "hy_spread_bps": float(initial["hy_spread_bps"]),
        "credit_availability_index": float(initial["credit_availability_index"]),
        "default_risk_index": float(initial["default_risk_index"]),
        "financial_conditions_index": float(initial["financial_conditions_index"]),
    }


def step(
    state: Mapping[str, float],
    real: Mapping[str, float],
    inflation: Mapping[str, float],
    rates: Mapping[str, float],
    *,
    seed: int,
    year_index: int,
    config: Mapping[str, Any],
    impulses: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    anchors = config["anchors"]
    bounds = config["bounds"]
    dollar_news = normal(seed, "dollar_value", year_index)
    funding_news = normal(seed, "funding_liquidity", year_index)
    credit_news = normal(seed, "credit_risk", year_index)
    impulses = {} if impulses is None else impulses
    funding_impulse = float(impulses.get("dollar_funding_impulse_index", 0.0))
    spread_impulse = float(impulses.get("credit_spread_impulse_bps", 0.0))

    real_policy_gap = float(rates["real_policy_rate_pct"]) - float(
        rates["neutral_real_policy_rate_pct"]
    )
    growth_gap = float(real["realized_growth_pct"]) - float(real["potential_growth_pct"])
    dollar_log_gap = 100.0 * math.log(state["dollar_index"] / float(anchors["dollar_index"]))
    dollar_log_change = (
        -0.14 * dollar_log_gap
        + 0.20 * real_policy_gap
        - 0.12 * growth_gap
        + 1.35 * dollar_news
    )
    dollar = clamp(
        state["dollar_index"] * math.exp(dollar_log_change / 100.0),
        *map(float, bounds["dollar_index"]),
    )
    dollar_yoy = (dollar / state["dollar_index"] - 1.0) * 100.0

    funding_stress = clamp(
        0.74 * state["dollar_funding_stress_index"]
        + 0.26 * float(anchors["funding_stress_index"])
        + 0.48 * dollar_yoy
        + 1.10 * real_policy_gap
        - 1.30 * float(real["output_gap_pct"])
        + 1.8 * funding_news
        + funding_impulse,
        *map(float, bounds["dollar_funding_stress_index"]),
    )
    liquidity = clamp(
        0.76 * state["funding_liquidity_index"]
        + 0.24 * float(anchors["funding_liquidity_index"])
        - 0.24 * (funding_stress - float(anchors["funding_stress_index"]))
        - 1.30 * real_policy_gap
        + 1.10 * funding_news
        - 0.55 * funding_impulse,
        *map(float, bounds["funding_liquidity_index"]),
    )
    default_risk = clamp(
        0.80 * state["default_risk_index"]
        + 0.20 * 30.0
        - 1.75 * float(real["output_gap_pct"])
        + 0.75 * real_policy_gap
        + 0.10 * max(0.0, funding_stress - 35.0)
        + 1.4 * credit_news,
        *map(float, bounds["default_risk_index"]),
    )
    availability = clamp(
        0.72 * state["credit_availability_index"]
        + 0.28 * 58.0
        + 0.15 * (liquidity - 55.0)
        - 0.11 * (funding_stress - 35.0)
        - 0.13 * (default_risk - 30.0)
        - 1.0 * credit_news,
        *map(float, bounds["credit_availability_index"]),
    )
    common_credit = (
        2.0 * (default_risk - 30.0)
        + 1.15 * (funding_stress - 35.0)
        - 1.2 * (availability - 58.0)
        + 4.5 * credit_news
    )
    ig = clamp(
        0.78 * state["ig_spread_bps"]
        + 0.22 * float(anchors["ig_spread_bps"])
        + 0.20 * common_credit
        + 0.30 * spread_impulse,
        *map(float, bounds["ig_spread_bps"]),
    )
    hy = clamp(
        0.78 * state["hy_spread_bps"]
        + 0.22 * float(anchors["hy_spread_bps"])
        + 0.78 * common_credit
        + spread_impulse,
        *map(float, bounds["hy_spread_bps"]),
    )
    hy = max(hy, ig + 140.0)
    fci = clamp(
        0.42 * real_policy_gap
        + 0.22 * (float(rates["global_real_10y_yield_pct"]) - 1.0)
        + 0.018 * (funding_stress - 35.0)
        - 0.015 * (liquidity - 55.0)
        + 0.0030 * (hy - 420.0)
        + 0.08 * max(0.0, dollar_yoy),
        *map(float, bounds["financial_conditions_index"]),
    )

    next_state = {
        "dollar_index": dollar,
        "dollar_funding_stress_index": funding_stress,
        "funding_liquidity_index": liquidity,
        "ig_spread_bps": ig,
        "hy_spread_bps": hy,
        "credit_availability_index": availability,
        "default_risk_index": default_risk,
        "financial_conditions_index": fci,
    }
    diagnostics = {
        "dollar_news_log_pct": 1.35 * dollar_news,
        "funding_news_index": 1.8 * funding_news,
        "credit_news_index": 1.4 * credit_news,
        "exogenous_funding_impulse_index": funding_impulse,
        "exogenous_credit_spread_impulse_bps": spread_impulse,
        "common_credit_pressure_bps": common_credit,
        "dollar_yoy_change_pct": dollar_yoy,
    }
    return next_state, diagnostics
