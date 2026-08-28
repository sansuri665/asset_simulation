from __future__ import annotations

import unittest

from asset_simulation.audit_corporate_risk_effectiveness import (
    _aggregate_rows,
    _paired_delta,
    _risk_profile,
)


class CorporateRiskEffectivenessAuditTests(unittest.TestCase):
    def test_fixed_risk_profile_is_continuous_and_uniform(self) -> None:
        profile = _risk_profile("strict", 12.5)

        self.assertEqual(
            {12.5}, set(profile["risk_appetite_radar"].values())
        )
        self.assertFalse(profile["governance"]["higher_score_is_better"])

    def test_paired_delta_keeps_seed_pairing(self) -> None:
        rows = {
            (0, "strict"): {
                "return_pct": 5.0,
                "annualized_return_pct": 5.0,
                "maximum_drawdown_pct": -2.0,
                "annualized_volatility_pct": 4.0,
                "calmar": 2.5,
                "maximum_margin_to_equity_pct": 5.0,
                "ending_equity_usd": 105.0,
            },
            (0, "neutral"): {
                "return_pct": 3.0,
                "annualized_return_pct": 3.0,
                "maximum_drawdown_pct": -4.0,
                "annualized_volatility_pct": 8.0,
                "calmar": 0.75,
                "maximum_margin_to_equity_pct": 10.0,
                "ending_equity_usd": 103.0,
            },
        }

        report = _paired_delta(
            rows, [0], left="strict", right="neutral"
        )

        self.assertEqual(2.0, report["median_delta"]["return_pct"])
        self.assertEqual(2.0, report["median_delta"]["maximum_drawdown_pct"])
        self.assertEqual(1, report["left_higher_ending_equity_seed_count"])
        self.assertEqual(0, report["right_higher_ending_equity_seed_count"])
        self.assertEqual(0, report["equal_ending_equity_seed_count"])

    def test_aggregate_reports_tail_and_risk_activity(self) -> None:
        base = {
            "return_pct": 5.0,
            "annualized_return_pct": 2.0,
            "maximum_drawdown_pct": -3.0,
            "annualized_volatility_pct": 4.0,
            "calmar": 0.666,
            "traded_lots": 100,
            "execution_cost_usd": 200.0,
            "maximum_margin_to_equity_pct": 5.0,
            "risk_clipped_gross_lots": 10,
            "risk_binding_turns": 1,
            "risk_status_counts": {
                "normal": 2,
                "watch": 1,
                "restricted": 0,
                "reduce_only": 0,
            },
        }

        report = _aggregate_rows([base, {**base, "return_pct": -5.0}])

        self.assertEqual(0.0, report["median_return_pct"])
        self.assertEqual(20, report["total_risk_clipped_gross_lots"])
        self.assertEqual(2, report["total_risk_status_counts"]["watch"])


if __name__ == "__main__":
    unittest.main()
