from __future__ import annotations

import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_price_projection import (
    OIL_PRICE_PROJECTION_MODEL_VERSION,
    run_oil_price_projection,
)


class OilPriceProjectionTests(unittest.TestCase):
    def test_determinism_prefix_and_annual_anchors(self) -> None:
        short = run_oil_price_projection(run_global_macro(42, 5))
        repeat = run_oil_price_projection(run_global_macro(42, 5))
        longer = run_oil_price_projection(run_global_macro(42, 60))
        other = run_oil_price_projection(run_global_macro(7, 5))

        self.assertEqual(OIL_PRICE_PROJECTION_MODEL_VERSION, short.identity["model_version"])
        self.assertEqual(short.identity["identity_hash"], repeat.identity["identity_hash"])
        self.assertNotEqual(short.identity["result_hash"], other.identity["result_hash"])
        self.assertEqual(short.annual, longer.annual[: len(short.annual)])
        self.assertEqual(short.monthly, longer.monthly[: len(short.monthly)])
        self.assertFalse(short.identity["write_back"])

        self.assertEqual(6, len(short.annual))
        self.assertEqual(6 * 12, len(short.monthly))
        for index, annual in enumerate(short.annual):
            months = short.monthly[index * 12 : (index + 1) * 12]
            self.assertEqual(list(range(1, 13)), [row["month"] for row in months])
            self.assertAlmostEqual(months[0]["open_usd_per_bbl"], annual["open_usd_per_bbl"], places=6)
            self.assertAlmostEqual(months[-1]["close_usd_per_bbl"], annual["close_usd_per_bbl"], places=6)
            self.assertAlmostEqual(max(row["high_usd_per_bbl"] for row in months), annual["high_usd_per_bbl"], places=6)
            self.assertAlmostEqual(min(row["low_usd_per_bbl"] for row in months), annual["low_usd_per_bbl"], places=6)
            for left, right in zip(months, months[1:]):
                self.assertAlmostEqual(left["close_usd_per_bbl"], right["open_usd_per_bbl"], places=6)
            for month in months:
                self.assertLessEqual(month["low_usd_per_bbl"], min(month["open_usd_per_bbl"], month["close_usd_per_bbl"]))
                self.assertGreaterEqual(month["high_usd_per_bbl"], max(month["open_usd_per_bbl"], month["close_usd_per_bbl"]))


if __name__ == "__main__":
    unittest.main()
