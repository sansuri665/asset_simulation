from __future__ import annotations

import statistics
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_shipping_regions import _project_to_global_total
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world


class OtherExportConservationTests(unittest.TestCase):
    def test_share_weighted_projection_closes_the_global_total(self) -> None:
        values = {
            "gulf": 27.0,
            "us_gulf": 14.0,
            "brazil_guyana": 5.0,
            "west_africa": 4.0,
            "other_export_regions": 19.5,
            "east_asia": 5.0,
            "south_asia": 0.6,
            "europe": 3.2,
            "north_america_import": 6.3,
            "rest_of_world": 1.4,
        }
        weights = {
            "gulf": 0.317,
            "us_gulf": 0.165,
            "brazil_guyana": 0.055,
            "west_africa": 0.043,
            "other_export_regions": 0.226,
            "east_asia": 0.058,
            "south_asia": 0.007,
            "europe": 0.038,
            "north_america_import": 0.075,
            "rest_of_world": 0.016,
        }
        projected = _project_to_global_total(
            values,
            global_total=83.8,
            weights=weights,
        )
        excess = sum(values.values()) - 83.8
        self.assertAlmostEqual(sum(projected.values()), 83.8, places=9)
        self.assertAlmostEqual(
            values["other_export_regions"] - projected["other_export_regions"],
            excess * weights["other_export_regions"],
            places=8,
        )
        self.assertLess(
            abs(projected["other_export_regions"] - values["other_export_regions"]),
            abs(excess) * 0.40,
        )

    def test_other_export_is_not_a_mechanical_mirror(self) -> None:
        world = run_oil_shipping_world(run_global_macro(0, 6))
        turns = world.turns[:72]
        oer_exports: list[float] = []
        gulf_exports: list[float] = []
        oer_policy: list[float] = []
        oer_cycle: list[float] = []
        oer_conservation: list[float] = []
        overlay: list[float] = []
        for turn in turns:
            regions = {
                str(region["region_id"]): region
                for region in turn["regional_balances"]
            }
            other = regions["other_export_regions"]
            gulf = regions["gulf"]
            self.assertGreater(float(other["net_seaborne_balance_mbd"]), 0.0)
            self.assertAlmostEqual(
                float(other["production_policy_adjustment_mbd"]),
                0.0,
                places=8,
            )
            self.assertAlmostEqual(
                float(turn["regional_crude_production_residual_mbd"]),
                0.0,
                places=8,
            )
            self.assertAlmostEqual(
                float(turn["regional_production_conservation_residual_mbd"]),
                0.0,
                places=6,
            )
            oer_exports.append(float(other["net_seaborne_balance_mbd"]))
            gulf_exports.append(float(gulf["net_seaborne_balance_mbd"]))
            oer_policy.append(float(other["production_policy_adjustment_mbd"]))
            oer_cycle.append(float(other["production_cycle_adjustment_mbd"]))
            oer_conservation.append(float(other["conservation_adjustment_mbd"]))
            overlay.append(
                -float(gulf["production_policy_adjustment_mbd"])
                - float(regions["us_gulf"]["production_cycle_adjustment_mbd"])
                - float(regions["brazil_guyana"]["production_cycle_adjustment_mbd"])
                - float(regions["west_africa"]["production_cycle_adjustment_mbd"])
            )

        monthly_changes = [
            current - previous
            for previous, current in zip(oer_exports, oer_exports[1:])
        ]
        gulf_changes = [
            current - previous
            for previous, current in zip(gulf_exports, gulf_exports[1:])
        ]
        self.assertLess(statistics.stdev(monthly_changes), statistics.stdev(gulf_changes))
        self.assertGreater(statistics.stdev(monthly_changes), 0.04)
        self.assertLess(statistics.stdev(monthly_changes), 0.16)
        self.assertGreater(max(oer_cycle) - min(oer_cycle), 0.15)
        self.assertGreaterEqual(min(oer_cycle), -0.30 - 1e-8)
        self.assertLessEqual(max(oer_cycle), 0.30 + 1e-8)
        self.assertGreater(
            statistics.stdev(oer_cycle),
            statistics.stdev(oer_conservation) * 0.35,
        )
        self.assertLess(abs(statistics.correlation(oer_exports, overlay)), 0.85)
        self.assertTrue(all(value == 0.0 for value in oer_policy))


if __name__ == "__main__":
    unittest.main()
