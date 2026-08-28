from __future__ import annotations

from statistics import correlation, mean
import unittest

from asset_simulation.model.oil_forecast_research_profile import (
    FORECAST_RESEARCH_STYLE_DIMENSIONS,
    research_behavior,
)
from asset_simulation.model.oil_short_term_forecast import (
    OIL_SHORT_TERM_FORECAST_MODEL_VERSION,
    RADAR_DIMENSIONS,
    _forecast_contract,
    aggregate_oil_short_term_scorecards,
    build_institution_profile,
    generate_institution_profile_for_score_range,
    generate_oil_short_term_forecast,
    score_oil_short_term_forecast,
)
from asset_simulation.model.oil_short_term_forecast_session import (
    OilShortTermForecastSession,
)
from asset_simulation.model.registry import load_registered_assets
from asset_simulation.server import build_oil_short_term_forecast_payload
from asset_simulation.tests.support import cached_global_run


class OilShortTermForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.global_run = cached_global_run(42, 5)

    def test_two_contract_weekly_forecast_is_deterministic_and_contains_no_truth(self) -> None:
        first = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        second = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            OIL_SHORT_TERM_FORECAST_MODEL_VERSION,
            first["identity"]["model_version"],
        )
        self.assertEqual(
            "oil_short_term_forecast_v2",
            first["identity"]["field_contract_id"],
        )
        self.assertFalse(first["identity"]["write_back"])
        self.assertFalse(first["identity"]["future_market_bars_in_output"])
        self.assertEqual(
            [("main", "OIL-3005", 18), ("next_main", "OIL-3009", 34)],
            [
                (item["role"], item["contract_id"], len(item["weekly"]))
                for item in first["forecasts"]
            ],
        )
        allowed_bar_fields = {
            "target_week",
            "week_serial",
            "year",
            "month",
            "week",
            "horizon_weeks",
            "open",
            "high",
            "low",
            "close",
            "confidence_low",
            "confidence_high",
            "revision_pct",
            "contract_phase",
        }
        for forecast in first["forecasts"]:
            for bar in forecast["weekly"]:
                self.assertEqual(allowed_bar_fields, set(bar))
                self.assertLessEqual(bar["low"], min(bar["open"], bar["close"]))
                self.assertGreaterEqual(bar["high"], max(bar["open"], bar["close"]))
                self.assertLess(bar["confidence_low"], bar["close"])
                self.assertGreater(bar["confidence_high"], bar["close"])

    def test_short_and_long_worlds_have_the_same_near_forecast(self) -> None:
        short = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        long = generate_oil_short_term_forecast(
            cached_global_run(42, 60),
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        self.assertEqual(short["forecasts"], long["forecasts"])
        self.assertEqual(short["institution"], long["institution"])

    def test_half_month_revision_and_realized_scoring_remain_joint(self) -> None:
        first = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        revised = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=2,
            previous_vintage=first,
        )
        self.assertEqual(
            first["identity"]["vintage_id"],
            revised["identity"]["previous_vintage_id"],
        )
        self.assertIn(
            revised["revision"]["reason"],
            {"routine_update", "surprise_update"},
        )
        self.assertGreater(revised["revision"]["revised_target_count"], 0)

        old = {
            (forecast["contract_id"], bar["week_serial"]): bar["close"]
            for forecast in first["forecasts"]
            for bar in forecast["weekly"]
        }
        overlap_changes = [
            abs(
                float(bar["close"])
                / old[(forecast["contract_id"], bar["week_serial"])]
                - 1.0
            )
            for forecast in revised["forecasts"]
            for bar in forecast["weekly"]
            if (forecast["contract_id"], bar["week_serial"]) in old
        ]
        self.assertTrue(overlap_changes)
        self.assertLess(max(overlap_changes), 0.15)

        scorecard = score_oil_short_term_forecast(
            revised,
            self.global_run,
            evaluation_year=2030,
            evaluation_month=4,
            evaluation_half=2,
            previous_vintage=first,
        )
        self.assertEqual("partial", scorecard["status"])
        self.assertGreater(scorecard["realized_by_role"]["main"], 0)
        self.assertGreater(scorecard["realized_by_role"]["next_main"], 0)
        self.assertGreater(scorecard["revision_pair_count"], 0)
        self.assertFalse(scorecard["future_values_released"])
        self.assertEqual(set(RADAR_DIMENSIONS), set(scorecard["dimension_scores"]))
        for value in scorecard["dimension_scores"].values():
            if value is not None:
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 100.0)
        self.assertGreaterEqual(scorecard["overall_score"], 0.0)
        self.assertLessEqual(scorecard["overall_score"], 100.0)
        track_record = aggregate_oil_short_term_scorecards([scorecard])
        self.assertEqual(1, track_record["vintage_count"])
        self.assertEqual(
            scorecard["overall_score"],
            track_record["overall_score"],
        )

    def test_direct_capabilities_keep_measurable_skill_separation(self) -> None:
        radar_sets = {
            "lower": {key: 45.0 for key in RADAR_DIMENSIONS},
            "middle": {key: 70.0 for key in RADAR_DIMENSIONS},
            "higher": {key: 85.0 for key in RADAR_DIMENSIONS},
        }
        scores: dict[str, list[float]] = {key: [] for key in radar_sets}
        for seed in range(6):
            run = cached_global_run(seed, 5)
            for label, radar in radar_sets.items():
                profile = build_institution_profile(
                    institution_id=f"audit_{label}",
                    capability_radar=radar,
                    research_style={
                        key: 50.0 for key in FORECAST_RESEARCH_STYLE_DIMENSIONS
                    },
                )
                vintage = generate_oil_short_term_forecast(
                    run,
                    as_of_year=2030,
                    as_of_month=1,
                    as_of_half=1,
                    institution_profile=profile,
                )
                scorecard = score_oil_short_term_forecast(
                    vintage,
                    run,
                    evaluation_year=2030,
                    evaluation_month=4,
                    evaluation_half=2,
                )
                scores[label].append(float(scorecard["overall_score"]))
        lower = mean(scores["lower"])
        middle = mean(scores["middle"])
        higher = mean(scores["higher"])
        self.assertGreater(middle, lower + 8.0)
        self.assertGreater(higher, middle + 8.0)

    def test_zero_skill_path_and_range_do_not_retain_hidden_future_shape(self) -> None:
        profile = build_institution_profile(
            institution_id="zero_skill_hidden_shape_guard",
            capability_radar={key: 0.0 for key in RADAR_DIMENSIONS},
            research_style={
                key: 50.0 for key in FORECAST_RESEARCH_STYLE_DIMENSIONS
            },
        )
        contract = {
            "contract_id": "guard-contract",
            "code": "OIL-GUARD",
            "name": "guard",
            "price_usd": 80.0,
            "expiry_year": 2030,
            "expiry_month": 9,
            "expiry_label": "2030-09",
            "monthly": [
                {
                    "year": 2029,
                    "month": 12,
                    "weekly": [
                        {
                            "week": index + 1,
                            "open": 79.0 + index,
                            "high": 81.0 + index,
                            "low": 78.0 + index,
                            "close": 80.0 + index,
                        }
                        for index in range(4)
                    ],
                }
            ],
        }
        truth_a = [
            {
                "target_week": f"2030-01-W{index + 1}",
                "week_serial": 97_000 + index,
                "year": 2030,
                "month": 1,
                "week": index + 1,
                "open": 80.0,
                "high": 83.0 + index,
                "low": 77.0 - index,
                "close": 81.0 + index,
            }
            for index in range(4)
        ]
        truth_b = [
            {
                **item,
                "open": 80.0,
                "high": 105.0 + index,
                "low": 58.0 - index,
                "close": 100.0 + index,
            }
            for index, item in enumerate(truth_a)
        ]
        error_config = load_registered_assets()["oil_short_term_forecast_config"][
            "error_model"
        ]
        first, _ = _forecast_contract(
            global_run=self.global_run,
            profile=profile,
            role="main",
            contract=contract,
            truth=truth_a,
            previous_map={},
            revision_alpha=0.5,
            error_config=error_config,
        )
        second, _ = _forecast_contract(
            global_run=self.global_run,
            profile=profile,
            role="main",
            contract=contract,
            truth=truth_b,
            previous_map={},
            revision_alpha=0.5,
            error_config=error_config,
        )
        self.assertEqual(first["weekly"], second["weekly"])

    def test_generated_research_personnel_are_specialized_styled_and_deterministic(self) -> None:
        first = generate_institution_profile_for_score_range(
            seed=42,
            score_min=65,
            score_max=75,
        )
        second = generate_institution_profile_for_score_range(
            seed=42,
            score_min=65,
            score_max=75,
        )
        another = generate_institution_profile_for_score_range(
            seed=43,
            score_min=65,
            score_max=75,
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first["capability_radar"], another["capability_radar"])
        self.assertNotEqual(first["research_style"], another["research_style"])
        self.assertGreaterEqual(first["capability_total_score"], 65.0)
        self.assertLessEqual(first["capability_total_score"], 75.0)
        self.assertGreater(
            max(first["capability_radar"].values())
            - min(first["capability_radar"].values()),
            12.0,
        )
        self.assertEqual(
            set(FORECAST_RESEARCH_STYLE_DIMENSIONS),
            set(first["research_style"]),
        )
        self.assertTrue(
            all(0.0 <= value <= 100.0 for value in first["research_style"].values())
        )

    def test_generated_capabilities_have_structured_not_collinear_correlations(self) -> None:
        profiles = [
            generate_institution_profile_for_score_range(
                seed=seed,
                score_min=65,
                score_max=75,
            )
            for seed in range(300)
        ]
        radars = [item["capability_radar"] for item in profiles]
        direction_path = correlation(
            [item["direction"] for item in radars],
            [item["path"] for item in radars],
        )
        turning_revision = correlation(
            [item["turning_points"] for item in radars],
            [item["revision"] for item in radars],
        )
        direction_curve = correlation(
            [item["direction"] for item in radars],
            [item["term_structure"] for item in radars],
        )
        specialist_share = sum(
            (
                max(item.values()) - min(item.values())
            ) >= 20.0
            for item in radars
        ) / len(radars)

        self.assertGreater(direction_path, 0.05)
        self.assertGreater(turning_revision, 0.15)
        self.assertLess(abs(direction_curve), 0.35)
        self.assertGreater(specialist_share, 0.70)

    def test_neutral_style_preserves_baseline_and_extremes_change_behavior_not_skill(self) -> None:
        assets = load_registered_assets()
        error_config = assets["oil_short_term_forecast_config"]["error_model"]
        radar = {key: 70.0 for key in RADAR_DIMENSIONS}
        neutral_style = {
            key: 50.0 for key in FORECAST_RESEARCH_STYLE_DIMENSIONS
        }
        neutral = build_institution_profile(
            institution_id="neutral_style",
            capability_radar=radar,
            research_style=neutral_style,
        )
        behavior = research_behavior(neutral, error_config)
        self.assertEqual(
            error_config["baseline_behavior"],
            behavior,
        )

        trend = build_institution_profile(
            institution_id="trend_style",
            capability_radar=radar,
            research_style={
                **neutral_style,
                "trend_reversion_bias": 100.0,
                "fundamental_market_bias": 100.0,
            },
        )
        trend_behavior = research_behavior(trend, error_config)
        self.assertGreater(
            trend_behavior["trend_extrapolation"],
            behavior["trend_extrapolation"],
        )
        self.assertLess(
            trend_behavior["mean_reversion"],
            behavior["mean_reversion"],
        )

        fast_revision = build_institution_profile(
            institution_id="fast_revision",
            capability_radar=radar,
            research_style={**neutral_style, "revision_style": 100.0},
        )
        fast_behavior = research_behavior(fast_revision, error_config)
        self.assertGreater(
            fast_behavior["revision_speed"],
            behavior["revision_speed"],
        )
        self.assertLess(
            fast_behavior["thesis_persistence"],
            behavior["thesis_persistence"],
        )

        anticipatory = build_institution_profile(
            institution_id="anticipatory",
            capability_radar=radar,
            research_style={
                **neutral_style,
                "confirmation_lead_bias": 100.0,
                "confidence_style": 100.0,
            },
        )
        anticipatory_behavior = research_behavior(anticipatory, error_config)
        self.assertGreater(anticipatory_behavior["timing_lead_weeks"], 0.0)
        self.assertLess(anticipatory_behavior["confidence_bias_pct"], 0.0)
        self.assertEqual(
            neutral["capability_radar"],
            trend["capability_radar"],
        )

    def test_continuous_forecast_session_matches_manual_revision_chain(self) -> None:
        profile = generate_institution_profile_for_score_range(
            seed=42,
            score_min=65,
            score_max=75,
        )
        first = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
            institution_profile=profile,
        )
        second = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=2,
            institution_profile=profile,
            previous_vintage=first,
        )
        third = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=2,
            as_of_half=1,
            institution_profile=profile,
            previous_vintage=second,
        )

        session = OilShortTermForecastSession(self.global_run, profile)
        direct = session.payload(
            as_of_year=2030,
            as_of_month=2,
            as_of_half=1,
        )
        self.assertEqual(third, direct)

        server_direct = build_oil_short_term_forecast_payload(
            self.global_run,
            as_of_year=2030,
            as_of_month=2,
            as_of_half=1,
            institution_profile=profile,
        )
        self.assertEqual(
            "generated_research_42_650_750:2030-01-H2",
            server_direct["identity"]["previous_vintage_id"],
        )

    def test_next_main_inherits_by_contract_id_when_it_becomes_main(self) -> None:
        before_roll = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=4,
            as_of_half=2,
        )
        after_roll = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=5,
            as_of_half=1,
            previous_vintage=before_roll,
        )
        self.assertEqual(
            [("main", "OIL-3005"), ("next_main", "OIL-3009")],
            [
                (item["role"], item["contract_id"])
                for item in before_roll["forecasts"]
            ],
        )
        self.assertEqual(
            [("main", "OIL-3009"), ("next_main", "OIL-3101")],
            [
                (item["role"], item["contract_id"])
                for item in after_roll["forecasts"]
            ],
        )
        self.assertEqual(
            "main_role_transition",
            after_roll["revision"]["reason"],
        )
        self.assertGreater(after_roll["revision"]["revised_target_count"], 0)


if __name__ == "__main__":
    unittest.main()
