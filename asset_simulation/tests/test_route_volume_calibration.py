from __future__ import annotations

import statistics
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world
from asset_simulation.model.registry import load_registered_assets


class RouteVolumeCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_registered_assets()["oil_shipping_demand_config"]
        cls.network = cls.config["route_network"]
        cls.calibration = cls.network["volume_calibration"]
        cls.world = run_oil_shipping_world(run_global_macro(42, 5))

    def test_reference_matrix_and_margins_close_exactly(self) -> None:
        matrix = self.calibration["reference_pair_cargo_mbd"]
        exports = self.calibration["reference_export_margins_mbd"]
        imports = self.calibration["reference_import_margins_mbd"]

        self.assertEqual(5, len(matrix))
        self.assertEqual(25, sum(len(row) for row in matrix.values()))
        self.assertAlmostEqual(
            float(self.calibration["reference_seaborne_cargo_mbd"]),
            sum(float(value) for row in matrix.values() for value in row.values()),
            places=8,
        )
        for origin_id, row in matrix.items():
            self.assertAlmostEqual(
                float(exports[origin_id]),
                sum(float(value) for value in row.values()),
                places=8,
            )
        for destination_id, target in imports.items():
            self.assertAlmostEqual(
                float(target),
                sum(float(row[destination_id]) for row in matrix.values()),
                places=8,
            )

        explicit_pairs = {
            f"{route['origin_id']}::{route['destination_id']}"
            for route in self.network["explicit_routes"]
        }
        all_pairs = {
            f"{origin_id}::{destination_id}"
            for origin_id, row in matrix.items()
            for destination_id in row
        }
        self.assertEqual(14, len(explicit_pairs))
        self.assertEqual(11, len(all_pairs - explicit_pairs))
        residual_reference = sum(
            float(matrix[pair_id.split("::", 1)[0]][pair_id.split("::", 1)[1]])
            for pair_id in all_pairs - explicit_pairs
        )
        self.assertAlmostEqual(11.1, residual_reference, places=8)

    def test_world_publishes_fourteen_routes_and_independent_diagnostics(self) -> None:
        matrix = self.calibration["reference_pair_cargo_mbd"]
        diagnostic_differences: list[float] = []
        gulf_east_asia_cargo: list[float] = []

        for turn in self.world.turns:
            self.assertEqual(14, int(turn["calibrated_major_route_count"]))
            self.assertEqual(25, int(turn["active_pair_count"]))
            self.assertEqual(15, len(turn["routes"]))
            self.assertEqual(
                1,
                sum(bool(route["is_other_pool"]) for route in turn["routes"]),
            )
            for route in turn["routes"]:
                self.assertIn("reference_cargo_mbd", route)
                self.assertIn("margin_scaled_reference_mbd", route)
                self.assertIn("cargo_vs_reference_pct", route)
                self.assertGreater(float(route["cargo_mbd"]), 0.0)
                diagnostic_differences.append(
                    abs(
                        float(route["cargo_mbd"])
                        - float(route["margin_scaled_reference_mbd"])
                    )
                )
                if not route["is_other_pool"]:
                    origin_id = str(route["origin_id"])
                    destination_id = str(route["destination_id"])
                    self.assertAlmostEqual(
                        float(matrix[origin_id][destination_id]),
                        float(route["reference_cargo_mbd"]),
                        places=8,
                    )
                    self.assertAlmostEqual(
                        float(
                            self.network["pair_distances_nm"][origin_id][
                                destination_id
                            ]
                        ),
                        float(route["baseline_haul_nm"]),
                        places=8,
                    )
                if route["route_id"] == "gulf_east_asia":
                    gulf_east_asia_cargo.append(float(route["cargo_mbd"]))

        self.assertGreater(max(diagnostic_differences), 0.01)
        self.assertGreater(max(gulf_east_asia_cargo) - min(gulf_east_asia_cargo), 0.20)
        self.assertLess(
            abs(statistics.mean(gulf_east_asia_cargo) / 9.3 - 1.0),
            0.20,
        )

    def test_tonne_mile_identity_is_unchanged(self) -> None:
        barrels_per_tonne = float(self.config["units"]["barrels_per_metric_tonne"])
        for turn in self.world.turns[::13]:
            for route in turn["routes"]:
                expected_tonnes = (
                    float(route["cargo_mbd"])
                    * int(turn["days"])
                    / barrels_per_tonne
                )
                expected_tonne_miles = (
                    expected_tonnes * float(route["effective_haul_nm"]) / 1000.0
                )
                self.assertAlmostEqual(
                    expected_tonnes,
                    float(route["cargo_million_tonnes"]),
                    places=6,
                )
                self.assertAlmostEqual(
                    expected_tonne_miles,
                    float(route["tonne_nautical_miles_billion"]),
                    places=6,
                )


if __name__ == "__main__":
    unittest.main()
