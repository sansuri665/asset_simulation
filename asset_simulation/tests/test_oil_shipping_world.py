from __future__ import annotations

import math
import statistics
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_physical_world import (
    annual_growth_targets,
    initial_physical_state,
)
from asset_simulation.model.oil_shipping_world import (
    OIL_SHIPPING_DEMAND_MODEL_VERSION,
    build_oil_shipping_payload,
    run_oil_shipping_world,
)
from asset_simulation.model.registry import load_registered_assets


class OilShippingWorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.short_macro = run_global_macro(42, 5)
        cls.short_world = run_oil_shipping_world(cls.short_macro)

    def test_identity_determinism_and_prefix_stability(self) -> None:
        repeat = run_oil_shipping_world(run_global_macro(42, 5))
        other = run_oil_shipping_world(run_global_macro(7, 5))
        longer = run_oil_shipping_world(run_global_macro(42, 60))

        self.assertEqual(
            OIL_SHIPPING_DEMAND_MODEL_VERSION,
            self.short_world.identity["model_version"],
        )
        self.assertEqual(
            self.short_world.identity["identity_hash"],
            repeat.identity["identity_hash"],
        )
        self.assertNotEqual(
            self.short_world.identity["result_hash"],
            other.identity["result_hash"],
        )
        self.assertEqual(
            self.short_world.turns,
            longer.turns[: len(self.short_world.turns)],
        )
        self.assertFalse(self.short_world.identity["freight_rate_present"])
        self.assertFalse(self.short_world.identity["player_price_feedback"])
        self.assertEqual(12, self.short_world.identity["turns_per_year"])
        self.assertEqual(6 * 12, len(self.short_world.turns))
        self.assertTrue(all("half" not in turn for turn in self.short_world.turns))

    def test_long_run_demand_regimes_are_stable_and_economically_distinct(self) -> None:
        worlds = {
            seed: run_oil_shipping_world(run_global_macro(seed, 60))
            for seed in (0, 1, 5)
        }
        self.assertEqual("continuation", worlds[0].identity["long_run_demand_regime"])
        self.assertEqual(
            "transition_plateau",
            worlds[1].identity["long_run_demand_regime"],
        )
        self.assertEqual(
            "accelerated_transition",
            worlds[5].identity["long_run_demand_regime"],
        )
        for world in worlds.values():
            self.assertEqual(
                {world.identity["long_run_demand_regime"]},
                {turn["long_run_demand_regime"] for turn in world.turns},
            )
        ending_demand = {
            seed: float(world.annual[-1]["average_demand_mbd"])
            for seed, world in worlds.items()
        }
        self.assertGreater(ending_demand[0], ending_demand[1])
        self.assertGreater(ending_demand[1], ending_demand[5])

    def test_inventory_target_moves_slowly_and_remains_bounded(self) -> None:
        targets = [
            float(turn["target_inventory_days"])
            for turn in self.short_world.turns
        ]
        self.assertGreater(max(targets) - min(targets), 1.0)
        self.assertGreaterEqual(min(targets), 51.0)
        self.assertLessEqual(max(targets), 70.0)
        self.assertTrue(
            all(
                abs(current - previous) < 2.5
                for previous, current in zip(targets, targets[1:])
            )
        )

    def test_monthly_demand_shape_is_not_a_fixed_annual_wave(self) -> None:
        by_year: dict[int, list[float]] = {}
        for turn in self.short_world.turns:
            by_year.setdefault(int(turn["year"]), []).append(
                float(turn["realized_demand_mbd"])
            )

        def correlation(left: list[float], right: list[float]) -> float:
            left_mean = statistics.fmean(left)
            right_mean = statistics.fmean(right)
            left_centered = [value - left_mean for value in left]
            right_centered = [value - right_mean for value in right]
            denominator = math.sqrt(
                sum(value * value for value in left_centered)
                * sum(value * value for value in right_centered)
            )
            return sum(
                left_value * right_value
                for left_value, right_value in zip(
                    left_centered,
                    right_centered,
                )
            ) / denominator

        years = sorted(by_year)
        adjacent_correlations = [
            correlation(by_year[previous], by_year[current])
            for previous, current in zip(years, years[1:])
        ]
        self.assertLess(statistics.median(adjacent_correlations), 0.93)
        january_turns = [
            turn for turn in self.short_world.turns if int(turn["month"]) == 1
        ]
        self.assertGreater(
            len(
                {
                    round(float(turn["demand_seasonal_amplitude_pct"]), 4)
                    for turn in january_turns
                }
            ),
            2,
        )

    def test_physical_balance_is_exact_and_calendar_aware(self) -> None:
        for turn in self.short_world.turns:
            expected = (
                float(turn["opening_inventory_mmbbl"])
                + float(turn["production_mbd"]) * int(turn["days"])
                - float(turn["realized_demand_mbd"]) * int(turn["days"])
            )
            self.assertAlmostEqual(
                float(turn["closing_inventory_mmbbl"]),
                expected,
                places=6,
            )
            self.assertAlmostEqual(
                float(turn["mass_balance_residual_mmbbl"]),
                0.0,
                places=8,
            )
            self.assertGreaterEqual(float(turn["closing_inventory_mmbbl"]), 0.0)
            self.assertGreaterEqual(float(turn["spare_capacity_mbd"]), 0.0)
            self.assertGreater(float(turn["capacity_utilization_pct"]), 0.0)
            self.assertLessEqual(float(turn["capacity_utilization_pct"]), 100.0)
            self.assertLessEqual(abs(float(turn["production_change_mbd"])), 0.60)

        annual_days = {int(row["year"]): int(row["days"]) for row in self.short_world.annual}
        self.assertEqual(365, annual_days[2025])
        self.assertEqual(365, annual_days[2027])
        self.assertEqual(366, annual_days[2028])
        self.assertEqual(31, int(self.short_world.turns[0]["days"]))
        self.assertEqual(28, int(self.short_world.turns[1]["days"]))
        self.assertEqual(29, int(self.short_world.turns[37]["days"]))

    def test_supply_is_not_a_contemporaneous_demand_servo(self) -> None:
        demand_spike = run_oil_shipping_world(
            self.short_macro,
            scenario_by_turn={0: {"demand_rate_impulse_pct": 5.0}},
        )
        baseline_first = self.short_world.turns[0]
        spike_first = demand_spike.turns[0]

        self.assertGreater(
            float(spike_first["realized_demand_mbd"]),
            float(baseline_first["realized_demand_mbd"]),
        )
        self.assertEqual(
            float(spike_first["production_mbd"]),
            float(baseline_first["production_mbd"]),
        )
        self.assertLess(
            float(spike_first["closing_inventory_mmbbl"]),
            float(baseline_first["closing_inventory_mmbbl"]),
        )

        baseline_next = self.short_world.turns[1]
        spike_next = demand_spike.turns[1]
        self.assertGreater(
            float(spike_next["inventory_supply_pressure_pct"]),
            float(baseline_next["inventory_supply_pressure_pct"]),
        )
        self.assertGreater(
            float(spike_next["production_mbd"]),
            float(baseline_next["production_mbd"]),
        )

    def test_annual_capacity_does_not_read_current_demand_growth_target(self) -> None:
        config = load_registered_assets()["oil_shipping_demand_config"]
        base_row = dict(self.short_macro.rows[0])
        high_growth_row = {**base_row, "realized_growth_pct": 7.0}
        state = initial_physical_state(config)

        baseline = annual_growth_targets(
            [base_row],
            seed=42,
            year_index=3,
            simulation_year=2028,
            config=config,
            physical_state=state,
        )
        high_growth = annual_growth_targets(
            [high_growth_row],
            seed=42,
            year_index=3,
            simulation_year=2028,
            config=config,
            physical_state=state,
        )

        self.assertNotEqual(baseline[0], high_growth[0])
        self.assertEqual(baseline[1], high_growth[1])

    def test_spare_capacity_is_a_cycle_not_a_fixed_target(self) -> None:
        spare = [
            float(turn["spare_capacity_mbd"])
            for turn in self.short_world.turns
        ]
        utilization_cycle = [
            float(turn["utilization_cycle_deviation_pct"])
            for turn in self.short_world.turns
        ]
        self.assertGreater(max(spare) - min(spare), 2.0)
        self.assertGreater(max(utilization_cycle) - min(utilization_cycle), 0.5)
        self.assertTrue(
            all(
                87.0 <= float(turn["target_utilization_pct"]) <= 99.4
                for turn in self.short_world.turns
            )
        )

    def test_tonne_mile_identity_and_physical_units(self) -> None:
        barrels_per_tonne = 7.3
        for turn in self.short_world.turns:
            expected_tonnes = (
                float(turn["seaborne_cargo_mbd"])
                * int(turn["days"])
                / barrels_per_tonne
            )
            expected_tonne_miles = (
                expected_tonnes * float(turn["average_haul_nm"]) / 1000.0
            )
            self.assertAlmostEqual(
                float(turn["cargo_million_tonnes"]),
                expected_tonnes,
                places=6,
            )
            self.assertAlmostEqual(
                float(turn["tonne_nautical_miles_billion"]),
                expected_tonne_miles,
                places=5,
            )
            self.assertNotIn("freight_rate", turn)
            self.assertNotIn("tce", turn)

    def test_regional_balance_and_route_network_are_conservative(self) -> None:
        self.assertEqual(
            "modeled_conventional_crude_tanker_market",
            self.short_world.identity["shipping_market_scope"],
        )
        self.assertEqual(9, self.short_world.identity["explicit_route_count"])
        self.assertEqual(10, len(self.short_world.identity["route_ids"]))
        self.assertEqual(
            "regional_physical_surplus_and_deficit",
            self.short_world.identity["cargo_generation"],
        )

        for turn in self.short_world.turns[::13]:
            routes = turn["routes"]
            self.assertEqual(10, len(routes))
            self.assertEqual(9, sum(not route["is_other_pool"] for route in routes))
            self.assertEqual(1, sum(route["is_other_pool"] for route in routes))
            self.assertAlmostEqual(
                1.0,
                sum(float(route["market_share"]) for route in routes),
                places=6,
            )
            self.assertAlmostEqual(
                float(turn["seaborne_cargo_mbd"]),
                sum(float(route["cargo_mbd"]) for route in routes),
                places=6,
            )
            regions = turn["regional_balances"]
            self.assertAlmostEqual(
                float(turn["production_mbd"]),
                sum(float(region["production_mbd"]) for region in regions),
                places=6,
            )
            self.assertAlmostEqual(
                float(turn["realized_demand_mbd"]),
                sum(float(region["refinery_demand_mbd"]) for region in regions),
                places=6,
            )
            self.assertAlmostEqual(
                float(turn["inventory_change_mmbbl"]),
                sum(float(region["inventory_change_mmbbl"]) for region in regions),
                places=6,
            )
            self.assertAlmostEqual(
                0.0,
                sum(float(region["pipeline_net_exports_mbd"]) for region in regions),
                places=6,
            )
            self.assertAlmostEqual(
                0.0,
                sum(float(region["net_seaborne_balance_mbd"]) for region in regions),
                places=6,
            )
            self.assertAlmostEqual(
                float(turn["seaborne_cargo_mbd"]),
                sum(
                    max(0.0, float(region["net_seaborne_balance_mbd"]))
                    for region in regions
                ),
                places=6,
            )
            self.assertAlmostEqual(
                float(turn["tonne_nautical_miles_billion"]),
                sum(
                    float(route["tonne_nautical_miles_billion"])
                    for route in routes
                ),
                places=6,
            )
            self.assertAlmostEqual(
                float(turn["seaborne_cargo_mbd"]),
                sum(
                    float(region["cargo_mbd"])
                    for region in turn["regional_exports"]
                ),
                places=6,
            )
            self.assertAlmostEqual(
                float(turn["seaborne_cargo_mbd"]),
                sum(
                    float(region["cargo_mbd"])
                    for region in turn["regional_imports"]
                ),
                places=6,
            )

    def test_gulf_production_policy_is_sticky_bounded_and_zero_sum(self) -> None:
        gulf_exports: list[float] = []
        gulf_adjustments: list[float] = []
        for turn in self.short_world.turns:
            regions = {
                str(region["region_id"]): region
                for region in turn["regional_balances"]
            }
            gulf = regions["gulf"]
            balancing = regions["other_export_regions"]
            gulf_exports.append(float(gulf["net_seaborne_balance_mbd"]))
            gulf_adjustments.append(
                float(gulf["production_policy_adjustment_mbd"])
            )
            self.assertAlmostEqual(
                0.0,
                sum(
                    float(region["production_policy_adjustment_mbd"])
                    for region in regions.values()
                ),
                places=8,
            )
            self.assertAlmostEqual(
                -float(gulf["production_policy_adjustment_mbd"]),
                float(balancing["production_policy_adjustment_mbd"]),
                places=8,
            )
            self.assertLessEqual(
                abs(float(gulf["production_policy_target_mbd"])),
                2.2,
            )
            self.assertEqual(
                int(turn["turn_index"]) % 3 == 0,
                bool(gulf["production_policy_decision_month"]),
            )

        monthly_policy_changes = [
            current - previous
            for previous, current in zip(
                gulf_adjustments,
                gulf_adjustments[1:],
            )
        ]
        monthly_export_changes = [
            current - previous
            for previous, current in zip(gulf_exports, gulf_exports[1:])
        ]
        self.assertLessEqual(
            max(abs(value) for value in monthly_policy_changes),
            0.40 + 1e-8,
        )
        self.assertGreater(statistics.stdev(monthly_export_changes), 0.08)
        self.assertGreater(max(gulf_exports) - min(gulf_exports), 1.0)

    def test_us_gulf_net_balance_has_shale_and_refinery_cycles(self) -> None:
        exports: list[float] = []
        production_adjustments: list[float] = []
        refinery_adjustments: list[float] = []
        refinery_adjustments_by_month: dict[int, list[float]] = {
            month: [] for month in range(1, 13)
        }
        refinery_adjustments_by_year: dict[int, list[float]] = {}
        for turn in self.short_world.turns:
            regions = {
                str(region["region_id"]): region
                for region in turn["regional_balances"]
            }
            us_gulf = regions["us_gulf"]
            exports.append(float(us_gulf["net_seaborne_balance_mbd"]))
            production_adjustments.append(
                float(us_gulf["production_cycle_adjustment_mbd"])
            )
            refinery_adjustments.append(
                float(us_gulf["refinery_cycle_adjustment_mbd"])
            )
            refinery_adjustments_by_month[int(turn["month"])].append(
                refinery_adjustments[-1]
            )
            refinery_adjustments_by_year.setdefault(int(turn["year"]), []).append(
                refinery_adjustments[-1]
            )
            self.assertAlmostEqual(
                0.0,
                sum(
                    float(region["production_cycle_adjustment_mbd"])
                    for region in regions.values()
                ),
                places=8,
            )
            self.assertAlmostEqual(
                0.0,
                sum(
                    float(region["refinery_cycle_adjustment_mbd"])
                    for region in regions.values()
                ),
                places=8,
            )
            self.assertLessEqual(
                abs(float(us_gulf["production_cycle_target_mbd"])),
                1.2,
            )
            self.assertLessEqual(
                abs(float(us_gulf["refinery_cycle_target_mbd"])),
                0.65,
            )
            self.assertEqual(
                int(turn["month"]) == 1,
                bool(us_gulf["production_cycle_decision_month"]),
            )

        production_changes = [
            current - previous
            for previous, current in zip(
                production_adjustments,
                production_adjustments[1:],
            )
        ]
        refinery_changes = [
            current - previous
            for previous, current in zip(
                refinery_adjustments,
                refinery_adjustments[1:],
            )
        ]
        export_changes = [
            current - previous
            for previous, current in zip(exports, exports[1:])
        ]
        self.assertLessEqual(max(map(abs, production_changes)), 0.18 + 1e-8)
        self.assertLessEqual(max(map(abs, refinery_changes)), 0.20 + 1e-8)
        self.assertGreater(statistics.stdev(export_changes), 0.075)
        self.assertLess(statistics.stdev(export_changes), 0.22)
        self.assertGreater(max(exports) - min(exports), 0.80)
        maintenance_run_rate = statistics.fmean(
            statistics.fmean(refinery_adjustments_by_month[month])
            for month in (1, 2, 3, 9, 10, 11)
        )
        high_run_rate = statistics.fmean(
            statistics.fmean(refinery_adjustments_by_month[month])
            for month in (5, 6, 7, 8, 12)
        )
        self.assertLess(maintenance_run_rate, high_run_rate - 0.05)
        years = sorted(refinery_adjustments_by_year)
        adjacent_profile_correlations = [
            statistics.correlation(
                refinery_adjustments_by_year[previous],
                refinery_adjustments_by_year[current],
            )
            for previous, current in zip(years, years[1:])
        ]
        self.assertLess(statistics.median(adjacent_profile_correlations), 0.90)

    def test_route_mix_is_seeded_sticky_and_prefix_stable(self) -> None:
        other_world = run_oil_shipping_world(run_global_macro(7, 5))
        route_id = "us_gulf_east_asia"

        def shares(world: object) -> list[float]:
            return [
                next(
                    float(route["market_share"])
                    for route in turn["routes"]
                    if route["route_id"] == route_id
                )
                for turn in world.turns
            ]

        baseline = shares(self.short_world)
        alternative = shares(other_world)
        self.assertNotEqual(baseline, alternative)
        self.assertGreater(max(baseline) - min(baseline), 0.002)
        self.assertLess(
            max(abs(current - previous) for previous, current in zip(baseline, baseline[1:])),
            0.01,
        )

    def test_cutoff_payload_never_publishes_future_turns(self) -> None:
        payload = build_oil_shipping_payload(
            self.short_world,
            as_of_year=2030,
            as_of_month=1,
        )
        self.assertEqual("2030-01", payload["current"]["label"])
        self.assertEqual("2030-01", payload["history"][-1]["label"])
        self.assertTrue(
            all(
                (row["year"], row["month"]) <= (2030, 1)
                for row in payload["history"]
            )
        )
        self.assertEqual(5, len(payload["completedAnnual"]))
        self.assertEqual(2029, payload["current"]["macro_information_year"])
        self.assertTrue(
            all(
                int(row["macro_information_year"]) <= int(row["year"])
                for row in payload["history"]
            )
        )

    def test_test_only_scenarios_reallocate_physical_margins_and_distance(self) -> None:
        baseline = self.short_world.turns[0]
        rerouted = run_oil_shipping_world(
            self.short_macro,
            scenario_by_turn={0: {"average_haul_impulse_pct": 15.0}},
        ).turns[0]
        regional_supply_shift = run_oil_shipping_world(
            self.short_macro,
            scenario_by_turn={
                0: {"regional_production_impulse_mbd": {"gulf": -2.0}}
            },
        ).turns[0]
        refinery_shift = run_oil_shipping_world(
            self.short_macro,
            scenario_by_turn={
                0: {"regional_refinery_impulse_mbd": {"east_asia": -2.0}}
            },
        ).turns[0]
        inventory_shift = run_oil_shipping_world(
            self.short_macro,
            scenario_by_turn={
                0: {"regional_inventory_impulse_mmbbl": {"europe": 20.0}}
            },
        ).turns[0]
        route_reroute = run_oil_shipping_world(
            self.short_macro,
            scenario_by_turn={
                0: {"route_haul_impulse_pct": {"gulf_europe": 25.0}}
            },
        ).turns[0]

        self.assertAlmostEqual(
            float(rerouted["average_haul_nm"])
            / float(baseline["average_haul_nm"]),
            1.15,
            places=7,
        )
        self.assertAlmostEqual(
            float(rerouted["tonne_nautical_miles_billion"])
            / float(baseline["tonne_nautical_miles_billion"]),
            1.15,
            places=7,
        )
        for base_route, rerouted_route in zip(
            baseline["routes"],
            rerouted["routes"],
        ):
            self.assertEqual(base_route["route_id"], rerouted_route["route_id"])
            self.assertAlmostEqual(
                float(rerouted_route["effective_haul_nm"])
                / float(base_route["effective_haul_nm"]),
                1.15,
                places=7,
            )
        def regions(turn: dict[str, object]) -> dict[str, dict[str, object]]:
            return {
                str(region["region_id"]): region
                for region in turn["regional_balances"]
            }

        base_regions = regions(baseline)
        supply_regions = regions(regional_supply_shift)
        refinery_regions = regions(refinery_shift)
        inventory_regions = regions(inventory_shift)
        self.assertAlmostEqual(
            float(supply_regions["gulf"]["net_seaborne_balance_mbd"])
            - float(base_regions["gulf"]["net_seaborne_balance_mbd"]),
            -2.0,
            places=6,
        )
        self.assertAlmostEqual(
            float(refinery_regions["east_asia"]["net_seaborne_balance_mbd"])
            - float(base_regions["east_asia"]["net_seaborne_balance_mbd"]),
            2.0,
            places=6,
        )
        self.assertLess(
            float(inventory_regions["europe"]["net_seaborne_balance_mbd"]),
            float(base_regions["europe"]["net_seaborne_balance_mbd"]),
        )
        base_routes = {route["route_id"]: route for route in baseline["routes"]}
        rerouted_routes = {
            route["route_id"]: route for route in route_reroute["routes"]
        }
        self.assertAlmostEqual(
            float(rerouted_routes["gulf_europe"]["effective_haul_nm"])
            / float(base_routes["gulf_europe"]["effective_haul_nm"]),
            1.25,
            places=7,
        )
        self.assertAlmostEqual(
            float(route_reroute["seaborne_cargo_mbd"]),
            float(baseline["seaborne_cargo_mbd"]),
            places=7,
        )
        for scenario_turn in (
            rerouted,
            regional_supply_shift,
            refinery_shift,
            inventory_shift,
            route_reroute,
        ):
            self.assertEqual(
                baseline["macro_brent_oil_price_usd"],
                scenario_turn["macro_brent_oil_price_usd"],
            )
        self.assertEqual(
            "test_only_not_exposed_by_service_or_viewer",
            self.short_world.identity["scenario_scope"],
        )

    def test_production_outage_draws_inventory_without_breaking_balance(self) -> None:
        baseline = self.short_world.turns[0]
        outage = run_oil_shipping_world(
            self.short_macro,
            scenario_by_turn={0: {"production_outage_mbd": 5.0}},
        ).turns[0]
        self.assertLess(float(outage["production_mbd"]), float(baseline["production_mbd"]))
        self.assertLess(
            float(outage["closing_inventory_mmbbl"]),
            float(baseline["closing_inventory_mmbbl"]),
        )
        self.assertAlmostEqual(
            float(outage["mass_balance_residual_mmbbl"]),
            0.0,
            places=8,
        )

    def test_ordinary_multi_seed_worlds_remain_physical(self) -> None:
        for seed in range(12):
            world = run_oil_shipping_world(run_global_macro(seed, 12))
            self.assertTrue(
                all(float(turn["unmet_demand_mmbbl"]) == 0.0 for turn in world.turns)
            )
            self.assertTrue(
                all(25.0 <= float(turn["seaborne_cargo_mbd"]) <= 55.0 for turn in world.turns)
            )
            self.assertTrue(
                all(math.isfinite(float(turn["tonne_nautical_miles_billion"])) for turn in world.turns)
            )
            self.assertTrue(all("seaborne_share" not in turn for turn in world.turns))
            implied_shares = [
                float(turn["seaborne_cargo_mbd"])
                / float(turn["realized_demand_mbd"])
                for turn in world.turns
            ]
            self.assertGreater(max(implied_shares) - min(implied_shares), 0.002)

    def test_scenario_validation_rejects_unknown_or_invalid_values(self) -> None:
        with self.assertRaises(KeyError):
            run_oil_shipping_world(
                self.short_macro,
                scenario_by_turn={0: {"future_freight_rate": 100.0}},
            )
        with self.assertRaises(ValueError):
            run_oil_shipping_world(
                self.short_macro,
                scenario_by_turn={0: {"production_outage_mbd": -1.0}},
            )
        with self.assertRaises(TypeError):
            run_oil_shipping_world(
                self.short_macro,
                scenario_by_turn={0: {"regional_production_impulse_mbd": 1.0}},
            )
        with self.assertRaises(KeyError):
            run_oil_shipping_world(
                self.short_macro,
                scenario_by_turn={
                    0: {"route_haul_impulse_pct": {"unknown_route": 5.0}}
                },
            )


if __name__ == "__main__":
    unittest.main()
