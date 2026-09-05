"""Audit the Gulf-East Asia single-route pricing engine."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .model.engine import run_global_macro
from .model.oil_shipping_world import run_oil_shipping_world
from .model.single_route_pricing import (
    price_single_route_turn,
    run_seeded_gulf_east_asia_pricing,
)


def _parse_seeds(raw: str) -> list[int]:
    values = []
    for token in raw.split(","):
        token = token.strip()
        if token:
            values.append(int(token))
    if not values:
        raise ValueError("at least one seed is required")
    return values


def audit_single_route_pricing(
    seeds: list[int],
    years: int,
) -> dict[str, Any]:
    per_seed = []
    all_dynamic = True
    all_conserved = True
    all_no_ordinary_cap = True
    shock_higher = True
    loose_lower = True

    for seed in seeds:
        macro = run_global_macro(seed, years)
        shipping = run_oil_shipping_world(macro)
        neutral = run_seeded_gulf_east_asia_pricing(
            macro,
            shipping,
            supply_lag_turns=2,
        )
        shock_start = min(36, max(0, len(neutral["turns"]) // 4))
        tight_deltas = {
            turn: -6
            for turn in range(shock_start, min(shock_start + 9, len(neutral["turns"])))
        }
        loose_deltas = {
            turn: 6
            for turn in range(shock_start, min(shock_start + 9, len(neutral["turns"])))
        }
        tight = run_seeded_gulf_east_asia_pricing(
            macro,
            shipping,
            supply_lag_turns=2,
            temporary_supply_delta_by_turn=tight_deltas,
        )
        loose = run_seeded_gulf_east_asia_pricing(
            macro,
            shipping,
            supply_lag_turns=2,
            temporary_supply_delta_by_turn=loose_deltas,
        )

        summary = neutral["summary"]
        all_dynamic = all_dynamic and (
            float(summary["structural_route_cargo_mbd_max"])
            > float(summary["structural_route_cargo_mbd_min"])
        )
        all_conserved = all_conserved and (
            float(
                summary[
                    "maximum_abs_inventory_conservation_residual_mmbbl"
                ]
            )
            == 0.0
        )
        all_no_ordinary_cap = all_no_ordinary_cap and (
            int(summary["maximum_price_guard_hit_turns"]) == 0
        )
        shock_higher = shock_higher and (
            float(tight["summary"]["real_tce_2025_usd_per_day_max"])
            > float(neutral["summary"]["real_tce_2025_usd_per_day_max"])
        )
        loose_lower = loose_lower and (
            min(
                float(row["real_tce_2025_usd_per_day"])
                for row in loose["turns"][shock_start : shock_start + 12]
            )
            < min(
                float(row["real_tce_2025_usd_per_day"])
                for row in neutral["turns"][shock_start : shock_start + 12]
            )
        )

        per_seed.append(
            {
                "seed": seed,
                "neutral": summary,
                "temporary_minus_6_prompt_vlcc": {
                    "real_tce_2025_usd_per_day_max": tight["summary"][
                        "real_tce_2025_usd_per_day_max"
                    ],
                    "real_tce_2025_usd_per_day_p95": tight["summary"][
                        "real_tce_2025_usd_per_day_p95"
                    ],
                    "total_unfilled_fixture_vlcc": tight["summary"][
                        "total_unfilled_fixture_vlcc"
                    ],
                    "maximum_abs_inventory_gap_days": tight["summary"][
                        "maximum_abs_inventory_gap_days"
                    ],
                },
                "temporary_plus_6_prompt_vlcc": {
                    "real_tce_2025_usd_per_day_min": loose["summary"][
                        "real_tce_2025_usd_per_day_min"
                    ],
                    "real_tce_2025_usd_per_day_p05": loose["summary"][
                        "real_tce_2025_usd_per_day_p05"
                    ],
                    "total_unused_prompt_vlcc": loose["summary"][
                        "total_unused_prompt_vlcc"
                    ],
                },
            }
        )

    baseline = price_single_route_turn(
        structural_cargo_mbd=9.3,
        turn_days=10,
        prompt_supply_vlcc=50.0,
    )
    tight_quote = price_single_route_turn(
        structural_cargo_mbd=9.3,
        turn_days=10,
        prompt_supply_vlcc=44.0,
    )
    loose_quote = price_single_route_turn(
        structural_cargo_mbd=9.3,
        turn_days=10,
        prompt_supply_vlcc=56.0,
    )
    inventory_quote = price_single_route_turn(
        structural_cargo_mbd=9.3,
        turn_days=10,
        prompt_supply_vlcc=50.0,
        origin_inventory_deviation_mmbbl=9.3,
        destination_inventory_deviation_mmbbl=-9.3,
    )

    gates = {
        "all_seeded_demands_are_dynamic": all_dynamic,
        "virtual_inventory_is_conserved": all_conserved,
        "ordinary_paths_do_not_hit_upper_price_guard": all_no_ordinary_cap,
        "temporary_prompt_shortage_raises_price": shock_higher,
        "temporary_prompt_surplus_lowers_price": loose_lower,
        "one_turn_price_is_monotonic_in_prompt_supply": (
            float(loose_quote["real_tce_2025_usd_per_day"])
            < float(baseline["real_tce_2025_usd_per_day"])
            < float(tight_quote["real_tce_2025_usd_per_day"])
        ),
        "inventory_stress_raises_price": (
            float(inventory_quote["real_tce_2025_usd_per_day"])
            > float(baseline["real_tce_2025_usd_per_day"])
        ),
    }
    return {
        "model_scope": "single_route_supply_demand_pricing_only",
        "seeds": seeds,
        "years": years,
        "gates": gates,
        "all_gates_pass": all(gates.values()),
        "one_turn_sensitivity": {
            "loose_56_prompt": loose_quote,
            "reference_50_prompt": baseline,
            "tight_44_prompt": tight_quote,
            "one_day_inventory_stress": inventory_quote,
        },
        "per_seed": per_seed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", default="0,1,5,7,42")
    parser.add_argument("--years", type=int, default=20)
    args = parser.parse_args()
    result = audit_single_route_pricing(_parse_seeds(args.seeds), args.years)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["all_gates_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
