"""Stage5A: fixed ten-day fleet -> bounded indicative price -> execution.

No cost accounts or strategic agents. Quantities use integer barrels. A route
plan is not an absolute regional inventory. Oil can only be delivered by a
specific ship completing discharge. Raw cargo never expires; pricing pressure can decay.
"""
from __future__ import annotations

import calendar
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .registry import sha256_json
from .single_route_fleet import FleetState, advance_fleet, count_integer, dispatch_fleet, initial_fleet
from .bounded_route_pricing import (
    OPERATING_TURN_DAYS, align_pressure_with_gap, bounded_pressure,
    load_bounded_pricing_config, price_bounded_route_turn, validate_bounded_config,
)

MODEL_VERSION = "asset-simulation-stage5a-physical-market-v0.2.0"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "stage5a_single_route_v0.2.json"


def load_stage5a_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config["model_version"] != MODEL_VERSION:
        raise ValueError("Stage5A config version mismatch")
    return config


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} cannot be boolean")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _whole_barrels(volume: float) -> int:
    volume = _finite(volume, "barrels")
    if volume < 0:
        raise ValueError("barrels cannot be negative")
    return int(math.floor(volume + 0.5))


@dataclass(frozen=True)
class RouteMarketState:
    fleet: FleetState
    cargo_bbl: int
    origin_bbl: int
    destination_deviation_bbl: int
    actual_in_transit_bbl: int
    reference_arrivals_bbl: tuple[int, ...]
    initial_in_transit_bbl: int
    cumulative_scheduled_bbl: int = 0
    cumulative_loaded_bbl: int = 0
    cumulative_delivered_bbl: int = 0
    cumulative_reference_delivered_bbl: int = 0
    elapsed_days: int = 0
    previous_real_tce: float = 35000.0
    pricing_pressure_days: float = 0.0

    def validate(self) -> None:
        self.fleet.validate()
        _finite(self.pricing_pressure_days, "pricing pressure")
        if _finite(self.previous_real_tce, "previous TCE") <= 0:
            raise ValueError("previous TCE must be positive")
        if self.fleet.phase != "closed":
            raise ValueError("market state must be at a completed turn boundary")
        for name in ("cargo_bbl", "origin_bbl", "actual_in_transit_bbl", "initial_in_transit_bbl",
                     "cumulative_scheduled_bbl", "cumulative_loaded_bbl", "cumulative_delivered_bbl",
                     "cumulative_reference_delivered_bbl", "elapsed_days"):
            count_integer(getattr(self, name), name)
        if self.cargo_bbl <= 0 or len(self.reference_arrivals_bbl) != 3:
            raise ValueError("invalid cargo unit or arrival queue")
        for amount in self.reference_arrivals_bbl:
            count_integer(amount, "reference arrival")
        if self.actual_in_transit_bbl != self.cargo_bbl * self.fleet.cargo_vessel_count():
            raise ValueError("in-transit barrels do not match loaded ships")
        if self.origin_bbl != self.cumulative_scheduled_bbl - self.cumulative_loaded_bbl:
            raise ValueError("source barrel balance failed")
        if self.actual_in_transit_bbl != self.initial_in_transit_bbl + self.cumulative_loaded_bbl - self.cumulative_delivered_bbl:
            raise ValueError("actual cargo conservation failed")
        if sum(self.reference_arrivals_bbl) != self.initial_in_transit_bbl + self.cumulative_scheduled_bbl - self.cumulative_reference_delivered_bbl:
            raise ValueError("reference pipeline balance failed")
        if self.destination_deviation_bbl != self.cumulative_delivered_bbl - self.cumulative_reference_delivered_bbl:
            raise ValueError("destination delivery deviation failed")
        if self.origin_bbl + self.actual_in_transit_bbl - sum(self.reference_arrivals_bbl) + self.destination_deviation_bbl != 0:
            raise ValueError("plan deviation balance failed")


