from __future__ import annotations

import unittest

from asset_simulation.audit_oil_calendar_spread_style_economic_acceptance import (
    _controlled_decision_with_reversal_guard,
)
from asset_simulation.audit_oil_calendar_spread_style_economics import _controlled_radar
from asset_simulation.tests.test_oil_calendar_spread_strategy import _forecast, _market


def _market_owner_shape() -> dict[str, object]:
    market = _market()
    for contract in market["curve"]["contracts"]:
        for month in contract.get("monthly", []):
            for week in month.get("weekly", []):
                week.pop("week_serial", None)
                week.pop("year", None)
                week.pop("month", None)
    return market


class OilCalendarSpreadStyleEconomicAcceptanceTests(unittest.TestCase):
    def test_reversal_exits_existing_spread_before_crossing_zero(self) -> None:
        decision = _controlled_decision_with_reversal_guard(
            _market_owner_shape(),
            _forecast(future_main=(65.0, 64.0), future_next=(75.0, 76.0)),
            current_spread_units=100,
            dedicated_radar=_controlled_radar("forecast_vs_visible_curve", 90.0),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        self.assertLess(decision["ideal_target_spread_units"], 0)
        self.assertTrue(decision["reversal_exit_applied"])
        self.assertEqual(0, decision["persistent_target_spread_units"])
        self.assertGreaterEqual(decision["target_spread_units"], 0)
        self.assertGreater(
            decision["signal"]["historical_observation_count"], 1
        )


if __name__ == "__main__":
    unittest.main()
