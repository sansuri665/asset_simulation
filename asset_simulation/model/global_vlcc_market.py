"""Globally constrained multi-route VLCC spot-market prototype."""

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
from .vlcc_spot_market import shipping_turn_days


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PACKAGE_ROOT / "config" / "global_vlcc_market_v0.1.json"
MODEL_VERSION = "asset-simulation-global-vlcc-spot-market-prototype-v0.1.0"


@lru_cache(maxsize=1)
def load_global_vlcc_market_config() -> dict[str, Any]:
    value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("global VLCC config must be an object")
    _validate_config(value)
    return value


def _validate_config(config: Mapping[str, Any]) -> None:
    if str(config["model_version"]) != MODEL_VERSION:
        raise ValueError("global VLCC model version mismatch")
    if int(config["shipping_turns_per_month"]) != 3:
        raise ValueError("prototype requires three shipping turns per month")
    if float(config["reference_turn_days"]) <= 0.0:
        raise ValueError("reference_turn_days must be positive")
    if float(config["vlcc_cargo_mmbbl"]) <= 0.0:
        raise ValueError("vlcc_cargo_mmbbl must be positive")
    routes = list(config["routes"])
    route_ids = [str(route["route_id"]) for route in routes]
    if len(route_ids) != len(set(route_ids)):
        raise ValueError("route ids must be unique")
    residual = [
        route for route in routes
        if str(route.get("source_mode", "")) == "residual_tonne_mile_index"
    ]
    if len(residual) != 1:
        raise ValueError("exactly one residual VLCC market is required")
    sources = [
        str(route["source_route_id"])
        for route in routes if "source_route_id" in route
    ]
    if len(sources) != len(set(sources)):
        raise ValueError("source route ids must be unique")
    for route in routes:
        if int(route["cycle_turns"]) <= 0:
            raise ValueError("cycle_turns must be positive")
        if int(route["cargo_arrival_lag_turns"]) <= 0:
            raise ValueError("cargo arrival lag must be positive")
        if int(route["reference_route_fleet_vlcc"]) <= 0:
            raise ValueError("reference route fleet must be positive")
        if "source_route_id" in route and not (
            0.0 < float(route["vlcc_share"]) <= 1.0
        ):
            raise ValueError("vlcc_share must lie in (0, 1]")
    fleet = config["fleet"]
    total = int(fleet["total_vlcc"])
    closed = (
        sum(int(route["reference_route_fleet_vlcc"]) for route in routes)
        + int(fleet["initial_idle_vlcc"])
        + int(fleet["unavailable_vlcc"])
    )
    if total <= 0 or closed != total:
        raise ValueError("reference route, idle and unavailable fleet must close")
    for key in (
        "maximum_global_reposition_vlcc_per_turn",
        "maximum_route_reposition_vlcc_per_turn",
        "route_to_route_lag_turns",
        "idle_to_route_lag_turns",
        "route_to_idle_lag_turns",
    ):
        if int(fleet[key]) <= 0:
            raise ValueError(f"{key} must be positive")


