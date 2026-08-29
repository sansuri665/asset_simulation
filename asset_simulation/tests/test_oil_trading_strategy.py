from __future__ import annotations

from copy import deepcopy
import math
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_short_term_forecast import (
    generate_oil_short_term_forecast,
)
from asset_simulation.model.oil_trading_strategy import (
    OIL_TRADING_STRATEGY_MODEL_VERSION,
    _apply_position_persistence,
    _limit_penetration_fill,
    _resolve_fee_profile,
    _signal_from_contract_forecast,
    _slippage_bps,
    build_oil_strategy_decision,
    settle_oil_strategy_turn,
    simulate_oil_trading_strategy,
)
from asset_simulation.model.oil_strategy_research import (
    build_default_oil_strategy_research_profile,
    generate_oil_strategy_research_roster,
    resolve_oil_strategy_runtime_policy,
)
from asset_simulation.model.registry import load_registered_assets


class OilTradingStrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.global_run = run_global_macro(42, 7)

    def test_visible_forecast_builds_deterministic_capacity_bounded_targets(self) -> None:
        market = oil_futures_payload(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        vintage = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        first = build_oil_strategy_decision(market, vintage)
        second = build_oil_strategy_decision(market, vintage)
        self.assertEqual(first, second)
        self.assertEqual(
            OIL_TRADING_STRATEGY_MODEL_VERSION,
            first["identity"]["model_version"],
        )
        self.assertFalse(first["identity"]["write_back"])
        self.assertFalse(first["informationPolicy"]["hidden_future_available"])
        self.assertFalse(
            first["informationPolicy"]["future_market_payload_available"]
        )
        self.assertEqual(
            [("main", "OIL-3005"), ("next_main", "OIL-3009")],
            [(item["role"], item["contract_id"]) for item in first["targets"]],
        )
        for item in first["targets"]:
            self.assertGreaterEqual(item["signal"], -1.0)
            self.assertLessEqual(item["signal"], 1.0)
            self.assertLessEqual(
                abs(item["target_position_lots"]), item["risk_capacity_lots"]
            )
            self.assertTrue(item["horizon_components"])
            for setup in item["weekly_turnover_setups"]:
                self.assertGreaterEqual(
                    setup["required_edge_after_cost_pct"],
                    first["strategy"]["turnover_profile"][
                        "minimum_trade_edge_pct"
                    ],
                )
                self.assertGreaterEqual(
                    setup["required_edge_after_cost_pct"] + 1e-12,
                    setup["estimated_cost"][
                        "estimated_round_trip_cost_pct"
                    ]
                    + setup["net_edge_floor_pct"],
                )
                self.assertGreaterEqual(
                    setup["round_trip_budget_utilization"], 0.0
                )
                self.assertLessEqual(
                    setup["round_trip_budget_utilization"], 1.0
                )
                if setup["excess_net_edge_pct"] <= 0.0:
                    self.assertEqual(
                        0.0, setup["round_trip_budget_utilization"]
                    )
                    self.assertFalse(setup["planned"])
        self.assertLessEqual(
            first["riskBudget"]["target_gross_lots"],
            first["riskBudget"]["gross_market_cap_lots"],
        )
        self.assertEqual(
            40.0,
            first["riskBudget"][
                "capital_deployment_pct_of_allocated_equity"
            ],
        )

    def test_forecast_band_location_and_path_direction_drive_the_signal(self) -> None:
        signal_config = load_registered_assets()["oil_trading_strategy_config"][
            "signal"
        ]
        _, strategy_policy = resolve_oil_strategy_runtime_policy(None)
        signal_config = {
            **signal_config,
            "horizon_weeks": strategy_policy["signal"]["horizon_weeks"],
            "horizon_weights": strategy_policy["signal"]["horizon_weights"],
            "reversion_weight": strategy_policy["signal"]["reversion_weight"],
            "continuation_weight": strategy_policy["signal"][
                "continuation_weight"
            ],
        }

        def forecast(
            centers: list[float], lows: list[float], highs: list[float]
        ) -> dict[str, object]:
            return {
                "anchor_price_usd": 100.0,
                "weekly": [
                    {
                        "horizon_weeks": horizon,
                        "week_serial": 50_000 + horizon,
                        "target_week": f"guard-W{horizon}",
                        "close": center,
                        "confidence_low": low,
                        "confidence_high": high,
                    }
                    for horizon, center, low, high in zip(
                        (2, 4, 8), centers, lows, highs, strict=True
                    )
                ],
            }

        near_lower = _signal_from_contract_forecast(
            forecast([110.0] * 3, [100.0] * 3, [120.0] * 3), signal_config
        )
        centered = _signal_from_contract_forecast(
            forecast([100.0] * 3, [90.0] * 3, [110.0] * 3), signal_config
        )
        near_upper = _signal_from_contract_forecast(
            forecast([90.0] * 3, [80.0] * 3, [100.0] * 3), signal_config
        )
        rising = _signal_from_contract_forecast(
            forecast(
                [100.0, 102.0, 106.0],
                [90.0, 92.0, 96.0],
                [110.0, 112.0, 116.0],
            ),
            signal_config,
        )

        self.assertGreater(near_lower["signal"], 0.0)
        self.assertEqual(0.0, centered["signal"])
        self.assertLess(near_upper["signal"], 0.0)
        self.assertGreater(rising["path_direction_signal"], 0.0)
        self.assertGreater(rising["signal"], centered["signal"])
        self.assertEqual(
            "forecast_location_plus_l2_normalized_continuation_overlay",
            rising["signal_combination_method"],
        )
        self.assertGreater(rising["effective_base_location_weight"], 0.0)
        self.assertGreater(
            rising["effective_continuation_overlay_weight"], 0.0
        )
        self.assertAlmostEqual(
            rising["raw_signal"],
            max(
                -1.0,
                min(
                    1.0,
                    (
                        rising["reversion_signal"]
                        + rising["continuation_overlay_intensity"]
                        * rising["continuation_signal"]
                    )
                    / rising["signal_combination_normalization"],
                ),
            ),
        )

        conflicting = forecast(
            [108.0, 103.0, 98.0],
            [98.0, 93.0, 88.0],
            [118.0, 113.0, 108.0],
        )
        reversion_led = _signal_from_contract_forecast(
            conflicting,
            {
                **signal_config,
                "reversion_weight": 0.90,
                "continuation_weight": 0.10,
            },
        )
        continuation_led = _signal_from_contract_forecast(
            conflicting,
            {
                **signal_config,
                "reversion_weight": 0.10,
                "continuation_weight": 0.90,
            },
        )
        self.assertGreater(reversion_led["raw_signal"], 0.0)
        self.assertLess(continuation_led["raw_signal"], 0.0)

    def test_configured_research_score_is_not_a_strategy_input(self) -> None:
        market = oil_futures_payload(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        vintage = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        altered = deepcopy(vintage)
        altered["institution"]["capability_total_score"] = 0.0
        altered["institution"]["capability_radar"] = {
            key: 0.0 for key in altered["institution"]["capability_radar"]
        }

        baseline = build_oil_strategy_decision(market, vintage)
        changed_metadata = build_oil_strategy_decision(market, altered)
        self.assertEqual(baseline, changed_metadata)
        self.assertNotIn("capability_total_score", baseline["institution"])
        self.assertFalse(
            baseline["informationPolicy"]["configured_capability_score_used"]
        )

    def test_turnover_development_override_changes_activity_not_other_axes(self) -> None:
        profile = build_default_oil_strategy_research_profile()
        _, low_policy = resolve_oil_strategy_runtime_policy(
            profile, turnover_development_override=0.0
        )
        _, medium_policy = resolve_oil_strategy_runtime_policy(profile)
        _, high_policy = resolve_oil_strategy_runtime_policy(
            profile, turnover_development_override=100.0
        )
        low = low_policy["execution"]
        medium = medium_policy["execution"]
        high = high_policy["execution"]

        self.assertEqual(0.5, low["adjustment_speed"])
        self.assertEqual(0.15, low["signal_deadband_abs"])
        self.assertEqual(1.15, low["minimum_trade_edge_pct"])
        self.assertEqual(0.6, low["gross_turnover_multiplier"])
        self.assertEqual(50.0, medium["turnover_intensity"])
        self.assertAlmostEqual(0.5, medium["adjustment_speed"])
        self.assertAlmostEqual(0.15, medium["signal_deadband_abs"])
        self.assertAlmostEqual(1.15, medium["minimum_trade_edge_pct"])
        self.assertAlmostEqual(
            math.sqrt(0.6 * 7.5), medium["gross_turnover_multiplier"]
        )
        self.assertEqual(0.5, high["adjustment_speed"])
        self.assertEqual(0.15, high["signal_deadband_abs"])
        self.assertEqual(1.15, high["minimum_trade_edge_pct"])
        self.assertAlmostEqual(7.5, high["gross_turnover_multiplier"])
        for invalid in (-0.1, 100.1, math.nan, math.inf, True):
            with self.assertRaises(ValueError):
                resolve_oil_strategy_runtime_policy(
                    profile, turnover_development_override=invalid
                )

    def test_fee_tiers_and_square_root_slippage_are_bounded(self) -> None:
        config = load_registered_assets()["oil_trading_strategy_config"]
        friction = config["execution_friction"]
        expected_tiers = (
            (0, 0.0),
            (250_000, 0.10),
            (750_000, 0.20),
            (1_500_000, 0.30),
        )
        for rolling_lots, expected_rate in expected_tiers:
            profile = _resolve_fee_profile(
                {"rolling_gross_turnover_lots": rolling_lots}, friction
            )
            self.assertEqual(expected_rate, profile["rebate_rate"])
            self.assertGreater(profile["gross_fee_usd_per_lot_side"], 0.0)
            self.assertGreaterEqual(profile["net_fee_usd_per_lot_side"], 0.0)
            self.assertLessEqual(
                profile["rebate_usd_per_lot_side"],
                profile["eligible_fee_usd_per_lot_side"],
            )
        for invalid in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                _resolve_fee_profile(
                    {"rolling_gross_turnover_lots": invalid}, friction
                )

        _, policy = resolve_oil_strategy_runtime_policy(None)
        turnover = policy["execution"]
        one = _slippage_bps(1_000, 3_000_000, 0.04, turnover, friction)
        four = _slippage_bps(4_000, 3_000_000, 0.04, turnover, friction)
        zero = _slippage_bps(0, 3_000_000, 0.04, turnover, friction)
        self.assertEqual(0.0, zero["slippage_bps"])
        self.assertAlmostEqual(
            2.0 * one["raw_slippage_bps"],
            four["raw_slippage_bps"],
        )
        self.assertFalse(one["slippage_capped"])
        self.assertFalse(four["slippage_capped"])

    def test_appointed_strategy_profile_is_auditable_and_affects_targets(self) -> None:
        roster = generate_oil_strategy_research_roster(seed=42, candidate_count=3)
        self.assertEqual(3, roster["candidateCount"])
        self.assertFalse(roster["selectionPolicy"]["player_can_edit_radar"])
        self.assertFalse(
            roster["selectionPolicy"]["preference_total_score_available"]
        )
        self.assertEqual(
            3,
            len({item["profile_hash"] for item in roster["candidates"]}),
        )
        market = oil_futures_payload(
            self.global_run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        vintage = generate_oil_short_term_forecast(
            self.global_run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        decisions = [
            build_oil_strategy_decision(
                market,
                vintage,
                strategy_research_profile=profile,
            )
            for profile in roster["candidates"]
        ]
        self.assertGreater(
            len({item["riskBudget"]["target_gross_lots"] for item in decisions}),
            1,
        )
        for profile, decision in zip(roster["candidates"], decisions, strict=True):
            strategy = decision["strategy"]
            self.assertEqual(
                profile["profile_hash"],
                strategy["strategy_research_profile"]["profile_hash"],
            )
            self.assertIsNone(
                strategy["strategy_research_profile"]["preference_total_score"]
            )
            self.assertFalse(
                decision["informationPolicy"]["strategy_radar_player_editable"]
            )
            for target in decision["targets"]:
                self.assertIn(
                    target["binding_capacity"],
                    {
                        "market_position_limit",
                        "capital_deployment_budget",
                        "new_trades_closed",
                    },
                )

    def test_holding_patience_retains_but_never_expands_capacity(self) -> None:
        no_patience = _apply_position_persistence(
            current_position_lots=10_000,
            ideal_target_lots=2_000,
            risk_capacity_lots=12_000,
            position_persistence=0.0,
        )
        patient = _apply_position_persistence(
            current_position_lots=10_000,
            ideal_target_lots=2_000,
            risk_capacity_lots=12_000,
            position_persistence=0.7,
        )
        reversal = _apply_position_persistence(
            current_position_lots=10_000,
            ideal_target_lots=-12_000,
            risk_capacity_lots=12_000,
            position_persistence=0.7,
        )
        self.assertEqual(2_000, no_patience)
        self.assertGreater(patient, no_patience)
        self.assertLessEqual(patient, 12_000)
        self.assertGreaterEqual(reversal, -12_000)

    def test_limit_penetration_requires_more_than_a_touch(self) -> None:
        touch = _limit_penetration_fill(
            [100.0, 99.0, 101.0, 100.0],
            side="buy",
            limit_price=99.0,
            hit_moment=1.0,
            full_fill_range_fraction=0.2,
        )
        partial = _limit_penetration_fill(
            [100.0, 98.8, 101.0, 100.0],
            side="buy",
            limit_price=99.0,
            hit_moment=0.8333333333,
            full_fill_range_fraction=0.2,
        )
        full = _limit_penetration_fill(
            [100.0, 98.0, 101.0, 100.0],
            side="buy",
            limit_price=99.0,
            hit_moment=0.5,
            full_fill_range_fraction=0.2,
        )
        self.assertEqual(0.0, touch["fill_ratio"])
        self.assertGreater(partial["fill_ratio"], 0.0)
        self.assertLess(partial["fill_ratio"], 1.0)
        self.assertEqual(1.0, full["fill_ratio"])

    def test_weekly_gross_turnover_is_triggered_and_capacity_bounded(self) -> None:
        start = oil_futures_payload(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        end = oil_futures_payload(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=2,
        )
        vintage = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        low_decision = build_oil_strategy_decision(
            start, vintage, turnover_intensity=0.0
        )
        high_decision = build_oil_strategy_decision(
            start, vintage, turnover_intensity=100.0
        )
        low = settle_oil_strategy_turn(
            start, end, low_decision, equity_usd=3_000_000_000.0
        )
        high = settle_oil_strategy_turn(
            start, end, high_decision, equity_usd=3_000_000_000.0
        )

        self.assertGreater(
            high["executionSummary"]["gross_turnover_lots"],
            low["executionSummary"]["gross_turnover_lots"],
        )
        self.assertGreater(high["executionSummary"]["round_trip_lots"], 0)
        for report in (low, high):
            summary = report["executionSummary"]
            self.assertEqual(
                summary["buy_lots"] + summary["sell_lots"],
                summary["gross_turnover_lots"],
            )
            self.assertEqual(
                summary["gross_turnover_lots"],
                summary["net_traded_lots"] + 2 * summary["round_trip_lots"],
            )
            for item in report["contracts"]:
                self.assertEqual(
                    item["buy_lots"] - item["sell_lots"],
                    item["executed_delta_lots"],
                )
                self.assertEqual(
                    item["buy_lots"] + item["sell_lots"],
                    item["gross_turnover_lots"],
                )
                self.assertLessEqual(
                    item["gross_turnover_lots"],
                    item["gross_turnover_budget_lots"],
                )
                self.assertLessEqual(
                    item["gross_turnover_budget_lots"],
                    item["hard_turn_trade_limit_lots"],
                )
                for week in item["weekly_executions"]:
                    self.assertEqual(
                        week["buy_lots"] - week["sell_lots"],
                        week["net_delta_lots"],
                    )
                    self.assertLessEqual(
                        week["gross_turnover_lots"], week["gross_budget_lots"]
                    )

    def test_next_half_month_uses_neutral_aggregate_price_and_marks_to_market(self) -> None:
        start = oil_futures_payload(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        end = oil_futures_payload(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=2,
        )
        vintage = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        decision = build_oil_strategy_decision(start, vintage)
        report = settle_oil_strategy_turn(
            start,
            end,
            decision,
            equity_usd=3_000_000_000.0,
        )
        self.assertEqual("2030-01-H1", report["fromAsOf"]["label"])
        self.assertEqual("2030-01-H2", report["toAsOf"]["label"])
        summary = report["executionSummary"]
        self.assertGreater(summary["fee_usd"], 0.0)
        self.assertGreater(summary["friction_bps"], 0.0)
        self.assertGreater(summary["spread_cost_usd"], 0.0)
        self.assertGreater(summary["slippage_cost_usd"], 0.0)
        self.assertGreater(summary["gross_fee_usd"], 0.0)
        self.assertGreaterEqual(summary["fee_rebate_usd"], 0.0)
        self.assertLessEqual(
            summary["fee_rebate_usd"], summary["gross_fee_usd"]
        )
        self.assertGreaterEqual(summary["net_fee_usd"], 0.0)
        self.assertAlmostEqual(
            summary["spread_cost_usd"]
            + summary["slippage_cost_usd"]
            + summary["net_fee_usd"],
            summary["execution_cost_usd"],
            places=4,
        )
        self.assertAlmostEqual(
            summary["gross_pnl_before_cost_usd"]
            - summary["execution_cost_usd"],
            report["accountAfter"]["turn_pnl_usd"],
            places=4,
        )
        self.assertAlmostEqual(
            summary["carry_gross_pnl_usd"]
            + summary["net_adjustment_gross_pnl_usd"]
            + summary["round_trip_gross_pnl_usd"],
            summary["gross_pnl_before_cost_usd"],
            places=4,
        )
        self.assertAlmostEqual(
            summary["net_adjustment_execution_cost_usd"]
            + summary["round_trip_execution_cost_usd"]
            + summary["cash_settlement_fee_usd"],
            summary["execution_cost_usd"],
            places=4,
        )
        self.assertAlmostEqual(
            summary["carry_gross_pnl_usd"]
            + summary["net_adjustment_net_pnl_usd"]
            + summary["round_trip_net_pnl_usd"]
            - summary["cash_settlement_fee_usd"],
            report["accountAfter"]["turn_pnl_usd"],
            places=4,
        )
        self.assertFalse(
            report["executionSummary"]["future_weeks_used_for_decision"]
        )
        self.assertTrue(
            report["executionSummary"]["newly_realized_weeks_used_for_settlement"]
        )
        for item in report["contracts"]:
            if item["executed_delta_lots"] == 0:
                continue
            self.assertEqual(
                ["2030-01-W3", "2030-01-W4"],
                item["execution_window_weeks"],
            )
            contract = next(
                row
                for row in end["curve"]["contracts"]
                if row["contract_id"] == item["contract_id"]
            )
            weeks = contract["monthly"][-1]["weekly"][2:]
            self.assertGreaterEqual(
                item["aggregate_execution_price_usd"],
                min(week["low"] for week in weeks),
            )
            self.assertLessEqual(
                item["aggregate_execution_price_usd"],
                max(week["high"] for week in weeks),
            )
            for week_report in item["weekly_executions"]:
                if week_report["buy_lots"]:
                    self.assertGreaterEqual(
                        week_report["all_in_buy_price_usd"],
                        week_report["raw_average_buy_price_usd"],
                    )
                if week_report["sell_lots"]:
                    self.assertLessEqual(
                        week_report["all_in_sell_price_usd"],
                        week_report["raw_average_sell_price_usd"],
                    )
                self.assertAlmostEqual(
                    week_report["spread_cost_usd"]
                    + week_report["slippage_cost_usd"]
                    + week_report["net_fee_usd"],
                    week_report["execution_cost_usd"],
                    places=4,
                )
                self.assertLessEqual(
                    week_report["round_trip_lots"],
                    week_report["planned_round_trip_lots"],
                )
                self.assertLessEqual(
                    week_report["planned_round_trip_lots"],
                    week_report["sized_round_trip_budget_lots"],
                )
                self.assertEqual(
                    week_report["round_trip_lots"],
                    week_report["target_exit_lots"]
                    + week_report["weekly_close_exit_lots"],
                )
                for key in (
                    "spread_cost_usd",
                    "slippage_cost_usd",
                    "gross_fee_usd",
                    "fee_rebate_usd",
                    "net_fee_usd",
                    "execution_cost_usd",
                ):
                    self.assertAlmostEqual(
                        week_report["net_adjustment"][key]
                        + week_report["round_trip"][key],
                        week_report[key],
                        places=4,
                    )
                self.assertAlmostEqual(
                    week_report["gross_execution_pnl_before_cost_usd"]
                    - week_report["execution_cost_usd"],
                    week_report["net_execution_pnl_after_cost_usd"],
                    places=4,
                )
            self.assertLessEqual(
                abs(item["executed_delta_lots"]),
                math.floor(item["hard_turn_trade_limit_lots"] * 0.5),
            )
        self.assertAlmostEqual(
            3_000_000_000.0
            + sum(item["turn_pnl_usd"] for item in report["contracts"]),
            report["accountAfter"]["equity_usd"],
            places=4,
        )

    def test_simulation_is_deterministic_prefix_stable_and_rolls_named_contracts(self) -> None:
        first = simulate_oil_trading_strategy(
            self.global_run,
            end_year=2030,
            end_month=6,
            end_half=1,
        )
        repeat = simulate_oil_trading_strategy(
            self.global_run,
            end_year=2030,
            end_month=6,
            end_half=1,
        )
        long_world = simulate_oil_trading_strategy(
            run_global_macro(42, 60),
            end_year=2030,
            end_month=6,
            end_half=1,
        )
        self.assertEqual(first, repeat)
        self.assertEqual(first["turns"], long_world["turns"])
        self.assertEqual(10, first["period"]["completed_turns"])
        self.assertGreater(first["summary"]["total_traded_lots"], 0)
        self.assertGreater(first["summary"]["total_settled_lots"], 0)
        self.assertGreater(first["summary"]["execution_cost_usd"], 0.0)
        self.assertGreater(first["summary"]["gross_fee_usd"], 0.0)
        self.assertGreaterEqual(first["summary"]["fee_rebate_usd"], 0.0)
        self.assertAlmostEqual(
            first["summary"]["gross_pnl_before_cost_usd"]
            - first["summary"]["execution_cost_usd"],
            first["summary"]["net_pnl_usd"],
            places=2,
        )
        self.assertNotIn("OIL-3005", first["summary"]["ending_positions"])
        self.assertEqual(0, first["summary"]["position_limit_excess_turns"])
        self.assertTrue(math.isfinite(first["summary"]["return_pct"]))
        self.assertTrue(math.isfinite(first["summary"]["maximum_drawdown_pct"]))
        prior_gross_turnover: list[int] = []
        for turn in first["turns"]:
            decision = turn["decision"]
            settlement = turn["settlement"]
            self.assertEqual(
                sum(prior_gross_turnover[-24:]),
                decision["strategy"]["fee_profile"][
                    "rolling_gross_turnover_lots"
                ],
            )
            self.assertAlmostEqual(
                settlement["executionSummary"]["settled_lots"] * 0.5,
                settlement["executionSummary"][
                    "cash_settlement_fee_usd"
                ],
            )
            self.assertLessEqual(
                settlement["accountAfter"]["gross_position_lots"],
                settlement["accountAfter"]["gross_position_cap_lots"],
            )
            self.assertFalse(settlement["identity"]["write_back"])
            prior_gross_turnover.append(
                settlement["executionSummary"]["gross_turnover_lots"]
            )

    def test_adjacent_cutoffs_and_world_identity_are_enforced(self) -> None:
        start = oil_futures_payload(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        nonadjacent = oil_futures_payload(
            self.global_run,
            as_of_year=2030,
            as_of_month=2,
            as_of_half=1,
        )
        vintage = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        decision = build_oil_strategy_decision(start, vintage)
        with self.assertRaises(ValueError):
            settle_oil_strategy_turn(
                start,
                nonadjacent,
                decision,
                equity_usd=3_000_000_000.0,
            )


if __name__ == "__main__":
    unittest.main()
