from __future__ import annotations

import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world
from asset_simulation.model.single_route_pricing import (
    MODEL_VERSION,
    build_lagged_prompt_supply_path,
    load_single_route_pricing_config,
    monthly_gulf_east_asia_pricing_inputs,
    price_single_route_turn,
    run_seeded_gulf_east_asia_pricing,
    simulate_gulf_east_asia_price_path,
)


class GulfEastAsiaSingleRoutePricingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_single_route_pricing_config()
        cls.macro = run_global_macro(42, 5)
        cls.shipping = run_oil_shipping_world(cls.macro)
        cls.months = monthly_gulf_east_asia_pricing_inputs(cls.shipping)
        cls.cpi = {
            int(row["year"]): float(row["cpi_price_level_index_2025_100"])
            for row in cls.macro.rows
        }

    def _balanced_prompt_supply(self, *, cargo_mbd: float, turn_days: int) -> float:
        cargo_capacity = float(self.config["vlcc_cargo_mmbbl"])
        smoothing = float(self.config["pricing"]["liquidity_smoothing_vlcc"])
        reference_demand = (
            float(self.config["reference_route_cargo_mbd"])
            * float(self.config["reference_turn_days"])
            / cargo_capacity
        )
        reference_prompt = float(self.config["reference_prompt_supply_vlcc"])
        reference_tightness = (
            reference_demand + smoothing
        ) / (reference_prompt + smoothing)
        demand = cargo_mbd * turn_days / cargo_capacity
        return (demand + smoothing) / reference_tightness - smoothing

    def test_state_contract_is_zero_two_one_two(self) -> None:
        state = self.config["state_contract"]
        self.assertEqual(0, int(state["loading_turns"]))
        self.assertEqual(2, int(state["laden_turns"]))
        self.assertEqual(1, int(state["discharge_turns"]))
        self.assertEqual(2, int(state["ballast_turns"]))
        self.assertEqual(5, int(state["cycle_turns"]))
        self.assertEqual(3, int(state["cargo_arrival_lag_turns"]))

    def test_balanced_route_prices_at_baseline(self) -> None:
        prompt = self._balanced_prompt_supply(cargo_mbd=9.3, turn_days=10)
        quote = price_single_route_turn(
            structural_cargo_mbd=9.3,
            turn_days=10,
            prompt_supply_vlcc=prompt,
            config=self.config,
        )
        self.assertEqual(MODEL_VERSION, quote["model_version"])
        self.assertAlmostEqual(
            35000.0,
            float(quote["real_tce_2025_usd_per_day"]),
            delta=0.02,
        )
        self.assertAlmostEqual(
            1.0,
            float(quote["relative_tightness_ratio"]),
            places=7,
        )

    def test_less_prompt_supply_raises_price_and_more_lowers_it(self) -> None:
        prompt = self._balanced_prompt_supply(cargo_mbd=9.3, turn_days=10)
        loose = price_single_route_turn(
            structural_cargo_mbd=9.3,
            turn_days=10,
            prompt_supply_vlcc=prompt + 6.0,
            config=self.config,
        )
        balanced = price_single_route_turn(
            structural_cargo_mbd=9.3,
            turn_days=10,
            prompt_supply_vlcc=prompt,
            config=self.config,
        )
        tight = price_single_route_turn(
            structural_cargo_mbd=9.3,
            turn_days=10,
            prompt_supply_vlcc=prompt - 6.0,
            config=self.config,
        )
        self.assertLess(
            float(loose["real_tce_2025_usd_per_day"]),
            float(balanced["real_tce_2025_usd_per_day"]),
        )
        self.assertLess(
            float(balanced["real_tce_2025_usd_per_day"]),
            float(tight["real_tce_2025_usd_per_day"]),
        )

    def test_origin_accumulation_and_destination_shortage_raise_price(self) -> None:
        prompt = self._balanced_prompt_supply(cargo_mbd=9.3, turn_days=10)
        normal = price_single_route_turn(
            structural_cargo_mbd=9.3,
            turn_days=10,
            prompt_supply_vlcc=prompt,
            config=self.config,
        )
        stressed = price_single_route_turn(
            structural_cargo_mbd=9.3,
            turn_days=10,
            prompt_supply_vlcc=prompt,
            origin_inventory_deviation_mmbbl=9.3,
            destination_inventory_deviation_mmbbl=-9.3,
            config=self.config,
        )
        reversed_gap = price_single_route_turn(
            structural_cargo_mbd=9.3,
            turn_days=10,
            prompt_supply_vlcc=prompt,
            origin_inventory_deviation_mmbbl=-9.3,
            destination_inventory_deviation_mmbbl=9.3,
            config=self.config,
        )
        self.assertGreater(
            float(stressed["real_tce_2025_usd_per_day"]),
            float(normal["real_tce_2025_usd_per_day"]),
        )
        self.assertLess(
            float(reversed_gap["real_tce_2025_usd_per_day"]),
            float(normal["real_tce_2025_usd_per_day"]),
        )
        self.assertAlmostEqual(1.0, float(stressed["inventory_gap_days"]), places=7)

    def test_nominal_price_is_real_price_times_cpi(self) -> None:
        quote = price_single_route_turn(
            structural_cargo_mbd=9.3,
            turn_days=10,
            prompt_supply_vlcc=50.0,
            cpi_price_level_index_2025_100=175.0,
            config=self.config,
        )
        self.assertAlmostEqual(
            float(quote["real_tce_2025_usd_per_day"]) * 1.75,
            float(quote["nominal_tce_usd_per_day"]),
            delta=0.02,
        )

    def test_pricing_output_contains_no_cost_or_owner_accounting(self) -> None:
        quote = price_single_route_turn(
            structural_cargo_mbd=9.3,
            turn_days=10,
            prompt_supply_vlcc=50.0,
            config=self.config,
        )
        forbidden_fragments = (
            "bunker",
            "port_cost",
            "commission",
            "opex",
            "cashflow",
            "debt",
            "interest",
            "depreciation",
            "ship_value",
            "owner_profit",
        )
        for key in quote:
            self.assertFalse(
                any(fragment in key for fragment in forbidden_fragments),
                msg=f"pricing output leaked accounting field: {key}",
            )

    def test_reads_actual_seeded_route_demand(self) -> None:
        first_route = next(
            route
            for route in self.shipping.turns[0]["routes"]
            if route["route_id"] == "gulf_east_asia"
        )
        self.assertAlmostEqual(
            float(first_route["cargo_mbd"]),
            float(self.months[0]["cargo_mbd"]),
            places=8,
        )

    def test_seeded_path_conserves_virtual_inventory(self) -> None:
        supply = build_lagged_prompt_supply_path(
            self.months,
            lag_turns=2,
            config=self.config,
        )
        result = simulate_gulf_east_asia_price_path(
            self.months,
            seed=42,
            cpi_by_year=self.cpi,
            prompt_supply_by_turn=supply,
            config=self.config,
        )
        self.assertEqual(
            0.0,
            float(
                result["summary"][
                    "maximum_abs_inventory_conservation_residual_mmbbl"
                ]
            ),
        )
        for row in result["turns"][::17]:
            self.assertAlmostEqual(
                0.0,
                float(row["inventory_conservation_residual_mmbbl"]),
                places=8,
            )

    def test_temporary_supply_shortage_raises_seeded_price(self) -> None:
        baseline = run_seeded_gulf_east_asia_pricing(
            self.macro,
            self.shipping,
            supply_lag_turns=2,
            config=self.config,
        )
        shock = {
            turn: -6
            for turn in range(30, 39)
        }
        stressed = run_seeded_gulf_east_asia_pricing(
            self.macro,
            self.shipping,
            supply_lag_turns=2,
            temporary_supply_delta_by_turn=shock,
            config=self.config,
        )
        self.assertGreater(
            float(stressed["summary"]["real_tce_2025_usd_per_day_max"]),
            float(baseline["summary"]["real_tce_2025_usd_per_day_max"]),
        )
        self.assertGreater(
            int(stressed["summary"]["total_unfilled_fixture_vlcc"]),
            int(baseline["summary"]["total_unfilled_fixture_vlcc"]),
        )

    def test_same_seed_and_supply_path_are_deterministic(self) -> None:
        supply = build_lagged_prompt_supply_path(
            self.months,
            lag_turns=2,
            config=self.config,
        )
        first = simulate_gulf_east_asia_price_path(
            self.months,
            seed=42,
            cpi_by_year=self.cpi,
            prompt_supply_by_turn=supply,
            config=self.config,
        )
        second = simulate_gulf_east_asia_price_path(
            self.months,
            seed=42,
            cpi_by_year=self.cpi,
            prompt_supply_by_turn=supply,
            config=self.config,
        )
        self.assertEqual(first["turns"], second["turns"])
        self.assertEqual(first["summary"], second["summary"])


if __name__ == "__main__":
    unittest.main()
