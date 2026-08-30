from __future__ import annotations

from collections import Counter
import json
import math
import statistics
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.investment_decision import (
    build_company_risk_appetite,
    build_strategy_capital_mandate,
    build_strategy_charter,
    build_strategy_position_mandate,
)
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_short_horizon_risk import (
    build_oil_short_horizon_risk_review,
)
from asset_simulation.model.oil_trading_strategy import simulate_oil_trading_strategy


ANNUALIZATION_WEEKS = 52.0
COUNTERFACTUAL_RISK_HORIZONS = (2, 4, 8)


def _gross(targets: dict[str, int]) -> int:
    return sum(abs(int(value)) for value in targets.values())


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else float(numerator) / float(denominator)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return ordered[low]
    mix = index - low
    return ordered[low] * (1.0 - mix) + ordered[high] * mix


def _summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "median": 0.0, "p90": 0.0, "max": 0.0}
    return {
        "min": min(values),
        "median": statistics.median(values),
        "p90": _percentile(values, 0.90),
        "max": max(values),
    }


def _counterfactual_horizon_approval(
    shadow: dict,
    *,
    horizon_weeks: int,
) -> tuple[float, dict[str, int], list[str]]:
    """Re-scale only the soft stress horizon; do not change production risk code."""

    estimates = shadow["softRiskEstimatesBeforePortfolioScale"]
    policy = shadow["companyRiskAppetite"]["resolved_limits"]
    equity = float(shadow["capitalContext"]["company_equity_usd"])
    allocated = float(shadow["capitalContext"]["allocated_strategy_capital_usd"])
    annualized_stress = float(estimates["estimated_stress_loss_usd"])
    horizon_stress = annualized_stress * math.sqrt(
        float(horizon_weeks) / ANNUALIZATION_WEEKS
    )
    margin = float(estimates["initial_margin_usd"])
    stress_strategy_limit = allocated * float(
        policy["max_strategy_stress_loss_pct_of_allocated_capital"]
    ) / 100.0
    stress_company_limit = equity * float(
        policy["max_company_stress_loss_pct_of_equity_per_strategy"]
    ) / 100.0
    margin_strategy_limit = allocated * float(
        policy["max_margin_pct_of_allocated_capital"]
    ) / 100.0
    margin_company_limit = equity * float(
        policy["max_company_margin_pct_of_equity_per_strategy"]
    ) / 100.0
    candidates = {
        "strategy_stress": 1.0
        if horizon_stress <= 1e-12
        else stress_strategy_limit / horizon_stress,
        "company_materiality": 1.0
        if horizon_stress <= 1e-12
        else stress_company_limit / horizon_stress,
        "strategy_margin": 1.0
        if margin <= 1e-12
        else margin_strategy_limit / margin,
        "company_margin_materiality": 1.0
        if margin <= 1e-12
        else margin_company_limit / margin,
    }
    scale = max(0.0, min(1.0, *candidates.values()))
    minimum = min(candidates.values())
    bindings = sorted(
        key
        for key, value in candidates.items()
        if value < 1.0 and math.isclose(value, minimum, rel_tol=1e-8, abs_tol=1e-10)
    )
    preliminary = {
        str(contract_id): int(item["target_lots"])
        for contract_id, item in estimates["per_contract"].items()
    }
    approved = {
        contract_id: (
            int(math.copysign(math.floor(abs(target) * scale), target))
            if target
            else 0
        )
        for contract_id, target in preliminary.items()
    }
    return scale, approved, bindings


