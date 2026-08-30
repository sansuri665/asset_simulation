from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.oil_calendar_spread_strategy_v2 import (
    OIL_CALENDAR_SPREAD_STRATEGY_V2_ID,
    build_oil_calendar_spread_strategy_v2_decision,
)
from asset_simulation.model.oil_calendar_spread_strategy_v22 import (
    OIL_CALENDAR_SPREAD_STRATEGY_V22_MODEL_VERSION,
    build_oil_calendar_spread_strategy_v22_decision,
)
from asset_simulation.model.oil_strategy_book import build_oil_strategy_book
from asset_simulation.tests.test_oil_calendar_spread_strategy import _forecast, _market


class OilCalendarSpreadStrategyV22Tests(unittest.TestCase):
    @staticmethod
    def _book() -> dict[str, object]:
        return build_oil_strategy_book(
            institution_id="PLAYER",
            strategy_id=OIL_CALENDAR_SPREAD_STRATEGY_V2_ID,
            positions={},
        )

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

    def test_v022_restores_visible_history_that_v021_dropped(self) -> None:
        market = self._market_owner_shape()
        forecast = _forecast()
        old = build_oil_calendar_spread_strategy_v2_decision(
            market,
            forecast,
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book(),
        )
        fixed = build_oil_calendar_spread_strategy_v22_decision(
            market,
            forecast,
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book(),
        )
        self.assertEqual(1, old["signal"]["historical_observation_count"])
        self.assertGreaterEqual(fixed["signal"]["historical_observation_count"], 6)
        self.assertTrue(fixed["signal"]["mean_reversion_available"])
        self.assertEqual(
            OIL_CALENDAR_SPREAD_STRATEGY_V22_MODEL_VERSION,
            fixed["identity"]["model_version"],
        )
        self.assertTrue(
            fixed["informationPolicy"]["market_history_metadata_only_adapter"]
        )
        self.assertFalse(fixed["marketHistoryAdapter"]["prices_modified"])

    def test_v022_keeps_exact_pair_and_strategy_book_ownership(self) -> None:
        fixed = build_oil_calendar_spread_strategy_v22_decision(
            self._market_owner_shape(),
            _forecast(future_main=(85.0, 90.0), future_next=(65.0, 62.0)),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book(),
        )
        self.assertEqual(
            0,
            fixed["target"]["target_main_lots"]
            + fixed["target"]["target_next_main_lots"],
        )
        self.assertFalse(
            fixed["strategyBook"]["aggregate_account_positions_consumed"]
        )
        self.assertEqual(
            fixed["strategyBook"]["book_identity_hash"],
            fixed["identity"]["strategy_book_identity_hash"],
        )

    def test_v022_is_deterministic_and_does_not_mutate_market(self) -> None:
        market = self._market_owner_shape()
        original = deepcopy(market)
        first = build_oil_calendar_spread_strategy_v22_decision(
            market,
            _forecast(),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book(),
        )
        second = build_oil_calendar_spread_strategy_v22_decision(
            market,
            _forecast(),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book(),
        )
        self.assertEqual(first, second)
        self.assertEqual(original, market)


if __name__ == "__main__":
    unittest.main()