def initial_market(fleet_size: int = 245, *, pricing_config: Mapping[str, Any] | None = None,
                   initialization: str = "phased", phase_rotation: int = 0) -> RouteMarketState:
    cfg = load_bounded_pricing_config() if pricing_config is None else pricing_config
    validate_bounded_config(cfg)
    cargo = _whole_barrels(float(cfg["vlcc_cargo_mmbbl"]) * 1e6)
    departures = float(cfg["reference_route_cargo_mbd"]) * float(cfg["reference_turn_days"]) * 1e6 / cargo
    fleet = initial_fleet(fleet_size, reference_departures=departures,
                          initialization=initialization, phase_rotation=phase_rotation)
    # Inherited cargo and reference cargo start equal. No phantom startup deficit.
    arrivals = tuple(len(getattr(fleet, name)) * cargo for name in ("ea_discharge", "laden_2", "laden_1"))
    state = RouteMarketState(fleet, cargo, 0, 0, sum(arrivals), arrivals, sum(arrivals),
                             previous_real_tce=float(cfg["pricing"]["baseline_real_tce_2025_usd_per_day"]))
    state.validate()
    return state


def step_route_market(state: RouteMarketState, *, scheduled_cargo_bbl: int,
                      turn_days: int, cpi: float = 100.0,
                      pricing_config: Mapping[str, Any] | None = None,
                      include_events: bool = False) -> tuple[RouteMarketState, dict[str, Any]]:
    """One causal step. Oil and returning ships become available at its start.

    All oil scheduled inside the window is aggregated for instant dispatch.
    This is a turn abstraction, not an intraday port arrival simulation.
    """
    state.validate()
    count_integer(scheduled_cargo_bbl, "scheduled cargo")
    count_integer(turn_days, "turn days")
    if turn_days != OPERATING_TURN_DAYS:
        raise ValueError("each operating turn must be exactly ten days")
    cfg = load_bounded_pricing_config() if pricing_config is None else pricing_config
    validate_bounded_config(cfg)
    if _whole_barrels(float(cfg["vlcc_cargo_mmbbl"]) * 1e6) != state.cargo_bbl:
        raise ValueError("cannot change vessel cargo capacity mid-run")
    fleet, events = advance_fleet(state.fleet)
    arrivals = len(events["delivered_ship_ids"]) * state.cargo_bbl
    reference_arrivals = state.reference_arrivals_bbl[0]
    destination = state.destination_deviation_bbl + arrivals - reference_arrivals
    current_rate = scheduled_cargo_bbl / 1e6 / turn_days
    # Destination slippage is known at the turn opening. Signal updates never
    # change the physical origin/destination records. One decay step per turn.
    reference_daily_bbl = float(cfg["reference_route_cargo_mbd"]) * 1e6
    opening_pressure = bounded_pressure(state.pricing_pressure_days,
        (reference_arrivals - arrivals) / (2.0 * reference_daily_bbl),
        config=cfg, decay=False)
    opening_pressure = align_pressure_with_gap(opening_pressure, state.origin_bbl - destination)
    quote = price_bounded_route_turn(
        structural_cargo_mbd=current_rate, turn_days=turn_days,
        prompt_supply_vlcc=len(fleet.gulf_prompt),
        pricing_pressure_days=opening_pressure,
        origin_inventory_deviation_mmbbl=state.origin_bbl / 1e6,
        destination_inventory_deviation_mmbbl=destination / 1e6,
        previous_real_tce_2025_usd_per_day=state.previous_real_tce,
        cpi_price_level_index_2025_100=cpi, config=cfg,
    )
    ready_bbl = state.origin_bbl + scheduled_cargo_bbl
    ready_fixtures = ready_bbl // state.cargo_bbl
    # No second fractional-demand ledger. Remainder stays in origin_bbl.
    # Round request up, but never load barrels that do not actually exist.
    # Physical catch-up is deliberately separate from quote memory. Reducing a
    # premium must not cancel orders or stop old barrels from being transported.
    physical_gap_bbl = 0.5 * (state.origin_bbl - destination)
    recovery_limit = float(cfg["inventory"]["maximum_recovery_fraction_of_structural_flow"]) * scheduled_cargo_bbl
    recovery_bbl = max(-recovery_limit, min(recovery_limit,
        float(cfg["inventory"]["recovery_fraction_per_turn"]) * physical_gap_bbl))
    execution_request = max(0.0, scheduled_cargo_bbl + recovery_bbl)
    desired = max(0, int(math.ceil(execution_request / state.cargo_bbl - 1e-8)))
    if scheduled_cargo_bbl == 0:
        desired = ready_fixtures  # clear old cargo; no new price observation
    eligible = min(desired, ready_fixtures)
    departures = min(eligible, len(fleet.gulf_prompt))
    next_fleet, departed_ids = dispatch_fleet(fleet, departures)
    loaded = departures * state.cargo_bbl
    closing_pressure = bounded_pressure(opening_pressure,
        (scheduled_cargo_bbl - loaded) / (2.0 * reference_daily_bbl),
        config=cfg, decay=True)
    closing_pressure = align_pressure_with_gap(closing_pressure, ready_bbl - loaded - destination)
    next_state = RouteMarketState(
        fleet=next_fleet, cargo_bbl=state.cargo_bbl,
        origin_bbl=ready_bbl - loaded, destination_deviation_bbl=destination,
        actual_in_transit_bbl=state.actual_in_transit_bbl - arrivals + loaded,
        reference_arrivals_bbl=state.reference_arrivals_bbl[1:] + (scheduled_cargo_bbl,),
        initial_in_transit_bbl=state.initial_in_transit_bbl,
        cumulative_scheduled_bbl=state.cumulative_scheduled_bbl + scheduled_cargo_bbl,
        cumulative_loaded_bbl=state.cumulative_loaded_bbl + loaded,
        cumulative_delivered_bbl=state.cumulative_delivered_bbl + arrivals,
        cumulative_reference_delivered_bbl=state.cumulative_reference_delivered_bbl + reference_arrivals,
        elapsed_days=state.elapsed_days + turn_days,
        previous_real_tce=float(quote["real_tce_2025_usd_per_day"]),
        pricing_pressure_days=closing_pressure,
    )
    next_state.validate()
    closing_gap = (next_state.origin_bbl - destination) / 2e6
    denominator = current_rate if current_rate > 0 else float(cfg["reference_route_cargo_mbd"])
    record = {
        **quote,
        "shipping_turn_index": next_fleet.turn_index,
        "day_start_offset": state.elapsed_days,
        "clock_scope": "fixed_10_day_operating_clock",
        "closing_pricing_pressure_days": closing_pressure,
        "physical_catchup_request_bbl": recovery_bbl,
        "execution_request_bbl": execution_request,
        "day_end_offset": next_state.elapsed_days,
        "fleet_size": fleet.size,
        "prompt_before_dispatch": len(fleet.gulf_prompt),
        "closing_state_counts": next_fleet.counts(),
        "idle_prompt_after_dispatch": len(next_fleet.gulf_prompt),
        "dispatched_vlcc": departures,
        "discharge_completed_vlcc": len(events["delivered_ship_ids"]),
        "returned_to_gulf_vlcc": len(events["returned_ship_ids"]),
        "requested_fixture_vlcc": desired,
        "cargo_ready_fixture_vlcc": ready_fixtures,
        "eligible_fixture_vlcc": eligible,
        "unfilled_fixture_observations": eligible - departures,
        "execution_status": "matched" if departures else "no_match",
        "executed_benchmark_real_tce": next_state.previous_real_tce if departures else None,
        "execution_price_scope": "assumed_benchmark_not_negotiated_cash_revenue",
        "scheduled_cargo_bbl": scheduled_cargo_bbl,
        "loaded_cargo_bbl": loaded,
        "delivered_cargo_bbl": arrivals,
        "reference_delivered_cargo_bbl": reference_arrivals,
        "origin_unshipped_bbl": next_state.origin_bbl,
        "destination_deviation_bbl": destination,
        "actual_in_transit_bbl": next_state.actual_in_transit_bbl,
        "reference_in_transit_bbl": sum(next_state.reference_arrivals_bbl),
        "in_transit_deviation_bbl": next_state.actual_in_transit_bbl - sum(next_state.reference_arrivals_bbl),
        "closing_inventory_gap_days": closing_gap / denominator,
        "fleet_conservation_residual": sum(next_fleet.counts().values()) - fleet.size,
        "barrel_conservation_residual": next_state.origin_bbl + next_state.actual_in_transit_bbl + next_state.cumulative_delivered_bbl - next_state.initial_in_transit_bbl - next_state.cumulative_scheduled_bbl,
        "plan_conservation_residual": next_state.origin_bbl + next_state.actual_in_transit_bbl - sum(next_state.reference_arrivals_bbl) + destination,
    }
    if include_events:
        record.update(events)
        record["departed_ship_ids"] = departed_ids
    return next_state, record


