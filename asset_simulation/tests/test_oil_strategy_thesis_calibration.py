from __future__ import annotations

import unittest

from asset_simulation.model.oil_strategy_thesis import evaluate_oil_strategy_thesis_state
from asset_simulation.model.registry import load_registered_assets


class OilStrategyThesisCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_registered_assets()["oil_trading_strategy_config"][
            "thesis_invalidation"
        ]

    def _decision(self, forecast_close: float, uncertainty_log: float) -> dict:
        return {
            "thesisInvalidation": {"policy": self.policy, "stateBefore": {}},
            "targets": [
                {
                    "contract_id": "OIL-3005",
                    "role": "main",
                    "anchor_price_usd": 100.0,
                    "signal": 0.5,
                    "horizon_components": [
                        {
                            "selected_horizon_weeks": 2,
                            "target_week": "2030-01-W4",
                            "forecast_close_usd": forecast_close,
                            "confidence_low_usd": 95.0,
                            "confidence_high_usd": 105.0,
                            "uncertainty_log": uncertainty_log,
                        }
                    ],
                }
            ],
        }

    def _end_market(self, actual_price: float) -> dict:
        return {
            "curve": {
                "contracts": [
                    {"contract_id": "OIL-3005", "price_usd": actual_price}
                ]
            }
        }

    def test_low_conviction_opposite_move_is_not_a_direction_miss(self) -> None:
        outcome = evaluate_oil_strategy_thesis_state(
            self._decision(forecast_close=100.5, uncertainty_log=0.04),
            self._end_market(actual_price=99.4),
        )
        evaluation = outcome["evaluations"][0]
        self.assertGreater(evaluation["predicted_direction"], 0)
        self.assertLess(evaluation["realized_direction"], 0)
        self.assertLess(
            evaluation["forecast_direction_z"],
            self.policy["minimum_direction_forecast_z"],
        )
        self.assertFalse(evaluation["direction_miss_eligible"])
        self.assertFalse(evaluation["direction_miss"])
        self.assertEqual(
            "active", outcome["state"]["contracts"]["OIL-3005"]["status"]
        )

    def test_material_forecast_opposite_move_still_counts_direction_miss(self) -> None:
        outcome = evaluate_oil_strategy_thesis_state(
            self._decision(forecast_close=102.0, uncertainty_log=0.04),
            self._end_market(actual_price=99.0),
        )
        evaluation = outcome["evaluations"][0]
        self.assertGreaterEqual(
            evaluation["forecast_direction_z"],
            self.policy["minimum_direction_forecast_z"],
        )
        self.assertTrue(evaluation["direction_miss_eligible"])
        self.assertTrue(evaluation["direction_miss"])
        self.assertEqual(
            "watch", outcome["state"]["contracts"]["OIL-3005"]["status"]
        )


if __name__ == "__main__":
    unittest.main()
