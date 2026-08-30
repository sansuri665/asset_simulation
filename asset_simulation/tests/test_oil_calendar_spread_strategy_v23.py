from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.oil_calendar_spread_strategy_v2 import (
    OIL_CALENDAR_SPREAD_STRATEGY_V2_ID,
)
from asset_simulation.model.oil_calendar_spread_strategy_v23 import (
    OIL_CALENDAR_SPREAD_STRATEGY_V23_MODEL_VERSION,
    build_oil_calendar_spread_strategy_v23_decision,
)
from asset_simulation.model.oil_strategy_book import build_oil_strategy_book
from asset_simulation.model.oil_strategy_research import (
    build_default_oil_strategy_research_profile,
)
from asset_simulation.model.registry import load_registered_assets
from asset_simulation.tests.test_oil_calendar_spread_strategy import _forecast, _market


class OilCalendarSpreadStrategyV23Tests(unittest.TestCase):
    @staticmethod
    def _book(positions: dict[str, int] | None = None) -> dict[str, object]:
        return build_oil_strategy_book(
            institution_id="V23-PLAYER",
            strategy_id=OIL_CALENDAR_SPREAD_STRATEGY_V2_ID,
            positions=positions or {},
        )

    @staticmethod
    def _market_owner_shape() -> dict[str, object]:
        """Use the same parent-month/child-week shape published by the market owner."""

        market = _market()
        for contract in market["curve"]["contracts"]:
            monthly = contract.get("monthly", [])
            if not monthly:
                continue
            source_weeks = [deepcopy(week) for week in monthly[0].get("weekly", [])]
            rebuilt_months = []
            for block_index in range(0, len(source_weeks), 4):
                block = source_weeks[block_index : block_index + 4]
                parent_month = 10 + block_index // 4
                normalized_block = []
                for week_index, week in enumerate(block, start=1):
                    week.pop("week_serial", None)
                    week.pop("year", None)
                    week.pop("month", None)
                    week["week"] = week_index
                    normalized_block.append(week)
                rebuilt_months.append(
                    {
                        "year": 2029,
                        "month": parent_month,
                        "weekly": normalized_block,
                    }
                )
            contract["monthly"] = rebuilt_months
        return market

    def test_registered_v023_is_deterministic(self) -> None:
        assets = load_registered_assets()
        self.assertEqual(
            OIL_CALENDAR_SPREAD_STRATEGY_V23_MODEL_VERSION,
            assets["oil_calendar_spread_strategy_v23_config"]["model_version"],
        )
        book = self._book()
        market = self._market_owner_shape()
        first = build_oil_calendar_spread_strategy_v23_decision(
            market,
            _forecast(),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=book,
        )
        second = build_oil_calendar_spread_strategy_v23_decision(
            market,
            _forecast(),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=book,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            OIL_CALENDAR_SPREAD_STRATEGY_V23_MODEL_VERSION,
            first["identity"]["model_version"],
        )

    def test_zero_error_construction_is_identity_with_nonzero_book(self) -> None:
        decision = build_oil_calendar_spread_strategy_v23_decision(
            self._market_owner_shape(),
            _forecast(future_main=(85.0, 90.0), future_next=(65.0, 62.0)),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book({"OIL-3005": 40, "OIL-3009": -40}),
        )
        construction = decision["construction"]
        self.assertTrue(construction["zero_error_construction_identity"])
        self.assertEqual(
            construction["ideal_policy_target_spread_units"],
            construction["construction_submitted_target_spread_units"],
        )
        self.assertEqual(
            decision["target"]["reference_target_spread_units"],
            decision["target"]["target_spread_units"],
        )

    def test_zero_error_reversal_remains_visible_until_thesis_exits_first(self) -> None:
        current_units = 100
        decision = build_oil_calendar_spread_strategy_v23_decision(
            self._market_owner_shape(),
            _forecast(future_main=(65.0, 64.0), future_next=(75.0, 76.0)),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book(
                {"OIL-3005": current_units, "OIL-3009": -current_units}
            ),
            thesis_state={"status": "active", "last_signal": 0.8},
        )
        construction = decision["construction"]
        self.assertTrue(construction["zero_error_construction_identity"])
        self.assertLess(construction["ideal_policy_target_spread_units"], 0)
        self.assertEqual(
            construction["ideal_policy_target_spread_units"],
            construction["construction_submitted_target_spread_units"],
        )
        self.assertFalse(construction["thesis_reversal_policy_applied_here"])
        self.assertEqual(
            "exit_before_direction_reversal",
            decision["thesisInvalidation"]["action"]["action"],
        )
        self.assertEqual(0, decision["target"]["thesis_adjusted_target_spread_units"])
        self.assertGreaterEqual(decision["target"]["target_spread_units"], 0)
        self.assertLess(decision["target"]["target_spread_units"], current_units)
        self.assertEqual(
            decision["target"]["reference_target_spread_units"],
            decision["target"]["target_spread_units"],
        )

    def test_nonzero_construction_error_retains_reversal_safety_guard(self) -> None:
        profile = deepcopy(build_default_oil_strategy_research_profile())
        profile.pop("profile_hash")
        profile["construction_capability_radar"] = {
            "exposure_construction": 0.0,
            "transition_planning": 0.0,
            "contract_lifecycle_planning": 0.0,
        }
        decision = build_oil_calendar_spread_strategy_v23_decision(
            self._market_owner_shape(),
            _forecast(future_main=(65.0, 64.0), future_next=(75.0, 76.0)),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book({"OIL-3005": 100, "OIL-3009": -100}),
            strategy_research_profile=profile,
            thesis_state={"status": "active", "last_signal": 0.8},
        )
        construction = decision["construction"]
        self.assertFalse(construction["zero_error_construction_identity"])
        self.assertGreaterEqual(
            construction["construction_submitted_target_spread_units"], 0
        )
        self.assertIn(
            construction["direction_guard_action"],
            {"nonzero_error_reversal_guard", "unchanged"},
        )
        self.assertGreaterEqual(decision["target"]["target_spread_units"], 0)


if __name__ == "__main__":
    unittest.main()
