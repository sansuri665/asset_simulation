"""Semantic acceptance layer for directional-oil economic calibration.

Raw calibration metrics remain available for diagnosis.  Merge acceptance
evaluates development, validation, and combined orientation ecology instead
of tuning to one brittle winner-share threshold.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .model.registry import sha256_json


DEV_SEEDS = frozenset(range(0, 8))
VALIDATION_SEEDS = frozenset(range(8, 16))


def _orientation_ecology(
    rows: Sequence[Mapping[str, Any]], seeds: frozenset[int]
) -> dict[str, Any]:
    selected = [
        row
        for row in rows
        if str(row.get("controlled_axis")) == "continuation_reversion"
        and int(row["seed"]) in seeds
    ]
    cells: dict[tuple[int, str], list[Mapping[str, Any]]] = {}
    for row in selected:
        cells.setdefault((int(row["seed"]), str(row["forecast_band"])), []).append(row)
    winner_counts: dict[str, int] = {}
    for cell in cells.values():
        winner = max(cell, key=lambda item: float(item["cagr_pct"]))
        key = str(float(winner["controlled_score"]))
        winner_counts[key] = winner_counts.get(key, 0) + 1
    total = sum(winner_counts.values())
    winning_scores = sorted(float(key) for key, value in winner_counts.items() if value > 0)
    largest = 0.0 if total == 0 else max(winner_counts.values()) / total
    return {
        "cell_count": total,
        "winner_counts": winner_counts,
        "largest_winner_share": largest,
        "winning_scores": winning_scores,
        "winner_score_count": len(winning_scores),
        "has_reversion_side_winner": any(score < 50.0 for score in winning_scores),
        "has_continuation_side_winner": any(score > 50.0 for score in winning_scores),
    }


def _medium_invalidated(
    rows: Sequence[Mapping[str, Any]], seeds: frozenset[int]
) -> float:
    values = sorted(
        float(row["thesis_status_share_pct"]["invalidated"])
        for row in rows
        if str(row.get("controlled_axis")) == "continuation_reversion"
        and float(row.get("controlled_score", -1)) == 50.0
        and str(row.get("forecast_band")) == "medium"
        and int(row["seed"]) in seeds
    )
    if not values:
        return 0.0
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return 0.5 * (values[midpoint - 1] + values[midpoint])


def build_directional_economic_acceptance(
    calibration: Mapping[str, Any],
    regime: Mapping[str, Any],
) -> dict[str, Any]:
    rows = list(calibration.get("scenarios", ()))
    if not rows:
        raise ValueError("directional economic acceptance requires scenario rows")
    dev = _orientation_ecology(rows, DEV_SEEDS)
    validation = _orientation_ecology(rows, VALIDATION_SEEDS)
    combined = _orientation_ecology(rows, DEV_SEEDS | VALIDATION_SEEDS)
    dev_invalidated = _medium_invalidated(rows, DEV_SEEDS)
    validation_invalidated = _medium_invalidated(rows, VALIDATION_SEEDS)
    combined_values = sorted((dev_invalidated, validation_invalidated))
    combined_invalidated = 0.5 * (combined_values[0] + combined_values[1])

    metrics = regime["metrics"]
    trend_reversion = float(metrics["reversion10"]["trend"]["mean_turn_return_bps"])
    trend_balanced = float(metrics["balanced50"]["trend"]["mean_turn_return_bps"])
    trend_continuation = float(metrics["continuation90"]["trend"]["mean_turn_return_bps"])
    range_reversion = float(metrics["reversion10"]["range"]["mean_turn_return_bps"])
    range_continuation = float(metrics["continuation90"]["range"]["mean_turn_return_bps"])
    turning_reversion = float(metrics["reversion10"]["turning"]["mean_turn_return_bps"])
    turning_continuation = float(metrics["continuation90"]["turning"]["mean_turn_return_bps"])

    raw_gates = dict(calibration.get("gates", {}))
    gates = {
        **raw_gates,
        "combined_orientation_has_64_cells": combined["cell_count"] == 64,
        "combined_largest_orientation_winner_share_not_above_65_pct": (
            combined["largest_winner_share"] <= 0.65
        ),
        "combined_at_least_three_orientation_scores_win": (
            combined["winner_score_count"] >= 3
        ),
        "development_has_winners_on_both_sides_of_neutral": (
            dev["has_reversion_side_winner"]
            and dev["has_continuation_side_winner"]
        ),
        "validation_has_winners_on_both_sides_of_neutral": (
            validation["has_reversion_side_winner"]
            and validation["has_continuation_side_winner"]
        ),
        "development_medium_thesis_invalidated_3_to_35_pct": (
            3.0 <= dev_invalidated <= 35.0
        ),
        "validation_medium_thesis_invalidated_3_to_35_pct": (
            3.0 <= validation_invalidated <= 35.0
        ),
        "trend_does_not_structurally_penalize_continuation": (
            trend_continuation >= trend_balanced - 5.0
            and trend_continuation > trend_reversion
        ),
        "range_preserves_reversion_advantage": range_reversion > range_continuation,
        "turning_preserves_reversion_advantage": (
            turning_reversion > turning_continuation
        ),
    }
    result = {
        "ok": all(gates.values()),
        "schemaVersion": "asset-simulation-oil-directional-economic-acceptance-v2",
        "orientation": {
            "development_seeds": sorted(DEV_SEEDS),
            "validation_seeds": sorted(VALIDATION_SEEDS),
            "development": dev,
            "validation": validation,
            "combined": combined,
        },
        "mediumThesisInvalidatedOccupancyPct": {
            "development": dev_invalidated,
            "validation": validation_invalidated,
            "combined_reference": combined_invalidated,
        },
        "rawDiagnostics": calibration.get("diagnostics", {}),
        "regimeWinners": regime.get("regime_winners", {}),
        "gates": gates,
    }
    return {"identity": {"result_hash": sha256_json(result)}, **result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--regime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
    regime = json.loads(args.regime.read_text(encoding="utf-8"))
    report = build_directional_economic_acceptance(calibration, regime)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failed = [name for name, passed in report["gates"].items() if not passed]
    if failed:
        raise SystemExit("directional economic acceptance failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()
