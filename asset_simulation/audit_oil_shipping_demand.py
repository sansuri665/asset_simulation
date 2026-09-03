"""Cross-seed audit for physical oil and the regional crude-route network."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics
from typing import Any, Iterable

from .model.engine import run_global_macro
from .model.oil_shipping_world import run_oil_shipping_world


def _correlation(left: list[float], right: list[float]) -> float:
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
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


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("percentile probability must be in [0, 1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + weight * (ordered[upper] - ordered[lower])


def audit_oil_shipping_demand(
    seeds: Iterable[int],
    *,
    years: int = 60,
) -> dict[str, Any]:
    seed_list = tuple(int(seed) for seed in seeds)
    if not seed_list:
        raise ValueError("at least one seed is required")
    demand: list[float] = []
    inventory_days: list[float] = []
    target_inventory_days: list[float] = []
    production: list[float] = []
    production_capacity: list[float] = []
    spare_capacity: list[float] = []
    capacity_utilization: list[float] = []
    production_minus_demand: list[float] = []
    crude_runs: list[float] = []
    crude_production: list[float] = []
    crude_production_capacity: list[float] = []
    crude_spare_capacity: list[float] = []
    crude_inventory_days: list[float] = []
    crude_target_inventory_days: list[float] = []
    crude_production_minus_runs: list[float] = []
    annual_demand_growth_targets: list[float] = []
    annual_capacity_growth_targets: list[float] = []
    per_seed_spare_capacity_ranges: list[float] = []
    per_seed_inventory_p05: list[float] = []
    per_seed_inventory_p95: list[float] = []
    per_seed_mean_supply_gaps: list[float] = []
    per_seed_crude_inventory_p05: list[float] = []
    per_seed_crude_inventory_p95: list[float] = []
    per_seed_mean_crude_supply_gaps: list[float] = []
    per_seed_gulf_export_monthly_change_sd: list[float] = []
    per_seed_gulf_export_ranges: list[float] = []
    per_seed_us_gulf_export_monthly_change_sd: list[float] = []
    per_seed_us_gulf_export_ranges: list[float] = []
    per_seed_other_export_monthly_change_sd: list[float] = []
    per_seed_other_export_ranges: list[float] = []
    per_seed_other_export_overlay_correlation: list[float] = []
    per_seed_other_export_gulf_change_correlation: list[float] = []
    per_seed_east_asia_import_monthly_change_sd: list[float] = []
    per_seed_east_asia_import_ranges: list[float] = []
    per_seed_east_asia_overlay_correlation: list[float] = []
    per_seed_south_asia_import_monthly_change_sd: list[float] = []
    per_seed_south_asia_import_ranges: list[float] = []
    per_seed_south_asia_overlay_correlation: list[float] = []
    per_seed_europe_import_monthly_change_sd: list[float] = []
    per_seed_europe_import_ranges: list[float] = []
    per_seed_europe_overlay_correlation: list[float] = []
    per_seed_north_america_import_monthly_change_sd: list[float] = []
    per_seed_north_america_import_ranges: list[float] = []
    per_seed_north_america_import_overlay_correlation: list[float] = []
    per_seed_rest_of_world_import_monthly_change_sd: list[float] = []
    per_seed_rest_of_world_import_ranges: list[float] = []
    per_seed_rest_of_world_overlay_correlation: list[float] = []
    per_seed_rest_of_world_us_gulf_correlation: list[float] = []
    us_gulf_refinery_adjustments_by_month: dict[int, list[float]] = {
        month: [] for month in range(1, 13)
    }
    us_gulf_adjacent_year_refinery_profile_correlations: list[float] = []
    east_asia_refinery_adjustments_by_month: dict[int, list[float]] = {
        month: [] for month in range(1, 13)
    }
    east_asia_adjacent_year_refinery_profile_correlations: list[float] = []
    south_asia_refinery_adjustments_by_month: dict[int, list[float]] = {
        month: [] for month in range(1, 13)
    }
    south_asia_adjacent_year_refinery_profile_correlations: list[float] = []
    europe_refinery_adjustments_by_month: dict[int, list[float]] = {
        month: [] for month in range(1, 13)
    }
    europe_adjacent_year_refinery_profile_correlations: list[float] = []
    north_america_import_refinery_adjustments_by_month: dict[int, list[float]] = {
        month: [] for month in range(1, 13)
    }
    north_america_import_adjacent_year_refinery_profile_correlations: list[float] = []
    rest_of_world_own_refinery_adjustments_by_month: dict[int, list[float]] = {
        month: [] for month in range(1, 13)
    }
    rest_of_world_adjacent_year_refinery_profile_correlations: list[float] = []
    south_asia_crude_run_share_changes: list[float] = []
    europe_crude_run_share_changes: list[float] = []
    brazil_guyana_production_share_changes: list[float] = []
    ending_demand: list[float] = []
    cargo: list[float] = []
    implied_cargo_shares: list[float] = []
    haul: list[float] = []
    tonne_miles: list[float] = []
    reference_seaborne_cargo: list[float] = []
    initial_major_route_reference_deviation_pct: list[float] = []
    route_margin_scaled_diagnostic_difference_mbd: list[float] = []
    reference_years: set[int] = set()
    calibrated_major_route_counts: set[int] = set()
    active_pair_counts: set[int] = set()
    residual_pair_counts: set[int] = set()
    maximum_balance_residual = 0.0
    maximum_crude_balance_residual = 0.0
    total_unmet_demand = 0.0
    total_unmet_crude_runs = 0.0
    result_hashes: list[str] = []
    regime_counts: Counter[str] = Counter()
    regimes_stable = True
    adjacent_year_demand_shape_correlations: list[float] = []
    maximum_route_cargo_residual_mbd = 0.0
    maximum_route_tonne_mile_residual = 0.0
    maximum_regional_flow_residual_mbd = 0.0
    maximum_regional_production_residual_mbd = 0.0
    maximum_regional_refinery_residual_mbd = 0.0
    maximum_regional_inventory_residual_mmbbl = 0.0
    maximum_regional_pipeline_residual_mbd = 0.0
    maximum_regional_net_balance_residual_mbd = 0.0
    maximum_production_conservation_residual_mbd = 0.0
    maximum_gulf_policy_target_mbd = 0.0
    maximum_gulf_policy_monthly_adjustment_mbd = 0.0
    maximum_us_gulf_refinery_cycle_residual_mbd = 0.0
    maximum_refinery_conservation_residual_mbd = 0.0
    maximum_east_asia_refinery_target_mbd = 0.0
    maximum_east_asia_refinery_monthly_adjustment_mbd = 0.0
    east_asia_always_importer = True
    maximum_south_asia_refinery_target_mbd = 0.0
    maximum_south_asia_refinery_monthly_adjustment_mbd = 0.0
    south_asia_always_importer = True
    maximum_europe_refinery_target_mbd = 0.0
    maximum_europe_refinery_monthly_adjustment_mbd = 0.0
    europe_always_importer = True
    maximum_north_america_import_refinery_target_mbd = 0.0
    maximum_north_america_import_refinery_monthly_adjustment_mbd = 0.0
    north_america_import_always_importer = True
    maximum_rest_of_world_refinery_target_mbd = 0.0
    maximum_rest_of_world_refinery_monthly_adjustment_mbd = 0.0
    rest_of_world_always_importer = True
    maximum_us_gulf_production_target_mbd = 0.0
    maximum_us_gulf_refinery_target_mbd = 0.0
    maximum_us_gulf_production_monthly_adjustment_mbd = 0.0
    maximum_us_gulf_refinery_monthly_adjustment_mbd = 0.0
    route_ids: set[str] = set()

    def allocated_share(
        regions: dict[str, dict[str, object]],
        region_id: str,
        field: str,
        total: float,
    ) -> float:
        region = regions[region_id]
        allocated = float(region[field])
        if field == "crude_production_mbd":
            allocated -= float(region["production_policy_adjustment_mbd"])
            allocated -= float(region["production_cycle_adjustment_mbd"])
            allocated -= float(region["conservation_adjustment_mbd"])
        if field == "crude_refinery_runs_mbd":
            allocated -= float(region["refinery_cycle_adjustment_mbd"])
            allocated -= float(region["refinery_conservation_adjustment_mbd"])
        return allocated / float(total)

    for seed in seed_list:
        world = run_oil_shipping_world(run_global_macro(seed, years))

        first_regions = {
            str(region["region_id"]): region
            for region in world.turns[0]["regional_balances"]
        }
        last_regions = {
            str(region["region_id"]): region
            for region in world.turns[-1]["regional_balances"]
        }
        south_asia_crude_run_share_changes.append(
            allocated_share(
                last_regions,
                "south_asia",
                "crude_refinery_runs_mbd",
                world.turns[-1]["crude_refinery_runs_mbd"],
            )
            - allocated_share(
                first_regions,
                "south_asia",
                "crude_refinery_runs_mbd",
                world.turns[0]["crude_refinery_runs_mbd"],
            )
        )
        europe_crude_run_share_changes.append(
            allocated_share(
                last_regions,
                "europe",
                "crude_refinery_runs_mbd",
                world.turns[-1]["crude_refinery_runs_mbd"],
            )
            - allocated_share(
                first_regions,
                "europe",
                "crude_refinery_runs_mbd",
                world.turns[0]["crude_refinery_runs_mbd"],
            )
        )
        brazil_guyana_production_share_changes.append(
            allocated_share(
                last_regions,
                "brazil_guyana",
                "crude_production_mbd",
                world.turns[-1]["crude_production_mbd"],
            )
            - allocated_share(
                first_regions,
                "brazil_guyana",
                "crude_production_mbd",
                world.turns[0]["crude_production_mbd"],
            )
        )
        seed_inventory_days: list[float] = []
        seed_spare_capacity: list[float] = []
        seed_supply_gaps: list[float] = []
        seed_crude_inventory_days: list[float] = []
        seed_crude_supply_gaps: list[float] = []
        seed_gulf_exports: list[float] = []
        seed_us_gulf_exports: list[float] = []
        seed_other_export_exports: list[float] = []
        seed_other_export_overlay: list[float] = []
        seed_east_asia_imports: list[float] = []
        seed_east_asia_overlay: list[float] = []
        seed_south_asia_imports: list[float] = []
        seed_south_asia_overlay: list[float] = []
        seed_europe_imports: list[float] = []
        seed_europe_overlay: list[float] = []
        seed_north_america_imports: list[float] = []
        seed_north_america_overlay: list[float] = []
        seed_rest_of_world_imports: list[float] = []
        seed_rest_of_world_overlay: list[float] = []
        seed_rest_of_world_own_refinery: list[float] = []
        seed_us_gulf_refinery: list[float] = []
        seed_us_gulf_refinery_adjustments_by_year: dict[int, list[float]] = {}
        seed_east_asia_refinery_adjustments_by_year: dict[int, list[float]] = {}
        seed_south_asia_refinery_adjustments_by_year: dict[int, list[float]] = {}
        seed_europe_refinery_adjustments_by_year: dict[int, list[float]] = {}
        seed_north_america_refinery_adjustments_by_year: dict[int, list[float]] = {}
        seed_rest_of_world_own_refinery_adjustments_by_year: dict[int, list[float]] = {}
        previous_gulf_policy_adjustment: float | None = None
        previous_us_gulf_production_adjustment: float | None = None
        previous_us_gulf_refinery_adjustment: float | None = None
        previous_east_asia_refinery_adjustment: float | None = None
        previous_south_asia_refinery_adjustment: float | None = None
        previous_europe_refinery_adjustment: float | None = None
        previous_north_america_refinery_adjustment: float | None = None
        previous_rest_of_world_own_refinery_adjustment: float | None = None
        result_hashes.append(str(world.identity["result_hash"]))
        regime = str(world.identity["long_run_demand_regime"])
        regime_counts[regime] += 1
        ending_demand.append(float(world.annual[-1]["average_demand_mbd"]))
        turns_by_year: dict[int, list[float]] = {}
        for turn in world.turns:
            demand.append(float(turn["realized_demand_mbd"]))
            production.append(float(turn["production_mbd"]))
            production_capacity.append(float(turn["production_capacity_mbd"]))
            spare_capacity.append(float(turn["spare_capacity_mbd"]))
            capacity_utilization.append(float(turn["capacity_utilization_pct"]))
            production_minus_demand.append(
                float(turn["production_mbd"])
                - float(turn["realized_demand_mbd"])
            )
            seed_supply_gaps.append(production_minus_demand[-1])
            crude_runs.append(float(turn["crude_refinery_runs_mbd"]))
            crude_production.append(float(turn["crude_production_mbd"]))
            crude_production_capacity.append(
                float(turn["crude_production_capacity_mbd"])
            )
            crude_spare_capacity.append(float(turn["crude_spare_capacity_mbd"]))
            crude_inventory_days.append(float(turn["crude_inventory_days"]))
            seed_crude_inventory_days.append(crude_inventory_days[-1])
            crude_target_inventory_days.append(
                float(turn["crude_target_inventory_days"])
            )
            crude_production_minus_runs.append(
                float(turn["crude_production_mbd"])
                - float(turn["crude_refinery_runs_mbd"])
            )
            seed_crude_supply_gaps.append(crude_production_minus_runs[-1])
            inventory_days.append(float(turn["inventory_days"]))
            seed_inventory_days.append(inventory_days[-1])
            seed_spare_capacity.append(float(turn["spare_capacity_mbd"]))
            target_inventory_days.append(float(turn["target_inventory_days"]))
            if int(turn["month"]) == 1:
                annual_demand_growth_targets.append(
                    float(turn["annual_demand_growth_target_pct"])
                )
                annual_capacity_growth_targets.append(
                    float(turn["annual_capacity_growth_target_pct"])
                )
            regimes_stable = regimes_stable and (
                str(turn["long_run_demand_regime"]) == regime
            )
            turns_by_year.setdefault(int(turn["year"]), []).append(
                float(turn["realized_demand_mbd"])
            )
            cargo.append(float(turn["seaborne_cargo_mbd"]))
            implied_cargo_shares.append(
                float(turn["seaborne_cargo_mbd"])
                / float(turn["crude_refinery_runs_mbd"])
            )
            haul.append(float(turn["average_haul_nm"]))
            tonne_miles.append(float(turn["annualized_tonne_nautical_miles_billion"]))
            routes = turn["routes"]
            reference_seaborne_cargo.append(
                float(turn["reference_seaborne_cargo_mbd"])
            )
            reference_years.add(int(turn["reference_year"]))
            calibrated_major_route_counts.add(
                int(turn["calibrated_major_route_count"])
            )
            active_pair_counts.add(int(turn["active_pair_count"]))
            for route in routes:
                route_margin_scaled_diagnostic_difference_mbd.append(
                    abs(
                        float(route["cargo_mbd"])
                        - float(route["margin_scaled_reference_mbd"])
                    )
                )
                if bool(route["is_other_pool"]):
                    residual_pair_counts.add(int(route["residual_pair_count"]))
                elif int(turn["turn_index"]) == 0:
                    initial_major_route_reference_deviation_pct.append(
                        abs(float(route["cargo_vs_reference_pct"]))
                    )
            regions_by_id = {
                str(region["region_id"]): region
                for region in turn["regional_balances"]
            }
            gulf = regions_by_id["gulf"]
            us_gulf = regions_by_id["us_gulf"]
            other_export = regions_by_id["other_export_regions"]
            east_asia = regions_by_id["east_asia"]
            south_asia = regions_by_id["south_asia"]
            europe = regions_by_id["europe"]
            north_america = regions_by_id["north_america_import"]
            rest_of_world = regions_by_id["rest_of_world"]
            seed_gulf_exports.append(float(gulf["net_seaborne_balance_mbd"]))
            seed_us_gulf_exports.append(
                float(us_gulf["net_seaborne_balance_mbd"])
            )
            seed_other_export_exports.append(
                float(other_export["net_seaborne_balance_mbd"])
            )
            seed_east_asia_imports.append(
                -float(east_asia["net_seaborne_balance_mbd"])
            )
            seed_south_asia_imports.append(
                -float(south_asia["net_seaborne_balance_mbd"])
            )
            seed_europe_imports.append(
                -float(europe["net_seaborne_balance_mbd"])
            )
            seed_north_america_imports.append(
                -float(north_america["net_seaborne_balance_mbd"])
            )
            seed_rest_of_world_imports.append(
                -float(rest_of_world["net_seaborne_balance_mbd"])
            )
            east_asia_always_importer = east_asia_always_importer and (
                float(east_asia["net_seaborne_balance_mbd"]) < 0.0
            )
            south_asia_always_importer = south_asia_always_importer and (
                float(south_asia["net_seaborne_balance_mbd"]) < 0.0
            )
            europe_always_importer = europe_always_importer and (
                float(europe["net_seaborne_balance_mbd"]) < 0.0
            )
            north_america_import_always_importer = (
                north_america_import_always_importer
                and float(north_america["net_seaborne_balance_mbd"]) < 0.0
            )
            rest_of_world_always_importer = rest_of_world_always_importer and (
                float(rest_of_world["net_seaborne_balance_mbd"]) < 0.0
            )
            seed_other_export_overlay.append(
                -float(gulf["production_policy_adjustment_mbd"])
                - float(us_gulf["production_cycle_adjustment_mbd"])
                - float(
                    regions_by_id["brazil_guyana"]["production_cycle_adjustment_mbd"]
                )
                - float(
                    regions_by_id["west_africa"]["production_cycle_adjustment_mbd"]
                )
            )
            seed_east_asia_overlay.append(seed_other_export_overlay[-1])
            seed_south_asia_overlay.append(seed_other_export_overlay[-1])
            seed_europe_overlay.append(seed_other_export_overlay[-1])
            seed_north_america_overlay.append(seed_other_export_overlay[-1])
            seed_rest_of_world_overlay.append(seed_other_export_overlay[-1])
            gulf_policy_adjustment = float(
                gulf["production_policy_adjustment_mbd"]
            )
            maximum_gulf_policy_target_mbd = max(
                maximum_gulf_policy_target_mbd,
                abs(float(gulf["production_policy_target_mbd"])),
            )
            if previous_gulf_policy_adjustment is not None:
                maximum_gulf_policy_monthly_adjustment_mbd = max(
                    maximum_gulf_policy_monthly_adjustment_mbd,
                    abs(gulf_policy_adjustment - previous_gulf_policy_adjustment),
                )
            previous_gulf_policy_adjustment = gulf_policy_adjustment
            maximum_production_conservation_residual_mbd = max(
                maximum_production_conservation_residual_mbd,
                abs(
                    float(turn["regional_production_conservation_residual_mbd"])
                ),
                abs(
                    sum(
                        float(region["production_policy_adjustment_mbd"])
                        + float(region["production_cycle_adjustment_mbd"])
                        + float(region["conservation_adjustment_mbd"])
                        for region in regions_by_id.values()
                    )
                ),
            )
            us_gulf_production_adjustment = float(
                us_gulf["production_cycle_adjustment_mbd"]
            )
            us_gulf_refinery_adjustment = float(
                us_gulf["refinery_cycle_adjustment_mbd"]
            )
            east_asia_refinery_adjustment = float(
                east_asia["refinery_cycle_adjustment_mbd"]
            )
            south_asia_refinery_adjustment = float(
                south_asia["refinery_cycle_adjustment_mbd"]
            )
            europe_refinery_adjustment = float(
                europe["refinery_cycle_adjustment_mbd"]
            )
            north_america_refinery_adjustment = float(
                north_america["refinery_cycle_adjustment_mbd"]
            )
            rest_of_world_own_refinery_adjustment = (
                us_gulf_refinery_adjustment
                + float(rest_of_world["refinery_cycle_adjustment_mbd"])
            )
            us_gulf_refinery_adjustments_by_month[int(turn["month"])].append(
                us_gulf_refinery_adjustment
            )
            east_asia_refinery_adjustments_by_month[int(turn["month"])].append(
                east_asia_refinery_adjustment
            )
            south_asia_refinery_adjustments_by_month[int(turn["month"])].append(
                south_asia_refinery_adjustment
            )
            europe_refinery_adjustments_by_month[int(turn["month"])].append(
                europe_refinery_adjustment
            )
            north_america_import_refinery_adjustments_by_month[int(turn["month"])].append(
                north_america_refinery_adjustment
            )
            rest_of_world_own_refinery_adjustments_by_month[int(turn["month"])].append(
                rest_of_world_own_refinery_adjustment
            )
            seed_us_gulf_refinery_adjustments_by_year.setdefault(
                int(turn["year"]),
                [],
            ).append(us_gulf_refinery_adjustment)
            seed_east_asia_refinery_adjustments_by_year.setdefault(
                int(turn["year"]),
                [],
            ).append(east_asia_refinery_adjustment)
            seed_south_asia_refinery_adjustments_by_year.setdefault(
                int(turn["year"]),
                [],
            ).append(south_asia_refinery_adjustment)
            seed_europe_refinery_adjustments_by_year.setdefault(
                int(turn["year"]),
                [],
            ).append(europe_refinery_adjustment)
            seed_north_america_refinery_adjustments_by_year.setdefault(
                int(turn["year"]),
                [],
            ).append(north_america_refinery_adjustment)
            seed_rest_of_world_own_refinery_adjustments_by_year.setdefault(
                int(turn["year"]),
                [],
            ).append(rest_of_world_own_refinery_adjustment)
            seed_rest_of_world_own_refinery.append(
                rest_of_world_own_refinery_adjustment
            )
            seed_us_gulf_refinery.append(us_gulf_refinery_adjustment)
            maximum_us_gulf_production_target_mbd = max(
                maximum_us_gulf_production_target_mbd,
                abs(float(us_gulf["production_cycle_target_mbd"])),
            )
            maximum_us_gulf_refinery_target_mbd = max(
                maximum_us_gulf_refinery_target_mbd,
                abs(float(us_gulf["refinery_cycle_target_mbd"])),
            )
            maximum_east_asia_refinery_target_mbd = max(
                maximum_east_asia_refinery_target_mbd,
                abs(float(east_asia["refinery_cycle_target_mbd"])),
            )
            maximum_south_asia_refinery_target_mbd = max(
                maximum_south_asia_refinery_target_mbd,
                abs(float(south_asia["refinery_cycle_target_mbd"])),
            )
            maximum_europe_refinery_target_mbd = max(
                maximum_europe_refinery_target_mbd,
                abs(float(europe["refinery_cycle_target_mbd"])),
            )
            maximum_north_america_import_refinery_target_mbd = max(
                maximum_north_america_import_refinery_target_mbd,
                abs(float(north_america["refinery_cycle_target_mbd"])),
            )
            maximum_rest_of_world_refinery_target_mbd = max(
                maximum_rest_of_world_refinery_target_mbd,
                abs(
                    float(rest_of_world["refinery_cycle_target_mbd"])
                    + float(us_gulf["refinery_cycle_target_mbd"])
                ),
            )
            if previous_us_gulf_production_adjustment is not None:
                maximum_us_gulf_production_monthly_adjustment_mbd = max(
                    maximum_us_gulf_production_monthly_adjustment_mbd,
                    abs(
                        us_gulf_production_adjustment
                        - previous_us_gulf_production_adjustment
                    ),
                )
            if previous_us_gulf_refinery_adjustment is not None:
                maximum_us_gulf_refinery_monthly_adjustment_mbd = max(
                    maximum_us_gulf_refinery_monthly_adjustment_mbd,
                    abs(
                        us_gulf_refinery_adjustment
                        - previous_us_gulf_refinery_adjustment
                    ),
                )
            if previous_east_asia_refinery_adjustment is not None:
                maximum_east_asia_refinery_monthly_adjustment_mbd = max(
                    maximum_east_asia_refinery_monthly_adjustment_mbd,
                    abs(
                        east_asia_refinery_adjustment
                        - previous_east_asia_refinery_adjustment
                    ),
                )
            if previous_south_asia_refinery_adjustment is not None:
                maximum_south_asia_refinery_monthly_adjustment_mbd = max(
                    maximum_south_asia_refinery_monthly_adjustment_mbd,
                    abs(
                        south_asia_refinery_adjustment
                        - previous_south_asia_refinery_adjustment
                    ),
                )
            if previous_europe_refinery_adjustment is not None:
                maximum_europe_refinery_monthly_adjustment_mbd = max(
                    maximum_europe_refinery_monthly_adjustment_mbd,
                    abs(
                        europe_refinery_adjustment
                        - previous_europe_refinery_adjustment
                    ),
                )
            if previous_north_america_refinery_adjustment is not None:
                maximum_north_america_import_refinery_monthly_adjustment_mbd = max(
                    maximum_north_america_import_refinery_monthly_adjustment_mbd,
                    abs(
                        north_america_refinery_adjustment
                        - previous_north_america_refinery_adjustment
                    ),
                )
            if previous_rest_of_world_own_refinery_adjustment is not None:
                maximum_rest_of_world_refinery_monthly_adjustment_mbd = max(
                    maximum_rest_of_world_refinery_monthly_adjustment_mbd,
                    abs(
                        rest_of_world_own_refinery_adjustment
                        - previous_rest_of_world_own_refinery_adjustment
                    ),
                )
            previous_us_gulf_production_adjustment = us_gulf_production_adjustment
            previous_us_gulf_refinery_adjustment = us_gulf_refinery_adjustment
            previous_east_asia_refinery_adjustment = east_asia_refinery_adjustment
            previous_south_asia_refinery_adjustment = south_asia_refinery_adjustment
            previous_europe_refinery_adjustment = europe_refinery_adjustment
            previous_north_america_refinery_adjustment = (
                north_america_refinery_adjustment
            )
            previous_rest_of_world_own_refinery_adjustment = (
                rest_of_world_own_refinery_adjustment
            )
            maximum_us_gulf_refinery_cycle_residual_mbd = max(
                maximum_us_gulf_refinery_cycle_residual_mbd,
                abs(
                    us_gulf_refinery_adjustment
                    + float(rest_of_world["refinery_cycle_adjustment_mbd"])
                    - rest_of_world_own_refinery_adjustment
                ),
            )
            maximum_refinery_conservation_residual_mbd = max(
                maximum_refinery_conservation_residual_mbd,
                abs(float(turn["regional_refinery_conservation_residual_mbd"])),
                abs(
                    sum(
                        float(region["refinery_cycle_adjustment_mbd"])
                        + float(region["refinery_conservation_adjustment_mbd"])
                        for region in regions_by_id.values()
                    )
                ),
            )
            route_ids.update(str(route["route_id"]) for route in routes)
            maximum_route_cargo_residual_mbd = max(
                maximum_route_cargo_residual_mbd,
                abs(
                    sum(float(route["cargo_mbd"]) for route in routes)
                    - float(turn["seaborne_cargo_mbd"])
                ),
            )
            maximum_route_tonne_mile_residual = max(
                maximum_route_tonne_mile_residual,
                abs(
                    sum(
                        float(route["tonne_nautical_miles_billion"])
                        for route in routes
                    )
                    - float(turn["tonne_nautical_miles_billion"])
                ),
            )
            maximum_regional_flow_residual_mbd = max(
                maximum_regional_flow_residual_mbd,
                abs(
                    sum(
                        float(region["cargo_mbd"])
                        for region in turn["regional_exports"]
                    )
                    - float(turn["seaborne_cargo_mbd"])
                ),
                abs(
                    sum(
                        float(region["cargo_mbd"])
                        for region in turn["regional_imports"]
                    )
                    - float(turn["seaborne_cargo_mbd"])
                ),
            )
            maximum_balance_residual = max(
                maximum_balance_residual,
                abs(float(turn["mass_balance_residual_mmbbl"])),
            )
            maximum_crude_balance_residual = max(
                maximum_crude_balance_residual,
                abs(float(turn["crude_mass_balance_residual_mmbbl"])),
            )
            maximum_regional_production_residual_mbd = max(
                maximum_regional_production_residual_mbd,
                abs(float(turn["regional_crude_production_residual_mbd"])),
            )
            maximum_regional_refinery_residual_mbd = max(
                maximum_regional_refinery_residual_mbd,
                abs(float(turn["regional_crude_runs_residual_mbd"])),
            )
            maximum_regional_inventory_residual_mmbbl = max(
                maximum_regional_inventory_residual_mmbbl,
                abs(float(turn["regional_crude_inventory_residual_mmbbl"])),
            )
            maximum_regional_pipeline_residual_mbd = max(
                maximum_regional_pipeline_residual_mbd,
                abs(float(turn["regional_crude_pipeline_residual_mbd"])),
            )
            maximum_regional_net_balance_residual_mbd = max(
                maximum_regional_net_balance_residual_mbd,
                abs(float(turn["regional_net_balance_residual_mbd"])),
            )
            total_unmet_demand += float(turn["unmet_demand_mmbbl"])
            total_unmet_crude_runs += float(
                turn["crude_unmet_refinery_runs_mmbbl"]
            )
        per_seed_spare_capacity_ranges.append(
            max(seed_spare_capacity) - min(seed_spare_capacity)
        )
        per_seed_inventory_p05.append(_percentile(seed_inventory_days, 0.05))
        per_seed_inventory_p95.append(_percentile(seed_inventory_days, 0.95))
        per_seed_mean_supply_gaps.append(statistics.fmean(seed_supply_gaps))
        per_seed_crude_inventory_p05.append(
            _percentile(seed_crude_inventory_days, 0.05)
        )
        per_seed_crude_inventory_p95.append(
            _percentile(seed_crude_inventory_days, 0.95)
        )
        per_seed_mean_crude_supply_gaps.append(
            statistics.fmean(seed_crude_supply_gaps)
        )
        gulf_export_changes = [
            current - previous
            for previous, current in zip(
                seed_gulf_exports,
                seed_gulf_exports[1:],
            )
        ]
        per_seed_gulf_export_monthly_change_sd.append(
            statistics.stdev(gulf_export_changes)
        )
        per_seed_gulf_export_ranges.append(
            max(seed_gulf_exports) - min(seed_gulf_exports)
        )
        us_gulf_export_changes = [
            current - previous
            for previous, current in zip(
                seed_us_gulf_exports,
                seed_us_gulf_exports[1:],
            )
        ]
        per_seed_us_gulf_export_monthly_change_sd.append(
            statistics.stdev(us_gulf_export_changes)
        )
        per_seed_us_gulf_export_ranges.append(
            max(seed_us_gulf_exports) - min(seed_us_gulf_exports)
        )
        other_export_changes = [
            current - previous
            for previous, current in zip(
                seed_other_export_exports,
                seed_other_export_exports[1:],
            )
        ]
        per_seed_other_export_monthly_change_sd.append(
            statistics.stdev(other_export_changes)
        )
        per_seed_other_export_ranges.append(
            max(seed_other_export_exports) - min(seed_other_export_exports)
        )
        per_seed_other_export_overlay_correlation.append(
            _correlation(seed_other_export_exports, seed_other_export_overlay)
        )
        per_seed_other_export_gulf_change_correlation.append(
            _correlation(other_export_changes, gulf_export_changes)
        )
        east_asia_import_changes = [
            current - previous
            for previous, current in zip(
                seed_east_asia_imports,
                seed_east_asia_imports[1:],
            )
        ]
        per_seed_east_asia_import_monthly_change_sd.append(
            statistics.stdev(east_asia_import_changes)
        )
        per_seed_east_asia_import_ranges.append(
            max(seed_east_asia_imports) - min(seed_east_asia_imports)
        )
        per_seed_east_asia_overlay_correlation.append(
            _correlation(seed_east_asia_imports, seed_east_asia_overlay)
        )
        south_asia_import_changes = [
            current - previous
            for previous, current in zip(
                seed_south_asia_imports,
                seed_south_asia_imports[1:],
            )
        ]
        per_seed_south_asia_import_monthly_change_sd.append(
            statistics.stdev(south_asia_import_changes)
        )
        per_seed_south_asia_import_ranges.append(
            max(seed_south_asia_imports) - min(seed_south_asia_imports)
        )
        per_seed_south_asia_overlay_correlation.append(
            _correlation(seed_south_asia_imports, seed_south_asia_overlay)
        )
        europe_import_changes = [
            current - previous
            for previous, current in zip(
                seed_europe_imports,
                seed_europe_imports[1:],
            )
        ]
        per_seed_europe_import_monthly_change_sd.append(
            statistics.stdev(europe_import_changes)
        )
        per_seed_europe_import_ranges.append(
            max(seed_europe_imports) - min(seed_europe_imports)
        )
        per_seed_europe_overlay_correlation.append(
            _correlation(seed_europe_imports, seed_europe_overlay)
        )
        north_america_import_changes = [
            current - previous
            for previous, current in zip(
                seed_north_america_imports,
                seed_north_america_imports[1:],
            )
        ]
        per_seed_north_america_import_monthly_change_sd.append(
            statistics.stdev(north_america_import_changes)
        )
        per_seed_north_america_import_ranges.append(
            max(seed_north_america_imports) - min(seed_north_america_imports)
        )
        per_seed_north_america_import_overlay_correlation.append(
            _correlation(seed_north_america_imports, seed_north_america_overlay)
        )
        rest_of_world_import_changes = [
            current - previous
            for previous, current in zip(
                seed_rest_of_world_imports,
                seed_rest_of_world_imports[1:],
            )
        ]
        per_seed_rest_of_world_import_monthly_change_sd.append(
            statistics.stdev(rest_of_world_import_changes)
        )
        per_seed_rest_of_world_import_ranges.append(
            max(seed_rest_of_world_imports) - min(seed_rest_of_world_imports)
        )
        per_seed_rest_of_world_overlay_correlation.append(
            _correlation(seed_rest_of_world_imports, seed_rest_of_world_overlay)
        )
        per_seed_rest_of_world_us_gulf_correlation.append(
            _correlation(seed_rest_of_world_own_refinery, seed_us_gulf_refinery)
        )
        us_gulf_years = sorted(seed_us_gulf_refinery_adjustments_by_year)
        us_gulf_adjacent_year_refinery_profile_correlations.extend(
            _correlation(
                seed_us_gulf_refinery_adjustments_by_year[previous],
                seed_us_gulf_refinery_adjustments_by_year[current],
            )
            for previous, current in zip(us_gulf_years, us_gulf_years[1:])
        )
        east_asia_years = sorted(seed_east_asia_refinery_adjustments_by_year)
        east_asia_adjacent_year_refinery_profile_correlations.extend(
            _correlation(
                seed_east_asia_refinery_adjustments_by_year[previous],
                seed_east_asia_refinery_adjustments_by_year[current],
            )
            for previous, current in zip(east_asia_years, east_asia_years[1:])
        )
        south_asia_years = sorted(seed_south_asia_refinery_adjustments_by_year)
        south_asia_adjacent_year_refinery_profile_correlations.extend(
            _correlation(
                seed_south_asia_refinery_adjustments_by_year[previous],
                seed_south_asia_refinery_adjustments_by_year[current],
            )
            for previous, current in zip(south_asia_years, south_asia_years[1:])
        )
        europe_years = sorted(seed_europe_refinery_adjustments_by_year)
        europe_adjacent_year_refinery_profile_correlations.extend(
            _correlation(
                seed_europe_refinery_adjustments_by_year[previous],
                seed_europe_refinery_adjustments_by_year[current],
            )
            for previous, current in zip(europe_years, europe_years[1:])
        )
        north_america_years = sorted(seed_north_america_refinery_adjustments_by_year)
        north_america_import_adjacent_year_refinery_profile_correlations.extend(
            _correlation(
                seed_north_america_refinery_adjustments_by_year[previous],
                seed_north_america_refinery_adjustments_by_year[current],
            )
            for previous, current in zip(
                north_america_years,
                north_america_years[1:],
            )
        )
        rest_of_world_years = sorted(
            seed_rest_of_world_own_refinery_adjustments_by_year
        )
        rest_of_world_adjacent_year_refinery_profile_correlations.extend(
            _correlation(
                seed_rest_of_world_own_refinery_adjustments_by_year[previous],
                seed_rest_of_world_own_refinery_adjustments_by_year[current],
            )
            for previous, current in zip(
                rest_of_world_years,
                rest_of_world_years[1:],
            )
        )
        years_in_world = sorted(turns_by_year)
        adjacent_year_demand_shape_correlations.extend(
            _correlation(turns_by_year[previous], turns_by_year[current])
            for previous, current in zip(years_in_world, years_in_world[1:])
        )

    baseline_macro = run_global_macro(seed_list[0], max(5, min(years, 12)))
    baseline = run_oil_shipping_world(baseline_macro).turns[0]
    rerouted = run_oil_shipping_world(
        baseline_macro,
        scenario_by_turn={0: {"average_haul_impulse_pct": 15.0}},
    ).turns[0]
    reroute_ratio = float(rerouted["tonne_nautical_miles_billion"]) / float(
        baseline["tonne_nautical_miles_billion"]
    )
    demand_spike_world = run_oil_shipping_world(
        baseline_macro,
        scenario_by_turn={0: {"demand_rate_impulse_pct": 5.0}},
    )
    demand_spike = demand_spike_world.turns[0]
    demand_spike_next = demand_spike_world.turns[1]
    baseline_next = run_oil_shipping_world(baseline_macro).turns[1]
    inventory_p05 = _percentile(inventory_days, 0.05)
    inventory_p50 = _percentile(inventory_days, 0.50)
    inventory_p95 = _percentile(inventory_days, 0.95)
    spare_capacity_p05 = _percentile(spare_capacity, 0.05)
    spare_capacity_p50 = _percentile(spare_capacity, 0.50)
    spare_capacity_p95 = _percentile(spare_capacity, 0.95)
    absolute_supply_gap_p95 = _percentile(
        [abs(value) for value in production_minus_demand],
        0.95,
    )
    crude_inventory_p05 = _percentile(crude_inventory_days, 0.05)
    crude_inventory_p50 = _percentile(crude_inventory_days, 0.50)
    crude_inventory_p95 = _percentile(crude_inventory_days, 0.95)
    crude_spare_capacity_p05 = _percentile(crude_spare_capacity, 0.05)
    crude_spare_capacity_p50 = _percentile(crude_spare_capacity, 0.50)
    crude_spare_capacity_p95 = _percentile(crude_spare_capacity, 0.95)
    absolute_crude_supply_gap_p95 = _percentile(
        [abs(value) for value in crude_production_minus_runs],
        0.95,
    )
    annual_capacity_demand_growth_correlation = _correlation(
        annual_demand_growth_targets,
        annual_capacity_growth_targets,
    )
    us_gulf_maintenance_run_rate = statistics.fmean(
        statistics.fmean(us_gulf_refinery_adjustments_by_month[month])
        for month in (1, 2, 3, 9, 10, 11)
    )
    us_gulf_high_run_rate = statistics.fmean(
        statistics.fmean(us_gulf_refinery_adjustments_by_month[month])
        for month in (5, 6, 7, 8, 12)
    )
    east_asia_maintenance_run_rate = statistics.fmean(
        statistics.fmean(east_asia_refinery_adjustments_by_month[month])
        for month in (3, 4, 5, 10, 11)
    )
    east_asia_high_run_rate = statistics.fmean(
        statistics.fmean(east_asia_refinery_adjustments_by_month[month])
        for month in (6, 7, 8)
    )
    south_asia_maintenance_run_rate = statistics.fmean(
        statistics.fmean(south_asia_refinery_adjustments_by_month[month])
        for month in (4, 5, 9, 10)
    )
    south_asia_high_run_rate = statistics.fmean(
        statistics.fmean(south_asia_refinery_adjustments_by_month[month])
        for month in (7, 8, 12)
    )
    europe_maintenance_run_rate = statistics.fmean(
        statistics.fmean(europe_refinery_adjustments_by_month[month])
        for month in (3, 4, 9, 10)
    )
    europe_high_run_rate = statistics.fmean(
        statistics.fmean(europe_refinery_adjustments_by_month[month])
        for month in (6, 7, 12, 1)
    )
    north_america_import_maintenance_run_rate = statistics.fmean(
        statistics.fmean(
            north_america_import_refinery_adjustments_by_month[month]
        )
        for month in (5, 6, 10, 11)
    )
    north_america_import_high_run_rate = statistics.fmean(
        statistics.fmean(
            north_america_import_refinery_adjustments_by_month[month]
        )
        for month in (8, 1, 2)
    )
    rest_of_world_maintenance_run_rate = statistics.fmean(
        statistics.fmean(
            rest_of_world_own_refinery_adjustments_by_month[month]
        )
        for month in (2, 3, 7, 8)
    )
    rest_of_world_high_run_rate = statistics.fmean(
        statistics.fmean(rest_of_world_own_refinery_adjustments_by_month[month])
        for month in (5, 11, 12)
    )
    checks = {
        "deterministic_seed_hashes_unique": len(set(result_hashes)) == len(seed_list),
        "mass_balance_exact": maximum_balance_residual <= 1e-6,
        "crude_mass_balance_exact": maximum_crude_balance_residual <= 1e-6,
        "ordinary_world_has_no_unmet_demand": total_unmet_demand <= 1e-6,
        "ordinary_world_has_no_unmet_crude_runs": total_unmet_crude_runs <= 1e-6,
        "total_liquids_and_crude_are_distinct": (
            min(
                liquids_demand - runs
                for liquids_demand, runs in zip(demand, crude_runs)
            ) >= 5.0
            and statistics.fmean(crude_runs) < 0.90 * statistics.fmean(demand)
        ),
        "crude_production_never_exceeds_capacity": all(
            output <= capacity + 1e-9
            for output, capacity in zip(
                crude_production,
                crude_production_capacity,
            )
        ),
        "crude_inventory_central_90pct_is_credible": (
            crude_inventory_p05 >= 38.0 and crude_inventory_p95 <= 66.0
        ),
        "every_seed_crude_inventory_central_90pct_is_credible": (
            min(per_seed_crude_inventory_p05) >= 35.0
            and max(per_seed_crude_inventory_p95) <= 70.0
        ),
        "crude_inventory_target_is_bounded": (
            min(crude_target_inventory_days) >= 42.0
            and max(crude_target_inventory_days) <= 58.0
        ),
        "long_run_crude_supply_gap_is_bounded": (
            absolute_crude_supply_gap_p95 <= 2.5
            and max(
                abs(value) for value in per_seed_mean_crude_supply_gaps
            ) <= 0.35
        ),
        "crude_spare_capacity_central_90pct_is_credible": (
            crude_spare_capacity_p05 >= 0.5
            and crude_spare_capacity_p95 <= 12.0
        ),
        "regional_crude_geography_has_structural_dynamics": (
            min(south_asia_crude_run_share_changes) > 0.0
            and max(europe_crude_run_share_changes) < 0.0
            and min(brazil_guyana_production_share_changes) > 0.0
        ),
        "production_never_exceeds_capacity": all(
            output <= capacity + 1e-9
            for output, capacity in zip(production, production_capacity)
        ),
        "utilization_is_physical": (
            min(capacity_utilization) > 0.0
            and max(capacity_utilization) <= 100.0
        ),
        "spare_capacity_is_not_a_fixed_target": (
            max(spare_capacity) - min(spare_capacity) >= 2.0
        ),
        "every_seed_has_a_spare_capacity_cycle": (
            min(per_seed_spare_capacity_ranges) >= 2.0
        ),
        "annual_capacity_is_not_a_same_year_demand_servo": (
            abs(annual_capacity_demand_growth_correlation) <= 0.65
        ),
        "current_demand_does_not_directly_set_current_production": (
            float(demand_spike["realized_demand_mbd"])
            > float(baseline["realized_demand_mbd"])
            and float(demand_spike["production_mbd"])
            == float(baseline["production_mbd"])
        ),
        "inventory_pressure_changes_later_production": (
            float(demand_spike_next["production_mbd"])
            > float(baseline_next["production_mbd"])
        ),
        "inventory_is_positive": min(inventory_days) > 0.0,
        "inventory_central_90pct_is_credible": (
            inventory_p05 >= 45.0 and inventory_p95 <= 80.0
        ),
        "every_seed_inventory_central_90pct_is_credible": (
            min(per_seed_inventory_p05) >= 40.0
            and max(per_seed_inventory_p95) <= 85.0
        ),
        "inventory_tail_stays_inside_physical_storage_range": (
            max(inventory_days) <= 95.0
        ),
        "inventory_target_is_bounded": (
            min(target_inventory_days) >= 51.0
            and max(target_inventory_days) <= 70.0
        ),
        "long_run_regime_is_seed_stable": regimes_stable,
        "monthly_demand_is_not_a_fixed_annual_template": (
            statistics.median(adjacent_year_demand_shape_correlations) < 0.93
        ),
        "long_run_supply_gap_is_bounded": (
            absolute_supply_gap_p95 <= 2.5
            and max(abs(value) for value in per_seed_mean_supply_gaps) <= 0.35
        ),
        "spare_capacity_central_90pct_is_credible": (
            spare_capacity_p05 >= 1.5 and spare_capacity_p95 <= 12.0
        ),
        "cargo_is_positive": min(cargo) > 0.0,
        "cargo_is_not_a_fixed_consumption_share": (
            max(implied_cargo_shares) - min(implied_cargo_shares) >= 0.002
        ),
        "haul_is_bounded": min(haul) >= 4500.0 and max(haul) <= 8500.0,
        "reroute_15pct_raises_tonne_miles_15pct": abs(reroute_ratio - 1.15) <= 1e-7,
        "route_catalog_has_fourteen_explicit_plus_other_pool": (
            len(route_ids) == 15 and "other_routes" in route_ids
        ),
        "route_reference_matrix_metadata_is_complete": (
            reference_years == {2024}
            and calibrated_major_route_counts == {14}
            and active_pair_counts == {25}
            and residual_pair_counts == {11}
            and max(
                abs(value - 39.8) for value in reference_seaborne_cargo
            )
            <= 1e-8
        ),
        "initial_major_routes_are_centered_on_reference_matrix": (
            max(initial_major_route_reference_deviation_pct) <= 15.0
        ),
        "margin_scaled_route_reference_is_not_a_post_ipf_alias": (
            max(route_margin_scaled_diagnostic_difference_mbd) >= 0.01
        ),
        "route_cargo_is_conserved": maximum_route_cargo_residual_mbd <= 1e-6,
        "route_tonne_miles_are_conserved": (
            maximum_route_tonne_mile_residual <= 1e-6
        ),
        "regional_exports_and_imports_are_conserved": (
            maximum_regional_flow_residual_mbd <= 1e-6
        ),
        "regional_physical_allocation_is_conserved": max(
            maximum_regional_production_residual_mbd,
            maximum_regional_refinery_residual_mbd,
            maximum_regional_inventory_residual_mmbbl,
            maximum_regional_pipeline_residual_mbd,
            maximum_regional_net_balance_residual_mbd,
        ) <= 1e-6,
        "gulf_production_policy_is_bounded_and_conserved": (
            maximum_production_conservation_residual_mbd <= 1e-6
            and maximum_gulf_policy_target_mbd <= 2.2 + 1e-8
            and maximum_gulf_policy_monthly_adjustment_mbd <= 0.40 + 1e-8
        ),
        "gulf_exports_have_persistent_ordinary_volatility": (
            min(per_seed_gulf_export_monthly_change_sd) >= 0.08
            and max(per_seed_gulf_export_monthly_change_sd) <= 0.35
            and min(per_seed_gulf_export_ranges) >= 1.0
        ),
        "us_gulf_cycles_are_bounded_and_refinery_zero_sum": (
            maximum_us_gulf_refinery_cycle_residual_mbd <= 1e-8
            and maximum_refinery_conservation_residual_mbd <= 1e-6
            and maximum_us_gulf_production_target_mbd <= 1.2 + 1e-8
            and maximum_us_gulf_refinery_target_mbd <= 0.65 + 1e-8
            and maximum_us_gulf_production_monthly_adjustment_mbd <= 0.18 + 1e-8
            and maximum_us_gulf_refinery_monthly_adjustment_mbd <= 0.20 + 1e-8
        ),
        "us_gulf_net_balance_has_ordinary_cycle_volatility": (
            min(per_seed_us_gulf_export_monthly_change_sd) >= 0.075
            and max(per_seed_us_gulf_export_monthly_change_sd) <= 0.22
            and min(per_seed_us_gulf_export_ranges) >= 0.75
        ),
        "us_gulf_refinery_cycle_has_spring_autumn_maintenance": (
            us_gulf_maintenance_run_rate <= us_gulf_high_run_rate - 0.05
        ),
        "us_gulf_refinery_cycle_is_not_a_fixed_annual_template": (
            statistics.median(
                us_gulf_adjacent_year_refinery_profile_correlations
            ) < 0.90
        ),
        "other_export_is_not_a_mechanical_mirror": (
            max(abs(value) for value in per_seed_other_export_overlay_correlation)
            <= 0.85
            and max(
                abs(value)
                for value in per_seed_other_export_gulf_change_correlation
            )
            <= 0.65
        ),
        "other_export_has_independent_ordinary_volatility": (
            min(per_seed_other_export_monthly_change_sd) >= 0.04
            and max(per_seed_other_export_monthly_change_sd) <= 0.16
            and all(
                other_sd < gulf_sd
                for other_sd, gulf_sd in zip(
                    per_seed_other_export_monthly_change_sd,
                    per_seed_gulf_export_monthly_change_sd,
                )
            )
            and min(per_seed_other_export_ranges) >= 0.40
        ),
        "east_asia_remains_an_importer": east_asia_always_importer,
        "east_asia_refinery_cycle_is_bounded": (
            maximum_east_asia_refinery_target_mbd <= 0.85 + 1e-8
            and maximum_east_asia_refinery_monthly_adjustment_mbd <= 0.22 + 1e-8
        ),
        "east_asia_refinery_cycle_has_spring_autumn_maintenance": (
            east_asia_maintenance_run_rate <= east_asia_high_run_rate - 0.05
        ),
        "east_asia_refinery_cycle_is_not_a_fixed_annual_template": (
            statistics.median(
                east_asia_adjacent_year_refinery_profile_correlations
            ) < 0.90
        ),
        "east_asia_is_not_a_mechanical_production_mirror": (
            max(abs(value) for value in per_seed_east_asia_overlay_correlation)
            <= 0.85
        ),
        "east_asia_imports_have_independent_ordinary_volatility": (
            min(per_seed_east_asia_import_monthly_change_sd) >= 0.065
            and max(per_seed_east_asia_import_monthly_change_sd) <= 0.18
            and min(per_seed_east_asia_import_ranges) >= 0.40
        ),
        "south_asia_remains_an_importer": south_asia_always_importer,
        "south_asia_refinery_cycle_is_bounded": (
            maximum_south_asia_refinery_target_mbd <= 0.38 + 1e-8
            and maximum_south_asia_refinery_monthly_adjustment_mbd <= 0.12 + 1e-8
        ),
        "south_asia_refinery_cycle_has_pre_post_monsoon_maintenance": (
            south_asia_maintenance_run_rate <= south_asia_high_run_rate - 0.03
        ),
        "south_asia_refinery_cycle_is_not_a_fixed_annual_template": (
            statistics.median(
                south_asia_adjacent_year_refinery_profile_correlations
            ) < 0.90
        ),
        "south_asia_is_not_a_mechanical_production_mirror": (
            max(abs(value) for value in per_seed_south_asia_overlay_correlation)
            <= 0.85
        ),
        "south_asia_imports_have_independent_ordinary_volatility": (
            min(per_seed_south_asia_import_monthly_change_sd) >= 0.040
            and max(per_seed_south_asia_import_monthly_change_sd) <= 0.14
            and min(per_seed_south_asia_import_ranges) >= 0.25
        ),
        "europe_remains_an_importer": europe_always_importer,
        "europe_refinery_cycle_is_bounded": (
            maximum_europe_refinery_target_mbd <= 0.60 + 1e-8
            and maximum_europe_refinery_monthly_adjustment_mbd <= 0.16 + 1e-8
        ),
        "europe_refinery_cycle_has_spring_autumn_maintenance": (
            europe_maintenance_run_rate <= europe_high_run_rate - 0.04
        ),
        "europe_refinery_cycle_is_not_a_fixed_annual_template": (
            statistics.median(
                europe_adjacent_year_refinery_profile_correlations
            ) < 0.90
        ),
        "europe_is_not_a_mechanical_production_mirror": (
            max(abs(value) for value in per_seed_europe_overlay_correlation)
            <= 0.85
        ),
        "europe_imports_have_independent_ordinary_volatility": (
            min(per_seed_europe_import_monthly_change_sd) >= 0.052
            and max(per_seed_europe_import_monthly_change_sd) <= 0.16
            and min(per_seed_europe_import_ranges) >= 0.35
        ),
        "north_america_import_remains_an_importer": (
            north_america_import_always_importer
        ),
        "north_america_import_refinery_cycle_is_bounded": (
            maximum_north_america_import_refinery_target_mbd <= 0.50 + 1e-8
            and maximum_north_america_import_refinery_monthly_adjustment_mbd
            <= 0.14 + 1e-8
        ),
        "north_america_import_refinery_cycle_has_later_spring_autumn_maintenance": (
            north_america_import_maintenance_run_rate
            <= north_america_import_high_run_rate - 0.03
        ),
        "north_america_import_refinery_cycle_is_not_a_fixed_annual_template": (
            statistics.median(
                north_america_import_adjacent_year_refinery_profile_correlations
            ) < 0.90
        ),
        "north_america_import_is_not_a_mechanical_production_mirror": (
            max(
                abs(value)
                for value in per_seed_north_america_import_overlay_correlation
            )
            <= 0.85
        ),
        "north_america_import_has_independent_ordinary_volatility": (
            min(per_seed_north_america_import_monthly_change_sd) >= 0.050
            and max(per_seed_north_america_import_monthly_change_sd) <= 0.14
            and min(per_seed_north_america_import_ranges) >= 0.28
        ),
        "rest_of_world_remains_an_importer": rest_of_world_always_importer,
        "rest_of_world_refinery_cycle_is_bounded": (
            maximum_rest_of_world_refinery_target_mbd <= 0.42 + 5e-8
            and maximum_rest_of_world_refinery_monthly_adjustment_mbd
            <= 0.13 + 5e-8
        ),
        "rest_of_world_refinery_cycle_has_mixed_latitude_maintenance": (
            rest_of_world_maintenance_run_rate
            <= rest_of_world_high_run_rate - 0.02
        ),
        "rest_of_world_refinery_cycle_is_not_a_fixed_annual_template": (
            statistics.median(
                rest_of_world_adjacent_year_refinery_profile_correlations
            )
            < 0.90
        ),
        "rest_of_world_is_not_a_mechanical_production_mirror": (
            max(abs(value) for value in per_seed_rest_of_world_overlay_correlation)
            <= 0.85
        ),
        "rest_of_world_is_not_a_us_gulf_refinery_mirror": (
            max(abs(value) for value in per_seed_rest_of_world_us_gulf_correlation)
            <= 0.85
        ),
        "rest_of_world_imports_have_independent_ordinary_volatility": (
            min(per_seed_rest_of_world_import_monthly_change_sd) >= 0.060
            and max(per_seed_rest_of_world_import_monthly_change_sd) <= 0.18
            and min(per_seed_rest_of_world_import_ranges) >= 0.55
        ),
    }
    return {
        "ok": all(checks.values()),
        "profile": "stage_4_crude_physical_route_network",
        "seed_count": len(seed_list),
        "seeds": list(seed_list),
        "long_run_regime_counts": dict(sorted(regime_counts.items())),
        "years": years,
        "turn_count": len(demand),
        "checks": checks,
        "metrics": {
            "demand_mbd_mean": statistics.fmean(demand),
            "demand_mbd_min": min(demand),
            "demand_mbd_max": max(demand),
            "production_mbd_mean": statistics.fmean(production),
            "production_mbd_min": min(production),
            "production_mbd_max": max(production),
            "crude_refinery_runs_mbd_mean": statistics.fmean(crude_runs),
            "crude_refinery_runs_mbd_min": min(crude_runs),
            "crude_refinery_runs_mbd_max": max(crude_runs),
            "crude_production_mbd_mean": statistics.fmean(crude_production),
            "crude_production_mbd_min": min(crude_production),
            "crude_production_mbd_max": max(crude_production),
            "crude_production_minus_runs_mbd_mean": statistics.fmean(
                crude_production_minus_runs
            ),
            "absolute_crude_production_minus_runs_mbd_p95": (
                absolute_crude_supply_gap_p95
            ),
            "maximum_abs_per_seed_mean_crude_supply_gap_mbd": max(
                abs(value) for value in per_seed_mean_crude_supply_gaps
            ),
            "crude_production_capacity_mbd_mean": statistics.fmean(
                crude_production_capacity
            ),
            "crude_spare_capacity_mbd_p05": crude_spare_capacity_p05,
            "crude_spare_capacity_mbd_p50": crude_spare_capacity_p50,
            "crude_spare_capacity_mbd_p95": crude_spare_capacity_p95,
            "crude_inventory_days_min": min(crude_inventory_days),
            "crude_inventory_days_p05": crude_inventory_p05,
            "crude_inventory_days_p50": crude_inventory_p50,
            "crude_inventory_days_p95": crude_inventory_p95,
            "crude_inventory_days_max": max(crude_inventory_days),
            "south_asia_crude_run_share_change_median": statistics.median(
                south_asia_crude_run_share_changes
            ),
            "europe_crude_run_share_change_median": statistics.median(
                europe_crude_run_share_changes
            ),
            "brazil_guyana_production_share_change_median": statistics.median(
                brazil_guyana_production_share_changes
            ),
            "minimum_per_seed_crude_inventory_days_p05": min(
                per_seed_crude_inventory_p05
            ),
            "maximum_per_seed_crude_inventory_days_p95": max(
                per_seed_crude_inventory_p95
            ),
            "production_minus_demand_mbd_mean": statistics.fmean(
                production_minus_demand
            ),
            "production_minus_demand_mbd_min": min(production_minus_demand),
            "production_minus_demand_mbd_max": max(production_minus_demand),
            "absolute_production_minus_demand_mbd_p95": (
                absolute_supply_gap_p95
            ),
            "maximum_abs_per_seed_mean_supply_gap_mbd": max(
                abs(value) for value in per_seed_mean_supply_gaps
            ),
            "annual_capacity_demand_growth_target_correlation": (
                annual_capacity_demand_growth_correlation
            ),
            "production_capacity_mbd_mean": statistics.fmean(
                production_capacity
            ),
            "spare_capacity_mbd_mean": statistics.fmean(spare_capacity),
            "spare_capacity_mbd_min": min(spare_capacity),
            "spare_capacity_mbd_max": max(spare_capacity),
            "spare_capacity_mbd_p05": spare_capacity_p05,
            "spare_capacity_mbd_p50": spare_capacity_p50,
            "spare_capacity_mbd_p95": spare_capacity_p95,
            "minimum_per_seed_spare_capacity_range_mbd": min(
                per_seed_spare_capacity_ranges
            ),
            "capacity_utilization_pct_mean": statistics.fmean(
                capacity_utilization
            ),
            "capacity_utilization_pct_min": min(capacity_utilization),
            "capacity_utilization_pct_max": max(capacity_utilization),
            "inventory_days_mean": statistics.fmean(inventory_days),
            "inventory_days_min": min(inventory_days),
            "inventory_days_max": max(inventory_days),
            "inventory_days_p05": inventory_p05,
            "inventory_days_p50": inventory_p50,
            "inventory_days_p95": inventory_p95,
            "minimum_per_seed_inventory_days_p05": min(
                per_seed_inventory_p05
            ),
            "maximum_per_seed_inventory_days_p95": max(
                per_seed_inventory_p95
            ),
            "target_inventory_days_mean": statistics.fmean(
                target_inventory_days
            ),
            "target_inventory_days_min": min(target_inventory_days),
            "target_inventory_days_max": max(target_inventory_days),
            "ending_demand_mbd_median": statistics.median(ending_demand),
            "ending_demand_mbd_min": min(ending_demand),
            "ending_demand_mbd_max": max(ending_demand),
            "adjacent_year_demand_shape_correlation_median": statistics.median(
                adjacent_year_demand_shape_correlations
            ),
            "seaborne_cargo_mbd_mean": statistics.fmean(cargo),
            "seaborne_cargo_mbd_min": min(cargo),
            "seaborne_cargo_mbd_max": max(cargo),
            "implied_cargo_share_min": min(implied_cargo_shares),
            "implied_cargo_share_max": max(implied_cargo_shares),
            "average_haul_nm_min": min(haul),
            "average_haul_nm_mean": statistics.fmean(haul),
            "average_haul_nm_max": max(haul),
            "annualized_tonne_nautical_miles_billion_mean": statistics.fmean(
                tonne_miles
            ),
            "reference_seaborne_cargo_mbd": statistics.fmean(
                reference_seaborne_cargo
            ),
            "maximum_initial_major_route_reference_deviation_pct": max(
                initial_major_route_reference_deviation_pct
            ),
            "maximum_route_margin_scaled_diagnostic_difference_mbd": max(
                route_margin_scaled_diagnostic_difference_mbd
            ),
            "maximum_abs_mass_balance_residual_mmbbl": maximum_balance_residual,
            "maximum_abs_crude_mass_balance_residual_mmbbl": (
                maximum_crude_balance_residual
            ),
            "total_unmet_demand_mmbbl": total_unmet_demand,
            "total_unmet_crude_runs_mmbbl": total_unmet_crude_runs,
            "reroute_tonne_mile_ratio": reroute_ratio,
            "maximum_route_cargo_residual_mbd": maximum_route_cargo_residual_mbd,
            "maximum_route_tonne_mile_residual_billion": (
                maximum_route_tonne_mile_residual
            ),
            "maximum_regional_flow_residual_mbd": (
                maximum_regional_flow_residual_mbd
            ),
            "maximum_regional_physical_residual": max(
                maximum_regional_production_residual_mbd,
                maximum_regional_refinery_residual_mbd,
                maximum_regional_inventory_residual_mmbbl,
                maximum_regional_pipeline_residual_mbd,
                maximum_regional_net_balance_residual_mbd,
            ),
            "gulf_export_monthly_change_sd_mbd_min": min(
                per_seed_gulf_export_monthly_change_sd
            ),
            "gulf_export_monthly_change_sd_mbd_median": statistics.median(
                per_seed_gulf_export_monthly_change_sd
            ),
            "gulf_export_monthly_change_sd_mbd_max": max(
                per_seed_gulf_export_monthly_change_sd
            ),
            "gulf_export_range_mbd_min": min(per_seed_gulf_export_ranges),
            "gulf_export_range_mbd_median": statistics.median(
                per_seed_gulf_export_ranges
            ),
            "gulf_export_range_mbd_max": max(per_seed_gulf_export_ranges),
            "maximum_production_conservation_residual_mbd": (
                maximum_production_conservation_residual_mbd
            ),
            "maximum_gulf_policy_target_mbd": maximum_gulf_policy_target_mbd,
            "maximum_gulf_policy_monthly_adjustment_mbd": (
                maximum_gulf_policy_monthly_adjustment_mbd
            ),
            "us_gulf_export_monthly_change_sd_mbd_min": min(
                per_seed_us_gulf_export_monthly_change_sd
            ),
            "us_gulf_export_monthly_change_sd_mbd_median": statistics.median(
                per_seed_us_gulf_export_monthly_change_sd
            ),
            "us_gulf_export_monthly_change_sd_mbd_max": max(
                per_seed_us_gulf_export_monthly_change_sd
            ),
            "us_gulf_export_range_mbd_min": min(per_seed_us_gulf_export_ranges),
            "us_gulf_export_range_mbd_median": statistics.median(
                per_seed_us_gulf_export_ranges
            ),
            "us_gulf_export_range_mbd_max": max(per_seed_us_gulf_export_ranges),
            "us_gulf_maintenance_refinery_adjustment_mbd_mean": (
                us_gulf_maintenance_run_rate
            ),
            "us_gulf_high_run_refinery_adjustment_mbd_mean": (
                us_gulf_high_run_rate
            ),
            "us_gulf_adjacent_year_refinery_profile_correlation_median": (
                statistics.median(
                    us_gulf_adjacent_year_refinery_profile_correlations
                )
            ),
            "maximum_us_gulf_refinery_cycle_residual_mbd": (
                maximum_us_gulf_refinery_cycle_residual_mbd
            ),
            "maximum_us_gulf_production_target_mbd": (
                maximum_us_gulf_production_target_mbd
            ),
            "maximum_us_gulf_refinery_target_mbd": (
                maximum_us_gulf_refinery_target_mbd
            ),
            "maximum_us_gulf_production_monthly_adjustment_mbd": (
                maximum_us_gulf_production_monthly_adjustment_mbd
            ),
            "maximum_us_gulf_refinery_monthly_adjustment_mbd": (
                maximum_us_gulf_refinery_monthly_adjustment_mbd
            ),
            "maximum_refinery_conservation_residual_mbd": (
                maximum_refinery_conservation_residual_mbd
            ),
            "east_asia_import_monthly_change_sd_mbd_min": min(
                per_seed_east_asia_import_monthly_change_sd
            ),
            "east_asia_import_monthly_change_sd_mbd_median": statistics.median(
                per_seed_east_asia_import_monthly_change_sd
            ),
            "east_asia_import_monthly_change_sd_mbd_max": max(
                per_seed_east_asia_import_monthly_change_sd
            ),
            "east_asia_import_range_mbd_min": min(
                per_seed_east_asia_import_ranges
            ),
            "east_asia_maintenance_refinery_adjustment_mbd_mean": (
                east_asia_maintenance_run_rate
            ),
            "east_asia_high_run_refinery_adjustment_mbd_mean": (
                east_asia_high_run_rate
            ),
            "east_asia_adjacent_year_refinery_profile_correlation_median": (
                statistics.median(
                    east_asia_adjacent_year_refinery_profile_correlations
                )
            ),
            "east_asia_overlay_correlation_abs_max": max(
                abs(value) for value in per_seed_east_asia_overlay_correlation
            ),
            "maximum_east_asia_refinery_target_mbd": (
                maximum_east_asia_refinery_target_mbd
            ),
            "maximum_east_asia_refinery_monthly_adjustment_mbd": (
                maximum_east_asia_refinery_monthly_adjustment_mbd
            ),
            "south_asia_import_monthly_change_sd_mbd_min": min(
                per_seed_south_asia_import_monthly_change_sd
            ),
            "south_asia_import_monthly_change_sd_mbd_median": statistics.median(
                per_seed_south_asia_import_monthly_change_sd
            ),
            "south_asia_import_monthly_change_sd_mbd_max": max(
                per_seed_south_asia_import_monthly_change_sd
            ),
            "south_asia_import_range_mbd_min": min(
                per_seed_south_asia_import_ranges
            ),
            "south_asia_maintenance_refinery_adjustment_mbd_mean": (
                south_asia_maintenance_run_rate
            ),
            "south_asia_high_run_refinery_adjustment_mbd_mean": (
                south_asia_high_run_rate
            ),
            "south_asia_adjacent_year_refinery_profile_correlation_median": (
                statistics.median(
                    south_asia_adjacent_year_refinery_profile_correlations
                )
            ),
            "south_asia_overlay_correlation_abs_max": max(
                abs(value) for value in per_seed_south_asia_overlay_correlation
            ),
            "maximum_south_asia_refinery_target_mbd": (
                maximum_south_asia_refinery_target_mbd
            ),
            "maximum_south_asia_refinery_monthly_adjustment_mbd": (
                maximum_south_asia_refinery_monthly_adjustment_mbd
            ),
            "europe_import_monthly_change_sd_mbd_min": min(
                per_seed_europe_import_monthly_change_sd
            ),
            "europe_import_monthly_change_sd_mbd_median": statistics.median(
                per_seed_europe_import_monthly_change_sd
            ),
            "europe_import_monthly_change_sd_mbd_max": max(
                per_seed_europe_import_monthly_change_sd
            ),
            "europe_import_range_mbd_min": min(per_seed_europe_import_ranges),
            "europe_maintenance_refinery_adjustment_mbd_mean": (
                europe_maintenance_run_rate
            ),
            "europe_high_run_refinery_adjustment_mbd_mean": (
                europe_high_run_rate
            ),
            "europe_adjacent_year_refinery_profile_correlation_median": (
                statistics.median(
                    europe_adjacent_year_refinery_profile_correlations
                )
            ),
            "europe_overlay_correlation_abs_max": max(
                abs(value) for value in per_seed_europe_overlay_correlation
            ),
            "maximum_europe_refinery_target_mbd": (
                maximum_europe_refinery_target_mbd
            ),
            "maximum_europe_refinery_monthly_adjustment_mbd": (
                maximum_europe_refinery_monthly_adjustment_mbd
            ),
            "north_america_import_monthly_change_sd_mbd_min": min(
                per_seed_north_america_import_monthly_change_sd
            ),
            "north_america_import_monthly_change_sd_mbd_median": statistics.median(
                per_seed_north_america_import_monthly_change_sd
            ),
            "north_america_import_monthly_change_sd_mbd_max": max(
                per_seed_north_america_import_monthly_change_sd
            ),
            "north_america_import_range_mbd_min": min(
                per_seed_north_america_import_ranges
            ),
            "north_america_import_maintenance_refinery_adjustment_mbd_mean": (
                north_america_import_maintenance_run_rate
            ),
            "north_america_import_high_run_refinery_adjustment_mbd_mean": (
                north_america_import_high_run_rate
            ),
            "north_america_import_adjacent_year_refinery_profile_correlation_median": (
                statistics.median(
                    north_america_import_adjacent_year_refinery_profile_correlations
                )
            ),
            "north_america_import_overlay_correlation_abs_max": max(
                abs(value)
                for value in per_seed_north_america_import_overlay_correlation
            ),
            "maximum_north_america_import_refinery_target_mbd": (
                maximum_north_america_import_refinery_target_mbd
            ),
            "maximum_north_america_import_refinery_monthly_adjustment_mbd": (
                maximum_north_america_import_refinery_monthly_adjustment_mbd
            ),
            "rest_of_world_import_monthly_change_sd_mbd_min": min(
                per_seed_rest_of_world_import_monthly_change_sd
            ),
            "rest_of_world_import_monthly_change_sd_mbd_median": statistics.median(
                per_seed_rest_of_world_import_monthly_change_sd
            ),
            "rest_of_world_import_monthly_change_sd_mbd_max": max(
                per_seed_rest_of_world_import_monthly_change_sd
            ),
            "rest_of_world_import_range_mbd_min": min(
                per_seed_rest_of_world_import_ranges
            ),
            "rest_of_world_maintenance_refinery_adjustment_mbd_mean": (
                rest_of_world_maintenance_run_rate
            ),
            "rest_of_world_high_run_refinery_adjustment_mbd_mean": (
                rest_of_world_high_run_rate
            ),
            "rest_of_world_adjacent_year_refinery_profile_correlation_median": (
                statistics.median(
                    rest_of_world_adjacent_year_refinery_profile_correlations
                )
            ),
            "rest_of_world_overlay_correlation_abs_max": max(
                abs(value) for value in per_seed_rest_of_world_overlay_correlation
            ),
            "rest_of_world_us_gulf_correlation_abs_max": max(
                abs(value) for value in per_seed_rest_of_world_us_gulf_correlation
            ),
            "maximum_rest_of_world_refinery_target_mbd": (
                maximum_rest_of_world_refinery_target_mbd
            ),
            "maximum_rest_of_world_refinery_monthly_adjustment_mbd": (
                maximum_rest_of_world_refinery_monthly_adjustment_mbd
            ),
            "other_export_monthly_change_sd_mbd_min": min(
                per_seed_other_export_monthly_change_sd
            ),
            "other_export_monthly_change_sd_mbd_median": statistics.median(
                per_seed_other_export_monthly_change_sd
            ),
            "other_export_monthly_change_sd_mbd_max": max(
                per_seed_other_export_monthly_change_sd
            ),
            "other_export_range_mbd_min": min(per_seed_other_export_ranges),
            "other_export_overlay_correlation_abs_max": max(
                abs(value) for value in per_seed_other_export_overlay_correlation
            ),
            "other_export_gulf_change_correlation_abs_max": max(
                abs(value)
                for value in per_seed_other_export_gulf_change_correlation
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", default="0,1,2,3,4,5,6,7")
    parser.add_argument("--years", type=int, default=60)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    seeds = [int(value.strip()) for value in args.seeds.split(",") if value.strip()]
    result = audit_oil_shipping_demand(seeds, years=args.years)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