def monthly_turn_inputs(months: Sequence[Mapping[str, Any]], *,
                        cpi_by_information_year: Mapping[int, float],
                        initial_year: int) -> tuple[dict[str, Any], ...]:
    """Current-month plan is exogenous. No future month or year-close CPI read.

    Preserve the seed's daily RATE on three ten-day operating turns. This is a
    360-day game projection, not execution of every real-calendar monthly barrel.
    Record the source-calendar volume and the explicit mapping difference.
    """
    records = []
    previous_ordinal = None
    for month in months:
        year, number = month["year"], month["month"]
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (year, number)):
            raise ValueError("calendar year/month must be integers")
        days = month["days"]
        if days != calendar.monthrange(year, number)[1]:
            raise ValueError("month length does not match real calendar")
        ordinal = year * 12 + number
        if previous_ordinal is not None and ordinal != previous_ordinal + 1:
            raise ValueError("months must be contiguous and ordered")
        previous_ordinal = ordinal
        rate = _finite(month["cargo_mbd"], "cargo rate")
        if rate < 0:
            raise ValueError("cargo rate cannot be negative")
        lengths = (OPERATING_TURN_DAYS,) * 3
        source_calendar_volume = _whole_barrels(rate * days * 1e6)
        total = _whole_barrels(rate * 3 * OPERATING_TURN_DAYS * 1e6)
        amounts = [_whole_barrels(rate * d * 1e6) for d in lengths[:2]]
        amounts.append(total - sum(amounts))
        information_year = max(initial_year, year - 1)
        if information_year not in cpi_by_information_year:
            raise ValueError(f"missing completed-year CPI for {information_year}")
        cpi = _finite(cpi_by_information_year[information_year], "CPI")
        if cpi <= 0:
            raise ValueError("CPI must be positive")
        for i, (days_in_turn, amount) in enumerate(zip(lengths, amounts), 1):
            records.append({"year": year, "month": number, "turn_in_month": i,
                            "label": f"{year}-{number:02d}.{i}", "turn_days": days_in_turn,
                            "scheduled_cargo_bbl": amount, "source_cargo_mbd": rate,
                            "cpi": cpi, "cpi_information_year": information_year,
                            "source_calendar_month_days": days,
                            "source_calendar_month_cargo_bbl": source_calendar_volume,
                            "operating_month_cargo_bbl": total,
                            "clock_projection_difference_bbl": total - source_calendar_volume})
    return tuple(records)


