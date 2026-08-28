"""Post-hoc trend/range/turning audit for the short single-contract strategy.

Regime labels are calculated only after settlement and never enter decisions.
The audit holds every strategy dimension fixed except continuation/reversion.
"""

from __future__ import annotations

from collections import defaultdict
import json
import math
from typing import Any, Iterable, Mapping

from .model.engine import run_global_macro
from .model.oil_futures_overlay import oil_futures_payload
from .model.oil_short_term_forecast import (
    build_institution_profile,
    generate_oil_short_term_forecast,
)
from .model.oil_strategy_research import (
    build_default_oil_strategy_research_profile,
    resolve_oil_strategy_research_profile,
)
from .model.oil_trading_strategy import (
    _half_turn_serial,
    _turn_from_serial,
    build_oil_strategy_decision,
    settle_oil_strategy_turn,
)


REGIMES = ("trend", "range", "turning")
STYLE_SCORES = {
    "reversion": 10.0,
    "balanced": 50.0,
    "continuation": 90.0,
}


def classify_realized_regimes(reference_prices: Iterable[float]) -> list[str]:
    """Classify four-turn realized windows; labels are evaluation-only."""

    prices = [float(value) for value in reference_prices]
    if any(not math.isfinite(value) or value <= 0.0 for value in prices):
        raise ValueError("regime audit reference prices must be positive")
    if len(prices) < 2:
        return []
    moves = [math.log(right / left) for left, right in zip(prices, prices[1:])]
    labels: list[str] = []
    for index in range(len(moves)):
        window = moves[max(0, index - 3) : index + 1]
        if len(window) < 4:
            labels.append("warmup")
            continue
        early = sum(window[:2])
        late = sum(window[2:])
        total = sum(window)
        path = sum(abs(value) for value in window)
        efficiency = 0.0 if path <= 1e-12 else abs(total) / path
        if early * late < 0.0 and min(abs(early), abs(late)) >= 0.015:
            labels.append("turning")
        elif abs(total) >= 0.035 and efficiency >= 0.55:
            labels.append("trend")
        else:
            labels.append("range")
    return labels


def _fixed_style_profile(style: str, score: float) -> dict[str, Any]:
    base = build_default_oil_strategy_research_profile()
    radar = dict(base["style_radar"])
    radar["continuation_reversion"] = float(score)
    return resolve_oil_strategy_research_profile(
        {
            "appointment": {
                "personnel_id": f"regime_audit_{style}",
                "display_name": f"审计-{style}",
                "source": "audit_fixed_profile",
            },
            "style_radar": radar,
        }
    )


def _replay_style(
    markets: list[Mapping[str, Any]],
    vintages: list[Mapping[str, Any]],
    profile: Mapping[str, Any],
) -> list[dict[str, Any]]:
    positions: dict[str, int] = {}
    equity = 3_000_000_000.0
    risk_state = None
    strategy_risk_state = None
    thesis_state = None
    turnover_history: list[int] = []
    rows: list[dict[str, Any]] = []
    for index, vintage in enumerate(vintages):
        decision = build_oil_strategy_decision(
            markets[index],
            vintage,
            positions=positions,
            equity_usd=equity,
            strategy_research_profile=profile,
            risk_state=risk_state,
            strategy_risk_state=strategy_risk_state,
            thesis_state=thesis_state,
            fee_state={
                "rolling_gross_turnover_lots": sum(turnover_history[-24:])
            },
        )
        settlement = settle_oil_strategy_turn(
            markets[index],
            markets[index + 1],
            decision,
            positions=positions,
            equity_usd=equity,
        )
        before_equity = equity
        equity = float(settlement["accountAfter"]["equity_usd"])
        positions = {
            str(key): int(value)
            for key, value in settlement["accountAfter"]["positions"].items()
        }
        risk_state = dict(decision["corporateRisk"]["state"])
        strategy_risk_state = dict(decision["strategyRisk"]["state"])
        thesis_state = dict(settlement["thesisInvalidation"]["state"])
        turnover_history.append(
            int(settlement["executionSummary"]["gross_turnover_lots"])
        )
        rows.append(
            {
                "turn_return": equity / before_equity - 1.0,
                "turn_pnl_usd": float(
                    settlement["accountAfter"]["turn_pnl_usd"]
                ),
                "gross_target_lots": sum(
                    abs(int(item["strategy_intent_target_position_lots"]))
                    for item in decision["targets"]
                ),
                "traded_lots": int(
                    settlement["executionSummary"]["traded_lots"]
                ),
                "execution_cost_usd": float(
                    settlement["executionSummary"]["execution_cost_usd"]
                ),
            }
        )
    return rows


