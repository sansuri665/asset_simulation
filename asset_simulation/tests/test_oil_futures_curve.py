from __future__ import annotations

import math
import unittest

from asset_simulation.audit_oil_futures_curve import audit_seed_range
from asset_simulation.model.engine import run_global_macro
from asset_simulation.model import oil_futures_overlay as futures
from asset_simulation.model.registry import load_registered_assets


class OilFuturesCurveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assets = load_registered_assets()
        self.config = self.assets["oil_futures_overlay_config"]
        self.curve_config = self.config["curve"]

    @staticmethod
    def _macro(*, tightness: float, demand: float, supply: float) -> dict[str, float]:
        return {
            "global_2y_yield_pct": 3.0,
            "global_oil_volatility_regime_index": 1.0,
            "global_oil_demand_index": demand,
            "global_oil_supply_index": supply,
            "global_oil_inventory_tightness_index": tightness,
        }

    def test_physical_scarcity_reverses_carry_and_prompt_pressure(self) -> None:
        closes = [70.0] * 13
        loose = futures._curve_targets(
            self._macro(tightness=-10.0, demand=100.0, supply=102.0),
            closes,
            month=6,
            curve_config=self.curve_config,
        )
        tight = futures._curve_targets(
            self._macro(tightness=10.0, demand=102.0, supply=100.0),
            closes,
            month=6,
            curve_config=self.curve_config,
        )
        self.assertGreater(tight["convenience_yield_pct"], loose["convenience_yield_pct"])
        self.assertLess(tight["long_slope_target_pct"], loose["long_slope_target_pct"])
        self.assertLess(tight["near_pressure_target_pct"], loose["near_pressure_target_pct"])

    def test_basis_is_continuous_and_zero_at_final_settlement(self) -> None:
        factors = {
            "long_slope_pct": -2.5,
            "near_pressure_pct": -3.0,
            "curvature_pct": 0.8,
        }
        at_expiry = futures._log_basis_pct(
            0.0, factors=factors, curve_config=self.curve_config
        )
        two_weeks = futures._log_basis_pct(
            0.5 / 12.0, factors=factors, curve_config=self.curve_config
        )
        one_month = futures._log_basis_pct(
            1.0 / 12.0, factors=factors, curve_config=self.curve_config
        )
        self.assertEqual(0.0, at_expiry)
        self.assertLess(abs(two_weeks), abs(one_month))
        self.assertTrue(math.isfinite(one_month))

    def test_public_payload_exposes_registered_v8_curve_inputs(self) -> None:
        run = run_global_macro(seed=42, years=5)
        payload = futures.oil_futures_payload(
            run, as_of_year=2030, as_of_month=1, as_of_half=1
        )
        self.assertEqual("asset-simulation-oil-futures-response-v8", payload["schemaVersion"])
        self.assertEqual(
            "oil_futures_overlay_v8", payload["identity"]["field_contract_id"]
        )
        self.assertIn("convenience_yield_pct", payload["curve"]["inputs"])
        self.assertAlmostEqual(
            payload["curve"]["inputs"]["long_slope_target_pct"],
            payload["curve"]["inputs"]["funding_pct"]
            + payload["curve"]["inputs"]["storage_pct"]
            + payload["curve"]["inputs"]["term_risk_premium_pct"]
            - payload["curve"]["inputs"]["convenience_yield_pct"],
            places=7,
        )

    def test_small_cross_seed_balance_audit_passes(self) -> None:
        report = audit_seed_range(seed_start=0, seed_end=11, years=60)
        self.assertTrue(report["gates"]["passed"], report["gates"])


if __name__ == "__main__":
    unittest.main()
