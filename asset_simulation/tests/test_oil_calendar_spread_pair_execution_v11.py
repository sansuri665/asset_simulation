from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_calendar_spread_pair_execution_v11 import (
    OIL_CALENDAR_SPREAD_PAIR_EXECUTION_V11_MODEL_VERSION,
    execute_oil_calendar_spread_pair_turn_v11,
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


class OilCalendarSpreadPairExecutionV11Tests(unittest.TestCase):
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
            institution_id="PAIR-EXEC-V11",
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
            personnel_id=f"PAIR-V11-{int(score)}",
            display_name=f"PAIR-V11-{int(score)}",
            capability_radar={key: score for key in CAPABILITY_DIMENSIONS},
            execution_style={key: 50.0 for key in STYLE_DIMENSIONS},
            candidate_index=None,
            generation_seed=None,
            source="test_controlled_profile",
        )

    def test_registered_v011_executes_real_balanced_weekly_legs(self) -> None:
        assets = load_registered_assets()
        self.assertEqual(
            OIL_CALENDAR_SPREAD_PAIR_EXECUTION_V11_MODEL_VERSION,
            assets["oil_calendar_spread_pair_execution_v11_config"]["model_version"],
        )
        book = self._book()
        decision = self._decision(book)
        first = execute_oil_calendar_spread_pair_turn_v11(
            self.start_market,
            self.end_market,
            decision,
            strategy_book=book,
        )
        second = execute_oil_calendar_spread_pair_turn_v11(
            self.start_market,
            self.end_market,
            decision,
            strategy_book=book,
        )
        self.assertEqual(first, second)
        self.assertEqual(2, len(first["weeklyWindows"]))
        self.assertTrue(
            first["informationPolicy"][
                "schedule_frozen_from_predecision_visible_liquidity"
            ]
        )
        self.assertFalse(
            first["informationPolicy"]["future_window_volume_used_for_schedule"]
        )
        for window in first["weeklyWindows"]:
            self.assertEqual(
                int(window["main_pair_delta_lots"]),
                -int(window["next_main_pair_delta_lots"]),
            )
            self.assertFalse(window["realized_volume_used_for_schedule"])
        self.assertFalse(first["pairExecution"]["synthetic_spread_fill_created"])
        self.assertLessEqual(
            first["legs"]["main"]["gross_turnover_lots"],
            first["mandate"]["main_turn_liquidity_lots"],
        )
        self.assertLessEqual(
            first["legs"]["next_main"]["gross_turnover_lots"],
            first["mandate"]["next_main_turn_liquidity_lots"],
        )

    def test_future_realized_volume_cannot_change_frozen_schedule_or_fill_split(self) -> None:
        book = self._book()
        decision = self._decision(book)
        baseline = execute_oil_calendar_spread_pair_turn_v11(
            self.start_market,
            self.end_market,
            decision,
            strategy_book=book,
        )
        altered_end = deepcopy(self.end_market)
        for contract in altered_end["curve"]["contracts"]:
            for month in contract.get("monthly", []):
                if int(month["year"]) == 2030 and int(month["month"]) == 1:
                    for week in month.get("weekly", []):
                        if int(week["week"]) in (3, 4) and "volume_lots" in week:
                            multiplier = 25 if int(week["week"]) == 3 else 1
                            week["volume_lots"] = max(
                                1, int(week["volume_lots"]) * multiplier
                            )
        altered = execute_oil_calendar_spread_pair_turn_v11(
            self.start_market,
            altered_end,
            decision,
            strategy_book=book,
        )
        self.assertEqual(
            baseline["schedule"]["schedule_input_hash"],
            altered["schedule"]["schedule_input_hash"],
        )
        self.assertEqual(
            baseline["schedule"]["pair_execution_weights"],
            altered["schedule"]["pair_execution_weights"],
        )
        self.assertEqual(
            [row["pair_units"] for row in baseline["weeklyWindows"]],
            [row["pair_units"] for row in altered["weeklyWindows"]],
        )
        # Realized volume is still allowed to change market impact/cost once the
        # window exists; it simply cannot rewrite the schedule retroactively.
        self.assertNotEqual(
            baseline["costs"]["slippage_cost_usd"],
            altered["costs"]["slippage_cost_usd"],
        )

    def test_pair_execution_price_excludes_residual_remediation_fills(self) -> None:
        main_id = str(self.start_market["curve"]["main_contract_id"])
        ids = [str(item["contract_id"]) for item in self.start_market["curve"]["contracts"]]
        next_id = ids[ids.index(main_id) + 1]
        book = self._book({main_id: 50, next_id: -40})
        decision = self._decision(book)
        report = execute_oil_calendar_spread_pair_turn_v11(
            self.start_market,
            self.end_market,
            decision,
            strategy_book=book,
        )
        self.assertEqual("new_pair_only", report["pairExecution"]["execution_price_bucket"])
        if report["completion"]["executed_pair_units"]:
            expected_neutral = (
                report["legs"]["main"]["newPair"][
                    "average_neutral_execution_price_usd"
                ]
                - report["legs"]["next_main"]["newPair"][
                    "average_neutral_execution_price_usd"
                ]
            )
            expected_all_in = (
                report["legs"]["main"]["newPair"][
                    "average_all_in_execution_price_usd"
                ]
                - report["legs"]["next_main"]["newPair"][
                    "average_all_in_execution_price_usd"
                ]
            )
            self.assertAlmostEqual(
                expected_neutral,
                report["pairExecution"]["neutral_pair_execution_spread_usd_per_bbl"],
                places=7,
            )
            self.assertAlmostEqual(
                expected_all_in,
                report["pairExecution"]["all_in_pair_execution_spread_usd_per_bbl"],
                places=7,
            )
        self.assertEqual(
            -10,
            report["remediation"]["executed_main_delta_lots"],
        )

    def test_execution_capability_changes_completion_and_tca_not_mandate(self) -> None:
        book = self._book()
        decision = self._decision(book)
        low = execute_oil_calendar_spread_pair_turn_v11(
            self.start_market,
            self.end_market,
            decision,
            strategy_book=book,
            execution_desk_profile=self._execution_profile(0.0),
        )
        high = execute_oil_calendar_spread_pair_turn_v11(
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
        self.assertGreaterEqual(
            high["completion"]["pair_completion_ratio"],
            low["completion"]["pair_completion_ratio"],
        )
        self.assertGreaterEqual(high["costs"]["execution_value_added_usd"], -1e-6)
        self.assertLessEqual(low["costs"]["execution_value_added_usd"], 1e-6)
        self.assertFalse(high["completion"]["execution_can_expand_authorized_order"])


if __name__ == "__main__":
    unittest.main()
