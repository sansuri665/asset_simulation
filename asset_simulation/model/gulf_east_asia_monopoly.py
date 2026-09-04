"""Single-route Gulf-to-East-Asia VLCC monopoly operations prototype.

The model sits downstream of the existing seeded crude-shipping world.  One
operator owns a fixed numbered VLCC fleet and may leave prompt vessels idle at
the Gulf instead of offering them for a voyage.  Ships that are dispatched
follow a five-shipping-turn commercial circuit:

    loading/early laden -> laden -> late laden/discharge -> ballast -> ballast

The prototype is deliberately operational.  It includes gross freight,
commission, bunker, port/other voyage expenses, vessel OPEX and idle bunker
costs.  It excludes depreciation, debt, interest, ship values, newbuildings and
scrapping.

Virtual inventories are deviations from the normal pipeline, not absolute
stocks.  They remain exactly conserved across the Gulf, in-transit and East
Asia ledgers.
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
CONFIG_PATH = PACKAGE_ROOT / "config" / "gulf_east_asia_monopoly_v0.1.json"
MODEL_VERSION = "asset-simulation-gulf-east-asia-monopoly-operations-v0.1.0"


@lru_cache(maxsize=1)
def load_gulf_east_asia_monopoly_config() -> dict[str, Any]:
    value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("monopoly operations config must be an object")
    _validate_config(value)
    return value


def _validate_config(config: Mapping[str, Any]) -> None:
    if str(config["model_version"]) != MODEL_VERSION:
        raise ValueError("monopoly operations model version mismatch")
    if int(config["shipping_turns_per_month"]) != 3:
        raise ValueError("prototype requires three shipping turns per month")
    if float(config["reference_turn_days"]) <= 0.0:
        raise ValueError("reference turn length must be positive")

    fleet = config["fleet"]
    total = int(fleet["total_vlcc"])
    if total <= 0:
        raise ValueError("fixed VLCC fleet must be positive")
    if float(fleet["cargo_mmbbl"]) <= 0.0:
        raise ValueError("VLCC cargo must be positive")
    if float(fleet["cargo_tonnes"]) <= 0.0:
        raise ValueError("VLCC cargo tonnes must be positive")
    cycle_turns = int(fleet["cycle_turns"])
    arrival_lag = int(fleet["cargo_arrival_lag_turns"])
    if cycle_turns != 5:
        raise ValueError("prototype requires the agreed five-turn circuit")
    if arrival_lag != 3:
        raise ValueError("prototype requires cargo arrival after three turns")
    cohorts = [int(value) for value in fleet["opening_departure_cohorts_vlcc"]]
    if len(cohorts) != cycle_turns - 1 or any(value < 0 for value in cohorts):
        raise ValueError("opening cohorts must cover four non-prompt phases")
    if sum(cohorts) >= total:
        raise ValueError("opening cohorts must leave prompt Gulf vessels")
    if int(fleet["reference_active_vlcc"]) != sum(cohorts) + int(
        fleet["reference_prompt_dispatch_vlcc"]
    ):
        raise ValueError("reference active fleet must close to cohorts plus prompt dispatch")
    if int(fleet["reference_active_vlcc"]) > total:
        raise ValueError("reference active fleet cannot exceed fixed fleet")

    costs = config["costs"]
    positive_cost_fields = (
        "base_real_opex_2025_usd_per_vessel_day",
        "base_real_bunker_2025_usd_per_tonne",
        "laden_fuel_tonnes_per_day",
        "ballast_fuel_tonnes_per_day",
        "idle_fuel_tonnes_per_day",
        "gulf_load_port_cost_real_2025_usd",
        "east_asia_discharge_port_cost_real_2025_usd",
    )
    for field in positive_cost_fields:
        if float(costs[field]) <= 0.0:
            raise ValueError(f"{field} must be positive")
    if not 0.0 <= float(costs["commission_rate"]) < 1.0:
        raise ValueError("commission rate must lie in [0, 1)")
    if not 0.0 <= float(costs["bunker_real_oil_weight"]) <= 1.0:
        raise ValueError("bunker oil weight must lie in [0, 1]")
    if float(costs["minimum_real_bunker_usd_per_tonne"]) <= 0.0:
        raise ValueError("minimum bunker price must be positive")
    if float(costs["maximum_real_bunker_usd_per_tonne"]) <= float(
        costs["minimum_real_bunker_usd_per_tonne"]
    ):
        raise ValueError("maximum bunker price must exceed minimum")

    pricing = config["pricing"]
    if float(pricing["baseline_real_tce_2025_usd_per_day"]) <= 0.0:
        raise ValueError("baseline TCE must be positive")
    if float(pricing["minimum_real_tce_2025_usd_per_day"]) < 0.0:
        raise ValueError("minimum TCE cannot be negative in this monopoly prototype")
    if float(pricing["maximum_real_tce_2025_usd_per_day"]) <= float(
        pricing["minimum_real_tce_2025_usd_per_day"]
    ):
        raise ValueError("maximum TCE must exceed minimum")
    if not 0.0 <= float(pricing["price_persistence"]) < 1.0:
        raise ValueError("price persistence must lie in [0, 1)")

    control = config["control"]
    if float(control["inventory_shadow_price_real_usd_per_bbl_day"]) < 0.0:
        raise ValueError("inventory shadow price cannot be negative")
    if float(control["inventory_free_band_days"]) < 0.0:
        raise ValueError("inventory free band cannot be negative")
    if int(control["maximum_strategic_withholding_vlcc"]) < 0:
        raise ValueError("maximum strategic withholding cannot be negative")


def shipping_turn_days(month_days: int) -> tuple[int, int, int]:
    """Split a calendar month into three nearly equal operating turns."""

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
        raise ValueError("percentile requires values")
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
        raise KeyError(f"missing route in shipping world: {route_id}")
    cargo = float(route["cargo_mbd"])
    if cargo <= 0.0:
        raise ValueError("route cargo must remain positive")
    return cargo


def monthly_gulf_east_asia_inputs(
    shipping_world: OilShippingWorld,
    *,
    route_id: str = "gulf_east_asia",
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "year": int(month["year"]),
            "month": int(month["month"]),
            "days": int(month["days"]),
            "cargo_mbd": _route_cargo_mbd(month, route_id),
            "real_oil_price_index": float(month["macro_real_oil_price_index"]),
            "nominal_brent_usd_per_bbl": float(month["macro_brent_oil_price_usd"]),
        }
        for month in shipping_world.turns
    )


def _normalize_controls(
    control_by_turn: Mapping[int, Mapping[str, Any]] | None,
    *,
    turn_count: int,
) -> dict[int, dict[str, int]]:
    allowed = {"dispatch_override_vlcc", "additional_withholding_vlcc"}
    normalized: dict[int, dict[str, int]] = {}
    for key, raw in ({} if control_by_turn is None else control_by_turn).items():
        if isinstance(key, bool) or not isinstance(key, int):
            raise TypeError("control turn indices must be integers")
        if not 0 <= key < turn_count:
            raise ValueError(f"control turn is outside simulation: {key}")
        unknown = set(raw) - allowed
        if unknown:
            raise KeyError(f"unknown monopoly control fields: {sorted(unknown)}")
        values: dict[str, int] = {}
        for field, value in raw.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field} must be an integer")
            if value < 0:
                raise ValueError(f"{field} cannot be negative")
            values[field] = int(value)
        normalized[key] = values
    return normalized


def _real_bunker_price(
    real_oil_price_index: float,
    costs: Mapping[str, Any],
) -> float:
    weight = float(costs["bunker_real_oil_weight"])
    base = float(costs["base_real_bunker_2025_usd_per_tonne"])
    linked = base * (
        (1.0 - weight) + weight * max(float(real_oil_price_index), 1.0) / 100.0
    )
    return clamp(
        linked,
        float(costs["minimum_real_bunker_usd_per_tonne"]),
        float(costs["maximum_real_bunker_usd_per_tonne"]),
    )


def _phase_fuel_tonnes(config: Mapping[str, Any]) -> tuple[float, ...]:
    costs = config["costs"]
    turn_days = float(config["reference_turn_days"])
    laden = float(costs["laden_fuel_tonnes_per_day"])
    ballast = float(costs["ballast_fuel_tonnes_per_day"])
    return (
        float(costs["load_port_fuel_tonnes"]) + 0.5 * turn_days * laden,
        turn_days * laden,
        0.5 * turn_days * laden
        + float(costs["discharge_port_fuel_tonnes"])
        + float(costs["waiting_fuel_tonnes"]),
        turn_days * ballast,
        turn_days * ballast,
    )


def _voyage_terms(
    *,
    real_tce: float,
    real_bunker_price: float,
    cpi: float,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    fleet = config["fleet"]
    costs = config["costs"]
    cycle_turns = int(fleet["cycle_turns"])
    cycle_days = float(config["reference_turn_days"]) * cycle_turns
    phase_fuel = _phase_fuel_tonnes(config)
    total_fuel = sum(phase_fuel)
    real_bunker_cost = total_fuel * real_bunker_price
    real_load_port = float(costs["gulf_load_port_cost_real_2025_usd"])
    real_discharge_port = float(
        costs["east_asia_discharge_port_cost_real_2025_usd"]
    )
    real_other = float(costs["other_voyage_cost_real_2025_usd"])
    commission_rate = float(costs["commission_rate"])
    real_net_voyage_revenue = real_tce * cycle_days
    real_gross_freight = (
        real_net_voyage_revenue
        + real_bunker_cost
        + real_load_port
        + real_discharge_port
        + real_other
    ) / (1.0 - commission_rate)
    real_commission = real_gross_freight * commission_rate
    factor = cpi / 100.0

    real_revenue_slices = tuple(
        real_gross_freight / cycle_turns for _ in range(cycle_turns)
    )
    real_commission_slices = tuple(
        real_commission / cycle_turns for _ in range(cycle_turns)
    )
    real_bunker_slices = tuple(
        fuel * real_bunker_price for fuel in phase_fuel
    )
    real_port_slices = (
        real_load_port,
        0.0,
        real_discharge_port,
        0.0,
        0.0,
    )
    real_other_slices = (real_other, 0.0, 0.0, 0.0, 0.0)

    return {
        "real_tce": real_tce,
        "nominal_tce": real_tce * factor,
        "cycle_days": cycle_days,
        "total_fuel_tonnes": total_fuel,
        "real_bunker_price": real_bunker_price,
        "nominal_bunker_price": real_bunker_price * factor,
        "real_gross_freight": real_gross_freight,
        "nominal_gross_freight": real_gross_freight * factor,
        "real_commission": real_commission,
        "nominal_commission": real_commission * factor,
        "real_bunker_cost": real_bunker_cost,
        "nominal_bunker_cost": real_bunker_cost * factor,
        "real_port_cost": real_load_port + real_discharge_port,
        "nominal_port_cost": (real_load_port + real_discharge_port) * factor,
        "real_other_voyage_cost": real_other,
        "nominal_other_voyage_cost": real_other * factor,
        "real_net_voyage_revenue": real_net_voyage_revenue,
        "nominal_net_voyage_revenue": real_net_voyage_revenue * factor,
        "real_revenue_slices": real_revenue_slices,
        "nominal_revenue_slices": tuple(value * factor for value in real_revenue_slices),
        "real_commission_slices": real_commission_slices,
        "nominal_commission_slices": tuple(
            value * factor for value in real_commission_slices
        ),
        "real_bunker_slices": real_bunker_slices,
        "nominal_bunker_slices": tuple(value * factor for value in real_bunker_slices),
        "real_port_slices": real_port_slices,
        "nominal_port_slices": tuple(value * factor for value in real_port_slices),
        "real_other_slices": real_other_slices,
        "nominal_other_slices": tuple(value * factor for value in real_other_slices),
    }


def _quoted_real_tce(
    *,
    dispatched: int,
    desired_fixtures: int,
    structural_fixtures: float,
    opening_gap_days: float,
    cargo_mbd: float,
    previous_real_tce: float,
    config: Mapping[str, Any],
) -> float:
    pricing = config["pricing"]
    base = float(pricing["baseline_real_tce_2025_usd_per_day"])
    shortage_ratio = (
        float(desired_fixtures - dispatched) / max(structural_fixtures, 1.0)
    )
    demand_level = cargo_mbd / float(config["reference_route_cargo_mbd"]) - 1.0
    raw_log = (
        float(pricing["prompt_shortage_log_sensitivity"]) * shortage_ratio
        + float(pricing["inventory_gap_day_log_sensitivity"]) * opening_gap_days
        + float(pricing["demand_level_log_sensitivity"]) * demand_level
    )
    persistence = float(pricing["price_persistence"])
    settled_log = (
        persistence * math.log(max(previous_real_tce / base, 1e-12))
        + (1.0 - persistence) * raw_log
    )
    return clamp(
        base * math.exp(settled_log),
        float(pricing["minimum_real_tce_2025_usd_per_day"]),
        float(pricing["maximum_real_tce_2025_usd_per_day"]),
    )


def _select_dispatch(
    *,
    available_vlcc: int,
    desired_fixtures: int,
    structural_cargo_mmbbl: float,
    cargo_mbd: float,
    gulf_deviation: float,
    east_asia_deviation: float,
    opening_gap_days: float,
    previous_real_tce: float,
    real_bunker_price: float,
    turn_days: int,
    control: Mapping[str, int],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    fleet = config["fleet"]
    costs = config["costs"]
    policy = config["control"]
    cargo_capacity = float(fleet["cargo_mmbbl"])
    maximum_dispatch = min(available_vlcc, desired_fixtures)
    structural_fixtures = structural_cargo_mmbbl / cargo_capacity
    max_strategic_withholding = int(
        policy["maximum_strategic_withholding_vlcc"]
    )
    candidate_minimum = max(0, maximum_dispatch - max_strategic_withholding)
    candidates = range(candidate_minimum, maximum_dispatch + 1)

    best: dict[str, Any] | None = None
    for dispatched in candidates:
        real_tce = _quoted_real_tce(
            dispatched=dispatched,
            desired_fixtures=desired_fixtures,
            structural_fixtures=structural_fixtures,
            opening_gap_days=opening_gap_days,
            cargo_mbd=cargo_mbd,
            previous_real_tce=previous_real_tce,
            config=config,
        )
        voyage_terms = _voyage_terms(
            real_tce=real_tce,
            real_bunker_price=real_bunker_price,
            cpi=100.0,
            config=config,
        )
        load_deviation = dispatched * cargo_capacity - structural_cargo_mmbbl
        projected_gulf = gulf_deviation - load_deviation
        projected_gap = 0.5 * (projected_gulf - east_asia_deviation)
        projected_gap_days = projected_gap / cargo_mbd
        free_band = float(policy["inventory_free_band_days"])
        excess_days = max(abs(projected_gap_days) - free_band, 0.0)
        inventory_shadow_cost = (
            float(policy["inventory_shadow_price_real_usd_per_bbl_day"])
            * abs(projected_gap)
            * 1_000_000.0
            * excess_days
        )
        idle_bunker_cost = (
            (available_vlcc - dispatched)
            * float(costs["idle_fuel_tonnes_per_day"])
            * real_bunker_price
            * turn_days
        )
        objective = (
            dispatched * float(voyage_terms["real_net_voyage_revenue"])
            - idle_bunker_cost
            - inventory_shadow_cost
        )
        candidate = {
            "policy_dispatch": dispatched,
            "real_tce": real_tce,
            "objective": objective,
            "projected_gap_days": projected_gap_days,
            "inventory_shadow_cost": inventory_shadow_cost,
            "idle_bunker_cost": idle_bunker_cost,
        }
        if (
            best is None
            or objective > float(best["objective"]) + 1e-6
            or (
                abs(objective - float(best["objective"])) <= 1e-6
                and dispatched > int(best["policy_dispatch"])
            )
        ):
            best = candidate

    if best is None:
        real_tce = _quoted_real_tce(
            dispatched=0,
            desired_fixtures=desired_fixtures,
            structural_fixtures=structural_fixtures,
            opening_gap_days=opening_gap_days,
            cargo_mbd=cargo_mbd,
            previous_real_tce=previous_real_tce,
            config=config,
        )
        best = {
            "policy_dispatch": 0,
            "real_tce": real_tce,
            "objective": 0.0,
            "projected_gap_days": opening_gap_days,
            "inventory_shadow_cost": 0.0,
            "idle_bunker_cost": (
                available_vlcc
                * float(costs["idle_fuel_tonnes_per_day"])
                * real_bunker_price
                * turn_days
            ),
        }

    policy_dispatch = int(best["policy_dispatch"])
    dispatch = policy_dispatch
    control_mode = "policy"
    if "dispatch_override_vlcc" in control:
        dispatch = min(maximum_dispatch, int(control["dispatch_override_vlcc"]))
        control_mode = "dispatch_override"
    elif "additional_withholding_vlcc" in control:
        dispatch = max(
            0,
            policy_dispatch - int(control["additional_withholding_vlcc"]),
        )
        control_mode = "additional_withholding"

    actual_tce = _quoted_real_tce(
        dispatched=dispatch,
        desired_fixtures=desired_fixtures,
        structural_fixtures=structural_fixtures,
        opening_gap_days=opening_gap_days,
        cargo_mbd=cargo_mbd,
        previous_real_tce=previous_real_tce,
        config=config,
    )
    actual_terms = _voyage_terms(
        real_tce=actual_tce,
        real_bunker_price=real_bunker_price,
        cpi=100.0,
        config=config,
    )
    load_deviation = dispatch * cargo_capacity - structural_cargo_mmbbl
    projected_gulf = gulf_deviation - load_deviation
    projected_gap = 0.5 * (projected_gulf - east_asia_deviation)
    projected_gap_days = projected_gap / cargo_mbd
    free_band = float(policy["inventory_free_band_days"])
    excess_days = max(abs(projected_gap_days) - free_band, 0.0)
    shadow_cost = (
        float(policy["inventory_shadow_price_real_usd_per_bbl_day"])
        * abs(projected_gap)
        * 1_000_000.0
        * excess_days
    )
    idle_bunker_cost = (
        (available_vlcc - dispatch)
        * float(costs["idle_fuel_tonnes_per_day"])
        * real_bunker_price
        * turn_days
    )
    objective = (
        dispatch * float(actual_terms["real_net_voyage_revenue"])
        - idle_bunker_cost
        - shadow_cost
    )
    return {
        "policy_dispatch_vlcc": policy_dispatch,
        "dispatch_vlcc": dispatch,
        "control_mode": control_mode,
        "real_tce": actual_tce,
        "objective_real_usd": objective,
        "inventory_shadow_cost_real_usd": shadow_cost,
        "projected_gap_days": projected_gap_days,
        "withheld_vlcc": max(0, maximum_dispatch - dispatch),
        "physical_shortage_vlcc": max(0, desired_fixtures - available_vlcc),
    }


def _new_voyage(
    *,
    ship_id: str,
    departure_turn: int,
    cargo_mmbbl: float,
    terms: Mapping[str, Any],
    opening_pipeline: bool,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "ship_id": ship_id,
        "departure_turn": int(departure_turn),
        "arrival_turn": int(departure_turn)
        + int(config["fleet"]["cargo_arrival_lag_turns"]),
        "return_turn": int(departure_turn) + int(config["fleet"]["cycle_turns"]),
        "cargo_mmbbl": float(cargo_mmbbl),
        "terms": dict(terms),
        "opening_pipeline": bool(opening_pipeline),
    }


def _phase_name(age: int) -> str:
    return (
        "loading_and_early_laden",
        "laden",
        "late_laden_and_discharge",
        "ballast_1",
        "ballast_2",
    )[age]


def _accrue_active_voyages(
    active_voyages: Mapping[str, Mapping[str, Any]],
    *,
    turn_index: int,
) -> dict[str, Any]:
    nominal_revenue = 0.0
    nominal_commission = 0.0
    nominal_bunker = 0.0
    nominal_port = 0.0
    nominal_other = 0.0
    real_revenue = 0.0
    real_commission = 0.0
    real_bunker = 0.0
    real_port = 0.0
    real_other = 0.0
    phase_counts = {
        "loading_and_early_laden": 0,
        "laden": 0,
        "late_laden_and_discharge": 0,
        "ballast_1": 0,
        "ballast_2": 0,
    }
    for voyage in active_voyages.values():
        age = turn_index - int(voyage["departure_turn"])
        if not 0 <= age < 5:
            continue
        terms = voyage["terms"]
        phase_counts[_phase_name(age)] += 1
        nominal_revenue += float(terms["nominal_revenue_slices"][age])
        nominal_commission += float(terms["nominal_commission_slices"][age])
        nominal_bunker += float(terms["nominal_bunker_slices"][age])
        nominal_port += float(terms["nominal_port_slices"][age])
        nominal_other += float(terms["nominal_other_slices"][age])
        real_revenue += float(terms["real_revenue_slices"][age])
        real_commission += float(terms["real_commission_slices"][age])
        real_bunker += float(terms["real_bunker_slices"][age])
        real_port += float(terms["real_port_slices"][age])
        real_other += float(terms["real_other_slices"][age])
    return {
        "phase_counts": phase_counts,
        "nominal_gross_freight_revenue": nominal_revenue,
        "nominal_commission": nominal_commission,
        "nominal_bunker": nominal_bunker,
        "nominal_port": nominal_port,
        "nominal_other": nominal_other,
        "real_gross_freight_revenue": real_revenue,
        "real_commission": real_commission,
        "real_bunker": real_bunker,
        "real_port": real_port,
        "real_other": real_other,
    }


def _opening_state(
    *,
    first_month: Mapping[str, Any],
    first_cpi: float,
    config: Mapping[str, Any],
) -> tuple[list[str], dict[str, dict[str, Any]], defaultdict[int, float]]:
    fleet = config["fleet"]
    total = int(fleet["total_vlcc"])
    ids = [f"VLCC-{index:04d}" for index in range(1, total + 1)]
    cohorts = [int(value) for value in fleet["opening_departure_cohorts_vlcc"]]
    baseline_tce = float(config["pricing"]["baseline_real_tce_2025_usd_per_day"])
    bunker = _real_bunker_price(
        float(first_month["real_oil_price_index"]),
        config["costs"],
    )
    terms = _voyage_terms(
        real_tce=baseline_tce,
        real_bunker_price=bunker,
        cpi=first_cpi,
        config=config,
    )
    active: dict[str, dict[str, Any]] = {}
    arrival_queue: defaultdict[int, float] = defaultdict(float)
    cursor = 0
    structural_reference = (
        float(config["reference_route_cargo_mbd"])
        * float(config["reference_turn_days"])
    )
    cargo_capacity = float(fleet["cargo_mmbbl"])
    departure_turns = list(range(-(len(cohorts)), 0))
    for departure_turn, count in zip(departure_turns, cohorts):
        load_deviation = count * cargo_capacity - structural_reference
        arrival_turn = departure_turn + int(fleet["cargo_arrival_lag_turns"])
        if arrival_turn >= 0:
            arrival_queue[arrival_turn] += load_deviation
        for ship_id in ids[cursor : cursor + count]:
            active[ship_id] = _new_voyage(
                ship_id=ship_id,
                departure_turn=departure_turn,
                cargo_mmbbl=cargo_capacity,
                terms=terms,
                opening_pipeline=True,
                config=config,
            )
        cursor += count
    idle = ids[cursor:]
    return idle, active, arrival_queue


def simulate_gulf_east_asia_monopoly_operations(
    months: Sequence[Mapping[str, Any]],
    *,
    seed: int,
    cpi_by_year: Mapping[int, float],
    config: Mapping[str, Any] | None = None,
    control_by_turn: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Simulate a fixed-fleet monopoly on the Gulf-to-East-Asia VLCC route."""

    if not months:
        raise ValueError("simulation requires monthly inputs")
    market = load_gulf_east_asia_monopoly_config() if config is None else dict(config)
    _validate_config(market)
    controls = _normalize_controls(
        control_by_turn,
        turn_count=len(months) * int(market["shipping_turns_per_month"]),
    )
    first_year = int(months[0]["year"])
    if first_year not in cpi_by_year:
        raise KeyError(f"missing CPI for opening year {first_year}")
    first_cpi = float(cpi_by_year[first_year])
    if first_cpi <= 0.0:
        raise ValueError("CPI must remain positive")

    fleet_cfg = market["fleet"]
    costs = market["costs"]
    inventory_cfg = market["inventory"]
    total_fleet = int(fleet_cfg["total_vlcc"])
    cargo_capacity = float(fleet_cfg["cargo_mmbbl"])
    idle_ships, active_voyages, arrival_queue = _opening_state(
        first_month=months[0],
        first_cpi=first_cpi,
        config=market,
    )
    idle_ships.sort()
    all_ship_ids = {
        f"VLCC-{index:04d}" for index in range(1, total_fleet + 1)
    }

    gulf_deviation = 0.0
    transit_deviation = 0.0
    east_asia_deviation = 0.0
    fixture_carry = 0.0
    previous_real_tce = float(
        market["pricing"]["baseline_real_tce_2025_usd_per_day"]
    )
    records: list[dict[str, Any]] = []
    turn_index = 0

    for month in months:
        year = int(month["year"])
        month_number = int(month["month"])
        cargo_mbd = float(month["cargo_mbd"])
        real_oil_price_index = float(month["real_oil_price_index"])
        nominal_brent = float(month["nominal_brent_usd_per_bbl"])
        if cargo_mbd <= 0.0:
            raise ValueError("route cargo must remain positive")
        if year not in cpi_by_year:
            raise KeyError(f"missing CPI for {year}")
        cpi = float(cpi_by_year[year])
        if cpi <= 0.0:
            raise ValueError("CPI must remain positive")
        real_bunker_price = _real_bunker_price(real_oil_price_index, costs)
        nominal_bunker_price = real_bunker_price * cpi / 100.0
        real_opex_per_day = float(
            costs["base_real_opex_2025_usd_per_vessel_day"]
        )
        nominal_opex_per_day = real_opex_per_day * cpi / 100.0

        for turn_in_month, turn_days in enumerate(
            shipping_turn_days(int(month["days"])),
            start=1,
        ):
            returning_ids = sorted(
                ship_id
                for ship_id, voyage in active_voyages.items()
                if int(voyage["return_turn"]) == turn_index
            )
            for ship_id in returning_ids:
                del active_voyages[ship_id]
                idle_ships.append(ship_id)
            idle_ships.sort()

            arriving_deviation = float(arrival_queue.pop(turn_index, 0.0))
            transit_deviation -= arriving_deviation
            east_asia_deviation += arriving_deviation

            structural_cargo = cargo_mbd * turn_days
            opening_gap = 0.5 * (gulf_deviation - east_asia_deviation)
            opening_gap_days = opening_gap / cargo_mbd
            correction = clamp(
                float(inventory_cfg["clearance_fraction_per_turn"]) * opening_gap,
                -float(
                    inventory_cfg[
                        "maximum_correction_fraction_of_structural_flow"
                    ]
                )
                * structural_cargo,
                float(
                    inventory_cfg[
                        "maximum_correction_fraction_of_structural_flow"
                    ]
                )
                * structural_cargo,
            )
            desired_load = max(0.0, structural_cargo + correction)
            raw_desired_fixtures = desired_load / cargo_capacity + fixture_carry
            desired_fixtures = max(0, _nearest_int(raw_desired_fixtures))
            fixture_carry = raw_desired_fixtures - desired_fixtures

            available_before_dispatch = len(idle_ships)
            selection = _select_dispatch(
                available_vlcc=available_before_dispatch,
                desired_fixtures=desired_fixtures,
                structural_cargo_mmbbl=structural_cargo,
                cargo_mbd=cargo_mbd,
                gulf_deviation=gulf_deviation,
                east_asia_deviation=east_asia_deviation,
                opening_gap_days=opening_gap_days,
                previous_real_tce=previous_real_tce,
                real_bunker_price=real_bunker_price,
                turn_days=turn_days,
                control=controls.get(turn_index, {}),
                config=market,
            )
            dispatched = int(selection["dispatch_vlcc"])
            voyage_terms = _voyage_terms(
                real_tce=float(selection["real_tce"]),
                real_bunker_price=real_bunker_price,
                cpi=cpi,
                config=market,
            )

            dispatched_ids = idle_ships[:dispatched]
            del idle_ships[:dispatched]
            for ship_id in dispatched_ids:
                active_voyages[ship_id] = _new_voyage(
                    ship_id=ship_id,
                    departure_turn=turn_index,
                    cargo_mmbbl=cargo_capacity,
                    terms=voyage_terms,
                    opening_pipeline=False,
                    config=market,
                )

            loaded_cargo = dispatched * cargo_capacity
            load_deviation = loaded_cargo - structural_cargo
            gulf_deviation -= load_deviation
            transit_deviation += load_deviation
            arrival_queue[
                turn_index + int(fleet_cfg["cargo_arrival_lag_turns"])
            ] += load_deviation

            closing_gap = 0.5 * (gulf_deviation - east_asia_deviation)
            closing_gap_days = closing_gap / cargo_mbd
            conservation_residual = (
                gulf_deviation + transit_deviation + east_asia_deviation
            )

            accrual = _accrue_active_voyages(
                active_voyages,
                turn_index=turn_index,
            )
            idle_fuel_tonnes = (
                len(idle_ships)
                * float(costs["idle_fuel_tonnes_per_day"])
                * turn_days
            )
            real_idle_bunker_cost = idle_fuel_tonnes * real_bunker_price
            nominal_idle_bunker_cost = idle_fuel_tonnes * nominal_bunker_price
            real_opex = total_fleet * real_opex_per_day * turn_days
            nominal_opex = total_fleet * nominal_opex_per_day * turn_days
            real_voyage_expense = (
                float(accrual["real_commission"])
                + float(accrual["real_bunker"])
                + float(accrual["real_port"])
                + float(accrual["real_other"])
            )
            nominal_voyage_expense = (
                float(accrual["nominal_commission"])
                + float(accrual["nominal_bunker"])
                + float(accrual["nominal_port"])
                + float(accrual["nominal_other"])
            )
            real_operating_cashflow = (
                float(accrual["real_gross_freight_revenue"])
                - real_voyage_expense
                - real_idle_bunker_cost
                - real_opex
            )
            nominal_operating_cashflow = (
                float(accrual["nominal_gross_freight_revenue"])
                - nominal_voyage_expense
                - nominal_idle_bunker_cost
                - nominal_opex
            )

            active_ids = set(active_voyages)
            idle_ids = set(idle_ships)
            fleet_residual = len(active_ids | idle_ids) - total_fleet
            duplicate_ship_count = len(active_ids & idle_ids)
            missing_ship_count = len(all_ship_ids - (active_ids | idle_ids))
            extra_ship_count = len((active_ids | idle_ids) - all_ship_ids)
            phase_counts = accrual["phase_counts"]
            previous_real_tce = float(selection["real_tce"])

            records.append(
                {
                    "seed": int(seed),
                    "shipping_turn_index": turn_index,
                    "year": year,
                    "month": month_number,
                    "shipping_turn_in_month": turn_in_month,
                    "label": f"{year}-{month_number:02d}.{turn_in_month}",
                    "turn_days": int(turn_days),
                    "route_id": str(market["route_id"]),
                    "structural_route_cargo_mbd": round(cargo_mbd, 8),
                    "structural_cargo_mmbbl": round(structural_cargo, 8),
                    "desired_load_mmbbl": round(desired_load, 8),
                    "desired_fixture_vlcc": desired_fixtures,
                    "available_gulf_vlcc_before_dispatch": available_before_dispatch,
                    "policy_dispatch_vlcc": int(selection["policy_dispatch_vlcc"]),
                    "dispatch_vlcc": dispatched,
                    "control_mode": str(selection["control_mode"]),
                    "strategically_withheld_vlcc": int(selection["withheld_vlcc"]),
                    "physical_shortage_vlcc": int(selection["physical_shortage_vlcc"]),
                    "loaded_cargo_mmbbl": round(loaded_cargo, 8),
                    "load_deviation_mmbbl": round(load_deviation, 8),
                    "cargo_arrival_deviation_mmbbl": round(arriving_deviation, 8),
                    "gulf_inventory_deviation_mmbbl": round(gulf_deviation, 8),
                    "in_transit_deviation_mmbbl": round(transit_deviation, 8),
                    "east_asia_inventory_deviation_mmbbl": round(
                        east_asia_deviation,
                        8,
                    ),
                    "inventory_conservation_residual_mmbbl": round(
                        conservation_residual,
                        10,
                    ),
                    "opening_inventory_gap_days": round(opening_gap_days, 8),
                    "inventory_gap_days": round(closing_gap_days, 8),
                    "inventory_shadow_cost_real_usd": round(
                        float(selection["inventory_shadow_cost_real_usd"]),
                        2,
                    ),
                    "monopoly_dispatch_objective_real_usd": round(
                        float(selection["objective_real_usd"]),
                        2,
                    ),
                    "fixed_fleet_vlcc": total_fleet,
                    "active_voyage_vlcc": len(active_voyages),
                    "gulf_idle_vlcc": len(idle_ships),
                    "returning_to_gulf_vlcc": len(returning_ids),
                    "fleet_utilization_pct": round(
                        100.0 * len(active_voyages) / total_fleet,
                        6,
                    ),
                    "loading_and_early_laden_vlcc": int(
                        phase_counts["loading_and_early_laden"]
                    ),
                    "laden_vlcc": int(phase_counts["laden"]),
                    "late_laden_and_discharge_vlcc": int(
                        phase_counts["late_laden_and_discharge"]
                    ),
                    "ballast_1_vlcc": int(phase_counts["ballast_1"]),
                    "ballast_2_vlcc": int(phase_counts["ballast_2"]),
                    "fleet_conservation_residual_vlcc": fleet_residual,
                    "duplicate_ship_count": duplicate_ship_count,
                    "missing_ship_count": missing_ship_count,
                    "extra_ship_count": extra_ship_count,
                    "real_oil_price_index": round(real_oil_price_index, 8),
                    "nominal_brent_usd_per_bbl": round(nominal_brent, 8),
                    "real_bunker_2025_usd_per_tonne": round(
                        real_bunker_price,
                        2,
                    ),
                    "nominal_bunker_usd_per_tonne": round(
                        nominal_bunker_price,
                        2,
                    ),
                    "real_opex_2025_usd_per_vessel_day": round(
                        real_opex_per_day,
                        2,
                    ),
                    "nominal_opex_usd_per_vessel_day": round(
                        nominal_opex_per_day,
                        2,
                    ),
                    "real_tce_2025_usd_per_day": round(
                        float(selection["real_tce"]),
                        2,
                    ),
                    "nominal_tce_usd_per_day": round(
                        float(voyage_terms["nominal_tce"]),
                        2,
                    ),
                    "real_gross_freight_per_voyage_usd": round(
                        float(voyage_terms["real_gross_freight"]),
                        2,
                    ),
                    "nominal_gross_freight_per_voyage_usd": round(
                        float(voyage_terms["nominal_gross_freight"]),
                        2,
                    ),
                    "real_voyage_expense_per_voyage_usd": round(
                        float(voyage_terms["real_commission"])
                        + float(voyage_terms["real_bunker_cost"])
                        + float(voyage_terms["real_port_cost"])
                        + float(voyage_terms["real_other_voyage_cost"]),
                        2,
                    ),
                    "nominal_voyage_expense_per_voyage_usd": round(
                        float(voyage_terms["nominal_commission"])
                        + float(voyage_terms["nominal_bunker_cost"])
                        + float(voyage_terms["nominal_port_cost"])
                        + float(voyage_terms["nominal_other_voyage_cost"]),
                        2,
                    ),
                    "real_net_voyage_revenue_per_voyage_usd": round(
                        float(voyage_terms["real_net_voyage_revenue"]),
                        2,
                    ),
                    "nominal_net_voyage_revenue_per_voyage_usd": round(
                        float(voyage_terms["nominal_net_voyage_revenue"]),
                        2,
                    ),
                    "real_operating_margin_per_voyage_after_opex_usd": round(
                        (
                            float(selection["real_tce"]) - real_opex_per_day
                        )
                        * float(voyage_terms["cycle_days"]),
                        2,
                    ),
                    "nominal_operating_margin_per_voyage_after_opex_usd": round(
                        (
                            float(voyage_terms["nominal_tce"])
                            - nominal_opex_per_day
                        )
                        * float(voyage_terms["cycle_days"]),
                        2,
                    ),
                    "real_accrued_gross_freight_revenue_usd": round(
                        float(accrual["real_gross_freight_revenue"]),
                        2,
                    ),
                    "real_accrued_voyage_expense_usd": round(
                        real_voyage_expense,
                        2,
                    ),
                    "real_idle_bunker_cost_usd": round(
                        real_idle_bunker_cost,
                        2,
                    ),
                    "real_fleet_opex_usd": round(real_opex, 2),
                    "real_operating_cashflow_2025_usd": round(
                        real_operating_cashflow,
                        2,
                    ),
                    "nominal_accrued_gross_freight_revenue_usd": round(
                        float(accrual["nominal_gross_freight_revenue"]),
                        2,
                    ),
                    "nominal_accrued_voyage_expense_usd": round(
                        nominal_voyage_expense,
                        2,
                    ),
                    "nominal_idle_bunker_cost_usd": round(
                        nominal_idle_bunker_cost,
                        2,
                    ),
                    "nominal_fleet_opex_usd": round(nominal_opex, 2),
                    "nominal_operating_cashflow_usd": round(
                        nominal_operating_cashflow,
                        2,
                    ),
                    "cpi_price_level_index_2025_100": round(cpi, 8),
                }
            )
            turn_index += 1

    summary = summarize_gulf_east_asia_monopoly(records)
    return {
        "identity": {
            "model_version": MODEL_VERSION,
            "seed": int(seed),
            "route_id": str(market["route_id"]),
            "owner_count": 1,
            "fixed_fleet_vlcc": total_fleet,
            "cycle_turns": int(fleet_cfg["cycle_turns"]),
            "cargo_arrival_lag_turns": int(
                fleet_cfg["cargo_arrival_lag_turns"]
            ),
            "cost_scope": (
                "gross_freight_minus_commission_bunker_port_other_"
                "minus_all_fleet_opex_and_idle_bunker"
            ),
            "excluded_scope": (
                "depreciation_debt_interest_ship_value_newbuildings_scrapping"
            ),
            "price_basis": "real_2025_usd_then_cpi_to_nominal",
            "upstream_cargo_owner": "oil_shipping_world",
        },
        "turns": tuple(records),
        "summary": summary,
    }


