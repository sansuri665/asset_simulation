from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.audit_oil_directional_economic_acceptance import (
    evaluate_directional_economic_acceptance,
)


class OilDirectionalEconomicAcceptanceTests(unittest.TestCase):
    @staticmethod
    def _calibration() -> dict:
        rows = []
        bands = ("low", "medium", "high", "elite")
        scores = (10.0, 30.0, 50.0, 70.0, 90.0)
        winners = (10.0, 30.0, 50.0, 70.0, 90.0)
        for seed in range(16):
            for band_index, band in enumerate(bands):
                winner = winners[(seed + band_index) % len(winners)]
                for score in scores:
                    rows.append(
                        {
                            "seed": seed,
                            "forecast_band": band,
                            "controlled_axis": "continuation_reversion",
                            "controlled_score": score,
                            "cagr_pct": 10.0 if score == winner else 0.0,
                            "thesis_status_share_pct": {"invalidated": 20.0},
                        }
                    )
        return {
            "ok": False,
            "scenarios": rows,
            "gates": {
                "all_account_invariants_pass": True,
                "medium_forecast_invalidated_occupancy_guardrail_5_to_30_pct": True,
                "higher_capital_deployment_increases_median_volatility": True,
                "higher_capital_deployment_increases_median_drawdown": True,
                "no_orientation_score_wins_more_than_half_of_cells": False,
            },
        }

    @staticmethod
    def _regime() -> dict:
        return {
            "metrics": {
                "reversion": {
                    "trend": {"mean_turn_return_bps": 10.0},
                    "range": {"mean_turn_return_bps": 20.0},
                    "turning": {"mean_turn_return_bps": 30.0},
                },
                "balanced": {
                    "trend": {"mean_turn_return_bps": 25.0},
                    "range": {"mean_turn_return_bps": 22.0},
                    "turning": {"mean_turn_return_bps": 10.0},
                },
                "continuation": {
                    "trend": {"mean_turn_return_bps": 30.0},
                    "range": {"mean_turn_return_bps": 21.0},
                    "turning": {"mean_turn_return_bps": 5.0},
                },
            },
            "regime_winners": {
                "trend": "continuation",
                "range": "balanced",
                "turning": "reversion",
            },
        }

    def test_dev_validation_ecology_replaces_only_legacy_orientation_gate(self) -> None:
        acceptance = evaluate_directional_economic_acceptance(
            self._calibration(), self._regime()
        )
        self.assertTrue(acceptance["ok"])
        self.assertFalse(acceptance["raw_report_ok"])
        self.assertEqual(64, acceptance["orientation"]["combined"]["cell_count"])
        self.assertNotIn(
            "no_orientation_score_wins_more_than_half_of_cells",
            acceptance["gates"],
        )

    def test_non_orientation_raw_gate_remains_blocking(self) -> None:
        calibration = self._calibration()
        calibration["gates"][
            "higher_capital_deployment_increases_median_volatility"
        ] = False
        acceptance = evaluate_directional_economic_acceptance(
            calibration, self._regime()
        )
        self.assertFalse(acceptance["ok"])
        self.assertIn(
            "higher_capital_deployment_increases_median_volatility",
            acceptance["failed_gates"],
        )

    def test_each_seed_partition_keeps_the_frozen_thesis_guardrail(self) -> None:
        calibration = self._calibration()
        for row in calibration["scenarios"]:
            if (
                row["seed"] < 8
                and row["forecast_band"] == "medium"
                and row["controlled_score"] == 50.0
            ):
                row["thesis_status_share_pct"]["invalidated"] = 31.0
        acceptance = evaluate_directional_economic_acceptance(
            calibration, self._regime()
        )
        self.assertFalse(acceptance["ok"])
        self.assertIn(
            "development_medium_thesis_invalidated_5_to_30_pct",
            acceptance["failed_gates"],
        )


if __name__ == "__main__":
    unittest.main()
