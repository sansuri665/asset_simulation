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
    annual_demand_growth_targets: list[float] = []
    annual_capacity_growth_targets: list[float] = []
    per_seed_spare_capacity_ranges: list[float] = []
    per_seed_inventory_p05: list[float] = []
    per_seed_inventory_p95: list[float] = []
    per_seed_mean_supply_gaps: list[float] = []
    per_seed_gulf_export_monthly_change_sd: list[float] = []
    per_seed_gulf_export_ranges: list[float] = []
    per_seed_us_gulf_export_monthly_change_sd: list[float] = []
    per_seed_us_gulf_export_ranges: list[float] = []
    us_gulf_refinery_adjustments_by_month: dict[int, list[float]] = {
        month: [] for month in range(1, 13)
    }
    us_gulf_adjacent_year_refinery_profile_correlations: list[float] = []
    ending_demand: list[float] = []
    cargo: list[float] = []
    implied_cargo_shares: list[float] = []
    haul: list[float] = []
    tonne_miles: list[float] = []
    maximum_balance_residual = 0.0
    total_unmet_demand = 0.0
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
    maximum_production_policy_residual_mbd = 0.0
    maximum_gulf_policy_target_mbd = 0.0
    maximum_gulf_policy_monthly_adjustment_mbd = 0.0
    maximum_us_gulf_production_cycle_residual_mbd = 0.0
    maximum_us_gulf_refinery_cycle_residual_mbd = 0.0
    maximum_us_gulf_production_target_mbd = 0.0
    maximum_us_gulf_refinery_target_mbd = 0.0
    maximum_us_gulf_production_monthly_adjustment_mbd = 0.0
    maximum_us_gulf_refinery_monthly_adjustment_mbd = 0.0
    route_ids: set[str] = set()

    for seed in seed_list:
        world = run_oil_shipping_world(run_global_macro(seed, years))
        seed_inventory_days: list[float] = []
        seed_spare_capacity: list[float] = []
        seed_supply_gaps: list[float] = []
        seed_gulf_exports: list[float] = []
        seed_us_gulf_exports: list[float] = []
        seed_us_gulf_refinery_adjustments_by_year: dict[int, list[float]] = {}
        previous_gulf_policy_adjustment: float | None = None
        previous_us_gulf_production_adjustment: float | None = None
        previous_us_gulf_refinery_adjustment: float | None = None
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
                / float(turn["realized_demand_mbd"])
            )
            haul.append(float(turn["average_haul_nm"]))
            tonne_miles.append(float(turn["annualized_tonne_nautical_miles_billion"]))
            routes = turn["routes"]
            regions_by_id = {
                str(region["region_id"]): region
                for region in turn["regional_balances"]
            }
            gulf = regions_by_id["gulf"]
            us_gulf = regions_by_id["us_gulf"]
            seed_gulf_exports.append(float(gulf["net_seaborne_balance_mbd"]))
            seed_us_gulf_exports.append(
                float(us_gulf["net_seaborne_balance_mbd"])
            )
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
            maximum_production_policy_residual_mbd = max(
                maximum_production_policy_residual_mbd,
                abs(
                    sum(
                        float(region["production_policy_adjustment_mbd"])
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
            us_gulf_refinery_adjustments_by_month[int(turn["month"])].append(
                us_gulf_refinery_adjustment
            )
            seed_us_gulf_refinery_adjustments_by_year.setdefault(
                int(turn["year"]),
                [],
            ).append(us_gulf_refinery_adjustment)
            maximum_us_gulf_production_target_mbd = max(
                maximum_us_gulf_production_target_mbd,
                abs(float(us_gulf["production_cycle_target_mbd"])),
            )
            maximum_us_gulf_refinery_target_mbd = max(
                maximum_us_gulf_refinery_target_mbd,
                abs(float(us_gulf["refinery_cycle_target_mbd"])),
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
            previous_us_gulf_production_adjustment = us_gulf_production_adjustment
            previous_us_gulf_refinery_adjustment = us_gulf_refinery_adjustment
            maximum_us_gulf_production_cycle_residual_mbd = max(
                maximum_us_gulf_production_cycle_residual_mbd,
                abs(
                    sum(
                        float(region["production_cycle_adjustment_mbd"])
                        for region in regions_by_id.values()
                    )
                ),
            )
            maximum_us_gulf_refinery_cycle_residual_mbd = max(
                maximum_us_gulf_refinery_cycle_residual_mbd,
                abs(
                    sum(
                        float(region["refinery_cycle_adjustment_mbd"])
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
            maximum_regional_production_residual_mbd = max(
                maximum_regional_production_residual_mbd,
                abs(float(turn["regional_production_residual_mbd"])),
            )
            maximum_regional_refinery_residual_mbd = max(
                maximum_regional_refinery_residual_mbd,
                abs(float(turn["regional_refinery_residual_mbd"])),
            )
            maximum_regional_inventory_residual_mmbbl = max(
                maximum_regional_inventory_residual_mmbbl,
                abs(float(turn["regional_inventory_residual_mmbbl"])),
            )
            maximum_regional_pipeline_residual_mbd = max(
                maximum_regional_pipeline_residual_mbd,
                abs(float(turn["regional_pipeline_residual_mbd"])),
            )
            maximum_regional_net_balance_residual_mbd = max(
                maximum_regional_net_balance_residual_mbd,
                abs(float(turn["regional_net_balance_residual_mbd"])),
            )
            total_unmet_demand += float(turn["unmet_demand_mmbbl"])
        per_seed_spare_capacity_ranges.append(
            max(seed_spare_capacity) - min(seed_spare_capacity)
        )
        per_seed_inventory_p05.append(_percentile(seed_inventory_days, 0.05))
        per_seed_inventory_p95.append(_percentile(seed_inventory_days, 0.95))
        per_seed_mean_supply_gaps.append(statistics.fmean(seed_supply_gaps))
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
        us_gulf_years = sorted(seed_us_gulf_refinery_adjustments_by_year)
        us_gulf_adjacent_year_refinery_profile_correlations.extend(
            _correlation(
                seed_us_gulf_refinery_adjustments_by_year[previous],
                seed_us_gulf_refinery_adjustments_by_year[current],
            )
            for previous, current in zip(us_gulf_years, us_gulf_years[1:])
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
    checks = {
        "deterministic_seed_hashes_unique": len(set(result_hashes)) == len(seed_list),
        "mass_balance_exact": maximum_balance_residual <= 1e-6,
        "ordinary_world_has_no_unmet_demand": total_unmet_demand <= 1e-6,
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
        "route_catalog_has_nine_explicit_plus_other_pool": (
            len(route_ids) == 10 and "other_routes" in route_ids
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
        "gulf_production_policy_is_zero_sum_and_bounded": (
            maximum_production_policy_residual_mbd <= 1e-8
            and maximum_gulf_policy_target_mbd <= 2.2 + 1e-8
            and maximum_gulf_policy_monthly_adjustment_mbd <= 0.40 + 1e-8
        ),
        "gulf_exports_have_persistent_ordinary_volatility": (
            min(per_seed_gulf_export_monthly_change_sd) >= 0.08
            and max(per_seed_gulf_export_monthly_change_sd) <= 0.35
            and min(per_seed_gulf_export_ranges) >= 1.0
        ),
        "us_gulf_cycles_are_zero_sum_and_bounded": (
            maximum_us_gulf_production_cycle_residual_mbd <= 1e-8
            and maximum_us_gulf_refinery_cycle_residual_mbd <= 1e-8
            and maximum_us_gulf_production_target_mbd <= 1.2 + 1e-8
            and maximum_us_gulf_refinery_target_mbd <= 0.65 + 1e-8
            and maximum_us_gulf_production_monthly_adjustment_mbd <= 0.18 + 1e-8
            and maximum_us_gulf_refinery_monthly_adjustment_mbd <= 0.20 + 1e-8
        ),
        "us_gulf_net_balance_has_ordinary_cycle_volatility": (
            min(per_seed_us_gulf_export_monthly_change_sd) >= 0.075
            and max(per_seed_us_gulf_export_monthly_change_sd) <= 0.22
            and min(per_seed_us_gulf_export_ranges) >= 0.80
        ),
        "us_gulf_refinery_cycle_has_spring_autumn_maintenance": (
            us_gulf_maintenance_run_rate <= us_gulf_high_run_rate - 0.05
        ),
        "us_gulf_refinery_cycle_is_not_a_fixed_annual_template": (
            statistics.median(
                us_gulf_adjacent_year_refinery_profile_correlations
            ) < 0.90
        ),
    }
    return {
        "ok": all(checks.values()),
        "profile": "stage_3_regional_physical_route_network",
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
            "maximum_abs_mass_balance_residual_mmbbl": maximum_balance_residual,
            "total_unmet_demand_mmbbl": total_unmet_demand,
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
            "maximum_production_policy_residual_mbd": (
                maximum_production_policy_residual_mbd
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
            "maximum_us_gulf_production_cycle_residual_mbd": (
                maximum_us_gulf_production_cycle_residual_mbd
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