def summarize_gulf_east_asia_monopoly(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not records:
        raise ValueError("summary requires records")

    real_tce = [float(row["real_tce_2025_usd_per_day"]) for row in records]
    nominal_tce = [float(row["nominal_tce_usd_per_day"]) for row in records]
    demand = [float(row["structural_route_cargo_mbd"]) for row in records]
    idle = [int(row["gulf_idle_vlcc"]) for row in records]
    utilization = [float(row["fleet_utilization_pct"]) for row in records]
    gap = [abs(float(row["inventory_gap_days"])) for row in records]
    real_cash = [float(row["real_operating_cashflow_2025_usd"]) for row in records]
    nominal_cash = [float(row["nominal_operating_cashflow_usd"]) for row in records]
    dispatch = [int(row["dispatch_vlcc"]) for row in records]
    withheld = [int(row["strategically_withheld_vlcc"]) for row in records]
    physical_shortage = [int(row["physical_shortage_vlcc"]) for row in records]
    real_opex = [
        float(row["real_opex_2025_usd_per_vessel_day"]) for row in records
    ]
    gross = [
        float(row["real_gross_freight_per_voyage_usd"]) for row in records
    ]

    return {
        "turn_count": len(records),
        "fixed_fleet_vlcc": int(records[0]["fixed_fleet_vlcc"]),
        "structural_route_cargo_mbd_mean": round(statistics.fmean(demand), 6),
        "structural_route_cargo_mbd_min": round(min(demand), 6),
        "structural_route_cargo_mbd_max": round(max(demand), 6),
        "dispatch_vlcc_mean": round(statistics.fmean(dispatch), 4),
        "dispatch_vlcc_min": min(dispatch),
        "dispatch_vlcc_max": max(dispatch),
        "gulf_idle_vlcc_mean": round(statistics.fmean(idle), 4),
        "gulf_idle_vlcc_min": min(idle),
        "gulf_idle_vlcc_max": max(idle),
        "fleet_utilization_pct_mean": round(statistics.fmean(utilization), 6),
        "fleet_utilization_pct_min": round(min(utilization), 6),
        "fleet_utilization_pct_max": round(max(utilization), 6),
        "strategically_withheld_vlcc_total": sum(withheld),
        "strategically_withheld_vlcc_mean": round(
            statistics.fmean(withheld),
            6,
        ),
        "physical_shortage_vlcc_total": sum(physical_shortage),
        "p95_abs_inventory_gap_days": round(_percentile(gap, 0.95), 6),
        "maximum_abs_inventory_gap_days": round(max(gap), 6),
        "real_tce_2025_usd_per_day_p05": round(
            _percentile(real_tce, 0.05),
            2,
        ),
        "real_tce_2025_usd_per_day_median": round(
            _percentile(real_tce, 0.50),
            2,
        ),
        "real_tce_2025_usd_per_day_p95": round(
            _percentile(real_tce, 0.95),
            2,
        ),
        "real_tce_2025_usd_per_day_min": round(min(real_tce), 2),
        "real_tce_2025_usd_per_day_max": round(max(real_tce), 2),
        "nominal_tce_usd_per_day_p05": round(
            _percentile(nominal_tce, 0.05),
            2,
        ),
        "nominal_tce_usd_per_day_median": round(
            _percentile(nominal_tce, 0.50),
            2,
        ),
        "nominal_tce_usd_per_day_p95": round(
            _percentile(nominal_tce, 0.95),
            2,
        ),
        "nominal_tce_usd_per_day_max": round(max(nominal_tce), 2),
        "real_gross_freight_per_voyage_usd_median": round(
            _percentile(gross, 0.50),
            2,
        ),
        "real_opex_2025_usd_per_vessel_day": round(
            statistics.fmean(real_opex),
            2,
        ),
        "turns_tce_below_opex": sum(
            1
            for tce, opex in zip(real_tce, real_opex)
            if tce < opex
        ),
        "turns_real_operating_cashflow_negative": sum(
            1 for value in real_cash if value < 0.0
        ),
        "cumulative_real_operating_cashflow_2025_usd": round(
            sum(real_cash),
            2,
        ),
        "cumulative_nominal_operating_cashflow_usd": round(
            sum(nominal_cash),
            2,
        ),
        "real_operating_cashflow_2025_usd_p05": round(
            _percentile(real_cash, 0.05),
            2,
        ),
        "real_operating_cashflow_2025_usd_median": round(
            _percentile(real_cash, 0.50),
            2,
        ),
        "real_operating_cashflow_2025_usd_p95": round(
            _percentile(real_cash, 0.95),
            2,
        ),
        "maximum_abs_inventory_conservation_residual_mmbbl": round(
            max(
                abs(float(row["inventory_conservation_residual_mmbbl"]))
                for row in records
            ),
            10,
        ),
        "maximum_abs_fleet_conservation_residual_vlcc": max(
            abs(int(row["fleet_conservation_residual_vlcc"]))
            for row in records
        ),
        "maximum_duplicate_ship_count": max(
            int(row["duplicate_ship_count"]) for row in records
        ),
        "maximum_missing_ship_count": max(
            int(row["missing_ship_count"]) for row in records
        ),
        "maximum_extra_ship_count": max(
            int(row["extra_ship_count"]) for row in records
        ),
        "cpi_price_level_start": round(
            float(records[0]["cpi_price_level_index_2025_100"]),
            6,
        ),
        "cpi_price_level_end": round(
            float(records[-1]["cpi_price_level_index_2025_100"]),
            6,
        ),
    }


def run_gulf_east_asia_monopoly_operations(
    global_run: GlobalMacroRun,
    shipping_world: OilShippingWorld,
    *,
    config: Mapping[str, Any] | None = None,
    control_by_turn: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if int(global_run.seed) != int(shipping_world.seed):
        raise ValueError("macro and shipping worlds must use the same seed")
    market = load_gulf_east_asia_monopoly_config() if config is None else dict(config)
    months = monthly_gulf_east_asia_inputs(
        shipping_world,
        route_id=str(market["route_id"]),
    )
    cpi = {
        int(row["year"]): float(row["cpi_price_level_index_2025_100"])
        for row in global_run.rows
    }
    return simulate_gulf_east_asia_monopoly_operations(
        months,
        seed=int(global_run.seed),
        cpi_by_year=cpi,
        config=market,
        control_by_turn=control_by_turn,
    )


def run_seeded_gulf_east_asia_monopoly_operations(
    seed: int = 42,
    years: int = 20,
    *,
    control_by_turn: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    from .engine import run_global_macro
    from .oil_shipping_world import run_oil_shipping_world

    macro = run_global_macro(seed, years)
    shipping = run_oil_shipping_world(macro)
    return run_gulf_east_asia_monopoly_operations(
        macro,
        shipping,
        control_by_turn=control_by_turn,
    )