def _quantile(values: Sequence[float], p: float) -> float:
    values = sorted(values)
    point = (len(values) - 1) * p
    lower = int(point)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (point - lower) * (values[upper] - values[lower])


def summarize_market(records: Sequence[Mapping[str, Any]], *, cargo_bbl: int,
                     warmup_turns: int = 36) -> dict[str, Any]:
    """Warmup only excludes statistics. Never erases cargo or resets prices."""
    count_integer(warmup_turns, "warmup turns")
    if not records or warmup_turns >= len(records):
        raise ValueError("summary requires post-warmup records")
    rows = records[warmup_turns:]
    prices = [float(r["real_tce_2025_usd_per_day"]) for r in rows]
    nominal = [float(r["nominal_tce_usd_per_day"]) for r in rows]
    days = sum(r["turn_days"] for r in rows)
    cargo = sum(r["scheduled_cargo_bbl"] for r in rows)
    loaded = sum(r["loaded_cargo_bbl"] for r in rows)
    mean_demand = cargo / days / 1e6
    ships = rows[0]["fleet_size"]
    output = {
        "fleet_size": ships, "warmup_turns": warmup_turns,
        "observed_turns": len(rows), "observed_operating_days": days,
        "clock_scope": "360_operating_days_per_label_year",
        "mean_demand_mbd": mean_demand,
        "mean_required_cycling_vlcc": cargo / cargo_bbl / len(rows) * 5,
        "load_to_new_plan_ratio": loaded / cargo if cargo else None,
        "mean_prompt_vlcc": sum(r["prompt_before_dispatch"] for r in rows) / len(rows),
        "min_prompt_vlcc": min(r["prompt_before_dispatch"] for r in rows),
        "mean_idle_vlcc": sum(r["idle_prompt_after_dispatch"] for r in rows) / len(rows),
        "mean_active_utilization": sum((ships - r["idle_prompt_after_dispatch"]) * r["turn_days"] for r in rows) / (days * ships) if ships else 0.0,
        "mean_real_tce": sum(r["real_tce_2025_usd_per_day"] * r["turn_days"] for r in rows) / days,
        "loaded_cargo_weighted_real_tce": sum(r["real_tce_2025_usd_per_day"] * r["loaded_cargo_bbl"] for r in rows) / loaded if loaded else None,
        "max_abs_plan_gap_days": max(abs(r["closing_inventory_gap_days"]) for r in rows),
        "end_origin_unshipped_mmbbl": rows[-1]["origin_unshipped_bbl"] / 1e6,
        "end_origin_backlog_days_at_mean_flow": rows[-1]["origin_unshipped_bbl"] / 1e6 / mean_demand if mean_demand else None,
        "max_origin_backlog_days_at_mean_flow": max(r["origin_unshipped_bbl"] for r in rows) / 1e6 / mean_demand if mean_demand else None,
        "end_actual_in_transit_mmbbl": rows[-1]["actual_in_transit_bbl"] / 1e6,
        "cumulative_unfilled_fixture_observations": sum(r["unfilled_fixture_observations"] for r in rows),
        "max_abs_pricing_pressure_days": max(abs(r["closing_pricing_pressure_days"]) for r in rows),
        "end_pricing_pressure_days": rows[-1]["closing_pricing_pressure_days"],
        "near_upper_bound_turns": sum(r["near_upper_price_bound"] for r in rows),
        "near_lower_bound_turns": sum(r["near_lower_price_bound"] for r in rows),
        "no_match_turns": sum(r["dispatched_vlcc"] == 0 for r in rows),
        "upper_guard_turns": sum(r["maximum_price_guard_hit"] for r in rows),
        "lower_guard_turns": sum(r["minimum_price_guard_hit"] for r in rows),
        "max_fleet_residual": max(abs(r["fleet_conservation_residual"]) for r in records),
        "max_barrel_residual": max(abs(r["barrel_conservation_residual"]) for r in records),
        "max_plan_residual": max(abs(r["plan_conservation_residual"]) for r in records),
    }
    mean_price = sum(prices) / len(prices)
    output["real_tce_cv"] = (sum((v - mean_price) ** 2 for v in prices) / len(prices)) ** 0.5 / mean_price
    for prefix, values in (("real_tce", prices), ("nominal_tce", nominal)):
        output.update({f"{prefix}_p05": _quantile(values, 0.05), f"{prefix}_median": _quantile(values, 0.5),
                       f"{prefix}_p95": _quantile(values, 0.95), f"{prefix}_min": min(values), f"{prefix}_max": max(values)})
    output["interpretation"] = "fixed_fleet_counterfactual_not_a_market_equilibrium_or_supercycle"
    return output


