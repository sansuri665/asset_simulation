"""Experimental no-lookahead audit for an oil investment-decision layer.

The audit is deliberately outside the registered runtime.  It asks whether one
account can benefit from appointing different existing strategy-research
directors at quarterly boundaries while preserving positions, fees, capacity,
and execution costs.  It does not add an investment department to the game.

Run the development stage first and freeze the rules before opening holdout::

    py -3.13 -m asset_simulation.audit_oil_investment_decision --phase develop
    py -3.13 -m asset_simulation.audit_oil_investment_decision --phase holdout

The institutional diversity review is a separate balanced cross audit::

    py -3.13 -m asset_simulation.audit_oil_investment_decision --profile institution-cross
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from statistics import fmean, median, pstdev
import sys
import time
from typing import Any, Iterable, Mapping, Sequence

from .model.engine import run_global_macro
from .model import oil_futures_overlay as futures
from .model import oil_short_term_forecast as forecast
from .model import oil_trading_strategy as trading
from .model.oil_short_term_forecast import (
    build_institution_profile,
    generate_institution_profile_for_score_range,
)
from .model.oil_strategy_research import (
    build_default_oil_strategy_research_profile,
    generate_oil_strategy_research_roster,
)
from .model.registry import load_registered_assets


TURNS_PER_YEAR = 24
INITIAL_EQUITY_USD = 3_000_000_000.0
DEFAULT_ROSTER_SEED = 20_260_825
DEFAULT_CANDIDATE_COUNT = 5
DEFAULT_BLOCK_TURNS = 6
DEFAULT_START = (2030, 1, 1)
DEFAULT_END = (2033, 1, 1)
DEMO_CALIBRATION_SEEDS = tuple(range(0, 12))
DEMO_VALIDATION_SEEDS = tuple(range(100, 108))
DEMO_HOLDOUT_SEEDS = tuple(range(192, 200))
SMOKE_CALIBRATION_SEEDS = (0, 1, 2)
SMOKE_VALIDATION_SEEDS = (100, 101)
SMOKE_HOLDOUT_SEEDS = (198, 199)
INSTITUTION_CROSS_SEEDS = tuple(range(100, 108))
SMOKE_INSTITUTION_CROSS_SEEDS = (100, 101)
INSTITUTION_PROFILE_SPECS = (
    ("low", 20_260_901, 15.0, 25.0),
    ("mid_a", 20_260_902, 45.0, 55.0),
    ("mid_b", 20_260_903, 45.0, 55.0),
    ("high", 20_260_904, 75.0, 85.0),
)


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _round(value: float | int | None) -> float | int | None:
    if isinstance(value, float):
        return round(value, 6)
    return value


def _new_account() -> dict[str, Any]:
    return {
        "positions": {},
        "equity_usd": INITIAL_EQUITY_USD,
        "equity_curve": [INITIAL_EQUITY_USD],
        "turn_returns": [],
        "strategy_target_net_lots": [],
        "gross_turnover_history": [],
        "traded_lots": 0,
        "execution_cost_usd": 0.0,
        "maximum_margin_to_equity_pct": 0.0,
        "position_limit_excess_turns": 0,
        "position_limit_excess_lots_total": 0,
        "maximum_position_limit_excess_lots": 0,
        "current_position_limit_excess_streak": 0,
        "maximum_position_limit_excess_streak": 0,
        "team_indices": [],
        "corporate_risk_state": None,
        "strategy_risk_states": {},
        "thesis_states": {},
    }


def _execute_turn(
    account: dict[str, Any],
    *,
    profile: Mapping[str, Any],
    team_index: int,
    market: Mapping[str, Any],
    next_market: Mapping[str, Any],
    vintage: Mapping[str, Any],
) -> None:
    before_equity = float(account["equity_usd"])
    decision = trading.build_oil_strategy_decision(
        market,
        vintage,
        positions=account["positions"],
        equity_usd=before_equity,
        strategy_research_profile=profile,
        risk_state=account["corporate_risk_state"],
        strategy_risk_state=account["strategy_risk_states"].get(team_index),
        thesis_state=account["thesis_states"].get(team_index),
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
    account["positions"] = {
        str(key): int(value) for key, value in after["positions"].items()
    }
    account["equity_usd"] = after_equity
    account["equity_curve"].append(after_equity)
    account["turn_returns"].append(after_equity / before_equity - 1.0)
    account["corporate_risk_state"] = dict(decision["corporateRisk"]["state"])
    account["strategy_risk_states"][team_index] = dict(
        decision["strategyRisk"]["state"]
    )
    account["thesis_states"][team_index] = dict(
        settlement["thesisInvalidation"]["state"]
    )
    account["strategy_target_net_lots"].append(
        sum(
            int(target["strategy_intent_target_position_lots"])
            for target in decision["targets"]
        )
    )
    gross_turnover = int(execution["gross_turnover_lots"])
    account["gross_turnover_history"].append(gross_turnover)
    account["traded_lots"] += int(execution["traded_lots"])
    account["execution_cost_usd"] += float(execution["execution_cost_usd"])
    account["maximum_margin_to_equity_pct"] = max(
        float(account["maximum_margin_to_equity_pct"]),
        float(after["margin_to_equity_pct"]),
    )
    position_limit_excess_lots = int(execution["position_limit_excess_lots"])
    if position_limit_excess_lots > 0:
        account["position_limit_excess_turns"] += 1
        account["position_limit_excess_lots_total"] += position_limit_excess_lots
        account["maximum_position_limit_excess_lots"] = max(
            int(account["maximum_position_limit_excess_lots"]),
            position_limit_excess_lots,
        )
        account["current_position_limit_excess_streak"] += 1
        account["maximum_position_limit_excess_streak"] = max(
            int(account["maximum_position_limit_excess_streak"]),
            int(account["current_position_limit_excess_streak"]),
        )
    else:
        account["current_position_limit_excess_streak"] = 0
    account["team_indices"].append(team_index)


def _path_statistics(turn_returns: Sequence[float]) -> dict[str, float]:
    wealth = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    for value in turn_returns:
        wealth *= 1.0 + float(value)
        peak = max(peak, wealth)
        maximum_drawdown = min(maximum_drawdown, wealth / peak - 1.0)
    turns = len(turn_returns)
    annualized_return = (
        0.0 if turns == 0 else wealth ** (TURNS_PER_YEAR / turns) - 1.0
    )
    annualized_volatility = (
        0.0
        if turns < 2
        else pstdev(turn_returns) * math.sqrt(TURNS_PER_YEAR)
    )
    return_pct = 100.0 * (wealth - 1.0)
    annualized_return_pct = 100.0 * annualized_return
    maximum_drawdown_pct = 100.0 * maximum_drawdown
    annualized_volatility_pct = 100.0 * annualized_volatility
    calmar = (
        annualized_return_pct / abs(maximum_drawdown_pct)
        if maximum_drawdown_pct < -1e-12
        else 0.0
    )
    decision_utility = annualized_return_pct - 1.5 * abs(maximum_drawdown_pct)
    return {
        "return_pct": return_pct,
        "annualized_return_pct": annualized_return_pct,
        "maximum_drawdown_pct": maximum_drawdown_pct,
        "annualized_volatility_pct": annualized_volatility_pct,
        "calmar": calmar,
        "decision_utility": decision_utility,
    }


def _block_utility(turn_returns: Sequence[float]) -> float:
    stats = _path_statistics(turn_returns)
    return float(stats["return_pct"]) - 1.5 * abs(
        float(stats["maximum_drawdown_pct"])
    )


def _account_metrics(account: Mapping[str, Any]) -> dict[str, Any]:
    stats = _path_statistics(account["turn_returns"])
    team_indices = list(map(int, account.get("team_indices", ())))
    switches = sum(
        current != previous
        for previous, current in zip(team_indices, team_indices[1:])
    )
    usage = Counter(team_indices)
    return {
        **{key: _round(value) for key, value in stats.items()},
        "ending_equity_usd": _round(float(account["equity_usd"])),
        "traded_lots": int(account["traded_lots"]),
        "execution_cost_usd": _round(float(account["execution_cost_usd"])),
        "maximum_margin_to_equity_pct": _round(
            float(account["maximum_margin_to_equity_pct"])
        ),
        "position_limit_excess_turns": int(
            account["position_limit_excess_turns"]
        ),
        "position_limit_excess_lots_total": int(
            account.get("position_limit_excess_lots_total", 0)
        ),
        "maximum_position_limit_excess_lots": int(
            account.get("maximum_position_limit_excess_lots", 0)
        ),
        "maximum_position_limit_excess_streak": int(
            account.get("maximum_position_limit_excess_streak", 0)
        ),
        "turn_count": len(account["turn_returns"]),
        "team_switches": switches,
        "team_turn_usage": {str(key): value for key, value in sorted(usage.items())},
    }


def _metrics_from_returns(turn_returns: Sequence[float]) -> dict[str, Any]:
    return {
        **{key: _round(value) for key, value in _path_statistics(turn_returns).items()},
        "synthetic_sleeve_benchmark": True,
    }


def _visible_feature(market: Mapping[str, Any]) -> str:
    weeks = [
        week
        for month in market["reference"].get("monthly", ())
        for week in month.get("weekly", ())
    ]
    recent = weeks[-8:]
    trend_pct = 0.0
    if len(recent) >= 2 and float(recent[0]["close"]) > 0.0:
        trend_pct = 100.0 * math.log(
            float(recent[-1]["close"]) / float(recent[0]["close"])
        )
    trend = "up" if trend_pct >= 4.0 else "down" if trend_pct <= -4.0 else "sideways"
    return f"{market['curve']['state']}|{trend}"


def _team_profiles(
    *, roster_seed: int, candidate_count: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    default = build_default_oil_strategy_research_profile()
    generated = generate_oil_strategy_research_roster(
        seed=roster_seed, candidate_count=candidate_count
    )["candidates"]
    profiles = [default, *generated]
    descriptions = []
    for index, profile in enumerate(profiles):
        descriptions.append(
            {
                "team_index": index,
                "personnel_id": profile["appointment"]["personnel_id"],
                "display_name": profile["appointment"]["display_name"],
                "style_tags": list(profile.get("style_tags", ())),
                "style_radar": dict(profile["style_radar"]),
                "profile_hash": profile["profile_hash"],
            }
        )
    return profiles, descriptions


def _institution_profiles() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profiles: list[dict[str, Any]] = []
    descriptions: list[dict[str, Any]] = []
    for label, seed, score_min, score_max in INSTITUTION_PROFILE_SPECS:
        profile = generate_institution_profile_for_score_range(
            seed=seed,
            score_min=score_min,
            score_max=score_max,
        )
        profiles.append(profile)
        descriptions.append(
            {
                "institution_index": len(profiles) - 1,
                "label": label,
                "generation_seed": seed,
                "requested_score_range": [score_min, score_max],
                "institution_id": profile["institution_id"],
                "display_name": profile["display_name"],
                "capability_total_score": profile["capability_total_score"],
                "capability_radar": profile["capability_radar"],
                "profile_hash": profile["profile_hash"],
            }
        )
    return profiles, descriptions


def _cached_market_owner(global_run: Any) -> tuple[Any, dict[tuple[int, int, int], Any]]:
    cache: dict[tuple[int, int, int], Any] = {}
    original = futures.oil_futures_payload

    def cached(
        run: Any, *, as_of_year: int, as_of_month: int, as_of_half: int = 2
    ) -> Any:
        if run is not global_run:
            return original(
                run,
                as_of_year=as_of_year,
                as_of_month=as_of_month,
                as_of_half=as_of_half,
            )
        key = (int(as_of_year), int(as_of_month), int(as_of_half))
        if key not in cache:
            cache[key] = original(
                run,
                as_of_year=key[0],
                as_of_month=key[1],
                as_of_half=key[2],
            )
        return cache[key]

    return cached, cache


def _assign_by_blocks(
    block_choices: Sequence[int], *, total_turns: int, block_turns: int
) -> list[int]:
    assignments: list[int] = []
    for choice in block_choices:
        assignments.extend([int(choice)] * block_turns)
    return assignments[:total_turns]


def _rolling_choice(
    team_returns: Sequence[Sequence[float]],
    *,
    total_turns: int,
    block_turns: int,
    fallback_team: int,
    choose_best: bool,
) -> list[int]:
    choices: list[int] = []
    for start in range(0, total_turns, block_turns):
        if start == 0:
            choices.append(fallback_team)
            continue
        history_start = max(0, start - 12)
        values = [
            _block_utility(returns[history_start:start]) for returns in team_returns
        ]
        choice = (
            max(range(len(values)), key=values.__getitem__)
            if choose_best
            else min(range(len(values)), key=values.__getitem__)
        )
        choices.append(choice)
    return _assign_by_blocks(
        choices, total_turns=total_turns, block_turns=block_turns
    )


def _hindsight_block_choice(
    team_returns: Sequence[Sequence[float]],
    *,
    total_turns: int,
    block_turns: int,
) -> list[int]:
    choices = []
    for start in range(0, total_turns, block_turns):
        end = min(total_turns, start + block_turns)
        values = [_block_utility(returns[start:end]) for returns in team_returns]
        choices.append(max(range(len(values)), key=values.__getitem__))
    return _assign_by_blocks(
        choices, total_turns=total_turns, block_turns=block_turns
    )


def _state_choices(
    features: Sequence[str],
    model: Mapping[str, Any],
    *,
    total_turns: int,
    block_turns: int,
    sticky: bool,
) -> list[int]:
    fallback = int(model["fixed_best_team"])
    scores = model["state_team_scores"]
    choices: list[int] = []
    current: int | None = None
    for start in range(0, total_turns, block_turns):
        feature = features[start]
        row = scores.get(feature)
        if row is None:
            candidate = fallback
            row = model["global_team_scores"]
        else:
            candidate = max(range(len(row)), key=lambda index: float(row[index]))
        if current is None:
            current = candidate
        elif not sticky:
            current = candidate
        elif candidate != current and float(row[candidate]) >= float(row[current]) + 0.20:
            current = candidate
        choices.append(current)
    return _assign_by_blocks(
        choices, total_turns=total_turns, block_turns=block_turns
    )


def _replay_assignments(
    timeline: Sequence[Mapping[str, Any]],
    profiles: Sequence[Mapping[str, Any]],
    assignments: Sequence[int],
) -> dict[str, Any]:
    account = _new_account()
    for item, team_index in zip(timeline, assignments, strict=True):
        _execute_turn(
            account,
            profile=profiles[int(team_index)],
            team_index=int(team_index),
            market=item["market"],
            next_market=item["next_market"],
            vintage=item["vintage"],
        )
    return _account_metrics(account)


def _simulate_seed(
    seed: int,
    profiles: Sequence[Mapping[str, Any]],
    *,
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    block_turns: int,
    decision_model: Mapping[str, Any] | None,
) -> dict[str, Any]:
    max_year = int(end[0])
    global_run = run_global_macro(seed=seed, years=max_year - 2025)
    cached_market, market_cache = _cached_market_owner(global_run)
    original_forecast_market = forecast.oil_futures_payload
    original_strategy_market = trading.oil_futures_payload
    forecast.oil_futures_payload = cached_market
    trading.oil_futures_payload = cached_market
    try:
        institution = build_institution_profile()
        previous_vintage: Mapping[str, Any] | None = None
        current_market = cached_market(
            global_run,
            as_of_year=start[0],
            as_of_month=start[1],
            as_of_half=start[2],
        )
        start_serial = trading._half_turn_serial(*start)
        end_serial = trading._half_turn_serial(*end)
        accounts = [_new_account() for _ in profiles]
        timeline: list[dict[str, Any]] = []
        features: list[str] = []
        for serial in range(start_serial, end_serial):
            year, month, half = trading._turn_from_serial(serial)
            next_year, next_month, next_half = trading._turn_from_serial(serial + 1)
            vintage = forecast.generate_oil_short_term_forecast(
                global_run,
                as_of_year=year,
                as_of_month=month,
                as_of_half=half,
                institution_profile=institution,
                previous_vintage=previous_vintage,
            )
            next_market = cached_market(
                global_run,
                as_of_year=next_year,
                as_of_month=next_month,
                as_of_half=next_half,
            )
            feature = _visible_feature(current_market)
            for team_index, (profile, account) in enumerate(
                zip(profiles, accounts, strict=True)
            ):
                _execute_turn(
                    account,
                    profile=profile,
                    team_index=team_index,
                    market=current_market,
                    next_market=next_market,
                    vintage=vintage,
                )
            timeline.append(
                {
                    "market": current_market,
                    "next_market": next_market,
                    "vintage": vintage,
                }
            )
            features.append(feature)
            previous_vintage = vintage
            current_market = next_market

        team_returns = [list(account["turn_returns"]) for account in accounts]
        total_turns = len(timeline)
        blocks = []
        for start_index in range(0, total_turns, block_turns):
            end_index = min(total_turns, start_index + block_turns)
            utilities = [
                _block_utility(returns[start_index:end_index])
                for returns in team_returns
            ]
            blocks.append(
                {
                    "start_turn_index": start_index,
                    "feature": features[start_index],
                    "team_utilities": utilities,
                    "winner_team": max(
                        range(len(utilities)), key=utilities.__getitem__
                    ),
                }
            )
        result: dict[str, Any] = {
            "seed": seed,
            "market_cache_entries": len(market_cache),
            "team_metrics": [_account_metrics(account) for account in accounts],
            "team_turn_returns": team_returns,
            "blocks": blocks,
        }
        if decision_model is not None:
            fixed_best = int(decision_model["fixed_best_team"])
            assignments = {
                "state_match": _state_choices(
                    features,
                    decision_model,
                    total_turns=total_turns,
                    block_turns=block_turns,
                    sticky=False,
                ),
                "state_match_sticky": _state_choices(
                    features,
                    decision_model,
                    total_turns=total_turns,
                    block_turns=block_turns,
                    sticky=True,
                ),
                "recent_winner": _rolling_choice(
                    team_returns,
                    total_turns=total_turns,
                    block_turns=block_turns,
                    fallback_team=fixed_best,
                    choose_best=True,
                ),
                "recent_loser": _rolling_choice(
                    team_returns,
                    total_turns=total_turns,
                    block_turns=block_turns,
                    fallback_team=fixed_best,
                    choose_best=False,
                ),
                "hindsight_block_replay": _hindsight_block_choice(
                    team_returns,
                    total_turns=total_turns,
                    block_turns=block_turns,
                ),
            }
            method_metrics: dict[str, Any] = {
                "fixed_best_calibration": result["team_metrics"][fixed_best]
            }
            method_metrics["best_fixed_ex_post"] = max(
                result["team_metrics"],
                key=lambda item: float(item["decision_utility"]),
            )
            equal_weight_returns = [
                fmean(returns[index] for returns in team_returns)
                for index in range(total_turns)
            ]
            method_metrics["equal_weight_shadow_sleeves"] = _metrics_from_returns(
                equal_weight_returns
            )
            for name, method_assignments in assignments.items():
                method_metrics[name] = _replay_assignments(
                    timeline, profiles, method_assignments
                )
            result["method_metrics"] = method_metrics
        return result
    finally:
        forecast.oil_futures_payload = original_forecast_market
        trading.oil_futures_payload = original_strategy_market


def _simulate_institution_cross_seed(
    seed: int,
    institution_profiles: Sequence[Mapping[str, Any]],
    strategy_profiles: Sequence[Mapping[str, Any]],
    *,
    start: tuple[int, int, int],
    end: tuple[int, int, int],
) -> dict[str, Any]:
    max_year = int(end[0])
    global_run = run_global_macro(seed=seed, years=max_year - 2025)
    cached_market, market_cache = _cached_market_owner(global_run)
    original_forecast_market = forecast.oil_futures_payload
    original_strategy_market = trading.oil_futures_payload
    forecast.oil_futures_payload = cached_market
    trading.oil_futures_payload = cached_market
    try:
        current_market = cached_market(
            global_run,
            as_of_year=start[0],
            as_of_month=start[1],
            as_of_half=start[2],
        )
        start_serial = trading._half_turn_serial(*start)
        end_serial = trading._half_turn_serial(*end)
        accounts = [
            [_new_account() for _ in strategy_profiles]
            for _ in institution_profiles
        ]
        previous_vintages: list[Mapping[str, Any] | None] = [
            None for _ in institution_profiles
        ]
        first_vintage_hashes: list[str] = []
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
                if serial == start_serial:
                    first_vintage_hashes.append(
                        str(vintage["identity"]["result_hash"])
                    )
            for institution_index, vintage in enumerate(vintages):
                for team_index, strategy_profile in enumerate(strategy_profiles):
                    _execute_turn(
                        accounts[institution_index][team_index],
                        profile=strategy_profile,
                        team_index=team_index,
                        market=current_market,
                        next_market=next_market,
                        vintage=vintage,
                    )
            current_market = next_market

        cells = []
        for institution_index, institution_accounts in enumerate(accounts):
            for team_index, account in enumerate(institution_accounts):
                cells.append(
                    {
                        "institution_index": institution_index,
                        "team_index": team_index,
                        "metrics": _account_metrics(account),
                        "turn_returns": list(account["turn_returns"]),
                        "strategy_target_net_lots": list(
                            account["strategy_target_net_lots"]
                        ),
                    }
                )
        return {
            "seed": seed,
            "market_cache_entries": len(market_cache),
            "first_vintage_hashes": first_vintage_hashes,
            "cells": cells,
        }
    finally:
        forecast.oil_futures_payload = original_forecast_market
        trading.oil_futures_payload = original_strategy_market


def _train_decision_model(
    calibration_results: Sequence[Mapping[str, Any]], team_count: int
) -> dict[str, Any]:
    global_values: list[list[float]] = [[] for _ in range(team_count)]
    state_values: dict[str, list[list[float]]] = defaultdict(
        lambda: [[] for _ in range(team_count)]
    )
    for result in calibration_results:
        for block in result["blocks"]:
            feature = str(block["feature"])
            for team_index, value in enumerate(block["team_utilities"]):
                global_values[team_index].append(float(value))
                state_values[feature][team_index].append(float(value))
    global_scores = [fmean(values) for values in global_values]
    shrink_observations = 4.0
    state_scores: dict[str, list[float]] = {}
    state_counts: dict[str, int] = {}
    for feature, team_values in sorted(state_values.items()):
        state_counts[feature] = len(team_values[0])
        state_scores[feature] = [
            (
                sum(values)
                + shrink_observations * global_scores[team_index]
            )
            / (len(values) + shrink_observations)
            for team_index, values in enumerate(team_values)
        ]
    fixed_best = max(range(team_count), key=global_scores.__getitem__)
    return {
        "training_policy": (
            "calibration_only_block_utility_with_four_observation_global_shrinkage"
        ),
        "fixed_best_team": fixed_best,
        "global_team_scores": global_scores,
        "state_team_scores": state_scores,
        "state_observation_counts": state_counts,
        "feature_definition": "current_curve_state_x_visible_eight_week_spot_trend",
        "block_turns": DEFAULT_BLOCK_TURNS,
        "sticky_switch_minimum_score_improvement": 0.20,
    }


def _pearson(first: Sequence[float], second: Sequence[float]) -> float:
    if len(first) != len(second) or len(first) < 2:
        return 0.0
    first_mean = fmean(first)
    second_mean = fmean(second)
    numerator = sum(
        (left - first_mean) * (right - second_mean)
        for left, right in zip(first, second, strict=True)
    )
    first_scale = math.sqrt(sum((value - first_mean) ** 2 for value in first))
    second_scale = math.sqrt(sum((value - second_mean) ** 2 for value in second))
    return 0.0 if first_scale <= 0.0 or second_scale <= 0.0 else numerator / (
        first_scale * second_scale
    )


def _diversity_report(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not results:
        return {}
    team_count = len(results[0]["team_metrics"])
    block_winners: list[int] = []
    reversal_flags: list[bool] = []
    seed_winners: list[int] = []
    pooled_returns: list[list[float]] = [[] for _ in range(team_count)]
    for result in results:
        winners = [int(block["winner_team"]) for block in result["blocks"]]
        block_winners.extend(winners)
        reversal_flags.extend(
            current != previous
            for previous, current in zip(winners, winners[1:])
        )
        utilities = [
            float(item["decision_utility"]) for item in result["team_metrics"]
        ]
        seed_winners.append(max(range(team_count), key=utilities.__getitem__))
        for team_index, returns in enumerate(result["team_turn_returns"]):
            pooled_returns[team_index].extend(map(float, returns))
    block_counts = Counter(block_winners)
    seed_counts = Counter(seed_winners)
    correlations = []
    for left in range(team_count):
        for right in range(left + 1, team_count):
            correlations.append(_pearson(pooled_returns[left], pooled_returns[right]))
    active_block_winners = sum(value > 0 for value in block_counts.values())
    winner_concentration = max(block_counts.values()) / len(block_winners)
    reversal_rate = sum(reversal_flags) / len(reversal_flags)
    active_seed_winners = sum(value > 0 for value in seed_counts.values())
    checks = {
        "at_least_four_teams_win_blocks": active_block_winners >= 4,
        "block_winner_concentration_below_60_pct": winner_concentration < 0.60,
        "consecutive_block_rank_reversal_at_least_35_pct": reversal_rate >= 0.35,
        "at_least_three_teams_win_whole_seed_periods": active_seed_winners >= 3,
    }
    return {
        "block_winner_counts": {
            str(index): block_counts[index] for index in range(team_count)
        },
        "seed_winner_counts": {
            str(index): seed_counts[index] for index in range(team_count)
        },
        "active_block_winner_team_count": active_block_winners,
        "block_winner_concentration_pct": round(100.0 * winner_concentration, 6),
        "consecutive_block_rank_reversal_pct": round(100.0 * reversal_rate, 6),
        "active_seed_winner_team_count": active_seed_winners,
        "mean_pairwise_turn_return_correlation": round(fmean(correlations), 6),
        "checks": checks,
        "passed": all(checks.values()),
    }


def _aggregate_methods(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    methods = sorted(results[0]["method_metrics"])
    aggregate: dict[str, Any] = {}
    for method in methods:
        rows = [result["method_metrics"][method] for result in results]
        annual_returns = [float(row["annualized_return_pct"]) for row in rows]
        utilities = [float(row["decision_utility"]) for row in rows]
        drawdowns = [float(row["maximum_drawdown_pct"]) for row in rows]
        calmars = [float(row["calmar"]) for row in rows]
        aggregate[method] = {
            "seed_count": len(rows),
            "median_annualized_return_pct": round(median(annual_returns), 6),
            "p10_annualized_return_pct": round(_percentile(annual_returns, 0.10), 6),
            "median_maximum_drawdown_pct": round(median(drawdowns), 6),
            "median_calmar": round(median(calmars), 6),
            "median_decision_utility": round(median(utilities), 6),
            "p10_decision_utility": round(_percentile(utilities, 0.10), 6),
            "median_team_switches": round(
                median(float(row.get("team_switches", 0)) for row in rows), 6
            ),
            "total_position_limit_excess_turns": sum(
                int(row.get("position_limit_excess_turns", 0)) for row in rows
            ),
            "seed_level_utility": [round(value, 6) for value in utilities],
        }
    return aggregate


def _summarize_cross_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "observation_count": len(rows),
        "median_annualized_return_pct": round(
            median(float(row["annualized_return_pct"]) for row in rows), 6
        ),
        "min_annualized_return_pct": round(
            min(float(row["annualized_return_pct"]) for row in rows), 6
        ),
        "max_annualized_return_pct": round(
            max(float(row["annualized_return_pct"]) for row in rows), 6
        ),
        "median_maximum_drawdown_pct": round(
            median(float(row["maximum_drawdown_pct"]) for row in rows), 6
        ),
        "median_decision_utility": round(
            median(float(row["decision_utility"]) for row in rows), 6
        ),
        "median_traded_lots": round(
            median(float(row["traded_lots"]) for row in rows), 6
        ),
        "median_execution_cost_usd": round(
            median(float(row["execution_cost_usd"]) for row in rows), 6
        ),
        "maximum_margin_to_equity_pct": round(
            max(float(row["maximum_margin_to_equity_pct"]) for row in rows), 6
        ),
        "total_position_limit_excess_turns": sum(
            int(row["position_limit_excess_turns"]) for row in rows
        ),
        "total_position_limit_excess_lots": sum(
            int(row.get("position_limit_excess_lots_total", 0)) for row in rows
        ),
        "maximum_position_limit_excess_lots": max(
            int(row.get("maximum_position_limit_excess_lots", 0)) for row in rows
        ),
        "maximum_position_limit_excess_streak": max(
            int(row.get("maximum_position_limit_excess_streak", 0)) for row in rows
        ),
        "turn_observation_count": sum(int(row.get("turn_count", 0)) for row in rows),
    }


def _institution_cross_report(
    results: Sequence[Mapping[str, Any]],
    institution_descriptions: Sequence[Mapping[str, Any]],
    team_descriptions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    institution_count = len(institution_descriptions)
    team_count = len(team_descriptions)
    grouped: dict[tuple[int, int], list[Mapping[str, Any]]] = defaultdict(list)
    within_institution_correlations: list[float] = []
    within_institution_opposite_direction_rates: list[float] = []
    within_strategy_correlations: list[float] = []
    best_strategy_counts: Counter[int] = Counter()
    best_institution_counts: Counter[int] = Counter()
    all_vintages_distinct = True
    for result in results:
        lookup = {
            (int(cell["institution_index"]), int(cell["team_index"])): cell
            for cell in result["cells"]
        }
        all_vintages_distinct = all_vintages_distinct and (
            len(set(result["first_vintage_hashes"])) == institution_count
        )
        for address, cell in lookup.items():
            grouped[address].append(cell["metrics"])
        for institution_index in range(institution_count):
            utilities = [
                float(lookup[(institution_index, team_index)]["metrics"]["decision_utility"])
                for team_index in range(team_count)
            ]
            best_strategy_counts[
                max(range(team_count), key=utilities.__getitem__)
            ] += 1
            sequences = [
                lookup[(institution_index, team_index)]["turn_returns"]
                for team_index in range(team_count)
            ]
            target_sequences = [
                lookup[(institution_index, team_index)][
                    "strategy_target_net_lots"
                ]
                for team_index in range(team_count)
            ]
            for left in range(team_count):
                for right in range(left + 1, team_count):
                    within_institution_correlations.append(
                        _pearson(sequences[left], sequences[right])
                    )
                    active_pairs = [
                        (int(left_target), int(right_target))
                        for left_target, right_target in zip(
                            target_sequences[left],
                            target_sequences[right],
                            strict=True,
                        )
                        if int(left_target) != 0 and int(right_target) != 0
                    ]
                    if active_pairs:
                        within_institution_opposite_direction_rates.append(
                            sum(
                                left_target * right_target < 0
                                for left_target, right_target in active_pairs
                            )
                            / len(active_pairs)
                        )
        for team_index in range(team_count):
            utilities = [
                float(lookup[(institution_index, team_index)]["metrics"]["decision_utility"])
                for institution_index in range(institution_count)
            ]
            best_institution_counts[
                max(range(institution_count), key=utilities.__getitem__)
            ] += 1
            sequences = [
                lookup[(institution_index, team_index)]["turn_returns"]
                for institution_index in range(institution_count)
            ]
            for left in range(institution_count):
                for right in range(left + 1, institution_count):
                    within_strategy_correlations.append(
                        _pearson(sequences[left], sequences[right])
                    )

    cell_summaries = []
    for institution_index, institution in enumerate(institution_descriptions):
        for team_index, team in enumerate(team_descriptions):
            cell_summaries.append(
                {
                    "institution_index": institution_index,
                    "institution_label": institution["label"],
                    "team_index": team_index,
                    "team_name": team["display_name"],
                    **_summarize_cross_rows(grouped[(institution_index, team_index)]),
                }
            )
    institution_summaries = []
    for institution_index, institution in enumerate(institution_descriptions):
        rows = [
            row
            for team_index in range(team_count)
            for row in grouped[(institution_index, team_index)]
        ]
        institution_summaries.append(
            {
                "institution_index": institution_index,
                "label": institution["label"],
                "capability_total_score": institution["capability_total_score"],
                **_summarize_cross_rows(rows),
            }
        )
    strategy_summaries = []
    for team_index, team in enumerate(team_descriptions):
        rows = [
            row
            for institution_index in range(institution_count)
            for row in grouped[(institution_index, team_index)]
        ]
        strategy_summaries.append(
            {
                "team_index": team_index,
                "team_name": team["display_name"],
                "style_tags": team["style_tags"],
                **_summarize_cross_rows(rows),
            }
        )

    institution_by_label = {
        str(row["label"]): row for row in institution_summaries
    }
    institution_returns = [
        float(row["median_annualized_return_pct"])
        for row in institution_summaries
    ]
    strategy_returns = [
        float(row["median_annualized_return_pct"])
        for row in strategy_summaries
    ]
    strategy_turnover = [float(row["median_traded_lots"]) for row in strategy_summaries]
    strategy_costs = [
        float(row["median_execution_cost_usd"]) for row in strategy_summaries
    ]
    weak = institution_by_label["low"]
    strong = institution_by_label["high"]
    mid_a = institution_by_label["mid_a"]
    mid_b = institution_by_label["mid_b"]
    checks = {
        "all_first_vintages_are_distinct_within_seed": all_vintages_distinct,
        "high_skill_median_return_beats_low_skill_by_5pp": (
            float(strong["median_annualized_return_pct"])
            >= float(weak["median_annualized_return_pct"]) + 5.0
        ),
        "strategy_style_median_return_spread_at_least_5pp": (
            max(strategy_returns) - min(strategy_returns) >= 5.0
        ),
        "strategy_style_turnover_ratio_at_least_3x": (
            max(strategy_turnover) / max(1.0, min(strategy_turnover)) >= 3.0
        ),
        "strategy_style_execution_cost_ratio_at_least_3x": (
            max(strategy_costs) / max(1.0, min(strategy_costs)) >= 3.0
        ),
        "same_report_strategy_return_correlation_below_0_90": (
            fmean(within_institution_correlations) < 0.90
        ),
        "same_report_opposite_target_direction_rate_at_least_5pct": (
            bool(within_institution_opposite_direction_rates)
            and fmean(within_institution_opposite_direction_rates) >= 0.05
        ),
        "at_least_three_strategy_styles_win_cells": (
            sum(value > 0 for value in best_strategy_counts.values()) >= 3
        ),
        "forced_remediation_rate_below_0_1_pct": (
            sum(
                int(row["total_position_limit_excess_turns"])
                for row in cell_summaries
            )
            / max(
                1,
                sum(int(row["turn_observation_count"]) for row in cell_summaries),
            )
            < 0.001
        ),
        "no_position_limit_remediation_persists_two_turns": all(
            int(row["maximum_position_limit_excess_streak"]) <= 1
            for row in cell_summaries
        ),
    }
    return {
        "cell_summaries": cell_summaries,
        "institution_main_effect": institution_summaries,
        "strategy_style_main_effect": strategy_summaries,
        "best_strategy_counts": {
            str(index): best_strategy_counts[index] for index in range(team_count)
        },
        "best_institution_counts": {
            str(index): best_institution_counts[index]
            for index in range(institution_count)
        },
        "institution_median_return_spread_pp": round(
            max(institution_returns) - min(institution_returns), 6
        ),
        "same_score_band_median_return_difference_pp": round(
            abs(
                float(mid_a["median_annualized_return_pct"])
                - float(mid_b["median_annualized_return_pct"])
            ),
            6,
        ),
        "strategy_median_return_spread_pp": round(
            max(strategy_returns) - min(strategy_returns), 6
        ),
        "strategy_turnover_ratio": round(
            max(strategy_turnover) / max(1.0, min(strategy_turnover)), 6
        ),
        "strategy_execution_cost_ratio": round(
            max(strategy_costs) / max(1.0, min(strategy_costs)), 6
        ),
        "position_limit_remediation_turn_rate_pct": round(
            100.0
            * sum(
                int(row["total_position_limit_excess_turns"])
                for row in cell_summaries
            )
            / max(
                1,
                sum(int(row["turn_observation_count"]) for row in cell_summaries),
            ),
            6,
        ),
        "mean_return_correlation_same_report_across_strategies": round(
            fmean(within_institution_correlations), 6
        ),
        "mean_opposite_target_direction_rate_same_report_pct": round(
            100.0 * fmean(within_institution_opposite_direction_rates), 6
        ),
        "mean_return_correlation_same_strategy_across_reports": round(
            fmean(within_strategy_correlations), 6
        ),
        "checks": checks,
        "institutional_differentiation_supported": all(checks.values()),
    }


def _effectiveness_gate(aggregate: Mapping[str, Any]) -> dict[str, Any]:
    primary = aggregate["state_match_sticky"]
    fixed = aggregate["fixed_best_calibration"]
    loser = aggregate["recent_loser"]
    equal_weight = aggregate["equal_weight_shadow_sleeves"]
    best_fixed_ex_post = aggregate["best_fixed_ex_post"]
    checks = {
        "primary_median_utility_beats_fixed_by_0_5pp": (
            float(primary["median_decision_utility"])
            >= float(fixed["median_decision_utility"]) + 0.50
        ),
        "primary_p10_return_not_worse_than_fixed_by_over_1pp": (
            float(primary["p10_annualized_return_pct"])
            >= float(fixed["p10_annualized_return_pct"]) - 1.0
        ),
        "primary_median_utility_beats_recent_loser_by_2pp": (
            float(primary["median_decision_utility"])
            >= float(loser["median_decision_utility"]) + 2.0
        ),
        "primary_median_utility_beats_equal_weight_shadow_by_0_25pp": (
            float(primary["median_decision_utility"])
            >= float(equal_weight["median_decision_utility"]) + 0.25
        ),
        "ex_post_team_choice_has_0_5pp_median_headroom": (
            float(best_fixed_ex_post["median_decision_utility"])
            >= float(fixed["median_decision_utility"]) + 0.50
        ),
        "all_live_methods_respect_position_limits": all(
            int(row["total_position_limit_excess_turns"]) == 0
            for name, row in aggregate.items()
            if name != "equal_weight_shadow_sleeves"
        ),
    }
    return {"checks": checks, "passed": all(checks.values())}


def _run_seed_group(
    seeds: Sequence[int],
    profiles: Sequence[Mapping[str, Any]],
    *,
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    block_turns: int,
    decision_model: Mapping[str, Any] | None,
    label: str,
) -> list[dict[str, Any]]:
    results = []
    for index, seed in enumerate(seeds, start=1):
        started = time.perf_counter()
        result = _simulate_seed(
            seed,
            profiles,
            start=start,
            end=end,
            block_turns=block_turns,
            decision_model=decision_model,
        )
        results.append(result)
        print(
            f"[{label}] seed {seed} ({index}/{len(seeds)}) "
            f"{time.perf_counter() - started:.1f}s",
            file=sys.stderr,
            flush=True,
        )
    return results


def build_investment_decision_audit(
    *,
    phase: str,
    smoke: bool = False,
    roster_seed: int = DEFAULT_ROSTER_SEED,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    block_turns: int = DEFAULT_BLOCK_TURNS,
) -> dict[str, Any]:
    if phase not in {"develop", "holdout"}:
        raise ValueError("phase must be develop or holdout")
    if block_turns <= 0:
        raise ValueError("block_turns must be positive")
    calibration_seeds = (
        SMOKE_CALIBRATION_SEEDS if smoke else DEMO_CALIBRATION_SEEDS
    )
    evaluation_seeds = (
        SMOKE_VALIDATION_SEEDS
        if smoke and phase == "develop"
        else SMOKE_HOLDOUT_SEEDS
        if smoke
        else DEMO_VALIDATION_SEEDS
        if phase == "develop"
        else DEMO_HOLDOUT_SEEDS
    )
    end = (2032, 1, 1) if smoke else DEFAULT_END
    profiles, team_descriptions = _team_profiles(
        roster_seed=roster_seed, candidate_count=candidate_count
    )
    started = time.perf_counter()
    calibration_results = _run_seed_group(
        calibration_seeds,
        profiles,
        start=DEFAULT_START,
        end=end,
        block_turns=block_turns,
        decision_model=None,
        label="calibration",
    )
    decision_model = _train_decision_model(calibration_results, len(profiles))
    evaluation_label = "validation" if phase == "develop" else "holdout"
    evaluation_results = _run_seed_group(
        evaluation_seeds,
        profiles,
        start=DEFAULT_START,
        end=end,
        block_turns=block_turns,
        decision_model=decision_model,
        label=evaluation_label,
    )
    aggregate = _aggregate_methods(evaluation_results)
    diversity = _diversity_report([*calibration_results, *evaluation_results])
    effectiveness = _effectiveness_gate(aggregate)
    assets = load_registered_assets()
    return {
        "schemaVersion": "asset-simulation-oil-investment-decision-audit-v1",
        "status": "experimental_audit_not_registered_runtime",
        "phase": phase,
        "smoke": smoke,
        "market_model_version": assets["oil_futures_overlay_config"]["model_version"],
        "forecast_model_version": assets["oil_short_term_forecast_config"]["model_version"],
        "strategy_research_model_version": assets["oil_strategy_research_config"]["model_version"],
        "trading_strategy_model_version": assets["oil_trading_strategy_config"]["model_version"],
        "roster_seed": roster_seed,
        "period": {
            "start": f"{DEFAULT_START[0]}-{DEFAULT_START[1]:02d}-H{DEFAULT_START[2]}",
            "end": f"{end[0]}-{end[1]:02d}-H{end[2]}",
            "block_turns": block_turns,
            "decision_frequency": "quarterly" if block_turns == 6 else "custom",
        },
        "calibration_seeds": list(calibration_seeds),
        "evaluation_seeds": list(evaluation_seeds),
        "teams": team_descriptions,
        "decision_model": {
            **decision_model,
            "global_team_scores": [
                round(float(value), 6)
                for value in decision_model["global_team_scores"]
            ],
            "state_team_scores": {
                feature: [round(float(value), 6) for value in values]
                for feature, values in decision_model["state_team_scores"].items()
            },
        },
        "diversity": diversity,
        "method_aggregate": aggregate,
        "effectiveness": effectiveness,
        "closed_loop_supported": diversity["passed"] and effectiveness["passed"],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "evidence_policy": {
            "future_information_in_state_match": False,
            "future_information_in_recent_winner": False,
            "hindsight_block_replay_is_non_deployable_and_not_an_upper_bound": True,
            "best_fixed_ex_post_is_non_deployable_choice_headroom": True,
            "equal_weight_is_synthetic_shadow_sleeve_benchmark": True,
            "live_allocator_positions_fees_and_costs_are_continuous": True,
            "player_market_write_back": False,
        },
    }


def build_institution_strategy_cross_audit(
    *,
    smoke: bool = False,
    roster_seed: int = DEFAULT_ROSTER_SEED,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
) -> dict[str, Any]:
    seeds = SMOKE_INSTITUTION_CROSS_SEEDS if smoke else INSTITUTION_CROSS_SEEDS
    end = (2032, 1, 1) if smoke else DEFAULT_END
    strategy_profiles, team_descriptions = _team_profiles(
        roster_seed=roster_seed,
        candidate_count=candidate_count,
    )
    institution_profiles, institution_descriptions = _institution_profiles()
    results = []
    started = time.perf_counter()
    for index, seed in enumerate(seeds, start=1):
        seed_started = time.perf_counter()
        results.append(
            _simulate_institution_cross_seed(
                seed,
                institution_profiles,
                strategy_profiles,
                start=DEFAULT_START,
                end=end,
            )
        )
        print(
            f"[institution-cross] seed {seed} ({index}/{len(seeds)}) "
            f"{time.perf_counter() - seed_started:.1f}s",
            file=sys.stderr,
            flush=True,
        )
    assets = load_registered_assets()
    analysis = _institution_cross_report(
        results,
        institution_descriptions,
        team_descriptions,
    )
    return {
        "schemaVersion": "asset-simulation-oil-institution-strategy-cross-audit-v2",
        "status": "experimental_audit_not_registered_runtime",
        "profile": "institution-cross",
        "smoke": smoke,
        "market_model_version": assets["oil_futures_overlay_config"]["model_version"],
        "forecast_model_version": assets["oil_short_term_forecast_config"]["model_version"],
        "strategy_research_model_version": assets["oil_strategy_research_config"]["model_version"],
        "trading_strategy_model_version": assets["oil_trading_strategy_config"]["model_version"],
        "period": {
            "start": f"{DEFAULT_START[0]}-{DEFAULT_START[1]:02d}-H{DEFAULT_START[2]}",
            "end": f"{end[0]}-{end[1]:02d}-H{end[2]}",
            "turn_frequency": "half_month",
        },
        "seeds": list(seeds),
        "institutions": institution_descriptions,
        "strategy_teams": team_descriptions,
        "analysis": analysis,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "evidence_policy": {
            "same_market_path_within_seed": True,
            "different_institution_profile_produces_different_report": True,
            "balanced_full_factorial_cross": True,
            "forecast_score_is_not_a_direct_strategy_input": True,
            "same_signal_family_return_correlation_is_diagnostic_not_a_gate": True,
            "positions_fees_costs_and_capacity_are_live": True,
            "player_market_write_back": False,
            "holdout_opened": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("decision", "institution-cross"),
        default="decision",
    )
    parser.add_argument("--phase", choices=("develop", "holdout"), default="develop")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--roster-seed", type=int, default=DEFAULT_ROSTER_SEED)
    parser.add_argument("--candidate-count", type=int, default=DEFAULT_CANDIDATE_COUNT)
    parser.add_argument("--block-turns", type=int, default=DEFAULT_BLOCK_TURNS)
    args = parser.parse_args()
    if args.profile == "institution-cross":
        report = build_institution_strategy_cross_audit(
            smoke=args.smoke,
            roster_seed=args.roster_seed,
            candidate_count=args.candidate_count,
        )
    else:
        report = build_investment_decision_audit(
            phase=args.phase,
            smoke=args.smoke,
            roster_seed=args.roster_seed,
            candidate_count=args.candidate_count,
            block_turns=args.block_turns,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
