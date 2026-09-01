"""First capital-market consumer of the compact global macro snapshot."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal


def initial_state(config: Mapping[str, Any]) -> dict[str, float]:
    initial = config["initial_conditions"]
    return {
        "earnings_index": float(initial["corporate_earnings_index"]),
        "corporate_profit_share_index": float(initial["corporate_profit_share_index"]),
        "equity_risk_premium_pct": float(initial["equity_risk_premium_pct"]),
        "equity_valuation_pe": float(initial["equity_valuation_pe"]),
        "equity_capitalization_index": float(initial["equity_capitalization_index"]),
        "equity_total_return_index": float(initial["equity_total_return_index"]),
        "sovereign_bond_wealth_index": float(initial["sovereign_bond_wealth_index"]),
    }


def step(
    state: Mapping[str, float],
    macro: Mapping[str, float],
    *,
    seed: int,
    year_index: int,
    config: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, float]]:
    bounds = config["bounds"]
    earnings_news = normal(seed, "asset_earnings", year_index)
    valuation_news = normal(seed, "asset_valuation", year_index)

    nominal_gdp_growth = (
        float(macro["global_nominal_gdp_trillion_usd"])
        / float(macro["previous_global_nominal_gdp_trillion_usd"])
        - 1.0
    ) * 100.0
    profit_share_change = clamp(
        -0.08 * (state["corporate_profit_share_index"] - 100.0)
        + 0.28 * float(macro["output_gap_pct"])
        - 0.003 * (float(macro["global_high_yield_spread_bps"]) - 420.0)
        - 0.04 * (float(macro["energy_cost_pressure_index"]) - 50.0)
        + 0.40 * earnings_news,
        -3.5,
        3.5,
    )
    profit_share = clamp(
        state["corporate_profit_share_index"] * (1.0 + profit_share_change / 100.0),
        *map(float, bounds["corporate_profit_share_index"]),
    )
    earnings_growth = (
        (1.0 + nominal_gdp_growth / 100.0)
        * profit_share
        / state["corporate_profit_share_index"]
        - 1.0
    ) * 100.0
    earnings = state["earnings_index"] * (1.0 + earnings_growth / 100.0)
    erp = clamp(
        0.78 * state["equity_risk_premium_pct"]
        + 0.22 * 4.5
        + 0.16 * float(macro["global_financial_conditions_index"])
        + 0.0016 * (float(macro["global_high_yield_spread_bps"]) - 420.0),
        *map(float, bounds["equity_risk_premium_pct"]),
    )
    valuation_log_target = (
        math.log(float(config["anchors"]["equity_valuation_pe"]))
        - 0.055 * (float(macro["global_real_10y_yield_pct"]) - 1.0)
        - 0.045 * (erp - 4.5)
        + 0.015 * (earnings_growth - 4.0)
    )
    valuation = clamp(
        math.exp(
            0.78 * math.log(state["equity_valuation_pe"])
            + 0.22 * valuation_log_target
            + 0.012 * valuation_news
        ),
        *map(float, bounds["equity_valuation_pe"]),
    )
    capitalization = earnings * valuation / float(config["anchors"]["equity_valuation_pe"])
    capitalization_growth = (capitalization / state["equity_capitalization_index"] - 1.0) * 100.0
    dividend_yield = (
        100.0
        * float(config["anchors"]["equity_payout_ratio"])
        * earnings
        / (
            float(config["anchors"]["equity_valuation_pe"])
            * state["equity_capitalization_index"]
        )
    )
    equity_total_return = capitalization_growth + dividend_yield
    equity_total_return_index = state["equity_total_return_index"] * (
        1.0 + equity_total_return / 100.0
    )
    yield_change = float(macro["global_10y_yield_pct"]) - float(macro["previous_global_10y_yield_pct"])
    bond_return = clamp(
        float(macro["previous_global_10y_yield_pct"]) - 7.2 * yield_change,
        -18.0,
        22.0,
    )
    bond_wealth = state["sovereign_bond_wealth_index"] * (1.0 + bond_return / 100.0)
    risk_appetite = clamp(
        50.0
        - 7.0 * float(macro["global_financial_conditions_index"])
        - 0.020 * (float(macro["global_high_yield_spread_bps"]) - 420.0)
        + 0.16 * (float(macro["credit_availability_index"]) - 58.0),
        10.0,
        90.0,
    )
    next_state = {
        "earnings_index": earnings,
        "corporate_profit_share_index": profit_share,
        "equity_risk_premium_pct": erp,
        "equity_valuation_pe": valuation,
        "equity_capitalization_index": capitalization,
        "equity_total_return_index": equity_total_return_index,
        "sovereign_bond_wealth_index": bond_wealth,
    }
    diagnostics = {
        "earnings_news_pp": 0.40 * earnings_news,
        "nominal_gdp_growth_pct": nominal_gdp_growth,
        "corporate_profit_share_change_pct": profit_share_change,
        "valuation_news_log": 0.012 * valuation_news,
        "earnings_growth_pct": earnings_growth,
        "capitalization_growth_pct": capitalization_growth,
        "equity_dividend_yield_pct": dividend_yield,
        "equity_total_return_growth_pct": equity_total_return,
        "bond_wealth_growth_pct": bond_return,
        "risk_appetite_index": risk_appetite,
    }
    return next_state, diagnostics
