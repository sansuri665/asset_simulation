from __future__ import annotations

import unittest
from unittest.mock import patch

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_short_horizon_risk_v2 import (
    build_oil_short_horizon_risk_review,
)
from asset_simulation.model.oil_trading_strategy_risk_runtime import (
    V2_CANDIDATE_RISK_RUNTIME,
    simulate_oil_trading_strategy_with_risk_runtime,
)
from asset_simulation.tests import (
    test_oil_short_horizon_risk_economic_counterfactual as economic,
)


class OilTradingStrategyRiskRuntimeEquivalenceTests(unittest.TestCase):
    def test_integrated_candidate_matches_standalone_counterfactual(self) -> None:
        global_run = run_global_macro(2, 10)
        allocation_pct = 60.0
        with patch.object(
            economic,
            "build_oil_short_horizon_risk_review",
            new=build_oil_short_horizon_risk_review,
        ):
            standalone = economic._simulate_v2_counterfactual(
                global_run, allocation_pct
            )
        integrated = simulate_oil_trading_strategy_with_risk_runtime(
            global_run,
            risk_runtime=V2_CANDIDATE_RISK_RUNTIME,
            start_year=economic.START[0],
            start_month=economic.START[1],
            start_half=economic.START[2],
            end_year=economic.END[0],
            end_month=economic.END[1],
            end_half=economic.END[2],
            capital_authorization_pct_of_company_equity=allocation_pct,
        )
        summary = integrated["summary"]
        for key in (
            "initial_equity_usd",
            "ending_equity_usd",
            "return_pct",
            "cagr_pct",
            "maximum_drawdown_pct",
            "annualized_turn_volatility_pct",
            "return_to_drawdown",
            "worst_turn_return_pct",
            "p10_turn_return_pct",
            "total_traded_lots",
            "total_net_traded_lots",
            "execution_cost_usd",
            "total_traded_notional_usd",
            "friction_bps",
            "maximum_margin_to_equity_pct",
            "clipped_turns",
            "turn_count",
            "ending_positions",
        ):
            self.assertEqual(standalone[key], summary[key], key)
        self.assertEqual(standalone["approval_ratio"], summary["approval_ratio"])
        self.assertEqual(standalone["binding_counts"], summary["binding_counts"])
        self.assertEqual(
            V2_CANDIDATE_RISK_RUNTIME,
            integrated["riskRuntime"]["mode"],
        )
        self.assertFalse(integrated["riskRuntime"]["legacy_risk_objects_binding"])


if __name__ == "__main__":
    unittest.main()
