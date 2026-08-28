from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.corporate_risk_control import (
    build_default_corporate_risk_profile,
)
from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_short_term_forecast import (
    generate_oil_short_term_forecast,
)
from asset_simulation.model.oil_strategy_research import (
    generate_oil_strategy_research_roster,
)
from asset_simulation.model.oil_strategy_risk import (
    build_investment_committee_strategy_approval,
    build_oil_strategy_risk_review,
)
from asset_simulation.model.oil_trading_strategy import (
    build_oil_strategy_decision,
)
from asset_simulation.model.institution_organization import (
    initial_proprietary_capital_usd,
)


class OilStrategyRiskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.global_run = run_global_macro(42, 7)
        cls.market = oil_futures_payload(
            cls.global_run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        cls.forecast = generate_oil_short_term_forecast(
            cls.global_run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        cls.strategies = generate_oil_strategy_research_roster(
            seed=42, candidate_count=5
        )["candidates"]
        cls.cro = build_default_corporate_risk_profile()

    def test_same_risk_department_reviews_distinct_strategies_differently(self) -> None:
        reviews = [
            build_oil_strategy_risk_review(profile, self.cro)
            for profile in self.strategies
        ]
        self.assertEqual(
            len(reviews), len({item["identity"]["result_hash"] for item in reviews})
        )
        recommendations = {
            round(
                item["proposedPolicy"]["capacity"][
                    "recommended_capital_authorization_pct_of_company_equity"
                ],
                4,
            )
            for item in reviews
        }
        self.assertGreaterEqual(len(recommendations), 4)
        for review in reviews:
            self.assertEqual("risk_department", review["governance"]["proposal_owner"])
            self.assertEqual(
                "investment_decision_committee",
                review["governance"]["approval_owner"],
            )
            self.assertFalse(review["governance"]["proposal_is_capital_allocation"])

    def test_committee_capital_authorization_is_independent_and_tamper_evident(self) -> None:
        review = build_oil_strategy_risk_review(self.strategies[0], self.cro)
        recommendation = review["proposedPolicy"]["capacity"][
            "recommended_capital_authorization_pct_of_company_equity"
        ]
        approval = build_investment_committee_strategy_approval(
            review,
            company_equity_usd=3_000_000_000.0,
            capital_authorization_pct_of_company_equity=35.0,
        )
        self.assertEqual(35.0, approval["capitalAuthorization"]["authorized_pct_of_company_equity"])
        self.assertEqual(1_050_000_000.0, approval["capitalAuthorization"]["authorized_capital_usd"])
        self.assertAlmostEqual(
            35.0 - recommendation,
            approval["capitalAuthorization"]["deviation_from_risk_recommendation_pct"],
        )
        self.assertEqual(
            review["proposedPolicy"],
            approval["riskPolicyDecision"]["approvedPolicy"],
        )
        modified = deepcopy(review)
        modified["proposedPolicy"]["drawdown"]["reduce_only_pct"] += 0.01
        with self.assertRaises(ValueError):
            build_investment_committee_strategy_approval(
                modified, company_equity_usd=3_000_000_000.0
            )

    def test_capital_authorization_scales_intent_without_changing_market_capacity(self) -> None:
        full = build_oil_strategy_decision(
            self.market,
            self.forecast,
            capital_authorization_pct_of_company_equity=100.0,
        )
        partial = build_oil_strategy_decision(
            self.market,
            self.forecast,
            capital_authorization_pct_of_company_equity=30.0,
        )
        self.assertEqual(
            full["riskBudget"]["gross_market_cap_lots"],
            partial["riskBudget"]["gross_market_cap_lots"],
        )
        self.assertEqual(
            0.30 * initial_proprietary_capital_usd(),
            partial["riskBudget"]["allocated_strategy_capital_usd"],
        )
        self.assertLess(
            partial["riskBudget"]["strategy_intent_gross_lots"],
            full["riskBudget"]["strategy_intent_gross_lots"],
        )
        self.assertTrue(
            partial["investmentDecision"]["governance"][
                "capital_authorization_is_committee_discretion"
            ]
        )

    def test_strategy_drawdown_can_reduce_risk_while_company_layer_is_normal(self) -> None:
        decision = build_oil_strategy_decision(
            self.market,
            self.forecast,
            strategy_risk_state={
                "peak_strategy_equity_usd": 5_000_000_000.0,
                "strategy_drawdown_scale": 1.0,
            },
            risk_state=None,
        )
        self.assertEqual("reduce_only", decision["strategyRisk"]["state"]["risk_status"])
        self.assertEqual("normal", decision["corporateRisk"]["state"]["risk_status"])
        self.assertGreater(
            decision["strategyRisk"]["approvalSummary"]["clipped_gross_lots"],
            0,
        )
        self.assertTrue(
            all(
                item["strategy_risk_approved_target_position_lots"] == 0
                for item in decision["targets"]
            )
        )


if __name__ == "__main__":
    unittest.main()
