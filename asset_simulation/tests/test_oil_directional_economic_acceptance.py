from __future__ import annotations

import unittest

from asset_simulation.audit_oil_directional_economic_acceptance import (
    _orientation_ecology,
    build_directional_economic_acceptance,
)


class OilDirectionalEconomicAcceptanceTests(unittest.TestCase):
    def test_orientation_ecology_requires_real_cells_and_both_sides(self) -> None:
        rows = []
        for seed in (0, 1):
            for band in ("low", "medium"):
                for score, cagr in ((10.0, 3.0), (50.0, 2.0), (90.0, 1.0)):
                    rows.append(
                        {
                            "seed": seed,
                            "forecast_band": band,
                            "controlled_axis": "continuation_reversion",
                            "controlled_score": score,
                            "cagr_pct": cagr
                            + (10.0 if seed == 1 and score == 90.0 else 0.0),
                        }
                    )
        ecology = _orientation_ecology(rows, frozenset({0, 1}))
        self.assertEqual(4, ecology["cell_count"])
        self.assertTrue(ecology["has_reversion_side_winner"])
        self.assertTrue(ecology["has_continuation_side_winner"])
        self.assertGreaterEqual(ecology["winner_score_count"], 2)

    def test_acceptance_reads_registered_regime_schema(self) -> None:
        calibration = {
            "gates": {},
            "diagnostics": {},
            "scenarios": [
                {
                    "seed": 0,
                    "forecast_band": "medium",
                    "controlled_axis": "continuation_reversion",
                    "controlled_score": 50.0,
                    "cagr_pct": 1.0,
                    "thesis_status_share_pct": {"invalidated": 20.0},
                }
            ],
        }
        regime = {
            "metrics": {
                "reversion": {
                    "trend": {"mean_turn_return_bps": 10.0},
                    "range": {"mean_turn_return_bps": 8.0},
                    "turning": {"mean_turn_return_bps": 9.0},
                },
                "balanced": {
                    "trend": {"mean_turn_return_bps": 12.0},
                    "range": {"mean_turn_return_bps": 7.0},
                    "turning": {"mean_turn_return_bps": 4.0},
                },
                "continuation": {
                    "trend": {"mean_turn_return_bps": 13.0},
                    "range": {"mean_turn_return_bps": 9.0},
                    "turning": {"mean_turn_return_bps": 1.0},
                },
            },
            "regime_winners": {
                "trend": "continuation",
                "range": "continuation",
                "turning": "reversion",
            },
        }
        report = build_directional_economic_acceptance(calibration, regime)
        self.assertEqual(
            13.0,
            report["regimeMeanTurnReturnBps"]["trend"]["continuation"],
        )
        self.assertEqual(
            8.0,
            report["regimeMeanTurnReturnBps"]["range"]["reversion"],
        )
        self.assertFalse(report["ok"])


if __name__ == "__main__":
    unittest.main()
