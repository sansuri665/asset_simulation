from __future__ import annotations

import math
import unittest

from asset_simulation.audit_oil_direction_regimes import (
    classify_realized_regimes,
)


def _prices(moves: list[float]) -> list[float]:
    values = [100.0]
    for move in moves:
        values.append(values[-1] * math.exp(move))
    return values


class OilDirectionRegimeAuditTests(unittest.TestCase):
    def test_classifier_separates_trend_range_and_turning(self) -> None:
        trend = classify_realized_regimes(_prices([0.012, 0.012, 0.012, 0.012]))
        ranging = classify_realized_regimes(_prices([0.02, -0.02, 0.02, -0.02]))
        turning = classify_realized_regimes(_prices([0.02, 0.02, -0.02, -0.02]))
        self.assertEqual("trend", trend[-1])
        self.assertEqual("range", ranging[-1])
        self.assertEqual("turning", turning[-1])
        self.assertEqual(["warmup"] * 3, trend[:3])

    def test_classifier_rejects_nonpositive_prices(self) -> None:
        with self.assertRaises(ValueError):
            classify_realized_regimes([100.0, 0.0])


if __name__ == "__main__":
    unittest.main()
