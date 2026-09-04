"""Pure single-route Gulf-to-East-Asia VLCC pricing engine.

The module deliberately separates market pricing from vessel operations and
owner accounting.  It consumes only the current route demand, prompt VLCC
supply, relative inventory deviations and the previous price.  It returns a
spot TCE benchmark plus transparent tightness diagnostics.

A separate adapter can supply prompt tonnage from any fleet model.  The state
contract assumed by that future supply model is:

    loading: 0 turns
    laden voyage: 2 turns
    East Asia discharge/turnaround: 1 turn
    ballast return: 2 turns

Costs, cash flow, debt, depreciation and ship values are intentionally absent.
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
from .oil_shipping_world import OilShippingWorld


MODEL_VERSION = "asset-simulation-gulf-east-asia-single-route-pricing-v0.2.0"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PACKAGE_ROOT / "config" / "gulf_east_asia_pricing_v0.2.json"


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


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


@lru_cache(maxsize=1)
def load_single_route_pricing_config() -> dict[str, Any]:
    value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("single-route pricing config must be an object")
    _validate_config(value)
    return value


def _validate_config(config: Mapping[str, Any]) -> None:
    if str(config.get("model_version")) != MODEL_VERSION:
        raise ValueError("single-route pricing model version mismatch")
    if int(config["shipping_turns_per_month"]) != 3:
        raise ValueError("pricing engine requires three shipping turns per month")
    if float(config["reference_turn_days"]) <= 0.0:
        raise ValueError("reference turn length must be positive")
    if float(config["vlcc_cargo_mmbbl"]) <= 0.0:
        raise ValueError("VLCC cargo capacity must be positive")

    state = config["state_contract"]
    if int(state["loading_turns"]) != 0:
        raise ValueError("loading must not consume a separate turn")
    if int(state["laden_turns"]) != 2:
        raise ValueError("Gulf-East Asia laden voyage must use two turns")
    if int(state["discharge_turns"]) != 1:
        raise ValueError("East Asia discharge must use one turn")
    if int(state["ballast_turns"]) != 2:
        raise ValueError("East Asia-Gulf ballast return must use two turns")
    if int(state["cycle_turns"]) != 5:
        raise ValueError("route cycle must total five turns")
    if int(state["cargo_arrival_lag_turns"]) != 3:
        raise ValueError("cargo must enter destination inventory after three turns")

    pricing = config["pricing"]
    if float(pricing["baseline_real_tce_2025_usd_per_day"]) <= 0.0:
        raise ValueError("baseline TCE must be positive")
    if float(pricing["minimum_real_tce_2025_usd_per_day"]) < 0.0:
        raise ValueError("minimum TCE cannot be negative in this prototype")
    if (
        float(pricing["maximum_real_tce_2025_usd_per_day"])
        <= float(pricing["minimum_real_tce_2025_usd_per_day"])
    ):
        raise ValueError("maximum TCE must exceed minimum TCE")
    if not 0.0 <= float(pricing["price_persistence"]) < 1.0:
        raise ValueError("price persistence must lie in [0, 1)")


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
        raise KeyError(f"missing route in monthly shipping world: {route_id}")
    cargo_mbd = float(route["cargo_mbd"])
    if cargo_mbd <= 0.0:
        raise ValueError(f"route cargo must remain positive: {route_id}")
    return cargo_mbd


def monthly_gulf_east_asia_pricing_inputs(
    shipping_world: OilShippingWorld,
    *,
    route_id: str = "gulf_east_asia",
) -> tuple[dict[str, Any], ...]:
    """Read only the demand fields needed by the pricing adapter."""

    return tuple(
        {
            "year": int(month["year"]),
            "month": int(month["month"]),
            "days": int(month["days"]),
            "cargo_mbd": _route_cargo_mbd(month, route_id),
        }
        for month in shipping_world.turns
    )


def price_single_route_turn(
    *,
    structural_cargo_mbd: float,
    turn_days: int,
    prompt_supply_vlcc: float,
    origin_inventory_deviation_mmbbl: float = 0.0,
    destination_inventory_deviation_mmbbl: float = 0.0,
    previous_real_tce_2025_usd_per_day: float | None = None,
    cpi_price_level_index_2025_100: float = 100.0,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Price one Gulf-East Asia shipping turn from route supply and demand.

    Positive origin inventory deviation means crude is accumulating in the
    Gulf relative to the normal pipeline.  Negative destination deviation
    means East Asia is short.  The signed route gap uses one-half of their
    difference so that the same missing cargo is not counted twice.
    """

    market = load_single_route_pricing_config() if config is None else dict(config)
    _validate_config(market)

    cargo_mbd = float(structural_cargo_mbd)
    days = int(turn_days)
    prompt_supply = float(prompt_supply_vlcc)
    origin = float(origin_inventory_deviation_mmbbl)
    destination = float(destination_inventory_deviation_mmbbl)
    cpi = float(cpi_price_level_index_2025_100)

    if cargo_mbd <= 0.0:
        raise ValueError("structural cargo rate must be positive")
    if days <= 0:
        raise ValueError("turn days must be positive")
    if prompt_supply < 0.0:
        raise ValueError("prompt supply cannot be negative")
    if cpi <= 0.0:
        raise ValueError("CPI price level must be positive")
    for value in (origin, destination):
        if not math.isfinite(value):
            raise ValueError("inventory deviations must be finite")

    cargo_capacity = float(market["vlcc_cargo_mmbbl"])
    structural_cargo = cargo_mbd * days

    inventory_cfg = market["inventory"]
    inventory_gap = 0.5 * (origin - destination)
    inventory_gap_days = inventory_gap / cargo_mbd
    recovery = _clamp(
        float(inventory_cfg["recovery_fraction_per_turn"]) * inventory_gap,
        -float(inventory_cfg["maximum_recovery_fraction_of_structural_flow"])
        * structural_cargo,
        float(inventory_cfg["maximum_recovery_fraction_of_structural_flow"])
        * structural_cargo,
    )
    pricing_cargo_demand = max(0.0, structural_cargo + recovery)
    demand_vlcc_equivalent = pricing_cargo_demand / cargo_capacity

    pricing = market["pricing"]
    smoothing = float(pricing["liquidity_smoothing_vlcc"])
    reference_structural = (
        float(market["reference_route_cargo_mbd"])
        * float(market["reference_turn_days"])
        / cargo_capacity
    )
    reference_prompt = float(market["reference_prompt_supply_vlcc"])
    reference_tightness = (
        reference_structural + smoothing
    ) / (reference_prompt + smoothing)
    observed_tightness = (
        demand_vlcc_equivalent + smoothing
    ) / (prompt_supply + smoothing)
    relative_tightness = observed_tightness / reference_tightness

    max_gap_days = float(inventory_cfg["maximum_abs_gap_days_for_pricing"])
    priced_gap_days = _clamp(
        inventory_gap_days,
        -max_gap_days,
        max_gap_days,
    )
    raw_log_signal = (
        float(pricing["supply_demand_log_sensitivity"])
        * math.log(max(relative_tightness, 1e-12))
        + float(pricing["inventory_urgency_log_sensitivity_per_day"])
        * priced_gap_days
    )

    base_tce = float(pricing["baseline_real_tce_2025_usd_per_day"])
    previous_tce = (
        base_tce
        if previous_real_tce_2025_usd_per_day is None
        else float(previous_real_tce_2025_usd_per_day)
    )
    if previous_tce <= 0.0:
        raise ValueError("previous TCE must be positive")
    persistence = float(pricing["price_persistence"])
    previous_log_signal = math.log(previous_tce / base_tce)
    settled_log_signal = (
        persistence * previous_log_signal
        + (1.0 - persistence) * raw_log_signal
    )
    raw_real_tce = base_tce * math.exp(settled_log_signal)
    minimum_tce = float(pricing["minimum_real_tce_2025_usd_per_day"])
    maximum_tce = float(pricing["maximum_real_tce_2025_usd_per_day"])
    real_tce = _clamp(raw_real_tce, minimum_tce, maximum_tce)
    nominal_tce = real_tce * cpi / 100.0

    return {
        "model_version": MODEL_VERSION,
        "route_id": str(market["route_id"]),
        "turn_days": days,
        "structural_cargo_mbd": round(cargo_mbd, 8),
        "structural_cargo_mmbbl": round(structural_cargo, 8),
        "origin_inventory_deviation_mmbbl": round(origin, 8),
        "destination_inventory_deviation_mmbbl": round(destination, 8),
        "inventory_gap_mmbbl": round(inventory_gap, 8),
        "inventory_gap_days": round(inventory_gap_days, 8),
        "inventory_recovery_cargo_mmbbl": round(recovery, 8),
        "pricing_cargo_demand_mmbbl": round(pricing_cargo_demand, 8),
        "pricing_demand_vlcc_equivalent": round(demand_vlcc_equivalent, 8),
        "prompt_supply_vlcc": round(prompt_supply, 8),
        "reference_tightness_ratio": round(reference_tightness, 8),
        "observed_tightness_ratio": round(observed_tightness, 8),
        "relative_tightness_ratio": round(relative_tightness, 8),
        "raw_log_price_signal": round(raw_log_signal, 10),
        "settled_log_price_signal": round(settled_log_signal, 10),
        "raw_real_tce_2025_usd_per_day": round(raw_real_tce, 2),
        "real_tce_2025_usd_per_day": round(real_tce, 2),
        "cpi_price_level_index_2025_100": round(cpi, 8),
        "nominal_tce_usd_per_day": round(nominal_tce, 2),
        "minimum_price_guard_hit": bool(raw_real_tce < minimum_tce),
        "maximum_price_guard_hit": bool(raw_real_tce > maximum_tce),
    }


