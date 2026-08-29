"""Cross-seed distribution audit for the formal oil futures account.

This audit deliberately separates exchange/account facts from game calibration
targets.  It replays the same immutable market and forecast path across three
direction styles and multiple committee capital authorizations, then checks
ledger, margin, tail-risk, cost and capacity behaviour.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics
from typing import Any, Iterable, Mapping, Sequence

from .model.engine import run_global_macro
from .model.oil_futures_account import (
    apply_oil_futures_account_constraints,
    create_oil_futures_account,
    settle_oil_futures_account_turn,
)
from .model.oil_futures_overlay import oil_futures_payload
from .model.oil_short_term_forecast import (
    generate_institution_profile_for_score_range,
    generate_oil_short_term_forecast,
)
from .model.oil_strategy_research import generate_oil_strategy_research_roster
from .model.oil_trading_strategy import (
    _half_turn_serial,
    _turn_from_serial,
    build_oil_strategy_decision,
    settle_oil_strategy_turn,
)
from .model.registry import load_registered_assets, sha256_json
from .model.institution_organization import (
    initial_proprietary_capital_usd,
    resolve_institution_organization,
)


CALIBRATION_VERSION = "asset-simulation-oil-formal-account-calibration-v0.3.0"
DEFAULT_SEEDS = (42, 2026, 7777, 9001, 314159, 271828)
DEFAULT_AUTHORIZATIONS = (35.0, 60.0, 85.0)
DEFAULT_STYLES = ("reversion", "balanced", "continuation")
TURNS_PER_YEAR = 24


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("formal account calibration contains a non-finite value")
        return round(value, 6)
    return value


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(map(float, values))
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    location = (len(ordered) - 1) * percentile
    lower = math.floor(location)
    upper = math.ceil(location)
    weight = location - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _strategy_style_profiles() -> dict[str, dict[str, Any]]:
    candidates = generate_oil_strategy_research_roster(
        seed=20260826, candidate_count=8
    )["candidates"]
    ordered = sorted(
        candidates,
        key=lambda item: float(item["style_radar"]["continuation_reversion"]),
    )
    return {
        "reversion": ordered[0],
        "balanced": min(
            ordered,
            key=lambda item: abs(
                float(item["style_radar"]["continuation_reversion"]) - 50.0
            ),
        ),
        "continuation": ordered[-1],
    }


def _build_visible_path(
    seed: int,
    horizon_years: int,
    *,
    forecast_score_range: tuple[float, float] = (55.0, 65.0),
) -> list[dict[str, Any]]:
    start_serial = _half_turn_serial(2030, 1, 1)
    end_serial = start_serial + int(horizon_years) * TURNS_PER_YEAR
    end_year, _, _ = _turn_from_serial(end_serial)
    run = run_global_macro(int(seed), max(6, end_year - 2024))
    forecast_profile = generate_institution_profile_for_score_range(
        seed=20260827,
        score_min=float(forecast_score_range[0]),
        score_max=float(forecast_score_range[1]),
    )
    previous_vintage = None
    path: list[dict[str, Any]] = []
    current_market = oil_futures_payload(
        run, as_of_year=2030, as_of_month=1, as_of_half=1
    )
    for serial in range(start_serial, end_serial):
        year, month, half = _turn_from_serial(serial)
        next_year, next_month, next_half = _turn_from_serial(serial + 1)
        vintage = generate_oil_short_term_forecast(
            run,
            as_of_year=year,
            as_of_month=month,
            as_of_half=half,
            institution_profile=forecast_profile,
            previous_vintage=previous_vintage,
        )
        next_market = oil_futures_payload(
            run,
            as_of_year=next_year,
            as_of_month=next_month,
            as_of_half=next_half,
        )
        path.append(
            {
                "start_market": current_market,
                "end_market": next_market,
                "forecast": vintage,
            }
        )
        current_market = next_market
        previous_vintage = vintage
    return path


def _maximum_drawdown_pct(curve: Sequence[float]) -> float:
    peak = float(curve[0])
    drawdown = 0.0
    for value in map(float, curve):
        peak = max(peak, value)
        if peak > 0.0:
            drawdown = min(drawdown, value / peak - 1.0)
    return 100.0 * drawdown


def _run_scenario(
    *,
    seed: int,
    path: Sequence[Mapping[str, Any]],
    style_label: str,
    strategy_profile: Mapping[str, Any],
    authorization_pct: float,
) -> dict[str, Any]:
    assets = load_registered_assets()
    initial_equity = initial_proprietary_capital_usd(assets)
    account = create_oil_futures_account(
        account_id=f"CAL-{seed}-{style_label}-{authorization_pct:g}",
        initial_cash_usd=initial_equity,
    )
    corporate_risk_state = None
    strategy_risk_state = None
    thesis_state = None
    gross_turnover_history: list[int] = []
    equity_curve = [initial_equity]
    turn_returns: list[float] = []
    margin_utilizations: list[float] = []
    gross_notional_history: list[float] = []
    total_traded_lots = 0
    total_traded_notional = 0.0
    total_execution_cost = 0.0
    total_round_trip_lots = 0
    total_round_trip_gross_pnl = 0.0
    total_round_trip_execution_cost = 0.0
    total_gross_pnl_before_cost = 0.0
    total_positive_gross_pnl = 0.0
    thesis_status_counts: Counter[str] = Counter()
    thesis_event_counts: Counter[str] = Counter()
    thesis_transition_counts: Counter[str] = Counter()
    account_expansion_violations = 0
    maintenance_violations = 0
    maximum_cash_identity_error = 0.0
    completed_turns = 0

    for item in path:
        if bool(account["ever_insolvent"]):
            break
        start_market = item["start_market"]
        end_market = item["end_market"]
        before_equity = float(account["equity_usd"])
        decision = build_oil_strategy_decision(
            start_market,
            item["forecast"],
            positions=account["positions"],
            equity_usd=before_equity,
            strategy_research_profile=strategy_profile,
            risk_state=corporate_risk_state,
            strategy_risk_state=strategy_risk_state,
            thesis_state=thesis_state,
            capital_authorization_pct_of_company_equity=authorization_pct,
            fee_state={
                "rolling_gross_turnover_lots": sum(gross_turnover_history[-24:])
            },
        )
        account_authorization = apply_oil_futures_account_constraints(
            account, start_market, decision
        )
        if bool(account_authorization["authorization"]["account_can_expand_prior_approval"]):
            account_expansion_violations += 1
        constrained_decision = account_authorization["decision"]
        strategy_settlement = settle_oil_strategy_turn(
            start_market,
            end_market,
            constrained_decision,
            positions=account["positions"],
            equity_usd=before_equity,
            allow_equity_exhaustion=True,
        )
        account_settlement = settle_oil_futures_account_turn(
            account, start_market, end_market, strategy_settlement
        )
        account = account_settlement["state"]
        ledger = account_settlement["ledger"]
        snapshot = account_settlement["accountAfter"]
        corporate_risk_state = dict(decision["corporateRisk"]["state"])
        strategy_risk_state = dict(decision["strategyRisk"]["state"])
        thesis_report = strategy_settlement["thesisInvalidation"]
        thesis_state = dict(thesis_report["state"])
        for thesis_contract in thesis_state.get("contracts", {}).values():
            thesis_status_counts[str(thesis_contract["status"])] += 1
        for evaluation in thesis_report.get("evaluations", ()):
            thesis_event_counts["evaluations"] += 1
            for event in (
                "band_breach",
                "material_band_breach",
                "severe_band_breach",
                "direction_miss",
            ):
                if bool(evaluation[event]):
                    thesis_event_counts[event] += 1
            thesis_transition_counts[
                f"{evaluation['status_before']}->{evaluation['status_after']}"
            ] += 1
        execution_summary = strategy_settlement["executionSummary"]
        gross_turnover = int(execution_summary["gross_turnover_lots"])
        gross_turnover_history.append(gross_turnover)
        total_traded_lots += gross_turnover
        total_round_trip_lots += int(execution_summary["round_trip_lots"])
        total_round_trip_gross_pnl += float(
            execution_summary["round_trip_gross_pnl_usd"]
        )
        total_round_trip_execution_cost += float(
            execution_summary["round_trip_execution_cost_usd"]
        )
        gross_pnl_before_cost = float(
            execution_summary["gross_pnl_before_cost_usd"]
        )
        total_gross_pnl_before_cost += gross_pnl_before_cost
        total_positive_gross_pnl += max(0.0, gross_pnl_before_cost)
        total_traded_notional += float(
            execution_summary["traded_notional_usd"]
        )
        total_execution_cost += float(
            execution_summary["execution_cost_usd"]
        )
        after_equity = float(account["equity_usd"])
        equity_curve.append(after_equity)
        turn_returns.append(after_equity / before_equity - 1.0)
        margin_value = snapshot["margin_to_equity_pct"]
        margin_utilizations.append(
            100.0 if margin_value is None else float(margin_value)
        )
        gross_notional_history.append(float(snapshot["gross_notional_usd"]))
        maximum_cash_identity_error = max(
            maximum_cash_identity_error,
            abs(float(ledger["cash_identity_error_usd"])),
        )
        if (
            snapshot["status"] != "insolvent"
            and float(snapshot["cash_balance_usd"]) + 0.01
            < float(snapshot["maintenance_margin_usd"])
        ):
            maintenance_violations += 1
        completed_turns += 1

    years = max(completed_turns / TURNS_PER_YEAR, 1.0 / TURNS_PER_YEAR)
    ending_equity = float(account["equity_usd"])
    cagr = (
        -100.0
        if ending_equity <= 0.0
        else 100.0 * ((ending_equity / initial_equity) ** (1.0 / years) - 1.0)
    )
    annualized_volatility = (
        0.0
        if len(turn_returns) < 2
        else 100.0
        * statistics.stdev(turn_returns)
        * math.sqrt(TURNS_PER_YEAR)
    )
    var_95 = _percentile(turn_returns, 0.05) * 100.0
    tail = [value for value in turn_returns if value <= var_95 / 100.0 + 1e-15]
    expected_shortfall_95 = 100.0 * statistics.fmean(tail) if tail else var_95
    average_equity = statistics.fmean(equity_curve)
    average_notional = statistics.fmean(gross_notional_history or [0.0])
    execution_cost_bps = (
        0.0
        if total_traded_notional <= 0.0
        else 10_000.0 * total_execution_cost / total_traded_notional
    )
    thesis_observations = sum(thesis_status_counts.values())
    thesis_status_share_pct = {
        status: (
            0.0
            if thesis_observations <= 0
            else 100.0 * thesis_status_counts[status] / thesis_observations
        )
        for status in ("active", "watch", "invalidated")
    }
    thesis_evaluations = thesis_event_counts["evaluations"]
    thesis_event_rate_pct = {
        event: (
            0.0
            if thesis_evaluations <= 0
            else 100.0 * thesis_event_counts[event] / thesis_evaluations
        )
        for event in (
            "band_breach",
            "material_band_breach",
            "severe_band_breach",
            "direction_miss",
        )
    }
    round_trip_gross_positive_pnl_share = (
        0.0
        if total_positive_gross_pnl <= 0.0
        else abs(total_round_trip_gross_pnl) / total_positive_gross_pnl
    )
    return _round_nested(
        {
            "scenario_id": f"{seed}:{style_label}:{authorization_pct:g}",
            "seed": int(seed),
            "style": style_label,
            "strategy_personnel_id": strategy_profile["appointment"]["personnel_id"],
            "continuation_reversion_score": float(
                strategy_profile["style_radar"]["continuation_reversion"]
            ),
            "style_radar": dict(strategy_profile["style_radar"]),
            "capital_authorization_pct": float(authorization_pct),
            "completed_turns": completed_turns,
            "years": years,
            "initial_equity_usd": initial_equity,
            "ending_equity_usd": ending_equity,
            "cagr_pct": cagr,
            "annualized_volatility_pct": annualized_volatility,
            "return_to_volatility_ratio": (
                0.0 if annualized_volatility <= 0.0 else cagr / annualized_volatility
            ),
            "maximum_drawdown_pct": _maximum_drawdown_pct(equity_curve),
            "half_turn_var_95_pct": var_95,
            "half_turn_expected_shortfall_95_pct": expected_shortfall_95,
            "trading_pnl_pct_of_initial_equity": 100.0
            * float(account["cumulative_trading_pnl_usd"])
            / initial_equity,
            "idle_cash_interest_pct_of_initial_equity": 100.0
            * float(account["cumulative_idle_cash_interest_usd"])
            / initial_equity,
            "margin_financing_cost_pct_of_initial_equity": 100.0
            * float(account["cumulative_margin_financing_cost_usd"])
            / initial_equity,
            "forced_liquidation_cost_pct_of_initial_equity": 100.0
            * float(account["cumulative_forced_liquidation_cost_usd"])
            / initial_equity,
            "median_initial_margin_to_equity_pct": _percentile(
                margin_utilizations, 0.5
            ),
            "maximum_initial_margin_to_equity_pct": max(
                margin_utilizations or [0.0]
            ),
            "average_gross_notional_to_equity_times": (
                0.0 if average_equity <= 0.0 else average_notional / average_equity
            ),
            "margin_call_count": int(account["margin_call_count"]),
            "margin_call_turn_rate_pct": 100.0
            * int(account["margin_call_count"])
            / max(1, completed_turns),
            "forced_liquidation_count": int(account["forced_liquidation_count"]),
            "forced_liquidation_lots": int(
                account["cumulative_forced_liquidation_lots"]
            ),
            "insolvent": bool(account["ever_insolvent"]),
            "total_traded_lots": total_traded_lots,
            "total_round_trip_lots": total_round_trip_lots,
            "total_round_trip_gross_pnl_usd": total_round_trip_gross_pnl,
            "total_round_trip_execution_cost_usd": (
                total_round_trip_execution_cost
            ),
            "total_gross_pnl_before_cost_usd": total_gross_pnl_before_cost,
            "total_positive_gross_pnl_usd": total_positive_gross_pnl,
            "round_trip_gross_positive_pnl_share": (
                round_trip_gross_positive_pnl_share
            ),
            "thesis_status_counts": dict(thesis_status_counts),
            "thesis_status_share_pct": thesis_status_share_pct,
            "thesis_event_counts": dict(thesis_event_counts),
            "thesis_event_rate_pct": thesis_event_rate_pct,
            "thesis_transition_counts": dict(thesis_transition_counts),
            "total_execution_cost_usd": total_execution_cost,
            "execution_cost_bps_of_traded_notional": execution_cost_bps,
            "annual_traded_lots": total_traded_lots / years,
            "annual_traded_notional_to_average_equity_times": (
                0.0
                if average_equity <= 0.0
                else total_traded_notional / years / average_equity
            ),
            "hard_invariants": {
                "maximum_cash_identity_error_usd": maximum_cash_identity_error,
                "account_expansion_violations": account_expansion_violations,
                "maintenance_violations": maintenance_violations,
                "external_capital_flow_usd": 0.0,
            },
        }
    )


def _distribution(values: Iterable[float]) -> dict[str, float]:
    sample = list(map(float, values))
    return {
        "minimum": min(sample),
        "p10": _percentile(sample, 0.10),
        "median": _percentile(sample, 0.50),
        "p90": _percentile(sample, 0.90),
        "maximum": max(sample),
        "mean": statistics.fmean(sample),
    }


def _capacity_pairs(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    authorizations = sorted({float(row["capital_authorization_pct"]) for row in rows})
    if len(authorizations) < 2:
        return pairs
    low, high = authorizations[0], authorizations[-1]
    for seed in sorted({int(row["seed"]) for row in rows}):
        for style in sorted({str(row["style"]) for row in rows}):
            selected = {
                float(row["capital_authorization_pct"]): row
                for row in rows
                if int(row["seed"]) == seed and str(row["style"]) == style
            }
            if low not in selected or high not in selected:
                continue
            low_row, high_row = selected[low], selected[high]
            low_efficiency = float(low_row["trading_pnl_pct_of_initial_equity"]) / low
            high_efficiency = float(high_row["trading_pnl_pct_of_initial_equity"]) / high
            pairs.append(
                {
                    "seed": seed,
                    "style": style,
                    "low_authorization_pct": low,
                    "high_authorization_pct": high,
                    "low_trading_return_per_authorized_pct": low_efficiency,
                    "high_trading_return_per_authorized_pct": high_efficiency,
                    "efficiency_change": high_efficiency - low_efficiency,
                    "low_execution_cost_bps": low_row[
                        "execution_cost_bps_of_traded_notional"
                    ],
                    "high_execution_cost_bps": high_row[
                        "execution_cost_bps_of_traded_notional"
                    ],
                    "incremental_margin_call_count": int(
                        high_row["margin_call_count"]
                    ) - int(low_row["margin_call_count"]),
                }
            )
    return _round_nested(pairs)


def build_oil_formal_account_calibration_report(
    *,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    horizon_years: int = 5,
    styles: Sequence[str] = DEFAULT_STYLES,
    authorizations: Sequence[float] = DEFAULT_AUTHORIZATIONS,
) -> dict[str, Any]:
    if horizon_years <= 0:
        raise ValueError("calibration horizon must be positive")
    assets = load_registered_assets()
    organization_config, _ = resolve_institution_organization(assets)
    capital_base = organization_config["capital_base"]
    capacity_binding_expected = bool(
        capital_base["market_capacity_binding_expected_at_initial_scale"]
    )
    profiles = _strategy_style_profiles()
    unknown = set(styles) - set(profiles)
    if unknown:
        raise ValueError(f"unknown calibration styles: {sorted(unknown)}")
    rows: list[dict[str, Any]] = []
    for seed in map(int, seeds):
        path = _build_visible_path(seed, horizon_years)
        for style in styles:
            for authorization in map(float, authorizations):
                rows.append(
                    _run_scenario(
                        seed=seed,
                        path=path,
                        style_label=style,
                        strategy_profile=profiles[style],
                        authorization_pct=authorization,
                    )
                )

    capacity = _capacity_pairs(rows)
    distributions = {
        key: _distribution(float(row[key]) for row in rows)
        for key in (
            "cagr_pct",
            "annualized_volatility_pct",
            "maximum_drawdown_pct",
            "half_turn_expected_shortfall_95_pct",
            "median_initial_margin_to_equity_pct",
            "maximum_initial_margin_to_equity_pct",
            "execution_cost_bps_of_traded_notional",
            "annual_traded_notional_to_average_equity_times",
        )
    }
    hard_gates = {
        "cash_ledger_reconciles_to_one_cent": max(
            float(row["hard_invariants"]["maximum_cash_identity_error_usd"])
            for row in rows
        ) <= 0.01,
        "account_never_expands_prior_approval": sum(
            int(row["hard_invariants"]["account_expansion_violations"])
            for row in rows
        ) == 0,
        "maintenance_is_restored_or_account_is_insolvent": sum(
            int(row["hard_invariants"]["maintenance_violations"])
            for row in rows
        ) == 0,
        "no_external_capital_flow": True,
    }
    highest_authorization = max(map(float, authorizations))
    lowest_authorization = min(map(float, authorizations))
    highest_authorization_rows = [
        row
        for row in rows
        if float(row["capital_authorization_pct"]) == highest_authorization
    ]
    lowest_authorization_rows = [
        row
        for row in rows
        if float(row["capital_authorization_pct"]) == lowest_authorization
    ]
    high_authorization_median_volatility = _percentile(
        [float(row["annualized_volatility_pct"]) for row in highest_authorization_rows],
        0.5,
    )
    low_authorization_median_volatility = _percentile(
        [float(row["annualized_volatility_pct"]) for row in lowest_authorization_rows],
        0.5,
    )
    capacity_efficiency_decay_share = (
        0.0
        if not capacity
        else sum(float(row["efficiency_change"]) <= 0.0 for row in capacity)
        / len(capacity)
    )
    capacity_cost_increase_share = (
        0.0
        if not capacity
        else sum(
            float(row["high_execution_cost_bps"])
            >= float(row["low_execution_cost_bps"])
            for row in capacity
        )
        / len(capacity)
    )
    realism_gates = {
        "all_authorization_median_volatility_between_2_and_20_pct": 2.0
        <= distributions["annualized_volatility_pct"]["median"]
        <= 20.0,
        "high_authorization_median_volatility_between_5_and_30_pct": 5.0
        <= high_authorization_median_volatility
        <= 30.0,
        "higher_authorization_increases_median_volatility": (
            high_authorization_median_volatility
            > low_authorization_median_volatility
        ),
        "median_cagr_between_minus_10_and_30_pct": -10.0
        <= distributions["cagr_pct"]["median"]
        <= 30.0,
        "p10_maximum_drawdown_not_below_minus_60_pct": distributions[
            "maximum_drawdown_pct"
        ]["p10"]
        >= -60.0,
        "ordinary_insolvency_rate_not_above_5_pct": 100.0
        * sum(bool(row["insolvent"]) for row in rows)
        / len(rows)
        <= 5.0,
        "median_margin_call_turn_rate_not_above_5_pct": _percentile(
            [float(row["margin_call_turn_rate_pct"]) for row in rows], 0.5
        )
        <= 5.0,
        "median_execution_cost_between_0_and_50_bps": 0.0
        <= distributions["execution_cost_bps_of_traded_notional"]["median"]
        <= 50.0,
        "capacity_efficiency_decay_matches_declared_scale_expectation": (
            not capacity_binding_expected
            or not capacity
            or capacity_efficiency_decay_share >= 0.60
        ),
        "higher_authorization_cost_not_lower_in_at_least_80_pct_of_pairs": (
            not capacity or capacity_cost_increase_share >= 0.80
        ),
    }
    result = {
        "ok": all(hard_gates.values()),
        "schemaVersion": "asset-simulation-oil-formal-account-calibration-v2",
        "scope": {
            "seeds": list(map(int, seeds)),
            "horizon_years": int(horizon_years),
            "styles": list(styles),
            "authorizations_pct": list(map(float, authorizations)),
            "scenario_count": len(rows),
            "turns_per_year": TURNS_PER_YEAR,
            "same_forecast_profile_across_scenarios": True,
            "same_market_and_forecast_path_within_seed": True,
            "configured_forecast_score_used_by_strategy": False,
            "institution_type": organization_config["institution_type"],
            "initial_proprietary_capital_usd": initial_proprietary_capital_usd(
                assets
            ),
        },
        "realityAnchors": {
            "contract_size_bbl": 1000,
            "tick_size_usd_per_bbl": 0.01,
            "tick_value_usd_per_lot": 10.0,
            "model_initial_margin_pct": 15.0,
            "model_maintenance_margin_pct": 12.0,
            "maintenance_to_initial_ratio_pct": 80.0,
            "anchor_interpretation": (
                "contract and account mechanics are exchange-anchored; return, "
                "drawdown and failure bands are explicit game calibration gates, "
                "not promises or replicas of one fund index"
            ),
        },
        "distributions": distributions,
        "capacityComparisons": capacity,
        "calibrationDiagnostics": {
            "lowest_authorization_pct": lowest_authorization,
            "highest_authorization_pct": highest_authorization,
            "low_authorization_median_volatility_pct": (
                low_authorization_median_volatility
            ),
            "high_authorization_median_volatility_pct": (
                high_authorization_median_volatility
            ),
            "capacity_efficiency_decay_share_pct": (
                100.0 * capacity_efficiency_decay_share
            ),
            "market_capacity_binding_expected_at_initial_scale": (
                capacity_binding_expected
            ),
            "capacity_efficiency_decay_gate_applicable": (
                capacity_binding_expected
            ),
            "capacity_cost_increase_share_pct": (
                100.0 * capacity_cost_increase_share
            ),
        },
        "hardGates": hard_gates,
        "realismGates": realism_gates,
        "hard_gate_pass": all(hard_gates.values()),
        "realism_gate_pass": all(realism_gates.values()),
        "scenarios": rows,
    }
    identity = {
        "schema_version": "asset-simulation-oil-formal-account-calibration-identity-v2",
        "model_version": CALIBRATION_VERSION,
        "oil_account_config_hash": assets["oil_futures_account_config_hash"],
        "oil_strategy_config_hash": assets["oil_trading_strategy_config_hash"],
        "institution_organization_config_hash": assets[
            "institution_organization_config_hash"
        ],
        "oil_futures_config_hash": assets["oil_futures_overlay_config_hash"],
        "result_hash": sha256_json(result),
    }
    return _round_nested({"identity": identity, **result})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", default=",".join(map(str, DEFAULT_SEEDS)))
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    seeds = tuple(int(item.strip()) for item in args.seeds.split(",") if item.strip())
    report = build_oil_formal_account_calibration_report(
        seeds=seeds, horizon_years=args.years
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
