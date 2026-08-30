from __future__ import annotations

import math
import unittest

from asset_simulation.model.investment_decision import (
    build_company_risk_appetite,
    build_strategy_capital_mandate,
    build_strategy_charter,
    build_strategy_position_mandate,
)
from asset_simulation.model.oil_short_horizon_risk import (
    build_oil_short_horizon_risk_review,
)
from asset_simulation.tests.test_oil_short_horizon_risk import _market


class OilShortHorizonRiskCalendarHorizonTests(unittest.TestCase):
    def test_calendar_spread_uses_two_week_sigma_and_one_tail_model_multiplier(self) -> None:
        market = _market()
        charter = build_strategy_charter(
            asset="oil",
            horizon="short_horizon",
            strategy_type="calendar_spread",
            strategy_id="calendar-risk-horizon-test",
        )
        capital = build_strategy_capital_mandate(
            charter,
            company_equity_usd=100_000_000.0,
            authorized_pct_of_company_equity=10.0,
        )
        units = 100
        mandate = build_strategy_position_mandate(
            charter,
            capital,
            {"OIL-3005": units, "OIL-3009": -units},
        )
        review = build_oil_short_horizon_risk_review(
            market,
            mandate,
            company_equity_usd=100_000_000.0,
            allocated_strategy_capital_usd=float(capital["authorized_capital_usd"]),
            company_risk_appetite=build_company_risk_appetite(),
        )

        estimates = review["softRiskEstimatesBeforePortfolioScale"]
        spread = estimates["calendar_spread"]
        self.assertIsNotNone(spread)
        self.assertEqual(2.0, float(review["riskHorizon"]["review_horizon_weeks"]))
        self.assertEqual(2.0, float(estimates["risk_horizon_weeks"]))
        self.assertEqual(1, int(estimates["tail_model_multiplier_application_count"]))
        self.assertTrue(spread["tail_model_multiplier_applied_once"])

        weekly_sigma = float(
            spread["visible_weekly_spread_change_volatility_usd_per_bbl"]
        )
        horizon_sigma = float(spread["review_horizon_spread_sigma_usd_per_bbl"])
        self.assertAlmostEqual(weekly_sigma * math.sqrt(2.0), horizon_sigma)

        tail = float(estimates["tail_stress_multiplier"])
        model = float(estimates["model_uncertainty_multiplier"])
        expected_move = max(0.5, horizon_sigma * tail * model)
        self.assertAlmostEqual(
            expected_move,
            float(spread["stressed_spread_move_usd_per_bbl"]),
        )

        stress_error = float(estimates["stress_analysis_error_fraction"])
        expected_stress = units * 1000.0 * expected_move * (1.0 + stress_error)
        self.assertAlmostEqual(
            expected_stress,
            float(estimates["estimated_stress_loss_usd"]),
            places=5,
        )


if __name__ == "__main__":
    unittest.main()