def build_oil_direction_regime_audit(
    *,
    seeds: Iterable[int] = (0, 42, 99),
    start: tuple[int, int, int] = (2030, 1, 1),
    end: tuple[int, int, int] = (2032, 1, 1),
) -> dict[str, Any]:
    seed_values = tuple(int(value) for value in seeds)
    start_serial = _half_turn_serial(*start)
    end_serial = _half_turn_serial(*end)
    if end_serial <= start_serial + 4:
        raise ValueError("regime audit needs more than four half-month turns")
    styles = {
        name: _fixed_style_profile(name, score)
        for name, score in STYLE_SCORES.items()
    }
    records: dict[str, dict[str, list[dict[str, Any]]]] = {
        name: {regime: [] for regime in REGIMES} for name in styles
    }
    regime_counts = defaultdict(int)
    per_seed_winners: dict[str, dict[str, str]] = {}
    for seed in seed_values:
        world_years = max(7, int(end[0]) - 2024)
        run = run_global_macro(seed=seed, years=world_years)
        turns = [_turn_from_serial(serial) for serial in range(start_serial, end_serial + 1)]
        markets = [
            oil_futures_payload(
                run, as_of_year=year, as_of_month=month, as_of_half=half
            )
            for year, month, half in turns
        ]
        institution = build_institution_profile()
        vintages = []
        previous = None
        for year, month, half in turns[:-1]:
            vintage = generate_oil_short_term_forecast(
                run,
                as_of_year=year,
                as_of_month=month,
                as_of_half=half,
                institution_profile=institution,
                previous_vintage=previous,
            )
            vintages.append(vintage)
            previous = vintage
        reference_prices = [float(market["reference"]["price_usd"]) for market in markets]
        regimes = classify_realized_regimes(reference_prices)
        replayed = {
            name: _replay_style(markets, vintages, profile)
            for name, profile in styles.items()
        }
        seed_winners: dict[str, str] = {}
        for regime in REGIMES:
            indices = [index for index, label in enumerate(regimes) if label == regime]
            regime_counts[regime] += len(indices)
            if not indices:
                continue
            scores = {}
            for style, rows in replayed.items():
                selected = [rows[index] for index in indices]
                records[style][regime].extend(selected)
                scores[style] = sum(float(row["turn_return"]) for row in selected) / len(selected)
            seed_winners[regime] = max(scores, key=scores.get)
        per_seed_winners[str(seed)] = seed_winners

    metrics: dict[str, dict[str, Any]] = {}
    regime_winners: dict[str, str | None] = {}
    for style in styles:
        metrics[style] = {}
        for regime in REGIMES:
            rows = records[style][regime]
            metrics[style][regime] = {
                "turns": len(rows),
                "mean_turn_return_bps": 0.0
                if not rows
                else 10_000.0
                * sum(float(row["turn_return"]) for row in rows)
                / len(rows),
                "win_rate_pct": 0.0
                if not rows
                else 100.0
                * sum(float(row["turn_pnl_usd"]) > 0.0 for row in rows)
                / len(rows),
                "mean_gross_target_lots": 0.0
                if not rows
                else sum(int(row["gross_target_lots"]) for row in rows)
                / len(rows),
                "total_traded_lots": sum(int(row["traded_lots"]) for row in rows),
                "total_execution_cost_usd": sum(
                    float(row["execution_cost_usd"]) for row in rows
                ),
            }
    for regime in REGIMES:
        available = {
            style: float(metrics[style][regime]["mean_turn_return_bps"])
            for style in styles
            if int(metrics[style][regime]["turns"]) > 0
        }
        regime_winners[regime] = None if not available else max(available, key=available.get)
    result = {
        "schemaVersion": "asset-simulation-oil-direction-regime-audit-v1",
        "seeds": list(seed_values),
        "period": {
            "start": f"{start[0]:04d}-{start[1]:02d}-H{start[2]}",
            "end": f"{end[0]:04d}-{end[1]:02d}-H{end[2]}",
        },
        "style_definition": {
            "only_changed_dimension": "continuation_reversion",
            "scores": STYLE_SCORES,
            "all_other_strategy_dimensions_fixed": True,
        },
        "regime_definition": {
            "evaluation_only": True,
            "used_by_strategy": False,
            "window_half_month_turns": 4,
            "trend": "abs four-turn return >=3.5% and directional efficiency >=55%",
            "turning": "first and second two-turn returns oppose and each exceed 1.5%",
            "range": "all other completed four-turn windows",
        },
        "regime_counts": dict(regime_counts),
        "metrics": metrics,
        "regime_winners": regime_winners,
        "per_seed_winners": per_seed_winners,
        "conclusion": {
            "all_regimes_observed": all(regime_counts[key] > 0 for key in REGIMES),
            "single_style_wins_all_regimes": len(
                {value for value in regime_winners.values() if value is not None}
            )
            == 1,
            "result_is_calibration_claim": False,
        },
        "informationPolicy": {
            "regime_label_available_to_decision": False,
            "hidden_future_available_to_decision": False,
            "same_forecast_vintages_across_styles": True,
            "ability_score_used_by_strategy": False,
        },
    }
    return result


def main() -> None:
    print(json.dumps(build_oil_direction_regime_audit(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
