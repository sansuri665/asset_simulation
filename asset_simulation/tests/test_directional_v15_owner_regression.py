from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.oil_strategy_research import (
    build_default_oil_strategy_research_profile,
    resolve_oil_strategy_runtime_policy,
)


class DirectionalV15OwnerRegressionTests(unittest.TestCase):
    def test_capital_deployment_is_style_score_not_capital_haircut(self) -> None:
        base = build_default_oil_strategy_research_profile()
        low = deepcopy(base)
        high = deepcopy(base)
        low.pop("profile_hash", None)
        high.pop("profile_hash", None)
        low["style_radar"]["capital_deployment"] = 0.0
        high["style_radar"]["capital_deployment"] = 100.0

        _, low_policy = resolve_oil_strategy_runtime_policy(low)
        _, high_policy = resolve_oil_strategy_runtime_policy(high)
        self.assertEqual(0.0, low_policy["risk"]["capital_deployment_score"])
        self.assertEqual(100.0, high_policy["risk"]["capital_deployment_score"])
        self.assertEqual(
            "risk_preference_input_only",
            low_policy["risk"]["capital_deployment_semantics"],
        )
        self.assertNotIn(
            "capital_deployment_pct_of_allocated_equity", low_policy["risk"]
        )
        self.assertNotIn(
            "capital_deployment_pct_of_allocated_equity", high_policy["risk"]
        )


if __name__ == "__main__":
    unittest.main()
