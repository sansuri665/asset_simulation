from __future__ import annotations

import math
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_short_term_forecast import (
    generate_oil_short_term_forecast,
)
from asset_simulation.model.oil_strategy_thesis import (
    apply_oil_strategy_thesis_invalidation,
    evaluate_oil_strategy_thesis_state,
)
from asset_simulation.model.oil_trading_strategy import (
    _assign_roll_transfer_attribution,
    build_oil_strategy_decision,
    settle_oil_strategy_turn,
)
from asset_simulation.model.registry import load_registered_assets


class OilStrategyThesisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_registered_assets()["oil_trading_strategy_config"][
            "thesis_invalidation"
        ]

    def test_invalidated_thesis_cannot_increase_same_direction(self) -> None:
        state = {
            "contracts": {
                "OIL-3005": {
                    "status": "invalidated",
                    "consecutive_band_breaches": 2,
                    "consecutive_direction_misses": 0,
                    "last_signal": 0.7,
                }
            }
        }
        target, report = apply_oil_strategy_thesis_invalidation(
            contract_id="OIL-3005",
            current_position_lots=10_000,
            proposed_target_lots=40_000,
            signal=0.8,
            state=state,
            policy=self.policy,
        )
        self.assertEqual(10_000, target)
        self.assertEqual("invalidated_no_same_direction_increase", report["action"])

    def test_material_reversal_exits_before_crossing_zero(self) -> None:
        state = {
            "contracts": {
                "OIL-3005": {
                    "status": "active",
                    "last_signal": 0.7,
                }
            }
        }
        target, report = apply_oil_strategy_thesis_invalidation(
            contract_id="OIL-3005",
            current_position_lots=10_000,
            proposed_target_lots=-30_000,
            signal=-0.8,
            state=state,
            policy=self.policy,
        )
        self.assertEqual(0, target)
        self.assertEqual("exit_before_direction_reversal", report["action"])

    def test_severe_realized_band_breach_invalidates_without_ability_score(self) -> None:
        decision = {
            "thesisInvalidation": {"policy": self.policy, "stateBefore": {}},
            "targets": [
                {
                    "contract_id": "OIL-3005",
                    "role": "main",
                    "anchor_price_usd": 100.0,
                    "signal": 0.8,
                    "horizon_components": [
                        {
                            "selected_horizon_weeks": 2,
                            "target_week": "2030-01-W4",
                            "forecast_close_usd": 101.0,
                            "confidence_low_usd": 99.0,
                            "confidence_high_usd": 102.0,
                            "uncertainty_log": 0.01,
                        }
                    ],
                }
            ],
        }
        end_market = {
            "curve": {
                "contracts": [
                    {"contract_id": "OIL-3005", "price_usd": 110.0}
                ]
            }
        }
        outcome = evaluate_oil_strategy_thesis_state(decision, end_market)
        self.assertEqual(
            "invalidated", outcome["state"]["contracts"]["OIL-3005"]["status"]
        )
        self.assertFalse(
            outcome["informationPolicy"]["configured_research_ability_used"]
        )

    def test_small_band_exit_is_observed_without_poisoning_the_thesis(self) -> None:
        decision = {
            "thesisInvalidation": {"policy": self.policy, "stateBefore": {}},
            "targets": [
                {
                    "contract_id": "OIL-3005",
                    "role": "main",
                    "anchor_price_usd": 100.0,
                    "signal": 0.5,
                    "horizon_components": [
                        {
                            "selected_horizon_weeks": 2,
                            "target_week": "2030-01-W4",
                            "forecast_close_usd": 101.0,
                            "confidence_low_usd": 99.0,
                            "confidence_high_usd": 102.0,
                            "uncertainty_log": 0.01,
                        }
                    ],
                }
            ],
        }
        end_market = {
            "curve": {
                "contracts": [
                    {"contract_id": "OIL-3005", "price_usd": 102.50}
                ]
            }
        }
        outcome = evaluate_oil_strategy_thesis_state(decision, end_market)
        evaluation = outcome["evaluations"][0]
        self.assertTrue(evaluation["band_breach"])
        self.assertFalse(evaluation["material_band_breach"])
        self.assertEqual(
            "active", outcome["state"]["contracts"]["OIL-3005"]["status"]
        )

    def test_turn_pnl_direction_selection_and_cost_attribution_reconciles(self) -> None:
        run = run_global_macro(42, 7)
        start = oil_futures_payload(
            run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        end = oil_futures_payload(
            run, as_of_year=2030, as_of_month=1, as_of_half=2
        )
        vintage = generate_oil_short_term_forecast(
            run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        decision = build_oil_strategy_decision(start, vintage)
        report = settle_oil_strategy_turn(
            start, end, decision, equity_usd=3_000_000_000.0
        )
        summary = report["executionSummary"]
        self.assertAlmostEqual(
            summary["carry_gross_pnl_usd"],
            summary["direction_carry_gross_pnl_usd"]
            + summary["contract_selection_gross_pnl_usd"],
            places=4,
        )
        self.assertAlmostEqual(
            summary["net_adjustment_gross_pnl_usd"],
            summary["roll_execution_gross_pnl_usd"]
            + summary["directional_rebalance_gross_pnl_usd"],
            places=4,
        )
        self.assertTrue(
            math.isclose(
                report["pnlAttribution"]["net_pnl_after_cost_usd"],
                report["accountAfter"]["turn_pnl_usd"],
                abs_tol=1e-4,
            )
        )

    def test_roll_matching_includes_cash_settled_old_leg(self) -> None:
        old = {
            "role": "legacy_exit",
            "starting_position_lots": -3_000,
            "ending_position_lots": 0,
            "executed_delta_lots": 0,
            "settlement_delta_lots": 3_000,
            "net_adjustment_gross_pnl_usd": 0.0,
            "net_adjustment_execution_cost_usd": 0.0,
            "cash_settlement_fee_usd": 1_500.0,
        }
        new = {
            "role": "main",
            "starting_position_lots": -2_000,
            "ending_position_lots": -6_000,
            "executed_delta_lots": -4_000,
            "settlement_delta_lots": 0,
            "net_adjustment_gross_pnl_usd": 80_000.0,
            "net_adjustment_execution_cost_usd": 20_000.0,
            "cash_settlement_fee_usd": 0.0,
        }
        reports = [old, new]
        self.assertEqual(3_000, _assign_roll_transfer_attribution(reports))
        self.assertEqual(3_000, old["roll_transfer_lots"])
        self.assertEqual(3_000, new["roll_transfer_lots"])
        self.assertEqual(1_500.0, old["roll_cash_settlement_fee_usd"])
        self.assertAlmostEqual(60_000.0, new["roll_execution_gross_pnl_usd"])
        self.assertAlmostEqual(15_000.0, new["roll_execution_cost_usd"])


if __name__ == "__main__":
    unittest.main()
