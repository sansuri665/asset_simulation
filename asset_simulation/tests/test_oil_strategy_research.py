from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.oil_strategy_research import (
    STRATEGY_STYLE_DIMENSIONS,
    build_default_oil_strategy_research_profile,
    generate_oil_strategy_research_candidate,
    generate_oil_strategy_research_roster,
    resolve_oil_strategy_research_profile,
)


class OilStrategyResearchTests(unittest.TestCase):
    def test_roster_is_deterministic_seeded_and_non_flat(self) -> None:
        first = generate_oil_strategy_research_roster(seed=42, candidate_count=5)
        repeat = generate_oil_strategy_research_roster(seed=42, candidate_count=5)
        another = generate_oil_strategy_research_roster(seed=43, candidate_count=5)
        self.assertEqual(first, repeat)
        self.assertNotEqual(
            [item["profile_hash"] for item in first["candidates"]],
            [item["profile_hash"] for item in another["candidates"]],
        )
        for item in first["candidates"]:
            self.assertEqual(set(STRATEGY_STYLE_DIMENSIONS), set(item["style_radar"]))
            self.assertGreater(
                max(item["style_radar"].values())
                - min(item["style_radar"].values()),
                5.0,
            )
            self.assertIsNone(item["preference_total_score"])
            self.assertFalse(item["governance"]["player_can_edit_radar"])
            self.assertFalse(item["governance"]["higher_score_is_better"])
        orientation_scores = [
            float(item["style_radar"]["continuation_reversion"])
            for item in first["candidates"]
        ]
        self.assertLess(min(orientation_scores), 35.0)
        self.assertGreater(max(orientation_scores), 65.0)

    def test_default_profile_preserves_the_prior_signal_mix_and_tempo(self) -> None:
        profile = build_default_oil_strategy_research_profile()
        policy = profile["resolved_policy"]
        self.assertEqual(
            50.0,
            policy["risk"]["capital_deployment_pct_of_allocated_equity"],
        )
        self.assertEqual(0.3, policy["signal"]["continuation_weight"])
        self.assertEqual(0.7, policy["signal"]["reversion_weight"])
        self.assertEqual(
            {"main": 0.65, "next_main": 0.35}, policy["risk"]["role_weights"]
        )
        self.assertEqual([0.5, 0.3, 0.2], policy["signal"]["horizon_weights"])
        self.assertEqual(0.5, policy["execution"]["adjustment_speed"])
        self.assertEqual(0.15, policy["execution"]["signal_deadband_abs"])
        self.assertEqual(1.15, policy["execution"]["minimum_trade_edge_pct"])
        self.assertAlmostEqual(
            (0.15 * 30.0) ** 0.5,
            policy["execution"]["gross_turnover_multiplier"],
        )
        self.assertEqual(1.875, policy["execution"]["expected_holding_turns"])
        self.assertEqual(0.175, policy["execution"]["position_persistence"])

    def test_generated_profile_cannot_be_modified_after_appointment(self) -> None:
        profile = generate_oil_strategy_research_candidate(seed=7, candidate_index=2)
        self.assertEqual(profile, resolve_oil_strategy_research_profile(profile))
        modified = deepcopy(profile)
        modified["style_radar"]["continuation_reversion"] += 1.0
        with self.assertRaises(ValueError):
            resolve_oil_strategy_research_profile(modified)

    def test_candidate_count_and_identity_inputs_are_bounded(self) -> None:
        for invalid in (0, 9, True):
            with self.assertRaises(ValueError):
                generate_oil_strategy_research_roster(
                    seed=42, candidate_count=invalid
                )
        for invalid_seed in (-1, True):
            with self.assertRaises(ValueError):
                generate_oil_strategy_research_candidate(
                    seed=invalid_seed, candidate_index=0
                )


if __name__ == "__main__":
    unittest.main()
