from __future__ import annotations

from copy import deepcopy
import json
import math
import statistics
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.institution_organization import (
    initial_proprietary_capital_usd,
)
from asset_simulation.model.investment_decision import (
    build_company_risk_appetite,
    build_strategy_capital_mandate,
    build_strategy_charter,
    build_strategy_position_mandate,
)
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_short_horizon_risk import (
    build_default_oil_short_horizon_risk_profile,
    build_oil_short_horizon_risk_review,
)
from asset_simulation.model.oil_short_term_forecast import (
    build_institution_profile,
    generate_oil_short_term_forecast,
)
from asset_simulation.model.oil_trading_strategy import (
    build_oil_strategy_decision,
    settle_oil_strategy_turn,
    simulate_oil_trading_strategy,
)
from asset_simulation.model.registry import load_registered_assets


SEEDS = (0, 1, 2, 3)
ALLOCATIONS = (10.0, 35.0, 60.0, 100.0)
START = (2030, 1, 1)
END = (2033, 1, 1)


def _half_turn_serial(year: int, month: int, half: int) -> int:
    return (int(year) * 12 + int(month) - 1) * 2 + int(half) - 1


def _turn_from_serial(serial: int) -> tuple[int, int, int]:
    month_serial, half_index = divmod(int(serial), 2)
    return month_serial // 12, month_serial % 12 + 1, half_index + 1


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


def _maximum_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    maximum = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        maximum = min(maximum, equity / peak - 1.0)
    return -maximum


def _annualized_turn_volatility(turn_returns: list[float]) -> float:
    if len(turn_returns) < 2:
        return 0.0
    return statistics.pstdev(turn_returns) * math.sqrt(24.0)


def _adapt_decision_to_v2(
    decision: dict,
    review: dict,
) -> dict:
    """Replace only final risk approval while preserving the frozen strategy core."""

    adapted = deepcopy(decision)
    approved = {
        str(key): int(value) for key, value in review["riskApprovedTargets"].items()
    }
    for item in adapted["targets"]:
        contract_id = str(item["contract_id"])
        pm_target = int(item["strategy_intent_target_position_lots"])
        v2_target = int(approved.get(contract_id, 0))
        if abs(v2_target) > abs(pm_target) or v2_target * pm_target < 0:
            raise AssertionError("v2 counterfactual risk expanded or reversed PM intent")
        item["counterfactual_v2_approved_target_position_lots"] = v2_target
        item["strategy_risk_approved_target_position_lots"] = v2_target
        item["risk_approved_target_position_lots"] = v2_target
        item["company_risk_approved_target_position_lots"] = v2_target
        item["target_position_lots"] = v2_target

        strategy_budget = int(
            item.get(
                "strategy_gross_turnover_budget_lots",
                item.get("gross_turnover_budget_lots", 0),
            )
        )
        scale = (
            0.0
            if pm_target == 0
            else min(1.0, abs(v2_target) / max(1, abs(pm_target)))
        )
        item["risk_turnover_budget_scale"] = scale
        item["gross_turnover_budget_lots"] = math.floor(strategy_budget * scale)

    adapted["counterfactualRiskV2"] = {
        "review": review,
        "binding_runtime_changed": True,
        "frozen_signal_thesis_execution_account_core": True,
    }
    return adapted


