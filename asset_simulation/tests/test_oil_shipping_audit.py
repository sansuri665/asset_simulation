from __future__ import annotations

import unittest

from asset_simulation.audit_oil_shipping_demand import audit_oil_shipping_demand


class OilShippingDemandAuditTests(unittest.TestCase):
    def test_lightweight_audit_passes(self) -> None:
        result = audit_oil_shipping_demand(range(4), years=5)
        self.assertTrue(result["ok"])
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(4 * 6 * 12, result["turn_count"])


if __name__ == "__main__":
    unittest.main()
