from __future__ import annotations

import unittest

from asset_simulation.model.investment_decision import (
    build_company_risk_appetite,
    build_strategy_capital_mandate,
    build_strategy_charter,
    build_strategy_position_mandate,
)
from asset_simulation.model.oil_short_horizon_risk_v2 import (
    OIL_SHORT_HORIZON_RISK_MODEL_VERSION,
    build_oil_short_horizon_risk_review,
)
from asset_simulation.tests.test_oil_short_horizon_risk import _market


class OilShortHorizonRiskV2Tests(unittest.TestCase):
    def _review(self, allocation_pct: float, target_lots: int) -> dict:
        charter = build_strategy_charter(
            asset="oil",
            horizon="short_horizon",
            strategy_type="directional",
            strategy_id=f"v2-boundary-{allocation_pct:g}-{target_lots}",
        )
        capital = build_strategy_capital_mandate(
            charter,
            company_equity_usd=100_000_000.0,
            authorized_pct_of_company_equity=allocation_pct,
        )
        mandate = build_strategy_position_mandate(
            charter,
            capital,
            {"OIL-3005": target_lots},
        )
        return build_oil_short_horizon_risk_review(
            _market(),
            mandate,
            company_equity_usd=100_000_000.0,
            allocated_strategy_capital_usd=float(capital["authorized_capital_usd"]),
            company_risk_appetite=build_company_risk_appetite(),
        )

    def test_v2_company_materiality_is_diagnostic_not_a_strategy_clip(self) -> None:
        small = self._review(10.0, 400)
        large = self._review(50.0, 2000)
        self.assertEqual(
            OIL_SHORT_HORIZON_RISK_MODEL_VERSION,
            "asset-simulation-oil-short-horizon-risk-v0.2.0",
        )
        self.assertFalse(
            small["companyMaterialityDiagnostic"]["binding_in_strategy_review"]
        )
        self.assertEqual(
            "corporate_aggregate_risk",
            small["companyMaterialityDiagnostic"]["future_binding_owner"],
        )
        self.assertNotIn("company_materiality", small["portfolioBindingRules"])
        self.assertNotIn("company_margin_materiality", small["portfolioBindingRules"])
        self.assertNotIn("company_materiality", large["portfolioBindingRules"])
        self.assertNotIn("company_margin_materiality", large["portfolioBindingRules"])
        self.assertGreater(
            float(
                large["materialityBeforePortfolioScale"][
                    "stress_loss_pct_of_company_equity"
                ]
            ),
            float(
                small["materialityBeforePortfolioScale"][
                    "stress_loss_pct_of_company_equity"
                ]
            ),
        )
        self.assertGreater(
            float(
                large["materialityBeforePortfolioScale"][
                    "margin_pct_of_company_equity"
                ]
            ),
            float(
                small["materialityBeforePortfolioScale"][
                    "margin_pct_of_company_equity"
                ]
            ),
        )

    def test_neutral_strategy_stress_mapping_is_recalibrated_not_relabelled(self) -> None:
        review = self._review(20.0, 1000)
        self.assertEqual(
            50.0,
            float(
                review["companyRiskAppetite"]["risk_appetite_radar"][
                    "strategy_stress_loss_tolerance"
                ]
            ),
        )
        resolved = float(
            review["companyRiskAppetite"]["resolved_binding_strategy_limits"][
                "max_strategy_stress_loss_pct_of_allocated_capital"
            ]
        )
        # The appointed officer may operate conservatively inside the committee
        # policy, so the resolved value can be below the raw 18% neutral anchor.
        self.assertGreater(resolved, 12.0)
        self.assertLessEqual(resolved, 18.0)
        self.assertTrue(review["governance"]["strategy_relative_limits_binding"])
        self.assertFalse(
            review["governance"]["company_materiality_binding_in_strategy_review"]
        )


if __name__ == "__main__":
    unittest.main()