def _simulate_v2_counterfactual(global_run, allocation_pct: float) -> dict:
    assets = load_registered_assets()
    strategy_config = assets["oil_trading_strategy_config"]
    initial_equity = float(initial_proprietary_capital_usd(assets))
    company_risk_appetite = build_company_risk_appetite()
    v2_risk_profile = build_default_oil_short_horizon_risk_profile()
    charter = build_strategy_charter(
        asset="oil",
        horizon="short_horizon",
        strategy_type="directional",
        strategy_id=str(strategy_config["strategy_id"]),
    )
    institution_profile = build_institution_profile()
    fee_lookback_turns = int(
        strategy_config["execution_friction"]["fees"]["rebate_lookback_turns"]
    )

    positions: dict[str, int] = {}
    equity = initial_equity
    equity_curve = [equity]
    turn_returns: list[float] = []
    turn_pnls: list[float] = []
    gross_turnover_history: list[int] = []
    previous_vintage = None
    thesis_state = None
    total_traded_lots = 0
    total_net_traded_lots = 0
    total_execution_cost = 0.0
    total_traded_notional = 0.0
    maximum_margin_to_equity_pct = 0.0
    binding_counts: dict[str, int] = {}
    clipped_turns = 0
    approval_ratios: list[float] = []

    start_serial = _half_turn_serial(*START)
    end_serial = _half_turn_serial(*END)
    current_market = oil_futures_payload(
        global_run,
        as_of_year=START[0],
        as_of_month=START[1],
        as_of_half=START[2],
    )

    for turn_serial in range(start_serial, end_serial):
        year, month, half = _turn_from_serial(turn_serial)
        next_year, next_month, next_half = _turn_from_serial(turn_serial + 1)
        vintage = generate_oil_short_term_forecast(
            global_run,
            as_of_year=year,
            as_of_month=month,
            as_of_half=half,
            institution_profile=institution_profile,
            previous_vintage=previous_vintage,
        )
        # Legacy risk objects are deliberately allowed to exist inside the frozen
        # decision builder, but only PM intent and pre-risk turnover budget are used
        # below. Their final approvals are replaced before settlement.
        raw_decision = build_oil_strategy_decision(
            current_market,
            vintage,
            positions=positions,
            equity_usd=equity,
            thesis_state=thesis_state,
            capital_authorization_pct_of_company_equity=allocation_pct,
            fee_state={
                "rolling_gross_turnover_lots": sum(
                    gross_turnover_history[-fee_lookback_turns:]
                )
            },
        )
        pm_targets = {
            str(item["contract_id"]): int(
                item["strategy_intent_target_position_lots"]
            )
            for item in raw_decision["targets"]
        }
        capital = build_strategy_capital_mandate(
            charter,
            company_equity_usd=equity,
            authorized_pct_of_company_equity=allocation_pct,
        )
        mandate = build_strategy_position_mandate(
            charter,
            capital,
            pm_targets,
        )
        review = build_oil_short_horizon_risk_review(
            current_market,
            mandate,
            company_equity_usd=equity,
            allocated_strategy_capital_usd=float(capital["authorized_capital_usd"]),
            current_positions=positions,
            company_risk_appetite=company_risk_appetite,
            risk_profile=v2_risk_profile,
        )
        decision = _adapt_decision_to_v2(raw_decision, review)
        pm_gross = sum(abs(value) for value in pm_targets.values())
        v2_gross = sum(
            abs(int(value)) for value in review["riskApprovedTargets"].values()
        )
        if pm_gross > 0:
            approval_ratios.append(v2_gross / pm_gross)
        if v2_gross < pm_gross:
            clipped_turns += 1
        for rule in review["portfolioBindingRules"]:
            binding_counts[str(rule)] = binding_counts.get(str(rule), 0) + 1

        next_market = oil_futures_payload(
            global_run,
            as_of_year=next_year,
            as_of_month=next_month,
            as_of_half=next_half,
        )
        equity_before = equity
        settlement = settle_oil_strategy_turn(
            current_market,
            next_market,
            decision,
            positions=positions,
            equity_usd=equity,
        )
        positions = {
            str(key): int(value)
            for key, value in settlement["accountAfter"]["positions"].items()
        }
        equity = float(settlement["accountAfter"]["equity_usd"])
        turn_pnl = float(settlement["accountAfter"]["turn_pnl_usd"])
        turn_pnls.append(turn_pnl)
        turn_returns.append(turn_pnl / equity_before)
        equity_curve.append(equity)
        gross_turnover = int(settlement["executionSummary"]["gross_turnover_lots"])
        gross_turnover_history.append(gross_turnover)
        total_traded_lots += int(settlement["executionSummary"]["traded_lots"])
        total_net_traded_lots += int(settlement["executionSummary"]["net_traded_lots"])
        total_execution_cost += float(settlement["executionSummary"]["execution_cost_usd"])
        total_traded_notional += float(settlement["executionSummary"]["traded_notional_usd"])
        maximum_margin_to_equity_pct = max(
            maximum_margin_to_equity_pct,
            float(settlement["accountAfter"]["margin_to_equity_pct"]),
        )
        thesis_state = dict(settlement["thesisInvalidation"]["state"])
        previous_vintage = vintage
        current_market = next_market

    years = (end_serial - start_serial) / 24.0
    cagr = (equity / initial_equity) ** (1.0 / years) - 1.0
    maximum_drawdown = _maximum_drawdown(equity_curve)
    annualized_volatility = _annualized_turn_volatility(turn_returns)
    return {
        "initial_equity_usd": initial_equity,
        "ending_equity_usd": equity,
        "return_pct": 100.0 * (equity / initial_equity - 1.0),
        "cagr_pct": 100.0 * cagr,
        "maximum_drawdown_pct": 100.0 * maximum_drawdown,
        "annualized_turn_volatility_pct": 100.0 * annualized_volatility,
        "return_to_drawdown": (
            0.0 if maximum_drawdown <= 1e-12 else cagr / maximum_drawdown
        ),
        "worst_turn_return_pct": 100.0 * min(turn_returns),
        "p10_turn_return_pct": 100.0 * _percentile(turn_returns, 0.10),
        "total_traded_lots": total_traded_lots,
        "total_net_traded_lots": total_net_traded_lots,
        "execution_cost_usd": total_execution_cost,
        "total_traded_notional_usd": total_traded_notional,
        "friction_bps": (
            0.0
            if total_traded_notional <= 0.0
            else 10_000.0 * total_execution_cost / total_traded_notional
        ),
        "maximum_margin_to_equity_pct": maximum_margin_to_equity_pct,
        "clipped_turns": clipped_turns,
        "approval_ratio": _summary(approval_ratios),
        "binding_counts": dict(sorted(binding_counts.items())),
        "turn_count": len(turn_returns),
        "ending_positions": positions,
    }


