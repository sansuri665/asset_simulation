from __future__ import annotations

import unittest
from pathlib import Path

from asset_simulation.server import (
    SERVICE_ID,
    VIEWER_ROOT,
    build_run_payload,
    cache_info,
    clear_cache,
    get_cached_run,
)
from asset_simulation.model.oil_shipping_world import (
    build_oil_shipping_payload,
    run_oil_shipping_world,
)
from asset_simulation.model.oil_price_projection import (
    build_oil_price_payload,
    run_oil_price_projection,
)


class ServiceApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_cache()

    def test_service_cache_and_payloads(self) -> None:
        self.assertEqual("asset-simulation-macro-oil-ui-v0.7", SERVICE_ID)
        first = get_cached_run(42, 5)
        second = get_cached_run(42, 5)
        self.assertIs(first, second)
        self.assertEqual(1, cache_info()["entries"])

        global_payload = build_run_payload(first)
        self.assertTrue(global_payload["ok"])
        self.assertNotIn("commodities", global_payload)

        shipping_payload = build_oil_shipping_payload(
            run_oil_shipping_world(first),
            as_of_year=2030,
            as_of_month=1,
        )
        self.assertTrue(shipping_payload["ok"])
        self.assertEqual("2030-01", shipping_payload["asOf"]["label"])
        self.assertFalse(shipping_payload["identity"]["freight_rate_present"])
        self.assertEqual("oil_physical_world", shipping_payload["identity"]["total_liquids_physical_balance_owner"])
        self.assertEqual("crude_physical_world", shipping_payload["identity"]["crude_physical_balance_owner"])
        self.assertEqual(14, shipping_payload["identity"]["explicit_route_count"])
        self.assertEqual(15, shipping_payload["current"]["route_count"])
        self.assertEqual(14, shipping_payload["current"]["calibrated_major_route_count"])
        self.assertEqual(25, shipping_payload["current"]["active_pair_count"])
        self.assertEqual(39.8, shipping_payload["current"]["reference_seaborne_cargo_mbd"])
        first_route = shipping_payload["current"]["routes"][0]
        self.assertIn("reference_cargo_mbd", first_route)
        self.assertIn("margin_scaled_reference_mbd", first_route)
        self.assertIn("cargo_vs_reference_pct", first_route)
        self.assertEqual(10, len(shipping_payload["current"]["regional_balances"]))
        first_region = shipping_payload["current"]["regional_balances"][0]
        self.assertIn("base_crude_production_mbd", first_region)
        self.assertIn("effective_production_adjustment_mbd", first_region)
        self.assertIn("base_crude_refinery_runs_mbd", first_region)
        self.assertIn("effective_refinery_adjustment_mbd", first_region)
        self.assertNotIn("scenario", shipping_payload)

        oil_payload = build_oil_price_payload(run_oil_price_projection(first))
        self.assertEqual(6, len(oil_payload["annual"]))
        self.assertEqual(72, len(oil_payload["monthly"]))
        self.assertEqual("2025-01", oil_payload["monthly"][0]["label"])

    def test_viewer_assets_expose_the_macro_to_shipping_chain(self) -> None:
        index = (VIEWER_ROOT / "index.html").read_text(encoding="utf-8")
        overview = (VIEWER_ROOT / "overview.html").read_text(encoding="utf-8")
        physical = (VIEWER_ROOT / "physical.html").read_text(encoding="utf-8")
        app = (VIEWER_ROOT / "js" / "app.js").read_text(encoding="utf-8")
        overview_app = (VIEWER_ROOT / "js" / "overview.js").read_text(encoding="utf-8")
        physical_app = (VIEWER_ROOT / "js" / "physical.js").read_text(encoding="utf-8")
        self.assertNotIn("原油物理平衡</h3>", index)
        self.assertIn("全球总览", index)
        self.assertIn("区域平衡", index)
        self.assertIn("航线状况参考", index)
        self.assertIn("routeBoard", index)
        self.assertIn("/api/oil-shipping", app)
        self.assertIn("tonneMiles", app)
        self.assertIn("regional_balances", app)
        self.assertIn("regional_export_supply_mbd", app)
        self.assertIn("is_other_pool", app)
        self.assertNotIn("seaborne_share", app)
        self.assertNotIn("production_outage_mbd", app)
        self.assertNotIn("regional_production_impulse_mbd", app)
        self.assertNotIn("route_haul_impulse_pct", app)
        self.assertIn("全球宏观", overview)
        self.assertIn("原油价格", overview)
        self.assertIn("/physical", overview)
        self.assertIn("mainModeNav", overview)
        self.assertNotIn("prevYear", overview)
        self.assertNotIn("jumpYear", overview)
        self.assertIn("/api/global", overview_app)
        self.assertIn("/api/oil-price", overview_app)
        self.assertIn('addEventListener("pointermove"', overview_app)
        self.assertIn('addEventListener("pointerdown"', overview_app)
        self.assertIn("monthlyWindowStart", overview_app)
        self.assertIn("全球总液体物理平衡", physical)
        self.assertIn("不等于供炼厂加工并进入原油轮贸易", physical)
        self.assertIn("data-mode=\"balance\"", physical)
        self.assertIn("data-mode=\"inventory\"", physical)
        self.assertIn("data-mode=\"capacity\"", physical)
        self.assertIn("/api/oil-shipping", physical_app)
        self.assertIn("target_inventory_days", physical_app)
        self.assertIn("long_run_demand_regime", physical_app)
        self.assertIn("正在生成月度总液体物理世界", physical_app)
        self.assertIn('addEventListener("pointermove"', physical_app)
        self.assertIn('addEventListener("pointerdown"', physical_app)
        self.assertIn("MONTHLY_WINDOW = 72", physical_app)
        self.assertTrue(Path(VIEWER_ROOT / "css" / "viewer.css").is_file())


if __name__ == "__main__":
    unittest.main()
