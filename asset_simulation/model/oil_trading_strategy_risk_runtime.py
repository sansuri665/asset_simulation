"""Explicit Directional Oil risk-runtime router.

The frozen strategy simulator remains the production default.  ``v2_candidate``
uses the same visible forecast, PM/thesis logic, execution desk and settlement
mechanics, but replaces the binding risk approval with Investment Decision +
Oil / Short-Horizon Risk v0.2 before settlement.

Legacy strategy/corporate risk objects are still computed by the frozen decision
builder as compatibility diagnostics.  They do not determine candidate target
positions or turnover budgets.
"""

from __future__ import annotations

from copy import deepcopy
import math
import statistics
from typing import Any, Mapping

from .engine import GlobalMacroRun
from .institution_organization import initial_proprietary_capital_usd
from .investment_decision import (
    build_company_risk_appetite,
    build_strategy_capital_mandate,
    build_strategy_charter,
    build_strategy_position_mandate,
)
from .oil_futures_overlay import oil_futures_payload
from .oil_short_horizon_risk_v2 import (
    OIL_SHORT_HORIZON_RISK_MODEL_VERSION,
    build_oil_short_horizon_risk_review,
)
from .oil_short_term_forecast import (
    build_institution_profile,
    generate_oil_short_term_forecast,
)
from .oil_trading_strategy import (
    _half_turn_serial,
    _turn_from_serial,
    build_oil_strategy_decision,
    settle_oil_strategy_turn,
    simulate_oil_trading_strategy,
)
from .registry import load_registered_assets, sha256_json


OIL_TRADING_STRATEGY_RISK_RUNTIME_MODEL_VERSION = (
    "asset-simulation-oil-trading-strategy-risk-runtime-v0.1.0"
)
LEGACY_RISK_RUNTIME = "legacy"
V2_CANDIDATE_RISK_RUNTIME = "v2_candidate"
SUPPORTED_RISK_RUNTIMES = (LEGACY_RISK_RUNTIME, V2_CANDIDATE_RISK_RUNTIME)


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


