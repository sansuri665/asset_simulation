from __future__ import annotations

import json
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.impulses import IMPULSE_FIELDS
from asset_simulation.model.registry import PACKAGE_ROOT, load_registered_assets


CONTRACT_PATH = PACKAGE_ROOT / "contracts" / "capital_market_minimum_v1.json"


class CapitalMarketContractTests(unittest.TestCase):
    def test_reserved_contract_is_not_registered_and_does_not_change_macro_ports(self) -> None:
        payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        assets = load_registered_assets()
        self.assertEqual("capital_market_minimum_v1", payload["contract_id"])
        self.assertEqual("reserved_not_registered", payload["status"])
        self.assertIsNone(payload["runtime_owner"])
        self.assertNotIn("capital_market_contract", assets)
        self.assertNotIn("capital_market_minimum_v1", json.dumps(assets, default=str))

        market_ports = set(payload["market_event_ports"])
        macro_ports = set(IMPULSE_FIELDS)
        self.assertTrue(market_ports)
        self.assertTrue(market_ports.isdisjoint(macro_ports))
        self.assertEqual(set(payload["macro_event_ports"]["fields"]), macro_ports)
        self.assertEqual("opt_in_only_not_ordinary_seed", payload["macro_event_ports"]["reuse_policy"])
        for spec in payload["market_event_ports"].values():
            self.assertEqual(0.0, spec["default"])

        self.assertIn("issuance_calendar_index", payload["primary_fields"])
        self.assertIn("secondary_liquidity_index", payload["secondary_fields"])
        self.assertIn("same_year_write_back_to_real_economy_or_inflation", payload["forbidden"])

        run = run_global_macro(seed=42, years=8, diagnostics_level="full")
        for row in run.diagnostics:
            impulse = row["exogenous_impulses"]
            self.assertTrue(all(impulse[field] == 0.0 for field in IMPULSE_FIELDS))
            self.assertTrue(all(port not in impulse for port in market_ports))


if __name__ == "__main__":
    unittest.main()
