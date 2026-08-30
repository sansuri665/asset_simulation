from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.corporate_risk_control import (
    RISK_APPETITE_DIMENSIONS,
    approve_oil_strategy_targets,
    build_default_corporate_risk_profile,
    generate_corporate_risk_candidate,
    generate_corporate_risk_roster,
    resolve_corporate_risk_profile,
)
from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_short_term_forecast import generate_oil_short_term_forecast
from asset_simulation.model.oil_trading_strategy import build_oil_strategy_decision


def _profile(score: float, personnel_id: str = "risk_calibration") -> dict:
    return {
        "appointment": {
            "personnel_id": personnel_id,
            "display_name": personnel_id,
            "source": "test_calibration",
            "candidate_index": None,
            "generation_seed": None,
        },
        "risk_appetite_radar": {
            key: float(score) for key in RISK_APPETITE_DIMENSIONS
        },
    }


class CorporateRiskControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.global_run = run_global_macro(42, 7)
        cls.market = oil_futures_payload(
            cls.global_run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        cls.forecast = generate_oil_short_term_forecast(
            cls.global_run, as_of_year=2030, as_of_month=1, as_of_half=1
        )

    def test_default_profile_is_continuous_company_policy_without_total_score(self) -> None:
        profile = build_default_corporate_risk_profile()
        self.assertIsNone(profile["risk_appetite_total_score"])
        self.assertFalse(profile["governance"]["higher_score_is_better"])
        self.assertEqual("company_level", profile["governance"]["scope"])
        self.assertEqual(
            0.75,
            profile["resolved_policy"]["capital"][
                "max_gross_market_limit_utilization"
            ],
        )
        decimal = resolve_corporate_risk_profile(_profile(50.01))
        higher = resolve_corporate_risk_profile(_profile(50.02))
        self.assertLess(
            decimal["resolved_policy"]["capital"]["max_initial_margin_pct_of_equity"],
            higher["resolved_policy"]["capital"]["max_initial_margin_pct_of_equity"],
        )

    def test_seed_roster_is_deterministic_nonflat_and_tamper_evident(self) -> None:
        first = generate_corporate_risk_roster(seed=42, candidate_count=5)
        self.assertEqual(first, generate_corporate_risk_roster(seed=42, candidate_count=5))
        self.assertNotEqual(
            [item["profile_hash"] for item in first["candidates"]],
            [item["profile_hash"] for item in generate_corporate_risk_roster(seed=43, candidate_count=5)["candidates"]],
        )
        self.assertTrue(all(item["risk_appetite_total_score"] is None for item in first["candidates"]))
        profile = generate_corporate_risk_candidate(seed=7, candidate_index=2)
        modified = deepcopy(profile)
        modified["risk_appetite_radar"]["capital_tolerance"] += 0.01
        with self.assertRaises(ValueError):
            resolve_corporate_risk_profile(modified)

    def test_legacy_cro_profiles_do_not_enter_single_strategy_runtime(self) -> None:
        neutral_strategy_risk = build_default_corporate_risk_profile()
        neutral = build_oil_strategy_decision(
            self.market,
            self.forecast,
            strategy_risk_profile=neutral_strategy_risk,
        )
        strict = build_oil_strategy_decision(
            self.market,
            self.forecast,
            strategy_risk_profile=neutral_strategy_risk,
            corporate_risk_profile=_profile(0.0, "strict"),
        )
        self.assertEqual(neutral, strict)
        self.assertEqual("dormant_single_strategy", strict["portfolioRisk"]["status"])
        self.assertIsNone(strict["portfolioRisk"]["personnel"])
        self.assertEqual(
            0, strict["portfolioRisk"]["approval_summary"]["clipped_gross_lots"]
        )

    def test_dormant_company_module_retains_future_portfolio_drawdown_logic(self) -> None:
        decision = build_oil_strategy_decision(
            self.market,
            self.forecast,
        )
        raw_targets = {
            str(item["contract_id"]): dict(item) for item in decision["targets"]
        }
        for item in raw_targets.values():
            item["target_position_lots"] = item["strategy_target_position_lots"]
        _, strict = approve_oil_strategy_targets(
            self.market,
            raw_targets,
            positions={},
            equity_usd=3_000_000_000.0,
            risk_profile=_profile(0.0, "strict_drawdown"),
            risk_state={
                "peak_equity_usd": 4_000_000_000.0,
                "drawdown_scale": 1.0,
            },
        )
        self.assertEqual("reduce_only", strict["state"]["risk_status"])
        _, recovered = approve_oil_strategy_targets(
            self.market,
            raw_targets,
            positions={},
            equity_usd=3_000_000_000.0,
            risk_profile=build_default_corporate_risk_profile(),
            risk_state={
                "peak_equity_usd": 3_000_000_000.0,
                "drawdown_scale": 0.15,
            },
        )
        self.assertAlmostEqual(0.25, recovered["state"]["drawdown_scale"])
        self.assertTrue(recovered["state"]["scale_recovery_capped"])


if __name__ == "__main__":
    unittest.main()
