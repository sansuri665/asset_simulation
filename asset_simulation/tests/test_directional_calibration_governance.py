from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

from asset_simulation.model import oil_short_term_forecast, oil_trading_strategy
from asset_simulation.model.registry import load_registered_assets


class DirectionalCalibrationGovernanceTests(unittest.TestCase):
    def test_minimum_direction_forecast_z_is_required_fail_closed(self) -> None:
        assets = load_registered_assets()
        broken = deepcopy(assets)
        broken["oil_trading_strategy_config"] = deepcopy(
            assets["oil_trading_strategy_config"]
        )
        broken["oil_trading_strategy_config"]["thesis_invalidation"].pop(
            "minimum_direction_forecast_z"
        )
        with patch.object(oil_trading_strategy, "load_registered_assets", return_value=broken):
            with self.assertRaisesRegex(ValueError, "minimum_direction_forecast_z is required"):
                oil_trading_strategy._validate_registered_assets()

    def test_truth_mix_is_quadratic_and_bounded(self) -> None:
        expected = {
            0.0: 0.0,
            15.0: 0.0225,
            50.0: 0.25,
            70.0: 0.49,
            100.0: 1.0,
        }
        for score, value in expected.items():
            self.assertAlmostEqual(value, oil_short_term_forecast._skill_truth_mix(score))
        self.assertEqual(0.0, oil_short_term_forecast._skill_truth_mix(-10.0))
        self.assertEqual(1.0, oil_short_term_forecast._skill_truth_mix(110.0))


if __name__ == "__main__":
    unittest.main()
