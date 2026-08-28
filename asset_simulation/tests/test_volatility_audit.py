from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from asset_simulation.audit_volatility import (
    GOAL_C_SEGMENTS,
    build_audit,
    build_goal_c_audit,
    evaluate_segment_gates,
    main,
    summarize_goal_c_drift,
)


def _summary(mean: float, std: float, p05: float | None = None, p95: float | None = None) -> dict[str, float | int]:
    low = mean - 2 * std if p05 is None else p05
    high = mean + 2 * std if p95 is None else p95
    return {"count": 100, "mean": mean, "std": std, "p05": low, "p50": mean, "p95": high, "min": low, "max": high}


def _passing_segment_report() -> dict:
    return {
        "schema_version": "asset-simulation-volatility-audit-v1",
        "seed_range": [0, 1],
        "years": 5,
        "world_count": 2,
        "observation_years": 10,
        "levels": {
            "global": {
                "growth": _summary(2.35, 0.9),
                "inflation": _summary(2.20, 0.4),
                "policy": _summary(3.1, 0.8),
                "yield_10y": _summary(3.4, 0.5),
            }
        },
        "annual_changes": {
            "global": {
                "inflation": _summary(0.0, 0.36),
                "policy": _summary(0.0, 0.3),
                "yield_2y": _summary(0.0, 0.3),
                "yield_10y": _summary(0.0, 0.34),
            }
        },
        "event_frequency_pct": {
            "global": {
                "inflation_above_4": 0.74,
                "inflation_above_8": 0.0,
                "deflation": 0.13,
                "inversion": 22.9,
                "negative_growth": 0.4,
            }
        },
        "event_counts": {
            "global": {"years": 10, "inflation_above_4": 2, "deflation": 1, "inflation_above_8": 0},
        },
        "bounds_hits": {
            "global": {"headline_inflation_pct": 0, "output_gap_pct": 0, "hy_spread_bps": 0},
        },
        "bounds_hit_rates_pct": {
            "global": {"headline_inflation_pct": 0.0, "output_gap_pct": 0.0, "hy_spread_bps": 0.0},
        },
        "nonfinite_counts": {"global": 0},
    }


