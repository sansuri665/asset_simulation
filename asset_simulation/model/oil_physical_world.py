"""Monthly global physical-oil balance used by crude shipping demand.

The annual macro world remains the owner of the displayed oil-price anchor.
This module converts only information visible in the current or prior annual
rows into physical rates and an exactly conserved inventory balance.  It does
not reuse the legacy global oil demand/supply indices as physical barrels.
"""

from __future__ import annotations

import calendar
import math
from typing import Any, Mapping, Sequence

from .math_utils import clamp
from .random_stream import normal, uniform


def month_days(year: int, month: int) -> int:
    """Return actual calendar days in one monthly settlement turn."""

    if not 1 <= int(month) <= 12:
        raise ValueError("month must be between 1 and 12")
    return calendar.monthrange(int(year), int(month))[1]


def initial_physical_state(config: Mapping[str, Any]) -> dict[str, float]:
    physical = config["physical_oil"]
    demand = float(physical["initial_demand_mbd"])
    initial_production = float(physical["initial_production_mbd"])
    initial_capacity = float(physical["initial_production_capacity_mbd"])
    initial_utilization_deviation = (
        100.0 * initial_production / max(initial_capacity, 1e-9)
        - float(physical["production"]["base_target_utilization_pct"])
    )
    return {
        "trend_demand_mbd": demand,
        "production_mbd": initial_production,
        "production_capacity_mbd": initial_capacity,
        "inventory_mmbbl": demand * float(physical["initial_inventory_days"]),
        "inventory_target_deviation_days": (
            float(physical["initial_inventory_days"])
            - float(physical["target_inventory_days"])
        ),
        "demand_seasonal_amplitude_pct": float(
            physical["demand"]["seasonal_amplitude_pct"]
        ),
        "demand_seasonal_phase_shift_months": 0.0,
        "demand_seasonal_shape_weight": float(
            physical["demand"]["seasonal_second_harmonic_weight"]
        ),
        "demand_news_pct": 0.0,
        "utilization_cycle_deviation_pct": initial_utilization_deviation,
    }


def _macro_growth(row: Mapping[str, Any]) -> float:
    value = row.get("realized_growth_pct")
    return 2.35 if value is None else float(value)


