from __future__ import annotations

import unittest

from asset_simulation.audit_oil_investment_decision import (
    INSTITUTION_PROFILE_SPECS,
    _institution_profiles,
    _path_statistics,
    _state_choices,
    _summarize_cross_rows,
    _train_decision_model,
    _visible_feature,
)


class OilInvestmentDecisionAuditTests(unittest.TestCase):
    def test_path_statistics_compound_returns_and_penalize_drawdown(self) -> None:
        report = _path_statistics([0.10, -0.10])

        self.assertAlmostEqual(-1.0, report["return_pct"], places=9)
        self.assertAlmostEqual(-10.0, report["maximum_drawdown_pct"], places=9)
        self.assertLess(
            report["decision_utility"], report["annualized_return_pct"]
        )

    def test_visible_feature_uses_only_current_curve_and_published_weeks(self) -> None:
        market = {
            "curve": {"state": "contango"},
            "reference": {
                "monthly": [
                    {
                        "weekly": [
                            {"close": 100.0},
                            {"close": 105.0},
                        ]
                    }
                ]
            },
        }

        self.assertEqual("contango|up", _visible_feature(market))

    def test_calibration_can_learn_state_specific_appointments(self) -> None:
        calibration = [
            {
                "blocks": [
                    {
                        "feature": "contango|up",
                        "team_utilities": [1.0, 4.0],
                    },
                    {
                        "feature": "backwardation|down",
                        "team_utilities": [4.0, 1.0],
                    },
                ]
            }
        ]
        model = _train_decision_model(calibration, team_count=2)

        choices = _state_choices(
            ["contango|up"] * 6 + ["backwardation|down"] * 6,
            model,
            total_turns=12,
            block_turns=6,
            sticky=True,
        )

        self.assertEqual([1] * 6 + [0] * 6, choices)

    def test_institution_cross_profiles_are_distinct_and_inside_score_ranges(self) -> None:
        profiles, descriptions = _institution_profiles()

        self.assertEqual(len(INSTITUTION_PROFILE_SPECS), len(profiles))
        self.assertEqual(len(profiles), len({row["profile_hash"] for row in profiles}))
        for description in descriptions:
            lower, upper = description["requested_score_range"]
            self.assertGreaterEqual(description["capability_total_score"], lower)
            self.assertLessEqual(description["capability_total_score"], upper)

    def test_cross_summary_tracks_bounded_limit_remediation(self) -> None:
        row = {
            "annualized_return_pct": 8.0,
            "maximum_drawdown_pct": -2.0,
            "decision_utility": 5.0,
            "traded_lots": 100,
            "execution_cost_usd": 1_000.0,
            "maximum_margin_to_equity_pct": 7.0,
            "position_limit_excess_turns": 1,
            "position_limit_excess_lots_total": 200,
            "maximum_position_limit_excess_lots": 200,
            "maximum_position_limit_excess_streak": 1,
            "turn_count": 72,
        }

        summary = _summarize_cross_rows([row])

        self.assertEqual(1, summary["total_position_limit_excess_turns"])
        self.assertEqual(200, summary["maximum_position_limit_excess_lots"])
        self.assertEqual(1, summary["maximum_position_limit_excess_streak"])
        self.assertEqual(72, summary["turn_observation_count"])


if __name__ == "__main__":
    unittest.main()
