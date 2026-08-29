from __future__ import annotations

import unittest

from asset_simulation.audit_oil_directional_economic_calibration import (
    _controlled_profile,
    build_oil_directional_economic_calibration_audit,
)


class OilDirectionalEconomicCalibrationTests(unittest.TestCase):
    def test_controlled_profile_changes_only_the_requested_axis(self) -> None:
        profile = _controlled_profile("turnover_activity", 90.0)
        self.assertEqual(90.0, profile["style_radar"]["turnover_activity"])
        for name, value in profile["style_radar"].items():
            if name != "turnover_activity":
                self.assertEqual(50.0, value)
        self.assertIsNone(profile["preference_total_score"])

    def test_small_exact_replay_is_deterministic(self) -> None:
        arguments = {
            "seeds": (0,),
            "horizon_years": 1,
            "forecast_bands": {"medium": (45.0, 55.0)},
            "axes": ("turnover_activity",),
            "axis_scores": (10.0, 90.0),
            "include_rows": False,
        }
        first = build_oil_directional_economic_calibration_audit(**arguments)
        second = build_oil_directional_economic_calibration_audit(**arguments)
        self.assertEqual(first["identity"]["result_hash"], second["identity"]["result_hash"])
        self.assertEqual(2, first["scope"]["scenario_count"])
        self.assertGreater(
            first["turnover90To10ActualLotsRatio"]["median"], 1.0
        )


if __name__ == "__main__":
    unittest.main()
