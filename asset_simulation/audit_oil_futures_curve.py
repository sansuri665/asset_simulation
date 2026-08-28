"""Standalone cross-seed audit for the read-only oil futures term structure.

This module reuses the registered oil owner and its information cutoff.  It is
only a calibration/reporting aid; it does not publish a second market price.
Run from ``airport`` with::

    py -3.13 -m asset_simulation.audit_oil_futures_curve --profile balance
    py -3.13 -m asset_simulation.audit_oil_futures_curve --seed-start 0 --seed-end 99
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from statistics import fmean, median
from typing import Any, Iterable, Mapping, Sequence

from .model.engine import run_global_macro
from .model import oil_futures_overlay as futures
from .model.registry import load_registered_assets


BALANCE_SEGMENTS: tuple[tuple[str, int, int], ...] = (
    ("calibration", 0, 99),
    ("validation", 100, 149),
    ("holdout", 150, 199),
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


def _summary(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "p05": None, "p50": None, "p95": None}
    return {
        "count": len(values),
        "mean": round(fmean(values), 6),
        "p05": round(_percentile(values, 0.05), 6),
        "p50": round(_percentile(values, 0.50), 6),
        "p95": round(_percentile(values, 0.95), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
    }


def _shares(states: Iterable[str]) -> dict[str, float]:
    counts = Counter(states)
    total = sum(counts.values())
    return {
        state: round(100.0 * counts[state] / total, 6) if total else 0.0
        for state in ("contango", "backwardation", "flat")
    }


def _conditional_report(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    states = [str(item["state"]) for item in observations]
    spreads = [float(item["far_front_spread_pct"]) for item in observations]
    return {
        "observation_count": len(observations),
        "state_share_pct": _shares(states),
        "far_front_spread_pct": _summary(spreads),
    }


def _meets(value: float | int | None, *, minimum: float | None = None, maximum: float | None = None) -> bool:
    if value is None:
        return False
    numeric = float(value)
    return (minimum is None or numeric >= minimum) and (
        maximum is None or numeric <= maximum
    )


def audit_seed_range(
    *,
    seed_start: int,
    seed_end: int,
    years: int = 60,
    observation_start_year: int = 2030,
) -> dict[str, Any]:
    """Audit an inclusive Seed range without invoking liquidity or player state."""

    if seed_start < 0 or seed_end < seed_start:
        raise ValueError("seed range must be non-negative and ordered")
    assets = load_registered_assets()
    config = assets["oil_futures_overlay_config"]
    curve_config = config["curve"]
    threshold = float(curve_config["flat_curve_threshold_pct"])
    observations: list[dict[str, Any]] = []
    regime_durations: dict[str, list[int]] = {
        "contango": [],
        "backwardation": [],
        "flat": [],
    }
    per_seed_mean_spreads: list[float] = []
    expiry_basis_errors: list[float] = []
    nonpositive_price_count = 0

    for seed in range(seed_start, seed_end + 1):
        run = run_global_macro(seed=seed, years=years)
        end_year = int(run.rows[-1]["year"])
        references = futures._reference_half_turns(
            run,
            as_of_year=end_year,
            as_of_month=12,
            as_of_half=2,
        )
        closes: list[float] = []
        factors: dict[str, float] | None = None
        current_state: str | None = None
        current_duration = 0
        seed_spreads: list[float] = []
        for reference in references:
            year = int(reference["year"])
            month = int(reference["month"])
            half = int(reference["half"])
            spot = float(reference["close"])
            closes.append(spot)
            macro_row = futures._latest_completed_macro_row(run.rows, year)
            targets = futures._curve_targets(
                macro_row,
                closes,
                month=month,
                curve_config=curve_config,
            )
            factors = futures._advance_curve_factors(
                seed=seed,
                month_address=futures._half_turn_serial(year, month, half),
                previous=factors,
                targets=targets,
                curve_config=curve_config,
            )
            contracts = futures._curve_contracts(
                as_of_year=year,
                as_of_month=month,
                as_of_half=half,
                spot=spot,
                factors=factors,
                config=config,
            )
            expiry_basis_errors.extend(
                abs(float(item["basis_pct"]))
                for item in contracts
                if float(item["months_to_expiry"]) == 0.0
            )
            nonpositive_price_count += sum(
                float(item["futures_price_usd"]) <= 0.0 for item in contracts
            )
            if year < observation_start_year:
                continue
            state = futures._curve_state(contracts, threshold_pct=threshold)
            spread = 100.0 * (
                float(contracts[-1]["futures_price_usd"])
                / float(contracts[0]["futures_price_usd"])
                - 1.0
            )
            observations.append(
                {
                    "seed": seed,
                    "year": year,
                    "month": month,
                    "half": half,
                    "state": state,
                    "far_front_spread_pct": spread,
                    "inventory_tightness_index": float(
                        targets["inventory_tightness_index"]
                    ),
                    "flow_gap_pct": float(targets["flow_gap_pct"]),
                    "convenience_yield_pct": float(
                        targets["convenience_yield_pct"]
                    ),
                }
            )
            seed_spreads.append(spread)
            if state == current_state:
                current_duration += 1
            else:
                if current_state is not None:
                    regime_durations[current_state].append(current_duration)
                current_state = state
                current_duration = 1
        if current_state is not None:
            regime_durations[current_state].append(current_duration)
        per_seed_mean_spreads.append(fmean(seed_spreads))

    loose_inventory = [
        item for item in observations if float(item["inventory_tightness_index"]) <= -4.0
    ]
    balanced_inventory = [
        item for item in observations if abs(float(item["inventory_tightness_index"])) < 2.0
    ]
    tight_inventory = [
        item for item in observations if float(item["inventory_tightness_index"]) >= 4.0
    ]
    overall = _conditional_report(observations)
    conditional = {
        "loose_inventory_at_or_below_minus_4": _conditional_report(loose_inventory),
        "balanced_inventory_abs_below_2": _conditional_report(balanced_inventory),
        "tight_inventory_at_or_above_4": _conditional_report(tight_inventory),
    }
    overall_shares = overall["state_share_pct"]
    loose_shares = conditional["loose_inventory_at_or_below_minus_4"]["state_share_pct"]
    tight_shares = conditional["tight_inventory_at_or_above_4"]["state_share_pct"]
    spread_summary = overall["far_front_spread_pct"]
    gate_values = {
        "overall_contango_share_between_40_and_70_pct": (
            40.0 <= overall_shares["contango"] <= 70.0
        ),
        "overall_backwardation_share_between_25_and_55_pct": (
            25.0 <= overall_shares["backwardation"] <= 55.0
        ),
        "overall_flat_share_between_1_and_15_pct": (
            1.0 <= overall_shares["flat"] <= 15.0
        ),
        "loose_inventory_contango_share_at_least_90_pct": (
            loose_shares["contango"] >= 90.0
        ),
        "tight_inventory_backwardation_share_at_least_75_pct": (
            tight_shares["backwardation"] >= 75.0
        ),
        "loose_inventory_mean_spread_at_least_2_pct": _meets(
            conditional["loose_inventory_at_or_below_minus_4"][
                "far_front_spread_pct"
            ]["mean"],
            minimum=2.0,
        ),
        "tight_inventory_mean_spread_at_most_minus_1_pct": _meets(
            conditional["tight_inventory_at_or_above_4"][
                "far_front_spread_pct"
            ]["mean"],
            maximum=-1.0,
        ),
        "ordinary_far_front_tail_inside_25_pct": (
            abs(float(spread_summary["min"])) <= 25.0
            and abs(float(spread_summary["max"])) <= 25.0
        ),
        "expiry_basis_converges_to_zero": (
            max(expiry_basis_errors, default=0.0) <= 1e-10
        ),
        "all_futures_prices_positive": nonpositive_price_count == 0,
    }
    return {
        "model_version": str(config["model_version"]),
        "config_id": str(config["config_id"]),
        "seed_start": seed_start,
        "seed_end": seed_end,
        "seed_count": seed_end - seed_start + 1,
        "years": years,
        "observation_start_year": observation_start_year,
        "overall": overall,
        "conditional": conditional,
        "convenience_yield_pct": _summary(
            [float(item["convenience_yield_pct"]) for item in observations]
        ),
        "per_seed_mean_far_front_spread_pct": _summary(per_seed_mean_spreads),
        "regime_duration_half_turns": {
            state: {
                **_summary(values),
                "median": round(median(values), 6) if values else None,
            }
            for state, values in regime_durations.items()
        },
        "maximum_abs_expiry_basis_pct": max(expiry_basis_errors, default=0.0),
        "nonpositive_futures_price_count": nonpositive_price_count,
        "gates": {"passed": all(gate_values.values()), "checks": gate_values},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("balance",))
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--seed-end", type=int, default=99)
    parser.add_argument("--years", type=int, default=60)
    parser.add_argument("--observation-start-year", type=int, default=2030)
    args = parser.parse_args()
    if args.profile == "balance":
        reports = {
            name: audit_seed_range(
                seed_start=start,
                seed_end=end,
                years=args.years,
                observation_start_year=args.observation_start_year,
            )
            for name, start, end in BALANCE_SEGMENTS
        }
        payload: dict[str, Any] = {
            "profile": "balance",
            "segments": reports,
            "passed": all(report["gates"]["passed"] for report in reports.values()),
        }
    else:
        payload = audit_seed_range(
            seed_start=args.seed_start,
            seed_end=args.seed_end,
            years=args.years,
            observation_start_year=args.observation_start_year,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
