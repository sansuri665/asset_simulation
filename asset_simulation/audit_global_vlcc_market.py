"""Audit the fixed global VLCC pool against actual seeded route demand."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .model.global_vlcc_market import run_seeded_global_vlcc_spot_market


def audit_global_vlcc_market(
    *,
    seeds: tuple[int, ...] = (0, 1, 5, 7, 42),
    years: int = 20,
) -> dict[str, Any]:
    per_seed: list[dict[str, Any]] = []
    for seed in seeds:
        result = run_seeded_global_vlcc_spot_market(seed, years)
        summary = result["summary"]
        per_seed.append(
            {
                "seed": seed,
                "turn_count": summary["turn_count"],
                "global_real_tce_2025_usd_per_day_p05": summary[
                    "global_real_tce_2025_usd_per_day_p05"
                ],
                "global_real_tce_2025_usd_per_day_median": summary[
                    "global_real_tce_2025_usd_per_day_median"
                ],
                "global_real_tce_2025_usd_per_day_p95": summary[
                    "global_real_tce_2025_usd_per_day_p95"
                ],
                "global_real_tce_2025_usd_per_day_max": summary[
                    "global_real_tce_2025_usd_per_day_max"
                ],
                "global_nominal_tce_usd_per_day_p95": summary[
                    "global_nominal_tce_usd_per_day_p95"
                ],
                "global_idle_fleet_vlcc_min": summary[
                    "global_idle_fleet_vlcc_min"
                ],
                "global_idle_fleet_vlcc_mean": summary[
                    "global_idle_fleet_vlcc_mean"
                ],
                "global_repositioning_fleet_vlcc_max": summary[
                    "global_repositioning_fleet_vlcc_max"
                ],
                "global_structural_required_fleet_vlcc_mean": summary[
                    "global_structural_required_fleet_vlcc_mean"
                ],
                "global_structural_required_fleet_vlcc_max": summary[
                    "global_structural_required_fleet_vlcc_max"
                ],
                "global_tightness_ratio_mean": summary[
                    "global_tightness_ratio_mean"
                ],
                "global_tightness_ratio_max": summary[
                    "global_tightness_ratio_max"
                ],
                "maximum_abs_fleet_conservation_residual_vlcc": summary[
                    "maximum_abs_fleet_conservation_residual_vlcc"
                ],
                "total_unfilled_fixture_vlcc": summary[
                    "total_unfilled_fixture_vlcc"
                ],
                "total_repositioned_vlcc": summary[
                    "total_repositioned_vlcc"
                ],
                "cpi_price_level_start": summary["cpi_price_level_start"],
                "cpi_price_level_end": summary["cpi_price_level_end"],
                "per_route": summary["per_route"],
            }
        )

    all_route_spans = [
        row["structural_route_cargo_mbd_max"]
        - row["structural_route_cargo_mbd_min"]
        for seed_row in per_seed
        for row in seed_row["per_route"].values()
    ]
    all_route_inventory_max = [
        row["maximum_abs_inventory_gap_days"]
        for seed_row in per_seed
        for row in seed_row["per_route"].values()
    ]
    all_route_tce_p95 = [
        row["real_tce_2025_usd_per_day_p95"]
        for seed_row in per_seed
        for row in seed_row["per_route"].values()
    ]
    gates = {
        "global_fleet_is_strictly_conserved": all(
            row["maximum_abs_fleet_conservation_residual_vlcc"] == 0
            for row in per_seed
        ),
        "all_seeded_route_demands_are_dynamic": min(all_route_spans) > 1e-4,
        "all_routes_keep_inventory_p95_below_10_days": all(
            row["p95_abs_inventory_gap_days"] < 10.0
            for seed_row in per_seed
            for row in seed_row["per_route"].values()
        ),
        "all_routes_keep_inventory_max_below_25_days": max(
            all_route_inventory_max
        ) < 25.0,
        "market_reallocates_real_ships": all(
            row["total_repositioned_vlcc"] > 0 for row in per_seed
        ),
        "ordinary_global_real_tce_p95_below_100k": all(
            row["global_real_tce_2025_usd_per_day_p95"] < 100000.0
            for row in per_seed
        ),
        "route_real_tce_p95_below_hard_cap": max(all_route_tce_p95) < 122500.01,
    }
    aggregate = {
        "global_real_tce_2025_usd_per_day_median_mean": round(
            sum(
                row["global_real_tce_2025_usd_per_day_median"]
                for row in per_seed
            )
            / len(per_seed),
            2,
        ),
        "global_real_tce_2025_usd_per_day_p95_max": max(
            row["global_real_tce_2025_usd_per_day_p95"]
            for row in per_seed
        ),
        "global_real_tce_2025_usd_per_day_max": max(
            row["global_real_tce_2025_usd_per_day_max"]
            for row in per_seed
        ),
        "minimum_idle_fleet_vlcc": min(
            row["global_idle_fleet_vlcc_min"] for row in per_seed
        ),
        "maximum_repositioning_fleet_vlcc": max(
            row["global_repositioning_fleet_vlcc_max"] for row in per_seed
        ),
        "maximum_route_inventory_gap_days": max(all_route_inventory_max),
        "maximum_global_required_fleet_vlcc": max(
            row["global_structural_required_fleet_vlcc_max"]
            for row in per_seed
        ),
    }
    return {
        "seeds": list(seeds),
        "years": years,
        "all_gates_pass": all(gates.values()),
        "gates": gates,
        "aggregate": aggregate,
        "per_seed": per_seed,
    }


def _parse_seeds(value: str) -> tuple[int, ...]:
    seeds = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not seeds:
        raise argparse.ArgumentTypeError("at least one seed is required")
    if any(seed < 0 for seed in seeds):
        raise argparse.ArgumentTypeError("seeds must be non-negative")
    return seeds


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seeds",
        type=_parse_seeds,
        default=(0, 1, 5, 7, 42),
    )
    parser.add_argument("--years", type=int, default=20)
    args = parser.parse_args()
    result = audit_global_vlcc_market(seeds=args.seeds, years=args.years)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["all_gates_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
