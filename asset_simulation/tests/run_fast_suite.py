"""Run the ordinary unit suite without minute-scale economic research replays."""

from __future__ import annotations

from pathlib import Path
import unittest


HEAVY_MODULES = {
    "test_oil_short_horizon_risk_shadow_audit",
    "test_oil_short_horizon_risk_broad_shadow_audit",
    "test_oil_short_horizon_risk_economic_counterfactual",
    "test_oil_short_horizon_risk_appetite_frontier",
    "test_oil_short_horizon_risk_boundary_counterfactual",
    "test_oil_short_horizon_risk_v2_economic_acceptance",
    "test_oil_short_horizon_risk_v2_broad_shadow",
    "test_oil_trading_strategy_risk_runtime_equivalence",
}


def _iter_tests(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


def main() -> int:
    test_dir = Path(__file__).resolve().parent
    repo_root = test_dir.parents[1]
    loader = unittest.TestLoader()
    discovered = loader.discover(
        start_dir=str(test_dir),
        pattern="test_*.py",
        top_level_dir=str(repo_root),
    )
    fast = unittest.TestSuite()
    included = 0
    excluded = 0
    for test in _iter_tests(discovered):
        module_name = test.__class__.__module__.rsplit(".", 1)[-1]
        if module_name in HEAVY_MODULES:
            excluded += 1
            continue
        fast.addTest(test)
        included += 1
    print(
        "FAST_UNIT_SUITE "
        f"included_tests={included} excluded_heavy_tests={excluded} "
        f"heavy_modules={','.join(sorted(HEAVY_MODULES))}"
    )
    result = unittest.TextTestRunner(verbosity=2).run(fast)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
