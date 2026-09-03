from __future__ import annotations

import json
from pathlib import Path
import unittest

from asset_simulation.model.registry import load_registered_assets


REVIEW_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "route_cargo_haul_reference_review_v0.1.json"
)


class RouteCargoHaulReferenceReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        self.routes = list(self.review["major_routes"])
        self.runtime_network = load_registered_assets()[
            "oil_shipping_demand_config"
        ]["route_network"]
        self.runtime_routes_by_id = {
            route["route_id"]: route
            for route in self.runtime_network["explicit_routes"]
        }

    def test_fourteen_major_route_cargo_anchors_are_preserved(self) -> None:
        self.assertEqual(14, len(self.routes))
        self.assertAlmostEqual(
            28.7,
            sum(float(route["reference_cargo_mbd"]) for route in self.routes),
            places=8,
        )
        self.assertEqual(
            "keep_existing_2024_reference_matrix",
            self.review["cargo_conclusion"],
        )

    def test_review_only_changes_selected_haul_references(self) -> None:
        changed = [
            route
            for route in self.routes
            if float(route["current_haul_nm"]) != float(route["reviewed_haul_nm"])
        ]
        self.assertEqual(8, len(changed))
        self.assertTrue(
            all(float(route["reviewed_haul_nm"]) > 0.0 for route in self.routes)
        )
        self.assertTrue(
            all(float(route["sea_days_at_13kn"]) > 0.0 for route in self.routes)
        )

    def test_weighted_haul_revision_is_deliberately_small(self) -> None:
        current = float(self.review["current_reference_weighted_haul_nm"])
        reviewed = float(self.review["reviewed_reference_weighted_haul_nm"])
        change_pct = 100.0 * (reviewed / current - 1.0)
        self.assertAlmostEqual(-1.42, change_pct, places=2)
        self.assertLess(abs(change_pct), 2.0)

    def test_reviewed_haul_references_are_registered_in_runtime(self) -> None:
        self.assertEqual("applied_to_runtime_v0.6.9", self.review["status"])
        self.assertAlmostEqual(
            float(self.review["planning_speed_knots"]),
            float(self.runtime_network["planning_speed_knots"]),
        )
        for route in self.routes:
            runtime_route = self.runtime_routes_by_id[route["route_id"]]
            runtime_haul = self.runtime_network["pair_distances_nm"][
                runtime_route["origin_id"]
            ][runtime_route["destination_id"]]
            self.assertAlmostEqual(float(route["reviewed_haul_nm"]), runtime_haul)


if __name__ == "__main__":
    unittest.main()