def simulate_fixed_route(inputs: Sequence[Mapping[str, Any]], *, fleet_size: int = 245,
                         pricing_config: Mapping[str, Any] | None = None,
                         initialization: str = "phased", phase_rotation: int = 0,
                         include_events: bool = False, warmup_turns: int = 36) -> dict[str, Any]:
    if not inputs:
        raise ValueError("at least one turn required")
    cfg = deepcopy(load_bounded_pricing_config() if pricing_config is None else pricing_config)
    state = initial_market(fleet_size, pricing_config=cfg, initialization=initialization, phase_rotation=phase_rotation)
    initial_hash = sha256_json(asdict(state))
    records = []
    for item in inputs:
        state, row = step_route_market(state, scheduled_cargo_bbl=item["scheduled_cargo_bbl"],
            turn_days=item["turn_days"], cpi=item.get("cpi", 100.0), pricing_config=cfg, include_events=include_events)
        for key in ("year", "month", "turn_in_month", "label", "source_cargo_mbd", "cpi_information_year",
                    "source_calendar_month_days", "source_calendar_month_cargo_bbl",
                    "operating_month_cargo_bbl", "clock_projection_difference_bbl"):
            if key in item:
                row[key] = item[key]
        records.append(row)
    return {
        "identity": {"model_version": MODEL_VERSION, "route_id": cfg["route_id"],
                     "fleet_size": fleet_size, "initialization": initialization,
                     "phase_rotation": phase_rotation, "pricing_config_hash": sha256_json(cfg),
                     "input_hash": sha256_json(inputs), "initial_state_hash": initial_hash,
                     "final_state_hash": sha256_json(asdict(state)), "result_hash": sha256_json(records),
                     "price_feedback_into_execution": False,
                     "pricing_memory_feedback_into_execution": False,
                     "demand_destruction": False,
                     "operating_turn_days": OPERATING_TURN_DAYS,
                     "operating_days_per_label_year": 360,
                     "clock_projection": "preserve_daily_rate_not_calendar_month_total",
                     "pricing_pressure_scope": "bounded_recent_plan_slippage_not_physical_inventory", "cost_accounting_present": False,
                     "supply_owner": "single_route_fleet", "inventory_scope": "integer_barrel_transport_plan_deviation"},
        "turns": tuple(records), "final_state": asdict(state),
        "summary": summarize_market(records, cargo_bbl=state.cargo_bbl, warmup_turns=warmup_turns),
    }


