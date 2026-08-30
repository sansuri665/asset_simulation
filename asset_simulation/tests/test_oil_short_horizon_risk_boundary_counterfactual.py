from __future__ import annotations

from copy import deepcopy
import json
import statistics
import unittest
from unittest.mock import patch

from asset_simulation.model import oil_short_horizon_risk as risk_module
from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.investment_decision import build_company_risk_appetite
from asset_simulation.model.oil_trading_strategy import simulate_oil_trading_strategy
from asset_simulation.tests.test_oil_short_horizon_risk_economic_counterfactual import (
    ALLOCATIONS,
    END,
    SEEDS,
    START,
    _legacy_metrics,
    _simulate_v2_counterfactual,
)


MODES = (
    "default_strategy50",
    "no_company_stress_strategy50",
    "no_company_caps_strategy50",
    "no_company_caps_strategy80",
)


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "max": max(values),
    }


def _appetite(strategy_stress_score: float) -> dict:
    return build_company_risk_appetite(
        {
            "strategy_stress_loss_tolerance": float(strategy_stress_score),
            "company_materiality_tolerance": 50.0,
            "margin_tolerance": 50.0,
            "concentration_tolerance": 50.0,
            "liquidity_tolerance": 50.0,
            "roll_tolerance": 50.0,
        }
    )


class OilShortHorizonRiskBoundaryCounterfactualTests(unittest.TestCase):
    def test_per_strategy_company_caps_duplicate_capital_governance(self) -> None:
        original_company_policy = risk_module._company_policy
        appetites = {
            "50": _appetite(50.0),
            "80": _appetite(80.0),
        }

        def policy_without_company_stress(risk_appetite, profile, config):
            policy = original_company_policy(risk_appetite, profile, config)
            return {
                **policy,
                "max_company_stress_loss_pct_of_equity_per_strategy": 1.0e9,
            }

        def policy_without_company_caps(risk_appetite, profile, config):
            policy = original_company_policy(risk_appetite, profile, config)
            return {
                **policy,
                "max_company_stress_loss_pct_of_equity_per_strategy": 1.0e9,
                "max_company_margin_pct_of_equity_per_strategy": 1.0e9,
            }

        report: dict[str, object] = {
            "scope": {
                "seeds": list(SEEDS),
                "allocations_pct": list(ALLOCATIONS),
                "years": 3,
                "production_runtime_unchanged": True,
                "interpretation": (
                    "company-level per-strategy stress/margin caps are removed only "
                    "in test counterfactuals; market hard limits and strategy-relative "
                    "stress/margin/liquidity/concentration/roll remain active"
                ),
            },
            "allocations": {},
        }

        legacy_by_allocation: dict[float, dict[int, dict]] = {}
        for allocation_pct in ALLOCATIONS:
            legacy_by_allocation[allocation_pct] = {}
            for seed in SEEDS:
                global_run = run_global_macro(seed, 10)
                legacy_by_allocation[allocation_pct][seed] = _legacy_metrics(
                    simulate_oil_trading_strategy(
                        global_run,
                        start_year=START[0],
                        start_month=START[1],
                        start_half=START[2],
                        end_year=END[0],
                        end_month=END[1],
                        end_half=END[2],
                        capital_authorization_pct_of_company_equity=allocation_pct,
                    )
                )

        for allocation_pct in ALLOCATIONS:
            mode_report: dict[str, object] = {}
            for mode in MODES:
                if mode == "default_strategy50":
                    policy_patch = None
                    appetite = appetites["50"]
                elif mode == "no_company_stress_strategy50":
                    policy_patch = policy_without_company_stress
                    appetite = appetites["50"]
                elif mode == "no_company_caps_strategy50":
                    policy_patch = policy_without_company_caps
                    appetite = appetites["50"]
                else:
                    policy_patch = policy_without_company_caps
                    appetite = appetites["80"]

                rows: list[dict[str, object]] = []
                for seed in SEEDS:
                    global_run = run_global_macro(seed, 10)
                    patches = [
                        patch(
                            "asset_simulation.tests.test_oil_short_horizon_risk_economic_counterfactual.build_company_risk_appetite",
                            return_value=appetite,
                        )
                    ]
                    if policy_patch is not None:
                        patches.append(
                            patch(
                                "asset_simulation.model.oil_short_horizon_risk._company_policy",
                                side_effect=policy_patch,
                            )
                        )
                    entered = []
                    try:
                        for item in patches:
                            entered.append(item)
                            item.start()
                        v2 = _simulate_v2_counterfactual(global_run, allocation_pct)
                    finally:
                        for item in reversed(entered):
                            item.stop()

                    legacy = legacy_by_allocation[allocation_pct][seed]
                    rows.append(
                        {
                            "seed": seed,
                            "v2": {
                                "cagr_pct": v2["cagr_pct"],
                                "maximum_drawdown_pct": v2["maximum_drawdown_pct"],
                                "annualized_turn_volatility_pct": v2[
                                    "annualized_turn_volatility_pct"
                                ],
                                "return_to_drawdown": v2["return_to_drawdown"],
                                "total_traded_lots": v2["total_traded_lots"],
                                "maximum_margin_to_equity_pct": v2[
                                    "maximum_margin_to_equity_pct"
                                ],
                                "approval_ratio_mean": v2["approval_ratio"]["mean"],
                                "binding_counts": v2["binding_counts"],
                            },
                            "relative": {
                                "cagr_retention": v2["cagr_pct"]
                                / legacy["cagr_pct"],
                                "drawdown_ratio": v2["maximum_drawdown_pct"]
                                / legacy["maximum_drawdown_pct"],
                                "risk_adjusted_ratio": v2["return_to_drawdown"]
                                / legacy["return_to_drawdown"],
                                "turnover_ratio": v2["total_traded_lots"]
                                / legacy["total_traded_lots"],
                            },
                        }
                    )

                mode_report[mode] = {
                    "rows": rows,
                    "summary": {
                        key: _summary(
                            [float(row["relative"][key]) for row in rows]
                        )
                        for key in (
                            "cagr_retention",
                            "drawdown_ratio",
                            "risk_adjusted_ratio",
                            "turnover_ratio",
                        )
                    }
                    | {
                        "approval_ratio_mean": _summary(
                            [float(row["v2"]["approval_ratio_mean"]) for row in rows]
                        ),
                        "maximum_margin_to_equity_pct": _summary(
                            [
                                float(row["v2"]["maximum_margin_to_equity_pct"])
                                for row in rows
                            ]
                        ),
                    },
                }
            report["allocations"][str(int(allocation_pct))] = mode_report

        # Structural diagnostic gates: removing only company stress must not be
        # enough at large allocations if company margin is the next duplicate cap;
        # removing both company-level per-strategy caps should restore materially
        # more capital differentiation without altering production runtime.
        a100 = report["allocations"]["100"]
        stress_only_turnover = float(
            a100["no_company_stress_strategy50"]["summary"]["turnover_ratio"]["mean"]
        )
        no_caps_turnover = float(
            a100["no_company_caps_strategy50"]["summary"]["turnover_ratio"]["mean"]
        )
        no_caps_80_turnover = float(
            a100["no_company_caps_strategy80"]["summary"]["turnover_ratio"]["mean"]
        )
        self.assertGreater(no_caps_turnover, stress_only_turnover + 0.10)
        self.assertGreater(no_caps_80_turnover, no_caps_turnover + 0.05)

        print("RISK_V2_BOUNDARY_COUNTERFACTUAL=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
