from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.corporate_risk_control import (
    build_default_corporate_risk_profile,
    resolve_corporate_risk_profile,
)
from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_short_term_forecast import (
    generate_oil_short_term_forecast,
)
from asset_simulation.model.oil_strategy_research import (
    STRATEGY_STYLE_DIMENSIONS,
    generate_oil_strategy_research_roster,
    resolve_oil_strategy_research_profile,
)
from asset_simulation.model.oil_strategy_risk import (
    _apply_position_gap_completion,
    _position_gap_completion,
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

    @staticmethod
    def _deployment_profile(score: float) -> dict:
        radar = {key: 50.0 for key in STRATEGY_STYLE_DIMENSIONS}
        radar["capital_deployment"] = float(score)
        return resolve_oil_strategy_research_profile(
            {
                "appointment": {
                    "personnel_id": f"deployment_{score:g}",
                    "display_name": f"Deployment {score:g}",
                    "source": "test_controlled_profile",
                },
                "style_radar": radar,
            }
        )

    def test_registered_mandate_maps_distinct_strategy_structures_differently(self) -> None:
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
            self.assertEqual(
                "oil_directional_strategy_design",
                review["governance"]["proposal_owner"],
            )
            self.assertEqual(
                "investment_decision_committee",
                review["governance"]["approval_owner"],
            )
            self.assertFalse(review["governance"]["proposal_is_capital_allocation"])
            self.assertFalse(
                review["governance"]["risk_personnel_institution_enabled"]
            )
            self.assertIsNone(review["riskMandate"]["personnel_id"])

    def test_position_curve_is_strategy_owned_and_independent_of_cro_preferences(self) -> None:
        permissive = deepcopy(self.cro)
        permissive.pop("profile_hash")
        permissive["risk_appetite_radar"] = {
            key: 100.0 for key in permissive["risk_appetite_radar"]
        }
        permissive = resolve_corporate_risk_profile(permissive)
        baseline = build_oil_strategy_risk_review(self.strategies[0], self.cro)
        altered = build_oil_strategy_risk_review(
            self.strategies[0], permissive
        )
        self.assertEqual(
            baseline["proposedPolicy"]["positionUtilization"],
            altered["proposedPolicy"]["positionUtilization"],
        )
        self.assertEqual(
            baseline["proposedPolicy"]["volatility"],
            altered["proposedPolicy"]["volatility"],
        )
        self.assertEqual(
            "oil_directional_strategy_design",
            baseline["governance"]["position_risk_mandate_owner"],
        )
        self.assertFalse(baseline["governance"]["risk_personnel_input_used"])
        self.assertEqual(baseline, altered)

    def test_position_curve_progressively_slows_only_risk_increases(self) -> None:
        policy = build_oil_strategy_risk_review(
            self.strategies[0], self.cro
        )["proposedPolicy"]["positionUtilization"]
        light = _position_gap_completion(
            current_utilization=0.30,
            proposed_utilization=0.80,
            policy=policy,
        )
        moderate = _position_gap_completion(
            current_utilization=0.55,
            proposed_utilization=0.80,
            policy=policy,
        )
        heavy = _position_gap_completion(
            current_utilization=0.80,
            proposed_utilization=0.95,
            policy=policy,
        )
        danger = _position_gap_completion(
            current_utilization=0.95,
            proposed_utilization=1.0,
            policy=policy,
        )
        self.assertEqual(1.0, light)
        self.assertGreater(light, moderate)
        self.assertGreater(moderate, heavy)
        self.assertGreater(heavy, danger)
        self.assertEqual(
            1.0,
            _position_gap_completion(
                current_utilization=0.80,
                proposed_utilization=0.40,
                policy=policy,
            ),
        )
        self.assertEqual(
            0.0,
            _position_gap_completion(
                current_utilization=0.99,
                proposed_utilization=1.0,
                policy=policy,
            ),
        )
        self.assertEqual(
            20,
            _apply_position_gap_completion(
                current_position=100,
                desired_target=20,
                completion=0.0,
            ),
        )
        self.assertEqual(
            60,
            _apply_position_gap_completion(
                current_position=20,
                desired_target=100,
                completion=0.5,
            ),
        )
        self.assertEqual(
            0,
            _apply_position_gap_completion(
                current_position=100,
                desired_target=-100,
                completion=0.0,
            ),
        )

    def test_directional_decision_publishes_position_dependent_risk_state(self) -> None:
        light_decision = build_oil_strategy_decision(self.market, self.forecast)
        light_risk = light_decision["strategyRisk"]["positionRisk"]
        self.assertEqual("light", light_risk["effective_tier"])
        self.assertEqual(1.0, light_risk["risk_increasing_gap_completion"])
        self.assertEqual(
            light_risk["proposed"]["maximum_utilization"],
            light_risk["approved"]["maximum_utilization"],
        )

        decision = build_oil_strategy_decision(
            self.market,
            self.forecast,
            positions={"OIL-3005": 120},
        )
        position_risk = decision["strategyRisk"]["positionRisk"]
        self.assertEqual(
            "oil_directional_strategy_design", position_risk["owner"]
        )
        self.assertEqual(
            "investment_decision_committee",
            position_risk["approval_owner"],
        )
        self.assertGreater(position_risk["current"]["maximum_utilization"], 0.40)
        self.assertGreater(
            position_risk["proposed"]["maximum_utilization"],
            position_risk["approved"]["maximum_utilization"],
        )
        self.assertEqual(0.95, position_risk["risk_increasing_gap_completion"])
        self.assertEqual("moderate", position_risk["effective_tier"])
        for target in decision["targets"]:
            self.assertEqual(
                position_risk["effective_tier"],
                target["strategy_position_risk_tier"],
            )
            self.assertEqual(
                position_risk["risk_increasing_gap_completion"],
                target["strategy_position_risk_gap_completion"],
            )

        reduce_only = build_oil_strategy_decision(
            self.market,
            self.forecast,
            positions={"OIL-3005": 300},
        )
        reduce_only_state = reduce_only["strategyRisk"]["positionRisk"]
        self.assertTrue(reduce_only_state["current_reduce_only"])
        self.assertEqual(0.0, reduce_only_state["risk_increasing_gap_completion"])
        reduce_only_targets = {
            item["contract_id"]: item for item in reduce_only["targets"]
        }
        self.assertLessEqual(
            abs(
                reduce_only_targets["OIL-3005"][
                    "strategy_risk_approved_target_position_lots"
                ]
            ),
            300,
        )
        self.assertEqual(
            0,
            reduce_only_targets["OIL-3009"][
                "strategy_risk_approved_target_position_lots"
            ],
        )

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

    def test_pm_deployment_changes_intent_not_risk_policy_or_authorization(self) -> None:
        low_profile = self._deployment_profile(0.0)
        high_profile = self._deployment_profile(100.0)
        low_review = build_oil_strategy_risk_review(low_profile, self.cro)
        high_review = build_oil_strategy_risk_review(high_profile, self.cro)

        self.assertEqual(low_review["strategyRiskPressures"], high_review["strategyRiskPressures"])
        self.assertEqual(low_review["reviewAllowanceScores"], high_review["reviewAllowanceScores"])
        self.assertEqual(low_review["proposedPolicy"], high_review["proposedPolicy"])
        self.assertFalse(
            low_review["governance"]["pm_capital_deployment_is_review_policy_input"]
        )

        low = build_oil_strategy_decision(
            self.market,
            self.forecast,
            strategy_research_profile=low_profile,
            capital_authorization_pct_of_company_equity=60.0,
        )
        high = build_oil_strategy_decision(
            self.market,
            self.forecast,
            strategy_research_profile=high_profile,
            capital_authorization_pct_of_company_equity=60.0,
        )
        self.assertEqual(
            low["investmentDecision"]["capitalAuthorization"],
            high["investmentDecision"]["capitalAuthorization"],
        )
        self.assertEqual(
            low["strategyRisk"]["approvedPolicy"],
            high["strategyRisk"]["approvedPolicy"],
        )
        self.assertEqual(
            low["strategyRisk"]["strategyLimits"],
            high["strategyRisk"]["strategyLimits"],
        )
        self.assertLess(
            low["riskBudget"]["strategy_intent_gross_lots"],
            high["riskBudget"]["strategy_intent_gross_lots"],
        )
        self.assertEqual(
            "parallel_strategy_intent_and_independent_risk_limits",
            low["riskBudget"]["sizing_architecture"]["method"],
        )
        self.assertFalse(
            low["riskBudget"]["sizing_architecture"][
                "pm_deployment_multiplied_by_risk_allowance"
            ]
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

    def test_strategy_drawdown_can_reduce_risk_while_portfolio_layer_is_dormant(self) -> None:
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
        self.assertEqual(
            "dormant_single_strategy", decision["portfolioRisk"]["status"]
        )
        self.assertFalse(
            decision["portfolioRisk"]["governance"]["second_clipping_enabled"]
        )
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