def seeded_route_inputs(global_run: Any, shipping_world: Any) -> tuple[dict[str, Any], ...]:
    from .single_route_pricing import monthly_gulf_east_asia_pricing_inputs
    if global_run.seed != shipping_world.seed or shipping_world.identity["upstream_global_identity_hash"] != global_run.identity["identity_hash"]:
        raise ValueError("shipping projection must belong to this macro run")
    return monthly_turn_inputs(monthly_gulf_east_asia_pricing_inputs(shipping_world),
        cpi_by_information_year={int(r["year"]): float(r["cpi_price_level_index_2025_100"]) for r in global_run.rows},
        initial_year=global_run.start_year)


def run_seeded_fixed_route(seed: int = 42, years: int = 20, *, fleet_size: int = 245,
                           include_events: bool = False) -> dict[str, Any]:
    from .engine import run_global_macro
    from .oil_shipping_world import run_oil_shipping_world
    macro = run_global_macro(seed, years)
    shipping = run_oil_shipping_world(macro)
    result = simulate_fixed_route(seeded_route_inputs(macro, shipping), fleet_size=fleet_size, include_events=include_events)
    result["identity"].update({"seed": seed, "annual_transitions": years,
        "covered_calendar_years": years + 1, "upstream_macro_hash": macro.identity["identity_hash"],
        "upstream_shipping_hash": shipping.identity["identity_hash"]})
    return result
