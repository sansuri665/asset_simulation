from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.oil_multi_strategy_pretrade_allocator import (
    OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_MODEL_VERSION,
    allocate_oil_dual_strategy_pretrade,
)


DIRECTIONAL_ID = "oil.short.directional.test"
SPREAD_ID = "oil.short.relative_value.calendar_spread.v1"


def _market(
    *,
    main_position_limit: int = 100,
    main_turn_limit: int = 100,
    next_position_limit: int = 100,
    next_turn_limit: int = 100,
    gross_cap: int = 500,
) -> dict[str, object]:
    def contract(contract_id: str, position_limit: int, turn_limit: int) -> dict[str, object]:
        return {
            "contract_id": contract_id,
            "participantLimits": {
                "single_contract_position_limit_lots": position_limit,
                "turn_trade_limit_lots": turn_limit,
                "new_trades_allowed": True,
                "binding_position_rule": "test",
            },
        }

    return {
        "ok": True,
        "identity": {"result_hash": "TEST-MARKET"},
        "curve": {
            "main_contract_id": "OIL-3005",
            "contracts": [
                contract("OIL-3005", main_position_limit, main_turn_limit),
                contract("OIL-3009", next_position_limit, next_turn_limit),
                contract("OIL-3101", 100, 100),
            ],
        },
        "participantLimitsPolicy": {
            "all_contract_gross_position_cap_lots": gross_cap,
        },
    }


def _directional(delta: int, *, mandatory: int = 0) -> dict[str, object]:
    return {
        "strategy_id": DIRECTIONAL_ID,
        "contract_id": "OIL-3005",
        "requested_delta_lots": delta,
        "mandatory_delta_lots": mandatory,
    }


def _spread(units: int, *, remediation_main: int = 0, remediation_next: int = 0) -> dict[str, object]:
    return {
        "strategy_id": SPREAD_ID,
        "main_contract_id": "OIL-3005",
        "next_main_contract_id": "OIL-3009",
        "requested_pair_delta_units": units,
        "remediation_main_delta_lots": remediation_main,
        "remediation_next_main_delta_lots": remediation_next,
    }


