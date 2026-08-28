"""Standalone cross-seed volatility audit for the global macro run.

This module is deliberately outside the Viewer and service cache.  It is a
calibration aid, not a second simulation owner.  Run it from ``airport`` with::

    py -3.13 -m asset_simulation.audit_volatility --seed-start 0 --seed-end 699 --years 60
    py -3.13 -m asset_simulation.audit_volatility --profile goal-c --years 60 --output <path>
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Iterable, Mapping, Sequence

from .model.engine import MODEL_VERSION, run_global_macro
from .model.registry import load_registered_assets


GOAL_C_SEGMENTS: tuple[tuple[str, int, int], ...] = (
    ("calibration", 0, 399),
    ("validation", 400, 499),
    ("holdout", 500, 699),
)
BOUND_FAIL_RATE_PCT = 0.5
BOUND_EXPLAIN_RATE_PCT = 0.1
SATURATION_BOUND_NAMES = {
    "output_gap_pct",
    "potential_growth_pct",
    "ig_spread_bps",
    "hy_spread_bps",
}
SATURATION_FAIL_RATE_PCT = 10.0
SATURATION_EXPLAIN_RATE_PCT = BOUND_EXPLAIN_RATE_PCT
GLOBAL_BOUND_FIELDS = {
    "headline_inflation_pct": "headline_inflation_pct",
    "core_inflation_pct": "core_inflation_pct",
    "inflation_expectation_pct": "inflation_expectation_pct",
    "policy_rate_pct": "global_policy_rate_pct",
    "term_premium_10y_pct": "term_premium_10y_pct",
    "output_gap_pct": "output_gap_pct",
    "ig_spread_bps": "global_investment_grade_spread_bps",
    "hy_spread_bps": "global_high_yield_spread_bps",
    "potential_growth_pct": "potential_growth_pct",
}
AUDIT_THRESHOLD_NOTES = {
    "process_bound_fail_rate_pct": BOUND_FAIL_RATE_PCT,
    "process_bound_explain_rate_pct": BOUND_EXPLAIN_RATE_PCT,
    "process_bound_fail_rule": (
        "Inflation, policy and term-premium process bounds fail only when hit rate "
        "> 0.5%; 0.1%–0.5% is warning and needs a causal explanation, not clipping."
    ),
    "saturation_bound_names": sorted(SATURATION_BOUND_NAMES),
    "saturation_fail_rate_pct": SATURATION_FAIL_RATE_PCT,
    "saturation_explain_rate_pct": SATURATION_EXPLAIN_RATE_PCT,
    "saturation_bound_rule": (
        "output_gap, potential_growth, IG and HY are ordinary-cycle rails. Hit rate "
        ">= 0.1% is warning; fail only above 10%, which would mean the clamp is the typical year."
    ),
}


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


def _summary(values: Sequence[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "mean": round(fmean(values), 6),
        "std": round(pstdev(values), 6),
        "p05": round(_percentile(values, 0.05), 6),
        "p50": round(_percentile(values, 0.50), 6),
        "p95": round(_percentile(values, 0.95), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
    }


def _changes(values: Sequence[float]) -> list[float]:
    return [current - previous for previous, current in zip(values, values[1:])]


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _runs(flags: Iterable[bool]) -> list[int]:
    durations: list[int] = []
    current = 0
    for flag in flags:
        if flag:
            current += 1
        elif current:
            durations.append(current)
            current = 0
    if current:
        durations.append(current)
    return durations


def _frequency(count: int, total: int) -> float:
    return round(100.0 * count / total, 6) if total else 0.0


def _at_bound(value: float, bound: Sequence[float], tolerance: float = 1e-7) -> bool:
    return abs(value - float(bound[0])) <= tolerance or abs(value - float(bound[1])) <= tolerance


def _at_low_bound(value: float, bound: Sequence[float], tolerance: float = 1e-7) -> bool:
    return abs(value - float(bound[0])) <= tolerance


def _at_high_bound(value: float, bound: Sequence[float], tolerance: float = 1e-7) -> bool:
    return abs(value - float(bound[1])) <= tolerance


def _count_nonfinite(value: Any) -> int:
    if value is None or isinstance(value, (str, bool)):
        return 0
    if isinstance(value, (int, float)):
        return int(not math.isfinite(float(value)))
    if isinstance(value, Mapping):
        return sum(_count_nonfinite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_count_nonfinite(item) for item in value)
    return 0


def _in_range(value: float, low: float, high: float) -> bool:
    return math.isfinite(value) and low <= value <= high


def _relative_gap(left: float, right: float) -> float:
    denominator = max(abs(left), abs(right), 1e-12)
    return abs(left - right) / denominator


def _status_from(failures: Sequence[Any], warnings: Sequence[Any]) -> str:
    if failures:
        return "fail"
    if warnings:
        return "warning"
    return "pass"


def _combine_status(statuses: Sequence[str]) -> str:
    if any(status == "fail" for status in statuses):
        return "fail"
    if any(status == "warning" for status in statuses):
        return "warning"
    return "pass"


def build_audit(*, seed_start: int, seed_end: int, years: int) -> dict[str, Any]:
    if seed_start < 0 or seed_end < seed_start:
        raise ValueError("seed range must be non-negative and ordered")
    assets = load_registered_assets()
    config = assets["config"]
    level_fields = {
        "growth": "realized_growth_pct",
        "potential_growth": "potential_growth_pct",
        "inflation": "headline_inflation_pct",
        "core_inflation": "core_inflation_pct",
        "inflation_expectation": "inflation_expectation_pct",
        "policy": "global_policy_rate_pct",
        "yield_2y": "global_2y_yield_pct",
        "yield_10y": "global_10y_yield_pct",
        "term_premium": "term_premium_10y_pct",
        "output_gap": "output_gap_pct",
        "fci": "global_financial_conditions_index",
    }
    levels = {name: [] for name in level_fields}
    changes = {name: [] for name in ("inflation", "policy", "yield_2y", "yield_10y")}
    event_counts = {
        "years": 0,
        "negative_growth": 0,
        "inflation_above_4": 0,
        "inflation_above_8": 0,
        "deflation": 0,
        "inversion": 0,
    }
    duration_values = {"inflation_above_4": [], "deflation": []}
    bounds_hits: dict[str, int] = {}
    bounds_edge_hits: dict[str, dict[str, int]] = {}
    nonfinite_count = 0

    for seed in range(seed_start, seed_end + 1):
        run = run_global_macro(seed=seed, years=years)
        rows = run.rows
        transition_rows = rows[1:]
        for name, field in level_fields.items():
            levels[name].extend(float(row[field]) for row in transition_rows if row[field] is not None)
        inflation = [float(row["headline_inflation_pct"]) for row in rows]
        policy = [float(row["global_policy_rate_pct"]) for row in rows]
        yield_2y = [float(row["global_2y_yield_pct"]) for row in rows]
        yield_10y = [float(row["global_10y_yield_pct"]) for row in rows]
        changes["inflation"].extend(_changes(inflation))
        changes["policy"].extend(_changes(policy))
        changes["yield_2y"].extend(_changes(yield_2y))
        changes["yield_10y"].extend(_changes(yield_10y))
        event_counts["years"] += len(transition_rows)
        event_counts["negative_growth"] += sum(float(row["realized_growth_pct"]) < 0.0 for row in transition_rows)
        event_counts["inflation_above_4"] += sum(float(row["headline_inflation_pct"]) > 4.0 for row in transition_rows)
        event_counts["inflation_above_8"] += sum(float(row["headline_inflation_pct"]) > 8.0 for row in transition_rows)
        event_counts["deflation"] += sum(float(row["headline_inflation_pct"]) < 0.0 for row in transition_rows)
        event_counts["inversion"] += sum(float(row["term_spread_10y_2y_pct"]) < 0.0 for row in transition_rows)
        duration_values["inflation_above_4"].extend(
            _runs(float(row["headline_inflation_pct"]) > 4.0 for row in transition_rows)
        )
        duration_values["deflation"].extend(
            _runs(float(row["headline_inflation_pct"]) < 0.0 for row in transition_rows)
        )
        nonfinite_count += _count_nonfinite(rows)
        for bound_name, row_name in GLOBAL_BOUND_FIELDS.items():
            bound = config["bounds"][bound_name]
            hits = 0
            low_hits = 0
            high_hits = 0
            for row in transition_rows:
                value = float(row[row_name])
                if _at_bound(value, bound):
                    hits += 1
                if _at_low_bound(value, bound):
                    low_hits += 1
                if _at_high_bound(value, bound):
                    high_hits += 1
            bounds_hits[bound_name] = bounds_hits.get(bound_name, 0) + hits
            edge = bounds_edge_hits.setdefault(bound_name, {"low": 0, "high": 0})
            edge["low"] += low_hits
            edge["high"] += high_hits

    observation_years = (seed_end - seed_start + 1) * years
    years_count = event_counts["years"]
    return {
        "schema_version": "asset-simulation-volatility-audit-v1",
        "seed_range": [seed_start, seed_end],
        "years": years,
        "world_count": seed_end - seed_start + 1,
        "observation_years": observation_years,
        "model_versions": {"global": MODEL_VERSION},
        "levels": {"global": {name: _summary(values) for name, values in levels.items()}},
        "annual_changes": {"global": {name: _summary(values) for name, values in changes.items()}},
        "event_counts": {"global": dict(event_counts)},
        "event_frequency_pct": {
            "global": {
                key: _frequency(value, years_count) for key, value in event_counts.items() if key != "years"
            }
        },
        "event_durations": {
            "global": {name: _summary(values) for name, values in duration_values.items()}
        },
        "bounds_hits": {"global": bounds_hits},
        "bounds_hit_rates_pct": {
            "global": {
                name: round(100.0 * hits / observation_years, 6) if observation_years else 0.0
                for name, hits in bounds_hits.items()
            }
        },
        "bounds_edge_hits": {"global": bounds_edge_hits},
        "nonfinite_counts": {"global": nonfinite_count},
    }


def _gate(gate: str, detail: str) -> dict[str, str]:
    return {"gate": gate, "detail": detail}


def evaluate_segment_gates(report: Mapping[str, Any]) -> dict[str, Any]:
    """Apply Goal C hard gates to one ``build_audit`` report."""

    failures: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    infos: list[dict[str, str]] = []
    explanations: list[str] = []
    levels = report["levels"]["global"]
    changes = report["annual_changes"]["global"]
    events = report["event_frequency_pct"]["global"]
    event_counts = report.get("event_counts", {}).get("global", {})
    nonfinite = report.get("nonfinite_counts", {})
    bound_rates = report.get("bounds_hit_rates_pct", {}).get("global", {})
    bound_edges = report.get("bounds_edge_hits", {}).get("global", {})

    if any(int(count) for count in nonfinite.values()):
        failures.append(_gate("runability", f"non-finite values present: {dict(nonfinite)}"))

    bound_warnings = 0
    for name, rate in bound_rates.items():
        hits = report["bounds_hits"]["global"][name]
        edge = bound_edges.get(name, {})
        edge_note = ""
        if edge:
            edge_note = f" low-edge {edge.get('low', 0)} / high-edge {edge.get('high', 0)}."
        if name in SATURATION_BOUND_NAMES:
            if rate > SATURATION_FAIL_RATE_PCT:
                failures.append(
                    _gate("bounds_saturation", f"global.{name} hit rate {rate}% ({hits} hits) exceeds {SATURATION_FAIL_RATE_PCT}%")
                )
            elif rate >= SATURATION_EXPLAIN_RATE_PCT:
                bound_warnings += 1
                warnings.append(
                    _gate(
                        "bounds_saturation",
                        f"global.{name} hit rate {rate}% ({hits} hits); ordinary-cycle rail, not inflation-tail clipping",
                    )
                )
                explanations.append(
                    f"global.{name} hit {hits} times, rate {rate}% is above {SATURATION_EXPLAIN_RATE_PCT}% "
                    f"and below the {SATURATION_FAIL_RATE_PCT}% saturation fail line.{edge_note}"
                )
            continue
        if rate > BOUND_FAIL_RATE_PCT:
            failures.append(_gate("bounds", f"global.{name} hit rate {rate}% ({hits} hits)"))
        elif rate >= BOUND_EXPLAIN_RATE_PCT:
            bound_warnings += 1
            warnings.append(_gate("bounds", f"global.{name} hit rate {rate}% ({hits} hits); explain causal chain, not clipping"))
            explanations.append(
                f"global.{name} hit {hits} times, rate {rate}% is above {BOUND_EXPLAIN_RATE_PCT}% "
                f"but below the {BOUND_FAIL_RATE_PCT}% fail line.{edge_note}"
            )

    mean = float(levels["inflation"]["mean"])
    if not _in_range(mean, 1.8, 2.7):
        failures.append(_gate("inflation_mean", f"global headline mean {mean}% is outside 1.8–2.7"))
    std = float(changes["inflation"]["std"])
    if not _in_range(std, 0.30, 0.75):
        failures.append(_gate("inflation_change_std", f"global inflation annual-change std {std}pp is outside 0.30–0.75"))
    yield_std = float(changes["yield_10y"]["std"])
    if not _in_range(yield_std, 0.20, 0.75):
        failures.append(_gate("yield_10y_change_std", f"global 10Y change std {yield_std}pp is outside 0.20–0.75"))

    above_4 = float(events["inflation_above_4"])
    deflation = float(events["deflation"])
    above_8 = float(events.get("inflation_above_8", 0.0))
    above_4_count = int(event_counts.get("inflation_above_4", 1 if above_4 else 0))
    deflation_count = int(event_counts.get("deflation", 1 if deflation else 0))
    if above_8 >= 1.0:
        failures.append(_gate("inflation_tail", f"global inflation above 8% is {above_8}%, too frequent for ordinary worlds"))
    elif above_8 >= 0.2:
        warnings.append(_gate("inflation_tail", f"global inflation above 8% is {above_8}%"))
    if above_4_count <= 0 or above_4 <= 0.0:
        warnings.append(_gate("inflation_tail", "no headline inflation year above 4% in global"))
    if deflation_count <= 0 or deflation <= 0.0:
        warnings.append(_gate("inflation_tail", "no headline inflation year below 0% in global"))

    return {
        "status": _status_from(failures, warnings),
        "failures": failures,
        "warnings": warnings,
        "infos": infos,
        "explanations": explanations,
        "bound_warning_count": bound_warnings,
    }


def _lookup(report: Mapping[str, Any], path: str) -> float:
    current: Any = report
    for part in path.split("."):
        current = current[part]
    return float(current)


def summarize_goal_c_drift(segment_reports: Mapping[str, Mapping[str, Any] | None]) -> dict[str, Any]:
    """Compare calibration / validation / holdout without a fourth 0–699 rerun."""

    failures: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    present = {name: report for name, report in segment_reports.items() if report is not None}
    if len(present) < 2:
        return {
            "status": "fail",
            "failures": [_gate("drift", "fewer than two segments produced an audit; cannot check drift")],
            "warnings": [],
            "mean_max_abs_diff_pp": {},
            "std_relative_gaps": {},
            "tail_presence": {},
            "frequency_ratios": {},
        }

    names = tuple(present)
    mean_paths = {
        "global.growth": "levels.global.growth.mean",
        "global.inflation": "levels.global.inflation.mean",
        "global.policy": "levels.global.policy.mean",
        "global.yield_10y": "levels.global.yield_10y.mean",
    }
    mean_max_abs_diff: dict[str, float] = {}
    for label, path in mean_paths.items():
        values = [_lookup(report, path) for report in present.values()]
        spread = round(max(values) - min(values), 6)
        mean_max_abs_diff[label] = spread
        if spread > 0.25:
            failures.append(_gate("drift_mean", f"{label} segment means differ by {spread}pp (> 0.25pp)"))

    std_paths = {
        "global.inflation_change": "annual_changes.global.inflation.std",
        "global.yield_10y_change": "annual_changes.global.yield_10y.std",
    }
    std_relative_gaps: dict[str, float] = {}
    std_over_30 = 0
    for label, path in std_paths.items():
        values = [_lookup(report, path) for report in present.values()]
        gap = 0.0
        for index, left in enumerate(values):
            for right in values[index + 1 :]:
                gap = max(gap, _relative_gap(left, right))
        gap = round(gap, 6)
        std_relative_gaps[label] = gap
        if gap > 0.50:
            failures.append(_gate("drift_std", f"{label} relative std gap {gap:.1%} exceeds 50%"))
        elif gap > 0.30:
            std_over_30 += 1
            warnings.append(_gate("drift_std", f"{label} relative std gap {gap:.1%} exceeds 30%"))
    if std_over_30 >= 3:
        failures.append(_gate("drift_std", f"{std_over_30} series have relative std gaps above 30%"))

    tail_keys = (
        ("global.inflation_above_4", "event_frequency_pct.global.inflation_above_4"),
        ("global.deflation", "event_frequency_pct.global.deflation"),
    )
    tail_presence: dict[str, dict[str, float]] = {}
    calibration = present.get("calibration")
    for label, path in tail_keys:
        by_segment = {name: _lookup(report, path) for name, report in present.items()}
        tail_presence[label] = by_segment
        if calibration is not None:
            cal_value = by_segment.get("calibration", 0.0)
            others = [value for name, value in by_segment.items() if name != "calibration"]
            if cal_value > 0.0 and others and all(value <= 0.0 for value in others):
                failures.append(_gate("drift_tail", f"{label} appears in calibration but disappears in later segments"))

    frequency_paths = {
        "global.inflation_above_4": "event_frequency_pct.global.inflation_above_4",
        "global.deflation": "event_frequency_pct.global.deflation",
        "global.inversion": "event_frequency_pct.global.inversion",
    }
    frequency_ratios: dict[str, float] = {}
    if calibration is not None:
        for label, path in frequency_paths.items():
            cal_value = max(_lookup(calibration, path), 1e-9)
            worst = 1.0
            for name, report in present.items():
                if name == "calibration":
                    continue
                ratio = _lookup(report, path) / cal_value
                inverse = cal_value / max(_lookup(report, path), 1e-9)
                worst = max(worst, ratio, inverse)
            frequency_ratios[label] = round(worst, 6)
            if worst > 4.0:
                failures.append(_gate("drift_frequency", f"{label} segment frequency ratio {worst:.2f}x exceeds 4x"))
            elif worst > 2.5:
                warnings.append(_gate("drift_frequency", f"{label} segment frequency ratio {worst:.2f}x exceeds 2.5x"))

    return {
        "status": _status_from(failures, warnings),
        "failures": failures,
        "warnings": warnings,
        "compared_segments": list(names),
        "mean_max_abs_diff_pp": mean_max_abs_diff,
        "std_relative_gaps": std_relative_gaps,
        "tail_presence": tail_presence,
        "frequency_ratios": frequency_ratios,
    }


def build_goal_c_audit(
    *,
    years: int = 60,
    segments: Sequence[tuple[str, int, int]] | None = None,
) -> dict[str, Any]:
    """Run calibration / validation / holdout once each; do not rerun 0–699 as a fourth block."""

    resolved = tuple(segments or GOAL_C_SEGMENTS)
    segment_reports: dict[str, dict[str, Any] | None] = {}
    segment_gates: dict[str, dict[str, Any]] = {}
    for name, seed_start, seed_end in resolved:
        try:
            report = build_audit(seed_start=seed_start, seed_end=seed_end, years=years)
            segment_reports[name] = report
            segment_gates[name] = evaluate_segment_gates(report)
        except Exception as exc:
            segment_reports[name] = None
            segment_gates[name] = {
                "status": "fail",
                "failures": [_gate("runability", f"{type(exc).__name__}: {exc}")],
                "warnings": [],
                "infos": [],
                "explanations": [f"{name} did not complete: {type(exc).__name__}: {exc}"],
                "bound_warning_count": 0,
            }
    drift = summarize_goal_c_drift(segment_reports)
    overall_status = _combine_status([gate["status"] for gate in segment_gates.values()] + [drift["status"]])
    return {
        "schema_version": "asset-simulation-volatility-audit-goal-c-v1",
        "profile": "goal-c",
        "years": years,
        "holdout_used_for_parameter_selection": False,
        "threshold_notes": dict(AUDIT_THRESHOLD_NOTES),
        "segments": {
            name: {
                "seed_range": [seed_start, seed_end],
                "world_count": seed_end - seed_start + 1,
                "audit": segment_reports[name],
                "gates": segment_gates[name],
            }
            for name, seed_start, seed_end in resolved
        },
        "drift": drift,
        "summary": {
            "status": overall_status,
            "segment_status": {name: segment_gates[name]["status"] for name, _, _ in resolved},
            "drift_status": drift["status"],
            "world_count_total": sum(seed_end - seed_start + 1 for _, seed_start, seed_end in resolved),
            "failure_count": sum(len(segment_gates[name]["failures"]) for name, _, _ in resolved) + len(drift["failures"]),
            "warning_count": sum(len(segment_gates[name]["warnings"]) for name, _, _ in resolved) + len(drift["warnings"]),
            "info_count": sum(len(segment_gates[name].get("infos", [])) for name, _, _ in resolved),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--seed-end", type=int, default=99)
    parser.add_argument("--years", type=int, default=60)
    parser.add_argument("--output", type=str)
    parser.add_argument(
        "--profile",
        choices=("goal-c",),
        default=None,
        help="goal-c runs calibration 0–399, validation 400–499 and holdout 500–699 once each",
    )
    args = parser.parse_args()
    if args.profile == "goal-c":
        report: dict[str, Any] = build_goal_c_audit(years=args.years)
    else:
        report = build_audit(seed_start=args.seed_start, seed_end=args.seed_end, years=args.years)
    payload = json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
