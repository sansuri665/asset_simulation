from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.audit_oil_multi_strategy_gate_b_runtime import (
    audit_oil_multi_strategy_gate_b_runtime,
)
from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_multi_strategy_authorization import (
    create_strategy_capital_authorization_state,
)
from asset_simulation.model.oil_multi_strategy_execution import (
    CALENDAR_SPREAD_STRATEGY_ID,
    DIRECTIONAL_STRATEGY_ID,
    execute_oil_multi_strategy_parent_orders,
    settle_oil_multi_strategy_allocated_turn,
)
from asset_simulation.model.oil_multi_strategy_gate_b import (
    allocate_gate_b_strategy_orders,
    build_gate_b_market_limits_from_oil_futures_payload,
)
from asset_simulation.model.oil_multi_strategy_runtime import (
    create_oil_multi_strategy_runtime_state,
    simulate_oil_multi_strategy_runtime,
)


class OilMultiStrategyRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.run = run_global_macro(42, 7)
        cls.start = oil_futures_payload(
            cls.run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        cls.end = oil_futures_payload(
            cls.run, as_of_year=2030, as_of_month=1, as_of_half=2
        )
        listed = [
            str(item["contract_id"])
            for item in cls.start["curve"]["contracts"]
        ]
        cls.main_id = str(cls.start["curve"]["main_contract_id"])
        cls.next_id = listed[listed.index(cls.main_id) + 1]

    def _authorization(self, directional: float, spread: float) -> dict:
        return create_strategy_capital_authorization_state(
            decision_id=f"TEST-{directional:g}-{spread:g}",
            effective_turn=str(self.start["asOf"]["label"]),
            reference_company_equity_usd=10_000_000.0,
            strategy_authorizations_usd={
                DIRECTIONAL_STRATEGY_ID: directional,
                CALENDAR_SPREAD_STRATEGY_ID: spread,
            },
        )

    def test_internal_cross_executes_once_and_reconciles_strategy_books(self) -> None:
        authorization = self._authorization(5_000_000.0, 5_000_000.0)
        state = create_oil_multi_strategy_runtime_state(
            initial_market=self.start,
            authorization_state=authorization,
        )
        limits = build_gate_b_market_limits_from_oil_futures_payload(
            self.start, maximum_initial_margin_usd=9_000_000.0
        )
        limits = deepcopy(limits)
        limits["contracts"][self.main_id]["turn_trade_limit_lots"] = 100
        limits["contracts"][self.next_id]["turn_trade_limit_lots"] = 100
        allocation = allocate_gate_b_strategy_orders(
            authorization_state=authorization,
            current_company_equity_usd=10_000_000.0,
            strategy_book_positions={
                DIRECTIONAL_STRATEGY_ID: {},
                CALENDAR_SPREAD_STRATEGY_ID: {},
            },
            current_formal_positions={},
            market_limits=limits,
            order_groups=[
                {
                    "strategy_id": DIRECTIONAL_STRATEGY_ID,
                    "group_id": "directional-main",
                    "priority": "risk_increase",
                    "requested_units": 80,
                    "legs": {self.main_id: 1},
                },
                {
                    "strategy_id": CALENDAR_SPREAD_STRATEGY_ID,
                    "group_id": "spread-pair",
                    "priority": "risk_increase",
                    "requested_units": 60,
                    "legs": {self.main_id: -1, self.next_id: 1},
                    "atomic": True,
                },
            ],
        )
        self.assertEqual(
            {self.main_id: 20, self.next_id: 60},
            allocation["externalParentOrders"],
        )
        self.assertEqual(60, allocation["turnoverDiagnostics"]["internal_cross_lots"])
        desk = execute_oil_multi_strategy_parent_orders(
            self.start,
            self.end,
            external_parent_orders=allocation["externalParentOrders"],
            formal_positions_before={},
            benchmark_contract_ids=[self.main_id],
        )
        self.assertEqual(1, desk["desk"]["shared_desk_count"])
        self.assertGreater(desk["summary"]["execution_cost_usd"], 0.0)
        settlement = settle_oil_multi_strategy_allocated_turn(
            start_market=self.start,
            end_market=self.end,
            allocation=allocation,
            desk_execution=desk,
            strategy_books_state=state["strategyBooks"],
            formal_account_state=state["formalAccount"],
            corporate_reserve_usd=state["corporateReserveUsd"],
        )
        self.assertTrue(settlement["all_hard_gates_pass"])
        self.assertEqual(
            settlement["formalAccount"]["accountAfter"]["positions"],
            {
                self.main_id: 20,
                self.next_id: 60,
            },
        )
        self.assertAlmostEqual(
            0.0,
            sum(
                item["internal_transfer_pnl_usd"]
                for item in settlement["strategyBooks"].values()
            ),
            places=2,
        )

    def test_dual_strategy_runtime_is_deterministic_and_amount_authoritative(self) -> None:
        first = simulate_oil_multi_strategy_runtime(
            self.run,
            strategy_authorizations_usd={
                DIRECTIONAL_STRATEGY_ID: 5_000_000.0,
                CALENDAR_SPREAD_STRATEGY_ID: 5_000_000.0,
            },
            maximum_turns=2,
        )
        repeat = simulate_oil_multi_strategy_runtime(
            self.run,
            strategy_authorizations_usd={
                DIRECTIONAL_STRATEGY_ID: 5_000_000.0,
                CALENDAR_SPREAD_STRATEGY_ID: 5_000_000.0,
            },
            maximum_turns=2,
        )
        self.assertEqual(first, repeat)
        self.assertTrue(first["all_mechanical_hard_gates_pass"])
        self.assertFalse(first["long_horizon_economic_result_valid"])
        entries = first["finalState"]["authorizationState"]["authorizations"]
        self.assertEqual(
            5_000_000.0,
            entries[DIRECTIONAL_STRATEGY_ID]["authorized_capital_usd"],
        )
        self.assertEqual(
            5_000_000.0,
            entries[CALENDAR_SPREAD_STRATEGY_ID]["authorized_capital_usd"],
        )
        self.assertEqual(2, first["performance"]["completed_turns"])
        self.assertTrue(
            all(report["all_hard_gates_pass"] for report in first["turnReports"])
        )

    def test_calendar_spread_pending_thesis_ledger_matures_exact_horizons(self) -> None:
        report = simulate_oil_multi_strategy_runtime(
            self.run,
            strategy_authorizations_usd={
                DIRECTIONAL_STRATEGY_ID: 5_000_000.0,
                CALENDAR_SPREAD_STRATEGY_ID: 5_000_000.0,
            },
            maximum_turns=2,
        )
        first_turn = report["turnReports"][0]
        second_turn = report["turnReports"][1]
        self.assertGreaterEqual(
            len(first_turn["thesis"]["calendar_spread_matured"]), 1
        )
        self.assertGreaterEqual(
            first_turn["thesis"]["calendar_spread_pending_count"], 1
        )
        self.assertGreaterEqual(
            len(second_turn["thesis"]["calendar_spread_matured"]), 1
        )
        self.assertTrue(
            all(
                item["evaluated_target_week_serial"]
                == item["realized_week_serial"]
                for turn in report["turnReports"]
                for item in turn["thesis"]["calendar_spread_matured"]
            )
        )

    def test_runtime_stops_before_pair_change_without_roll_scheduler(self) -> None:
        report = simulate_oil_multi_strategy_runtime(
            self.run,
            strategy_authorizations_usd={
                DIRECTIONAL_STRATEGY_ID: 5_000_000.0,
                CALENDAR_SPREAD_STRATEGY_ID: 5_000_000.0,
            },
            maximum_turns=20,
        )
        self.assertLess(report["performance"]["completed_turns"], 20)
        self.assertEqual(
            "main_next_pair_change_requires_roll_scheduler",
            report["lifecycle"]["stop_reason"],
        )
        self.assertFalse(report["long_horizon_economic_result_valid"])

    def test_first_comparison_audit_runs_both_amount_vectors(self) -> None:
        report = audit_oil_multi_strategy_gate_b_runtime(
            seeds=[42], maximum_turns=1
        )
        self.assertTrue(report["all_hard_gates_pass"])
        row = report["scenarios"][0]
        self.assertEqual(1, row["directional_only"]["completed_turns"])
        self.assertEqual(1, row["fixed_5m_5m"]["completed_turns"])
        self.assertIn(
            CALENDAR_SPREAD_STRATEGY_ID,
            row["fixed_5m_5m"]["strategy_fully_loaded_pnl_usd"],
        )


if __name__ == "__main__":
    unittest.main()
