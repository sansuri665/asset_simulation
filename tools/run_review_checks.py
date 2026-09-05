"""Self-contained calendar and seeded pricing regression controls.

Run from repository root:
  python tools/run_review_checks.py --output review_validation.json

The legacy calendar bug is reproduced locally from its small adapter formula;
no historical Git object, branch, worktree, or network access is required.
"""
from __future__ import annotations

import argparse
import calendar
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SEEDS = [0, 1, 5, 7, 42]


def legacy_lagged_prompt_supply_path(
    months: Sequence[Mapping[str, Any]],
    *,
    config: Mapping[str, Any],
    lag_turns: int = 2,
) -> tuple[int, ...]:
    """Reproduce the old window-total lag that created calendar seasonality."""

    from asset_simulation.model.single_route_pricing import shipping_turn_days

    cargo_capacity = float(config['vlcc_cargo_mmbbl'])
    reference_days = float(config['reference_turn_days'])
    reference_rate = float(config['reference_route_cargo_mbd'])
    reference_equivalent = reference_rate * reference_days / cargo_capacity
    reference_buffer = float(config['reference_prompt_supply_vlcc']) - reference_equivalent
    demand_equivalents = [
        float(month['cargo_mbd']) * days / cargo_capacity
        for month in months
        for days in shipping_turn_days(int(month['days']))
    ]
    supply = []
    for index in range(len(demand_equivalents)):
        source_equivalent = (
            reference_equivalent
            if index < lag_turns
            else demand_equivalents[index - lag_turns]
        )
        supply.append(int(math.floor(source_equivalent + reference_buffer + 0.5)))
    return tuple(supply)


def window_summary(
    result: Mapping[str, Any],
    warmup: int = 36,
) -> dict[str, float | int]:
    rows = result['turns'][warmup:]
    prices = [float(row['real_tce_2025_usd_per_day']) for row in rows]
    return {
        'observed_turns': len(rows),
        'minimum_real_tce': min(prices),
        'median_real_tce': statistics.median(prices),
        'maximum_real_tce': max(prices),
        'real_tce_cv': statistics.pstdev(prices) / statistics.mean(prices),
        'maximum_abs_inventory_gap_days': max(
            abs(float(row['inventory_gap_days'])) for row in rows
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='review_validation.json')
    args = parser.parse_args()

    from asset_simulation.model.engine import run_global_macro
    from asset_simulation.model.oil_shipping_world import run_oil_shipping_world
    from asset_simulation.model.single_route_pricing import (
        build_lagged_prompt_supply_path,
        load_single_route_pricing_config,
        monthly_gulf_east_asia_pricing_inputs,
        run_seeded_gulf_east_asia_pricing,
        simulate_gulf_east_asia_price_path,
    )

    config = load_single_route_pricing_config()
    months = tuple(
        {
            'year': year,
            'month': month,
            'days': calendar.monthrange(year, month)[1],
            'cargo_mbd': 9.3,
        }
        for year in range(2025, 2046)
        for month in range(1, 13)
    )
    cpi = {year: 100 for year in range(2025, 2046)}
    old_path = simulate_gulf_east_asia_price_path(
        months,
        seed=42,
        cpi_by_year=cpi,
        prompt_supply_by_turn=legacy_lagged_prompt_supply_path(
            months,
            config=config,
        ),
        config=config,
    )
    repaired_path = simulate_gulf_east_asia_price_path(
        months,
        seed=42,
        cpi_by_year=cpi,
        prompt_supply_by_turn=build_lagged_prompt_supply_path(months),
    )
    old_constant = window_summary(old_path)
    repaired_constant = window_summary(repaired_path)
    assert old_constant['maximum_real_tce'] - old_constant['minimum_real_tce'] > 20000
    assert repaired_constant['maximum_real_tce'] - repaired_constant['minimum_real_tce'] < 1500
    assert repaired_constant['maximum_abs_inventory_gap_days'] < 0.20
    print(
        'CONSTANT DAILY DEMAND:',
        json.dumps(
            {'old': old_constant, 'repaired': repaired_constant},
            sort_keys=True,
        ),
        flush=True,
    )

    per_seed = []
    for seed in SEEDS:
        macro = run_global_macro(seed, 20)
        shipping = run_oil_shipping_world(macro)
        inputs = monthly_gulf_east_asia_pricing_inputs(shipping)
        price_levels = {
            int(row['year']): float(row['cpi_price_level_index_2025_100'])
            for row in macro.rows
        }
        legacy = simulate_gulf_east_asia_price_path(
            inputs,
            seed=seed,
            cpi_by_year=price_levels,
            prompt_supply_by_turn=legacy_lagged_prompt_supply_path(
                inputs,
                config=config,
            ),
            config=config,
        )
        repaired = run_seeded_gulf_east_asia_pricing(macro, shipping)
        tight = run_seeded_gulf_east_asia_pricing(
            macro,
            shipping,
            temporary_supply_delta_by_turn={turn: -6 for turn in range(36, 45)},
        )
        loose = run_seeded_gulf_east_asia_pricing(
            macro,
            shipping,
            temporary_supply_delta_by_turn={turn: 6 for turn in range(36, 45)},
        )
        record = {
            'seed': seed,
            'legacy_calendar_adapter': legacy['summary'],
            'repaired_adapter': repaired['summary'],
            'repaired_minus_6_prompt': tight['summary'],
            'repaired_plus_6_prompt': loose['summary'],
        }
        per_seed.append(record)
        print('REAL SEED:', json.dumps(record, sort_keys=True), flush=True)

    output = {
        'schema_version': 'asset-simulation-review-validation-v2',
        'comparison_method': 'self_contained_legacy_calendar_formula',
        'pricing_coefficients_changed': False,
        'calendar_control': {
            'legacy_calendar_adapter': old_constant,
            'repaired_adapter': repaired_constant,
        },
        'constant_control_warmup_turns': 36,
        'seeded_window': {
            'annual_transitions': 20,
            'calendar_years': 21,
            'shipping_turns': 756,
        },
        'per_seed': per_seed,
        'interpretation': (
            'Self-contained behavioral regressions, not a fit to observed '
            'tanker-market freight prices.'
        ),
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )


if __name__ == '__main__':
    main()
