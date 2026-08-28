"""Policy-rate and policy-expectations-plus-term-premium curve block."""

from __future__ import annotations

from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal


def initial_state(config: Mapping[str, Any]) -> dict[str, float]:
    initial = config["initial_conditions"]
    return {
        "policy_rate_pct": float(initial["policy_rate_pct"]),
        "neutral_real_policy_rate_pct": float(initial["neutral_real_policy_rate_pct"]),
        "expected_short_rate_2y_pct": float(initial["expected_short_rate_2y_pct"]),
        "expected_short_rate_10y_pct": float(initial["expected_short_rate_10y_pct"]),
        "term_premium_10y_pct": float(initial["term_premium_10y_pct"]),
        "policy_rate_change_pct": float(initial.get("policy_rate_change_pct", 0.0)),
    }


def step(
    state: Mapping[str, float],
    real: Mapping[str, float],
    inflation: Mapping[str, float],
    previous: Mapping[str, float],
    *,
    seed: int,
    year_index: int,
    config: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, float]]:
    anchors = config["anchors"]
    bounds = config["bounds"]
    target_inflation = float(anchors["inflation_target_pct"])
    neutral_news = normal(seed, "neutral_real_rate", year_index)
    term_news = normal(seed, "term_premium", year_index)

    neutral_real = clamp(
        0.96 * state["neutral_real_policy_rate_pct"]
        + 0.04 * float(anchors["neutral_real_policy_rate_pct"])
        + 0.015 * neutral_news,
        0.25,
        2.0,
    )
    policy_target = (
        neutral_real
        + float(inflation["inflation_expectation_pct"])
        + 0.58 * (float(inflation["core_inflation_pct"]) - target_inflation)
        + 0.30 * float(real["output_gap_pct"])
        + 0.10
        * (float(inflation["headline_inflation_pct"]) - float(inflation["core_inflation_pct"]))
    )
    policy_gap = policy_target - state["policy_rate_pct"]
    desired_change = 0.46 * policy_gap + 0.18 * state.get("policy_rate_change_pct", 0.0)
    if abs(policy_gap) < 0.20:
        desired_change = 0.0
    policy_change = clamp(desired_change, -1.50, 1.25)
    policy = clamp(
        state["policy_rate_pct"] + policy_change,
        *map(float, bounds["policy_rate_pct"]),
    )
    nominal_neutral = neutral_real + float(inflation["inflation_expectation_pct"])
    expected_2y_target = 0.78 * policy + 0.22 * policy_target
    expected_2y = 0.35 * state["expected_short_rate_2y_pct"] + 0.65 * expected_2y_target
    expected_10y_target = nominal_neutral + 0.08 * float(real["output_gap_pct"])
    expected_10y = 0.56 * state["expected_short_rate_10y_pct"] + 0.44 * expected_10y_target
    term_premium = clamp(
        0.70 * state["term_premium_10y_pct"]
        + 0.30 * float(anchors["term_premium_10y_pct"])
        + 0.085 * (float(inflation["inflation_expectation_pct"]) - target_inflation)
        - 0.055 * float(previous["global_financial_conditions_index"])
        + 0.28 * term_news,
        *map(float, bounds["term_premium_10y_pct"]),
    )
    term_premium_2y = 0.22 * term_premium
    yield_2y = expected_2y + term_premium_2y
    yield_10y = expected_10y + term_premium
    real_policy = policy - float(inflation["inflation_expectation_pct"])
    real_10y = yield_10y - float(inflation["inflation_expectation_pct"])

    next_state = {
        "policy_rate_pct": policy,
        "neutral_real_policy_rate_pct": neutral_real,
        "expected_short_rate_2y_pct": expected_2y,
        "expected_short_rate_10y_pct": expected_10y,
        "term_premium_10y_pct": term_premium,
        "policy_rate_change_pct": policy - state["policy_rate_pct"],
    }
    diagnostics = {
        "policy_target_pct": policy_target,
        "policy_gap_pct": policy_gap,
        "policy_rate_change_pct": policy - state["policy_rate_pct"],
        "term_premium_news_pct": 0.28 * term_news,
        "global_policy_rate_pct": policy,
        "real_policy_rate_pct": real_policy,
        "global_2y_yield_pct": yield_2y,
        "global_10y_yield_pct": yield_10y,
        "global_real_10y_yield_pct": real_10y,
        "term_spread_10y_2y_pct": yield_10y - yield_2y,
        "yield_2y_identity_residual": yield_2y - expected_2y - term_premium_2y,
        "yield_10y_identity_residual": yield_10y - expected_10y - term_premium,
    }
    return next_state, diagnostics
