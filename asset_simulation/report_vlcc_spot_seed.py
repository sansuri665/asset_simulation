"""Print compact annual diagnostics for the experimental VLCC spot market."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from typing import Any

from .model.vlcc_spot_market import run_seeded_gulf_east_asia_vlcc_spot_market


def build_annual_report(seed: int = 42, years: int = 60) -> dict[str, Any]:
    run = run_seeded_gulf_east_asia_vlcc_spot_market(seed, years)
    grouped: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for turn in run["turns"]:
        grouped[int(turn["year"])].append(turn)

    annual = []
    for year in sorted(grouped):
        turns = grouped[year]
        annual.append(
            {
                "year": year,
                "structural_route_cargo_mbd": round(
                    statistics.fmean(
                        float(turn["structural_route_cargo_mbd"])
                        for turn in turns
                    ),
                    6,
                ),
                "route_fleet_vlcc": round(
                    statistics.fmean(float(turn["route_fleet_vlcc"]) for turn in turns),
                    2,
                ),
                "abs_inventory_gap_days_mean": round(
                    statistics.fmean(
                        abs(float(turn["inventory_gap_days"])) for turn in turns
                    ),
                    4,
                ),
                "real_tce_2025_usd_per_day": round(
                    statistics.fmean(
                        float(turn["real_tce_2025_usd_per_day"])
                        for turn in turns
                    ),
                    2,
                ),
                "nominal_tce_usd_per_day": round(
                    statistics.fmean(
                        float(turn["nominal_tce_usd_per_day"]) for turn in turns
                    ),
                    2,
                ),
                "cpi_price_level_index_2025_100": round(
                    statistics.fmean(
                        float(turn["cpi_price_level_index_2025_100"])
                        for turn in turns
                    ),
                    6,
                ),
            }
        )
    return {
        "identity": run["identity"],
        "summary": run["summary"],
        "annual": annual,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--years", type=int, default=60)
    parser.add_argument("--stride", type=int, default=5)
    args = parser.parse_args()
    report = build_annual_report(args.seed, args.years)
    annual = report["annual"]
    sampled = [
        row
        for index, row in enumerate(annual)
        if index == 0 or index == len(annual) - 1 or index % max(1, args.stride) == 0
    ]
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "sampled_annual": sampled,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
