from __future__ import annotations

import unittest

from asset_simulation.audit_oil_formal_account_calibration import (
    build_oil_formal_account_calibration_report,
)


class OilFormalAccountCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = build_oil_formal_account_calibration_report(
            seeds=(42,),
            horizon_years=1,
            styles=("balanced",),
            authorizations=(35.0, 85.0),
        )

    def test_hard_account_gates_pass(self) -> None:
        self.assertTrue(self.report["hard_gate_pass"])
        self.assertTrue(all(self.report["hardGates"].values()))
        self.assertTrue(self.report["ok"])

    def test_distribution_and_capacity_are_reproducible(self) -> None:
        self.assertEqual(2, self.report["scope"]["scenario_count"])
        self.assertEqual(2, len(self.report["scenarios"]))
        self.assertEqual(1, len(self.report["capacityComparisons"]))
        self.assertIn("annualized_volatility_pct", self.report["distributions"])
        self.assertEqual(
            self.report,
            build_oil_formal_account_calibration_report(
                seeds=(42,),
                horizon_years=1,
                styles=("balanced",),
                authorizations=(35.0, 85.0),
            ),
        )


if __name__ == "__main__":
    unittest.main()
