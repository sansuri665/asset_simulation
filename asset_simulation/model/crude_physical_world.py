"""Independent global crude-oil balance feeding the tanker trade network.

The upstream macro oil world remains a total-liquids system.  This module owns
the narrower refinery-feed crude system: crude production, refinery crude
runs, crude stocks, capacity and exact monthly mass conservation.
"""

from __future__ import annotations

import calendar
from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal


def initial_crude_state(config: Mapping[str, Any]) -> dict[str, float]:
    crude = config["crude_physical"]
    runs = float(crude["initial_refinery_runs_mbd"])
    production = float(crude["initial_production_mbd"])
    capacity = float(crude["initial_production_capacity_mbd"])
    return {
        "trend_refinery_runs_mbd": runs,
        "refinery_runs_mbd": runs,
        "refinery_operations_deviation_pct": 0.0,
        "production_mbd": production,
        "production_capacity_mbd": capacity,
        "production_investment_cycle_pct": 0.0,
        "production_utilization_cycle_pct": 0.0,
        "inventory_mmbbl": runs * float(crude["initial_inventory_days"]),
        "inventory_target_deviation_days": (
            float(crude["initial_inventory_days"])
            - float(crude["target_inventory_days"])
        ),
    }


def annual_crude_growth_targets(
    state: Mapping[str, float],
    *,
    seed: int,
    year_index: int,
    simulation_year: int,
    liquids_demand_growth_pct: float,
    lagged_real_oil_price_index: float,
    config: Mapping[str, Any],
) -> tuple[float, float, float]:
    """Form annual crude-run and production-capacity growth independently."""

    crude = config["crude_physical"]
    runs_config = crude["refinery_runs"]
    if year_index == 0:
        runs_growth = 0.0
    else:
        committed_runs_growth = (
            float(runs_config["near_term_committed_growth_pct"])
            if int(runs_config["near_term_committed_growth_start_year"])
            <= simulation_year
            <= int(runs_config["near_term_committed_growth_end_year"])
            else 0.0
        )
        runs_growth = clamp(
            float(runs_config["base_growth_pct"])
            + committed_runs_growth
            + float(runs_config["liquids_growth_response"])
            * float(liquids_demand_growth_pct)
            + float(runs_config["annual_news_scale_pct"])
            * normal(seed, "crude_annual_refinery_runs", year_index),
            *map(float, runs_config["annual_growth_bounds_pct"]),
        )

    capacity_config = crude["production_capacity"]
    investment_cycle = clamp(
        float(capacity_config["investment_cycle_persistence"])
        * float(state["production_investment_cycle_pct"])
        + float(capacity_config["investment_cycle_news_scale_pct"])
        * normal(seed, "crude_annual_production_investment", year_index),
        *map(float, capacity_config["investment_cycle_bounds_pct"]),
    )
    prior_utilization = (
        100.0
        * float(state["production_mbd"])
        / max(float(state["production_capacity_mbd"]), 1e-9)
    )
    committed_growth = (
        float(capacity_config["near_term_committed_growth_pct"])
        if int(capacity_config["near_term_committed_growth_start_year"])
        <= simulation_year
        <= int(capacity_config["near_term_committed_growth_end_year"])
        else 0.0
    )
    if year_index == 0:
        capacity_growth = 0.0
    else:
        capacity_growth = clamp(
            float(capacity_config["base_growth_pct"])
            + committed_growth
            + float(capacity_config["lagged_real_price_level_elasticity"])
            * (float(lagged_real_oil_price_index) - 100.0)
            + float(capacity_config["utilization_gap_growth_response"])
            * (
                prior_utilization
                - float(capacity_config["reference_utilization_pct"])
            )
            + investment_cycle
            + float(capacity_config["annual_news_scale_pct"])
            * normal(seed, "crude_annual_production_capacity", year_index),
            *map(float, capacity_config["annual_growth_bounds_pct"]),
        )
    return runs_growth, capacity_growth, investment_cycle


