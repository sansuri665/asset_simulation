from __future__ import annotations

from copy import deepcopy
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


def _calibration_profile(capability_score: float) -> dict:
    return {
        "appointment": {
            "personnel_id": f"calibration_{capability_score}",
            "display_name": f"校准{capability_score}",
            "source": "test_calibration",
            "candidate_index": None,
            "generation_seed": None,
        },
        "capability_radar": {
            key: capability_score for key in CAPABILITY_DIMENSIONS
        },
        "execution_style": {key: 50.0 for key in STYLE_DIMENSIONS},
    }


class OilExecutionDeskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.global_run = run_global_macro(42, 7)

    def test_neutral_score_50_is_the_exact_runtime_baseline(self) -> None:
        profile = build_default_oil_execution_desk_profile()
        self.assertEqual(50.0, profile["capability_total_score"])
        self.assertTrue(profile["governance"]["scores_are_continuous"])
        policy = profile["resolved_policy"]
        self.assertEqual(1.0, policy["price_execution"]["spread_cost_multiplier"])
        self.assertEqual(1.0, policy["impact_control"]["slippage_multiplier"])
        self.assertEqual(
            1.0,
            policy["liquidity_scheduling"]["visible_liquidity_weight_exponent"],
        )
        self.assertEqual(
            1.0,
            policy["completion_reliability"]["normal_trade_completion_multiplier"],
        )
        self.assertEqual(1.0, policy["roll_coordination"]["roll_cost_multiplier"])
        self.assertEqual(1.0, policy["fee_efficiency"]["broker_fee_multiplier"])
        weights = [0.41, 0.59]
        self.assertEqual(weights, adjust_visible_execution_weights(weights, policy))

    def test_decimal_scores_map_continuously_not_by_tier(self) -> None:
        below = resolve_oil_execution_desk_profile(_calibration_profile(50.01))
        above = resolve_oil_execution_desk_profile(_calibration_profile(50.02))
        below_multiplier = below["resolved_policy"]["impact_control"]["slippage_multiplier"]
        above_multiplier = above["resolved_policy"]["impact_control"]["slippage_multiplier"]
        self.assertLess(above_multiplier, below_multiplier)
        self.assertLess(abs(above_multiplier - below_multiplier), 0.001)
        self.assertEqual(50.01, below["capability_total_score"])

    def test_seeded_roster_is_deterministic_non_flat_and_tamper_evident(self) -> None:
        first = generate_oil_execution_desk_roster(seed=42, candidate_count=5)
        self.assertEqual(first, generate_oil_execution_desk_roster(seed=42, candidate_count=5))
        self.assertNotEqual(
            [item["profile_hash"] for item in first["candidates"]],
            [
                item["profile_hash"]
                for item in generate_oil_execution_desk_roster(seed=43, candidate_count=5)["candidates"]
            ],
        )
        self.assertTrue(
            any(
                any(float(value) != round(float(value)) for value in item["capability_radar"].values())
                for item in first["candidates"]
            )
        )
        profile = generate_oil_execution_desk_candidate(seed=7, candidate_index=2)
        modified = deepcopy(profile)
        modified["capability_radar"]["price_execution"] += 0.01
        with self.assertRaises(ValueError):
            resolve_oil_execution_desk_profile(modified)

    def test_execution_skill_changes_cost_not_forecast_target_intent(self) -> None:
        start = oil_futures_payload(
            self.global_run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        end = oil_futures_payload(
            self.global_run, as_of_year=2030, as_of_month=1, as_of_half=2
        )
        forecast = generate_oil_short_term_forecast(
            self.global_run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        low = build_oil_strategy_decision(
            start, forecast, execution_desk_profile=_calibration_profile(0.0)
        )
        high = build_oil_strategy_decision(
            start, forecast, execution_desk_profile=_calibration_profile(100.0)
        )
        self.assertEqual(
            [item["target_position_lots"] for item in low["targets"]],
            [item["target_position_lots"] for item in high["targets"]],
        )
        low_turn = settle_oil_strategy_turn(start, end, low, positions={}, equity_usd=3e9)
        high_turn = settle_oil_strategy_turn(start, end, high, positions={}, equity_usd=3e9)
        self.assertLessEqual(
            high_turn["executionSummary"]["tca"]["actual_execution_cost_usd"],
            low_turn["executionSummary"]["tca"]["actual_execution_cost_usd"],
        )
        self.assertLessEqual(
            low_turn["executionSummary"]["tca"]["execution_value_added_usd"], 0.0
        )
        self.assertGreaterEqual(
            high_turn["executionSummary"]["tca"]["execution_value_added_usd"], 0.0
        )


if __name__ == "__main__":
    unittest.main()
