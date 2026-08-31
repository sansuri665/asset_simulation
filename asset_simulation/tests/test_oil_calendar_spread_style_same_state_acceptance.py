from __future__ import annotations

import unittest

from asset_simulation.audit_oil_calendar_spread_style_same_state_acceptance import (
    _final_signal,
    _partition,
    _retention,
)
from asset_simulation.model.oil_calendar_spread_research import (
    CALENDAR_SPREAD_STYLE_DIMENSIONS,
)


class OilCalendarSpreadStyleSameStateAcceptanceTests(unittest.TestCase):
    def test_deadband_and_retention_helpers_are_directionally_stable(self) -> None:
        self.assertEqual(0.0, _final_signal(0.1, 0.15))
        self.assertGreater(_final_signal(0.5, 0.15), 0.0)
        self.assertLess(_final_signal(-0.5, 0.15), 0.0)
        self.assertLess(_retention(100, 40, 50), _retention(100, 40, 80))

    def test_one_seed_partition_uses_one_neutral_state_path_for_all_axes(self) -> None:
        report = _partition(
            (0,),
            horizon_years=1,
            authorized_strategy_capital_usd=10_000_000.0,
        )
        self.assertEqual(24, report["neutral_turn_count"])
        self.assertGreaterEqual(report["pair_identity_reset_count"], 1)
        self.assertEqual(
            set(CALENDAR_SPREAD_STYLE_DIMENSIONS),
            set(report["axis_summaries"]),
        )
        for summary in report["axis_summaries"].values():
            self.assertEqual(24, summary["turn_count"])
            self.assertIn("low_value", summary)
            self.assertIn("high_value", summary)


if __name__ == "__main__":
    unittest.main()