class OilShortHorizonRiskShadowAuditTests(unittest.TestCase):
    def test_shadow_compare_real_directional_replays(self) -> None:
        appetite = build_company_risk_appetite()
        allocations = (10.0, 35.0, 60.0, 100.0)
        seeds = (0, 1, 2, 3)
        report: dict[str, object] = {
            "architecture": {
                "binding_runtime": "legacy_strategy_risk_then_legacy_corporate_risk",
                "shadow_runtime": "investment_decision_position_mandate_then_oil_short_horizon_risk_v2",
                "shadow_changes_realized_trades": False,
                "committee_shadow_policy": "preserve_full_pm_intent",
                "company_risk_appetite": "default_committee_policy",
                "production_v2_stress_window": "annualized_proxy",
                "counterfactual_windows_are_diagnostic_only": list(
                    COUNTERFACTUAL_RISK_HORIZONS
                ),
            },
            "allocations": {},
        }

        for allocation_pct in allocations:
            rows: list[dict[str, object]] = []
            binding_counts: Counter[str] = Counter()
            horizon_binding_counts = {
                horizon: Counter() for horizon in COUNTERFACTUAL_RISK_HORIZONS
            }
            for seed in seeds:
                global_run = run_global_macro(seed, 7)
                simulation = simulate_oil_trading_strategy(
                    global_run,
                    start_year=2030,
                    start_month=1,
                    start_half=1,
                    end_year=2031,
                    end_month=1,
                    end_half=1,
                    capital_authorization_pct_of_company_equity=allocation_pct,
                )
                charter = build_strategy_charter(
                    asset="oil",
                    horizon="short_horizon",
                    strategy_type="directional",
                    strategy_id=str(simulation["strategy"]["strategy_id"]),
                )
                for turn in simulation["turns"]:
                    decision = turn["decision"]
                    as_of = decision["asOf"]
                    market = oil_futures_payload(
                        global_run,
                        as_of_year=int(as_of["year"]),
                        as_of_month=int(as_of["month"]),
                        as_of_half=int(as_of["half"]),
                    )
                    equity = float(decision["accountBefore"]["equity_usd"])
                    capital = build_strategy_capital_mandate(
                        charter,
                        company_equity_usd=equity,
                        authorized_pct_of_company_equity=allocation_pct,
                    )
                    pm_targets = {
                        str(item["contract_id"]): int(
                            item["strategy_intent_target_position_lots"]
                        )
                        for item in decision["targets"]
                    }
                    legacy_strategy_targets = {
                        str(item["contract_id"]): int(
                            item["strategy_risk_approved_target_position_lots"]
                        )
                        for item in decision["targets"]
                    }
                    legacy_final_targets = {
                        str(item["contract_id"]): int(item["target_position_lots"])
                        for item in decision["targets"]
                    }
                    mandate = build_strategy_position_mandate(
                        charter,
                        capital,
                        pm_targets,
                    )
                    shadow = build_oil_short_horizon_risk_review(
                        market,
                        mandate,
                        company_equity_usd=equity,
                        allocated_strategy_capital_usd=float(
                            capital["authorized_capital_usd"]
                        ),
                        current_positions=decision["accountBefore"]["positions"],
                        company_risk_appetite=appetite,
                    )
                    shadow_targets = {
                        str(key): int(value)
                        for key, value in shadow["riskApprovedTargets"].items()
                    }
                    self.assertFalse(
                        shadow["governance"]["capital_recommendation_produced"]
                    )
                    for contract_id, shadow_target in shadow_targets.items():
                        pm = int(pm_targets.get(contract_id, 0))
                        self.assertLessEqual(abs(shadow_target), abs(pm))
                        self.assertGreaterEqual(shadow_target * pm, 0)
                    pm_gross = _gross(pm_targets)
                    legacy_strategy_gross = _gross(legacy_strategy_targets)
                    legacy_final_gross = _gross(legacy_final_targets)
                    shadow_gross = _gross(shadow_targets)
                    binding_counts.update(shadow["portfolioBindingRules"])
                    horizon_reports: dict[str, object] = {}
                    for horizon in COUNTERFACTUAL_RISK_HORIZONS:
                        scale, targets, bindings = _counterfactual_horizon_approval(
                            shadow,
                            horizon_weeks=horizon,
                        )
                        horizon_binding_counts[horizon].update(bindings)
                        candidate_gross = _gross(targets)
                        horizon_reports[str(horizon)] = {
                            "portfolio_scale": scale,
                            "approved_gross": candidate_gross,
                            "approval_ratio": _ratio(candidate_gross, pm_gross),
                            "bindings": bindings,
                        }
                    rows.append(
                        {
                            "seed": seed,
                            "turn": int(turn["turn_index"]),
                            "as_of": str(as_of["label"]),
                            "equity_usd": equity,
                            "pm_gross": pm_gross,
                            "legacy_strategy_gross": legacy_strategy_gross,
                            "legacy_final_gross": legacy_final_gross,
                            "shadow_v2_gross": shadow_gross,
                            "legacy_strategy_ratio": _ratio(
                                legacy_strategy_gross, pm_gross
                            ),
                            "legacy_final_ratio": _ratio(
                                legacy_final_gross, pm_gross
                            ),
                            "shadow_v2_ratio": _ratio(shadow_gross, pm_gross),
                            "shadow_vs_legacy_strategy_delta_lots": (
                                shadow_gross - legacy_strategy_gross
                            ),
                            "shadow_vs_legacy_final_delta_lots": (
                                shadow_gross - legacy_final_gross
                            ),
                            "shadow_portfolio_scale": float(
                                shadow["portfolioScale"]
                            ),
                            "shadow_binding_rules": list(
                                shadow["portfolioBindingRules"]
                            ),
                            "shadow_stress_pct_company": float(
                                shadow["materialityBeforePortfolioScale"][
                                    "stress_loss_pct_of_company_equity"
                                ]
                            ),
                            "shadow_stress_pct_strategy": float(
                                shadow["materialityBeforePortfolioScale"][
                                    "stress_loss_pct_of_allocated_strategy_capital"
                                ]
                            ),
                            "counterfactual_horizons": horizon_reports,
                        }
                    )

            active = [row for row in rows if int(row["pm_gross"]) > 0]
            legacy_strategy_ratios = [
                float(row["legacy_strategy_ratio"]) for row in active
            ]
            legacy_final_ratios = [float(row["legacy_final_ratio"]) for row in active]
            shadow_ratios = [float(row["shadow_v2_ratio"]) for row in active]
            delta_strategy = [
                int(row["shadow_vs_legacy_strategy_delta_lots"]) for row in active
            ]
            delta_final = [
                int(row["shadow_vs_legacy_final_delta_lots"]) for row in active
            ]
            horizon_summary: dict[str, object] = {}
            for horizon in COUNTERFACTUAL_RISK_HORIZONS:
                key = str(horizon)
                ratios = [
                    float(row["counterfactual_horizons"][key]["approval_ratio"])
                    for row in active
                ]
                gross_deltas_vs_strategy = [
                    int(row["counterfactual_horizons"][key]["approved_gross"])
                    - int(row["legacy_strategy_gross"])
                    for row in active
                ]
                gross_deltas_vs_final = [
                    int(row["counterfactual_horizons"][key]["approved_gross"])
                    - int(row["legacy_final_gross"])
                    for row in active
                ]
                horizon_summary[key] = {
                    "approval_ratio": _summary(ratios),
                    "more_restrictive_than_legacy_strategy_turn_share": (
                        sum(value < 0 for value in gross_deltas_vs_strategy)
                        / max(1, len(active))
                    ),
                    "less_restrictive_than_legacy_strategy_turn_share": (
                        sum(value > 0 for value in gross_deltas_vs_strategy)
                        / max(1, len(active))
                    ),
                    "equal_legacy_strategy_turn_share": (
                        sum(value == 0 for value in gross_deltas_vs_strategy)
                        / max(1, len(active))
                    ),
                    "more_restrictive_than_legacy_final_turn_share": (
                        sum(value < 0 for value in gross_deltas_vs_final)
                        / max(1, len(active))
                    ),
                    "binding_counts": dict(
                        sorted(horizon_binding_counts[horizon].items())
                    ),
                }
            allocation_report = {
                "turns": len(rows),
                "active_turns": len(active),
                "legacy_strategy_approval_ratio": _summary(legacy_strategy_ratios),
                "legacy_final_approval_ratio": _summary(legacy_final_ratios),
                "shadow_v2_approval_ratio": _summary(shadow_ratios),
                "shadow_more_restrictive_than_legacy_strategy_turn_share": (
                    0.0
                    if not active
                    else sum(value < 0 for value in delta_strategy) / len(active)
                ),
                "shadow_less_restrictive_than_legacy_strategy_turn_share": (
                    0.0
                    if not active
                    else sum(value > 0 for value in delta_strategy) / len(active)
                ),
                "shadow_equal_legacy_strategy_turn_share": (
                    0.0
                    if not active
                    else sum(value == 0 for value in delta_strategy) / len(active)
                ),
                "shadow_more_restrictive_than_legacy_final_turn_share": (
                    0.0
                    if not active
                    else sum(value < 0 for value in delta_final) / len(active)
                ),
                "shadow_less_restrictive_than_legacy_final_turn_share": (
                    0.0
                    if not active
                    else sum(value > 0 for value in delta_final) / len(active)
                ),
                "shadow_equal_legacy_final_turn_share": (
                    0.0
                    if not active
                    else sum(value == 0 for value in delta_final) / len(active)
                ),
                "shadow_binding_counts": dict(sorted(binding_counts.items())),
                "shadow_company_stress_pct": _summary(
                    [float(row["shadow_stress_pct_company"]) for row in active]
                ),
                "shadow_strategy_stress_pct": _summary(
                    [float(row["shadow_stress_pct_strategy"]) for row in active]
                ),
                "counterfactual_risk_horizon_weeks": horizon_summary,
                "largest_absolute_divergences": sorted(
                    (
                        {
                            "seed": int(row["seed"]),
                            "turn": int(row["turn"]),
                            "as_of": str(row["as_of"]),
                            "pm_gross": int(row["pm_gross"]),
                            "legacy_strategy_gross": int(
                                row["legacy_strategy_gross"]
                            ),
                            "legacy_final_gross": int(row["legacy_final_gross"]),
                            "shadow_v2_gross": int(row["shadow_v2_gross"]),
                            "shadow_vs_legacy_strategy_delta_lots": int(
                                row["shadow_vs_legacy_strategy_delta_lots"]
                            ),
                            "shadow_vs_legacy_final_delta_lots": int(
                                row["shadow_vs_legacy_final_delta_lots"]
                            ),
                            "shadow_binding_rules": list(
                                row["shadow_binding_rules"]
                            ),
                        }
                        for row in active
                    ),
                    key=lambda item: max(
                        abs(item["shadow_vs_legacy_strategy_delta_lots"]),
                        abs(item["shadow_vs_legacy_final_delta_lots"]),
                    ),
                    reverse=True,
                )[:5],
            }
            report["allocations"][str(int(allocation_pct))] = allocation_report

        print("RISK_V2_SHADOW_AUDIT=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
