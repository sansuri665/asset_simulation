"""Audit the experimental Gulf-East Asia VLCC spot-market prototype."""

from __future__ import annotations

import argparse
import json
import statistics
from typing import Any

from .model.vlcc_spot_market import run_seeded_gulf_east_asia_vlcc_spot_market


def _parse_seeds(raw: str) -> tuple[int, ...]:
    values = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("at least one seed is required")
    if any(value < 0 for value in values):
        raise ValueError("seeds must be non-negative")
    return values


def audit_vlcc_spot_market(
    seeds: tuple[int, ...] = (0, 1, 5, 7, 42),
    years: int = 20,
) -> dict[str, Any]:
    per_seed = []
    for seed in seeds:
        run = run_seeded_gulf_east_asia_vlcc_spot_market(seed, years)
        per_seed.append({"seed": seed, **run["summary"]})

    p95_inventory = [float(row["p95_abs_inventory_gap_days"]) for row in per_seed]
    max_inventory = [float(row["maximum_abs_inventory_gap_days"]) for row in per_seed]
    real_p05 = [float(row["real_tce_2025_usd_per_day_p05"]) for row in per_seed]
    real_median = [float(row["real_tce_2025_usd_per_day_median"]) for row in per_seed]
    real_p95 = [float(row["real_tce_2025_usd_per_day_p95"]) for row in per_seed]
    real_max = [float(row["real_tce_2025_usd_per_day_max"]) for row in per_seed]
    cargo_ranges = [
        float(row["structural_route_cargo_peak_to_trough_pct"])
        for row in per_seed
    ]
    fleet_ranges = [
        int(row["route_fleet_vlcc_max"]) - int(row["route_fleet_vlcc_min"])
        for row in per_seed
    ]

    gates = {
        "all_seed_inventory_p95_below_10_days": max(p95_inventory) < 10.0,
        "all_seed_inventory_max_below_20_days": max(max_inventory) < 20.0,
        "actual_seed_route_demand_is_not_fixed": min(cargo_ranges) > 0.5,
        "flexible_supply_actually_repositions": min(fleet_ranges) >= 2,
        "ordinary_real_tce_p95_below_100k": max(real_p95) < 100000.0,
        "ordinary_real_tce_max_below_prototype_rail": max(real_max) <= 122500.0 + 1.0,
    }
    return {
        "seeds": list(seeds),
        "years": int(years),
        "all_gates_pass": all(gates.values()),
        "gates": gates,
        "aggregate": {
            "structural_route_cargo_peak_to_trough_pct_min": round(min(cargo_ranges), 4),
            "structural_route_cargo_peak_to_trough_pct_max": round(max(cargo_ranges), 4),
            "p95_abs_inventory_gap_days_mean": round(statistics.fmean(p95_inventory), 4),
            "maximum_abs_inventory_gap_days_max": round(max(max_inventory), 4),
            "route_fleet_range_vlcc_min": min(fleet_ranges),
            "route_fleet_range_vlcc_max": max(fleet_ranges),
            "real_tce_2025_usd_per_day_p05_min": round(min(real_p05), 2),
            "real_tce_2025_usd_per_day_median_mean": round(statistics.fmean(real_median), 2),
            "real_tce_2025_usd_per_day_p95_max": round(max(real_p95), 2),
            "real_tce_2025_usd_per_day_max": round(max(real_max), 2),
        },
        "per_seed": per_seed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", default="0,1,5,7,42")
    parser.add_argument("--years", type=int, default=20)
    args = parser.parse_args()
    result = audit_vlcc_spot_market(_parse_seeds(args.seeds), args.years)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["all_gates_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
