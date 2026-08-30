from __future__ import annotations

import json
import math
import statistics
import unittest
from unittest.mock import patch

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


LOSS_APPETITE_SCORES = (50.0, 65.0, 80.0, 100.0)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    mix = position - low
    return ordered[low] * (1.0 - mix) + ordered[high] * mix


def _summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "median": 0.0, "mean": 0.0, "p90": 0.0, "max": 0.0}
    return {
        "min": min(values),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "p90": _percentile(values, 0.90),
        "max": max(values),
    }


def _appetite(score: float) -> dict:
    radar = {
        "strategy_stress_loss_tolerance": float(score),
        "company_materiality_tolerance": float(score),
        "margin_tolerance": 50.0,
        "concentration_tolerance": 50.0,
        "liquidity_tolerance": 50.0,
        "roll_tolerance": 50.0,
    }
    return build_company_risk_appetite(radar)


class OilShortHorizonRiskAppetiteFrontierTests(unittest.TestCase):
    def test_loss_appetite_economic_frontier(self) -> None:
        appetites = {str(int(score)): _appetite(score) for score in LOSS_APPETITE_SCORES}
        report: dict[str, object] = {
            "scope": {
                "seeds": list(SEEDS),
                "allocations_pct": list(ALLOCATIONS),
                "years": 3,
                "loss_appetite_scores": list(LOSS_APPETITE_SCORES),
                "varied_dimensions": [
                    "strategy_stress_loss_tolerance",
                    "company_materiality_tolerance",
                ],
                "held_at_50": [
                    "margin_tolerance",
                    "concentration_tolerance",
                    "liquidity_tolerance",
                    "roll_tolerance",
                ],
                "production_runtime_unchanged": True,
            },
            "allocations": {},
        }

        for allocation_pct in ALLOCATIONS:
            legacy_by_seed: dict[int, dict] = {}
            for seed in SEEDS:
                global_run = run_global_macro(seed, 10)
                legacy_by_seed[seed] = _legacy_metrics(
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

            score_report: dict[str, object] = {}
            for score_key, appetite in appetites.items():
                rows: list[dict[str, object]] = []
                for seed in SEEDS:
                    global_run = run_global_macro(seed, 10)
                    with patch(
                        "asset_simulation.tests.test_oil_short_horizon_risk_economic_counterfactual.build_company_risk_appetite",
                        return_value=appetite,
                    ):
                        v2 = _simulate_v2_counterfactual(global_run, allocation_pct)
                    legacy = legacy_by_seed[seed]
                    turnover_ratio = (
                        0.0
                        if legacy["total_traded_lots"] == 0
                        else v2["total_traded_lots"] / legacy["total_traded_lots"]
                    )
                    cost_ratio = (
                        0.0
                        if legacy["execution_cost_usd"] <= 0.0
                        else v2["execution_cost_usd"] / legacy["execution_cost_usd"]
                    )
                    cagr_retention = (
                        0.0
                        if abs(legacy["cagr_pct"]) <= 1e-12
                        else v2["cagr_pct"] / legacy["cagr_pct"]
                    )
                    drawdown_ratio = (
                        0.0
                        if legacy["maximum_drawdown_pct"] <= 1e-12
                        else v2["maximum_drawdown_pct"]
                        / legacy["maximum_drawdown_pct"]
                    )
                    risk_adjusted_ratio = (
                        0.0
                        if abs(legacy["return_to_drawdown"]) <= 1e-12
                        else v2["return_to_drawdown"]
                        / legacy["return_to_drawdown"]
                    )
                    rows.append(
                        {
                            "seed": seed,
                            "legacy": {
                                "cagr_pct": legacy["cagr_pct"],
                                "maximum_drawdown_pct": legacy["maximum_drawdown_pct"],
                                "annualized_turn_volatility_pct": legacy[
                                    "annualized_turn_volatility_pct"
                                ],
                                "return_to_drawdown": legacy["return_to_drawdown"],
                            },
                            "v2": {
                                "cagr_pct": v2["cagr_pct"],
                                "maximum_drawdown_pct": v2["maximum_drawdown_pct"],
                                "annualized_turn_volatility_pct": v2[
                                    "annualized_turn_volatility_pct"
                                ],
                                "return_to_drawdown": v2["return_to_drawdown"],
                                "maximum_margin_to_equity_pct": v2[
                                    "maximum_margin_to_equity_pct"
                                ],
                                "approval_ratio_mean": v2["approval_ratio"]["mean"],
                                "approval_ratio_median": v2["approval_ratio"]["median"],
                                "clipped_turns": v2["clipped_turns"],
                                "binding_counts": v2["binding_counts"],
                            },
                            "relative": {
                                "cagr_retention": cagr_retention,
                                "drawdown_ratio": drawdown_ratio,
                                "risk_adjusted_ratio": risk_adjusted_ratio,
                                "turnover_ratio": turnover_ratio,
                                "execution_cost_ratio": cost_ratio,
                            },
                        }
                    )

                score_report[score_key] = {
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
                            "execution_cost_ratio",
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

            report["allocations"][str(int(allocation_pct))] = score_report

        # Diagnostic only on first introduction. We assert only that the frontier
        # is economically non-degenerate; calibration gates are set after reading
        # the paired outcomes instead of choosing a target in advance.
        high_score_turnover = []
        low_score_turnover = []
        for allocation in report["allocations"].values():
            low_score_turnover.append(
                float(allocation["50"]["summary"]["turnover_ratio"]["mean"])
            )
            high_score_turnover.append(
                float(allocation["100"]["summary"]["turnover_ratio"]["mean"])
            )
        self.assertTrue(
            any(high > low + 1e-6 for high, low in zip(high_score_turnover, low_score_turnover))
        )

        print("RISK_V2_APPETITE_FRONTIER=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
