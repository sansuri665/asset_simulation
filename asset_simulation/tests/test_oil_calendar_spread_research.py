from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.oil_calendar_spread_research import (
    CALENDAR_SPREAD_STYLE_DIMENSIONS,
    OIL_CALENDAR_SPREAD_RESEARCH_MODEL_VERSION,
    resolve_oil_calendar_spread_research_profile,
    resolve_oil_calendar_spread_runtime_policy,
)
from asset_simulation.model.oil_strategy_research import (
    build_default_oil_strategy_research_profile,
    generate_oil_strategy_research_candidate,
)
from asset_simulation.model.registry import load_registered_assets


class OilCalendarSpreadResearchTests(unittest.TestCase):
    def test_registered_dedicated_style_owner_and_neutral_default(self) -> None:
        assets = load_registered_assets()
        config = assets["oil_calendar_spread_research_config"]
        self.assertEqual(OIL_CALENDAR_SPREAD_RESEARCH_MODEL_VERSION, config["model_version"])
        profile = resolve_oil_calendar_spread_research_profile(None)
        self.assertEqual(set(CALENDAR_SPREAD_STYLE_DIMENSIONS), set(profile["style_radar"]))
        self.assertTrue(all(value == 50.0 for value in profile["style_radar"].values()))
        self.assertIsNone(profile["preference_total_score"])
        self.assertIsNone(profile["alpha_score"])
        self.assertFalse(profile["governance"]["higher_score_is_better"])
        self.assertFalse(profile["governance"]["player_can_edit_dedicated_radar"])
        self.assertEqual(0.70, profile["resolved_policy"]["signal"]["forecast_component_weight"])
        self.assertEqual(0.30, profile["resolved_policy"]["signal"]["visible_curve_component_weight"])

    def test_same_person_projection_is_deterministic_but_not_directional_clone(self) -> None:
        source = generate_oil_strategy_research_candidate(seed=42, candidate_index=2)
        first = resolve_oil_calendar_spread_research_profile(source)
        repeat = resolve_oil_calendar_spread_research_profile(source)
        self.assertEqual(first, repeat)
        self.assertEqual(source["profile_hash"], first["source_strategy_profile_hash"])
        self.assertEqual(
            source["appointment"]["personnel_id"], first["appointment"]["personnel_id"]
        )
        self.assertNotEqual(
            source["style_radar"]["continuation_reversion"],
            first["style_radar"]["curve_continuation_reversion"],
        )
        self.assertTrue(
            all(10.0 <= value <= 90.0 for value in first["style_radar"].values())
        )

    def test_base_style_changes_feed_correlated_calendar_spread_preferences(self) -> None:
        neutral = deepcopy(build_default_oil_strategy_research_profile())
        neutral.pop("profile_hash")
        neutral["appointment"] = {
            **neutral["appointment"],
            "personnel_id": "test_nondefault_pm",
            "display_name": "Test PM",
            "source": "test_profile",
        }
        trend = deepcopy(neutral)
        trend["style_radar"]["continuation_reversion"] = 90.0
        reversion = deepcopy(neutral)
        reversion["style_radar"]["continuation_reversion"] = 10.0
        trend_profile = resolve_oil_calendar_spread_research_profile(trend)
        reversion_profile = resolve_oil_calendar_spread_research_profile(reversion)
        self.assertGreater(
            trend_profile["style_radar"]["curve_continuation_reversion"],
            reversion_profile["style_radar"]["curve_continuation_reversion"],
        )

    def test_forecast_vs_visible_curve_is_calendar_spread_specific(self) -> None:
        source = generate_oil_strategy_research_candidate(seed=20260830, candidate_index=4)
        dedicated, policy, reference_profile = resolve_oil_calendar_spread_runtime_policy(source)
        score = dedicated["style_radar"]["forecast_vs_visible_curve"]
        forecast_weight = policy["signal"]["forecast_component_weight"]
        visible_weight = policy["signal"]["visible_curve_component_weight"]
        self.assertAlmostEqual(1.0, forecast_weight + visible_weight)
        self.assertTrue(0.45 <= forecast_weight <= 0.90)
        self.assertEqual(50.0, reference_profile["style_radar"]["near_month_focus"])
        self.assertEqual(
            dedicated["style_radar"]["curve_continuation_reversion"],
            reference_profile["style_radar"]["continuation_reversion"],
        )
        if score > 50.0:
            self.assertGreater(forecast_weight, 0.70)
        elif score < 50.0:
            self.assertLess(forecast_weight, 0.70)

    def test_construction_capability_is_passed_through_not_duplicated(self) -> None:
        source = generate_oil_strategy_research_candidate(seed=99, candidate_index=1)
        dedicated = resolve_oil_calendar_spread_research_profile(source)
        self.assertEqual(
            source["construction_capability_radar"],
            dedicated["construction_capability_radar"],
        )
        self.assertEqual(
            "oil_strategy_research_v2", dedicated["construction_capability_owner"]
        )
        self.assertFalse(dedicated["governance"]["construction_capability_duplicated"])


if __name__ == "__main__":
    unittest.main()
