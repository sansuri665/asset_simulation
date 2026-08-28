from __future__ import annotations

import math
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_futures_account import (
    _finalize_state,
    apply_oil_futures_account_constraints,
    create_oil_futures_account,
    oil_futures_account_snapshot,
    settle_oil_futures_account_turn,
)
from asset_simulation.model.oil_futures_overlay import oil_futures_payload


class OilFuturesAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        run = run_global_macro(42, 6)
        cls.start = oil_futures_payload(
            run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        cls.end = oil_futures_payload(
            run, as_of_year=2030, as_of_month=1, as_of_half=2
        )
        cls.contract_id = str(cls.start["curve"]["main_contract_id"])

    def _state_with_position(self, *, cash: float, lots: int) -> dict:
        state = create_oil_futures_account(
            account_id="TEST", initial_cash_usd=max(cash, 1.0)
        )
        state.update(
            {
                "initial_equity_usd": max(cash, 1.0),
                "cash_balance_usd": cash,
                "equity_usd": cash,
                "positions": {self.contract_id: lots},
            }
        )
        return _finalize_state(state)

    def _flat_strategy_statement(self, state: dict, *, pnl: float) -> dict:
        positions = dict(state["positions"])
        return {
            "decision_id": "synthetic-account-fault-test",
            "contracts": [
                {
                    "contract_id": contract_id,
                    "starting_position_lots": lots,
                }
                for contract_id, lots in positions.items()
            ],
            "accountAfter": {
                "positions": positions,
                "turn_pnl_usd": pnl,
                "gross_pnl_before_cost_usd": pnl,
                "execution_cost_usd": 0.0,
            },
        }

    def test_initial_margin_is_restricted_not_double_deducted(self) -> None:
        state = self._state_with_position(cash=100_000_000.0, lots=1_000)
        snapshot = oil_futures_account_snapshot(state, self.start)
        self.assertEqual(snapshot["cash_balance_usd"], snapshot["equity_usd"])
        self.assertGreater(snapshot["restricted_initial_margin_usd"], 0.0)
        self.assertAlmostEqual(
            snapshot["available_funds_usd"],
            snapshot["cash_balance_usd"]
            - snapshot["restricted_initial_margin_usd"],
            places=2,
        )

    def test_account_authorization_cannot_expand_and_caps_margin(self) -> None:
        state = create_oil_futures_account(
            account_id="TEST", initial_cash_usd=1_000_000.0
        )
        decision = {
            "identity": {"result_hash": "pre-account"},
            "targets": [
                {
                    "contract_id": self.contract_id,
                    "role": "main",
                    "target_position_lots": 10_000,
                    "gross_turnover_budget_lots": 10_000,
                    "weekly_turnover_setups": [],
                }
            ],
        }
        result = apply_oil_futures_account_constraints(
            state, self.start, decision
        )
        authorization = result["authorization"]
        approved = authorization["approved_positions"][self.contract_id]
        self.assertLess(approved, 10_000)
        self.assertLessEqual(
            authorization["approved_initial_margin_usd"],
            authorization["maximum_initial_margin_usd"] + 0.01,
        )
        self.assertFalse(authorization["account_can_expand_prior_approval"])

    def test_margin_breach_calls_and_forces_liquidation_with_cash_identity(self) -> None:
        state = self._state_with_position(cash=8_000_000.0, lots=1_500)
        statement = self._flat_strategy_statement(state, pnl=-2_000_000.0)
        result = settle_oil_futures_account_turn(
            state, self.start, self.end, statement
        )
        ledger = result["ledger"]
        after = result["accountAfter"]
        self.assertTrue(ledger["margin_call_triggered"])
        self.assertGreater(ledger["margin_call_amount_usd"], 0.0)
        self.assertGreater(ledger["forced_liquidation_lots"], 0)
        self.assertAlmostEqual(ledger["cash_identity_error_usd"], 0.0, places=2)
        self.assertGreaterEqual(
            after["cash_balance_usd"] + 0.01,
            after["maintenance_margin_usd"],
        )
        self.assertEqual("forced_liquidation", after["status"])

    def test_idle_cash_interest_and_financing_are_separate_from_trading_pnl(self) -> None:
        state = self._state_with_position(cash=100_000_000.0, lots=1_000)
        statement = self._flat_strategy_statement(state, pnl=0.0)
        result = settle_oil_futures_account_turn(
            state, self.start, self.end, statement
        )
        ledger = result["ledger"]
        self.assertGreater(ledger["idle_cash_interest_usd"], 0.0)
        expected = (
            ledger["variation_margin_usd"]
            + ledger["idle_cash_interest_usd"]
            - ledger["margin_financing_cost_usd"]
            - ledger["forced_liquidation_cost_usd"]
        )
        self.assertTrue(
            math.isclose(expected, ledger["account_net_pnl_usd"], abs_tol=0.01)
        )

    def test_insolvency_is_sticky_and_flat(self) -> None:
        state = self._state_with_position(cash=1_100_000.0, lots=500)
        statement = self._flat_strategy_statement(state, pnl=-500_000.0)
        result = settle_oil_futures_account_turn(
            state, self.start, self.end, statement
        )
        self.assertEqual("insolvent", result["accountAfter"]["status"])
        self.assertEqual({}, result["accountAfter"]["positions"])
        self.assertTrue(result["state"]["ever_insolvent"])


if __name__ == "__main__":
    unittest.main()