class OilMultiStrategyPretradeAllocatorTests(unittest.TestCase):
    def test_equal_split_when_shared_position_headroom_binds(self) -> None:
        market = _market(main_position_limit=10, main_turn_limit=50)
        report = allocate_oil_dual_strategy_pretrade(
            market,
            account_positions={"OIL-3005": 6},
            directional_request=_directional(20),
            calendar_spread_request=_spread(20),
        )
        self.assertEqual(
            OIL_MULTI_STRATEGY_PRETRADE_ALLOCATOR_MODEL_VERSION,
            report["identity"]["model_version"],
        )
        self.assertEqual(2, report["ordinaryAllocation"]["directional_allocated_lots"])
        self.assertEqual(2, report["ordinaryAllocation"]["calendar_spread_allocated_units"])
        self.assertEqual(
            10,
            report["hardLimitChecks"]["final_account_positions"]["OIL-3005"],
        )
        self.assertTrue(report["hardLimitChecks"]["all_hard_limits_ok"])

    def test_equal_split_when_external_turn_limit_binds(self) -> None:
        report = allocate_oil_dual_strategy_pretrade(
            _market(main_position_limit=100, main_turn_limit=6),
            account_positions={},
            directional_request=_directional(20),
            calendar_spread_request=_spread(20),
        )
        self.assertEqual(3, report["ordinaryAllocation"]["directional_allocated_lots"])
        self.assertEqual(3, report["ordinaryAllocation"]["calendar_spread_allocated_units"])
        main_flow = report["internalNettingPreview"]["by_contract"]["OIL-3005"]
        self.assertEqual(6, main_flow["external_gross_turnover_lots"])

    def test_spread_second_leg_bottleneck_releases_unused_main_capacity(self) -> None:
        report = allocate_oil_dual_strategy_pretrade(
            _market(
                main_position_limit=20,
                main_turn_limit=20,
                next_position_limit=3,
                next_turn_limit=20,
            ),
            account_positions={},
            directional_request=_directional(20),
            calendar_spread_request=_spread(20),
        )
        self.assertEqual(3, report["ordinaryAllocation"]["calendar_spread_allocated_units"])
        self.assertEqual(17, report["ordinaryAllocation"]["directional_allocated_lots"])
        self.assertTrue(report["ordinaryAllocation"]["unused_entitlement_reallocated"])
        self.assertTrue(
            report["strategyAllocatedDeltas"]["calendar_spread"][
                "ordinary_pair_balance_ok"
            ]
        )
        self.assertEqual(
            -3,
            report["strategyAllocatedDeltas"]["calendar_spread"]["deltas"][
                "OIL-3009"
            ],
        )

    def test_opposing_strategy_flows_net_before_external_turn_capacity(self) -> None:
        report = allocate_oil_dual_strategy_pretrade(
            _market(
                main_position_limit=10,
                main_turn_limit=2,
                next_position_limit=10,
                next_turn_limit=10,
            ),
            account_positions={},
            directional_request=_directional(6),
            calendar_spread_request=_spread(-6),
        )
        self.assertEqual(6, report["ordinaryAllocation"]["directional_allocated_lots"])
        self.assertEqual(-6, report["ordinaryAllocation"]["calendar_spread_allocated_units"])
        main_flow = report["internalNettingPreview"]["by_contract"]["OIL-3005"]
        self.assertEqual(6, main_flow["internal_cross_lots"])
        self.assertEqual(0, main_flow["external_delta_lots"])
        self.assertEqual(12, main_flow["market_turnover_saved_lots"])
        self.assertEqual(
            {"OIL-3009": 6},
            report["internalNettingPreview"]["external_market_orders"],
        )

    def test_mandatory_reduction_has_priority_and_can_be_internally_offset(self) -> None:
        report = allocate_oil_dual_strategy_pretrade(
            _market(
                main_position_limit=10,
                main_turn_limit=5,
                next_position_limit=10,
                next_turn_limit=10,
            ),
            account_positions={"OIL-3005": 5},
            directional_request=_directional(0, mandatory=-4),
            calendar_spread_request=_spread(8),
        )
        self.assertEqual(-4, report["requests"]["directional"]["mandatory_delta_lots"])
        self.assertEqual(8, report["ordinaryAllocation"]["calendar_spread_allocated_units"])
        main_flow = report["internalNettingPreview"]["by_contract"]["OIL-3005"]
        self.assertEqual(4, main_flow["internal_cross_lots"])
        self.assertEqual(4, main_flow["external_delta_lots"])
        self.assertEqual(
            9,
            report["hardLimitChecks"]["final_account_positions"]["OIL-3005"],
        )

    def test_inputs_are_not_mutated_and_result_is_deterministic(self) -> None:
        market = _market(main_position_limit=10, main_turn_limit=6)
        positions = {"OIL-3005": 1}
        directional = _directional(20)
        spread = _spread(20)
        snapshots = tuple(deepcopy(value) for value in (market, positions, directional, spread))
        first = allocate_oil_dual_strategy_pretrade(
            market,
            account_positions=positions,
            directional_request=directional,
            calendar_spread_request=spread,
        )
        second = allocate_oil_dual_strategy_pretrade(
            market,
            account_positions=positions,
            directional_request=directional,
            calendar_spread_request=spread,
        )
        self.assertEqual(first, second)
        self.assertEqual(snapshots[0], market)
        self.assertEqual(snapshots[1], positions)
        self.assertEqual(snapshots[2], directional)
        self.assertEqual(snapshots[3], spread)
        self.assertFalse(first["governance"]["fills_created"])
        self.assertFalse(first["governance"]["formal_account_mutated"])


if __name__ == "__main__":
    unittest.main()
