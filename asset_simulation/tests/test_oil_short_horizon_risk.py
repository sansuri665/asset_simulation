from __future__ import annotations

from copy import deepcopy
import inspect
import unittest

from asset_simulation.model.investment_decision import (
    build_company_risk_appetite,
    build_strategy_capital_mandate,
    build_strategy_charter,
    build_strategy_position_mandate,
)
from asset_simulation.model.oil_short_horizon_risk import (
    build_default_oil_short_horizon_risk_profile,
    build_oil_short_horizon_risk_review,
    generate_oil_short_horizon_risk_roster,
    resolve_oil_short_horizon_risk_profile,
)


def _weeks(start: float) -> list[dict[str, object]]:
    return [
        {
            "week_serial": 1000 + index,
            "year": 2029,
            "month": 10 + index // 4,
            "week": index % 4 + 1,
            "close": start * (1.0 + 0.003 * index + 0.002 * ((index % 3) - 1)),
        }
        for index in range(12)
    ]


def _market() -> dict[str, object]:
    return {
        "ok": True,
        "asOf": {"year": 2030, "month": 1, "half": 1},
        "contractSpecification": {
            "contract_size_bbl": 1000,
            "initial_margin_rate_pct": 10.0,
        },
        "participantLimitsPolicy": {
            "all_contract_gross_position_cap_lots": 1_000_000,
        },
        "curve": {
            "main_contract_id": "OIL-3005",
            "contracts": [
                {
                    "contract_id": "OIL-3005",
                    "price_usd": 70.0,
                    "half_turns_to_expiry": 10,
                    "participantLimits": {
                        "single_contract_position_limit_lots": 500_000,
                        "turn_trade_limit_lots": 100_000,
                        "new_trades_allowed": True,
                    },
                    "monthly": [{"year": 2029, "month": 12, "weekly": _weeks(68.0)}],
                },
                {
                    "contract_id": "OIL-3009",
                    "price_usd": 71.0,
                    "half_turns_to_expiry": 18,
                    "participantLimits": {
                        "single_contract_position_limit_lots": 500_000,
                        "turn_trade_limit_lots": 100_000,
                        "new_trades_allowed": True,
                    },
                    "monthly": [{"year": 2029, "month": 12, "weekly": _weeks(69.0)}],
                },
            ],
        },
    }


def _mandate(*, pct: float, targets: dict[str, int], strategy_type: str = "directional") -> tuple[dict, float]:
    charter = build_strategy_charter(
        asset="oil",
        horizon="short_horizon",
        strategy_type=strategy_type,
        strategy_id=f"test-{strategy_type}-{pct:g}",
    )
    capital = build_strategy_capital_mandate(
        charter,
        company_equity_usd=100_000_000.0,
        authorized_pct_of_company_equity=pct,
    )
    position = build_strategy_position_mandate(charter, capital, targets)
    return position, float(capital["authorized_capital_usd"])


class OilShortHorizonRiskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.market = _market()
        self.appetite = build_company_risk_appetite()

    def test_roster_is_seeded_scoped_and_capability_is_narrower_than_style_design(self) -> None:
        first = generate_oil_short_horizon_risk_roster(seed=42, candidate_count=5)
        repeat = generate_oil_short_horizon_risk_roster(seed=42, candidate_count=5)
        self.assertEqual(first, repeat)
        for candidate in first["candidates"]:
            self.assertEqual({"asset": "oil", "horizon": "short_horizon"}, candidate["coverage_scope"])
            self.assertEqual({"directional", "calendar_spread"}, set(candidate["supported_strategy_types"]))
            self.assertIsNone(candidate["style_total_score"])
            self.assertIsNone(candidate["capability_total_score"])
            self.assertTrue(all(10.0 <= value <= 90.0 for value in candidate["style_radar"].values()))
            self.assertTrue(all(35.0 <= value <= 70.0 for value in candidate["capability_radar"].values()))

    def test_review_requires_actual_position_mandate_and_never_recommends_capital(self) -> None:
        with self.assertRaisesRegex(ValueError, "actual committee position mandate"):
            build_oil_short_horizon_risk_review(
                self.market,
                {},
                company_equity_usd=100_000_000.0,
                allocated_strategy_capital_usd=10_000_000.0,
                company_risk_appetite=self.appetite,
            )
        mandate, allocated = _mandate(pct=10.0, targets={"OIL-3005": 500})
        review = build_oil_short_horizon_risk_review(
            self.market,
            mandate,
            company_equity_usd=100_000_000.0,
            allocated_strategy_capital_usd=allocated,
            company_risk_appetite=self.appetite,
        )
        self.assertEqual(2.0, float(review["riskHorizon"]["review_horizon_weeks"]))
        self.assertEqual(
            2.0,
            float(review["softRiskEstimatesBeforePortfolioScale"]["risk_horizon_weeks"]),
        )
        self.assertFalse(review["capitalContext"]["capital_recommendation_produced"])
        self.assertFalse(review["governance"]["capital_recommendation_produced"])
        self.assertNotIn("recommended_capital_authorization_pct_of_company_equity", str(review))

    def test_one_percent_and_fifty_percent_books_have_same_relative_but_different_company_materiality(self) -> None:
        small_mandate, small_capital = _mandate(pct=1.0, targets={"OIL-3005": 100})
        large_mandate, large_capital = _mandate(pct=50.0, targets={"OIL-3005": 5_000})
        small = build_oil_short_horizon_risk_review(
            self.market,
            small_mandate,
            company_equity_usd=100_000_000.0,
            allocated_strategy_capital_usd=small_capital,
            company_risk_appetite=self.appetite,
        )
        large = build_oil_short_horizon_risk_review(
            self.market,
            large_mandate,
            company_equity_usd=100_000_000.0,
            allocated_strategy_capital_usd=large_capital,
            company_risk_appetite=self.appetite,
        )
        small_m = small["materialityBeforePortfolioScale"]
        large_m = large["materialityBeforePortfolioScale"]
        self.assertAlmostEqual(
            small_m["stress_loss_pct_of_allocated_strategy_capital"],
            large_m["stress_loss_pct_of_allocated_strategy_capital"],
            places=6,
        )
        self.assertAlmostEqual(
            50.0 * small_m["stress_loss_pct_of_company_equity"],
            large_m["stress_loss_pct_of_company_equity"],
            places=6,
        )
        self.assertLessEqual(large["portfolioScale"], small["portfolioScale"])

    def test_risk_can_only_preserve_or_reduce_committee_targets(self) -> None:
        mandate, allocated = _mandate(pct=20.0, targets={"OIL-3005": 30_000})
        review = build_oil_short_horizon_risk_review(
            self.market,
            mandate,
            company_equity_usd=100_000_000.0,
            allocated_strategy_capital_usd=allocated,
            company_risk_appetite=self.appetite,
        )
        expected = review["committeeExpectedTargets"]["OIL-3005"]
        approved = review["riskApprovedTargets"]["OIL-3005"]
        self.assertGreaterEqual(expected, 0)
        self.assertGreaterEqual(approved, 0)
        self.assertLessEqual(abs(approved), abs(expected))

    def test_capability_changes_soft_estimates_but_not_hard_facts(self) -> None:
        mandate, allocated = _mandate(pct=10.0, targets={"OIL-3005": 500})
        base = build_default_oil_short_horizon_risk_profile()
        low = deepcopy(base)
        low.pop("profile_hash")
        low["capability_radar"] = {key: 35.0 for key in low["capability_radar"]}
        high = deepcopy(base)
        high.pop("profile_hash")
        high["capability_radar"] = {key: 70.0 for key in high["capability_radar"]}
        low = resolve_oil_short_horizon_risk_profile(low)
        high = resolve_oil_short_horizon_risk_profile(high)
        low_review = build_oil_short_horizon_risk_review(
            self.market,
            mandate,
            company_equity_usd=100_000_000.0,
            allocated_strategy_capital_usd=allocated,
            company_risk_appetite=self.appetite,
            risk_profile=low,
        )
        high_review = build_oil_short_horizon_risk_review(
            self.market,
            mandate,
            company_equity_usd=100_000_000.0,
            allocated_strategy_capital_usd=allocated,
            company_risk_appetite=self.appetite,
            risk_profile=high,
        )
        self.assertEqual(low_review["hardFacts"], high_review["hardFacts"])
        self.assertNotEqual(
            low_review["softRiskEstimatesBeforePortfolioScale"],
            high_review["softRiskEstimatesBeforePortfolioScale"],
        )

    def test_style_has_large_policy_effect_without_changing_hard_facts(self) -> None:
        mandate, allocated = _mandate(pct=10.0, targets={"OIL-3005": 500})
        base = build_default_oil_short_horizon_risk_profile()
        patient = deepcopy(base)
        patient.pop("profile_hash")
        patient["style_radar"]["intervention_earliness"] = 10.0
        patient["style_radar"]["liquidity_priority"] = 10.0
        early = deepcopy(base)
        early.pop("profile_hash")
        early["style_radar"]["intervention_earliness"] = 90.0
        early["style_radar"]["liquidity_priority"] = 90.0
        patient_review = build_oil_short_horizon_risk_review(
            self.market,
            mandate,
            company_equity_usd=100_000_000.0,
            allocated_strategy_capital_usd=allocated,
            company_risk_appetite=self.appetite,
            risk_profile=resolve_oil_short_horizon_risk_profile(patient),
        )
        early_review = build_oil_short_horizon_risk_review(
            self.market,
            mandate,
            company_equity_usd=100_000_000.0,
            allocated_strategy_capital_usd=allocated,
            company_risk_appetite=self.appetite,
            risk_profile=resolve_oil_short_horizon_risk_profile(early),
        )
        self.assertEqual(patient_review["hardFacts"], early_review["hardFacts"])
        self.assertGreater(
            patient_review["companyRiskAppetite"]["resolved_limits"]["max_liquidation_half_turns"],
            early_review["companyRiskAppetite"]["resolved_limits"]["max_liquidation_half_turns"],
        )

    def test_calendar_spread_is_covered_by_same_group(self) -> None:
        mandate, allocated = _mandate(
            pct=10.0,
            strategy_type="calendar_spread",
            targets={"OIL-3005": 100, "OIL-3009": -100},
        )
        review = build_oil_short_horizon_risk_review(
            self.market,
            mandate,
            company_equity_usd=100_000_000.0,
            allocated_strategy_capital_usd=allocated,
            company_risk_appetite=self.appetite,
        )
        self.assertEqual("calendar_spread", review["strategy"]["strategy_type"])
        self.assertIsNotNone(review["softRiskEstimatesBeforePortfolioScale"]["calendar_spread"])

    def test_public_review_api_has_no_future_input(self) -> None:
        parameters = set(inspect.signature(build_oil_short_horizon_risk_review).parameters)
        self.assertTrue({"global_run", "future_market", "next_market"}.isdisjoint(parameters))


if __name__ == "__main__":
    unittest.main()
