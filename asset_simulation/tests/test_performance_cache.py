from __future__ import annotations

import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world


class PerformanceCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        run_oil_shipping_world.cache_clear()

    def tearDown(self) -> None:
        run_oil_shipping_world.cache_clear()

    def test_shipping_projection_reuses_same_world(self) -> None:
        run = run_global_macro(42, 5)
        first = run_oil_shipping_world(run)
        second = run_oil_shipping_world(run)
        self.assertIs(first, second)
        info = run_oil_shipping_world.cache_info()
        self.assertEqual(1, info["hits"])
        self.assertEqual(1, info["misses"])
        self.assertEqual(1, info["currentEntries"])

    def test_scenario_arguments_have_distinct_cache_keys(self) -> None:
        run = run_global_macro(42, 5)
        baseline = run_oil_shipping_world(run)
        rerouted = run_oil_shipping_world(
            run,
            scenario_by_turn={0: {"average_haul_impulse_pct": 15.0}},
        )
        repeat = run_oil_shipping_world(
            run,
            scenario_by_turn={0: {"average_haul_impulse_pct": 15.0}},
        )
        self.assertIs(rerouted, repeat)
        self.assertIsNot(baseline, rerouted)
        self.assertNotEqual(
            baseline.identity["scenario_hash"],
            rerouted.identity["scenario_hash"],
        )
        info = run_oil_shipping_world.cache_info()
        self.assertEqual(1, info["hits"])
        self.assertEqual(2, info["misses"])


if __name__ == "__main__":
    unittest.main()
