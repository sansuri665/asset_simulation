from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.oil_multi_strategy_gate_b import (
    allocate_gate_b_strategy_orders,
    create_strategy_capital_authorization_state,
)


DIRECTIONAL = "directional"
SPREAD = "calendar_spread"
MAIN = "OIL-3005"
NEXT = "OIL-3009"


def _authorization(
    directional_usd: float = 5_000_000.0,
    spread_usd: float = 5_000_000.0,
    *,
    equity: float = 10_000_000.0,
) -> dict:
    return create_strategy_capital_authorization_state(
        decision_id="IC-2030-01-H1",
        effective_turn="2030-01-H1",
        reference_company_equity_usd=equity,
        strategy_authorizations_usd={
            DIRECTIONAL: directional_usd,
            SPREAD: spread_usd,
        },
    )


def _limits(
    *,
    main_trade: int = 100,
    next_trade: int = 100,
    main_position: int = 10_000,
    next_position: int = 10_000,
    gross_position: int = 20_000,
) -> dict:
    return {
        "contracts": {
            MAIN: {
                "turn_trade_limit_lots": main_trade,
                "single_contract_position_limit_lots": main_position,
                "new_trades_allowed": True,
                "initial_margin_usd_per_lot": 10_000.0,
            },
            NEXT: {
                "turn_trade_limit_lots": next_trade,
                "single_contract_position_limit_lots": next_position,
                "new_trades_allowed": True,
                "initial_margin_usd_per_lot": 10_000.0,
            },
        },
        "all_contract_gross_position_cap_lots": gross_position,
    }


def _allocate(
    groups: list[dict],
    *,
    authorization: dict | None = None,
    books: dict | None = None,
    formal: dict | None = None,
    limits: dict | None = None,
    equity: float = 10_000_000.0,
) -> dict:
    return allocate_gate_b_strategy_orders(
        authorization_state=authorization or _authorization(),
        current_company_equity_usd=equity,
        strategy_book_positions=books
        or {DIRECTIONAL: {}, SPREAD: {}},
        current_formal_positions=formal or {},
        market_limits=limits or _limits(),
        order_groups=groups,
    )


