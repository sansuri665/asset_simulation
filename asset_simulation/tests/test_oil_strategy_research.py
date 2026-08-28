from __future__ import annotations

from copy import deepcopy
import inspect
import math
import unittest

from asset_simulation.model import oil_strategy_research
from asset_simulation.model.oil_short_term_forecast import (
    generate_oil_short_term_forecast,
)
from asset_simulation.model.oil_strategy_research import (
    STRATEGY_STYLE_DIMENSIONS,
    build_default_oil_strategy_research_profile,
    generate_oil_strategy_research_candidate,
    generate_oil_strategy_research_roster,
    resolve_oil_strategy_research_profile,
    resolve_oil_strategy_runtime_policy,
)
from asset_simulation.model.oil_trading_strategy import build_oil_strategy_decision
from asset_simulation.model.registry import load_registered_assets


FORBIDDEN_PM_QUALITY_FIELDS = {
    "capability_total_score",
    "quality_score",
    "alpha_score",
    "investment_skill",
    "compatibility_score",
}


def _all_mapping_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        nested_keys = [_all_mapping_keys(item) for item in value.values()]
        return set(value).union(*nested_keys)
    if isinstance(value, (list, tuple)):
        nested_keys = [_all_mapping_keys(item) for item in value]
        return set().union(*nested_keys)
    return set()


def _correlation(rows: list[dict[str, float]], left: str, right: str) -> float:
    left_values = [float(row[left]) for row in rows]
    right_values = [float(row[right]) for row in rows]
    left_mean = sum(left_values) / len(left_values)
    right_mean = sum(right_values) / len(right_values)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left_values, right_values, strict=True)
    )
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left_values)
        * sum((value - right_mean) ** 2 for value in right_values)
    )
    return numerator / denominator


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

    def test_style_first_profiles_have_no_hidden_pm_quality_or_ranking(self) -> None:
        roster = generate_oil_strategy_research_roster(seed=42, candidate_count=8)
        self.assertEqual(
            list(range(8)),
            [item["appointment"]["candidate_index"] for item in roster["candidates"]],
        )
        for profile in roster["candidates"]:
            self.assertIsNone(profile["preference_total_score"])
            self.assertNotIn("latent_traits", profile)
            self.assertTrue(
                FORBIDDEN_PM_QUALITY_FIELDS.isdisjoint(_all_mapping_keys(profile))
            )
        public_callables = {
            name
            for name, value in vars(oil_strategy_research).items()
            if not name.startswith("_") and callable(value)
        }
        self.assertFalse(
            any(
                token in name.lower()
                for name in public_callables
                for token in ("rank", "quality", "alpha", "compatibility")
            )
        )

    def test_latent_personality_keeps_style_dimensions_correlated(self) -> None:
        radars = [
            generate_oil_strategy_research_candidate(
                seed=seed, candidate_index=candidate_index
            )["style_radar"]
            for seed in range(32)
            for candidate_index in range(8)
        ]
        self.assertGreater(
            _correlation(radars, "responsiveness", "turnover_activity"), 0.75
        )
        self.assertGreater(
            _correlation(radars, "selectivity", "holding_patience"), 0.65
        )
        self.assertLess(
            _correlation(radars, "near_month_focus", "forecast_horizon"), -0.55
        )

    def test_style_changes_runtime_policy_without_owning_capital_allocation(self) -> None:
        conservative = build_default_oil_strategy_research_profile()
        conservative.pop("profile_hash")
        conservative["style_radar"]["capital_deployment"] = 0.0
        conservative["style_radar"]["responsiveness"] = 0.0
        aggressive = deepcopy(conservative)
        aggressive["style_radar"]["capital_deployment"] = 100.0
        aggressive["style_radar"]["responsiveness"] = 100.0
        _, conservative_policy = resolve_oil_strategy_runtime_policy(conservative)
        _, aggressive_policy = resolve_oil_strategy_runtime_policy(aggressive)
        self.assertLess(
            conservative_policy["risk"][
                "capital_deployment_pct_of_allocated_equity"
            ],
            aggressive_policy["risk"][
                "capital_deployment_pct_of_allocated_equity"
            ],
        )
        self.assertLess(
            conservative_policy["execution"]["adjustment_speed"],
            aggressive_policy["execution"]["adjustment_speed"],
        )
        self.assertNotIn("authorized_capital_usd", conservative_policy["risk"])
        self.assertNotIn("authorized_capital_usd", aggressive_policy["risk"])

    def test_pm_api_has_no_forecast_or_hidden_future_input(self) -> None:
        forecast_parameters = inspect.signature(
            generate_oil_short_term_forecast
        ).parameters
        self.assertNotIn("strategy_research_profile", forecast_parameters)
        self.assertNotIn("pm_profile", forecast_parameters)

        decision_parameters = inspect.signature(build_oil_strategy_decision).parameters
        self.assertTrue(
            {"market", "forecast_vintage", "positions", "strategy_research_profile"}
            <= set(decision_parameters)
        )
        self.assertTrue(
            {"global_run", "next_market", "future_market"}.isdisjoint(
                decision_parameters
            )
        )
        self.assertNotIn(
            "oil_futures_payload(", inspect.getsource(build_oil_strategy_decision)
        )

    def test_contract_declares_style_first_governance(self) -> None:
        philosophy = load_registered_assets()["oil_strategy_research_contract"][
            "personnel_philosophy"
        ]
        self.assertEqual("style_first", philosophy["system_type"])
        self.assertFalse(philosophy["aggregate_pm_quality_score_available"])
        self.assertFalse(philosophy["style_endpoints_have_universal_ordering"])
        self.assertFalse(philosophy["latent_traits_represent_capability"])
        self.assertFalse(
            philosophy["cross_department_compatibility_score_available"]
        )
        self.assertFalse(philosophy["hidden_future_access"])
        self.assertEqual("investment_decision", philosophy["capital_allocation_owner"])

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