def _long_run_demand_regime(
    seed: int,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    regimes = list(config["physical_oil"]["demand"]["long_run_regimes"])
    total_weight = sum(float(regime["weight"]) for regime in regimes)
    if total_weight <= 0.0:
        raise ValueError("long-run demand regime weights must sum to a positive value")
    draw = uniform(seed, "oil_physical_long_run_regime", 0) * total_weight
    cumulative = 0.0
    for regime in regimes:
        cumulative += float(regime["weight"])
        if draw <= cumulative:
            return regime
    return regimes[-1]


def _structural_demand_drag_pct(
    regime: Mapping[str, Any],
    simulation_year: int,
) -> float:
    start = int(regime["ramp_start_year"])
    end = int(regime["ramp_end_year"])
    if end <= start:
        raise ValueError("long-run demand regime ramp must have positive duration")
    progress = clamp((int(simulation_year) - start) / (end - start), 0.0, 1.0)
    smooth_progress = progress * progress * (3.0 - 2.0 * progress)
    initial = float(regime["initial_structural_drag_pct"])
    mature = float(regime["mature_structural_drag_pct"])
    return initial + (mature - initial) * smooth_progress


def _persistent_capacity_cycle_pct(
    seed: int,
    year_index: int,
    capacity_config: Mapping[str, Any],
) -> float:
    """Return a deterministic, prefix-stable multi-year investment cycle."""

    persistence = float(capacity_config["investment_cycle_persistence"])
    scale = float(capacity_config["investment_cycle_news_scale_pct"])
    lookback = int(capacity_config["investment_cycle_lookback_years"])
    if not 0.0 <= persistence < 1.0:
        raise ValueError("investment-cycle persistence must be in [0, 1)")
    if lookback <= 0:
        raise ValueError("investment-cycle lookback must be positive")
    weighted = 0.0
    weight_total = 0.0
    for lag in range(min(year_index, lookback - 1) + 1):
        weight = persistence**lag
        weighted += weight * normal(
            seed,
            "oil_physical_capacity_investment_cycle",
            year_index - lag,
        )
        weight_total += weight
    return scale * weighted / max(weight_total, 1e-12)


def _near_term_committed_capacity_growth_pct(
    simulation_year: int,
    capacity_config: Mapping[str, Any],
) -> float:
    """Return the fading contribution of projects already under construction."""

    start = int(capacity_config["near_term_committed_growth_start_year"])
    end = int(capacity_config["near_term_committed_growth_end_year"])
    if end <= start:
        raise ValueError("committed-capacity window must have positive duration")
    progress = clamp((int(simulation_year) - start) / (end - start), 0.0, 1.0)
    return float(capacity_config["near_term_committed_growth_pct"]) * (
        1.0 - progress
    )


def annual_growth_targets(
    visible_macro_rows: Sequence[Mapping[str, Any]],
    *,
    seed: int,
    year_index: int,
    simulation_year: int,
    config: Mapping[str, Any],
    physical_state: Mapping[str, float] | None = None,
) -> tuple[float, float, str, float]:
    """Return demand and capacity growth targets using no future macro row."""

    if not visible_macro_rows:
        raise ValueError("at least one visible macro row is required")
    current = visible_macro_rows[-1]
    physical = config["physical_oil"]
    demand_config = physical["demand"]
    capacity_config = physical["capacity"]
    real_price_index = float(current["global_real_oil_price_index"])
    regime = _long_run_demand_regime(seed, config)
    structural_drag = _structural_demand_drag_pct(regime, simulation_year)
    demand_growth = (
        float(demand_config["gdp_growth_elasticity"]) * _macro_growth(current)
        - float(demand_config["autonomous_efficiency_drag_pct"])
        - structural_drag
        - float(demand_config["real_price_level_elasticity"]) * (real_price_index - 100.0)
        + float(demand_config["annual_news_scale_pct"])
        * normal(seed, "oil_physical_annual_demand", year_index)
    )
    demand_growth = clamp(
        demand_growth,
        *map(float, demand_config["annual_growth_bounds_pct"]),
    )

    # Capacity responds only to completed price history and the prior physical
    # utilization state. Demand growth is deliberately absent: expected demand
    # can tighten utilization and inventories first, but it cannot order new
    # capacity into existence in the same annual planning step.
    price_rows = list(visible_macro_rows[-3:])
    lagged_real_price = sum(
        float(row["global_real_oil_price_index"]) for row in price_rows
    ) / len(price_rows)
    if physical_state is None:
        prior_production = float(physical["initial_production_mbd"])
        prior_capacity = float(physical["initial_production_capacity_mbd"])
    else:
        prior_production = float(physical_state["production_mbd"])
        prior_capacity = float(physical_state["production_capacity_mbd"])
    prior_utilization_pct = 100.0 * prior_production / max(prior_capacity, 1e-9)
    utilization_gap = (
        prior_utilization_pct
        - float(capacity_config["reference_utilization_pct"])
    )
    capacity_growth = (
        float(capacity_config["base_growth_pct"])
        + float(capacity_config["lagged_real_price_level_elasticity"])
        * (lagged_real_price - 100.0)
        + float(capacity_config["utilization_gap_growth_response"])
        * utilization_gap
        + _persistent_capacity_cycle_pct(seed, year_index, capacity_config)
        + _near_term_committed_capacity_growth_pct(
            simulation_year,
            capacity_config,
        )
        + float(capacity_config["annual_news_scale_pct"])
        * normal(seed, "oil_physical_annual_capacity", year_index)
    )
    capacity_growth = clamp(
        capacity_growth,
        *map(float, capacity_config["annual_growth_bounds_pct"]),
    )
    return demand_growth, capacity_growth, str(regime["id"]), structural_drag


def advance_physical_turn(
    state: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    year: int,
    month: int,
    annual_demand_growth_pct: float,
    annual_capacity_growth_pct: float,
    long_run_demand_regime: str,
    structural_demand_drag_pct: float,
    config: Mapping[str, Any],
    impulse: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Advance one conserved physical balance and return state plus diagnostics."""

    impulse = {} if impulse is None else impulse
    physical = config["physical_oil"]
    demand_config = physical["demand"]
    production_config = physical["production"]
    bounds = physical["bounds"]
    days = month_days(year, month)
    year_days = 366 if calendar.isleap(year) else 365
    year_fraction = days / year_days

    trend_demand = float(state["trend_demand_mbd"]) * (
        1.0 + float(annual_demand_growth_pct) / 100.0
    ) ** year_fraction
    trend_demand = clamp(trend_demand, *map(float, bounds["demand_mbd"]))
    production_capacity = float(state["production_capacity_mbd"]) * (
        1.0 + float(annual_capacity_growth_pct) / 100.0
    ) ** year_fraction
    production_capacity = clamp(
        production_capacity,
        *map(float, bounds["production_capacity_mbd"]),
    )

    if month == 1:
        year_index = turn_index // 12
        seasonal_amplitude = clamp(
            float(demand_config["seasonal_amplitude_pct"])
            + float(demand_config["seasonal_amplitude_persistence"])
            * (
                float(state["demand_seasonal_amplitude_pct"])
                - float(demand_config["seasonal_amplitude_pct"])
            )
            + float(demand_config["seasonal_amplitude_news_scale_pct"])
            * normal(seed, "oil_physical_seasonal_amplitude", year_index),
            *map(float, demand_config["seasonal_amplitude_bounds_pct"]),
        )
        seasonal_phase_shift = clamp(
            float(demand_config["seasonal_phase_persistence"])
            * float(state["demand_seasonal_phase_shift_months"])
            + float(demand_config["seasonal_phase_news_scale_months"])
            * normal(seed, "oil_physical_seasonal_phase", year_index),
            *map(float, demand_config["seasonal_phase_bounds_months"]),
        )
        seasonal_shape_weight = clamp(
            float(demand_config["seasonal_second_harmonic_weight"])
            + float(demand_config["seasonal_shape_persistence"])
            * (
                float(state["demand_seasonal_shape_weight"])
                - float(demand_config["seasonal_second_harmonic_weight"])
            )
            + float(demand_config["seasonal_shape_news_scale"])
            * normal(seed, "oil_physical_seasonal_shape", year_index),
            *map(
                float,
                demand_config["seasonal_second_harmonic_weight_bounds"],
            ),
        )
    else:
        seasonal_amplitude = float(state["demand_seasonal_amplitude_pct"])
        seasonal_phase_shift = float(
            state["demand_seasonal_phase_shift_months"]
        )
        seasonal_shape_weight = float(state["demand_seasonal_shape_weight"])

    demand_news = clamp(
        float(demand_config["monthly_news_persistence"])
        * float(state["demand_news_pct"])
        + float(demand_config["monthly_news_scale_pct"])
        * normal(seed, "oil_physical_monthly_demand", turn_index),
        *map(float, demand_config["monthly_news_bounds_pct"]),
    )
    season_position = (month - 0.5 - seasonal_phase_shift) / 12.0
    seasonal_angle = 2.0 * math.pi * season_position - 0.45
    seasonal_pct = seasonal_amplitude * (
        (1.0 - seasonal_shape_weight) * math.cos(seasonal_angle)
        + seasonal_shape_weight * math.cos(2.0 * seasonal_angle - 0.35)
    )
    demand_impulse_pct = float(impulse.get("demand_rate_impulse_pct", 0.0))
    desired_demand = trend_demand * (
        1.0 + (seasonal_pct + demand_news + demand_impulse_pct) / 100.0
    )
    desired_demand = clamp(desired_demand, *map(float, bounds["demand_mbd"]))

    opening_inventory = float(state["inventory_mmbbl"])
    inventory_target_deviation = (
        float(physical["target_inventory_persistence"])
        * float(state["inventory_target_deviation_days"])
        + float(physical["target_inventory_news_scale_days"])
        * normal(seed, "oil_physical_inventory_target", turn_index)
    )
    inventory_target_days = clamp(
        float(physical["target_inventory_days"]) + inventory_target_deviation,
        *map(float, physical["target_inventory_bounds_days"]),
    )
    inventory_target_deviation = (
        inventory_target_days - float(physical["target_inventory_days"])
    )
    opening_inventory_days = opening_inventory / max(trend_demand, 1e-9)
    inventory_pressure_pct = clamp(
        float(production_config["inventory_pressure_per_day_pct"])
        * (inventory_target_days - opening_inventory_days),
        *map(float, production_config["inventory_pressure_bounds_pct"]),
    )
    utilization_cycle_deviation = clamp(
        float(production_config["utilization_cycle_persistence"])
        * float(state["utilization_cycle_deviation_pct"])
        + float(production_config["utilization_cycle_news_scale_pct"])
        * normal(seed, "oil_physical_monthly_utilization", turn_index),
        *map(float, production_config["utilization_cycle_bounds_pct"]),
    )
    target_utilization_pct = clamp(
        float(production_config["base_target_utilization_pct"])
        + utilization_cycle_deviation
        + inventory_pressure_pct,
        *map(float, production_config["target_utilization_bounds_pct"]),
    )
    desired_production = production_capacity * target_utilization_pct / 100.0
    unconstrained_change = float(production_config["adjustment_speed"]) * (
        desired_production - float(state["production_mbd"])
    )
    production_change = clamp(
        unconstrained_change,
        -float(production_config["maximum_monthly_adjustment_mbd"]),
        float(production_config["maximum_monthly_adjustment_mbd"]),
    )
    production = float(state["production_mbd"]) + production_change
    outage_mbd = float(impulse.get("production_outage_mbd", 0.0))
    if outage_mbd < 0.0:
        raise ValueError("production_outage_mbd cannot be negative")
    available_capacity = max(0.0, production_capacity - outage_mbd)
    production = clamp(production, 0.0, available_capacity)

    storage_capacity = trend_demand * float(physical["storage_capacity_days"])
    provisional_inventory = opening_inventory + (production - desired_demand) * days
    if provisional_inventory > storage_capacity:
        production = max(
            0.0,
            production - (provisional_inventory - storage_capacity) / days,
        )

    available_volume = opening_inventory + production * days
    desired_volume = desired_demand * days
    realized_volume = min(desired_volume, max(0.0, available_volume))
    realized_demand = realized_volume / days
    unmet_demand = max(0.0, desired_volume - realized_volume)
    closing_inventory = opening_inventory + production * days - realized_volume
    if abs(closing_inventory) < 1e-10:
        closing_inventory = 0.0
    inventory_change = closing_inventory - opening_inventory
    mass_balance_residual = closing_inventory - (
        opening_inventory + production * days - realized_demand * days
    )
    inventory_days = closing_inventory / max(realized_demand, 1e-9)

    next_state = {
        "trend_demand_mbd": trend_demand,
        "production_mbd": production,
        "production_capacity_mbd": production_capacity,
        "inventory_mmbbl": closing_inventory,
        "inventory_target_deviation_days": inventory_target_deviation,
        "demand_seasonal_amplitude_pct": seasonal_amplitude,
        "demand_seasonal_phase_shift_months": seasonal_phase_shift,
        "demand_seasonal_shape_weight": seasonal_shape_weight,
        "demand_news_pct": demand_news,
        "utilization_cycle_deviation_pct": utilization_cycle_deviation,
    }
    record = {
        "days": float(days),
        "annual_demand_growth_target_pct": float(annual_demand_growth_pct),
        "annual_capacity_growth_target_pct": float(annual_capacity_growth_pct),
        "long_run_demand_regime": long_run_demand_regime,
        "structural_demand_drag_pct": float(structural_demand_drag_pct),
        "desired_demand_mbd": desired_demand,
        "realized_demand_mbd": realized_demand,
        "production_mbd": production,
        "production_capacity_mbd": production_capacity,
        "spare_capacity_mbd": max(0.0, production_capacity - production),
        "capacity_utilization_pct": (
            100.0 * production / max(production_capacity, 1e-9)
        ),
        "target_utilization_pct": target_utilization_pct,
        "utilization_cycle_deviation_pct": utilization_cycle_deviation,
        "inventory_supply_pressure_pct": inventory_pressure_pct,
        "production_change_mbd": production_change,
        "production_outage_mbd": outage_mbd,
        "opening_inventory_mmbbl": opening_inventory,
        "inventory_change_mmbbl": inventory_change,
        "closing_inventory_mmbbl": closing_inventory,
        "target_inventory_days": inventory_target_days,
        "inventory_days": inventory_days,
        "unmet_demand_mmbbl": unmet_demand,
        "mass_balance_residual_mmbbl": mass_balance_residual,
        "demand_seasonal_pct": seasonal_pct,
        "demand_seasonal_amplitude_pct": seasonal_amplitude,
        "demand_seasonal_phase_shift_months": seasonal_phase_shift,
        "demand_seasonal_shape_weight": seasonal_shape_weight,
        "demand_news_pct": demand_news,
    }
    return next_state, record
