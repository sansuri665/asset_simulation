from __future__ import annotations

from copy import deepcopy
import inspect
import unittest

from asset_simulation.model.oil_calendar_spread_strategy import (
    OIL_CALENDAR_SPREAD_STRATEGY_MODEL_VERSION,
    attribute_oil_calendar_spread_pnl,
    build_oil_calendar_spread_execution_report,
    build_oil_calendar_spread_research_decision,
    evaluate_oil_calendar_spread_thesis_state,
)
from asset_simulation.model.oil_strategy_research import (
    build_default_oil_strategy_research_profile,
)
from asset_simulation.model.registry import load_registered_assets


def _weeks(
    spreads: list[float],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    main: list[dict[str, object]] = []
    next_main: list[dict[str, object]] = []
    for index, spread in enumerate(spreads):
        next_price = 70.0 + 0.15 * index
        main_price = next_price + spread
        common = {
            "week_serial": 1000 + index,
            "year": 2029 + (index // 48),
            "month": index // 4 + 1,
            "week": index % 4 + 1,
        }
        main.append({**common, "close": main_price})
        next_main.append({**common, "close": next_price})
    return main, next_main


def _market(
    *,
    current_main: float = 70.0,
    current_next: float = 71.0,
    as_of: tuple[int, int, int] = (2030, 1, 1),
    main_months_to_expiry: float = 4.5,
    next_months_to_expiry: float = 8.5,
    history_spreads: list[float] | None = None,
    main_turn_limit: int = 2_000,
    next_turn_limit: int = 1_800,
) -> dict[str, object]:
    main_weeks, next_weeks = _weeks(
        history_spreads
        or [-0.8, -0.9, -1.0, -1.1, -1.2, -1.3, -1.2, -1.1, -1.0, -0.9, -1.0, -1.0]
    )
    year, month, half = as_of
    return {
        "ok": True,
        "asOf": {"year": year, "month": month, "half": half},
        "identity": {"upstream_global_identity_hash": "world-42"},
        "contractSpecification": {
            "contract_size_bbl": 1000,
            "initial_margin_rate_pct": 15.0,
        },
        "participantLimitsPolicy": {
            "all_contract_gross_position_cap_lots": 10_000,
        },
        "curve": {
            "main_contract_id": "OIL-3005",
            "nearest_contract_id": "OIL-3005",
            "contracts": [
                {
                    "contract_id": "OIL-3005",
                    "price_usd": current_main,
                    "expiry_year": 2030,
                    "expiry_month": 5,
                    "months_to_expiry": main_months_to_expiry,
                    "half_turns_to_expiry": int(round(main_months_to_expiry * 2)),
                    "participantLimits": {
                        "single_contract_position_limit_lots": 5_000,
                        "turn_trade_limit_lots": main_turn_limit,
                        "new_trades_allowed": True,
                    },
                    "monthly": [{"year": 2029, "month": 12, "weekly": main_weeks}],
                },
                {
                    "contract_id": "OIL-3009",
                    "price_usd": current_next,
                    "expiry_year": 2030,
                    "expiry_month": 9,
                    "months_to_expiry": next_months_to_expiry,
                    "half_turns_to_expiry": int(round(next_months_to_expiry * 2)),
                    "participantLimits": {
                        "single_contract_position_limit_lots": 5_000,
                        "turn_trade_limit_lots": next_turn_limit,
                        "new_trades_allowed": True,
                    },
                    "monthly": [{"year": 2029, "month": 12, "weekly": next_weeks}],
                },
                {
                    "contract_id": "OIL-3101",
                    "price_usd": 72.0,
                    "expiry_year": 2031,
                    "expiry_month": 1,
                    "months_to_expiry": next_months_to_expiry + 4.0,
                    "half_turns_to_expiry": int(round((next_months_to_expiry + 4.0) * 2)),
                    "participantLimits": {
                        "single_contract_position_limit_lots": 5_000,
                        "turn_trade_limit_lots": 1_500,
                        "new_trades_allowed": True,
                    },
                    "monthly": [],
                },
            ],
        },
    }


def _forecast(
    *,
    current_main: float = 70.0,
    current_next: float = 71.0,
    future_main: tuple[float, float] = (72.0, 73.0),
    future_next: tuple[float, float] = (71.5, 72.0),
    as_of: tuple[int, int, int] = (2030, 1, 1),
) -> dict[str, object]:
    def leg(
        role: str,
        contract_id: str,
        anchor: float,
        centers: tuple[float, float],
    ) -> dict[str, object]:
        return {
            "role": role,
            "contract_id": contract_id,
            "anchor_price_usd": anchor,
            "weekly": [
                {
                    "horizon_weeks": horizon,
                    "week_serial": 2000 + horizon,
                    "target_week": f"2030-W{horizon}",
                    "close": center,
                    "confidence_low": center * 0.97,
                    "confidence_high": center * 1.03,
                }
                for horizon, center in zip((2, 4), centers, strict=True)
            ],
        }

    year, month, half = as_of
    return {
        "ok": True,
        "asOf": {"year": year, "month": month, "half": half},
        "identity": {
            "upstream_global_identity_hash": "world-42",
            "vintage_id": f"forecast-42-{year:04d}-{month:02d}-H{half}",
        },
        "institution": {"institution_id": "research-a"},
        "forecasts": [
            leg("main", "OIL-3005", current_main, future_main),
            leg("next_main", "OIL-3009", current_next, future_next),
        ],
    }


class OilCalendarSpreadStrategyTests(unittest.TestCase):
    def test_registered_assets_and_decision_are_deterministic_real_two_leg(self) -> None:
        assets = load_registered_assets()
        self.assertEqual(
            OIL_CALENDAR_SPREAD_STRATEGY_MODEL_VERSION,
            assets["oil_calendar_spread_strategy_config"]["model_version"],
        )
        first = build_oil_calendar_spread_research_decision(
            _market(),
            _forecast(),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        repeat = build_oil_calendar_spread_research_decision(
            _market(),
            _forecast(),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        self.assertEqual(first, repeat)
        self.assertTrue(first["pairIdentity"]["adjacent_pair_valid"])
        self.assertGreater(first["signal"]["forecast_spread_change_usd_per_bbl"], 0.0)
        self.assertGreater(first["target"]["target_spread_units"], 0)
        self.assertEqual(
            first["target"]["target_spread_units"],
            first["target"]["target_main_lots"],
        )
        self.assertEqual(
            -first["target"]["target_spread_units"],
            first["target"]["target_next_main_lots"],
        )
        self.assertEqual(
            0,
            first["strategyRiskAdapter"]["target"]["absolute_leg_imbalance_lots"],
        )
        self.assertFalse(
            first["pairedExecutionMandate"]["governance"]["synthetic_security_created"]
        )
        self.assertEqual({"main", "next_main"}, set(first["legs"]))

    def test_pm_capital_deployment_score_does_not_haircut_authorized_pair_capital(self) -> None:
        low = build_default_oil_strategy_research_profile()
        low.pop("profile_hash")
        low["style_radar"]["capital_deployment"] = 0.0
        high = deepcopy(low)
        high["style_radar"]["capital_deployment"] = 100.0

        low_decision = build_oil_calendar_spread_research_decision(
            _market(),
            _forecast(),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_research_profile=low,
        )
        high_decision = build_oil_calendar_spread_research_decision(
            _market(),
            _forecast(),
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_research_profile=high,
        )
        low_capacity = low_decision["strategyRiskAdapter"]["capacity"]
        high_capacity = high_decision["strategyRiskAdapter"]["capacity"]
        self.assertEqual(10_000_000.0, low_capacity["capital_capacity_budget_usd"])
        self.assertEqual(
            low_capacity["capital_capacity_budget_usd"],
            high_capacity["capital_capacity_budget_usd"],
        )
        self.assertEqual(
            low_capacity["risk_capacity_units"],
            high_capacity["risk_capacity_units"],
        )
        self.assertNotIn("capital_deployment_pct_of_authorized_capital", low_capacity)

    def test_pm_continuation_reversion_style_changes_visible_curve_component(self) -> None:
        market = _market()
        forecast = _forecast(
            future_main=(70.5, 70.5),
            future_next=(71.5, 71.5),
        )
        reversion = build_default_oil_strategy_research_profile()
        reversion.pop("profile_hash")
        reversion["style_radar"]["continuation_reversion"] = 0.0
        continuation = deepcopy(reversion)
        continuation["style_radar"]["continuation_reversion"] = 100.0

        reversion_decision = build_oil_calendar_spread_research_decision(
            market,
            forecast,
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_research_profile=reversion,
        )
        continuation_decision = build_oil_calendar_spread_research_decision(
            market,
            forecast,
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_research_profile=continuation,
        )
        self.assertNotEqual(
            reversion_decision["signal"]["visible_curve_signal"],
            continuation_decision["signal"]["visible_curve_signal"],
        )
        self.assertEqual(
            reversion_decision["signal"]["forecast_spread_change_usd_per_bbl"],
            continuation_decision["signal"]["forecast_spread_change_usd_per_bbl"],
        )

    def test_pair_capacity_uses_market_owner_maturity_without_double_subtracting_half_turn(self) -> None:
        market = _market(
            as_of=(2030, 4, 2),
            main_months_to_expiry=1.0,
            next_months_to_expiry=5.0,
        )
        forecast = _forecast(as_of=(2030, 4, 2))
        decision = build_oil_calendar_spread_research_decision(
            market,
            forecast,
            authorized_strategy_capital_usd=10_000_000.0,
        )
        capacity = decision["strategyRiskAdapter"]["capacity"]
        self.assertEqual(1.0, capacity["main_months_to_expiry"])
        self.assertTrue(capacity["main_expiry_buffer_ok"])
        self.assertFalse(capacity["expiry_roll_mismatch"])
        self.assertGreater(capacity["risk_capacity_units"], 0)
        self.assertEqual("oil_futures_overlay.market_owner", capacity["maturity_source"])

    def test_thesis_scores_only_exact_matured_two_week_component(self) -> None:
        # 2-week call is exactly correct; 4-week call is intentionally extreme.
        decision = build_oil_calendar_spread_research_decision(
            _market(),
            _forecast(
                future_main=(71.0, 105.0),
                future_next=(71.0, 70.0),
            ),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        evaluated = evaluate_oil_calendar_spread_thesis_state(
            decision,
            realized_main_price_usd=71.0,
            realized_next_main_price_usd=71.0,
            realized_week_serial=2002,
        )
        self.assertEqual(2, evaluated["evaluation"]["evaluated_horizon_weeks"])
        self.assertEqual([4], evaluated["evaluation"]["unmatured_horizons_weeks"])
        self.assertAlmostEqual(0.0, evaluated["evaluation"]["forecast_error_z"])
        self.assertEqual("active", evaluated["state"]["status"])
        self.assertFalse(
            evaluated["evaluation"]["blended_2w_4w_signal_scored_as_one_forecast"]
        )

    def test_forecast_requires_exact_horizon_and_shared_target_week(self) -> None:
        missing_two_week = _forecast()
        for leg in missing_two_week["forecasts"]:
            leg["weekly"] = [
                bar for bar in leg["weekly"] if bar["horizon_weeks"] != 2
            ]
        with self.assertRaisesRegex(ValueError, "exactly one bar"):
            build_oil_calendar_spread_research_decision(
                _market(),
                missing_two_week,
                authorized_strategy_capital_usd=10_000_000.0,
            )

        mismatched_target = _forecast()
        mismatched_target["forecasts"][1]["weekly"][0]["week_serial"] = 9999
        with self.assertRaisesRegex(ValueError, "target week serial"):
            build_oil_calendar_spread_research_decision(
                _market(),
                mismatched_target,
                authorized_strategy_capital_usd=10_000_000.0,
            )

    def test_thesis_evaluation_requires_exact_realized_target_week(self) -> None:
        decision = build_oil_calendar_spread_research_decision(
            _market(),
            _forecast(),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        with self.assertRaisesRegex(ValueError, "exact forecast target week"):
            evaluate_oil_calendar_spread_thesis_state(
                decision,
                realized_main_price_usd=71.0,
                realized_next_main_price_usd=71.0,
                realized_week_serial=2004,
                evaluation_horizon_weeks=2,
            )
        evaluated = evaluate_oil_calendar_spread_thesis_state(
            decision,
            realized_main_price_usd=71.0,
            realized_next_main_price_usd=71.0,
            realized_week_serial=2004,
            evaluation_horizon_weeks=4,
        )
        self.assertEqual(2004, evaluated["evaluation"]["realized_week_serial"])

    def test_third_month_cannot_be_mislabeled_as_next_main(self) -> None:
        forecast = _forecast()
        forecast["forecasts"][1]["contract_id"] = "OIL-3101"
        with self.assertRaisesRegex(ValueError, "not adjacent"):
            build_oil_calendar_spread_research_decision(
                _market(),
                forecast,
                authorized_strategy_capital_usd=10_000_000.0,
            )

    def test_pair_request_and_report_enforce_both_half_turn_leg_limits(self) -> None:
        decision = build_oil_calendar_spread_research_decision(
            _market(main_turn_limit=10, next_turn_limit=8),
            _forecast(future_main=(90.0, 95.0), future_next=(60.0, 55.0)),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        mandate = decision["pairedExecutionMandate"]
        self.assertLessEqual(abs(mandate["requested_pair_delta_units"]), 8)
        self.assertTrue(mandate["request_within_both_leg_turn_limits"])

        invalid = {
            "requested_pair_delta_units": 68,
            "requested_main_delta_lots": 68,
            "requested_next_main_delta_lots": -68,
            "main_turn_liquidity_lots": 10,
            "next_main_turn_liquidity_lots": 8,
            "temporary_leg_imbalance_tolerance_lots": 0,
        }
        with self.assertRaisesRegex(ValueError, "half-turn market limit"):
            build_oil_calendar_spread_execution_report(
                invalid,
                executed_main_delta_lots=68,
                executed_next_main_delta_lots=-68,
            )

    def test_remediation_and_pair_share_each_leg_turn_limit(self) -> None:
        decision = build_oil_calendar_spread_research_decision(
            _market(main_turn_limit=10, next_turn_limit=8),
            _forecast(future_main=(90.0, 95.0), future_next=(60.0, 55.0)),
            authorized_strategy_capital_usd=10_000_000.0,
            positions={"OIL-3005": 100, "OIL-3009": 0},
        )
        mandate = decision["pairedExecutionMandate"]
        remediation = mandate["imbalanceRemediation"]
        self.assertEqual(-10, remediation["requested_main_delta_lots"])
        self.assertEqual(90, remediation["remaining_residual_main_lots_after_request"])
        self.assertEqual(0, mandate["requested_pair_delta_units"])
        self.assertLessEqual(
            mandate["combined_requested_main_turnover_lots"],
            mandate["main_turn_liquidity_lots"],
        )
        self.assertLessEqual(
            mandate["combined_requested_next_main_turnover_lots"],
            mandate["next_main_turn_liquidity_lots"],
        )
        report = build_oil_calendar_spread_execution_report(
            mandate,
            executed_main_delta_lots=0,
            executed_next_main_delta_lots=0,
        )
        self.assertTrue(
            report["governance"][
                "remediation_reservation_validated_against_leg_turn_limits"
            ]
        )

    def test_execution_report_rejects_missing_or_forged_market_limits(self) -> None:
        missing_limits = {
            "requested_pair_delta_units": 1_000_000,
            "requested_main_delta_lots": 1_000_000,
            "requested_next_main_delta_lots": -1_000_000,
        }
        with self.assertRaisesRegex(ValueError, "missing required market limits"):
            build_oil_calendar_spread_execution_report(
                missing_limits,
                executed_main_delta_lots=1_000_000,
                executed_next_main_delta_lots=-1_000_000,
            )

        forged_remediation = {
            "requested_pair_delta_units": 8,
            "requested_main_delta_lots": 8,
            "requested_next_main_delta_lots": -8,
            "main_turn_liquidity_lots": 10,
            "next_main_turn_liquidity_lots": 8,
            "imbalanceRemediation": {
                "requested_main_delta_lots": -100,
                "requested_next_main_delta_lots": 0,
            },
        }
        with self.assertRaisesRegex(ValueError, "pair plus remediation"):
            build_oil_calendar_spread_execution_report(
                forged_remediation,
                executed_main_delta_lots=8,
                executed_next_main_delta_lots=-8,
            )

    def test_parallel_common_price_move_cannot_create_forecast_spread_signal(self) -> None:
        market = _market(
            current_main=70.0,
            current_next=69.0,
            history_spreads=[1.0] * 12,
        )
        forecast = _forecast(
            current_main=70.0,
            current_next=69.0,
            future_main=(80.0, 90.0),
            future_next=(79.0, 89.0),
        )
        decision = build_oil_calendar_spread_research_decision(
            market,
            forecast,
            authorized_strategy_capital_usd=10_000_000.0,
        )
        self.assertEqual(0.0, decision["signal"]["forecast_spread_change_usd_per_bbl"])
        self.assertEqual(0.0, decision["signal"]["forecast_spread_change_normalized"])
        self.assertEqual(0.0, decision["signal"]["forecast_signal"])
        self.assertEqual(0.0, decision["signal"]["visible_curve_signal"])
        self.assertEqual(0.0, decision["signal"]["signal"])
        self.assertEqual(0, decision["target"]["target_spread_units"])

    def test_signed_leg_imbalance_contract_is_preserved(self) -> None:
        mandate = {
            "requested_pair_delta_units": 500,
            "requested_main_delta_lots": 500,
            "requested_next_main_delta_lots": -500,
            "main_turn_liquidity_lots": 500,
            "next_main_turn_liquidity_lots": 500,
            "temporary_leg_imbalance_tolerance_lots": 50,
        }
        contract = load_registered_assets()["oil_calendar_spread_strategy_contract"]
        self.assertEqual(
            "signed_lots",
            contract["paired_execution_fields"]["leg_imbalance_lots"]["unit"],
        )
        self.assertEqual(
            "nonnegative_lots",
            contract["paired_execution_fields"]["absolute_leg_imbalance_lots"]["unit"],
        )
        positive = build_oil_calendar_spread_execution_report(
            mandate,
            executed_main_delta_lots=500,
            executed_next_main_delta_lots=-320,
        )
        self.assertEqual(180, positive["leg_imbalance_lots"])
        self.assertEqual(180, positive["absolute_leg_imbalance_lots"])

        negative = build_oil_calendar_spread_execution_report(
            mandate,
            executed_main_delta_lots=320,
            executed_next_main_delta_lots=-500,
        )
        self.assertEqual(-180, negative["leg_imbalance_lots"])
        self.assertEqual(180, negative["absolute_leg_imbalance_lots"])

    def test_pnl_carry_is_computed_from_counterfactual_prices_not_free_form_pnl(self) -> None:
        params = inspect.signature(attribute_oil_calendar_spread_pnl).parameters
        self.assertNotIn("convergence_carry_pnl_usd", params)
        result = attribute_oil_calendar_spread_pnl(
            starting_main_lots=100,
            starting_next_main_lots=-100,
            main_start_price_usd=70.0,
            main_end_price_usd=73.0,
            next_main_start_price_usd=71.0,
            next_main_end_price_usd=72.0,
            carry_reference_main_end_price_usd=71.0,
            carry_reference_next_main_end_price_usd=71.0,
        )
        self.assertEqual(200_000.0, result["calendar_spread_pnl_usd"])
        self.assertEqual(100_000.0, result["convergence_carry_pnl_usd"])
        self.assertEqual(100_000.0, result["forecast_curve_move_pnl_usd"])
        self.assertEqual(
            "computed_from_counterfactual_leg_prices",
            result["carryAttribution"]["status"],
        )

        unavailable = attribute_oil_calendar_spread_pnl(
            starting_main_lots=100,
            starting_next_main_lots=-100,
            main_start_price_usd=70.0,
            main_end_price_usd=73.0,
            next_main_start_price_usd=71.0,
            next_main_end_price_usd=72.0,
        )
        self.assertEqual(0.0, unavailable["convergence_carry_pnl_usd"])
        self.assertEqual(
            "not_separately_available",
            unavailable["carryAttribution"]["status"],
        )

    def test_pnl_is_curve_pnl_when_both_legs_move_together(self) -> None:
        unchanged_spread = attribute_oil_calendar_spread_pnl(
            starting_main_lots=100,
            starting_next_main_lots=-100,
            main_start_price_usd=70.0,
            main_end_price_usd=77.0,
            next_main_start_price_usd=71.0,
            next_main_end_price_usd=78.0,
        )
        self.assertEqual(0.0, unchanged_spread["calendar_spread_pnl_usd"])
        self.assertEqual(0.0, unchanged_spread["residual_directional_pnl_usd"])
        self.assertEqual(0.0, unchanged_spread["net_pnl_usd"])

        imbalanced = attribute_oil_calendar_spread_pnl(
            starting_main_lots=100,
            starting_next_main_lots=-80,
            main_start_price_usd=70.0,
            main_end_price_usd=72.0,
            next_main_start_price_usd=71.0,
            next_main_end_price_usd=72.0,
        )
        self.assertNotEqual(0.0, imbalanced["residual_directional_pnl_usd"])
        self.assertEqual(
            imbalanced["gross_leg_pnl_before_cost_usd"],
            imbalanced["calendar_spread_pnl_usd"]
            + imbalanced["residual_directional_pnl_usd"],
        )

    def test_thesis_moves_active_watch_invalidated_and_reversal_exits_first(self) -> None:
        decision = build_oil_calendar_spread_research_decision(
            _market(),
            _forecast(),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        first = evaluate_oil_calendar_spread_thesis_state(
            decision,
            realized_main_price_usd=69.5,
            realized_next_main_price_usd=71.5,
            realized_week_serial=2002,
        )
        self.assertIn(first["state"]["status"], {"watch", "invalidated"})

        reversed_forecast = _forecast(
            future_main=(68.0, 67.0),
            future_next=(72.0, 73.0),
        )
        reversal = build_oil_calendar_spread_research_decision(
            _market(),
            reversed_forecast,
            authorized_strategy_capital_usd=10_000_000.0,
            positions={"OIL-3005": 100, "OIL-3009": -100},
            thesis_state={"status": "active", "last_signal": 0.8},
        )
        self.assertEqual(
            "exit_before_direction_reversal",
            reversal["thesisInvalidation"]["action"]["action"],
        )
        self.assertGreaterEqual(reversal["target"]["target_spread_units"], 0)
        self.assertLess(reversal["target"]["target_spread_units"], 100)

    def test_missing_owner_maturity_is_rejected_instead_of_recomputed(self) -> None:
        market = _market()
        del market["curve"]["contracts"][0]["months_to_expiry"]
        with self.assertRaisesRegex(ValueError, "market-owner months_to_expiry"):
            build_oil_calendar_spread_research_decision(
                market,
                _forecast(),
                authorized_strategy_capital_usd=10_000_000.0,
            )

    def test_mismatched_world_or_missing_leg_is_rejected(self) -> None:
        mismatched = _forecast()
        mismatched["identity"]["upstream_global_identity_hash"] = "other-world"
        with self.assertRaises(ValueError):
            build_oil_calendar_spread_research_decision(
                _market(),
                mismatched,
                authorized_strategy_capital_usd=10_000_000.0,
            )


if __name__ == "__main__":
    unittest.main()
