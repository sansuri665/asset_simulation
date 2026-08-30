from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.oil_calendar_spread_strategy import (
    build_oil_calendar_spread_research_decision,
)
from asset_simulation.model.oil_calendar_spread_strategy_v2 import (
    OIL_CALENDAR_SPREAD_STRATEGY_V2_ID,
    OIL_CALENDAR_SPREAD_STRATEGY_V2_MODEL_VERSION,
    build_oil_calendar_spread_strategy_v2_decision,
)
from asset_simulation.model.oil_strategy_book import (
    aggregate_oil_strategy_books,
    build_oil_strategy_book,
)
from asset_simulation.model.oil_strategy_research import (
    build_default_oil_strategy_research_profile,
)
from asset_simulation.model.registry import load_registered_assets
from asset_simulation.tests.test_oil_calendar_spread_strategy import _forecast, _market


class OilCalendarSpreadStrategyV2Tests(unittest.TestCase):
    def _book(self, positions: dict[str, int] | None = None) -> dict[str, object]:
        return build_oil_strategy_book(
            institution_id="PLAYER",
            strategy_id=OIL_CALENDAR_SPREAD_STRATEGY_V2_ID,
            positions=positions or {},
        )

    def test_registered_taxonomy_and_decision_identity(self) -> None:
        assets = load_registered_assets()
        config = assets["oil_calendar_spread_strategy_v2_config"]
        self.assertEqual(OIL_CALENDAR_SPREAD_STRATEGY_V2_MODEL_VERSION, config["model_version"])
        self.assertEqual("crude_oil", config["strategy_taxonomy"]["commodity"])
        self.assertEqual("short_horizon", config["strategy_taxonomy"]["time_scale"])
        self.assertEqual(
            "relative_value_calendar_spread",
            config["strategy_taxonomy"]["strategy_type"],
        )
        decision = build_oil_calendar_spread_strategy_v2_decision(
            _market(),
            _forecast(),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book(),
        )
        self.assertEqual(OIL_CALENDAR_SPREAD_STRATEGY_V2_ID, decision["strategy"]["strategy_id"])
        self.assertEqual(
            "research_candidate_not_default_competition_engine",
            decision["strategy"]["runtime_status"],
        )
        self.assertFalse(
            decision["strategyBook"]["aggregate_account_positions_consumed"]
        )
        self.assertTrue(decision["informationPolicy"]["strategy_owned_book_only"])

    def test_default_score_100_construction_reproduces_reference_target(self) -> None:
        market = _market()
        forecast = _forecast()
        reference = build_oil_calendar_spread_research_decision(
            market,
            forecast,
            authorized_strategy_capital_usd=10_000_000.0,
        )
        decision = build_oil_calendar_spread_strategy_v2_decision(
            market,
            forecast,
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book(),
        )
        construction = decision["construction"]
        self.assertEqual(0.0, construction["pair_target_scale_error"])
        self.assertEqual(0.0, construction["pair_transition_gap_error"])
        self.assertEqual(0.0, construction["curve_lifecycle_planning_error"])
        self.assertEqual(
            reference["target"]["target_spread_units"],
            decision["target"]["target_spread_units"],
        )
        self.assertTrue(
            construction["reference_target_reproduced_when_all_errors_zero"]
        )

    def test_low_construction_capability_is_deterministic_and_pair_safe(self) -> None:
        profile = deepcopy(build_default_oil_strategy_research_profile())
        profile.pop("profile_hash")
        profile["construction_capability_radar"] = {
            "exposure_construction": 0.0,
            "transition_planning": 0.0,
            "contract_lifecycle_planning": 0.0,
        }
        first = build_oil_calendar_spread_strategy_v2_decision(
            _market(),
            _forecast(future_main=(85.0, 90.0), future_next=(65.0, 62.0)),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book(),
            strategy_research_profile=profile,
        )
        repeat = build_oil_calendar_spread_strategy_v2_decision(
            _market(),
            _forecast(future_main=(85.0, 90.0), future_next=(65.0, 62.0)),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book(),
            strategy_research_profile=profile,
        )
        self.assertEqual(first, repeat)
        self.assertEqual(
            0,
            first["target"]["target_main_lots"]
            + first["target"]["target_next_main_lots"],
        )
        self.assertEqual(
            0,
            first["strategyRiskAdapter"]["target"]["absolute_leg_imbalance_lots"],
        )
        self.assertFalse(first["construction"]["construction_created_or_reversed_signal"])
        self.assertFalse(first["construction"]["curve_lifecycle_error_applied_to_target"])
        errors = (
            abs(first["construction"]["pair_target_scale_error"]),
            abs(first["construction"]["pair_transition_gap_error"]),
            abs(first["construction"]["curve_lifecycle_planning_error"]),
        )
        self.assertGreater(max(errors), 0.0)

    def test_strategy_book_prevents_directional_position_from_becoming_spread_residual(self) -> None:
        directional = build_oil_strategy_book(
            institution_id="PLAYER",
            strategy_id="oil.short.directional.v1",
            positions={"OIL-3005": 100},
        )
        spread = self._book({"OIL-3005": 50, "OIL-3009": -50})
        aggregate = aggregate_oil_strategy_books([directional, spread])
        self.assertEqual(
            {"OIL-3005": 150, "OIL-3009": -50}, aggregate["account_positions"]
        )
        decision = build_oil_calendar_spread_strategy_v2_decision(
            _market(),
            _forecast(),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=spread,
        )
        current = decision["strategyRiskAdapter"]["current"]
        self.assertEqual(50, current["spread_units"])
        self.assertEqual(0, current["residual_main_lots"])
        self.assertEqual(0, current["residual_next_main_lots"])
        self.assertEqual(50, decision["legs"]["main"]["current_position_lots"])
        self.assertEqual(-50, decision["legs"]["next_main"]["current_position_lots"])

    def test_only_this_strategy_books_residuals_are_remediated(self) -> None:
        spread = self._book({"OIL-3005": 100})
        decision = build_oil_calendar_spread_strategy_v2_decision(
            _market(main_turn_limit=10, next_turn_limit=8),
            _forecast(future_main=(90.0, 95.0), future_next=(60.0, 55.0)),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=spread,
        )
        remediation = decision["pairedExecutionMandate"]["imbalanceRemediation"]
        self.assertTrue(remediation["required"])
        self.assertEqual(100, remediation["current_residual_main_lots"])
        self.assertEqual(-10, remediation["requested_main_delta_lots"])
        self.assertEqual(90, remediation["remaining_residual_main_lots_after_request"])

    def test_wrong_strategy_book_is_rejected(self) -> None:
        wrong = build_oil_strategy_book(
            institution_id="PLAYER",
            strategy_id="oil.short.directional.v1",
            positions={"OIL-3005": 1},
        )
        with self.assertRaisesRegex(ValueError, "different strategy"):
            build_oil_calendar_spread_strategy_v2_decision(
                _market(),
                _forecast(),
                authorized_strategy_capital_usd=10_000_000.0,
                strategy_book=wrong,
            )

    def test_construction_cannot_cross_through_zero_on_reversal(self) -> None:
        profile = deepcopy(build_default_oil_strategy_research_profile())
        profile.pop("profile_hash")
        profile["construction_capability_radar"] = {
            "exposure_construction": 0.0,
            "transition_planning": 0.0,
            "contract_lifecycle_planning": 0.0,
        }
        decision = build_oil_calendar_spread_strategy_v2_decision(
            _market(),
            _forecast(future_main=(65.0, 64.0), future_next=(75.0, 76.0)),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=self._book({"OIL-3005": 100, "OIL-3009": -100}),
            strategy_research_profile=profile,
            thesis_state={"status": "active", "last_signal": 0.8},
        )
        submitted = decision["target"]["construction_submitted_target_spread_units"]
        self.assertGreaterEqual(submitted, 0)
        self.assertGreaterEqual(decision["target"]["target_spread_units"], 0)
        self.assertEqual(
            "exit_before_direction_reversal",
            decision["thesisInvalidation"]["action"]["action"],
        )


if __name__ == "__main__":
    unittest.main()
