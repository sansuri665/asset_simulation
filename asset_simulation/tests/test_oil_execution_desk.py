from __future__ import annotations

from copy import deepcopy
from statistics import correlation
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_execution_desk import (
    CAPABILITY_DIMENSIONS,
    STYLE_DIMENSIONS,
    adjust_visible_execution_weights,
    build_default_oil_execution_desk_profile,
    generate_oil_execution_desk_candidate,
    generate_oil_execution_desk_roster,
    resolve_oil_execution_desk_profile,
)
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_short_term_forecast import generate_oil_short_term_forecast
from asset_simulation.model.oil_trading_strategy import (
    build_oil_strategy_decision,
    settle_oil_strategy_turn,
)
from asset_simulation.model.registry import load_registered_assets


def _profile(
    capability_score: float = 50.0,
    *,
    urgency: float = 50.0,
    passive_preference: float = 50.0,
    window_timing: float = 50.0,
) -> dict:
    return {
        "appointment": {
            "personnel_id": (
                f"execution_test_{capability_score}_{urgency}_"
                f"{passive_preference}_{window_timing}"
            ),
            "display_name": "执行测试",
            "source": "test_calibration",
            "candidate_index": None,
            "generation_seed": None,
        },
        "capability_radar": {
            key: float(capability_score)
            for key in CAPABILITY_DIMENSIONS
        },
        "execution_style": {
            "urgency": float(urgency),
            "passive_preference": float(passive_preference),
            "window_timing": float(window_timing),
        },
    }


def _same_authorized_order_decision(
    market: dict,
    forecast: dict,
    execution_profile: dict,
    *,
    target_lots: int = 4_000,
) -> dict:
    decision = build_oil_strategy_decision(
        market,
        forecast,
        execution_desk_profile=execution_profile,
    )
    result = deepcopy(decision)
    selected = False
    for item in result["targets"]:
        is_target = str(item["role"]) == "next_main" and not selected
        value = int(target_lots if is_target else 0)
        if is_target:
            selected = True
        for key in (
            "strategy_intent_target_position_lots",
            "strategy_risk_approved_target_position_lots",
            "company_risk_approved_target_position_lots",
            "risk_approved_target_position_lots",
            "target_position_lots",
            "ideal_target_lots",
            "thesis_adjusted_target_position_lots",
            "pre_thesis_target_position_lots",
            "pre_persistence_ideal_target_lots",
        ):
            if key in item:
                item[key] = value
        item["gross_turnover_budget_lots"] = 0
        item["weekly_turnover_setups"] = []
    if not selected:
        raise AssertionError("same-order test could not find next_main target")
    return result


class OilExecutionDeskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.global_run = run_global_macro(42, 7)
        cls.start = oil_futures_payload(
            cls.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        cls.end = oil_futures_payload(
            cls.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=2,
        )
        cls.forecast = generate_oil_short_term_forecast(
            cls.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )

    def test_neutral_score_50_is_the_exact_runtime_baseline(self) -> None:
        profile = build_default_oil_execution_desk_profile()
        self.assertEqual(50.0, profile["capability_total_score"])
        self.assertTrue(profile["governance"]["scores_are_continuous"])
        self.assertTrue(
            profile["governance"]["capability_higher_score_is_better"]
        )
        self.assertFalse(
            profile["governance"]["style_higher_score_is_better"]
        )
        self.assertFalse(
            profile["governance"]["style_total_score_available"]
        )
        self.assertTrue(
            profile["governance"]["capability_style_latents_separate"]
        )
        policy = profile["resolved_policy"]
        self.assertEqual(
            1.0,
            policy["price_execution"]["spread_cost_multiplier"],
        )
        self.assertEqual(
            1.0,
            policy["impact_control"]["slippage_multiplier"],
        )
        self.assertEqual(
            1.0,
            policy["liquidity_scheduling"][
                "visible_liquidity_weight_exponent"
            ],
        )
        self.assertEqual(
            1.0,
            policy["completion_reliability"][
                "normal_trade_capacity_multiplier"
            ],
        )
        self.assertEqual(
            1.0,
            policy["completion_reliability"][
                "normal_order_completion_ratio"
            ],
        )
        self.assertEqual(
            1.0,
            policy["completion_reliability"][
                "normal_trade_completion_multiplier"
            ],
        )
        self.assertEqual(
            1.0,
            policy["roll_coordination"]["roll_cost_multiplier"],
        )
        self.assertEqual(
            1.0,
            policy["fee_efficiency"]["broker_fee_multiplier"],
        )
        weights = [0.41, 0.59]
        self.assertEqual(
            weights,
            adjust_visible_execution_weights(weights, policy),
        )

    def test_registered_v2_assets_are_complete_and_consistent(self) -> None:
        assets = load_registered_assets()
        config = assets["oil_execution_desk_config"]
        contract = assets["oil_execution_desk_contract"]
        self.assertEqual(
            "asset-simulation-oil-execution-desk-v0.2.0",
            config["model_version"],
        )
        self.assertEqual("oil_execution_desk_v2", contract["contract_id"])
        self.assertEqual(
            set(CAPABILITY_DIMENSIONS),
            set(config["candidate_generation"][
                "capability_latent_loadings"
            ]),
        )
        self.assertEqual(
            set(STYLE_DIMENSIONS),
            set(config["candidate_generation"]["style_latent_loadings"]),
        )
        self.assertTrue(
            contract["personnel_philosophy"][
                "capability_and_style_latents_are_separate"
            ]
        )
        self.assertTrue(
            contract["personnel_philosophy"][
                "completion_is_reported_separately_from_cost_tca"
            ]
        )

    def test_decimal_capability_scores_map_continuously_not_by_tier(self) -> None:
        below = resolve_oil_execution_desk_profile(_profile(50.01))
        above = resolve_oil_execution_desk_profile(_profile(50.02))
        below_multiplier = below["resolved_policy"]["impact_control"][
            "slippage_multiplier"
        ]
        above_multiplier = above["resolved_policy"]["impact_control"][
            "slippage_multiplier"
        ]
        self.assertLess(above_multiplier, below_multiplier)
        self.assertLess(abs(above_multiplier - below_multiplier), 0.001)
        self.assertEqual(50.01, below["capability_total_score"])

    def test_style_policy_has_cost_completion_tradeoffs(self) -> None:
        patient_passive = resolve_oil_execution_desk_profile(
            _profile(
                urgency=0.0,
                passive_preference=100.0,
            )
        )
        urgent_aggressive = resolve_oil_execution_desk_profile(
            _profile(
                urgency=100.0,
                passive_preference=0.0,
            )
        )
        patient = patient_passive["resolved_policy"]
        urgent = urgent_aggressive["resolved_policy"]
        self.assertLess(
            patient["impact_control"]["slippage_multiplier"],
            urgent["impact_control"]["slippage_multiplier"],
        )
        self.assertLess(
            patient["price_execution"]["spread_cost_multiplier"],
            urgent["price_execution"]["spread_cost_multiplier"],
        )
        self.assertLess(
            patient["completion_reliability"][
                "normal_order_completion_ratio"
            ],
            urgent["completion_reliability"][
                "normal_order_completion_ratio"
            ],
        )
        self.assertEqual(
            patient_passive["capability_total_score"],
            urgent_aggressive["capability_total_score"],
        )

    def test_same_authorized_order_changes_realized_completion(self) -> None:
        patient_profile = _profile(
            urgency=0.0,
            passive_preference=100.0,
        )
        urgent_profile = _profile(
            urgency=100.0,
            passive_preference=0.0,
        )
        patient_decision = _same_authorized_order_decision(
            self.start,
            self.forecast,
            patient_profile,
        )
        urgent_decision = _same_authorized_order_decision(
            self.start,
            self.forecast,
            urgent_profile,
        )
        self.assertEqual(
            [item["target_position_lots"] for item in patient_decision["targets"]],
            [item["target_position_lots"] for item in urgent_decision["targets"]],
        )

        patient = settle_oil_strategy_turn(
            self.start,
            self.end,
            patient_decision,
            positions={},
            equity_usd=3e9,
        )
        urgent = settle_oil_strategy_turn(
            self.start,
            self.end,
            urgent_decision,
            positions={},
            equity_usd=3e9,
        )

        self.assertGreater(
            urgent["executionSummary"]["net_traded_lots"],
            patient["executionSummary"]["net_traded_lots"],
        )
        self.assertGreater(
            patient["executionSummary"][
                "execution_completion_shortfall_lots"
            ],
            0,
        )
        self.assertEqual(
            0,
            urgent["executionSummary"][
                "execution_completion_shortfall_lots"
            ],
        )
        self.assertLess(
            patient["executionSummary"]["friction_bps"],
            urgent["executionSummary"]["friction_bps"],
        )
        patient_contract = next(
            item
            for item in patient["contracts"]
            if item["role"] == "next_main"
        )
        urgent_contract = next(
            item
            for item in urgent["contracts"]
            if item["role"] == "next_main"
        )
        self.assertEqual(
            patient_contract["requested_delta_lots"],
            urgent_contract["requested_delta_lots"],
        )
        self.assertLess(
            abs(patient_contract["completion_adjusted_requested_delta_lots"]),
            abs(urgent_contract["completion_adjusted_requested_delta_lots"]),
        )
        self.assertTrue(
            patient_contract["clipped_by_execution_completion"]
        )
        self.assertFalse(
            urgent_contract["clipped_by_execution_completion"]
        )

    def test_each_style_axis_changes_realized_same_order_completion(self) -> None:
        pairs = (
            (
                _profile(urgency=0.0, passive_preference=50.0),
                _profile(urgency=100.0, passive_preference=50.0),
            ),
            (
                _profile(urgency=50.0, passive_preference=100.0),
                _profile(urgency=50.0, passive_preference=0.0),
            ),
        )
        for low_completion_profile, high_completion_profile in pairs:
            with self.subTest(
                low=low_completion_profile["execution_style"],
                high=high_completion_profile["execution_style"],
            ):
                low_decision = _same_authorized_order_decision(
                    self.start,
                    self.forecast,
                    low_completion_profile,
                )
                high_decision = _same_authorized_order_decision(
                    self.start,
                    self.forecast,
                    high_completion_profile,
                )
                low = settle_oil_strategy_turn(
                    self.start,
                    self.end,
                    low_decision,
                    positions={},
                    equity_usd=3e9,
                )
                high = settle_oil_strategy_turn(
                    self.start,
                    self.end,
                    high_decision,
                    positions={},
                    equity_usd=3e9,
                )
                self.assertGreater(
                    high["executionSummary"]["net_traded_lots"],
                    low["executionSummary"]["net_traded_lots"],
                )
                self.assertLess(
                    low["executionSummary"]["friction_bps"],
                    high["executionSummary"]["friction_bps"],
                )

    def test_forced_required_reduction_bypasses_style_completion(self) -> None:
        base_decision = build_oil_strategy_decision(
            self.start,
            self.forecast,
            execution_desk_profile=_profile(),
        )
        next_contract_id = next(
            str(item["contract_id"])
            for item in base_decision["targets"]
            if str(item["role"]) == "next_main"
        )
        end_contract = next(
            item
            for item in self.end["curve"]["contracts"]
            if str(item["contract_id"]) == next_contract_id
        )
        position_limit = int(
            end_contract["participantLimits"][
                "single_contract_position_limit_lots"
            ]
        )
        starting_position = position_limit + 1_000

        def forced_decision(execution_profile: dict) -> dict:
            decision = _same_authorized_order_decision(
                self.start,
                self.forecast,
                execution_profile,
                target_lots=position_limit,
            )
            return decision

        patient = settle_oil_strategy_turn(
            self.start,
            self.end,
            forced_decision(
                _profile(
                    urgency=0.0,
                    passive_preference=100.0,
                )
            ),
            positions={str(end_contract["contract_id"]): starting_position},
            equity_usd=3e9,
        )
        urgent = settle_oil_strategy_turn(
            self.start,
            self.end,
            forced_decision(
                _profile(
                    urgency=100.0,
                    passive_preference=0.0,
                )
            ),
            positions={str(end_contract["contract_id"]): starting_position},
            equity_usd=3e9,
        )

        patient_contract = next(
            item
            for item in patient["contracts"]
            if item["contract_id"] == end_contract["contract_id"]
        )
        urgent_contract = next(
            item
            for item in urgent["contracts"]
            if item["contract_id"] == end_contract["contract_id"]
        )
        self.assertEqual(
            patient_contract["required_risk_reduction_lots"],
            -1_000,
        )
        self.assertEqual(
            patient_contract["completion_adjusted_requested_delta_lots"],
            patient_contract["requested_delta_lots"],
        )
        self.assertEqual(
            urgent_contract["completion_adjusted_requested_delta_lots"],
            urgent_contract["requested_delta_lots"],
        )
        self.assertEqual(
            patient_contract["executed_delta_lots"],
            urgent_contract["executed_delta_lots"],
        )
        self.assertEqual(
            0,
            patient_contract["execution_completion_shortfall_lots"],
        )
        self.assertEqual(
            0,
            urgent_contract["execution_completion_shortfall_lots"],
        )

    def test_cash_settlement_is_not_execution_completion_shortfall(self) -> None:
        start = oil_futures_payload(
            self.global_run,
            as_of_year=2030,
            as_of_month=5,
            as_of_half=1,
        )
        end = oil_futures_payload(
            self.global_run,
            as_of_year=2030,
            as_of_month=5,
            as_of_half=2,
        )
        forecast = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=5,
            as_of_half=1,
        )
        expiring_contract = next(
            item
            for item in end["curve"]["contracts"]
            if int(item["expiry_year"]) == 2030
            and int(item["expiry_month"]) == 5
        )
        contract_id = str(expiring_contract["contract_id"])
        starting_position = 100
        positions = {contract_id: starting_position}
        decision = build_oil_strategy_decision(
            start,
            forecast,
            positions=positions,
            equity_usd=3e9,
            execution_desk_profile=_profile(),
        )
        settlement = settle_oil_strategy_turn(
            start,
            end,
            decision,
            positions=positions,
            equity_usd=3e9,
        )
        contract = next(
            item
            for item in settlement["contracts"]
            if item["contract_id"] == contract_id
        )

        self.assertTrue(contract["final_settlement"])
        self.assertEqual(
            -starting_position,
            contract["required_risk_reduction_lots"],
        )
        self.assertEqual(0, contract["requested_delta_lots"])
        self.assertEqual(
            0,
            contract["completion_adjusted_requested_delta_lots"],
        )
        self.assertEqual(
            0,
            contract["execution_completion_shortfall_lots"],
        )
        self.assertFalse(contract["clipped_by_execution_completion"])
        self.assertFalse(contract["clipped_by_trade_limit"])
        self.assertEqual(0, contract["executed_delta_lots"])
        self.assertEqual(
            -starting_position,
            contract["settlement_delta_lots"],
        )
        self.assertEqual(0, contract["ending_position_lots"])
        self.assertEqual(
            0,
            settlement["executionSummary"][
                "execution_completion_shortfall_lots"
            ],
        )

    def test_window_timing_is_style_not_execution_quality(self) -> None:
        front = resolve_oil_execution_desk_profile(
            _profile(window_timing=0.0)
        )
        back = resolve_oil_execution_desk_profile(
            _profile(window_timing=100.0)
        )
        weights = [0.5, 0.5]
        front_weights = adjust_visible_execution_weights(
            weights,
            front["resolved_policy"],
        )
        back_weights = adjust_visible_execution_weights(
            weights,
            back["resolved_policy"],
        )
        self.assertGreater(front_weights[0], front_weights[1])
        self.assertLess(back_weights[0], back_weights[1])
        self.assertEqual(
            front["capability_total_score"],
            back["capability_total_score"],
        )

    def test_seeded_roster_is_deterministic_non_flat_and_tamper_evident(self) -> None:
        first = generate_oil_execution_desk_roster(
            seed=42,
            candidate_count=5,
        )
        self.assertEqual(
            first,
            generate_oil_execution_desk_roster(
                seed=42,
                candidate_count=5,
            ),
        )
        self.assertNotEqual(
            [item["profile_hash"] for item in first["candidates"]],
            [
                item["profile_hash"]
                for item in generate_oil_execution_desk_roster(
                    seed=43,
                    candidate_count=5,
                )["candidates"]
            ],
        )
        profile = generate_oil_execution_desk_candidate(
            seed=7,
            candidate_index=2,
        )
        modified = deepcopy(profile)
        modified["capability_radar"]["price_execution"] += 0.01
        with self.assertRaises(ValueError):
            resolve_oil_execution_desk_profile(modified)

    def test_capability_and_style_latents_are_separate(self) -> None:
        candidates = [
            generate_oil_execution_desk_candidate(
                seed=seed,
                candidate_index=index,
            )
            for seed in range(64)
            for index in range(8)
        ]
        totals = [
            float(item["capability_total_score"])
            for item in candidates
        ]
        for style in STYLE_DIMENSIONS:
            style_values = [
                float(item["execution_style"][style])
                for item in candidates
            ]
            self.assertLess(
                abs(correlation(totals, style_values)),
                0.15,
                style,
            )
        price = [
            float(item["capability_radar"]["price_execution"])
            for item in candidates
        ]
        impact = [
            float(item["capability_radar"]["impact_control"])
            for item in candidates
        ]
        self.assertGreater(correlation(price, impact), 0.60)

    def test_execution_skill_changes_cost_not_forecast_target_intent(self) -> None:
        low = build_oil_strategy_decision(
            self.start,
            self.forecast,
            execution_desk_profile=_profile(0.0),
        )
        high = build_oil_strategy_decision(
            self.start,
            self.forecast,
            execution_desk_profile=_profile(100.0),
        )
        self.assertEqual(
            [item["target_position_lots"] for item in low["targets"]],
            [item["target_position_lots"] for item in high["targets"]],
        )
        low_turn = settle_oil_strategy_turn(
            self.start,
            self.end,
            low,
            positions={},
            equity_usd=3e9,
        )
        high_turn = settle_oil_strategy_turn(
            self.start,
            self.end,
            high,
            positions={},
            equity_usd=3e9,
        )
        self.assertLessEqual(
            high_turn["executionSummary"]["tca"][
                "actual_execution_cost_usd"
            ],
            low_turn["executionSummary"]["tca"][
                "actual_execution_cost_usd"
            ],
        )


if __name__ == "__main__":
    unittest.main()
