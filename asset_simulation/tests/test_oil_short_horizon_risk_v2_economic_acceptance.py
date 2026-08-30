from __future__ import annotations

import json
import statistics
import unittest
from unittest.mock import patch

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_short_horizon_risk_v2 import (
    build_oil_short_horizon_risk_review,
)
from asset_simulation.model.oil_trading_strategy import simulate_oil_trading_strategy
from asset_simulation.tests import (
    test_oil_short_horizon_risk_economic_counterfactual as economic,
)


class OilShortHorizonRiskV2EconomicAcceptanceTests(unittest.TestCase):
    def test_v2_preserves_frozen_economics_without_flattening_capital_authorization(self) -> None:
        report: dict[str, object] = {"allocations": {}}
        margin_by_allocation: list[float] = []

        for allocation_pct in economic.ALLOCATIONS:
            rows: list[dict[str, float | int | dict]] = []
            for seed in economic.SEEDS:
                global_run = run_global_macro(seed, 10)
                legacy = economic._legacy_metrics(
                    simulate_oil_trading_strategy(
                        global_run,
                        start_year=economic.START[0],
                        start_month=economic.START[1],
                        start_half=economic.START[2],
                        end_year=economic.END[0],
                        end_month=economic.END[1],
                        end_half=economic.END[2],
                        capital_authorization_pct_of_company_equity=allocation_pct,
                    )
                )
                with patch.object(
                    economic,
                    "build_oil_short_horizon_risk_review",
                    new=build_oil_short_horizon_risk_review,
                ):
                    candidate = economic._simulate_v2_counterfactual(
                        global_run, allocation_pct
                    )
                company_bindings = {
                    key: value
                    for key, value in candidate["binding_counts"].items()
                    if key in {"company_materiality", "company_margin_materiality"}
                }
                self.assertEqual({}, company_bindings)
                rows.append(
                    {
                        "seed": seed,
                        "cagr_retention": candidate["cagr_pct"] / legacy["cagr_pct"],
                        "drawdown_ratio": candidate["maximum_drawdown_pct"]
                        / legacy["maximum_drawdown_pct"],
                        "risk_adjusted_ratio": candidate["return_to_drawdown"]
                        / legacy["return_to_drawdown"],
                        "turnover_ratio": candidate["total_traded_lots"]
                        / legacy["total_traded_lots"],
                        "maximum_margin_to_equity_pct": candidate[
                            "maximum_margin_to_equity_pct"
                        ],
                        "approval_ratio_mean": candidate["approval_ratio"]["mean"],
                        "binding_counts": candidate["binding_counts"],
                    }
                )

            means = {
                key: statistics.fmean(float(row[key]) for row in rows)
                for key in (
                    "cagr_retention",
                    "drawdown_ratio",
                    "risk_adjusted_ratio",
                    "turnover_ratio",
                    "maximum_margin_to_equity_pct",
                    "approval_ratio_mean",
                )
            }
            report["allocations"][str(int(allocation_pct))] = {
                "rows": rows,
                "means": means,
            }
            margin_by_allocation.append(means["maximum_margin_to_equity_pct"])

            self.assertGreaterEqual(means["cagr_retention"], 0.90)
            self.assertGreaterEqual(means["turnover_ratio"], 0.90)
            self.assertGreaterEqual(means["risk_adjusted_ratio"], 0.90)
            self.assertLessEqual(means["drawdown_ratio"], 1.05)

        # Capital authorization must remain economically distinguishable instead
        # of collapsing 35/60/100% books onto one absolute company-level ceiling.
        self.assertTrue(
            all(
                later > earlier + 1.0
                for earlier, later in zip(
                    margin_by_allocation, margin_by_allocation[1:]
                )
            )
        )
        self.assertGreater(margin_by_allocation[-1], 1.4 * margin_by_allocation[-2])

        print("RISK_V2_CANDIDATE_ECONOMIC_ACCEPTANCE=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
