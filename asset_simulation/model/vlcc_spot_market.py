"""Experimental single-route VLCC spot-market execution prototype.

This module deliberately sits downstream of the existing monthly crude-shipping
world.  It does not change structural route demand.  Instead it asks how a
fixed reference trade can be executed when locally positioned VLCC-equivalent
supply is allowed to move in and out of the route with a lag.

The virtual inventories are *deviations from a normal pipeline*, not absolute
physical stocks.  A value of zero means that the route is exactly on its
normal transport schedule.  Positive Gulf deviation means cargo has piled up
relative to normal; negative East Asia deviation means the destination is
short relative to normal.
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
import json
import math
from pathlib import Path
import statistics
from typing import Any, Mapping, Sequence

from .engine import GlobalMacroRun
from .math_utils import clamp
from .oil_shipping_world import OilShippingWorld


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
VLCC_SPOT_CONFIG_PATH = PACKAGE_ROOT / "config" / "vlcc_spot_market_v0.1.json"


@lru_cache(maxsize=1)
def load_vlcc_spot_market_config() -> dict[str, Any]:
    value = json.loads(VLCC_SPOT_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("VLCC spot market config must be an object")
    _validate_config(value)
    return value


def _validate_config(config: Mapping[str, Any]) -> None:
    if int(config["shipping_turns_per_month"]) != 3:
        raise ValueError("prototype currently requires three shipping turns per month")
    if float(config["vlcc_cargo_mmbbl"]) <= 0.0:
        raise ValueError("VLCC cargo capacity must be positive")
    if int(config["cycle_turns"]) <= 0:
        raise ValueError("VLCC route cycle must be positive")
    if int(config["cargo_arrival_lag_turns"]) <= 0:
        raise ValueError("cargo arrival lag must be positive")
    if int(config["reference_route_fleet_vlcc"]) <= 0:
        raise ValueError("reference route fleet must be positive")
    if float(config["reference_turn_days"]) <= 0.0:
        raise ValueError("reference turn length must be positive")


def shipping_turn_days(month_days: int) -> tuple[int, int, int]:
    """Split a calendar month into three nearly equal operational turns."""

    if month_days < 28 or month_days > 31:
        raise ValueError("month_days must be a real calendar-month length")
    base, remainder = divmod(int(month_days), 3)
    if remainder == 0:
        return base, base, base
    if remainder == 1:
        return base, base, base + 1
    return base, base + 1, base + 1


def _nearest_int(value: float) -> int:
    if value >= 0.0:
        return int(math.floor(value + 0.5))
    return -int(math.floor(abs(value) + 0.5))


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must lie in [0, 1]")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _correlation(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    left_centered = [float(value) - left_mean for value in left]
    right_centered = [float(value) - right_mean for value in right]
    denominator = math.sqrt(
        sum(value * value for value in left_centered)
        * sum(value * value for value in right_centered)
    )
    if denominator <= 1e-12:
        return 0.0
    return sum(
        left_value * right_value
        for left_value, right_value in zip(left_centered, right_centered)
    ) / denominator


def _route_cargo_mbd(month: Mapping[str, Any], route_id: str) -> float:
    route = next(
        (
            route
            for route in month["routes"]
            if str(route["route_id"]) == route_id
        ),
        None,
    )
    if route is None:
        raise KeyError(f"missing structural route in monthly world: {route_id}")
    cargo_mbd = float(route["cargo_mbd"])
    if cargo_mbd <= 0.0:
        raise ValueError(f"structural route cargo must remain positive: {route_id}")
    return cargo_mbd


def monthly_route_inputs(
    shipping_world: OilShippingWorld,
    *,
    route_id: str,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "year": int(month["year"]),
            "month": int(month["month"]),
            "days": int(month["days"]),
            "cargo_mbd": _route_cargo_mbd(month, route_id),
        }
        for month in shipping_world.turns
    )


def simulate_vlcc_spot_route(
    months: Sequence[Mapping[str, Any]],
    *,
    seed: int,
    cpi_by_year: Mapping[int, float],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the reduced-form spot market over supplied monthly route demand."""

    market = load_vlcc_spot_market_config() if config is None else dict(config)
    _validate_config(market)
    route_id = str(market["route_id"])
    cargo_capacity = float(market["vlcc_cargo_mmbbl"])
    cycle_turns = int(market["cycle_turns"])
    cargo_arrival_lag = int(market["cargo_arrival_lag_turns"])
    reference_turn_days = float(market["reference_turn_days"])
    base_fleet = int(market["reference_route_fleet_vlcc"])

    inventory_cfg = market["inventory"]
    clearance_fraction = float(inventory_cfg["clearance_fraction_per_turn"])
    max_correction_fraction = float(
        inventory_cfg["maximum_correction_fraction_of_structural_flow"]
    )

    reposition_cfg = market["repositioning"]
    reposition_lag = int(reposition_cfg["lag_turns"])
    inventory_reposition_gain = float(
        reposition_cfg["inventory_gap_gain_vlcc_per_day"]
    )
    shortage_reposition_gain = float(
        reposition_cfg["prompt_shortage_gain_vlcc_per_fixture"]
    )
    max_reposition = int(reposition_cfg["maximum_net_reposition_vlcc_per_turn"])
    external_pool_limit = int(reposition_cfg["maximum_external_pool_vlcc"])
    reposition_deadband = float(
        reposition_cfg["deadband_inventory_gap_days"]
    )

    freight_cfg = market["freight"]
    base_real_tce = float(freight_cfg["baseline_real_tce_2025_usd_per_day"])
    shortage_tce_sensitivity = float(
        freight_cfg["prompt_shortage_log_sensitivity"]
    )
    inventory_tce_sensitivity = float(
        freight_cfg["inventory_gap_day_log_sensitivity"]
    )
    minimum_tce_multiple = float(freight_cfg["minimum_real_tce_multiple"])
    maximum_tce_multiple = float(freight_cfg["maximum_real_tce_multiple"])

    gulf_deviation = 0.0
    transit_deviation = 0.0
    east_asia_deviation = 0.0
    route_fleet = base_fleet
    fixture_capacity_carry = 0.0
    cargo_arrival_queue: defaultdict[int, float] = defaultdict(float)
    reposition_queue: defaultdict[int, int] = defaultdict(int)
    records: list[dict[str, Any]] = []
    shipping_turn_index = 0

    for month in months:
        year = int(month["year"])
        month_number = int(month["month"])
        month_days = int(month["days"])
        route_cargo_mbd = float(month["cargo_mbd"])
        if route_cargo_mbd <= 0.0:
            raise ValueError("monthly route cargo must remain positive")
        if year not in cpi_by_year:
            raise KeyError(f"missing CPI price level for {year}")
        cpi_index = float(cpi_by_year[year])
        if cpi_index <= 0.0:
            raise ValueError("CPI price-level index must remain positive")

        for turn_in_month, turn_days in enumerate(
            shipping_turn_days(month_days),
            start=1,
        ):
            arriving_reposition = int(reposition_queue.pop(shipping_turn_index, 0))
            route_fleet += arriving_reposition
            route_fleet = int(
                clamp(
                    float(route_fleet),
                    float(base_fleet - external_pool_limit),
                    float(base_fleet + external_pool_limit),
                )
            )

            arriving_cargo_deviation = float(
                cargo_arrival_queue.pop(shipping_turn_index, 0.0)
            )
            transit_deviation -= arriving_cargo_deviation
            east_asia_deviation += arriving_cargo_deviation

            structural_cargo = route_cargo_mbd * int(turn_days)
            opening_inventory_gap = 0.5 * (
                gulf_deviation - east_asia_deviation
            )
            opening_inventory_gap_days = opening_inventory_gap / route_cargo_mbd
            correction = clamp(
                clearance_fraction * opening_inventory_gap,
                -max_correction_fraction * structural_cargo,
                max_correction_fraction * structural_cargo,
            )
            desired_load = max(0.0, structural_cargo + correction)
            desired_fixtures = max(
                0,
                _nearest_int(desired_load / cargo_capacity),
            )

            raw_fixture_capacity = (
                float(route_fleet)
                / cycle_turns
                * float(turn_days)
                / reference_turn_days
                + fixture_capacity_carry
            )
            available_fixtures = max(0, int(math.floor(raw_fixture_capacity + 1e-12)))
            fixture_capacity_carry = raw_fixture_capacity - available_fixtures
            loaded_fixtures = min(desired_fixtures, available_fixtures)
            loaded_cargo = loaded_fixtures * cargo_capacity
            load_deviation = loaded_cargo - structural_cargo

            gulf_deviation -= load_deviation
            transit_deviation += load_deviation
            cargo_arrival_queue[
                shipping_turn_index + cargo_arrival_lag
            ] += load_deviation

            closing_inventory_gap = 0.5 * (
                gulf_deviation - east_asia_deviation
            )
            closing_inventory_gap_days = closing_inventory_gap / route_cargo_mbd
            unfilled_fixtures = max(0, desired_fixtures - loaded_fixtures)

            reference_fixture_capacity = (
                float(base_fleet)
                / cycle_turns
                * float(turn_days)
                / reference_turn_days
            )
            prompt_shortage_ratio = (
                float(desired_fixtures - available_fixtures)
                / max(reference_fixture_capacity, 1.0)
            )
            freight_log_multiplier = (
                shortage_tce_sensitivity * prompt_shortage_ratio
                + inventory_tce_sensitivity * opening_inventory_gap_days
            )
            real_tce_multiple = clamp(
                math.exp(freight_log_multiplier),
                minimum_tce_multiple,
                maximum_tce_multiple,
            )
            real_tce = base_real_tce * real_tce_multiple
            nominal_tce = real_tce * cpi_index / 100.0

            raw_reposition_request = (
                inventory_reposition_gain * closing_inventory_gap_days
                + shortage_reposition_gain * unfilled_fixtures
            )
            if (
                abs(closing_inventory_gap_days) <= reposition_deadband
                and unfilled_fixtures == 0
            ):
                reposition_request = 0
            else:
                reposition_request = _nearest_int(raw_reposition_request)
                reposition_request = int(
                    clamp(
                        float(reposition_request),
                        float(-max_reposition),
                        float(max_reposition),
                    )
                )

            projected_fleet = route_fleet + sum(reposition_queue.values())
            minimum_fleet = base_fleet - external_pool_limit
            maximum_fleet = base_fleet + external_pool_limit
            if projected_fleet + reposition_request > maximum_fleet:
                reposition_request = maximum_fleet - projected_fleet
            elif projected_fleet + reposition_request < minimum_fleet:
                reposition_request = minimum_fleet - projected_fleet
            reposition_queue[
                shipping_turn_index + reposition_lag
            ] += int(reposition_request)

            conservation_residual = (
                gulf_deviation + transit_deviation + east_asia_deviation
            )
            records.append(
                {
                    "seed": int(seed),
                    "shipping_turn_index": shipping_turn_index,
                    "year": year,
                    "month": month_number,
                    "shipping_turn_in_month": turn_in_month,
                    "label": f"{year}-{month_number:02d}.{turn_in_month}",
                    "turn_days": int(turn_days),
                    "structural_route_cargo_mbd": round(route_cargo_mbd, 8),
                    "structural_cargo_mmbbl": round(structural_cargo, 8),
                    "desired_load_mmbbl": round(desired_load, 8),
                    "desired_fixture_vlcc": desired_fixtures,
                    "available_fixture_vlcc": available_fixtures,
                    "loaded_fixture_vlcc": loaded_fixtures,
                    "unfilled_fixture_vlcc": unfilled_fixtures,
                    "loaded_cargo_mmbbl": round(loaded_cargo, 8),
                    "load_deviation_mmbbl": round(load_deviation, 8),
                    "cargo_arrival_deviation_mmbbl": round(
                        arriving_cargo_deviation,
                        8,
                    ),
                    "gulf_inventory_deviation_mmbbl": round(
                        gulf_deviation,
                        8,
                    ),
                    "in_transit_deviation_mmbbl": round(
                        transit_deviation,
                        8,
                    ),
                    "east_asia_inventory_deviation_mmbbl": round(
                        east_asia_deviation,
                        8,
                    ),
                    "inventory_conservation_residual_mmbbl": round(
                        conservation_residual,
                        10,
                    ),
                    "opening_inventory_gap_mmbbl": round(
                        opening_inventory_gap,
                        8,
                    ),
                    "inventory_gap_mmbbl": round(closing_inventory_gap, 8),
                    "opening_inventory_gap_days": round(
                        opening_inventory_gap_days,
                        8,
                    ),
                    "inventory_gap_days": round(
                        closing_inventory_gap_days,
                        8,
                    ),
                    "route_fleet_vlcc": route_fleet,
                    "route_fleet_vs_reference_vlcc": route_fleet - base_fleet,
                    "reposition_arrivals_vlcc": arriving_reposition,
                    "reposition_request_vlcc": int(reposition_request),
                    "pending_reposition_vlcc": int(sum(reposition_queue.values())),
                    "prompt_shortage_ratio": round(prompt_shortage_ratio, 8),
                    "real_tce_2025_usd_per_day": round(real_tce, 2),
                    "cpi_price_level_index_2025_100": round(cpi_index, 8),
                    "nominal_tce_usd_per_day": round(nominal_tce, 2),
                }
            )
            shipping_turn_index += 1

    cargo_rates = [float(record["structural_route_cargo_mbd"]) for record in records]
    real_tce_values = [float(record["real_tce_2025_usd_per_day"]) for record in records]
    nominal_tce_values = [float(record["nominal_tce_usd_per_day"]) for record in records]
    inventory_gap_days = [abs(float(record["inventory_gap_days"])) for record in records]
    route_fleets = [int(record["route_fleet_vlcc"]) for record in records]
    unfilled = [int(record["unfilled_fixture_vlcc"]) for record in records]
    cpi_values = [float(record["cpi_price_level_index_2025_100"]) for record in records]

    summary = {
        "turn_count": len(records),
        "structural_route_cargo_mbd_mean": round(statistics.fmean(cargo_rates), 6),
        "structural_route_cargo_mbd_min": round(min(cargo_rates), 6),
        "structural_route_cargo_mbd_max": round(max(cargo_rates), 6),
        "structural_route_cargo_peak_to_trough_pct": round(
            100.0 * (max(cargo_rates) / min(cargo_rates) - 1.0),
            4,
        ),
        "route_fleet_vlcc_mean": round(statistics.fmean(route_fleets), 4),
        "route_fleet_vlcc_min": min(route_fleets),
        "route_fleet_vlcc_max": max(route_fleets),
        "maximum_abs_inventory_gap_days": round(max(inventory_gap_days), 6),
        "p95_abs_inventory_gap_days": round(_percentile(inventory_gap_days, 0.95), 6),
        "total_unfilled_fixture_vlcc": sum(unfilled),
        "real_tce_2025_usd_per_day_p05": round(_percentile(real_tce_values, 0.05), 2),
        "real_tce_2025_usd_per_day_median": round(_percentile(real_tce_values, 0.50), 2),
        "real_tce_2025_usd_per_day_p95": round(_percentile(real_tce_values, 0.95), 2),
        "real_tce_2025_usd_per_day_min": round(min(real_tce_values), 2),
        "real_tce_2025_usd_per_day_max": round(max(real_tce_values), 2),
        "nominal_tce_usd_per_day_p05": round(_percentile(nominal_tce_values, 0.05), 2),
        "nominal_tce_usd_per_day_median": round(_percentile(nominal_tce_values, 0.50), 2),
        "nominal_tce_usd_per_day_p95": round(_percentile(nominal_tce_values, 0.95), 2),
        "nominal_tce_usd_per_day_max": round(max(nominal_tce_values), 2),
        "cargo_tce_same_turn_correlation": round(
            _correlation(cargo_rates, real_tce_values),
            6,
        ),
        "cpi_price_level_start": round(cpi_values[0], 6),
        "cpi_price_level_end": round(cpi_values[-1], 6),
        "nominal_to_real_price_factor_end": round(cpi_values[-1] / 100.0, 6),
    }
    return {
        "identity": {
            "model_version": str(market["model_version"]),
            "seed": int(seed),
            "route_id": route_id,
            "shipping_turns_per_month": int(market["shipping_turns_per_month"]),
            "cycle_turns": cycle_turns,
            "reference_route_fleet_vlcc": base_fleet,
            "freight_price_basis": "real_2025_usd_then_cpi_to_nominal",
            "upstream_cargo_owner": "oil_shipping_world",
            "market_scope": "single_route_vlcc_spot_prototype",
        },
        "turns": tuple(records),
        "summary": summary,
    }


def run_gulf_east_asia_vlcc_spot_market(
    global_run: GlobalMacroRun,
    shipping_world: OilShippingWorld,
    *,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the prototype directly on the actual seeded shipping-demand world."""

    if int(global_run.seed) != int(shipping_world.seed):
        raise ValueError("macro and shipping worlds must use the same seed")
    market = load_vlcc_spot_market_config() if config is None else dict(config)
    route_id = str(market["route_id"])
    cpi_by_year = {
        int(row["year"]): float(row["cpi_price_level_index_2025_100"])
        for row in global_run.rows
    }
    months = monthly_route_inputs(shipping_world, route_id=route_id)
    return simulate_vlcc_spot_route(
        months,
        seed=int(global_run.seed),
        cpi_by_year=cpi_by_year,
        config=market,
    )


def run_seeded_gulf_east_asia_vlcc_spot_market(
    seed: int = 42,
    years: int = 20,
) -> dict[str, Any]:
    """Convenience entry point used by experiments and audit scripts."""

    from .engine import run_global_macro
    from .oil_shipping_world import run_oil_shipping_world

    macro = run_global_macro(seed, years)
    shipping = run_oil_shipping_world(macro)
    return run_gulf_east_asia_vlcc_spot_market(macro, shipping)
