from __future__ import annotations

import unittest

from asset_simulation.model.oil_multi_strategy_gate_b import (
    OIL_MULTI_STRATEGY_GATE_B_MODEL_VERSION,
    amend_strategy_capital_authorization_state,
    create_strategy_capital_authorization_state,
    evaluate_strategy_capital_authorization_status,
    load_oil_multi_strategy_gate_b_assets,
)


DIRECTIONAL = "directional"
SPREAD = "calendar_spread"


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


class OilMultiStrategyAuthorizationTests(unittest.TestCase):
    def test_registered_assets_and_amount_authorization_are_deterministic(self) -> None:
        assets = load_oil_multi_strategy_gate_b_assets()
        self.assertEqual(
            OIL_MULTI_STRATEGY_GATE_B_MODEL_VERSION,
            assets["oil_multi_strategy_gate_b_config"]["model_version"],
        )
        first = _authorization(5_000_000.0, 3_500_000.0)
        repeat = _authorization(5_000_000.0, 3_500_000.0)
        self.assertEqual(first, repeat)
        self.assertEqual(
            5_000_000.0,
            first["authorizations"][DIRECTIONAL]["authorized_capital_usd"],
        )
        self.assertEqual(
            3_500_000.0,
            first["authorizations"][SPREAD]["authorized_capital_usd"],
        )
        self.assertEqual(1_500_000.0, first["unallocated_reference_capital_usd"])
        self.assertTrue(first["governance"]["amount_is_authoritative"])
        self.assertTrue(first["governance"]["percentages_are_diagnostic_only"])
        self.assertFalse(
            first["governance"]["automatic_percentage_rebalancing_enabled"]
        )
        self.assertFalse(first["governance"]["authorization_change_mutates_positions"])

    def test_equity_change_does_not_rebalance_authorized_amounts(self) -> None:
        state = _authorization(5_000_000.0, 3_500_000.0)
        status = evaluate_strategy_capital_authorization_status(
            state, current_company_equity_usd=11_000_000.0
        )
        self.assertEqual("fully_fundable", status["status"])
        self.assertEqual(2_500_000.0, status["unallocated_company_capital_usd"])
        self.assertEqual(
            5_000_000.0,
            status["authorizations"][DIRECTIONAL][
                "effective_authorized_capital_usd"
            ],
        )
        self.assertEqual(
            3_500_000.0,
            status["authorizations"][SPREAD]["effective_authorized_capital_usd"],
        )
        self.assertFalse(status["governance"]["automatic_rescale_applied"])

    def test_equity_loss_reports_overhang_without_pro_rata_scaling(self) -> None:
        state = _authorization()
        status = evaluate_strategy_capital_authorization_status(
            state, current_company_equity_usd=7_000_000.0
        )
        self.assertEqual("authorization_overhang", status["status"])
        self.assertEqual(3_000_000.0, status["authorization_overhang_usd"])
        self.assertEqual(
            5_000_000.0,
            status["authorizations"][DIRECTIONAL][
                "effective_authorized_capital_usd"
            ],
        )
        self.assertEqual(
            5_000_000.0,
            status["authorizations"][SPREAD]["effective_authorized_capital_usd"],
        )

    def test_authorization_amendment_is_explicit_amount_decision(self) -> None:
        state = _authorization()
        amended = amend_strategy_capital_authorization_state(
            state,
            decision_id="IC-2030-02-H1",
            effective_turn="2030-02-H1",
            reference_company_equity_usd=11_000_000.0,
            strategy_authorizations_usd={
                DIRECTIONAL: 6_000_000.0,
                SPREAD: 3_000_000.0,
            },
        )
        self.assertEqual(2, amended["authorization_epoch"])
        self.assertEqual(state["identity"]["state_hash"], amended["previous_state_hash"])
        self.assertEqual(
            6_000_000.0,
            amended["authorizations"][DIRECTIONAL]["authorized_capital_usd"],
        )
        self.assertEqual(
            3_000_000.0,
            amended["authorizations"][SPREAD]["authorized_capital_usd"],
        )
        self.assertTrue(
            amended["governance"][
                "authorization_change_requires_market_execution_to_change_positions"
            ]
        )
        with self.assertRaisesRegex(ValueError, "exceed reference company equity"):
            amend_strategy_capital_authorization_state(
                amended,
                decision_id="IC-invalid",
                effective_turn="2030-03-H1",
                reference_company_equity_usd=8_000_000.0,
                strategy_authorizations_usd={
                    DIRECTIONAL: 5_000_000.0,
                    SPREAD: 5_000_000.0,
                },
            )


if __name__ == "__main__":
    unittest.main()
