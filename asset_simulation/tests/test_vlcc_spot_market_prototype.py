from __future__ import annotations

import copy
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world
from asset_simulation.model.vlcc_spot_market import (
    load_vlcc_spot_market_config,
    run_gulf_east_asia_vlcc_spot_market,
    shipping_turn_days,
    simulate_vlcc_spot_route,
)


class VlccSpotMarketPrototypeTests(unittest.TestCase):
    def test_calendar_month_is_split_into_three_near_equal_turns(self) -> None:
        self.assertEqual((10, 10, 10), shipping_turn_days(30))
        self.assertEqual((10, 10, 11), shipping_turn_days(31))
        self.assertEqual((9, 9, 10), shipping_turn_days(28))
        self.assertEqual((9, 10, 10), shipping_turn_days(29))

    def test_seed42_reads_actual_structural_route_demand(self) -> None:
        macro = run_global_macro(42, 10)
        shipping = run_oil_shipping_world(macro)
        market = run_gulf_east_asia_vlcc_spot_market(macro, shipping)

        self.assertEqual(3 * len(shipping.turns), len(market["turns"]))
        actual_monthly_cargo: list[float] = []
        for month_index, month in enumerate(shipping.turns):
            route = next(
                route
                for route in month["routes"]
                if route["route_id"] == "gulf_east_asia"
            )
            cargo_mbd = float(route["cargo_mbd"])
            actual_monthly_cargo.append(cargo_mbd)
            records = market["turns"][3 * month_index : 3 * month_index + 3]
            self.assertAlmostEqual(
                cargo_mbd * int(month["days"]),
                sum(float(record["structural_cargo_mmbbl"]) for record in records),
                places=6,
            )
            self.assertTrue(
                all(
                    abs(float(record["structural_route_cargo_mbd"]) - cargo_mbd)
                    <= 1e-8
                    for record in records
                )
            )

        self.assertGreater(max(actual_monthly_cargo) - min(actual_monthly_cargo), 0.10)

    def test_virtual_inventory_deviation_is_strictly_conserved(self) -> None:
        macro = run_global_macro(42, 10)
        shipping = run_oil_shipping_world(macro)
        market = run_gulf_east_asia_vlcc_spot_market(macro, shipping)
        for record in market["turns"]:
            self.assertAlmostEqual(
                0.0,
                float(record["gulf_inventory_deviation_mmbbl"])
                + float(record["in_transit_deviation_mmbbl"])
                + float(record["east_asia_inventory_deviation_mmbbl"]),
                places=6,
            )
            self.assertLessEqual(
                abs(float(record["inventory_conservation_residual_mmbbl"])),
                1e-7,
            )

    def test_nominal_tce_is_real_tce_times_seed_cpi(self) -> None:
        macro = run_global_macro(42, 10)
        shipping = run_oil_shipping_world(macro)
        market = run_gulf_east_asia_vlcc_spot_market(macro, shipping)
        for record in market["turns"][::17]:
            expected = (
                float(record["real_tce_2025_usd_per_day"])
                * float(record["cpi_price_level_index_2025_100"])
                / 100.0
            )
            self.assertAlmostEqual(
                expected,
                float(record["nominal_tce_usd_per_day"]),
                places=1,
            )

    def test_flexible_repositioning_reduces_a_five_percent_demand_step(self) -> None:
        months = []
        year = 2025
        for month in range(1, 13):
            cargo_mbd = 9.3 * (1.05 if 4 <= month <= 6 else 1.0)
            months.append(
                {
                    "year": year,
                    "month": month,
                    "days": 30,
                    "cargo_mbd": cargo_mbd,
                }
            )
        cpi = {2025: 100.0}
        base_config = copy.deepcopy(load_vlcc_spot_market_config())
        flexible = simulate_vlcc_spot_route(
            months,
            seed=42,
            cpi_by_year=cpi,
            config=base_config,
        )
        fixed_config = copy.deepcopy(base_config)
        fixed_config["repositioning"]["maximum_external_pool_vlcc"] = 0
        fixed_config["repositioning"]["maximum_net_reposition_vlcc_per_turn"] = 0
        fixed = simulate_vlcc_spot_route(
            months,
            seed=42,
            cpi_by_year=cpi,
            config=fixed_config,
        )

        reference_fleet = int(base_config["reference_route_fleet_vlcc"])
        self.assertGreater(
            max(int(turn["route_fleet_vlcc"]) for turn in flexible["turns"]),
            reference_fleet,
        )
        self.assertGreater(
            float(flexible["summary"]["real_tce_2025_usd_per_day_max"]),
            float(base_config["freight"]["baseline_real_tce_2025_usd_per_day"]),
        )
        self.assertLess(
            float(flexible["summary"]["maximum_abs_inventory_gap_days"]),
            float(fixed["summary"]["maximum_abs_inventory_gap_days"]),
        )

    def test_seed42_flexible_supply_stays_inside_declared_prototype_rails(self) -> None:
        config = load_vlcc_spot_market_config()
        macro = run_global_macro(42, 20)
        shipping = run_oil_shipping_world(macro)
        market = run_gulf_east_asia_vlcc_spot_market(macro, shipping)
        base_fleet = int(config["reference_route_fleet_vlcc"])
        pool = int(config["repositioning"]["maximum_external_pool_vlcc"])
        minimum_tce = (
            float(config["freight"]["baseline_real_tce_2025_usd_per_day"])
            * float(config["freight"]["minimum_real_tce_multiple"])
        )
        maximum_tce = (
            float(config["freight"]["baseline_real_tce_2025_usd_per_day"])
            * float(config["freight"]["maximum_real_tce_multiple"])
        )

        self.assertGreaterEqual(
            int(market["summary"]["route_fleet_vlcc_min"]),
            base_fleet - pool,
        )
        self.assertLessEqual(
            int(market["summary"]["route_fleet_vlcc_max"]),
            base_fleet + pool,
        )
        self.assertGreaterEqual(
            float(market["summary"]["real_tce_2025_usd_per_day_min"]),
            minimum_tce - 1.0,
        )
        self.assertLessEqual(
            float(market["summary"]["real_tce_2025_usd_per_day_max"]),
            maximum_tce + 1.0,
        )
        self.assertLess(
            float(market["summary"]["p95_abs_inventory_gap_days"]),
            10.0,
        )


if __name__ == "__main__":
    unittest.main()
