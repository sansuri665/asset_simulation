from __future__ import annotations

import math
import unittest

from asset_simulation.audit_oil_futures_curve import audit_seed_range
from asset_simulation.model import oil_futures_overlay as futures
from asset_simulation.model.oil_futures_world import get_oil_futures_world
from asset_simulation.model.registry import load_registered_assets
from asset_simulation.tests.support import cached_global_run


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
        self.assertGreater(
            tight["convenience_yield_pct"],
            loose["convenience_yield_pct"],
        )
        self.assertLess(
            tight["long_slope_target_pct"],
            loose["long_slope_target_pct"],
        )
        self.assertLess(
            tight["near_pressure_target_pct"],
            loose["near_pressure_target_pct"],
        )

    def test_basis_is_continuous_and_zero_at_final_settlement(self) -> None:
        factors = {
            "long_slope_pct": -2.5,
            "near_pressure_pct": -3.0,
            "curvature_pct": 0.8,
        }
        at_expiry = futures._log_basis_pct(
            0.0,
            factors=factors,
            curve_config=self.curve_config,
        )
        two_weeks = futures._log_basis_pct(
            0.5 / 12.0,
            factors=factors,
            curve_config=self.curve_config,
        )
        one_month = futures._log_basis_pct(
            1.0 / 12.0,
            factors=factors,
            curve_config=self.curve_config,
        )
        self.assertEqual(0.0, at_expiry)
        self.assertLess(abs(two_weeks), abs(one_month))
        self.assertTrue(math.isfinite(one_month))

    def test_public_payload_exposes_registered_v8_curve_inputs(self) -> None:
        run = cached_global_run(seed=42, years=5)
        payload = futures.oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        self.assertEqual(
            "asset-simulation-oil-futures-response-v8",
            payload["schemaVersion"],
        )
        self.assertEqual(
            "oil_futures_overlay_v8",
            payload["identity"]["field_contract_id"],
        )
        self.assertIn(
            "convenience_yield_pct",
            payload["curve"]["inputs"],
        )
        self.assertAlmostEqual(
            payload["curve"]["inputs"]["long_slope_target_pct"],
            payload["curve"]["inputs"]["funding_pct"]
            + payload["curve"]["inputs"]["storage_pct"]
            + payload["curve"]["inputs"]["term_risk_premium_pct"]
            - payload["curve"]["inputs"]["convenience_yield_pct"],
            places=7,
        )

    def test_incremental_world_matches_retained_rebuild_at_key_cutoffs(self) -> None:
        run = cached_global_run(42, 12)
        for year, month, half in (
            (2030, 1, 1),
            (2030, 1, 2),
            (2030, 5, 1),
            (2030, 5, 2),
            (2031, 12, 2),
        ):
            with self.subTest(cutoff=(year, month, half)):
                expected = futures._rebuild_oil_futures_payload(
                    run,
                    as_of_year=year,
                    as_of_month=month,
                    as_of_half=half,
                )
                actual = futures.oil_futures_payload(
                    run,
                    as_of_year=year,
                    as_of_month=month,
                    as_of_half=half,
                )
                self.assertEqual(expected, actual)

    def test_future_extension_does_not_change_an_earlier_public_view(self) -> None:
        run = cached_global_run(42, 12)
        world = get_oil_futures_world(run)
        early = futures.oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )

        world.ensure(year=2035, month=12, half=2)
        futures.oil_futures_payload.cache_clear()
        rebuilt_early = futures.oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )

        self.assertEqual(early, rebuilt_early)

    def test_indexed_named_contract_history_matches_retained_rebuild(self) -> None:
        run = cached_global_run(42, 12)
        monthly = get_oil_futures_world(run).contract_monthly_history(
            contract_id="OIL-3005",
            as_of_year=2030,
            as_of_month=5,
            as_of_half=2,
        )
        legacy = futures._rebuild_oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=5,
            as_of_half=2,
        )
        legacy_contract = next(
            item
            for item in legacy["curve"]["contracts"]
            if item["contract_id"] == "OIL-3005"
        )

        self.assertEqual(legacy_contract["monthly"], monthly)

    def test_small_cross_seed_balance_audit_passes(self) -> None:
        report = audit_seed_range(seed_start=0, seed_end=11, years=60)
        self.assertTrue(report["gates"]["passed"], report["gates"])


if __name__ == "__main__":
    unittest.main()
