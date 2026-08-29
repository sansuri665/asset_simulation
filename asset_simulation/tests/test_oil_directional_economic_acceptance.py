from __future__ import annotations

import unittest

from asset_simulation.audit_oil_directional_economic_acceptance import (
    _orientation_ecology,
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
                            "cagr_pct": cagr + (10.0 if seed == 1 and score == 90.0 else 0.0),
                        }
                    )
        ecology = _orientation_ecology(rows, frozenset({0, 1}))
        self.assertEqual(4, ecology["cell_count"])
        self.assertTrue(ecology["has_reversion_side_winner"])
        self.assertTrue(ecology["has_continuation_side_winner"])
        self.assertGreaterEqual(ecology["winner_score_count"], 2)


if __name__ == "__main__":
    unittest.main()
