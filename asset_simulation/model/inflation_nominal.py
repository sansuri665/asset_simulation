"""Compact inflation, price-level and nominal-GDP block."""

from __future__ import annotations

from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal


def initial_state(config: Mapping[str, Any]) -> dict[str, float]:
    initial = config["initial_conditions"]
    return {
        "headline_inflation_pct": float(initial["headline_inflation_pct"]),
        "core_inflation_pct": float(initial["core_inflation_pct"]),
        "inflation_expectation_pct": float(initial["inflation_expectation_pct"]),
        "food_energy_inflation_gap_pct": float(initial["food_energy_inflation_gap_pct"]),
        "inflation_supply_pressure_index": float(
            initial.get("inflation_supply_pressure_index", 0.0)
        ),
        "cpi_price_level_index": float(initial["cpi_price_level_index"]),
        "gdp_deflator_price_level_index": float(initial["gdp_deflator_price_level_index"]),
    }


def step(
    state: Mapping[str, float],
    real: Mapping[str, float],
    previous: Mapping[str, float],
    *,
    seed: int,
    year_index: int,
    config: Mapping[str, Any],
    impulses: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    target = float(config["anchors"]["inflation_target_pct"])
    bounds = config["bounds"]
    core_news = normal(seed, "core_inflation", year_index)
    food_energy_news = normal(seed, "food_energy_inflation", year_index)
    supply_news = normal(seed, "inflation_supply_pressure", year_index)
    impulses = {} if impulses is None else impulses
    inflation_impulse = float(impulses.get("inflation_impulse_pp", 0.0))

    supply_pressure = clamp(
        0.62 * state["inflation_supply_pressure_index"]
        + 0.030 * float(previous["real_oil_yoy_change_pct"])
        + 0.018 * float(previous["real_commodity_yoy_change_pct"])
        + 0.52 * supply_news,
        -3.5,
        4.5,
    )
    expectation = clamp(
        0.84 * state["inflation_expectation_pct"]
        + 0.16 * target
        + 0.050 * (state["headline_inflation_pct"] - target)
        + 0.020 * supply_pressure,
        *map(float, bounds["inflation_expectation_pct"]),
    )
    import_pressure = -0.045 * float(previous["dollar_yoy_change_pct"])
    commodity_pressure = 0.026 * float(previous["real_commodity_yoy_change_pct"])
    core_target = (
        expectation
        + 0.22 * float(real["output_gap_pct"])
        + import_pressure
        + commodity_pressure
        + 0.20 * supply_pressure
        + 0.35 * inflation_impulse
    )
    core = clamp(
        0.58 * state["core_inflation_pct"] + 0.42 * core_target + 0.19 * core_news,
        *map(float, bounds["core_inflation_pct"]),
    )
    food_energy_gap = clamp(
        0.58 * state["food_energy_inflation_gap_pct"]
        + 0.065 * float(previous["real_oil_yoy_change_pct"])
        + 0.024 * float(previous["real_commodity_yoy_change_pct"])
        + 0.16 * supply_pressure
        + 0.54 * food_energy_news,
        -3.5,
        5.0,
    )
    headline = clamp(
        core + 0.27 * food_energy_gap + 0.08 * supply_pressure + 0.65 * inflation_impulse,
        *map(float, bounds["headline_inflation_pct"]),
    )
    deflator_inflation = clamp(
        0.72 * core + 0.24 * headline - 0.012 * float(previous["dollar_yoy_change_pct"]),
        -0.5,
        7.0,
    )
    cpi_level = state["cpi_price_level_index"] * (1.0 + headline / 100.0)
    deflator_level = state["gdp_deflator_price_level_index"] * (
        1.0 + deflator_inflation / 100.0
    )
    nominal_gdp = float(real["real_gdp_trillion_usd"]) * deflator_level / 100.0

    next_state = {
        "headline_inflation_pct": headline,
        "core_inflation_pct": core,
        "inflation_expectation_pct": expectation,
        "food_energy_inflation_gap_pct": food_energy_gap,
        "inflation_supply_pressure_index": supply_pressure,
        "cpi_price_level_index": cpi_level,
        "gdp_deflator_price_level_index": deflator_level,
    }
    diagnostics = {
        "core_target_pct": core_target,
        "core_news_pp": 0.19 * core_news,
        "import_pressure_pp": import_pressure,
        "commodity_pressure_pp": commodity_pressure,
        "food_energy_news_pp": 0.54 * food_energy_news,
        "supply_pressure_news_index": 0.52 * supply_news,
        "inflation_supply_pressure_index": supply_pressure,
        "exogenous_inflation_impulse_pp": inflation_impulse,
        "gdp_deflator_inflation_pct": deflator_inflation,
        "global_nominal_gdp_trillion_usd": nominal_gdp,
        "nominal_gdp_identity_residual": nominal_gdp
        - float(real["real_gdp_trillion_usd"]) * deflator_level / 100.0,
    }
    return next_state, diagnostics
