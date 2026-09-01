"""Constrained regional crude-route allocation and tonne-mile settlement."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal


def _pair_id(origin_id: str, destination_id: str) -> str:
    return f"{origin_id}::{destination_id}"


def initial_route_state(config: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    network = config["route_network"]
    preferences = {
        _pair_id(str(origin_id), str(destination_id)): float(weight)
        for origin_id, destinations in network["pair_preferences"].items()
        for destination_id, weight in destinations.items()
    }
    return {"pair_preferences": preferences}


def _evolve_preferences(
    previous: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    network: Mapping[str, Any],
) -> dict[str, float]:
    persistence = float(network["preference_persistence"])
    news_scale = float(network["preference_news_scale"])
    evolved: dict[str, float] = {}
    for origin_id, destinations in network["pair_preferences"].items():
        for destination_id, raw_baseline in destinations.items():
            pair_id = _pair_id(str(origin_id), str(destination_id))
            baseline = float(raw_baseline)
            value = (
                baseline
                + persistence * (float(previous[pair_id]) - baseline)
                + news_scale
                * baseline
                * normal(seed, f"oil_route_preference_{pair_id}", turn_index)
            )
            evolved[pair_id] = clamp(value, 0.20 * baseline, 3.0 * baseline)
    return evolved


def _balance_matrix(
    row_targets: Mapping[str, float],
    column_targets: Mapping[str, float],
    preferences: Mapping[str, float],
) -> dict[str, float]:
    """Use iterative proportional fitting to satisfy both regional margins."""

    row_total = sum(row_targets.values())
    column_total = sum(column_targets.values())
    if row_total <= 0.0 or column_total <= 0.0:
        raise ValueError("regional route allocation requires positive trade margins")
    columns = {
        destination_id: value * row_total / column_total
        for destination_id, value in column_targets.items()
    }
    flows = {
        _pair_id(origin_id, destination_id): max(
            1e-12,
            float(preferences[_pair_id(origin_id, destination_id)]),
        )
        for origin_id in row_targets
        for destination_id in columns
    }
    for _ in range(80):
        for origin_id, target in row_targets.items():
            keys = [_pair_id(origin_id, destination_id) for destination_id in columns]
            current = sum(flows[key] for key in keys)
            scale = target / current
            for key in keys:
                flows[key] *= scale
        for destination_id, target in columns.items():
            keys = [_pair_id(origin_id, destination_id) for origin_id in row_targets]
            current = sum(flows[key] for key in keys)
            scale = target / current
            for key in keys:
                flows[key] *= scale
    maximum_residual = max(
        max(
            abs(
                sum(
                    flows[_pair_id(origin_id, destination_id)]
                    for destination_id in columns
                )
                - target
            )
            for origin_id, target in row_targets.items()
        ),
        max(
            abs(
                sum(
                    flows[_pair_id(origin_id, destination_id)]
                    for origin_id in row_targets
                )
                - target
            )
            for destination_id, target in columns.items()
        ),
    )
    if maximum_residual > 1e-8:
        raise ValueError(f"regional route allocation failed to converge: {maximum_residual}")
    return flows


def _route_status(relative_distance: float) -> str:
    if relative_distance >= 1.08:
        return "rerouted"
    if relative_distance <= 0.94:
        return "shortened"
    return "normal"


def advance_route_network(
    state: Mapping[str, Mapping[str, float]],
    regional_turn: Mapping[str, Any],
    shipping_turn: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    days: int,
    config: Mapping[str, Any],
    impulse: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """Allocate regional sea margins; routes now own global cargo volume."""

    impulse = {} if impulse is None else impulse
    network = config["route_network"]
    region_records = {
        str(region["region_id"]): region
        for region in regional_turn["regional_balances"]
    }
    export_region_ids = tuple(map(str, network["export_region_ids"]))
    import_region_ids = tuple(map(str, network["import_region_ids"]))
    row_targets = {
        region_id: max(
            0.0,
            float(region_records[region_id]["net_seaborne_balance_mbd"]),
        )
        for region_id in export_region_ids
    }
    column_targets = {
        region_id: max(
            0.0,
            -float(region_records[region_id]["net_seaborne_balance_mbd"]),
        )
        for region_id in import_region_ids
    }
    if any(value <= 0.0 for value in row_targets.values()):
        invalid = {
            region_id: value
            for region_id, value in row_targets.items()
            if value <= 0.0
        }
        raise ValueError(f"registered export region changed trade role: {invalid}")
    if any(value <= 0.0 for value in column_targets.values()):
        invalid = {
            region_id: value
            for region_id, value in column_targets.items()
            if value <= 0.0
        }
        raise ValueError(f"registered import region changed trade role: {invalid}")

    preferences = _evolve_preferences(
        state["pair_preferences"],
        seed=seed,
        turn_index=turn_index,
        network=network,
    )
    pair_flows = _balance_matrix(row_targets, column_targets, preferences)
    explicit_by_pair = {
        _pair_id(str(route["origin_id"]), str(route["destination_id"])): route
        for route in network["explicit_routes"]
    }
    route_haul_impulses = impulse.get("route_haul_impulse_pct", {})
    known_route_ids = {
        str(route["route_id"]) for route in network["explicit_routes"]
    } | {str(network["other_pool"]["route_id"])}
    unknown_route_impulses = set(route_haul_impulses) - known_route_ids
    if unknown_route_impulses:
        raise KeyError(
            f"unknown routes for route_haul_impulse_pct: {sorted(unknown_route_impulses)}"
        )

    barrels_per_tonne = float(config["units"]["barrels_per_metric_tonne"])
    global_haul_multiplier = 1.0 + float(
        impulse.get("average_haul_impulse_pct", 0.0)
    ) / 100.0
    if global_haul_multiplier <= 0.0:
        raise ValueError("average_haul_impulse_pct must keep route distances positive")
    base_dislocation = float(shipping_turn["base_trade_dislocation_index"])
    other_pool_config = network["other_pool"]
    other_route_id = str(other_pool_config["route_id"])

    route_accumulators: dict[str, dict[str, Any]] = {}
    other_accumulator = {
        "cargo_mbd": 0.0,
        "cargo_tonnes": 0.0,
        "tonne_miles": 0.0,
        "baseline_weighted_nm": 0.0,
    }
    for pair_id, cargo_mbd in pair_flows.items():
        origin_id, destination_id = pair_id.split("::", 1)
        explicit = explicit_by_pair.get(pair_id)
        baseline_haul_nm = float(
            network["pair_distances_nm"][origin_id][destination_id]
        )
        sensitivity = float(
            explicit["dislocation_sensitivity"]
            if explicit is not None
            else other_pool_config["dislocation_sensitivity"]
        )
        route_id = str(explicit["route_id"]) if explicit is not None else other_route_id
        route_multiplier = 1.0 + float(
            route_haul_impulses.get(route_id, 0.0)
        ) / 100.0
        if route_multiplier <= 0.0:
            raise ValueError("route haul impulse must keep route distances positive")
        dislocation_multiplier = max(
            0.70,
            1.0 + sensitivity * (base_dislocation - 1.0),
        )
        effective_haul_nm = (
            baseline_haul_nm
            * dislocation_multiplier
            * global_haul_multiplier
            * route_multiplier
        )
        cargo_tonnes = cargo_mbd * int(days) / barrels_per_tonne
        tonne_miles = cargo_tonnes * effective_haul_nm / 1000.0
        if explicit is None:
            other_accumulator["cargo_mbd"] += cargo_mbd
            other_accumulator["cargo_tonnes"] += cargo_tonnes
            other_accumulator["tonne_miles"] += tonne_miles
            other_accumulator["baseline_weighted_nm"] += cargo_tonnes * baseline_haul_nm
            continue
        route_accumulators[route_id] = {
            "config": explicit,
            "cargo_mbd": cargo_mbd,
            "cargo_tonnes": cargo_tonnes,
            "baseline_haul_nm": baseline_haul_nm,
            "effective_haul_nm": effective_haul_nm,
            "tonne_miles": tonne_miles,
        }

    route_records: list[dict[str, Any]] = []
    for route in network["explicit_routes"]:
        route_id = str(route["route_id"])
        values = route_accumulators[route_id]
        annualized_tonne_miles = (
            values["cargo_mbd"]
            * 365.0
            / barrels_per_tonne
            * values["effective_haul_nm"]
            / 1000.0
        )
        route_records.append(
            {
                "route_id": route_id,
                "route_name": str(route["route_name"]),
                "origin_id": str(route["origin_id"]),
                "origin_name": str(region_records[str(route["origin_id"])]["region_name"]),
                "destination_id": str(route["destination_id"]),
                "destination_name": str(
                    region_records[str(route["destination_id"])]["region_name"]
                ),
                "is_other_pool": False,
                "cargo_mbd": round(values["cargo_mbd"], 8),
                "cargo_million_tonnes": round(values["cargo_tonnes"], 8),
                "baseline_haul_nm": round(values["baseline_haul_nm"], 8),
                "effective_haul_nm": round(values["effective_haul_nm"], 8),
                "tonne_nautical_miles_billion": round(values["tonne_miles"], 8),
                "annualized_tonne_nautical_miles_billion": round(
                    annualized_tonne_miles,
                    8,
                ),
                "route_status": _route_status(
                    values["effective_haul_nm"] / values["baseline_haul_nm"]
                ),
                "chokepoints": list(route["chokepoints"]),
            }
        )

    other_baseline_haul = (
        other_accumulator["baseline_weighted_nm"] / other_accumulator["cargo_tonnes"]
    )
    other_effective_haul = (
        1000.0 * other_accumulator["tonne_miles"] / other_accumulator["cargo_tonnes"]
    )
    other_annualized = (
        other_accumulator["cargo_mbd"]
        * 365.0
        / barrels_per_tonne
        * other_effective_haul
        / 1000.0
    )
    route_records.append(
        {
            "route_id": other_route_id,
            "route_name": str(other_pool_config["route_name"]),
            "origin_id": "multiple_regions",
            "origin_name": "其他与交叉来源",
            "destination_id": "multiple_regions",
            "destination_name": "其他与交叉目的地",
            "is_other_pool": True,
            "cargo_mbd": round(other_accumulator["cargo_mbd"], 8),
            "cargo_million_tonnes": round(other_accumulator["cargo_tonnes"], 8),
            "baseline_haul_nm": round(other_baseline_haul, 8),
            "effective_haul_nm": round(other_effective_haul, 8),
            "tonne_nautical_miles_billion": round(
                other_accumulator["tonne_miles"],
                8,
            ),
            "annualized_tonne_nautical_miles_billion": round(other_annualized, 8),
            "route_status": _route_status(
                other_effective_haul / other_baseline_haul
            ),
            "chokepoints": list(other_pool_config["chokepoints"]),
        }
    )

    total_cargo_mbd = sum(float(route["cargo_mbd"]) for route in route_records)
    total_cargo_tonnes = sum(
        float(route["cargo_million_tonnes"]) for route in route_records
    )
    total_tonne_miles = sum(
        float(route["tonne_nautical_miles_billion"]) for route in route_records
    )
    weighted_haul_nm = 1000.0 * total_tonne_miles / total_cargo_tonnes
    for route in route_records:
        route["market_share"] = round(
            float(route["cargo_mbd"]) / total_cargo_mbd,
            8,
        )

    export_totals: defaultdict[str, float] = defaultdict(float)
    import_totals: defaultdict[str, float] = defaultdict(float)
    for pair_id, cargo_mbd in pair_flows.items():
        origin_id, destination_id = pair_id.split("::", 1)
        export_totals[origin_id] += cargo_mbd
        import_totals[destination_id] += cargo_mbd
    regional_exports = [
        {
            "region_id": region_id,
            "region_name": str(region_records[region_id]["region_name"]),
            "cargo_mbd": round(export_totals[region_id], 8),
            "market_share": round(export_totals[region_id] / total_cargo_mbd, 8),
        }
        for region_id in export_region_ids
    ]
    regional_imports = [
        {
            "region_id": region_id,
            "region_name": str(region_records[region_id]["region_name"]),
            "cargo_mbd": round(import_totals[region_id], 8),
            "market_share": round(import_totals[region_id] / total_cargo_mbd, 8),
        }
        for region_id in import_region_ids
    ]
    record = {
        "shipping_market_scope": str(network["market_scope"]),
        "seaborne_cargo_mbd": round(total_cargo_mbd, 8),
        "cargo_million_tonnes": round(total_cargo_tonnes, 8),
        "average_haul_nm": round(weighted_haul_nm, 8),
        "tonne_nautical_miles_billion": round(total_tonne_miles, 8),
        "annualized_tonne_nautical_miles_billion": round(
            total_cargo_mbd
            * 365.0
            / barrels_per_tonne
            * weighted_haul_nm
            / 1000.0,
            8,
        ),
        "route_count": len(route_records),
        "explicit_route_count": len(network["explicit_routes"]),
        "routes": route_records,
        "regional_exports": regional_exports,
        "regional_imports": regional_imports,
        "route_export_margin_residual_mbd": round(
            total_cargo_mbd - sum(export_totals.values()),
            8,
        ),
        "route_import_margin_residual_mbd": round(
            total_cargo_mbd - sum(import_totals.values()),
            8,
        ),
    }
    return {"pair_preferences": preferences}, record