def advance_crude_turn(
    state: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    year: int,
    month: int,
    annual_refinery_runs_growth_pct: float,
    annual_capacity_growth_pct: float,
    investment_cycle_pct: float,
    config: Mapping[str, Any],
    impulse: Mapping[str, Any] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Advance one exact monthly refinery-feed crude balance."""

    impulse = {} if impulse is None else impulse
    crude = config["crude_physical"]
    runs_config = crude["refinery_runs"]
    production_config = crude["production"]
    days = calendar.monthrange(year, month)[1]

    trend_runs = float(state["trend_refinery_runs_mbd"])
    production_capacity = float(state["production_capacity_mbd"])
    if month == 1 and turn_index > 0:
        trend_runs *= 1.0 + float(annual_refinery_runs_growth_pct) / 100.0
        production_capacity *= 1.0 + float(annual_capacity_growth_pct) / 100.0
    trend_runs = clamp(
        trend_runs,
        *map(float, crude["bounds"]["refinery_runs_mbd"]),
    )
    production_capacity = clamp(
        production_capacity,
        *map(float, crude["bounds"]["production_capacity_mbd"]),
    )

    seasonal_profile = list(map(float, runs_config["monthly_profile_pct"]))
    if len(seasonal_profile) != 12:
        raise ValueError("crude refinery monthly profile must contain 12 values")
    operations_deviation = clamp(
        float(runs_config["operations_persistence"])
        * float(state["refinery_operations_deviation_pct"])
        + float(runs_config["operations_news_scale_pct"])
        * normal(seed, "crude_monthly_refinery_operations", turn_index),
        *map(float, runs_config["operations_bounds_pct"]),
    )
    desired_runs = trend_runs * (
        1.0 + (seasonal_profile[month - 1] + operations_deviation) / 100.0
    )
    run_change = clamp(
        float(runs_config["adjustment_speed"])
        * (desired_runs - float(state["refinery_runs_mbd"])),
        -float(runs_config["maximum_monthly_adjustment_mbd"]),
        float(runs_config["maximum_monthly_adjustment_mbd"]),
    )
    scheduled_runs = max(0.0, float(state["refinery_runs_mbd"]) + run_change)

    opening_inventory = float(state["inventory_mmbbl"])
    inventory_target_deviation = (
        float(crude["target_inventory_persistence"])
        * float(state["inventory_target_deviation_days"])
        + float(crude["target_inventory_news_scale_days"])
        * normal(seed, "crude_inventory_target", turn_index)
    )
    inventory_target_days = clamp(
        float(crude["target_inventory_days"]) + inventory_target_deviation,
        *map(float, crude["target_inventory_bounds_days"]),
    )
    inventory_target_deviation = (
        inventory_target_days - float(crude["target_inventory_days"])
    )
    opening_inventory_days = opening_inventory / max(trend_runs, 1e-9)
    inventory_pressure_pct = clamp(
        float(production_config["inventory_pressure_per_day_pct"])
        * (inventory_target_days - opening_inventory_days),
        *map(float, production_config["inventory_pressure_bounds_pct"]),
    )
    utilization_cycle = clamp(
        float(production_config["utilization_cycle_persistence"])
        * float(state["production_utilization_cycle_pct"])
        + float(production_config["utilization_cycle_news_scale_pct"])
        * normal(seed, "crude_monthly_production_utilization", turn_index),
        *map(float, production_config["utilization_cycle_bounds_pct"]),
    )
    target_utilization = clamp(
        float(production_config["base_target_utilization_pct"])
        + utilization_cycle
        + inventory_pressure_pct,
        *map(float, production_config["target_utilization_bounds_pct"]),
    )
    desired_production = production_capacity * target_utilization / 100.0
    production_change = clamp(
        float(production_config["adjustment_speed"])
        * (desired_production - float(state["production_mbd"])),
        -float(production_config["maximum_monthly_adjustment_mbd"]),
        float(production_config["maximum_monthly_adjustment_mbd"]),
    )
    production = float(state["production_mbd"]) + production_change
    outage_mbd = float(impulse.get("crude_production_outage_mbd", 0.0))
    if outage_mbd < 0.0:
        raise ValueError("crude_production_outage_mbd cannot be negative")
    production = clamp(
        production,
        0.0,
        max(0.0, production_capacity - outage_mbd),
    )

    storage_capacity = trend_runs * float(crude["storage_capacity_days"])
    provisional_inventory = opening_inventory + (production - scheduled_runs) * days
    if provisional_inventory > storage_capacity:
        production = max(
            0.0,
            production - (provisional_inventory - storage_capacity) / days,
        )
    available_volume = opening_inventory + production * days
    scheduled_volume = scheduled_runs * days
    realized_volume = min(scheduled_volume, max(0.0, available_volume))
    realized_runs = realized_volume / days
    unmet_runs = max(0.0, scheduled_volume - realized_volume)
    closing_inventory = opening_inventory + production * days - realized_volume
    if abs(closing_inventory) < 1e-10:
        closing_inventory = 0.0
    inventory_change = closing_inventory - opening_inventory
    mass_balance_residual = closing_inventory - (
        opening_inventory + production * days - realized_runs * days
    )

    next_state = {
        "trend_refinery_runs_mbd": trend_runs,
        "refinery_runs_mbd": realized_runs,
        "refinery_operations_deviation_pct": operations_deviation,
        "production_mbd": production,
        "production_capacity_mbd": production_capacity,
        "production_investment_cycle_pct": investment_cycle_pct,
        "production_utilization_cycle_pct": utilization_cycle,
        "inventory_mmbbl": closing_inventory,
        "inventory_target_deviation_days": inventory_target_deviation,
    }
    record = {
        "days": float(days),
        "crude_annual_refinery_runs_growth_target_pct": float(
            annual_refinery_runs_growth_pct
        ),
        "crude_annual_capacity_growth_target_pct": float(
            annual_capacity_growth_pct
        ),
        "crude_trend_refinery_runs_mbd": trend_runs,
        "crude_desired_refinery_runs_mbd": desired_runs,
        "crude_refinery_runs_mbd": realized_runs,
        "crude_refinery_runs_change_mbd": run_change,
        "crude_refinery_operations_deviation_pct": operations_deviation,
        "crude_production_mbd": production,
        "crude_production_capacity_mbd": production_capacity,
        "crude_spare_capacity_mbd": max(0.0, production_capacity - production),
        "crude_capacity_utilization_pct": (
            100.0 * production / max(production_capacity, 1e-9)
        ),
        "crude_target_utilization_pct": target_utilization,
        "crude_utilization_cycle_deviation_pct": utilization_cycle,
        "crude_inventory_supply_pressure_pct": inventory_pressure_pct,
        "crude_production_change_mbd": production_change,
        "crude_production_outage_mbd": outage_mbd,
        "crude_opening_inventory_mmbbl": opening_inventory,
        "crude_inventory_change_mmbbl": inventory_change,
        "crude_closing_inventory_mmbbl": closing_inventory,
        "crude_target_inventory_days": inventory_target_days,
        "crude_inventory_days": closing_inventory / max(realized_runs, 1e-9),
        "crude_unmet_refinery_runs_mmbbl": unmet_runs,
        "crude_mass_balance_residual_mmbbl": mass_balance_residual,
    }
    return next_state, record