def _legacy_metrics(simulation: dict) -> dict:
    summary = simulation["summary"]
    initial_equity = float(summary["initial_equity_usd"])
    ending_equity = float(summary["ending_equity_usd"])
    turn_returns: list[float] = []
    equity_before = initial_equity
    equity_curve = [initial_equity]
    for turn in simulation["turns"]:
        account = turn["settlement"]["accountAfter"]
        pnl = float(account["turn_pnl_usd"])
        turn_returns.append(pnl / equity_before)
        equity_before = float(account["equity_usd"])
        equity_curve.append(equity_before)
    years = len(turn_returns) / 24.0
    cagr = (ending_equity / initial_equity) ** (1.0 / years) - 1.0
    maximum_drawdown = _maximum_drawdown(equity_curve)
    annualized_volatility = _annualized_turn_volatility(turn_returns)
    return {
        "initial_equity_usd": initial_equity,
        "ending_equity_usd": ending_equity,
        "return_pct": float(summary["return_pct"]),
        "cagr_pct": 100.0 * cagr,
        "maximum_drawdown_pct": 100.0 * maximum_drawdown,
        "annualized_turn_volatility_pct": 100.0 * annualized_volatility,
        "return_to_drawdown": (
            0.0 if maximum_drawdown <= 1e-12 else cagr / maximum_drawdown
        ),
        "worst_turn_return_pct": 100.0 * min(turn_returns),
        "p10_turn_return_pct": 100.0 * _percentile(turn_returns, 0.10),
        "total_traded_lots": int(summary["total_traded_lots"]),
        "total_net_traded_lots": int(summary["total_net_traded_lots"]),
        "execution_cost_usd": float(summary["execution_cost_usd"]),
        "total_traded_notional_usd": float(summary["total_traded_notional_usd"]),
        "friction_bps": float(summary["friction_bps"]),
        "maximum_margin_to_equity_pct": float(summary["maximum_margin_to_equity_pct"]),
        "turn_count": len(turn_returns),
    }


