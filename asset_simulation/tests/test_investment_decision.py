from __future__ import annotations

import inspect
import unittest

from asset_simulation.model.investment_decision import (
    build_company_risk_appetite,
    build_strategy_capital_mandate,
    build_strategy_charter,
    build_strategy_position_mandate,
)


class InvestmentDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.charter = build_strategy_charter(
            asset="oil",
            horizon="short_horizon",
            strategy_type="directional",
            strategy_id="directional-oil",
        )
        self.capital = build_strategy_capital_mandate(
            self.charter,
            company_equity_usd=100_000_000.0,
            authorized_pct_of_company_equity=35.0,
        )

    def test_capital_and_company_risk_appetite_are_committee_owned(self) -> None:
        self.assertEqual(35_000_000.0, self.capital["authorized_capital_usd"])
        self.assertFalse(self.capital["governance"]["risk_department_recommendation_used"])
        appetite = build_company_risk_appetite()
        self.assertEqual("investment_decision_committee", appetite["governance"]["owner"])
        self.assertFalse(appetite["governance"]["cro_identity_is_policy_input"])

    def test_position_mandate_can_preserve_or_reduce_pm_intent(self) -> None:
        mandate = build_strategy_position_mandate(
            self.charter,
            self.capital,
            {"OIL-3005": 4_000, "OIL-3009": -2_000},
            expected_targets={"OIL-3005": 2_500, "OIL-3009": -1_000},
        )
        self.assertEqual(4_000, mandate["pm_proposed_targets"]["OIL-3005"])
        self.assertEqual(2_500, mandate["committee_expected_targets"]["OIL-3005"])
        self.assertTrue(mandate["governance"]["preserve_or_reduce_only"])

    def test_committee_cannot_create_reverse_or_expand_alpha(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot create position"):
            build_strategy_position_mandate(
                self.charter,
                self.capital,
                {"OIL-3005": 0},
                expected_targets={"OIL-3005": 1},
            )
        with self.assertRaisesRegex(ValueError, "cannot reverse"):
            build_strategy_position_mandate(
                self.charter,
                self.capital,
                {"OIL-3005": 100},
                expected_targets={"OIL-3005": -50},
            )
        with self.assertRaisesRegex(ValueError, "cannot expand"):
            build_strategy_position_mandate(
                self.charter,
                self.capital,
                {"OIL-3005": 100},
                expected_targets={"OIL-3005": 101},
            )

    def test_rejected_strategy_cannot_receive_effective_capital_or_position(self) -> None:
        rejected = build_strategy_charter(
            asset="oil",
            horizon="short_horizon",
            strategy_type="calendar_spread",
            strategy_id="spread-oil",
            approved=False,
        )
        capital = build_strategy_capital_mandate(
            rejected,
            company_equity_usd=100_000_000.0,
            authorized_pct_of_company_equity=50.0,
        )
        self.assertEqual(0.0, capital["authorized_capital_usd"])
        mandate = build_strategy_position_mandate(
            rejected,
            capital,
            {"OIL-3005": 100, "OIL-3009": -100},
        )
        self.assertEqual({"OIL-3005": 0, "OIL-3009": 0}, mandate["committee_expected_targets"])

    def test_governance_api_has_no_future_market_input(self) -> None:
        for function in (
            build_strategy_charter,
            build_strategy_capital_mandate,
            build_company_risk_appetite,
            build_strategy_position_mandate,
        ):
            parameters = set(inspect.signature(function).parameters)
            self.assertTrue({"global_run", "future_market", "next_market"}.isdisjoint(parameters))


if __name__ == "__main__":
    unittest.main()
