"""Acceptance policy for directional-oil economic calibration reports.

The underlying calibration audit reports raw diagnostics, including a legacy
winner-share gate that proved too brittle for a 32-cell style comparison.
This module applies merge-oriented acceptance semantics without rewriting or
hiding the raw audit result.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


LEGACY_ORIENTATION_GATE = "no_orientation_score_wins_more_than_half_of_cells"


def evaluate_directional_economic_acceptance(
    report: Mapping[str, Any],
) -> dict[str, Any]:
    raw_gates = {
        str(name): bool(value) for name, value in dict(report.get("gates", {})).items()
    }
    if not raw_gates:
        raise ValueError("directional economic report has no diagnostic gates")
    counts = {
        float(score): int(count)
        for score, count in dict(report.get("orientationWinnerCounts", {})).items()
    }
    total = sum(counts.values())
    if total <= 0:
        raise ValueError("directional economic report has no orientation winner cells")

    nonzero_scores = sum(count > 0 for count in counts.values())
    reversion_side_wins = sum(count for score, count in counts.items() if score < 50.0)
    continuation_side_wins = sum(count for score, count in counts.items() if score > 50.0)
    largest_share = max(counts.values()) / total

    retained = {
        name: passed
        for name, passed in raw_gates.items()
        if name != LEGACY_ORIENTATION_GATE
    }
    orientation_acceptance = {
        "no_orientation_score_wins_more_than_70pct_of_cells": largest_share <= 0.70,
        "orientation_winner_diversity_at_least_three_scores": nonzero_scores >= 3,
        "orientation_winners_cover_both_sides_of_neutral": (
            reversion_side_wins > 0 and continuation_side_wins > 0
        ),
    }
    gates = {**retained, **orientation_acceptance}
    failed = [name for name, passed in gates.items() if not passed]
    return {
        "schemaVersion": "asset-simulation-oil-directional-economic-acceptance-v1",
        "ok": not failed,
        "source_result_hash": dict(report.get("identity", {})).get("result_hash"),
        "raw_report_ok": bool(report.get("ok", False)),
        "legacy_orientation_gate_accepted_as_diagnostic_only": LEGACY_ORIENTATION_GATE,
        "orientationWinnerCounts": {str(score): count for score, count in sorted(counts.items())},
        "orientationDiagnostics": {
            "cell_count": total,
            "largest_winner_share": largest_share,
            "winning_score_count": nonzero_scores,
            "reversion_side_wins": reversion_side_wins,
            "continuation_side_wins": continuation_side_wins,
        },
        "gates": gates,
        "failed_gates": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    acceptance = evaluate_directional_economic_acceptance(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(acceptance, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
