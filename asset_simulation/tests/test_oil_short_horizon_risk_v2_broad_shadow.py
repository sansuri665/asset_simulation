from __future__ import annotations

import unittest
from unittest.mock import patch

from asset_simulation.model.oil_short_horizon_risk_v2 import (
    build_oil_short_horizon_risk_review,
)
from asset_simulation.tests import test_oil_short_horizon_risk_broad_shadow_audit as broad


class OilShortHorizonRiskV2BroadShadowTests(unittest.TestCase):
    def test_v2_keeps_style_dominant_over_lightweight_capability(self) -> None:
        case = broad.OilShortHorizonRiskBroadShadowAuditTests(
            methodName="test_three_year_shadow_style_dominates_lightweight_capability"
        )
        with patch.object(
            broad,
            "build_oil_short_horizon_risk_review",
            new=build_oil_short_horizon_risk_review,
        ):
            case.test_three_year_shadow_style_dominates_lightweight_capability()


if __name__ == "__main__":
    unittest.main()