class OilMultiStrategyAllocatorTests(unittest.TestCase):
    def test_same_direction_collision_uses_equal_unit_water_filling(self) -> None:
        report = _allocate(
            [
                {
                    "strategy_id": DIRECTIONAL,
                    "group_id": "directional-main",
                    "priority": "risk_increase",
                    "requested_units": 80,
                    "legs": {MAIN: 1},
                },
                {
                    "strategy_id": SPREAD,
                    "group_id": "main-next-pair",
                    "priority": "risk_increase",
                    "requested_units": 80,
                    "legs": {MAIN: 1, NEXT: -1},
                },
            ]
        )
        allocated = {
            (item["strategy_id"], item["group_id"]): item["allocated_units"]
            for item in report["groups"]
        }
        self.assertEqual(50, allocated[(DIRECTIONAL, "directional-main")])
        self.assertEqual(50, allocated[(SPREAD, "main-next-pair")])
        self.assertEqual({MAIN: 100, NEXT: -50}, report["externalParentOrders"])
        self.assertTrue(report["all_hard_gates_pass"])

    def test_internal_cross_saves_external_turnover_and_reconciles_books(self) -> None:
        report = _allocate(
            [
                {
                    "strategy_id": DIRECTIONAL,
                    "group_id": "buy-main",
                    "priority": "risk_increase",
                    "requested_units": 80,
                    "legs": {MAIN: 1},
                },
                {
                    "strategy_id": SPREAD,
                    "group_id": "negative-spread",
                    "priority": "risk_increase",
                    "requested_units": 60,
                    "legs": {MAIN: -1, NEXT: 1},
                },
            ]
        )
        self.assertEqual({MAIN: 20, NEXT: 60}, report["externalParentOrders"])
        self.assertEqual(1, len(report["internalCrosses"]))
        self.assertEqual(60, report["internalCrosses"][0]["lots"])
        turnover = report["turnoverDiagnostics"]
        self.assertEqual(200, turnover["allocated_child_lot_sides"])
        self.assertEqual(120, turnover["internalized_child_lot_sides"])
        self.assertEqual(80, turnover["external_parent_turnover_lots"])
        self.assertEqual(
            {MAIN: 80},
            report["strategyBooks"][DIRECTIONAL]["positions_after"],
        )
        self.assertEqual(
            {MAIN: -60, NEXT: 60},
            report["strategyBooks"][SPREAD]["positions_after"],
        )
        self.assertEqual(
            {MAIN: 20, NEXT: 60},
            report["formalAccountProjection"]["positions_after"],
        )
        self.assertTrue(report["all_hard_gates_pass"])

    def test_asymmetric_pair_leg_limit_never_breaks_atomic_ratio(self) -> None:
        report = _allocate(
            [
                {
                    "strategy_id": DIRECTIONAL,
                    "group_id": "directional-main",
                    "priority": "risk_increase",
                    "requested_units": 70,
                    "legs": {MAIN: 1},
                },
                {
                    "strategy_id": SPREAD,
                    "group_id": "main-next-pair",
                    "priority": "risk_increase",
                    "requested_units": 60,
                    "legs": {MAIN: 1, NEXT: -1},
                },
            ],
            limits=_limits(main_trade=100, next_trade=35),
        )
        allocated = {
            item["strategy_id"]: item["allocated_units"] for item in report["groups"]
        }
        self.assertEqual(65, allocated[DIRECTIONAL])
        self.assertEqual(35, allocated[SPREAD])
        spread_group = next(
            item for item in report["groups"] if item["strategy_id"] == SPREAD
        )
        self.assertEqual({MAIN: 35, NEXT: -35}, spread_group["allocated_leg_lots"])
        self.assertTrue(spread_group["atomic_ratio_preserved"])
        self.assertEqual({MAIN: 100, NEXT: -35}, report["externalParentOrders"])
        self.assertTrue(report["all_hard_gates_pass"])

    def test_formal_position_limit_is_shared_across_both_strategies(self) -> None:
        report = _allocate(
            [
                {
                    "strategy_id": DIRECTIONAL,
                    "group_id": "directional-main",
                    "priority": "risk_increase",
                    "requested_units": 100,
                    "legs": {MAIN: 1},
                },
                {
                    "strategy_id": SPREAD,
                    "group_id": "main-next-pair",
                    "priority": "risk_increase",
                    "requested_units": 100,
                    "legs": {MAIN: 1, NEXT: -1},
                },
            ],
            books={DIRECTIONAL: {MAIN: 900}, SPREAD: {}},
            formal={MAIN: 900},
            limits=_limits(
                main_trade=500,
                next_trade=500,
                main_position=1_000,
                next_position=1_000,
            ),
        )
        allocated = {
            item["strategy_id"]: item["allocated_units"] for item in report["groups"]
        }
        self.assertEqual(50, allocated[DIRECTIONAL])
        self.assertEqual(50, allocated[SPREAD])
        self.assertEqual(
            1_000,
            report["formalAccountProjection"]["positions_after"][MAIN],
        )
        self.assertTrue(report["all_hard_gates_pass"])

    def test_small_request_is_filled_before_unused_capacity_flows_to_large_request(self) -> None:
        report = _allocate(
            [
                {
                    "strategy_id": DIRECTIONAL,
                    "group_id": "small-main",
                    "priority": "risk_increase",
                    "requested_units": 10,
                    "legs": {MAIN: 1},
                },
                {
                    "strategy_id": SPREAD,
                    "group_id": "large-pair",
                    "priority": "risk_increase",
                    "requested_units": 200,
                    "legs": {MAIN: 1, NEXT: -1},
                },
            ]
        )
        allocated = {
            item["strategy_id"]: item["allocated_units"] for item in report["groups"]
        }
        self.assertEqual(10, allocated[DIRECTIONAL])
        self.assertEqual(90, allocated[SPREAD])
        self.assertEqual({MAIN: 100, NEXT: -90}, report["externalParentOrders"])

    def test_zero_amount_authorization_blocks_new_risk_but_not_reduction(self) -> None:
        authorization = _authorization(0.0, 10_000_000.0)
        blocked = _allocate(
            [
                {
                    "strategy_id": DIRECTIONAL,
                    "group_id": "new-main-risk",
                    "priority": "risk_increase",
                    "requested_units": 20,
                    "legs": {MAIN: 1},
                },
                {
                    "strategy_id": SPREAD,
                    "group_id": "spread-risk",
                    "priority": "risk_increase",
                    "requested_units": 20,
                    "legs": {MAIN: 1, NEXT: -1},
                },
            ],
            authorization=authorization,
        )
        directional = next(
            item for item in blocked["groups"] if item["strategy_id"] == DIRECTIONAL
        )
        self.assertEqual(0, directional["allocated_units"])
        self.assertEqual(
            "zero_amount_authorization_blocks_risk_increase",
            directional["unfilled_reason"],
        )
        self.assertEqual({MAIN: 20, NEXT: -20}, blocked["externalParentOrders"])

        reduction = _allocate(
            [
                {
                    "strategy_id": DIRECTIONAL,
                    "group_id": "reduce-main",
                    "priority": "risk_reduction",
                    "requested_units": 10,
                    "legs": {MAIN: -1},
                }
            ],
            authorization=authorization,
            books={DIRECTIONAL: {MAIN: 20}, SPREAD: {}},
            formal={MAIN: 20},
        )
        self.assertEqual(-10, reduction["externalParentOrders"][MAIN])
        self.assertEqual(
            10,
            reduction["formalAccountProjection"]["positions_after"][MAIN],
        )

    def test_mandatory_order_keeps_priority_and_lower_order_can_unlock_internal_cross(self) -> None:
        report = _allocate(
            [
                {
                    "strategy_id": DIRECTIONAL,
                    "group_id": "mandatory-cover",
                    "priority": "mandatory_liquidation",
                    "requested_units": 150,
                    "legs": {MAIN: 1},
                },
                {
                    "strategy_id": SPREAD,
                    "group_id": "negative-spread-risk",
                    "priority": "risk_increase",
                    "requested_units": 50,
                    "legs": {MAIN: -1, NEXT: 1},
                },
            ],
            books={DIRECTIONAL: {MAIN: -150}, SPREAD: {}},
            formal={MAIN: -150},
            limits=_limits(main_trade=100, next_trade=100),
        )
        allocated = {
            item["strategy_id"]: item["allocated_units"] for item in report["groups"]
        }
        self.assertEqual(150, allocated[DIRECTIONAL])
        self.assertEqual(50, allocated[SPREAD])
        self.assertEqual({MAIN: 100, NEXT: 50}, report["externalParentOrders"])
        self.assertEqual(50, report["turnoverDiagnostics"]["internal_cross_lots"])
        self.assertTrue(report["all_hard_gates_pass"])

    def test_input_order_does_not_change_allocation_or_hash(self) -> None:
        groups = [
            {
                "strategy_id": SPREAD,
                "group_id": "pair",
                "priority": "risk_increase",
                "requested_units": 60,
                "legs": {NEXT: -1, MAIN: 1},
            },
            {
                "strategy_id": DIRECTIONAL,
                "group_id": "main",
                "priority": "risk_increase",
                "requested_units": 70,
                "legs": {MAIN: 1},
            },
        ]
        first = _allocate(groups, limits=_limits(main_trade=100, next_trade=35))
        reversed_groups = list(reversed(deepcopy(groups)))
        reversed_groups[1]["legs"] = dict(
            reversed(list(reversed_groups[1]["legs"].items()))
        )
        second = _allocate(
            reversed_groups,
            books={SPREAD: {}, DIRECTIONAL: {}},
            limits={
                **_limits(main_trade=100, next_trade=35),
                "contracts": dict(
                    reversed(
                        list(
                            _limits(main_trade=100, next_trade=35)["contracts"].items()
                        )
                    )
                ),
            },
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