class OilShortHorizonRiskEconomicCounterfactualTests(unittest.TestCase):
    def test_v2_counterfactual_economic_replay(self) -> None:
        report: dict[str, object] = {
            "scope": {
                "seeds": list(SEEDS),
                "allocations_pct": list(ALLOCATIONS),
                "start": "2030-01-H1",
                "end": "2033-01-H1",
                "years": 3,
                "v2_is_non_binding_in_production": True,
                "counterfactual_reuses_frozen_execution_and_account": True,
            },
            "allocations": {},
        }
        all_drawdown_deltas: list[float] = []
        all_volatility_deltas: list[float] = []
        all_turnover_ratios: list[float] = []
        all_return_deltas: list[float] = []

        for allocation_pct in ALLOCATIONS:
            rows: list[dict[str, object]] = []
            for seed in SEEDS:
                global_run = run_global_macro(seed, 10)
                legacy = _legacy_metrics(
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
                v2 = _simulate_v2_counterfactual(global_run, allocation_pct)
                self.assertEqual(legacy["turn_count"], v2["turn_count"])
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
                row = {
                    "seed": seed,
                    "legacy": legacy,
                    "v2": v2,
                    "delta": {
                        "return_pct_points": v2["return_pct"] - legacy["return_pct"],
                        "cagr_pct_points": v2["cagr_pct"] - legacy["cagr_pct"],
                        "maximum_drawdown_pct_points": (
                            v2["maximum_drawdown_pct"]
                            - legacy["maximum_drawdown_pct"]
                        ),
                        "annualized_volatility_pct_points": (
                            v2["annualized_turn_volatility_pct"]
                            - legacy["annualized_turn_volatility_pct"]
                        ),
                        "worst_turn_return_pct_points": (
                            v2["worst_turn_return_pct"]
                            - legacy["worst_turn_return_pct"]
                        ),
                        "p10_turn_return_pct_points": (
                            v2["p10_turn_return_pct"]
                            - legacy["p10_turn_return_pct"]
                        ),
                        "maximum_margin_to_equity_pct_points": (
                            v2["maximum_margin_to_equity_pct"]
                            - legacy["maximum_margin_to_equity_pct"]
                        ),
                        "turnover_ratio": turnover_ratio,
                        "execution_cost_ratio": cost_ratio,
                        "return_to_drawdown_delta": (
                            v2["return_to_drawdown"]
                            - legacy["return_to_drawdown"]
                        ),
                    },
                }
                rows.append(row)
                all_drawdown_deltas.append(
                    float(row["delta"]["maximum_drawdown_pct_points"])
                )
                all_volatility_deltas.append(
                    float(row["delta"]["annualized_volatility_pct_points"])
                )
                all_turnover_ratios.append(float(turnover_ratio))
                all_return_deltas.append(float(row["delta"]["return_pct_points"]))

            report["allocations"][str(int(allocation_pct))] = {
                "rows": rows,
                "delta_summary": {
                    key: _summary([float(row["delta"][key]) for row in rows])
                    for key in (
                        "return_pct_points",
                        "cagr_pct_points",
                        "maximum_drawdown_pct_points",
                        "annualized_volatility_pct_points",
                        "worst_turn_return_pct_points",
                        "p10_turn_return_pct_points",
                        "maximum_margin_to_equity_pct_points",
                        "turnover_ratio",
                        "execution_cost_ratio",
                        "return_to_drawdown_delta",
                    )
                },
            }

        report["cross_allocation"] = {
            "drawdown_delta_pct_points": _summary(all_drawdown_deltas),
            "volatility_delta_pct_points": _summary(all_volatility_deltas),
            "turnover_ratio": _summary(all_turnover_ratios),
            "return_delta_pct_points": _summary(all_return_deltas),
        }

        # Initial diagnostic gate only: preserve accounting and ensure the replay
        # actually changes economic exposures. Economic acceptance gates are set
        # only after inspecting this report, not before seeing the outcomes.
        self.assertTrue(
            any(abs(value) > 1e-9 for value in all_drawdown_deltas)
            or any(abs(value) > 1e-9 for value in all_return_deltas)
        )
        self.assertTrue(all(math.isfinite(value) for value in all_drawdown_deltas))
        self.assertTrue(all(math.isfinite(value) for value in all_volatility_deltas))
        self.assertTrue(all(math.isfinite(value) for value in all_turnover_ratios))

        print("RISK_V2_ECONOMIC_COUNTERFACTUAL=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
