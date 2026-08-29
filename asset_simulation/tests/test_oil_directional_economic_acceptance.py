from __future__ import annotations

import unittest

from asset_simulation.audit_oil_directional_economic_acceptance import (
    evaluate_directional_economic_acceptance,
)


class OilDirectionalEconomicAcceptanceTests(unittest.TestCase):
    def _report(self, counts: dict[str, int]) -> dict:
        return {
            "identity": {"result_hash": "diagnostic-hash"},
            "ok": False,
            "orientationWinnerCounts": counts,
            "gates": {
                "all_account_invariants_pass": True,
                "medium_forecast_invalidated_occupancy_guardrail_5_to_30_pct": True,
                "no_orientation_score_wins_more_than_half_of_cells": False,
                "round_trip_pnl_share_median_below_35_pct": True,
            },
        }

    def test_diverse_18_of_32_winner_distribution_is_accepted(self) -> None:
        acceptance = evaluate_directional_economic_acceptance(
            self._report({"10.0": 18, "30.0": 4, "50.0": 2, "70.0": 3, "90.0": 5})
        )
        self.assertTrue(acceptance["ok"])
        self.assertAlmostEqual(18 / 32, acceptance["orientationDiagnostics"]["largest_winner_share"])
        self.assertEqual(5, acceptance["orientationDiagnostics"]["winning_score_count"])
        self.assertGreater(acceptance["orientationDiagnostics"]["reversion_side_wins"], 0)
        self.assertGreater(acceptance["orientationDiagnostics"]["continuation_side_wins"], 0)

    def test_near_monopoly_or_one_sided_winners_are_rejected(self) -> None:
        acceptance = evaluate_directional_economic_acceptance(
            self._report({"10.0": 30, "30.0": 2, "50.0": 0, "70.0": 0, "90.0": 0})
        )
        self.assertFalse(acceptance["ok"])
        self.assertIn(
            "no_orientation_score_wins_more_than_70pct_of_cells",
            acceptance["failed_gates"],
        )
        self.assertIn(
            "orientation_winners_cover_both_sides_of_neutral",
            acceptance["failed_gates"],
        )

    def test_non_orientation_failure_remains_blocking(self) -> None:
        report = self._report({"10.0": 18, "30.0": 4, "50.0": 2, "70.0": 3, "90.0": 5})
        report["gates"]["medium_forecast_invalidated_occupancy_guardrail_5_to_30_pct"] = False
        acceptance = evaluate_directional_economic_acceptance(report)
        self.assertFalse(acceptance["ok"])
        self.assertIn(
            "medium_forecast_invalidated_occupancy_guardrail_5_to_30_pct",
            acceptance["failed_gates"],
        )


if __name__ == "__main__":
    unittest.main()