def build_lagged_prompt_supply_path(
    months: Sequence[Mapping[str, Any]],
    *,
    supply_multiplier: float = 1.0,
    lag_turns: int = 2,
    temporary_supply_delta_by_turn: Mapping[int, int] | None = None,
    config: Mapping[str, Any] | None = None,
) -> tuple[int, ...]:
    """Create a neutral test supply path outside the pricing engine.

    This is only an adapter for seeded validation.  It is not part of price
    formation.  A future fleet state machine can replace it by publishing the
    same ``prompt_supply_vlcc`` input.
    """

    market = load_single_route_pricing_config() if config is None else dict(config)
    _validate_config(market)
    if supply_multiplier <= 0.0:
        raise ValueError("supply multiplier must be positive")
    if lag_turns < 0:
        raise ValueError("supply lag cannot be negative")

    cargo_capacity = float(market["vlcc_cargo_mmbbl"])
    reference_buffer = (
        float(market["reference_prompt_supply_vlcc"])
        - float(market["reference_route_cargo_mbd"])
        * float(market["reference_turn_days"])
        / cargo_capacity
    )
    demand_equivalents: list[float] = []
    for month in months:
        for days in shipping_turn_days(int(month["days"])):
            demand_equivalents.append(float(month["cargo_mbd"]) * days / cargo_capacity)

    deltas = {} if temporary_supply_delta_by_turn is None else {
        int(key): int(value)
        for key, value in temporary_supply_delta_by_turn.items()
    }
    reference_equivalent = (
        float(market["reference_route_cargo_mbd"])
        * float(market["reference_turn_days"])
        / cargo_capacity
    )
    supply: list[int] = []
    for index in range(len(demand_equivalents)):
        source_equivalent = (
            reference_equivalent
            if index < lag_turns
            else demand_equivalents[index - lag_turns]
        )
        offered = _nearest_int(
            supply_multiplier * source_equivalent + reference_buffer
        )
        offered += deltas.get(index, 0)
        supply.append(max(0, offered))
    return tuple(supply)