def apply_v2_candidate_risk_to_directional_decision(
    market: Mapping[str, Any],
    legacy_compatible_decision: Mapping[str, Any],
    *,
    capital_authorization_pct_of_company_equity: float | None = None,
    company_risk_appetite: Mapping[str, Any] | None = None,
    short_horizon_risk_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replace binding legacy risk targets with Investment Decision + Risk v0.2.

    The input decision is produced by the frozen builder so all forecast, PM,
    thesis and execution setup semantics remain untouched.  The legacy risk
    objects are retained only because the frozen settlement reporter still
    expects their compatibility fields.
    """

    raw = dict(legacy_compatible_decision)
    equity = float(raw["accountBefore"]["equity_usd"])
    strategy_id = str(raw["strategy"]["strategy_id"])
    charter = build_strategy_charter(
        asset="oil",
        horizon="short_horizon",
        strategy_type="directional",
        strategy_id=strategy_id,
    )
    capital = build_strategy_capital_mandate(
        charter,
        company_equity_usd=equity,
        authorized_pct_of_company_equity=(
            capital_authorization_pct_of_company_equity
        ),
    )
    pm_targets = {
        str(item["contract_id"]): int(
            item["strategy_intent_target_position_lots"]
        )
        for item in raw["targets"]
    }
    position_mandate = build_strategy_position_mandate(
        charter,
        capital,
        pm_targets,
    )
    appetite = (
        build_company_risk_appetite()
        if company_risk_appetite is None
        else dict(company_risk_appetite)
    )
    review = build_oil_short_horizon_risk_review(
        market,
        position_mandate,
        company_equity_usd=equity,
        allocated_strategy_capital_usd=float(capital["authorized_capital_usd"]),
        current_positions=raw["accountBefore"]["positions"],
        company_risk_appetite=appetite,
        risk_profile=short_horizon_risk_profile,
    )

    adapted = deepcopy(raw)
    approved = {
        str(key): int(value)
        for key, value in review["riskApprovedTargets"].items()
    }
    for item in adapted["targets"]:
        contract_id = str(item["contract_id"])
        pm_target = int(item["strategy_intent_target_position_lots"])
        v2_target = int(approved.get(contract_id, 0))
        if abs(v2_target) > abs(pm_target) or v2_target * pm_target < 0:
            raise ValueError("v2 candidate risk expanded or reversed PM intent")
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
        item["strategy_gross_turnover_budget_lots"] = strategy_budget
        item["risk_turnover_budget_scale"] = scale
        item["gross_turnover_budget_lots"] = math.floor(strategy_budget * scale)

    legacy_strategy_risk = deepcopy(adapted.get("strategyRisk", {}))
    legacy_corporate_risk = deepcopy(adapted.get("corporateRisk", {}))
    adapted["investmentDecisionV2"] = {
        "strategyCharter": charter,
        "capitalMandate": capital,
        "positionMandate": position_mandate,
        "companyRiskAppetite": appetite,
    }
    adapted["strategyRiskV2"] = review
    adapted["legacyRiskDiagnostic"] = {
        "binding": False,
        "retained_for_frozen_settlement_compatibility": True,
        "strategyRisk": legacy_strategy_risk,
        "corporateRisk": legacy_corporate_risk,
    }
    adapted["riskRuntime"] = {
        "mode": V2_CANDIDATE_RISK_RUNTIME,
        "production_default": LEGACY_RISK_RUNTIME,
        "binding_strategy_risk_model_version": OIL_SHORT_HORIZON_RISK_MODEL_VERSION,
        "investment_decision_owner": "investment_decision_committee",
        "legacy_strategy_risk_binding": False,
        "legacy_corporate_risk_binding": False,
        "future_company_materiality_owner": "corporate_aggregate_risk",
        "frozen_signal_thesis_execution_account_core": True,
    }
    return adapted


def _simulate_v2_candidate(
    global_run: GlobalMacroRun,
    *,
    start_year: int,
    start_month: int,
    start_half: int,
    end_year: int,
    end_month: int,
    end_half: int,
    institution_profile: Mapping[str, Any] | None,
    strategy_research_profile: Mapping[str, Any] | None,
    execution_desk_profile: Mapping[str, Any] | None,
    capital_authorization_pct_of_company_equity: float | None,
    turnover_intensity: float | None,
    company_risk_appetite: Mapping[str, Any] | None,
    short_horizon_risk_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    assets = load_registered_assets()
    strategy_config = assets["oil_trading_strategy_config"]
    initial_equity = float(initial_proprietary_capital_usd(assets))
    profile = (
        build_institution_profile()
        if institution_profile is None
        else dict(institution_profile)
    )
    appetite = (
        build_company_risk_appetite()
        if company_risk_appetite is None
        else dict(company_risk_appetite)
    )
    fee_lookback_turns = int(
        strategy_config["execution_friction"]["fees"]["rebate_lookback_turns"]
    )

    start_serial = _half_turn_serial(start_year, start_month, start_half)
    end_serial = _half_turn_serial(end_year, end_month, end_half)
    if end_serial <= start_serial:
        raise ValueError("oil strategy risk-runtime end must follow start")

    positions: dict[str, int] = {}
    equity = initial_equity
    equity_curve = [equity]
    turn_returns: list[float] = []
    gross_turnover_history: list[int] = []
    previous_vintage = None
    thesis_state = None
    total_traded_lots = 0
    total_net_traded_lots = 0
    total_execution_cost = 0.0
    total_traded_notional = 0.0
    maximum_margin_to_equity_pct = 0.0
    clipped_turns = 0
    approval_ratios: list[float] = []
    binding_counts: dict[str, int] = {}
    turns: list[dict[str, Any]] = []

    current_market = oil_futures_payload(
        global_run,
        as_of_year=start_year,
        as_of_month=start_month,
        as_of_half=start_half,
    )
    for turn_serial in range(start_serial, end_serial):
        year, month, half = _turn_from_serial(turn_serial)
        next_year, next_month, next_half = _turn_from_serial(turn_serial + 1)
        vintage = generate_oil_short_term_forecast(
            global_run,
            as_of_year=year,
            as_of_month=month,
            as_of_half=half,
            institution_profile=profile,
            previous_vintage=previous_vintage,
        )
        raw_decision = build_oil_strategy_decision(
            current_market,
            vintage,
            positions=positions,
            equity_usd=equity,
            strategy_research_profile=strategy_research_profile,
            execution_desk_profile=execution_desk_profile,
            thesis_state=thesis_state,
            capital_authorization_pct_of_company_equity=(
                capital_authorization_pct_of_company_equity
            ),
            turnover_intensity=turnover_intensity,
            fee_state={
                "rolling_gross_turnover_lots": sum(
                    gross_turnover_history[-fee_lookback_turns:]
                )
            },
        )
        decision = apply_v2_candidate_risk_to_directional_decision(
            current_market,
            raw_decision,
            capital_authorization_pct_of_company_equity=(
                capital_authorization_pct_of_company_equity
            ),
            company_risk_appetite=appetite,
            short_horizon_risk_profile=short_horizon_risk_profile,
        )
        review = decision["strategyRiskV2"]
        pm_gross = sum(
            abs(int(item["strategy_intent_target_position_lots"]))
            for item in decision["targets"]
        )
        approved_gross = sum(
            abs(int(value)) for value in review["riskApprovedTargets"].values()
        )
        if pm_gross > 0:
            approval_ratios.append(approved_gross / pm_gross)
        if approved_gross < pm_gross:
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
        settlement = deepcopy(settlement)
        settlement["riskRuntime"] = deepcopy(decision["riskRuntime"])
        settlement["strategyRiskV2"] = deepcopy(review)
        settlement["legacyRiskDiagnostic"] = {
            "binding": False,
            "strategyRisk": deepcopy(settlement.get("strategyRisk", {})),
            "corporateRisk": deepcopy(settlement.get("corporateRisk", {})),
        }
        positions = {
            str(key): int(value)
            for key, value in settlement["accountAfter"]["positions"].items()
        }
        equity = float(settlement["accountAfter"]["equity_usd"])
        turn_pnl = float(settlement["accountAfter"]["turn_pnl_usd"])
        turn_returns.append(turn_pnl / equity_before)
        equity_curve.append(equity)
        gross_turnover = int(settlement["executionSummary"]["gross_turnover_lots"])
        gross_turnover_history.append(gross_turnover)
        total_traded_lots += int(settlement["executionSummary"]["traded_lots"])
        total_net_traded_lots += int(
            settlement["executionSummary"]["net_traded_lots"]
        )
        total_execution_cost += float(
            settlement["executionSummary"]["execution_cost_usd"]
        )
        total_traded_notional += float(
            settlement["executionSummary"]["traded_notional_usd"]
        )
        maximum_margin_to_equity_pct = max(
            maximum_margin_to_equity_pct,
            float(settlement["accountAfter"]["margin_to_equity_pct"]),
        )
        thesis_state = dict(settlement["thesisInvalidation"]["state"])
        turns.append(
            {
                "turn_index": len(turns) + 1,
                "decision": decision,
                "settlement": settlement,
            }
        )
        previous_vintage = vintage
        current_market = next_market

    years = (end_serial - start_serial) / 24.0
    cagr = (equity / initial_equity) ** (1.0 / years) - 1.0
    maximum_drawdown = _maximum_drawdown(equity_curve)
    annualized_volatility = _annualized_turn_volatility(turn_returns)
    result = {
        "schemaVersion": "asset-simulation-oil-strategy-risk-runtime-v1",
        "riskRuntime": {
            "mode": V2_CANDIDATE_RISK_RUNTIME,
            "production_default": LEGACY_RISK_RUNTIME,
            "router_model_version": OIL_TRADING_STRATEGY_RISK_RUNTIME_MODEL_VERSION,
            "binding_strategy_risk_model_version": OIL_SHORT_HORIZON_RISK_MODEL_VERSION,
            "legacy_risk_objects_binding": False,
            "future_company_materiality_owner": "corporate_aggregate_risk",
        },
        "period": {
            "start": f"{start_year:04d}-{start_month:02d}-H{start_half}",
            "end": f"{end_year:04d}-{end_month:02d}-H{end_half}",
            "completed_turns": len(turns),
        },
        "summary": {
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
        },
        "turns": turns,
    }
    identity = {
        "model_version": OIL_TRADING_STRATEGY_RISK_RUNTIME_MODEL_VERSION,
        "risk_runtime": V2_CANDIDATE_RISK_RUNTIME,
        "binding_strategy_risk_model_version": OIL_SHORT_HORIZON_RISK_MODEL_VERSION,
        "upstream_global_identity_hash": global_run.identity["identity_hash"],
        "seed": global_run.seed,
        "write_back": False,
        "result_hash": sha256_json(result),
    }
    return {"ok": True, "identity": identity, **result}


def simulate_oil_trading_strategy_with_risk_runtime(
    global_run: GlobalMacroRun,
    *,
    risk_runtime: str = LEGACY_RISK_RUNTIME,
    start_year: int = 2030,
    start_month: int = 1,
    start_half: int = 1,
    end_year: int = 2031,
    end_month: int = 1,
    end_half: int = 1,
    institution_profile: Mapping[str, Any] | None = None,
    strategy_research_profile: Mapping[str, Any] | None = None,
    execution_desk_profile: Mapping[str, Any] | None = None,
    corporate_risk_profile: Mapping[str, Any] | None = None,
    strategy_risk_profile: Mapping[str, Any] | None = None,
    capital_authorization_pct_of_company_equity: float | None = None,
    turnover_intensity: float | None = None,
    company_risk_appetite: Mapping[str, Any] | None = None,
    short_horizon_risk_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Route an exact legacy replay or a non-default Risk v0.2 candidate replay."""

    mode = str(risk_runtime)
    if mode not in SUPPORTED_RISK_RUNTIMES:
        raise ValueError(
            f"unsupported oil strategy risk runtime {mode!r}; "
            f"expected one of {SUPPORTED_RISK_RUNTIMES}"
        )
    if mode == LEGACY_RISK_RUNTIME:
        if company_risk_appetite is not None or short_horizon_risk_profile is not None:
            raise ValueError(
                "candidate-only company appetite/risk profile inputs require v2_candidate"
            )
        return simulate_oil_trading_strategy(
            global_run,
            start_year=start_year,
            start_month=start_month,
            start_half=start_half,
            end_year=end_year,
            end_month=end_month,
            end_half=end_half,
            institution_profile=institution_profile,
            strategy_research_profile=strategy_research_profile,
            execution_desk_profile=execution_desk_profile,
            corporate_risk_profile=corporate_risk_profile,
            strategy_risk_profile=strategy_risk_profile,
            capital_authorization_pct_of_company_equity=(
                capital_authorization_pct_of_company_equity
            ),
            turnover_intensity=turnover_intensity,
        )
    # Old CRO/strategy-risk profiles are intentionally not candidate inputs.  They
    # are accepted in the router signature solely to make legacy routing symmetric,
    # but using them in candidate mode would reintroduce the rejected ownership.
    if corporate_risk_profile is not None or strategy_risk_profile is not None:
        raise ValueError(
            "legacy corporate/strategy risk profiles are not binding v2_candidate inputs"
        )
    return _simulate_v2_candidate(
        global_run,
        start_year=start_year,
        start_month=start_month,
        start_half=start_half,
        end_year=end_year,
        end_month=end_month,
        end_half=end_half,
        institution_profile=institution_profile,
        strategy_research_profile=strategy_research_profile,
        execution_desk_profile=execution_desk_profile,
        capital_authorization_pct_of_company_equity=(
            capital_authorization_pct_of_company_equity
        ),
        turnover_intensity=turnover_intensity,
        company_risk_appetite=company_risk_appetite,
        short_horizon_risk_profile=short_horizon_risk_profile,
    )
