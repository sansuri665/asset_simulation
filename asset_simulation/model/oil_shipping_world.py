"""Deterministic monthly physical oil and crude-shipping demand world."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from .engine import GlobalMacroRun
from .math_utils import round_record
from .crude_physical_world import (
    advance_crude_turn,
    annual_crude_growth_targets,
    initial_crude_state,
)
from .oil_physical_world import (
    advance_physical_turn,
    annual_growth_targets,
    initial_physical_state,
)
from .oil_shipping_demand import advance_shipping_demand, initial_shipping_state
from .oil_shipping_regions import advance_regional_balance, initial_regional_state
from .oil_shipping_routes import advance_route_network, initial_route_state
from .performance_cache import deterministic_projection_cache
from .registry import load_registered_assets, sha256_json


OIL_SHIPPING_DEMAND_MODEL_VERSION = "asset-simulation-oil-shipping-demand-v0.6.0"
OIL_SHIPPING_DEMAND_SCHEMA_VERSION = "asset-simulation-oil-shipping-demand-response-v6"
SCALAR_SCENARIO_FIELDS = frozenset(
    {
        "demand_rate_impulse_pct",
        "production_outage_mbd",
        "crude_production_outage_mbd",
        "average_haul_impulse_pct",
    }
)
MAPPING_SCENARIO_FIELDS = frozenset(
    {
        "regional_crude_production_impulse_mbd",
        "regional_crude_runs_impulse_mbd",
        "regional_crude_inventory_impulse_mmbbl",
        "route_haul_impulse_pct",
    }
)
ALLOWED_SCENARIO_FIELDS = SCALAR_SCENARIO_FIELDS | MAPPING_SCENARIO_FIELDS


@dataclass(frozen=True)
class OilShippingWorld:
    seed: int
    start_year: int
    end_year: int
    turns: tuple[dict[str, Any], ...]
    annual: tuple[dict[str, Any], ...]
    identity: dict[str, Any]


def _validate_scenarios(
    scenario_by_turn: Mapping[int, Mapping[str, Any]] | None,
    *,
    turn_count: int,
) -> dict[int, dict[str, Any]]:
    normalized: dict[int, dict[str, Any]] = {}
    for key, raw in ({} if scenario_by_turn is None else scenario_by_turn).items():
        if isinstance(key, bool) or not isinstance(key, int):
            raise TypeError("scenario turn indices must be integers")
        if not 0 <= key < turn_count:
            raise ValueError(f"scenario turn index is outside the world: {key}")
        unknown = set(raw) - ALLOWED_SCENARIO_FIELDS
        if unknown:
            raise KeyError(f"unknown oil-shipping scenario fields: {sorted(unknown)}")
        values: dict[str, Any] = {}
        for field, value in raw.items():
            if field in MAPPING_SCENARIO_FIELDS:
                if not isinstance(value, Mapping):
                    raise TypeError(f"{field} must be a region/route mapping")
                nested: dict[str, float] = {}
                for nested_key, nested_value in value.items():
                    if not isinstance(nested_key, str) or not nested_key:
                        raise TypeError(f"{field} keys must be non-empty strings")
                    if isinstance(nested_value, bool) or not isinstance(
                        nested_value,
                        (int, float),
                    ):
                        raise TypeError(f"{field}.{nested_key} must be numeric")
                    numeric = float(nested_value)
                    if not math.isfinite(numeric):
                        raise ValueError(f"{field}.{nested_key} must be finite")
                    nested[nested_key] = numeric
                values[field] = nested
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field} must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError(f"{field} must be finite")
            values[field] = numeric
        normalized[key] = values
    return normalized


def _aggregate_year(year: int, turns: list[dict[str, Any]]) -> dict[str, Any]:
    total_days = sum(int(turn["days"]) for turn in turns)
    consumption = sum(float(turn["realized_demand_mbd"]) * int(turn["days"]) for turn in turns)
    production = sum(float(turn["production_mbd"]) * int(turn["days"]) for turn in turns)
    crude_production = sum(
        float(turn["crude_production_mbd"]) * int(turn["days"])
        for turn in turns
    )
    crude_runs = sum(
        float(turn["crude_refinery_runs_mbd"]) * int(turn["days"])
        for turn in turns
    )
    cargo_tonnes = sum(float(turn["cargo_million_tonnes"]) for turn in turns)
    tonne_miles = sum(float(turn["tonne_nautical_miles_billion"]) for turn in turns)
    weighted_haul = 1000.0 * tonne_miles / cargo_tonnes if cargo_tonnes else 0.0
    return round_record(
        {
            "year": year,
            "turn_count": len(turns),
            "days": total_days,
            "average_demand_mbd": consumption / total_days,
            "average_production_mbd": production / total_days,
            "average_crude_production_mbd": crude_production / total_days,
            "average_crude_refinery_runs_mbd": crude_runs / total_days,
            "crude_production_minus_runs_mmbbl": crude_production - crude_runs,
            "ending_crude_inventory_mmbbl": float(
                turns[-1]["crude_closing_inventory_mmbbl"]
            ),
            "ending_crude_inventory_days": float(
                turns[-1]["crude_inventory_days"]
            ),
            "production_minus_demand_mmbbl": production - consumption,
            "ending_inventory_mmbbl": float(turns[-1]["closing_inventory_mmbbl"]),
            "ending_inventory_days": float(turns[-1]["inventory_days"]),
            "ending_target_inventory_days": float(
                turns[-1]["target_inventory_days"]
            ),
            "long_run_demand_regime": str(turns[-1]["long_run_demand_regime"]),
            "structural_demand_drag_pct": float(
                turns[-1]["structural_demand_drag_pct"]
            ),
            "average_spare_capacity_mbd": sum(
                float(turn["spare_capacity_mbd"]) * int(turn["days"]) for turn in turns
            )
            / total_days,
            "average_seaborne_cargo_mbd": sum(
                float(turn["seaborne_cargo_mbd"]) * int(turn["days"])
                for turn in turns
            )
            / total_days,
            "seaborne_cargo_million_tonnes": cargo_tonnes,
            "average_haul_nm": weighted_haul,
            "tonne_nautical_miles_billion": tonne_miles,
            "unmet_demand_mmbbl": sum(float(turn["unmet_demand_mmbbl"]) for turn in turns),
            "maximum_abs_mass_balance_residual_mmbbl": max(
                abs(float(turn["mass_balance_residual_mmbbl"])) for turn in turns
            ),
            "maximum_abs_crude_mass_balance_residual_mmbbl": max(
                abs(float(turn["crude_mass_balance_residual_mmbbl"]))
                for turn in turns
            ),
        }
    )


@deterministic_projection_cache(max_entries=8)
def run_oil_shipping_world(
    global_run: GlobalMacroRun,
    *,
    scenario_by_turn: Mapping[int, Mapping[str, Any]] | None = None,
) -> OilShippingWorld:
    """Build physical oil and tonne-mile demand for every calendar month."""

    assets = load_registered_assets()
    config = assets["oil_shipping_demand_config"]
    contract = assets["oil_shipping_demand_contract"]
    if config["model_version"] != OIL_SHIPPING_DEMAND_MODEL_VERSION:
        raise ValueError("registered oil-shipping config version mismatch")
    if contract["model_version"] != OIL_SHIPPING_DEMAND_MODEL_VERSION:
        raise ValueError("registered oil-shipping contract version mismatch")
    if int(config["time"]["turns_per_month"]) != 1:
        raise ValueError("oil shipping world requires one turn per month")

    expected_turn_count = len(global_run.rows) * 12
    scenarios = _validate_scenarios(
        scenario_by_turn,
        turn_count=expected_turn_count,
    )
    physical_state = initial_physical_state(config)
    crude_state = initial_crude_state(config)
    shipping_state = initial_shipping_state(config)
    regional_state = initial_regional_state(config)
    route_state = initial_route_state(config)
    turns: list[dict[str, Any]] = []
    annual: list[dict[str, Any]] = []

    for macro_index, current_year_row in enumerate(global_run.rows):
        year = int(current_year_row["year"])
        information_index = max(0, macro_index - 1)
        macro_row = global_run.rows[information_index]
        demand_growth, capacity_growth, demand_regime, structural_drag = annual_growth_targets(
            global_run.rows[: information_index + 1],
            seed=global_run.seed,
            year_index=macro_index,
            simulation_year=year,
            config=config,
            physical_state=physical_state,
        )
        crude_runs_growth, crude_capacity_growth, crude_investment_cycle = (
            annual_crude_growth_targets(
                crude_state,
                seed=global_run.seed,
                year_index=macro_index,
                simulation_year=year,
                liquids_demand_growth_pct=demand_growth,
                lagged_real_oil_price_index=float(
                    macro_row["global_real_oil_price_index"]
                ),
                config=config,
            )
        )
        year_turns: list[dict[str, Any]] = []
        for month in range(1, 13):
            turn_index = len(turns)
            impulse = scenarios.get(turn_index, {})
            physical_state, physical = advance_physical_turn(
                physical_state,
                seed=global_run.seed,
                turn_index=turn_index,
                year=year,
                month=month,
                annual_demand_growth_pct=demand_growth,
                annual_capacity_growth_pct=capacity_growth,
                long_run_demand_regime=demand_regime,
                structural_demand_drag_pct=structural_drag,
                config=config,
                impulse=impulse,
            )
            crude_state, crude = advance_crude_turn(
                crude_state,
                seed=global_run.seed,
                turn_index=turn_index,
                year=year,
                month=month,
                annual_refinery_runs_growth_pct=crude_runs_growth,
                annual_capacity_growth_pct=crude_capacity_growth,
                investment_cycle_pct=crude_investment_cycle,
                config=config,
                impulse=impulse,
            )
            shipping_state, shipping = advance_shipping_demand(
                shipping_state,
                physical,
                seed=global_run.seed,
                turn_index=turn_index,
                config=config,
                impulse=impulse,
            )
            regional_state, regional = advance_regional_balance(
                regional_state,
                crude,
                seed=global_run.seed,
                turn_index=turn_index,
                month=month,
                lagged_real_oil_price_index=float(
                    macro_row["global_real_oil_price_index"]
                ),
                config=config,
                impulse=impulse,
            )
            route_state, route_network = advance_route_network(
                route_state,
                regional,
                shipping,
                seed=global_run.seed,
                turn_index=turn_index,
                days=int(physical["days"]),
                config=config,
                impulse=impulse,
            )
            record = round_record(
                {
                    "seed": global_run.seed,
                    "turn_index": turn_index,
                    "year": year,
                    "month": month,
                    "label": f"{year}-{month:02d}",
                    "macro_information_year": int(macro_row["year"]),
                    "macro_realized_growth_pct": (
                        None
                        if macro_row["realized_growth_pct"] is None
                        else float(macro_row["realized_growth_pct"])
                    ),
                    "macro_real_oil_price_index": float(
                        macro_row["global_real_oil_price_index"]
                    ),
                    "macro_brent_oil_price_usd": float(
                        macro_row["brent_oil_price_usd"]
                    ),
                    **physical,
                    **crude,
                    **shipping,
                    **regional,
                    **route_network,
                }
            )
            turns.append(record)
            year_turns.append(record)
        annual.append(_aggregate_year(year, year_turns))

    result = {"turns": turns, "annual": annual}
    identity = {
        "schema_version": "asset-simulation-oil-shipping-demand-identity-v6",
        "model_version": OIL_SHIPPING_DEMAND_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_shipping_demand_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_shipping_demand_contract_hash"],
        "upstream_global_identity_hash": global_run.identity["identity_hash"],
        "seed": global_run.seed,
        "start_year": int(global_run.rows[0]["year"]),
        "end_year": int(global_run.rows[-1]["year"]),
        "turn_count": len(turns),
        "turns_per_year": 12,
        "long_run_demand_regime": str(turns[0]["long_run_demand_regime"]),
        "game_start_year": int(config["time"]["game_start_year"]),
        "oil_price_owner": "global_macro_oil_commodity",
        "total_liquids_physical_balance_owner": "oil_physical_world",
        "crude_physical_balance_owner": "crude_physical_world",
        "shipping_environment_owner": "oil_shipping_demand",
        "regional_balance_owner": "oil_shipping_regions",
        "route_network_owner": "oil_shipping_routes",
        "shipping_market_scope": str(config["route_network"]["market_scope"]),
        "explicit_route_count": len(config["route_network"]["explicit_routes"]),
        "route_ids": [
            str(route["route_id"])
            for route in config["route_network"]["explicit_routes"]
        ]
        + [str(config["route_network"]["other_pool"]["route_id"])],
        "cargo_generation": "regional_crude_surplus_and_deficit",
        "scenario_scope": "test_only_not_exposed_by_service_or_viewer",
        "freight_rate_present": False,
        "player_price_feedback": False,
        "scenario_hash": sha256_json(scenarios),
        "result_hash": sha256_json(result),
    }
    identity["identity_hash"] = sha256_json(identity)
    return OilShippingWorld(
        seed=global_run.seed,
        start_year=int(global_run.rows[0]["year"]),
        end_year=int(global_run.rows[-1]["year"]),
        turns=tuple(turns),
        annual=tuple(annual),
        identity=identity,
    )


def build_oil_shipping_payload(
    world: OilShippingWorld,
    *,
    as_of_year: int,
    as_of_month: int,
) -> dict[str, Any]:
    """Publish only the selected month and its visible history."""

    if not world.start_year <= int(as_of_year) <= world.end_year:
        raise ValueError("as_of_year is outside the oil-shipping world")
    if not 1 <= int(as_of_month) <= 12:
        raise ValueError("as_of_month must be between 1 and 12")
    target = (int(as_of_year), int(as_of_month))
    visible = tuple(
        turn
        for turn in world.turns
        if (int(turn["year"]), int(turn["month"])) <= target
    )
    if not visible:
        raise ValueError("oil-shipping cutoff has no visible turn")
    completed_years = tuple(
        row
        for row in world.annual
        if int(row["year"]) < target[0]
        or (int(row["year"]) == target[0] and target[1] == 12)
    )
    return {
        "ok": True,
        "schemaVersion": OIL_SHIPPING_DEMAND_SCHEMA_VERSION,
        "identity": world.identity,
        "asOf": {
            "year": target[0],
            "month": target[1],
            "label": f"{target[0]}-{target[1]:02d}",
        },
        "informationCutoff": "selected_month_and_prior_turns_only",
        "current": visible[-1],
        "history": visible,
        "completedAnnual": completed_years,
    }