def simulate_gulf_east_asia_price_path(
    months: Sequence[Mapping[str, Any]],
    *,
    seed: int,
    cpi_by_year: Mapping[int, float],
    prompt_supply_by_turn: Sequence[int | float],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the pure pricing engine over a seeded route demand path.

    The adapter clears as many whole VLCC cargoes as prompt supply permits and
    updates relative inventories.  It does not model costs, fleet ownership or
    strategic dispatch.
    """

    market = load_single_route_pricing_config() if config is None else dict(config)
    _validate_config(market)
    cargo_capacity = float(market["vlcc_cargo_mmbbl"])
    arrival_lag = int(market["state_contract"]["cargo_arrival_lag_turns"])
    expected_turns = sum(3 for _ in months)
    if len(prompt_supply_by_turn) != expected_turns:
        raise ValueError(
            f"prompt supply path has {len(prompt_supply_by_turn)} turns; "
            f"expected {expected_turns}"
        )

    origin_deviation = 0.0
    transit_deviation = 0.0
    destination_deviation = 0.0
    arrival_queue: defaultdict[int, float] = defaultdict(float)
    fixture_carry = 0.0
    previous_tce = float(
        market["pricing"]["baseline_real_tce_2025_usd_per_day"]
    )
    records: list[dict[str, Any]] = []
    turn_index = 0

    for month in months:
        year = int(month["year"])
        month_number = int(month["month"])
        if year not in cpi_by_year:
            raise KeyError(f"missing CPI price level for {year}")
        cpi = float(cpi_by_year[year])

        for turn_in_month, turn_days in enumerate(
            shipping_turn_days(int(month["days"])),
            start=1,
        ):
            arriving_deviation = float(arrival_queue.pop(turn_index, 0.0))
            transit_deviation -= arriving_deviation
            destination_deviation += arriving_deviation

            prompt_supply = float(prompt_supply_by_turn[turn_index])
            quote = price_single_route_turn(
                structural_cargo_mbd=float(month["cargo_mbd"]),
                turn_days=turn_days,
                prompt_supply_vlcc=prompt_supply,
                origin_inventory_deviation_mmbbl=origin_deviation,
                destination_inventory_deviation_mmbbl=destination_deviation,
                previous_real_tce_2025_usd_per_day=previous_tce,
                cpi_price_level_index_2025_100=cpi,
                config=market,
            )
            previous_tce = float(quote["real_tce_2025_usd_per_day"])

            exact_required = (
                float(quote["pricing_demand_vlcc_equivalent"]) + fixture_carry
            )
            required_fixtures = max(0, int(math.floor(exact_required + 1e-12)))
            fixture_carry = exact_required - required_fixtures
            available_fixtures = max(0, int(math.floor(prompt_supply + 1e-12)))
            loaded_fixtures = min(required_fixtures, available_fixtures)
            unfilled_fixtures = max(0, required_fixtures - loaded_fixtures)
            unused_prompt = max(0, available_fixtures - loaded_fixtures)
            loaded_cargo = loaded_fixtures * cargo_capacity
            structural_cargo = float(quote["structural_cargo_mmbbl"])
            load_deviation = loaded_cargo - structural_cargo

            origin_deviation -= load_deviation
            transit_deviation += load_deviation
            arrival_queue[turn_index + arrival_lag] += load_deviation

            inventory_residual = (
                origin_deviation
                + transit_deviation
                + destination_deviation
            )
            records.append(
                {
                    "seed": int(seed),
                    "shipping_turn_index": turn_index,
                    "year": year,
                    "month": month_number,
                    "shipping_turn_in_month": turn_in_month,
                    "label": f"{year}-{month_number:02d}.{turn_in_month}",
                    **quote,
                    "required_fixture_vlcc": required_fixtures,
                    "available_fixture_vlcc": available_fixtures,
                    "loaded_fixture_vlcc": loaded_fixtures,
                    "unfilled_fixture_vlcc": unfilled_fixtures,
                    "unused_prompt_vlcc": unused_prompt,
                    "loaded_cargo_mmbbl": round(loaded_cargo, 8),
                    "load_deviation_mmbbl": round(load_deviation, 8),
                    "cargo_arrival_deviation_mmbbl": round(
                        arriving_deviation,
                        8,
                    ),
                    "closing_origin_inventory_deviation_mmbbl": round(
                        origin_deviation,
                        8,
                    ),
                    "closing_in_transit_deviation_mmbbl": round(
                        transit_deviation,
                        8,
                    ),
                    "closing_destination_inventory_deviation_mmbbl": round(
                        destination_deviation,
                        8,
                    ),
                    "inventory_conservation_residual_mmbbl": round(
                        inventory_residual,
                        10,
                    ),
                }
            )
            turn_index += 1

    real_tce = [
        float(record["real_tce_2025_usd_per_day"])
        for record in records
    ]
    nominal_tce = [
        float(record["nominal_tce_usd_per_day"])
        for record in records
    ]
    demand = [
        float(record["structural_cargo_mbd"])
        for record in records
    ]
    supply = [
        float(record["prompt_supply_vlcc"])
        for record in records
    ]
    gaps = [
        abs(float(record["inventory_gap_days"]))
        for record in records
    ]
    relative_tightness = [
        float(record["relative_tightness_ratio"])
        for record in records
    ]
    summary = {
        "turn_count": len(records),
        "structural_route_cargo_mbd_mean": round(statistics.fmean(demand), 6),
        "structural_route_cargo_mbd_min": round(min(demand), 6),
        "structural_route_cargo_mbd_max": round(max(demand), 6),
        "prompt_supply_vlcc_mean": round(statistics.fmean(supply), 6),
        "prompt_supply_vlcc_min": round(min(supply), 6),
        "prompt_supply_vlcc_max": round(max(supply), 6),
        "relative_tightness_ratio_mean": round(
            statistics.fmean(relative_tightness),
            6,
        ),
        "relative_tightness_ratio_max": round(max(relative_tightness), 6),
        "real_tce_2025_usd_per_day_p05": round(_percentile(real_tce, 0.05), 2),
        "real_tce_2025_usd_per_day_median": round(
            _percentile(real_tce, 0.50),
            2,
        ),
        "real_tce_2025_usd_per_day_p95": round(_percentile(real_tce, 0.95), 2),
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
        "total_unfilled_fixture_vlcc": sum(
            int(record["unfilled_fixture_vlcc"])
            for record in records
        ),
        "total_unused_prompt_vlcc": sum(
            int(record["unused_prompt_vlcc"])
            for record in records
        ),
        "p95_abs_inventory_gap_days": round(_percentile(gaps, 0.95), 6),
        "maximum_abs_inventory_gap_days": round(max(gaps), 6),
        "minimum_price_guard_hit_turns": sum(
            bool(record["minimum_price_guard_hit"])
            for record in records
        ),
        "maximum_price_guard_hit_turns": sum(
            bool(record["maximum_price_guard_hit"])
            for record in records
        ),
        "maximum_abs_inventory_conservation_residual_mmbbl": round(
            max(
                abs(float(record["inventory_conservation_residual_mmbbl"]))
                for record in records
            ),
            10,
        ),
    }
    return {
        "identity": {
            "model_version": MODEL_VERSION,
            "seed": int(seed),
            "route_id": str(market["route_id"]),
            "scope": "single_route_supply_demand_pricing_only",
            "price_output": "spot_tce_real_2025_usd_and_nominal_usd",
            "loading_turns": int(market["state_contract"]["loading_turns"]),
            "laden_turns": int(market["state_contract"]["laden_turns"]),
            "discharge_turns": int(
                market["state_contract"]["discharge_turns"]
            ),
            "ballast_turns": int(market["state_contract"]["ballast_turns"]),
            "cycle_turns": int(market["state_contract"]["cycle_turns"]),
            "excluded_scope": (
                "bunker",
                "ports",
                "commission",
                "opex",
                "cashflow",
                "depreciation",
                "debt",
                "interest",
                "ship_value",
                "newbuildings",
                "scrapping",
                "owner_strategy",
                "bilateral_bargaining",
            ),
        },
        "turns": tuple(records),
        "summary": summary,
    }


def run_seeded_gulf_east_asia_pricing(
    global_run: GlobalMacroRun,
    shipping_world: OilShippingWorld,
    *,
    supply_multiplier: float = 1.0,
    supply_lag_turns: int = 2,
    temporary_supply_delta_by_turn: Mapping[int, int] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the pricing engine on actual seeded Gulf-East Asia route demand."""

    if int(global_run.seed) != int(shipping_world.seed):
        raise ValueError("macro and shipping worlds must use the same seed")
    market = load_single_route_pricing_config() if config is None else dict(config)
    months = monthly_gulf_east_asia_pricing_inputs(
        shipping_world,
        route_id=str(market["route_id"]),
    )
    supply = build_lagged_prompt_supply_path(
        months,
        supply_multiplier=supply_multiplier,
        lag_turns=supply_lag_turns,
        temporary_supply_delta_by_turn=temporary_supply_delta_by_turn,
        config=market,
    )
    cpi_by_year = {
        int(row["year"]): float(row["cpi_price_level_index_2025_100"])
        for row in global_run.rows
    }
    return simulate_gulf_east_asia_price_path(
        months,
        seed=int(global_run.seed),
        cpi_by_year=cpi_by_year,
        prompt_supply_by_turn=supply,
        config=market,
    )


def run_seeded_gulf_east_asia_pricing_from_seed(
    seed: int = 42,
    years: int = 20,
    *,
    supply_multiplier: float = 1.0,
    supply_lag_turns: int = 2,
    temporary_supply_delta_by_turn: Mapping[int, int] | None = None,
) -> dict[str, Any]:
    """Convenience entry point for tests and audits."""

    from .engine import run_global_macro
    from .oil_shipping_world import run_oil_shipping_world

    macro = run_global_macro(seed, years)
    shipping = run_oil_shipping_world(macro)
    return run_seeded_gulf_east_asia_pricing(
        macro,
        shipping,
        supply_multiplier=supply_multiplier,
        supply_lag_turns=supply_lag_turns,
        temporary_supply_delta_by_turn=temporary_supply_delta_by_turn,
    )