def _nearest_int(value: float) -> int:
    return (
        int(math.floor(value + 0.5))
        if value >= 0.0
        else -int(math.floor(abs(value) + 0.5))
    )


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("percentile requires values")
    position = probability * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def monthly_global_vlcc_inputs(
    shipping_world: OilShippingWorld,
    *,
    config: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Map actual monthly crude routes into five named and one residual VLCC market."""

    market = load_global_vlcc_market_config() if config is None else dict(config)
    _validate_config(market)
    named = [route for route in market["routes"] if "source_route_id" in route]
    residual = next(
        route for route in market["routes"]
        if route.get("source_mode") == "residual_tonne_mile_index"
    )
    named_sources = {str(route["source_route_id"]) for route in named}
    residual_reference_cargo = (
        int(residual["reference_route_fleet_vlcc"])
        * float(market["vlcc_cargo_mmbbl"])
        / (
            int(residual["cycle_turns"])
            * float(market["reference_turn_days"])
        )
    )
    months: list[dict[str, Any]] = []
    for month in shipping_world.turns:
        source = {
            str(route["route_id"]): route for route in month["routes"]
        }
        route_cargo = {}
        for route in named:
            source_id = str(route["source_route_id"])
            if source_id not in source:
                raise KeyError(f"missing shipping route: {source_id}")
            route_cargo[str(route["route_id"])] = (
                float(source[source_id]["cargo_mbd"])
                * float(route["vlcc_share"])
            )
        residual_rows = [
            row for route_id, row in source.items()
            if route_id not in named_sources
        ]
        current_proxy = sum(
            float(row["cargo_mbd"]) * float(row["effective_haul_nm"])
            for row in residual_rows
        )
        reference_proxy = sum(
            float(row["reference_cargo_mbd"]) * float(row["baseline_haul_nm"])
            for row in residual_rows
        )
        if min(current_proxy, reference_proxy) <= 0.0:
            raise ValueError("residual tonne-mile proxy must be positive")
        route_cargo[str(residual["route_id"])] = (
            residual_reference_cargo * current_proxy / reference_proxy
        )
        months.append(
            {
                "year": int(month["year"]),
                "month": int(month["month"]),
                "days": int(month["days"]),
                "route_cargo_mbd": route_cargo,
                "residual_tonne_mile_index": current_proxy / reference_proxy,
            }
        )
    return tuple(months)


def _initial_fleet(config: Mapping[str, Any]) -> dict[str, Any]:
    total = int(config["fleet"]["total_vlcc"])
    ids = [f"VLCC-{index:04d}" for index in range(1, total + 1)]
    cursor = 0
    route_ships = {}
    for route in config["routes"]:
        count = int(route["reference_route_fleet_vlcc"])
        route_ships[str(route["route_id"])] = ids[cursor : cursor + count]
        cursor += count
    idle_count = int(config["fleet"]["initial_idle_vlcc"])
    idle = ids[cursor : cursor + idle_count]
    cursor += idle_count
    unavailable_count = int(config["fleet"]["unavailable_vlcc"])
    unavailable = ids[cursor : cursor + unavailable_count]
    cursor += unavailable_count
    if cursor != total:
        raise ValueError("numbered VLCC initialization failed")
    return {
        "all_ids": tuple(ids),
        "route_ships": route_ships,
        "idle": idle,
        "unavailable": unavailable,
        "transfers": defaultdict(list),
    }


def _queued_ids(transfers: Mapping[int, Sequence[Mapping[str, Any]]]) -> list[str]:
    return [
        str(ship_id)
        for rows in transfers.values()
        for row in rows
        for ship_id in row["ship_ids"]
    ]


def _pending_destinations(
    transfers: Mapping[int, Sequence[Mapping[str, Any]]],
) -> dict[str, int]:
    pending: defaultdict[str, int] = defaultdict(int)
    for rows in transfers.values():
        for row in rows:
            pending[str(row["destination_id"])] += len(row["ship_ids"])
    return dict(pending)


def _assert_fleet(state: Mapping[str, Any]) -> None:
    located = [
        *(ship for ships in state["route_ships"].values() for ship in ships),
        *state["idle"],
        *state["unavailable"],
        *_queued_ids(state["transfers"]),
    ]
    if len(located) != len(state["all_ids"]):
        raise AssertionError("global VLCC count changed")
    if len(set(located)) != len(located):
        raise AssertionError("a VLCC occupies more than one market state")
    if set(located) != set(state["all_ids"]):
        raise AssertionError("a VLCC disappeared or was created")


def _pop_ships(ships: list[str], count: int) -> tuple[str, ...]:
    if not 0 <= count <= len(ships):
        raise ValueError("invalid ship transfer count")
    selected = tuple(ships[-count:]) if count else ()
    if count:
        del ships[-count:]
    return selected


def _execute_route(
    *,
    route: Mapping[str, Any],
    cargo_mbd: float,
    turn_days: int,
    turn_index: int,
    route_fleet: int,
    cargo_capacity: float,
    reference_turn_days: float,
    clearance_fraction: float,
    max_correction_fraction: float,
    capacity_carry: float,
    deviation: dict[str, float],
    cargo_arrivals: defaultdict[int, float],
) -> dict[str, Any]:
    arriving = float(cargo_arrivals.pop(turn_index, 0.0))
    deviation["transit"] -= arriving
    deviation["destination"] += arriving
    structural_cargo = cargo_mbd * turn_days
    opening_gap = 0.5 * (deviation["origin"] - deviation["destination"])
    opening_gap_days = opening_gap / cargo_mbd
    correction = clamp(
        clearance_fraction * opening_gap,
        -max_correction_fraction * structural_cargo,
        max_correction_fraction * structural_cargo,
    )
    desired_load = max(0.0, structural_cargo + correction)
    desired_fixtures = max(0, _nearest_int(desired_load / cargo_capacity))
    raw_capacity = (
        route_fleet
        / int(route["cycle_turns"])
        * turn_days
        / reference_turn_days
        + capacity_carry
    )
    available_fixtures = max(0, int(math.floor(raw_capacity + 1e-12)))
    loaded_fixtures = min(desired_fixtures, available_fixtures)
    loaded_cargo = loaded_fixtures * cargo_capacity
    load_deviation = loaded_cargo - structural_cargo
    deviation["origin"] -= load_deviation
    deviation["transit"] += load_deviation
    cargo_arrivals[
        turn_index + int(route["cargo_arrival_lag_turns"])
    ] += load_deviation
    closing_gap = 0.5 * (deviation["origin"] - deviation["destination"])
    reference_fixture_capacity = (
        int(route["reference_route_fleet_vlcc"])
        / int(route["cycle_turns"])
        * turn_days
        / reference_turn_days
    )
    return {
        "cargo_mbd": cargo_mbd,
        "structural_cargo": structural_cargo,
        "desired_load": desired_load,
        "desired_fixtures": desired_fixtures,
        "available_fixtures": available_fixtures,
        "loaded_fixtures": loaded_fixtures,
        "unfilled_fixtures": max(0, desired_fixtures - loaded_fixtures),
        "loaded_cargo": loaded_cargo,
        "load_deviation": load_deviation,
        "arriving_cargo_deviation": arriving,
        "opening_gap": opening_gap,
        "opening_gap_days": opening_gap_days,
        "closing_gap": closing_gap,
        "closing_gap_days": closing_gap / cargo_mbd,
        "capacity_carry": raw_capacity - available_fixtures,
        "structural_required_fleet": (
            cargo_mbd
            * int(route["cycle_turns"])
            * reference_turn_days
            / cargo_capacity
        ),
        "prompt_shortage_ratio": (
            desired_fixtures - available_fixtures
        ) / max(reference_fixture_capacity, 1.0),
    }


def _schedule_transfer(
    *,
    state: dict[str, Any],
    source_id: str,
    destination_id: str,
    count: int,
    arrival_turn: int,
) -> dict[str, Any] | None:
    if count <= 0:
        return None
    source = (
        state["idle"]
        if source_id == "idle"
        else state["route_ships"][source_id]
    )
    ship_ids = _pop_ships(source, count)
    state["transfers"][arrival_turn].append(
        {
            "source_id": source_id,
            "destination_id": destination_id,
            "ship_ids": ship_ids,
        }
    )
    return {
        "source_id": source_id,
        "destination_id": destination_id,
        "ship_count": count,
        "arrival_turn_index": arrival_turn,
    }


def _clear_ship_market(
    *,
    state: dict[str, Any],
    calculations: Mapping[str, Mapping[str, Any]],
    route_ids: Sequence[str],
    turn_index: int,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    fleet = config["fleet"]
    max_global = int(fleet["maximum_global_reposition_vlcc_per_turn"])
    max_route = int(fleet["maximum_route_reposition_vlcc_per_turn"])
    deadband = float(fleet["target_deadband_vlcc"])
    pending = _pending_destinations(state["transfers"])
    requests, donors = {}, {}
    for route_id in route_ids:
        target = float(calculations[route_id]["target_fleet"])
        projected = len(state["route_ships"][route_id]) + pending.get(route_id, 0)
        requests[route_id] = min(
            max_route,
            max(0, int(math.ceil(target - projected - deadband))),
        )
        donors[route_id] = min(
            max_route,
            max(
                0,
                int(
                    math.floor(
                        len(state["route_ships"][route_id])
                        - target
                        - deadband
                    )
                ),
            ),
        )
    high_bid = sorted(
        route_ids,
        key=lambda route_id: float(calculations[route_id]["bid_score"]),
        reverse=True,
    )
    low_bid = list(reversed(high_bid))
    remaining = max_global
    destination_used = defaultdict(int)
    source_used = defaultdict(int)
    events: list[dict[str, Any]] = []

    for destination_id in high_bid:
        need = requests[destination_id]
        count = min(need, len(state["idle"]), max_route, remaining)
        event = _schedule_transfer(
            state=state,
            source_id="idle",
            destination_id=destination_id,
            count=count,
            arrival_turn=turn_index + int(fleet["idle_to_route_lag_turns"]),
        )
        if event:
            events.append(event)
            destination_used[destination_id] += count
            remaining -= count

    for destination_id in high_bid:
        need = requests[destination_id] - destination_used[destination_id]
        for source_id in low_bid:
            if need <= 0 or remaining <= 0:
                break
            if source_id == destination_id:
                continue
            available = donors[source_id] - source_used[source_id]
            count = min(
                need,
                available,
                max_route - destination_used[destination_id],
                remaining,
            )
            event = _schedule_transfer(
                state=state,
                source_id=source_id,
                destination_id=destination_id,
                count=count,
                arrival_turn=turn_index + int(fleet["route_to_route_lag_turns"]),
            )
            if event:
                events.append(event)
                source_used[source_id] += count
                destination_used[destination_id] += count
                remaining -= count
                need -= count

    active = (
        int(fleet["total_vlcc"]) - int(fleet["unavailable_vlcc"])
    )
    desired_idle = max(
        0,
        _nearest_int(
            active
            - sum(float(calculations[route_id]["target_fleet"]) for route_id in route_ids)
        ),
    )
    pending = _pending_destinations(state["transfers"])
    idle_need = max(0, desired_idle - len(state["idle"]) - pending.get("idle", 0))
    for source_id in low_bid:
        if idle_need <= 0 or remaining <= 0:
            break
        available = donors[source_id] - source_used[source_id]
        count = min(idle_need, available, remaining)
        event = _schedule_transfer(
            state=state,
            source_id=source_id,
            destination_id="idle",
            count=count,
            arrival_turn=turn_index + int(fleet["route_to_idle_lag_turns"]),
        )
        if event:
            events.append(event)
            source_used[source_id] += count
            remaining -= count
            idle_need -= count
    return events


def simulate_global_vlcc_spot_market(
    months: Sequence[Mapping[str, Any]],
    *,
    seed: int,
    cpi_by_year: Mapping[int, float],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    market = load_global_vlcc_market_config() if config is None else dict(config)
    _validate_config(market)
    routes = {str(route["route_id"]): dict(route) for route in market["routes"]}
    route_ids = tuple(routes)
    cargo_capacity = float(market["vlcc_cargo_mmbbl"])
    reference_turn_days = float(market["reference_turn_days"])
    state = _initial_fleet(market)
    deviations = {
        route_id: {"origin": 0.0, "transit": 0.0, "destination": 0.0}
        for route_id in route_ids
    }
    cargo_arrivals = {
        route_id: defaultdict(float) for route_id in route_ids
    }
    capacity_carry = {route_id: 0.0 for route_id in route_ids}
    previous_tce = {
        route_id: float(market["freight"]["baseline_real_tce_2025_usd_per_day"])
        for route_id in route_ids
    }
    fleet_cfg = market["fleet"]
    active_market_fleet = (
        int(fleet_cfg["total_vlcc"]) - int(fleet_cfg["unavailable_vlcc"])
    )
    reference_tightness = (
        sum(int(route["reference_route_fleet_vlcc"]) for route in routes.values())
        / active_market_fleet
    )
    records: list[dict[str, Any]] = []
    turn_index = 0

    for month in months:
        year = int(month["year"])
        monthly_cargo = {
            str(route_id): float(value)
            for route_id, value in month["route_cargo_mbd"].items()
        }
        if set(monthly_cargo) != set(route_ids):
            raise ValueError("monthly routes do not match global VLCC config")
        cpi = float(cpi_by_year[year])
        for turn_in_month, turn_days in enumerate(
            shipping_turn_days(int(month["days"])),
            start=1,
        ):
            arrivals = []
            for transfer in state["transfers"].pop(turn_index, []):
                destination = str(transfer["destination_id"])
                ships = list(transfer["ship_ids"])
                (
                    state["idle"]
                    if destination == "idle"
                    else state["route_ships"][destination]
                ).extend(ships)
                arrivals.append(
                    {
                        "source_id": str(transfer["source_id"]),
                        "destination_id": destination,
                        "ship_count": len(ships),
                    }
                )

            opening_fleet = {
                route_id: len(state["route_ships"][route_id])
                for route_id in route_ids
            }
            calculations = {}
            for route_id in route_ids:
                calculations[route_id] = _execute_route(
                    route=routes[route_id],
                    cargo_mbd=monthly_cargo[route_id],
                    turn_days=turn_days,
                    turn_index=turn_index,
                    route_fleet=opening_fleet[route_id],
                    cargo_capacity=cargo_capacity,
                    reference_turn_days=reference_turn_days,
                    clearance_fraction=float(
                        market["inventory"]["clearance_fraction_per_turn"]
                    ),
                    max_correction_fraction=float(
                        market["inventory"][
                            "maximum_correction_fraction_of_structural_flow"
                        ]
                    ),
                    capacity_carry=capacity_carry[route_id],
                    deviation=deviations[route_id],
                    cargo_arrivals=cargo_arrivals[route_id],
                )
                capacity_carry[route_id] = calculations[route_id]["capacity_carry"]

            required_total = sum(
                float(row["structural_required_fleet"])
                for row in calculations.values()
            )
            repositioning_before = len(_queued_ids(state["transfers"]))
            prompt_effective_fleet = max(
                1,
                active_market_fleet - repositioning_before,
            )
            global_tightness = required_total / prompt_effective_fleet
            global_gap = global_tightness - reference_tightness
            freight = market["freight"]
            base_tce = float(freight["baseline_real_tce_2025_usd_per_day"])
            for route_id, row in calculations.items():
                raw_log = (
                    float(freight["prompt_shortage_log_sensitivity"])
                    * float(row["prompt_shortage_ratio"])
                    + float(freight["inventory_gap_day_log_sensitivity"])
                    * float(row["opening_gap_days"])
                    + float(freight["global_tightness_log_sensitivity"])
                    * global_gap
                )
                settled_log = (
                    float(freight["price_persistence"])
                    * math.log(max(previous_tce[route_id] / base_tce, 1e-12))
                    + (1.0 - float(freight["price_persistence"])) * raw_log
                )
                multiple = clamp(
                    math.exp(settled_log),
                    float(freight["minimum_real_tce_multiple"]),
                    float(freight["maximum_real_tce_multiple"]),
                )
                row["real_tce"] = base_tce * multiple
                row["nominal_tce"] = row["real_tce"] * cpi / 100.0
                previous_tce[route_id] = row["real_tce"]
                row["target_fleet"] = max(
                    1.0,
                    float(row["structural_required_fleet"])
                    + float(market["allocation"]["inventory_gap_gain_vlcc_per_day"])
                    * float(row["closing_gap_days"])
                    + float(market["allocation"]["unfilled_fixture_gain_vlcc"])
                    * int(row["unfilled_fixtures"]),
                )
                row["bid_score"] = (
                    math.log(max(row["real_tce"] / base_tce, 1e-12))
                    + 0.025 * int(row["unfilled_fixtures"])
                    + 0.015 * float(row["closing_gap_days"])
                )

            events = _clear_ship_market(
                state=state,
                calculations=calculations,
                route_ids=route_ids,
                turn_index=turn_index,
                config=market,
            )
            _assert_fleet(state)
            repositioning = len(_queued_ids(state["transfers"]))
            route_fleet_total = sum(len(ships) for ships in state["route_ships"].values())
            residual = (
                route_fleet_total
                + len(state["idle"])
                + len(state["unavailable"])
                + repositioning
                - int(fleet_cfg["total_vlcc"])
            )
            total_loaded = sum(float(row["loaded_cargo"]) for row in calculations.values())
            weight = max(total_loaded, 1e-12)
            global_real_tce = sum(
                float(row["real_tce"]) * float(row["loaded_cargo"])
                for row in calculations.values()
            ) / weight
            route_records = []
            pending = _pending_destinations(state["transfers"])
            for route_id, row in calculations.items():
                deviation = deviations[route_id]
                route_records.append(
                    {
                        "route_id": route_id,
                        "route_name": str(routes[route_id]["route_name"]),
                        "structural_route_cargo_mbd": round(float(row["cargo_mbd"]), 8),
                        "structural_cargo_mmbbl": round(float(row["structural_cargo"]), 8),
                        "desired_fixture_vlcc": int(row["desired_fixtures"]),
                        "available_fixture_vlcc": int(row["available_fixtures"]),
                        "loaded_fixture_vlcc": int(row["loaded_fixtures"]),
                        "unfilled_fixture_vlcc": int(row["unfilled_fixtures"]),
                        "loaded_cargo_mmbbl": round(float(row["loaded_cargo"]), 8),
                        "origin_inventory_deviation_mmbbl": round(deviation["origin"], 8),
                        "in_transit_deviation_mmbbl": round(deviation["transit"], 8),
                        "destination_inventory_deviation_mmbbl": round(
                            deviation["destination"], 8
                        ),
                        "inventory_conservation_residual_mmbbl": round(
                            sum(deviation.values()), 10
                        ),
                        "inventory_gap_days": round(float(row["closing_gap_days"]), 8),
                        "opening_route_fleet_vlcc": opening_fleet[route_id],
                        "closing_route_fleet_vlcc": len(state["route_ships"][route_id]),
                        "pending_inbound_vlcc": pending.get(route_id, 0),
                        "structural_required_fleet_vlcc": round(
                            float(row["structural_required_fleet"]), 8
                        ),
                        "target_route_fleet_vlcc": round(float(row["target_fleet"]), 8),
                        "prompt_shortage_ratio": round(
                            float(row["prompt_shortage_ratio"]), 8
                        ),
                        "real_tce_2025_usd_per_day": round(float(row["real_tce"]), 2),
                        "nominal_tce_usd_per_day": round(
                            float(row["nominal_tce"]), 2
                        ),
                    }
                )
            records.append(
                {
                    "seed": int(seed),
                    "shipping_turn_index": turn_index,
                    "year": year,
                    "month": int(month["month"]),
                    "shipping_turn_in_month": turn_in_month,
                    "label": f"{year}-{int(month['month']):02d}.{turn_in_month}",
                    "turn_days": turn_days,
                    "cpi_price_level_index_2025_100": round(cpi, 8),
                    "global_total_fleet_vlcc": int(fleet_cfg["total_vlcc"]),
                    "global_unavailable_fleet_vlcc": len(state["unavailable"]),
                    "global_route_fleet_vlcc": route_fleet_total,
                    "global_idle_fleet_vlcc": len(state["idle"]),
                    "global_repositioning_fleet_vlcc": repositioning,
                    "global_fleet_conservation_residual_vlcc": residual,
                    "global_structural_required_fleet_vlcc": round(required_total, 8),
                    "global_prompt_effective_fleet_vlcc": prompt_effective_fleet,
                    "global_tightness_ratio": round(global_tightness, 8),
                    "global_loaded_cargo_mmbbl": round(total_loaded, 8),
                    "global_unfilled_fixture_vlcc": sum(
                        int(row["unfilled_fixtures"]) for row in calculations.values()
                    ),
                    "global_real_tce_2025_usd_per_day": round(global_real_tce, 2),
                    "global_nominal_tce_usd_per_day": round(
                        global_real_tce * cpi / 100.0, 2
                    ),
                    "transfer_arrivals": arrivals,
                    "transfer_events": events,
                    "routes": route_records,
                }
            )
            turn_index += 1

    return {
        "model_version": MODEL_VERSION,
        "seed": int(seed),
        "fleet_total_vlcc": int(fleet_cfg["total_vlcc"]),
        "records": tuple(records),
        "summary": summarize_global_vlcc_market(records),
    }


def summarize_global_vlcc_market(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not records:
        raise ValueError("summary requires records")
    global_real = [
        float(row["global_real_tce_2025_usd_per_day"]) for row in records
    ]
    global_nominal = [
        float(row["global_nominal_tce_usd_per_day"]) for row in records
    ]
    per_route = {}
    for route_id in [str(row["route_id"]) for row in records[0]["routes"]]:
        route_rows = [
            next(route for route in record["routes"] if route["route_id"] == route_id)
            for record in records
        ]
        cargo = [float(row["structural_route_cargo_mbd"]) for row in route_rows]
        fleet = [int(row["closing_route_fleet_vlcc"]) for row in route_rows]
        tce = [float(row["real_tce_2025_usd_per_day"]) for row in route_rows]
        gaps = [abs(float(row["inventory_gap_days"])) for row in route_rows]
        per_route[route_id] = {
            "structural_route_cargo_mbd_mean": round(statistics.fmean(cargo), 6),
            "structural_route_cargo_mbd_min": round(min(cargo), 6),
            "structural_route_cargo_mbd_max": round(max(cargo), 6),
            "route_fleet_vlcc_mean": round(statistics.fmean(fleet), 4),
            "route_fleet_vlcc_min": min(fleet),
            "route_fleet_vlcc_max": max(fleet),
            "real_tce_2025_usd_per_day_median": round(statistics.median(tce), 2),
            "real_tce_2025_usd_per_day_p95": round(_percentile(tce, 0.95), 2),
            "real_tce_2025_usd_per_day_max": round(max(tce), 2),
            "p95_abs_inventory_gap_days": round(_percentile(gaps, 0.95), 6),
            "maximum_abs_inventory_gap_days": round(max(gaps), 6),
            "total_unfilled_fixture_vlcc": sum(
                int(row["unfilled_fixture_vlcc"]) for row in route_rows
            ),
        }
    return {
        "turn_count": len(records),
        "global_real_tce_2025_usd_per_day_p05": round(
            _percentile(global_real, 0.05), 2
        ),
        "global_real_tce_2025_usd_per_day_median": round(
            statistics.median(global_real), 2
        ),
        "global_real_tce_2025_usd_per_day_p95": round(
            _percentile(global_real, 0.95), 2
        ),
        "global_real_tce_2025_usd_per_day_max": round(max(global_real), 2),
        "global_nominal_tce_usd_per_day_p05": round(
            _percentile(global_nominal, 0.05), 2
        ),
        "global_nominal_tce_usd_per_day_median": round(
            statistics.median(global_nominal), 2
        ),
        "global_nominal_tce_usd_per_day_p95": round(
            _percentile(global_nominal, 0.95), 2
        ),
        "global_nominal_tce_usd_per_day_max": round(max(global_nominal), 2),
        "global_idle_fleet_vlcc_min": min(
            int(row["global_idle_fleet_vlcc"]) for row in records
        ),
        "global_idle_fleet_vlcc_mean": round(
            statistics.fmean(int(row["global_idle_fleet_vlcc"]) for row in records),
            4,
        ),
        "global_repositioning_fleet_vlcc_max": max(
            int(row["global_repositioning_fleet_vlcc"]) for row in records
        ),
        "global_structural_required_fleet_vlcc_mean": round(
            statistics.fmean(
                float(row["global_structural_required_fleet_vlcc"])
                for row in records
            ),
            4,
        ),
        "global_structural_required_fleet_vlcc_max": round(
            max(
                float(row["global_structural_required_fleet_vlcc"])
                for row in records
            ),
            4,
        ),
        "global_tightness_ratio_mean": round(
            statistics.fmean(float(row["global_tightness_ratio"]) for row in records),
            6,
        ),
        "global_tightness_ratio_max": round(
            max(float(row["global_tightness_ratio"]) for row in records), 6
        ),
        "maximum_abs_fleet_conservation_residual_vlcc": max(
            abs(int(row["global_fleet_conservation_residual_vlcc"]))
            for row in records
        ),
        "total_unfilled_fixture_vlcc": sum(
            int(row["global_unfilled_fixture_vlcc"]) for row in records
        ),
        "total_repositioned_vlcc": sum(
            int(event["ship_count"])
            for row in records for event in row["transfer_events"]
        ),
        "cpi_price_level_start": float(
            records[0]["cpi_price_level_index_2025_100"]
        ),
        "cpi_price_level_end": float(
            records[-1]["cpi_price_level_index_2025_100"]
        ),
        "per_route": per_route,
    }


def run_global_vlcc_spot_market(
    global_run: GlobalMacroRun,
    shipping_world: OilShippingWorld,
    *,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if global_run.seed != shipping_world.seed:
        raise ValueError("macro and shipping worlds must share a seed")
    cpi_by_year = {
        int(row["year"]): float(row["cpi_price_level_index_2025_100"])
        for row in global_run.rows
    }
    return simulate_global_vlcc_spot_market(
        monthly_global_vlcc_inputs(shipping_world, config=config),
        seed=global_run.seed,
        cpi_by_year=cpi_by_year,
        config=config,
    )


def run_seeded_global_vlcc_spot_market(
    seed: int = 42,
    years: int = 20,
) -> dict[str, Any]:
    from .engine import run_global_macro
    from .oil_shipping_world import run_oil_shipping_world

    macro = run_global_macro(seed, years)
    return run_global_vlcc_spot_market(macro, run_oil_shipping_world(macro))
