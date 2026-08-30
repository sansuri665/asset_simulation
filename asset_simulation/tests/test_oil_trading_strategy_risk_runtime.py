from __future__ import annotations

import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_short_term_forecast import generate_oil_short_term_forecast
from asset_simulation.model.oil_trading_strategy import (
    build_oil_strategy_decision,
    simulate_oil_trading_strategy,
)
from asset_simulation.model.oil_trading_strategy_risk_runtime import (
    LEGACY_RISK_RUNTIME,
    V2_CANDIDATE_RISK_RUNTIME,
    apply_v2_candidate_risk_to_directional_decision,
    simulate_oil_trading_strategy_with_risk_runtime,
)


class OilTradingStrategyRiskRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.global_run = run_global_macro(42, 7)

    def test_legacy_router_is_exact_frozen_simulator(self) -> None:
        kwargs = {
            "start_year": 2030,
            "start_month": 1,
            "start_half": 1,
            "end_year": 2030,
            "end_month": 2,
            "end_half": 1,
            "capital_authorization_pct_of_company_equity": 60.0,
        }
        direct = simulate_oil_trading_strategy(self.global_run, **kwargs)
        routed = simulate_oil_trading_strategy_with_risk_runtime(
            self.global_run,
            risk_runtime=LEGACY_RISK_RUNTIME,
            **kwargs,
        )
        self.assertEqual(direct, routed)

    def test_v2_candidate_replaces_only_binding_targets_and_turnover_scale(self) -> None:
        market = oil_futures_payload(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        vintage = generate_oil_short_term_forecast(
            self.global_run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        raw = build_oil_strategy_decision(
            market,
            vintage,
            capital_authorization_pct_of_company_equity=60.0,
        )
        candidate = apply_v2_candidate_risk_to_directional_decision(
            market,
            raw,
            capital_authorization_pct_of_company_equity=60.0,
        )
        self.assertEqual(V2_CANDIDATE_RISK_RUNTIME, candidate["riskRuntime"]["mode"])
        self.assertFalse(candidate["legacyRiskDiagnostic"]["binding"])
        self.assertFalse(
            candidate["strategyRiskV2"]["governance"][
                "company_materiality_binding_in_strategy_review"
            ]
        )
        for before, after in zip(raw["targets"], candidate["targets"], strict=True):
            self.assertEqual(
                int(before["strategy_intent_target_position_lots"]),
                int(after["strategy_intent_target_position_lots"]),
            )
            self.assertEqual(before["signal"], after["signal"])
            self.assertEqual(before["thesis_action"], after["thesis_action"])
            self.assertEqual(
                before["weekly_turnover_setups"],
                after["weekly_turnover_setups"],
            )
            pm = int(after["strategy_intent_target_position_lots"])
            approved = int(after["target_position_lots"])
            self.assertLessEqual(abs(approved), abs(pm))
            self.assertGreaterEqual(approved * pm, 0)
            self.assertLessEqual(
                int(after["gross_turnover_budget_lots"]),
                int(after["strategy_gross_turnover_budget_lots"]),
            )

    def test_v2_candidate_router_is_deterministic_and_non_default(self) -> None:
        kwargs = {
            "start_year": 2030,
            "start_month": 1,
            "start_half": 1,
            "end_year": 2030,
            "end_month": 2,
            "end_half": 1,
            "capital_authorization_pct_of_company_equity": 35.0,
        }
        first = simulate_oil_trading_strategy_with_risk_runtime(
            self.global_run,
            risk_runtime=V2_CANDIDATE_RISK_RUNTIME,
            **kwargs,
        )
        second = simulate_oil_trading_strategy_with_risk_runtime(
            self.global_run,
            risk_runtime=V2_CANDIDATE_RISK_RUNTIME,
            **kwargs,
        )
        self.assertEqual(first, second)
        self.assertEqual(V2_CANDIDATE_RISK_RUNTIME, first["riskRuntime"]["mode"])
        self.assertEqual(LEGACY_RISK_RUNTIME, first["riskRuntime"]["production_default"])
        for turn in first["turns"]:
            self.assertEqual(
                V2_CANDIDATE_RISK_RUNTIME,
                turn["decision"]["riskRuntime"]["mode"],
            )
            self.assertFalse(turn["decision"]["legacyRiskDiagnostic"]["binding"])

    def test_router_rejects_ambiguous_cross_runtime_profiles(self) -> None:
        with self.assertRaises(ValueError):
            simulate_oil_trading_strategy_with_risk_runtime(
                self.global_run,
                risk_runtime="unknown",
            )
        with self.assertRaises(ValueError):
            simulate_oil_trading_strategy_with_risk_runtime(
                self.global_run,
                risk_runtime=LEGACY_RISK_RUNTIME,
                company_risk_appetite={"not": "legacy"},
            )
        with self.assertRaises(ValueError):
            simulate_oil_trading_strategy_with_risk_runtime(
                self.global_run,
                risk_runtime=V2_CANDIDATE_RISK_RUNTIME,
                corporate_risk_profile={"not": "candidate"},
            )


if __name__ == "__main__":
    unittest.main()
