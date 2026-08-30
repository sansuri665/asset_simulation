from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.oil_calendar_spread_market_history import (
    normalize_oil_calendar_spread_market_history,
)
from asset_simulation.tests.test_oil_calendar_spread_strategy import _market


class OilCalendarSpreadMarketHistoryTests(unittest.TestCase):
    @staticmethod
    def _market_owner_shape() -> dict[str, object]:
        market = _market()
        for contract in market["curve"]["contracts"]:
            for month in contract.get("monthly", []):
                for week in month.get("weekly", []):
                    week.pop("week_serial", None)
                    week.pop("year", None)
                    week.pop("month", None)
        return market

    def test_parent_month_coordinates_are_inherited_without_price_changes(self) -> None:
        market = self._market_owner_shape()
        original = deepcopy(market)
        normalized, report = normalize_oil_calendar_spread_market_history(market)
        self.assertEqual(original, market)
        self.assertEqual(original["identity"], normalized["identity"])
        self.assertEqual(original["asOf"], normalized["asOf"])
        self.assertFalse(report["prices_modified"])
        self.assertFalse(report["liquidity_modified"])
        self.assertFalse(report["market_write_back"])
        self.assertGreater(report["weekly_year_coordinates_inherited"], 0)
        self.assertEqual(
            report["weekly_year_coordinates_inherited"],
            report["weekly_month_coordinates_inherited"],
        )

        for contract in normalized["curve"]["contracts"]:
            for month in contract.get("monthly", []):
                for week in month.get("weekly", []):
                    self.assertEqual(month["year"], week["year"])
                    self.assertEqual(month["month"], week["month"])

    def test_conflicting_explicit_week_coordinates_are_rejected(self) -> None:
        market = self._market_owner_shape()
        first_month = market["curve"]["contracts"][0]["monthly"][0]
        first_month["weekly"][0]["year"] = int(first_month["year"]) + 1
        with self.assertRaisesRegex(ValueError, "conflicts with parent"):
            normalize_oil_calendar_spread_market_history(market)

    def test_adapter_is_deterministic(self) -> None:
        market = self._market_owner_shape()
        first = normalize_oil_calendar_spread_market_history(market)
        second = normalize_oil_calendar_spread_market_history(market)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
