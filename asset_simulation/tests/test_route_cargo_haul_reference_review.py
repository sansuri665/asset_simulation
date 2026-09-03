from __future__ import annotations

import json
from pathlib import Path
import unittest


REVIEW_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "route_cargo_haul_reference_review_v0.1.json"
)


class RouteCargoHaulReferenceReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        self.routes = list(self.review["major_routes"])

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


if __name__ == "__main__":
    unittest.main()
