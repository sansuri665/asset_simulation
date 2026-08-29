from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.oil_strategy_thesis import evaluate_oil_strategy_thesis_state
from asset_simulation.model.registry import load_registered_assets


class OilStrategyThesisCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = deepcopy(
            load_registered_assets()["oil_trading_strategy_config"]["thesis_invalidation"]
        )

    def _decision(self, *, center: float, uncertainty: float) -> dict[str, object]:
        anchor = 100.0
        return {
            "thesisInvalidation": {"policy": self.policy, "stateBefore": {}},
            "targets": [
                {
                    "contract_id": "OIL-3005",
                    "role": "main",
                    "anchor_price_usd": anchor,
                    "signal": 0.5,
                    "horizon_components": [
                        {
                            "selected_horizon_weeks": 2,
                            "target_week": "2030-01-W4",
                            "forecast_close_usd": center,
                            "confidence_low_usd": center * 0.98,
                            "confidence_high_usd": center * 1.02,
                            "uncertainty_log": uncertainty,
                        }
                    ],
                }
            ],
        }

    @staticmethod
    def _end(price: float) -> dict[str, object]:
        return {"curve": {"contracts": [{"contract_id": "OIL-3005", "price_usd": price}]}}

    def test_low_conviction_direction_disagreement_does_not_count_as_miss(self) -> None:
        outcome = evaluate_oil_strategy_thesis_state(
            self._decision(center=100.5, uncertainty=0.02),
            self._end(99.0),
        )
        evaluation = outcome["evaluations"][0]
        threshold = float(self.policy["minimum_direction_forecast_z"])
        self.assertLess(evaluation["forecast_direction_z"], threshold)
        self.assertFalse(evaluation["direction_miss_eligible"])
        self.assertFalse(evaluation["direction_miss"])

    def test_material_direction_disagreement_still_counts_as_miss(self) -> None:
        outcome = evaluate_oil_strategy_thesis_state(
            self._decision(center=102.0, uncertainty=0.02),
            self._end(99.0),
        )
        evaluation = outcome["evaluations"][0]
        threshold = float(self.policy["minimum_direction_forecast_z"])
        self.assertGreaterEqual(evaluation["forecast_direction_z"], threshold)
        self.assertTrue(evaluation["direction_miss_eligible"])
        self.assertTrue(evaluation["direction_miss"])


if __name__ == "__main__":
    unittest.main()
