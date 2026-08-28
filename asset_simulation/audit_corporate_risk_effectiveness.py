"""Balanced no-lookahead audit for forecast quality x corporate risk appetite.

The audit keeps the market path, strategy-research profile, execution desk and
forecast vintage identical inside each paired comparison.  Only the appointed
forecast institution and the company-level corporate-risk profile vary.

Run the full five-year, eight-seed audit::

    py -3 -m asset_simulation.audit_corporate_risk_effectiveness

Use ``--smoke`` for a short deterministic development check.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from statistics import fmean, median, pstdev
import sys
import time
from typing import Any, Mapping, Sequence

from .audit_oil_investment_decision import _cached_market_owner
from .model.engine import run_global_macro
from .model import oil_futures_overlay as futures
from .model import oil_short_term_forecast as forecast
from .model import oil_trading_strategy as trading
from .model.corporate_risk_control import (
    RISK_APPETITE_DIMENSIONS,
    build_default_corporate_risk_profile,
    resolve_corporate_risk_profile,
)
from .model.oil_short_term_forecast import (
    generate_institution_profile_for_score_range,
)
from .model.registry import load_registered_assets


TURNS_PER_YEAR = 24
DEFAULT_START = (2030, 1, 1)
DEFAULT_END = (2035, 1, 1)
FULL_SEEDS = tuple(range(100, 108))
SMOKE_SEEDS = (0, 42, 99)
INSTITUTION_SPECS = (
    ("low", 20_260_911, 10.0, 20.0),
    ("mid", 20_260_912, 45.0, 55.0),
    ("high", 20_260_913, 90.0, 100.0),
)
RISK_SPECS = (
    ("strict", 0.0),
    ("neutral", 50.0),
    ("permissive", 100.0),
)


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(map(float, values))
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * float(probability)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _institution_profiles() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profiles = []
    descriptions = []
    for label, seed, score_min, score_max in INSTITUTION_SPECS:
        profile = generate_institution_profile_for_score_range(
            seed=seed,
            score_min=score_min,
            score_max=score_max,
        )
        profiles.append(profile)
        descriptions.append(
            {
                "label": label,
                "generation_seed": seed,
                "requested_score_range": [score_min, score_max],
                "capability_total_score": profile["capability_total_score"],
                "capability_radar": profile["capability_radar"],
                "profile_hash": profile["profile_hash"],
            }
        )
    return profiles, descriptions


def _risk_profile(label: str, score: float) -> dict[str, Any]:
    profile = build_default_corporate_risk_profile()
    profile["appointment"] = {
        **profile["appointment"],
        "personnel_id": f"audit_corporate_risk_{label}",
        "display_name": f"审计风控-{label}",
        "source": "audit_fixed_profile",
    }
    profile["risk_appetite_radar"] = {
        key: float(score) for key in RISK_APPETITE_DIMENSIONS
    }
    profile.pop("profile_hash", None)
    return resolve_corporate_risk_profile(profile)


def _risk_profiles() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profiles = []
    descriptions = []
    for label, score in RISK_SPECS:
        profile = _risk_profile(label, score)
        profiles.append(profile)
        descriptions.append(
            {
                "label": label,
                "uniform_risk_appetite_score": score,
                "resolved_policy": profile["resolved_policy"],
                "profile_hash": profile["profile_hash"],
            }
        )
    return profiles, descriptions


def _new_account(initial_equity_usd: float) -> dict[str, Any]:
    return {
        "positions": {},
        "equity_usd": float(initial_equity_usd),
        "equity_curve": [float(initial_equity_usd)],
        "turn_returns": [],
        "gross_turnover_history": [],
        "traded_lots": 0,
        "execution_cost_usd": 0.0,
        "maximum_margin_to_equity_pct": 0.0,
        "risk_state": None,
        "strategy_risk_state": None,
        "thesis_state": None,
        "risk_status_counts": Counter(),
        "risk_clipped_gross_lots": 0,
        "risk_binding_turns": 0,
    }


def _execute_turn(
    account: dict[str, Any],
    *,
    market: Mapping[str, Any],
    next_market: Mapping[str, Any],
    vintage: Mapping[str, Any],
    risk_profile: Mapping[str, Any],
    strategy_risk_profile: Mapping[str, Any],
) -> None:
    before_equity = float(account["equity_usd"])
    decision = trading.build_oil_strategy_decision(
        market,
        vintage,
        positions=account["positions"],
        equity_usd=before_equity,
        corporate_risk_profile=risk_profile,
        strategy_risk_profile=strategy_risk_profile,
        risk_state=account["risk_state"],
        strategy_risk_state=account["strategy_risk_state"],
        thesis_state=account["thesis_state"],
        fee_state={
            "rolling_gross_turnover_lots": sum(
                account["gross_turnover_history"][-24:]
            )
        },
    )
    settlement = trading.settle_oil_strategy_turn(
        market,
        next_market,
        decision,
        positions=account["positions"],
        equity_usd=before_equity,
    )
    after = settlement["accountAfter"]
    execution = settlement["executionSummary"]
    after_equity = float(after["equity_usd"])
    risk = decision["corporateRisk"]
    clipped = int(risk["approval_summary"]["clipped_gross_lots"])
    account["positions"] = {
        str(key): int(value) for key, value in after["positions"].items()
    }
    account["equity_usd"] = after_equity
    account["equity_curve"].append(after_equity)
    account["turn_returns"].append(after_equity / before_equity - 1.0)
    account["thesis_state"] = dict(
        settlement["thesisInvalidation"]["state"]
    )
    account["gross_turnover_history"].append(
        int(execution["gross_turnover_lots"])
    )
    account["traded_lots"] += int(execution["traded_lots"])
    account["execution_cost_usd"] += float(execution["execution_cost_usd"])
    account["maximum_margin_to_equity_pct"] = max(
        float(account["maximum_margin_to_equity_pct"]),
        float(after["margin_to_equity_pct"]),
    )
    account["risk_state"] = dict(risk["state"])
    account["strategy_risk_state"] = dict(decision["strategyRisk"]["state"])
    account["risk_status_counts"][str(risk["state"]["risk_status"])] += 1
    account["risk_clipped_gross_lots"] += clipped
    account["risk_binding_turns"] += clipped > 0


def _path_metrics(account: Mapping[str, Any]) -> dict[str, Any]:
    equity_curve = list(map(float, account["equity_curve"]))
    turn_returns = list(map(float, account["turn_returns"]))
    initial = equity_curve[0]
    ending = equity_curve[-1]
    peak = initial
    maximum_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        maximum_drawdown = min(maximum_drawdown, equity / peak - 1.0)
    turns = len(turn_returns)
    total_return = ending / initial - 1.0
    annualized_return = (
        0.0 if turns == 0 else (1.0 + total_return) ** (TURNS_PER_YEAR / turns) - 1.0
    )
    annualized_volatility = (
        0.0 if turns < 2 else pstdev(turn_returns) * math.sqrt(TURNS_PER_YEAR)
    )
    drawdown_pct = 100.0 * maximum_drawdown
    annualized_return_pct = 100.0 * annualized_return
    return {
        "return_pct": round(100.0 * total_return, 6),
        "annualized_return_pct": round(annualized_return_pct, 6),
        "maximum_drawdown_pct": round(drawdown_pct, 6),
        "annualized_volatility_pct": round(100.0 * annualized_volatility, 6),
        "calmar": round(
            0.0
            if maximum_drawdown >= -1e-12
            else annualized_return_pct / abs(drawdown_pct),
            6,
        ),
        "ending_equity_usd": round(ending, 2),
        "traded_lots": int(account["traded_lots"]),
        "execution_cost_usd": round(float(account["execution_cost_usd"]), 2),
        "maximum_margin_to_equity_pct": round(
            float(account["maximum_margin_to_equity_pct"]), 6
        ),
        "risk_clipped_gross_lots": int(account["risk_clipped_gross_lots"]),
        "risk_binding_turns": int(account["risk_binding_turns"]),
        "risk_status_counts": {
            key: int(account["risk_status_counts"].get(key, 0))
            for key in ("normal", "watch", "restricted", "reduce_only")
        },
        "turn_count": turns,
    }


def _simulate_seed(
    seed: int,
    institution_profiles: Sequence[Mapping[str, Any]],
    risk_profiles: Sequence[Mapping[str, Any]],
    *,
    start: tuple[int, int, int],
    end: tuple[int, int, int],
) -> dict[str, Any]:
    global_run = run_global_macro(seed=seed, years=int(end[0]) - 2025)
    cached_market, market_cache = _cached_market_owner(global_run)
    original_forecast_market = forecast.oil_futures_payload
    original_strategy_market = trading.oil_futures_payload
    forecast.oil_futures_payload = cached_market
    trading.oil_futures_payload = cached_market
    try:
        config = load_registered_assets()["oil_trading_strategy_config"]
        initial_equity = float(config["initial_reference_equity_usd"])
        neutral_strategy_risk_profile = build_default_corporate_risk_profile()
        accounts = [
            [_new_account(initial_equity) for _ in risk_profiles]
            for _ in institution_profiles
        ]
        previous_vintages: list[Mapping[str, Any] | None] = [
            None for _ in institution_profiles
        ]
        current_market = cached_market(
            global_run,
            as_of_year=start[0],
            as_of_month=start[1],
            as_of_half=start[2],
        )
        start_serial = trading._half_turn_serial(*start)
        end_serial = trading._half_turn_serial(*end)
        for serial in range(start_serial, end_serial):
            year, month, half = trading._turn_from_serial(serial)
            next_year, next_month, next_half = trading._turn_from_serial(serial + 1)
            next_market = cached_market(
                global_run,
                as_of_year=next_year,
                as_of_month=next_month,
                as_of_half=next_half,
            )
            vintages = []
            for institution_index, institution_profile in enumerate(
                institution_profiles
            ):
                vintage = forecast.generate_oil_short_term_forecast(
                    global_run,
                    as_of_year=year,
                    as_of_month=month,
                    as_of_half=half,
                    institution_profile=institution_profile,
                    previous_vintage=previous_vintages[institution_index],
                )
                vintages.append(vintage)
                previous_vintages[institution_index] = vintage
            for institution_index, vintage in enumerate(vintages):
                for risk_index, risk_profile in enumerate(risk_profiles):
                    _execute_turn(
                        accounts[institution_index][risk_index],
                        market=current_market,
                        next_market=next_market,
                        vintage=vintage,
                        risk_profile=risk_profile,
                        strategy_risk_profile=neutral_strategy_risk_profile,
                    )
            current_market = next_market
        return {
            "seed": seed,
            "market_cache_entries": len(market_cache),
            "cells": [
                {
                    "institution_index": institution_index,
                    "risk_index": risk_index,
                    "metrics": _path_metrics(account),
                }
                for institution_index, institution_accounts in enumerate(accounts)
                for risk_index, account in enumerate(institution_accounts)
            ],
        }
    finally:
        forecast.oil_futures_payload = original_forecast_market
        trading.oil_futures_payload = original_strategy_market


def _aggregate_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    returns = [float(row["return_pct"]) for row in rows]
    annualized = [float(row["annualized_return_pct"]) for row in rows]
    drawdowns = [float(row["maximum_drawdown_pct"]) for row in rows]
    calmars = [float(row["calmar"]) for row in rows]
    return {
        "observation_count": len(rows),
        "median_return_pct": round(median(returns), 6),
        "p10_return_pct": round(_percentile(returns, 0.10), 6),
        "minimum_return_pct": round(min(returns), 6),
        "median_annualized_return_pct": round(median(annualized), 6),
        "median_maximum_drawdown_pct": round(median(drawdowns), 6),
        "worst_maximum_drawdown_pct": round(min(drawdowns), 6),
        "median_calmar": round(median(calmars), 6),
        "median_annualized_volatility_pct": round(
            median(float(row["annualized_volatility_pct"]) for row in rows), 6
        ),
        "median_traded_lots": round(
            median(float(row["traded_lots"]) for row in rows), 6
        ),
        "median_execution_cost_usd": round(
            median(float(row["execution_cost_usd"]) for row in rows), 2
        ),
        "maximum_margin_to_equity_pct": round(
            max(float(row["maximum_margin_to_equity_pct"]) for row in rows), 6
        ),
        "total_risk_clipped_gross_lots": sum(
            int(row["risk_clipped_gross_lots"]) for row in rows
        ),
        "total_risk_binding_turns": sum(
            int(row["risk_binding_turns"]) for row in rows
        ),
        "total_risk_status_counts": {
            status: sum(int(row["risk_status_counts"][status]) for row in rows)
            for status in ("normal", "watch", "restricted", "reduce_only")
        },
    }


def _paired_delta(
    rows_by_seed_and_risk: Mapping[tuple[int, str], Mapping[str, Any]],
    seeds: Sequence[int],
    *,
    left: str,
    right: str,
) -> dict[str, Any]:
    keys = (
        "return_pct",
        "annualized_return_pct",
        "maximum_drawdown_pct",
        "annualized_volatility_pct",
        "calmar",
        "maximum_margin_to_equity_pct",
    )
    deltas = {
        key: [
            float(rows_by_seed_and_risk[(seed, left)][key])
            - float(rows_by_seed_and_risk[(seed, right)][key])
            for seed in seeds
        ]
        for key in keys
    }
    comparisons = [
        (
            float(rows_by_seed_and_risk[(seed, left)]["ending_equity_usd"]),
            float(rows_by_seed_and_risk[(seed, right)]["ending_equity_usd"]),
        )
        for seed in seeds
    ]
    left_wins = sum(left_value > right_value for left_value, right_value in comparisons)
    right_wins = sum(left_value < right_value for left_value, right_value in comparisons)
    equal = sum(
        math.isclose(left_value, right_value, abs_tol=0.01)
        for left_value, right_value in comparisons
    )
    return {
        "comparison": f"{left}_minus_{right}",
        "median_delta": {
            key: round(median(values), 6) for key, values in deltas.items()
        },
        "mean_delta": {
            key: round(fmean(values), 6) for key, values in deltas.items()
        },
        "left_higher_ending_equity_seed_count": left_wins,
        "right_higher_ending_equity_seed_count": right_wins,
        "equal_ending_equity_seed_count": equal,
        "seed_level_return_delta_pct": [round(value, 6) for value in deltas["return_pct"]],
    }


def _analyze(
    results: Sequence[Mapping[str, Any]],
    institution_descriptions: Sequence[Mapping[str, Any]],
    risk_descriptions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    institution_labels = [str(row["label"]) for row in institution_descriptions]
    risk_labels = [str(row["label"]) for row in risk_descriptions]
    seeds = [int(result["seed"]) for result in results]
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    seed_lookup: dict[tuple[str, int, str], Mapping[str, Any]] = {}
    for result in results:
        seed = int(result["seed"])
        for cell in result["cells"]:
            institution = institution_labels[int(cell["institution_index"])]
            risk = risk_labels[int(cell["risk_index"])]
            metrics = cell["metrics"]
            grouped[(institution, risk)].append(metrics)
            seed_lookup[(institution, seed, risk)] = metrics

    cells = []
    comparisons = []
    strict_return_wins = 0
    strict_drawdown_improvements = 0
    strict_calmar_wins = 0
    paired_observations = 0
    for institution in institution_labels:
        for risk in risk_labels:
            cells.append(
                {
                    "institution_label": institution,
                    "risk_label": risk,
                    **_aggregate_rows(grouped[(institution, risk)]),
                }
            )
        by_seed_and_risk = {
            (seed, risk): seed_lookup[(institution, seed, risk)]
            for seed in seeds
            for risk in risk_labels
        }
        strict_vs_neutral = _paired_delta(
            by_seed_and_risk,
            seeds,
            left="strict",
            right="neutral",
        )
        permissive_vs_neutral = _paired_delta(
            by_seed_and_risk,
            seeds,
            left="permissive",
            right="neutral",
        )
        comparisons.append(
            {
                "institution_label": institution,
                "strict_vs_neutral": strict_vs_neutral,
                "permissive_vs_neutral": permissive_vs_neutral,
            }
        )
        for seed in seeds:
            strict = by_seed_and_risk[(seed, "strict")]
            neutral = by_seed_and_risk[(seed, "neutral")]
            strict_return_wins += float(strict["return_pct"]) > float(neutral["return_pct"])
            strict_drawdown_improvements += float(strict["maximum_drawdown_pct"]) > float(
                neutral["maximum_drawdown_pct"]
            )
            strict_calmar_wins += float(strict["calmar"]) > float(neutral["calmar"])
            paired_observations += 1

    strict_cells = [row for row in cells if row["risk_label"] == "strict"]
    neutral_cells = [row for row in cells if row["risk_label"] == "neutral"]
    permissive_cells = [row for row in cells if row["risk_label"] == "permissive"]
    permissive_differs = any(
        not math.isclose(
            float(seed_lookup[(institution, seed, "permissive")]["ending_equity_usd"]),
            float(seed_lookup[(institution, seed, "neutral")]["ending_equity_usd"]),
            abs_tol=0.01,
        )
        for institution in institution_labels
        for seed in seeds
    )
    return {
        "cell_summaries": cells,
        "paired_comparisons": comparisons,
        "global_diagnostics": {
            "paired_observation_count": paired_observations,
            "strict_higher_return_count": strict_return_wins,
            "strict_lower_drawdown_count": strict_drawdown_improvements,
            "strict_higher_calmar_count": strict_calmar_wins,
            "strict_higher_return_rate_pct": round(
                100.0 * strict_return_wins / max(1, paired_observations), 6
            ),
            "strict_lower_drawdown_rate_pct": round(
                100.0 * strict_drawdown_improvements / max(1, paired_observations), 6
            ),
            "strict_higher_calmar_rate_pct": round(
                100.0 * strict_calmar_wins / max(1, paired_observations), 6
            ),
            "strict_total_clipped_gross_lots": sum(
                int(row["total_risk_clipped_gross_lots"]) for row in strict_cells
            ),
            "neutral_total_clipped_gross_lots": sum(
                int(row["total_risk_clipped_gross_lots"]) for row in neutral_cells
            ),
            "permissive_total_clipped_gross_lots": sum(
                int(row["total_risk_clipped_gross_lots"])
                for row in permissive_cells
            ),
            "permissive_differs_from_neutral": permissive_differs,
            "interpretation": {
                "strict_is_not_a_pure_position_shrinker": (
                    strict_return_wins > 0 and strict_calmar_wins > 0
                ),
                "strict_does_not_universally_improve_compound_return": (
                    strict_return_wins < paired_observations
                ),
                "permissive_cannot_create_strategy_exposure": True,
            },
        },
    }


def build_corporate_risk_effectiveness_audit(*, smoke: bool = False) -> dict[str, Any]:
    seeds = SMOKE_SEEDS if smoke else FULL_SEEDS
    end = (2032, 1, 1) if smoke else DEFAULT_END
    institution_profiles, institution_descriptions = _institution_profiles()
    risk_profiles, risk_descriptions = _risk_profiles()
    results = []
    started = time.perf_counter()
    for index, seed in enumerate(seeds, start=1):
        seed_started = time.perf_counter()
        results.append(
            _simulate_seed(
                seed,
                institution_profiles,
                risk_profiles,
                start=DEFAULT_START,
                end=end,
            )
        )
        print(
            f"[corporate-risk-cross] seed {seed} ({index}/{len(seeds)}) "
            f"{time.perf_counter() - seed_started:.1f}s",
            file=sys.stderr,
            flush=True,
        )
    assets = load_registered_assets()
    return {
        "schemaVersion": "asset-simulation-corporate-risk-effectiveness-audit-v1",
        "status": "experimental_audit_not_registered_runtime",
        "smoke": smoke,
        "period": {
            "start": f"{DEFAULT_START[0]}-{DEFAULT_START[1]:02d}-H{DEFAULT_START[2]}",
            "end": f"{end[0]}-{end[1]:02d}-H{end[2]}",
            "turn_frequency": "half_month",
        },
        "seeds": list(seeds),
        "market_model_version": assets["oil_futures_overlay_config"]["model_version"],
        "forecast_model_version": assets["oil_short_term_forecast_config"]["model_version"],
        "trading_strategy_model_version": assets["oil_trading_strategy_config"]["model_version"],
        "corporate_risk_model_version": assets["corporate_risk_control_config"]["model_version"],
        "institutions": institution_descriptions,
        "risk_profiles": risk_descriptions,
        "analysis": _analyze(results, institution_descriptions, risk_descriptions),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "evidence_policy": {
            "same_market_path_within_seed": True,
            "same_forecast_vintage_across_risk_profiles": True,
            "same_strategy_research_and_execution_desk": True,
            "forecast_score_is_not_a_direct_strategy_input": True,
            "risk_can_only_preserve_or_shrink_strategy_intent": True,
            "continuous_positions_fees_costs_and_risk_state": True,
            "future_information_available": False,
            "player_market_write_back": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    report = build_corporate_risk_effectiveness_audit(smoke=args.smoke)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
