from __future__ import annotations

import statistics
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.math_utils import clamp
from asset_simulation.model.oil_shipping_regions import _advance_west_africa_cycle
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world


class WestAfricaCycleTests(unittest.TestCase):
    def test_disruption_cycle_is_persistent_bounded_and_nontrivial(self) -> None:
        state = {
            "project_target_mbd": 0.0,
            "project_deviation_mbd": 0.0,
            "operational_deviation_mbd": 0.0,
        }
        adjustments: list[float] = []
        for turn_index in range(10 * 12):
            state = _advance_west_africa_cycle(
                state,
                seed=42,
                turn_index=turn_index,
                month=turn_index % 12 + 1,
            )
            adjustments.append(
                clamp(
                    float(state["project_deviation_mbd"])
                    + float(state["operational_deviation_mbd"]),
                    -0.60,
                    0.50,
                )
            )

        monthly_changes = [
            current - previous
            for previous, current in zip(adjustments, adjustments[1:])
        ]
        self.assertGreater(statistics.stdev(monthly_changes), 0.08)
        self.assertLess(statistics.stdev(monthly_changes), 0.20)
        self.assertGreater(max(adjustments) - min(adjustments), 0.70)
        self.assertGreaterEqual(min(adjustments), -0.60)
        self.assertLessEqual(max(adjustments), 0.50)

    def test_west_africa_exports_have_visible_monthly_supply_cycle(self) -> None:
        world = run_oil_shipping_world(run_global_macro(42, 5))
        exports: list[float] = []
        cycle_adjustments: list[float] = []
        for turn in world.turns:
            region = next(
                region
                for region in turn["regional_balances"]
                if region["region_id"] == "west_africa"
            )
            exports.append(float(region["net_seaborne_balance_mbd"]))
            cycle_adjustments.append(float(region["production_cycle_adjustment_mbd"]))
            self.assertGreater(float(region["net_seaborne_balance_mbd"]), 0.0)

        monthly_changes = [
            current - previous
            for previous, current in zip(exports, exports[1:])
        ]
        self.assertGreater(statistics.stdev(monthly_changes), 0.08)
        self.assertLess(statistics.stdev(monthly_changes), 0.24)
        self.assertGreater(max(exports) - min(exports), 0.80)
        self.assertGreater(max(cycle_adjustments) - min(cycle_adjustments), 0.70)


if __name__ == "__main__":
    unittest.main()
