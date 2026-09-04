from __future__ import annotations

import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.gulf_east_asia_monopoly import (
    MODEL_VERSION,
    load_gulf_east_asia_monopoly_config,
    monthly_gulf_east_asia_inputs,
    run_gulf_east_asia_monopoly_operations,
    simulate_gulf_east_asia_monopoly_operations,
)
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world


class GulfEastAsiaMonopolyOperationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.macro = run_global_macro(42, 5)
        cls.shipping = run_oil_shipping_world(cls.macro)
        cls.config = load_gulf_east_asia_monopoly_config()
        cls.months = monthly_gulf_east_asia_inputs(cls.shipping)
        cls.cpi = {
            int(row["year"]): float(row["cpi_price_level_index_2025_100"])
            for row in cls.macro.rows
        }
        cls.result = run_gulf_east_asia_monopoly_operations(
            cls.macro,
            cls.shipping,
        )

    def test_reads_actual_seeded_route_and_oil_inputs(self) -> None:
        first_route = next(
            route
            for route in self.shipping.turns[0]["routes"]
            if route["route_id"] == "gulf_east_asia"
        )
        first = self.months[0]
        self.assertAlmostEqual(
            float(first_route["cargo_mbd"]),
            float(first["cargo_mbd"]),
            places=8,
        )
        self.assertAlmostEqual(
            float(self.shipping.turns[0]["macro_real_oil_price_index"]),
            float(first["real_oil_price_index"]),
            places=8,
        )
        self.assertAlmostEqual(
            float(self.shipping.turns[0]["macro_brent_oil_price_usd"]),
            float(first["nominal_brent_usd_per_bbl"]),
            places=8,
        )

    def test_fixed_numbered_fleet_is_strictly_conserved(self) -> None:
        self.assertEqual(MODEL_VERSION, self.result["identity"]["model_version"])
        self.assertEqual(250, self.result["identity"]["fixed_fleet_vlcc"])
        for row in self.result["turns"]:
            self.assertEqual(250, int(row["fixed_fleet_vlcc"]))
            self.assertEqual(
                250,
                int(row["active_voyage_vlcc"]) + int(row["gulf_idle_vlcc"]),
            )
            self.assertEqual(0, int(row["fleet_conservation_residual_vlcc"]))
            self.assertEqual(0, int(row["duplicate_ship_count"]))
            self.assertEqual(0, int(row["missing_ship_count"]))
            self.assertEqual(0, int(row["extra_ship_count"]))

    def test_virtual_inventory_is_strictly_conserved(self) -> None:
        for row in self.result["turns"]:
            self.assertAlmostEqual(
                0.0,
                float(row["inventory_conservation_residual_mmbbl"]),
                places=8,
            )

    def test_gross_freight_reconciles_to_tce_after_voyage_expenses(self) -> None:
        cycle_days = (
            float(self.config["reference_turn_days"])
            * int(self.config["fleet"]["cycle_turns"])
        )
        for row in self.result["turns"][::19]:
            net = (
                float(row["real_gross_freight_per_voyage_usd"])
                - float(row["real_voyage_expense_per_voyage_usd"])
            )
            self.assertAlmostEqual(
                float(row["real_net_voyage_revenue_per_voyage_usd"]),
                net,
                delta=0.05,
            )
            self.assertAlmostEqual(
                float(row["real_tce_2025_usd_per_day"]) * cycle_days,
                net,
                delta=0.10,
            )

    def test_opex_is_paid_on_every_owned_ship_even_when_idle(self) -> None:
        for row in self.result["turns"][::23]:
            expected = (
                int(row["fixed_fleet_vlcc"])
                * float(row["real_opex_2025_usd_per_vessel_day"])
                * int(row["turn_days"])
            )
            self.assertAlmostEqual(
                expected,
                float(row["real_fleet_opex_usd"]),
                delta=0.05,
            )
        self.assertGreater(
            max(int(row["gulf_idle_vlcc"]) for row in self.result["turns"]),
            0,
        )

    def test_nominal_values_are_real_values_scaled_by_cpi(self) -> None:
        for row in self.result["turns"][::29]:
            factor = float(row["cpi_price_level_index_2025_100"]) / 100.0
            self.assertAlmostEqual(
                float(row["nominal_tce_usd_per_day"]),
                float(row["real_tce_2025_usd_per_day"]) * factor,
                delta=0.02,
            )
            self.assertAlmostEqual(
                float(row["nominal_opex_usd_per_vessel_day"]),
                float(row["real_opex_2025_usd_per_vessel_day"]) * factor,
                delta=0.02,
            )

    def test_forced_withholding_raises_idle_capacity_and_later_scarcity(self) -> None:
        months = self.months[:24]
        baseline = simulate_gulf_east_asia_monopoly_operations(
            months,
            seed=42,
            cpi_by_year=self.cpi,
        )
        controls = {
            turn: {"dispatch_override_vlcc": 0}
            for turn in range(9, 12)
        }
        withheld = simulate_gulf_east_asia_monopoly_operations(
            months,
            seed=42,
            cpi_by_year=self.cpi,
            control_by_turn=controls,
        )
        baseline_rows = baseline["turns"]
        withheld_rows = withheld["turns"]
        self.assertGreater(
            sum(int(withheld_rows[index]["gulf_idle_vlcc"]) for index in range(9, 13)),
            sum(int(baseline_rows[index]["gulf_idle_vlcc"]) for index in range(9, 13)),
        )
        self.assertGreater(
            max(
                float(withheld_rows[index]["inventory_gap_days"])
                for index in range(10, 20)
            ),
            max(
                float(baseline_rows[index]["inventory_gap_days"])
                for index in range(10, 20)
            ),
        )
        self.assertGreater(
            max(
                float(withheld_rows[index]["real_tce_2025_usd_per_day"])
                for index in range(10, 20)
            ),
            max(
                float(baseline_rows[index]["real_tce_2025_usd_per_day"])
                for index in range(10, 20)
            ),
        )

    def test_same_seed_and_controls_are_deterministic(self) -> None:
        controls = {
            4: {"additional_withholding_vlcc": 3},
            5: {"additional_withholding_vlcc": 3},
        }
        first = simulate_gulf_east_asia_monopoly_operations(
            self.months[:12],
            seed=42,
            cpi_by_year=self.cpi,
            control_by_turn=controls,
        )
        second = simulate_gulf_east_asia_monopoly_operations(
            self.months[:12],
            seed=42,
            cpi_by_year=self.cpi,
            control_by_turn=controls,
        )
        self.assertEqual(first["turns"], second["turns"])
        self.assertEqual(first["summary"], second["summary"])

    def test_configuration_excludes_capital_structure_and_closes_opening_fleet(self) -> None:
        fleet = self.config["fleet"]
        cohorts = sum(int(value) for value in fleet["opening_departure_cohorts_vlcc"])
        self.assertEqual(
            int(fleet["reference_active_vlcc"]),
            cohorts + int(fleet["reference_prompt_dispatch_vlcc"]),
        )
        self.assertGreater(int(fleet["total_vlcc"]) - cohorts, 0)
        excluded = self.result["identity"]["excluded_scope"]
        for term in (
            "depreciation",
            "debt",
            "interest",
            "ship_value",
            "newbuildings",
            "scrapping",
        ):
            self.assertIn(term, excluded)


if __name__ == "__main__":
    unittest.main()