class VolatilityAuditTests(unittest.TestCase):
    def test_standalone_report_covers_levels_changes_events_and_bounds(self) -> None:
        report = build_audit(seed_start=0, seed_end=1, years=5)
        self.assertEqual("asset-simulation-volatility-audit-v1", report["schema_version"])
        self.assertEqual(2, report["world_count"])
        self.assertEqual(10, report["observation_years"])
        self.assertEqual(10, report["levels"]["global"]["growth"]["count"])
        self.assertEqual(10, report["annual_changes"]["global"]["yield_10y"]["count"])
        self.assertIn("inflation_above_8", report["event_frequency_pct"]["global"])
        self.assertIn("output_gap_pct", report["bounds_hits"]["global"])
        self.assertIn("hy_spread_bps", report["bounds_hits"]["global"])
        self.assertIn("global", report["model_versions"])
        self.assertEqual(0, report["nonfinite_counts"]["global"])
        self.assertEqual(0, report["event_counts"]["global"]["inflation_above_8"])
        self.assertNotIn("g17_accounts", report)
        self.assertNotIn("north_america", report["levels"])

    def test_goal_c_report_structure_uses_tiny_segments_not_seven_hundred_seeds(self) -> None:
        tiny = (("calibration", 0, 0), ("validation", 1, 1), ("holdout", 2, 2))
        report = build_goal_c_audit(years=5, segments=tiny)
        self.assertEqual("asset-simulation-volatility-audit-goal-c-v1", report["schema_version"])
        self.assertEqual("goal-c", report["profile"])
        self.assertFalse(report["holdout_used_for_parameter_selection"])
        self.assertEqual(3, report["summary"]["world_count_total"])
        self.assertEqual(("calibration", "validation", "holdout"), tuple(report["segments"]))
        for name, start, end in tiny:
            segment = report["segments"][name]
            self.assertEqual([start, end], segment["seed_range"])
            self.assertEqual(1, segment["world_count"])
            self.assertIsNotNone(segment["audit"])
            self.assertEqual("asset-simulation-volatility-audit-v1", segment["audit"]["schema_version"])
            self.assertIn("status", segment["gates"])
            self.assertIn("failures", segment["gates"])
            self.assertIn("warnings", segment["gates"])
            self.assertIn("nonfinite_counts", segment["audit"])
            self.assertIn("bounds_hit_rates_pct", segment["audit"])
            self.assertIn("infos", segment["gates"])
        self.assertIn("threshold_notes", report)
        self.assertEqual(0.5, report["threshold_notes"]["process_bound_fail_rate_pct"])
        self.assertEqual(10.0, report["threshold_notes"]["saturation_fail_rate_pct"])
        self.assertIn("mean_max_abs_diff_pp", report["drift"])
        self.assertIn("std_relative_gaps", report["drift"])
        self.assertIn(report["summary"]["status"], {"pass", "warning", "fail"})
        self.assertEqual((("calibration", 0, 399), ("validation", 400, 499), ("holdout", 500, 699)), GOAL_C_SEGMENTS)

    def test_segment_gates_and_drift_use_warning_fail_split(self) -> None:
        passing = _passing_segment_report()
        self.assertEqual("pass", evaluate_segment_gates(passing)["status"])

        broken = copy.deepcopy(passing)
        broken["nonfinite_counts"]["global"] = 2
        broken_gates = evaluate_segment_gates(broken)
        self.assertEqual("fail", broken_gates["status"])
        self.assertTrue(any(item["gate"] == "runability" for item in broken_gates["failures"]))

        bound_warn = copy.deepcopy(passing)
        bound_warn["bounds_hit_rates_pct"]["global"]["headline_inflation_pct"] = 0.2
        bound_warn["bounds_hits"]["global"]["headline_inflation_pct"] = 20
        bound_result = evaluate_segment_gates(bound_warn)
        self.assertEqual("warning", bound_result["status"])
        self.assertTrue(bound_result["explanations"])

        exact_half = copy.deepcopy(passing)
        exact_half["bounds_hit_rates_pct"]["global"]["headline_inflation_pct"] = 0.5
        exact_half["bounds_hits"]["global"]["headline_inflation_pct"] = 30
        exact_half_result = evaluate_segment_gates(exact_half)
        self.assertEqual("warning", exact_half_result["status"])
        self.assertFalse(exact_half_result["failures"])

        over_half = copy.deepcopy(passing)
        over_half["bounds_hit_rates_pct"]["global"]["headline_inflation_pct"] = 0.51
        over_half["bounds_hits"]["global"]["headline_inflation_pct"] = 31
        self.assertEqual("fail", evaluate_segment_gates(over_half)["status"])

        calibration = _passing_segment_report()
        holdout = copy.deepcopy(calibration)
        holdout["levels"]["global"]["inflation"]["mean"] = 2.60
        drifted = summarize_goal_c_drift({"calibration": calibration, "validation": calibration, "holdout": holdout})
        self.assertEqual("fail", drifted["status"])
        self.assertTrue(any(item["gate"] == "drift_mean" for item in drifted["failures"]))

        vanished = copy.deepcopy(calibration)
        vanished["event_frequency_pct"]["global"]["inflation_above_4"] = 0.0
        tail_drift = summarize_goal_c_drift(
            {"calibration": calibration, "validation": vanished, "holdout": vanished}
        )
        self.assertEqual("fail", tail_drift["status"])
        self.assertTrue(any(item["gate"] == "drift_tail" for item in tail_drift["failures"]))

        gap_warn = copy.deepcopy(passing)
        gap_warn["bounds_hit_rates_pct"]["global"]["output_gap_pct"] = 0.2
        gap_warn["bounds_hits"]["global"]["output_gap_pct"] = 20
        gap_warn["bounds_edge_hits"] = {"global": {"output_gap_pct": {"low": 0, "high": 20}}}
        gap_result = evaluate_segment_gates(gap_warn)
        self.assertEqual("warning", gap_result["status"])
        self.assertTrue(any("output_gap_pct" in item["detail"] for item in gap_result["warnings"]))
        self.assertTrue(any(item["gate"] == "bounds_saturation" for item in gap_result["warnings"]))
        self.assertTrue(any("high-edge 20" in text for text in gap_result["explanations"]))

        hy_warn = copy.deepcopy(passing)
        hy_warn["bounds_hit_rates_pct"]["global"]["hy_spread_bps"] = 0.51
        hy_warn["bounds_hits"]["global"]["hy_spread_bps"] = 51
        hy_warn_result = evaluate_segment_gates(hy_warn)
        self.assertEqual("warning", hy_warn_result["status"])
        self.assertTrue(any(item["gate"] == "bounds_saturation" for item in hy_warn_result["warnings"]))

        hy_fail = copy.deepcopy(passing)
        hy_fail["bounds_hit_rates_pct"]["global"]["hy_spread_bps"] = 10.1
        hy_fail["bounds_hits"]["global"]["hy_spread_bps"] = 1010
        self.assertEqual("fail", evaluate_segment_gates(hy_fail)["status"])

    def test_goal_c_cli_profile_writes_injected_segment_report(self) -> None:
        tiny = build_goal_c_audit(
            years=5,
            segments=(("calibration", 0, 0), ("validation", 1, 1), ("holdout", 2, 2)),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "goal_c.json"
            with patch(
                "sys.argv",
                ["audit_volatility", "--profile", "goal-c", "--years", "5", "--output", str(output)],
            ), patch(
                "asset_simulation.audit_volatility.build_goal_c_audit",
                return_value=tiny,
            ) as mocked:
                main()
            mocked.assert_called_once_with(years=5)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("goal-c", payload["profile"])
            self.assertEqual(3, payload["summary"]["world_count_total"])


if __name__ == "__main__":
    unittest.main()
