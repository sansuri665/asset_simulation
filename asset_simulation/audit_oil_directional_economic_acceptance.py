"""Merge acceptance for the integrated directional-oil v1.5 candidate.

The raw audit remains authoritative for accounting, turnover, execution,
thesis, deployment and stale-position gates.  This layer replaces only the
legacy single-sample orientation-winner threshold with explicit development,
validation and regime ecology checks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .model.registry import sha256_json


DEV_SEEDS = frozenset(range(0, 8))
VALIDATION_SEEDS = frozenset(range(8, 16))
LEGACY_ORIENTATION_GATE = "no_orientation_score_wins_more_than_half_of_cells"


def _orientation_ecology(
    rows: Sequence[Mapping[str, Any]], seeds: frozenset[int]
) -> dict[str, Any]:
    cells: dict[tuple[int, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        if (
            str(row.get("controlled_axis")) == "continuation_reversion"
            and int(row["seed"]) in seeds
        ):
            cells.setdefault(
                (int(row["seed"]), str(row["forecast_band"])), []
            ).append(row)
    winner_counts: dict[str, int] = {}
    for cell in cells.values():
        winner = max(cell, key=lambda item: float(item["cagr_pct"]))
        score = str(float(winner["controlled_score"]))
        winner_counts[score] = winner_counts.get(score, 0) + 1
    total = sum(winner_counts.values())
    winning_scores = sorted(
        float(score) for score, count in winner_counts.items() if count > 0
    )
    return {
        "cell_count": total,
        "winner_counts": winner_counts,
        "largest_winner_share": (
            0.0 if total == 0 else max(winner_counts.values()) / total
        ),
        "winning_scores": winning_scores,
        "winner_score_count": len(winning_scores),
        "has_reversion_side_winner": any(score < 50.0 for score in winning_scores),
        "has_continuation_side_winner": any(
            score > 50.0 for score in winning_scores
        ),
    }


def _medium_invalidated(
    rows: Sequence[Mapping[str, Any]], seeds: frozenset[int]
) -> float:
    values = sorted(
        float(row["thesis_status_share_pct"]["invalidated"])
        for row in rows
        if str(row.get("controlled_axis")) == "continuation_reversion"
        and float(row.get("controlled_score", -1.0)) == 50.0
        and str(row.get("forecast_band")) == "medium"
        and int(row["seed"]) in seeds
    )
    if not values:
        return 0.0
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return 0.5 * (values[midpoint - 1] + values[midpoint])


def evaluate_directional_economic_acceptance(
    calibration: Mapping[str, Any], regime: Mapping[str, Any]
) -> dict[str, Any]:
    rows = list(calibration.get("scenarios", ()))
    if not rows:
        raise ValueError("directional economic acceptance requires scenario rows")

    raw_gates = {
        str(name): bool(value)
        for name, value in dict(calibration.get("gates", {})).items()
    }
    if not raw_gates:
        raise ValueError("directional economic report has no raw gates")
    retained_raw_gates = {
        name: passed
        for name, passed in raw_gates.items()
        if name != LEGACY_ORIENTATION_GATE
    }

    development = _orientation_ecology(rows, DEV_SEEDS)
    validation = _orientation_ecology(rows, VALIDATION_SEEDS)
    combined = _orientation_ecology(rows, DEV_SEEDS | VALIDATION_SEEDS)
    development_invalidated = _medium_invalidated(rows, DEV_SEEDS)
    validation_invalidated = _medium_invalidated(rows, VALIDATION_SEEDS)

    metrics = regime["metrics"]
    trend_reversion = float(metrics["reversion"]["trend"]["mean_turn_return_bps"])
    trend_balanced = float(metrics["balanced"]["trend"]["mean_turn_return_bps"])
    trend_continuation = float(
        metrics["continuation"]["trend"]["mean_turn_return_bps"]
    )
    range_reversion = float(metrics["reversion"]["range"]["mean_turn_return_bps"])
    range_balanced = float(metrics["balanced"]["range"]["mean_turn_return_bps"])
    range_continuation = float(
        metrics["continuation"]["range"]["mean_turn_return_bps"]
    )
    turning_reversion = float(
        metrics["reversion"]["turning"]["mean_turn_return_bps"]
    )
    turning_balanced = float(
        metrics["balanced"]["turning"]["mean_turn_return_bps"]
    )
    turning_continuation = float(
        metrics["continuation"]["turning"]["mean_turn_return_bps"]
    )

    gates = {
        **retained_raw_gates,
        "combined_orientation_has_64_cells": combined["cell_count"] == 64,
        "combined_largest_orientation_winner_share_not_above_65_pct": (
            combined["largest_winner_share"] <= 0.65
        ),
        "combined_at_least_three_orientation_scores_win": (
            combined["winner_score_count"] >= 3
        ),
        "development_has_winners_on_both_sides_of_neutral": (
            development["has_reversion_side_winner"]
            and development["has_continuation_side_winner"]
        ),
        "validation_has_winners_on_both_sides_of_neutral": (
            validation["has_reversion_side_winner"]
            and validation["has_continuation_side_winner"]
        ),
        "development_medium_thesis_invalidated_5_to_30_pct": (
            5.0 <= development_invalidated <= 30.0
        ),
        "validation_medium_thesis_invalidated_5_to_30_pct": (
            5.0 <= validation_invalidated <= 30.0
        ),
        "trend_continuation_is_competitive_and_beats_reversion": (
            trend_continuation >= trend_balanced - 35.0
            and trend_continuation > trend_reversion
        ),
        "range_reversion_is_competitive": (
            range_reversion >= max(range_balanced, range_continuation) - 10.0
        ),
        "turning_reversion_is_clear_winner": (
            turning_reversion > max(turning_balanced, turning_continuation)
        ),
    }
    failed = [name for name, passed in gates.items() if not passed]
    result = {
        "schemaVersion": "asset-simulation-oil-directional-economic-acceptance-v5",
        "ok": not failed,
        "raw_report_ok": bool(calibration.get("ok", False)),
        "legacy_orientation_gate_is_diagnostic_only": LEGACY_ORIENTATION_GATE,
        "orientation": {
            "development_seeds": sorted(DEV_SEEDS),
            "validation_seeds": sorted(VALIDATION_SEEDS),
            "development": development,
            "validation": validation,
            "combined": combined,
        },
        "mediumThesisInvalidatedOccupancyPct": {
            "development": development_invalidated,
            "validation": validation_invalidated,
        },
        "regimeMeanTurnReturnBps": {
            "trend": {
                "reversion": trend_reversion,
                "balanced": trend_balanced,
                "continuation": trend_continuation,
            },
            "range": {
                "reversion": range_reversion,
                "balanced": range_balanced,
                "continuation": range_continuation,
            },
            "turning": {
                "reversion": turning_reversion,
                "balanced": turning_balanced,
                "continuation": turning_continuation,
            },
        },
        "rawDiagnostics": calibration.get("diagnostics", {}),
        "regimeWinners": regime.get("regime_winners", {}),
        "gates": gates,
        "failed_gates": failed,
    }
    return {
        "identity": {"result_hash": sha256_json(result)},
        **result,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--regime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
    regime = json.loads(args.regime.read_text(encoding="utf-8"))
    report = evaluate_directional_economic_acceptance(calibration, regime)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        raise SystemExit(
            "directional economic acceptance failed: "
            + ", ".join(report["failed_gates"])
        )


if __name__ == "__main__":
    main()
