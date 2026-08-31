from __future__ import annotations

import unittest

from asset_simulation.audit_oil_calendar_spread_style_economics import (
    _controlled_decision,
    _controlled_radar,
    _runtime_bundle,
)
from asset_simulation.model.oil_calendar_spread_strategy import (
    _apply_spread_position_persistence,
)
from asset_simulation.tests.test_oil_calendar_spread_strategy import _forecast, _market


class OilCalendarSpreadStyleEconomicTests(unittest.TestCase):
    def test_controlled_radar_moves_only_one_dedicated_axis(self) -> None:
        radar = _controlled_radar("forecast_vs_visible_curve", 90.0)
        self.assertEqual(90.0, radar["forecast_vs_visible_curve"])
        self.assertTrue(
            all(
                value == 50.0
                for key, value in radar.items()
                if key != "forecast_vs_visible_curve"
            )
        )

    def test_forecast_vs_visible_curve_changes_only_registered_component_mix(self) -> None:
        _, _, low = _runtime_bundle(_controlled_radar("forecast_vs_visible_curve", 10.0))
        _, _, high = _runtime_bundle(_controlled_radar("forecast_vs_visible_curve", 90.0))
        self.assertLess(
            low["signal"]["forecast_component_weight"],
            high["signal"]["forecast_component_weight"],
        )
        self.assertGreater(
            low["signal"]["visible_curve_component_weight"],
            high["signal"]["visible_curve_component_weight"],
        )
        self.assertEqual(
            low["signal"]["forecast_component_weight"]
            + low["signal"]["visible_curve_component_weight"],
            1.0,
        )

    def test_curve_orientation_changes_visible_curve_expression(self) -> None:
        history = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1]
        market = _market(
            current_main=72.0,
            current_next=70.0,
            history_spreads=history,
        )
        forecast = _forecast(
            current_main=72.0,
            current_next=70.0,
            future_main=(72.0, 72.0),
            future_next=(70.0, 70.0),
        )
        low = _controlled_decision(
            market,
            forecast,
            current_spread_units=0,
            dedicated_radar=_controlled_radar("curve_continuation_reversion", 10.0),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        high = _controlled_decision(
            market,
            forecast,
            current_spread_units=0,
            dedicated_radar=_controlled_radar("curve_continuation_reversion", 90.0),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        self.assertGreater(
            high["signal"]["continuation_weight"],
            low["signal"]["continuation_weight"],
        )
        self.assertGreater(
            high["signal"]["visible_curve_signal"],
            low["signal"]["visible_curve_signal"],
        )

    def test_selectivity_deployment_tempo_and_rebalance_have_expected_semantics(self) -> None:
        market = _market()
        forecast = _forecast(future_main=(85.0, 90.0), future_next=(65.0, 62.0))

        selective_low = _controlled_decision(
            market,
            forecast,
            current_spread_units=0,
            dedicated_radar=_controlled_radar("dislocation_selectivity", 10.0),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        selective_high = _controlled_decision(
            market,
            forecast,
            current_spread_units=0,
            dedicated_radar=_controlled_radar("dislocation_selectivity", 90.0),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        self.assertLess(
            selective_low["signal"]["signal_deadband_abs"],
            selective_high["signal"]["signal_deadband_abs"],
        )

        deployment_low = _controlled_decision(
            market,
            forecast,
            current_spread_units=0,
            dedicated_radar=_controlled_radar("capital_deployment", 10.0),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        deployment_high = _controlled_decision(
            market,
            forecast,
            current_spread_units=0,
            dedicated_radar=_controlled_radar("capital_deployment", 90.0),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        self.assertGreaterEqual(
            deployment_high["capacity"]["risk_capacity_units"],
            deployment_low["capacity"]["risk_capacity_units"],
        )

        tempo_low = _controlled_decision(
            market,
            forecast,
            current_spread_units=0,
            dedicated_radar=_controlled_radar("adjustment_tempo", 10.0),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        tempo_high = _controlled_decision(
            market,
            forecast,
            current_spread_units=0,
            dedicated_radar=_controlled_radar("adjustment_tempo", 90.0),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        self.assertGreater(
            tempo_high["policy"]["execution"]["adjustment_speed"],
            tempo_low["policy"]["execution"]["adjustment_speed"],
        )
        self.assertGreaterEqual(
            abs(tempo_high["target_spread_units"]),
            abs(tempo_low["target_spread_units"]),
        )

        rebalance_low = _controlled_decision(
            market,
            forecast,
            current_spread_units=100,
            dedicated_radar=_controlled_radar("rebalance_activity", 10.0),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        rebalance_high = _controlled_decision(
            market,
            forecast,
            current_spread_units=100,
            dedicated_radar=_controlled_radar("rebalance_activity", 90.0),
            authorized_strategy_capital_usd=10_000_000.0,
        )
        self.assertGreater(
            rebalance_high["policy"]["execution"]["gross_turnover_multiplier"],
            rebalance_low["policy"]["execution"]["gross_turnover_multiplier"],
        )
        self.assertGreaterEqual(
            rebalance_high["paired_execution_mandate"]["advisory_pair_turnover_budget_units"],
            rebalance_low["paired_execution_mandate"]["advisory_pair_turnover_budget_units"],
        )

    def test_patience_and_horizon_have_expected_semantics(self) -> None:
        _, _, patience_low = _runtime_bundle(_controlled_radar("holding_patience", 10.0))
        _, _, patience_high = _runtime_bundle(_controlled_radar("holding_patience", 90.0))
        low_persistent = _apply_spread_position_persistence(
            current_spread_units=100,
            proposed_target_units=40,
            capacity_units=500,
            position_persistence=patience_low["execution"]["position_persistence"],
        )
        high_persistent = _apply_spread_position_persistence(
            current_spread_units=100,
            proposed_target_units=40,
            capacity_units=500,
            position_persistence=patience_high["execution"]["position_persistence"],
        )
        self.assertGreater(abs(high_persistent), abs(low_persistent))

        _, _, horizon_low = _runtime_bundle(_controlled_radar("forecast_horizon", 10.0))
        _, _, horizon_high = _runtime_bundle(_controlled_radar("forecast_horizon", 90.0))
        self.assertGreater(
            horizon_high["signal"]["horizon_weights"][1],
            horizon_low["signal"]["horizon_weights"][1],
        )


if __name__ == "__main__":
    unittest.main()
