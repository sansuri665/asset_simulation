"""Audit the Gulf-to-East-Asia monopoly operations prototype."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .model.gulf_east_asia_monopoly import (
    load_gulf_east_asia_monopoly_config,
    run_seeded_gulf_east_asia_monopoly_operations,
)


def _parse_seeds(value: str) -> list[int]:
    seeds = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not seeds:
        raise argparse.ArgumentTypeError("at least one seed is required")
    return seeds


def audit_monopoly_operations(
    *,
    seeds: list[int],
    years: int,
) -> dict[str, Any]:
    if years <= 0:
        raise ValueError("years must be positive")
    config = load_gulf_east_asia_monopoly_config()
    cap = float(config["pricing"]["maximum_real_tce_2025_usd_per_day"])
    per_seed: list[dict[str, Any]] = []
    for seed in seeds:
        result = run_seeded_gulf_east_asia_monopoly_operations(seed, years)
        summary = dict(result["summary"])
        per_seed.append({"seed": seed, **summary})

    gates = {
        "fixed_fleet_is_conserved": all(
            int(row["maximum_abs_fleet_conservation_residual_vlcc"]) == 0
            and int(row["maximum_duplicate_ship_count"]) == 0
            and int(row["maximum_missing_ship_count"]) == 0
            and int(row["maximum_extra_ship_count"]) == 0
            for row in per_seed
        ),
        "virtual_inventory_is_conserved": all(
            float(row["maximum_abs_inventory_conservation_residual_mmbbl"])
            <= 1e-8
            for row in per_seed
        ),
        "all_seeded_demands_are_dynamic": all(
            float(row["structural_route_cargo_mbd_max"])
            - float(row["structural_route_cargo_mbd_min"])
            > 1e-4
            for row in per_seed
        ),
        "idle_port_state_is_used": all(
            int(row["gulf_idle_vlcc_max"]) > 0 for row in per_seed
        ),
        "monopoly_policy_withholds_some_prompt_capacity": all(
            int(row["strategically_withheld_vlcc_total"]) > 0
            for row in per_seed
        ),
        "ordinary_inventory_gap_p95_below_8_days": all(
            float(row["p95_abs_inventory_gap_days"]) < 8.0
            for row in per_seed
        ),
        "ordinary_inventory_gap_max_below_15_days": all(
            float(row["maximum_abs_inventory_gap_days"]) < 15.0
            for row in per_seed
        ),
        "ordinary_real_tce_p95_below_hard_cap": all(
            float(row["real_tce_2025_usd_per_day_p95"]) < cap
            for row in per_seed
        ),
        "operating_costs_are_live": all(
            float(row["real_opex_2025_usd_per_vessel_day"]) > 0.0
            and float(row["real_gross_freight_per_voyage_usd_median"]) > 0.0
            for row in per_seed
        ),
    }
    aggregate = {
        "maximum_inventory_gap_days": max(
            float(row["maximum_abs_inventory_gap_days"]) for row in per_seed
        ),
        "maximum_real_tce_2025_usd_per_day": max(
            float(row["real_tce_2025_usd_per_day_max"]) for row in per_seed
        ),
        "maximum_real_tce_p95_2025_usd_per_day": max(
            float(row["real_tce_2025_usd_per_day_p95"]) for row in per_seed
        ),
        "minimum_idle_vlcc": min(
            int(row["gulf_idle_vlcc_min"]) for row in per_seed
        ),
        "maximum_idle_vlcc": max(
            int(row["gulf_idle_vlcc_max"]) for row in per_seed
        ),
        "maximum_physical_shortage_vlcc_total": max(
            int(row["physical_shortage_vlcc_total"]) for row in per_seed
        ),
        "minimum_cumulative_real_operating_cashflow_2025_usd": min(
            float(row["cumulative_real_operating_cashflow_2025_usd"])
            for row in per_seed
        ),
    }
    return {
        "seeds": seeds,
        "years": years,
        "all_gates_pass": all(gates.values()),
        "gates": gates,
        "aggregate": aggregate,
        "per_seed": per_seed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=_parse_seeds, default=[0, 1, 5, 7, 42])
    parser.add_argument("--years", type=int, default=20)
    args = parser.parse_args()
    result = audit_monopoly_operations(seeds=args.seeds, years=args.years)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["all_gates_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
