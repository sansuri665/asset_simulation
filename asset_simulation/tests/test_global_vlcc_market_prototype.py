from __future__ import annotations

import copy
import statistics
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.global_vlcc_market import (
    MODEL_VERSION,
    load_global_vlcc_market_config,
    monthly_global_vlcc_inputs,
    run_global_vlcc_spot_market,
    simulate_global_vlcc_spot_market,
)
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world


class GlobalVlccSpotMarketPrototypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.macro = run_global_macro(42, 5)
        cls.shipping = run_oil_shipping_world(cls.macro)
        cls.inputs = monthly_global_vlcc_inputs(cls.shipping)
        cls.result = run_global_vlcc_spot_market(cls.macro, cls.shipping)

    def test_reads_actual_seeded_named_and_residual_demand(self) -> None:
        config = load_global_vlcc_market_config()
        route_config = {
            str(route["route_id"]): route for route in config["routes"]
        }
        first_shipping_routes = {
            str(route["route_id"]): route
            for route in self.shipping.turns[0]["routes"]
        }
        first_inputs = self.inputs[0]["route_cargo_mbd"]
        self.assertAlmostEqual(
            float(first_shipping_routes["gulf_east_asia"]["cargo_mbd"])
            * float(route_config["gulf_east_asia"]["vlcc_share"]),
            float(first_inputs["gulf_east_asia"]),
            places=8,
        )
        self.assertAlmostEqual(
            float(first_shipping_routes["us_gulf_east_asia"]["cargo_mbd"])
            * float(route_config["us_gulf_east_asia"]["vlcc_share"]),
            float(first_inputs["us_gulf_east_asia"]),
            places=8,
        )
        self.assertGreater(float(first_inputs["other_vlcc_market"]), 0.0)
        for route_id in first_inputs:
            series = [
                float(month["route_cargo_mbd"][route_id])
                for month in self.inputs
            ]
            self.assertGreater(max(series) - min(series), 1e-4)

    def test_numbered_global_fleet_is_strictly_conserved(self) -> None:
        self.assertEqual(MODEL_VERSION, self.result["model_version"])
        for record in self.result["records"]:
            self.assertEqual(900, int(record["global_total_fleet_vlcc"]))
            self.assertEqual(
                0,
                int(record["global_fleet_conservation_residual_vlcc"]),
            )
            self.assertEqual(
                900,
                int(record["global_route_fleet_vlcc"])
                + int(record["global_idle_fleet_vlcc"])
                + int(record["global_repositioning_fleet_vlcc"])
                + int(record["global_unavailable_fleet_vlcc"]),
            )
            for route in record["routes"]:
                self.assertGreaterEqual(
                    int(route["closing_route_fleet_vlcc"]), 0
                )
                self.assertAlmostEqual(
                    0.0,
                    float(route["inventory_conservation_residual_mmbbl"]),
                    places=8,
                )

    def test_nominal_tce_is_real_tce_times_seed_cpi(self) -> None:
        for record in self.result["records"][::17]:
            cpi = float(record["cpi_price_level_index_2025_100"])
            self.assertAlmostEqual(
                float(record["global_nominal_tce_usd_per_day"]),
                float(record["global_real_tce_2025_usd_per_day"]) * cpi / 100.0,
                delta=0.02,
            )
            for route in record["routes"]:
                self.assertAlmostEqual(
                    float(route["nominal_tce_usd_per_day"]),
                    float(route["real_tce_2025_usd_per_day"]) * cpi / 100.0,
                    delta=0.02,
                )

    def test_same_seed_is_deterministic(self) -> None:
        repeat = run_global_vlcc_spot_market(self.macro, self.shipping)
        self.assertEqual(self.result["records"], repeat["records"])
        self.assertEqual(self.result["summary"], repeat["summary"])

    def test_one_route_can_only_gain_ships_from_the_fixed_market(self) -> None:
        months = [copy.deepcopy(month) for month in self.inputs[:36]]
        for month in months[6:24]:
            month["route_cargo_mbd"]["gulf_east_asia"] *= 1.10
        cpi = {
            int(row["year"]): float(row["cpi_price_level_index_2025_100"])
            for row in self.macro.rows
        }
        constrained_config = copy.deepcopy(load_global_vlcc_market_config())
        idle_to_reassign = int(
            constrained_config["fleet"]["initial_idle_vlcc"]
        )
        constrained_config["fleet"]["initial_idle_vlcc"] = 0
        residual = next(
            route
            for route in constrained_config["routes"]
            if route["route_id"] == "other_vlcc_market"
        )
        residual["reference_route_fleet_vlcc"] += idle_to_reassign
        shocked = simulate_global_vlcc_spot_market(
            months,
            seed=42,
            cpi_by_year=cpi,
            config=constrained_config,
        )
        gulf_fleet = [
            next(
                int(route["closing_route_fleet_vlcc"])
                for route in record["routes"]
                if route["route_id"] == "gulf_east_asia"
            )
            for record in shocked["records"]
        ]
        self.assertGreater(
            statistics.fmean(gulf_fleet[-24:]),
            statistics.fmean(gulf_fleet[:18]),
        )
        route_to_route = [
            event
            for record in shocked["records"]
            for event in record["transfer_events"]
            if event["source_id"] != "idle"
            and event["destination_id"] != "idle"
        ]
        self.assertTrue(route_to_route)
        self.assertEqual(
            0,
            shocked["summary"]["maximum_abs_fleet_conservation_residual_vlcc"],
        )

    def test_market_wide_demand_step_tightens_global_freight(self) -> None:
        months = [copy.deepcopy(month) for month in self.inputs[:36]]
        shocked_months = [copy.deepcopy(month) for month in months]
        for month in shocked_months[6:24]:
            for route_id in month["route_cargo_mbd"]:
                month["route_cargo_mbd"][route_id] *= 1.08
        cpi = {
            int(row["year"]): float(row["cpi_price_level_index_2025_100"])
            for row in self.macro.rows
        }
        baseline = simulate_global_vlcc_spot_market(
            months,
            seed=42,
            cpi_by_year=cpi,
        )
        shocked = simulate_global_vlcc_spot_market(
            shocked_months,
            seed=42,
            cpi_by_year=cpi,
        )
        self.assertGreater(
            shocked["summary"]["global_real_tce_2025_usd_per_day_p95"],
            baseline["summary"]["global_real_tce_2025_usd_per_day_p95"],
        )
        self.assertLessEqual(
            shocked["summary"]["global_idle_fleet_vlcc_min"],
            baseline["summary"]["global_idle_fleet_vlcc_min"],
        )
        self.assertGreater(
            shocked["summary"]["total_unfilled_fixture_vlcc"],
            baseline["summary"]["total_unfilled_fixture_vlcc"],
        )

    def test_config_closes_reference_global_fleet(self) -> None:
        config = load_global_vlcc_market_config()
        fleet = config["fleet"]
        route_fleet = sum(
            int(route["reference_route_fleet_vlcc"])
            for route in config["routes"]
        )
        self.assertEqual(
            int(fleet["total_vlcc"]),
            route_fleet
            + int(fleet["initial_idle_vlcc"])
            + int(fleet["unavailable_vlcc"]),
        )


if __name__ == "__main__":
    unittest.main()
