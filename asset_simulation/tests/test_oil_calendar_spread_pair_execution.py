from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_calendar_spread_pair_execution import (
    OIL_CALENDAR_SPREAD_PAIR_EXECUTION_MODEL_VERSION,
    execute_oil_calendar_spread_pair_turn,
)
from asset_simulation.model.oil_calendar_spread_strategy_v2 import (
    OIL_CALENDAR_SPREAD_STRATEGY_V2_ID,
)
from asset_simulation.model.oil_calendar_spread_strategy_v22 import (
    build_oil_calendar_spread_strategy_v22_decision,
)
from asset_simulation.model.oil_execution_desk import (
    CAPABILITY_DIMENSIONS,
    STYLE_DIMENSIONS,
    _pack as build_execution_profile,
)
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_short_term_forecast import (
    generate_institution_profile_for_score_range,
    generate_oil_short_term_forecast,
)
from asset_simulation.model.oil_strategy_book import build_oil_strategy_book
from asset_simulation.model.registry import load_registered_assets


class OilCalendarSpreadPairExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.macro_run = run_global_macro(42, 8)
        cls.start_market = oil_futures_payload(
            cls.macro_run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        cls.end_market = oil_futures_payload(
            cls.macro_run, as_of_year=2030, as_of_month=1, as_of_half=2
        )
        forecast_profile = generate_institution_profile_for_score_range(
            seed=20260831,
            score_min=55.0,
            score_max=65.0,
        )
        cls.forecast = generate_oil_short_term_forecast(
            cls.macro_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
            institution_profile=forecast_profile,
            previous_vintage=None,
        )

    @staticmethod
    def _book(positions: dict[str, int] | None = None) -> dict[str, object]:
        return build_oil_strategy_book(
            institution_id="PAIR-EXEC-PLAYER",
            strategy_id=OIL_CALENDAR_SPREAD_STRATEGY_V2_ID,
            positions=positions or {},
        )

    def _decision(self, book: dict[str, object]) -> dict[str, object]:
        return build_oil_calendar_spread_strategy_v22_decision(
            self.start_market,
            self.forecast,
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=book,
        )

    @staticmethod
    def _execution_profile(score: float) -> dict[str, object]:
        return build_execution_profile(
            personnel_id=f"PAIR-EXEC-{int(score)}",
            display_name=f"PAIR-EXEC-{int(score)}",
            capability_radar={key: score for key in CAPABILITY_DIMENSIONS},
            execution_style={key: 50.0 for key in STYLE_DIMENSIONS},
            candidate_index=None,
            generation_seed=None,
            source="test_controlled_profile",
        )

    def test_registered_assets_and_real_leg_execution_are_deterministic(self) -> None:
        assets = load_registered_assets()
        self.assertEqual(
            OIL_CALENDAR_SPREAD_PAIR_EXECUTION_MODEL_VERSION,
            assets["oil_calendar_spread_pair_execution_config"]["model_version"],
        )
        book = self._book()
        decision = self._decision(book)
        start_before = deepcopy(self.start_market)
        end_before = deepcopy(self.end_market)
        book_before = deepcopy(book)
        first = execute_oil_calendar_spread_pair_turn(
            self.start_market,
            self.end_market,
            decision,
            strategy_book=book,
        )
        second = execute_oil_calendar_spread_pair_turn(
            self.start_market,
            self.end_market,
            decision,
            strategy_book=book,
        )
        self.assertEqual(first, second)
        self.assertEqual(start_before, self.start_market)
        self.assertEqual(end_before, self.end_market)
        self.assertEqual(book_before, book)
        self.assertEqual(2, len(first["weeklyWindows"]))
        self.assertFalse(first["pairExecution"]["synthetic_spread_fill_created"])
        self.assertTrue(first["pairExecution"]["new_pair_fills_balanced_within_each_window"])
        self.assertEqual(
            first["mandate"]["requested_remediation_main_delta_lots"]
            + first["completion"]["executed_pair_units"],
            first["legs"]["main"]["executed_delta_lots"],
        )
        self.assertEqual(
            first["mandate"]["requested_remediation_next_main_delta_lots"]
            - first["completion"]["executed_pair_units"],
            first["legs"]["next_main"]["executed_delta_lots"],
        )
        self.assertAlmostEqual(
            first["costs"]["execution_cost_usd"],
            first["costs"]["spread_cost_usd"]
            + first["costs"]["slippage_cost_usd"]
            + first["costs"]["net_fee_usd"],
            places=5,
        )
        self.assertFalse(
            first["strategyBookSettlementPreview"]["strategy_book_mutated"]
        )
        self.assertFalse(
            first["strategyBookSettlementPreview"]["formal_account_mutated"]
        )

    def test_execution_skill_changes_completion_and_cost_not_frozen_mandate(self) -> None:
        book = self._book()
        decision = self._decision(book)
        requested = abs(int(decision["pairedExecutionMandate"]["requested_pair_delta_units"]))
        self.assertGreater(requested, 0)
        low = execute_oil_calendar_spread_pair_turn(
            self.start_market,
            self.end_market,
            decision,
            strategy_book=book,
            execution_desk_profile=self._execution_profile(0.0),
        )
        high = execute_oil_calendar_spread_pair_turn(
            self.start_market,
            self.end_market,
            decision,
            strategy_book=book,
            execution_desk_profile=self._execution_profile(100.0),
        )
        self.assertEqual(
            low["mandate"]["requested_pair_delta_units"],
            high["mandate"]["requested_pair_delta_units"],
        )
        self.assertLessEqual(
            abs(int(low["completion"]["executed_pair_units"])), requested
        )
        self.assertLessEqual(
            abs(int(high["completion"]["executed_pair_units"])), requested
        )
        self.assertGreaterEqual(
            high["completion"]["pair_completion_ratio"],
            low["completion"]["pair_completion_ratio"],
        )
        self.assertLessEqual(
            high["costs"]["execution_cost_usd"],
            high["costs"]["neutral_execution_cost_usd"] + 1e-6,
        )
        self.assertGreaterEqual(
            low["costs"]["execution_cost_usd"],
            low["costs"]["neutral_execution_cost_usd"] - 1e-6,
        )
        self.assertFalse(high["completion"]["execution_can_expand_authorized_order"])

    def test_residual_remediation_is_mandatory_and_book_local(self) -> None:
        main_id = str(self.start_market["curve"]["main_contract_id"])
        ids = [
            str(item["contract_id"])
            for item in self.start_market["curve"]["contracts"]
        ]
        next_id = ids[ids.index(main_id) + 1]
        book = self._book({main_id: 50, next_id: -40})
        decision = self._decision(book)
        mandate = decision["pairedExecutionMandate"]
        self.assertEqual(-10, mandate["imbalanceRemediation"]["requested_main_delta_lots"])
        report = execute_oil_calendar_spread_pair_turn(
            self.start_market,
            self.end_market,
            decision,
            strategy_book=book,
            execution_desk_profile=self._execution_profile(0.0),
        )
        self.assertTrue(
            report["completion"]["mandatory_remediation_bypassed_style_completion"]
        )
        remediation_fills = [
            fill
            for fill in report["legs"]["main"]["fills"]
            if fill["bucket"] == "mandatory_residual_remediation"
        ]
        self.assertEqual(-10, sum(int(fill["delta_lots"]) for fill in remediation_fills))
        self.assertEqual(
            report["strategyBookSettlementPreview"]["book_identity_hash_before"],
            book["identity"]["identity_hash"],
        )

    def test_changed_book_or_nonadjacent_settlement_is_rejected(self) -> None:
        book = self._book()
        decision = self._decision(book)
        main_id = str(decision["legs"]["main"]["contract_id"])
        changed_book = self._book({main_id: 1})
        with self.assertRaisesRegex(ValueError, "changed after decision freeze"):
            execute_oil_calendar_spread_pair_turn(
                self.start_market,
                self.end_market,
                decision,
                strategy_book=changed_book,
            )
        later = oil_futures_payload(
            self.macro_run, as_of_year=2030, as_of_month=2, as_of_half=1
        )
        with self.assertRaisesRegex(ValueError, "adjacent half-month"):
            execute_oil_calendar_spread_pair_turn(
                self.start_market,
                later,
                decision,
                strategy_book=book,
            )


if __name__ == "__main__":
    unittest.main()
