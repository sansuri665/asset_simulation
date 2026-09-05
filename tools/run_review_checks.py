"""Review regressions, using the real repository functions and pinned old code.

Run from repository root with full Git history:
  python tools/run_review_checks.py --output review_validation.json

No branch writes, tuning or network requests are performed by this tool.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = '75722a1db5867eec9a2f0a1fa96fa30b1df88cb4'
OLD = '1f077c609111e6767866d1adc6b1796e2ccf87c2'
SEEDS = [0, 1, 5, 7, 42]


def old_pricing_module():
    source = subprocess.check_output(['git', 'show', f'{OLD}:asset_simulation/model/single_route_pricing.py'], cwd=ROOT, text=True)
    cfg = json.loads(subprocess.check_output(['git', 'show', f'{OLD}:asset_simulation/config/gulf_east_asia_pricing_v0.2.json'], cwd=ROOT, text=True))
    ns = {'__name__': 'asset_simulation.model._review_legacy_pricing',
          '__file__': str(ROOT / 'asset_simulation/model/single_route_pricing.py')}
    exec(compile(source, f'{OLD}:single_route_pricing.py', 'exec'), ns)
    return ns, cfg


def window_summary(result, warmup=36):
    rows = result['turns'][warmup:]
    prices = [r['real_tce_2025_usd_per_day'] for r in rows]
    return {
        'observed_turns': len(rows),
        'minimum_real_tce': min(prices),
        'median_real_tce': statistics.median(prices),
        'maximum_real_tce': max(prices),
        'real_tce_cv': statistics.pstdev(prices) / statistics.mean(prices),
        'maximum_abs_inventory_gap_days': max(abs(r['inventory_gap_days']) for r in rows),
    }


def verify_upstream_unchanged():
    program = '''
import json
from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world
from asset_simulation.model.oil_price_projection import run_oil_price_projection
out = {}
for seed in (0, 1, 5, 7, 42):
    run = run_global_macro(seed, 20)
    out[str(seed)] = {
        'macro': run.identity['result_hash'],
        'shipping': run_oil_shipping_world(run).identity['result_hash'],
        'oil_price': run_oil_price_projection(run).identity['result_hash'],
    }
print(json.dumps(out, sort_keys=True))
'''
    env = {**os.environ, 'PYTHONHASHSEED': '0'}
    env.pop('PYTHONPATH', None)
    with tempfile.TemporaryDirectory(prefix='asset-main-review-') as tmp:
        baseline = Path(tmp) / 'baseline'
        subprocess.run(['git', 'worktree', 'add', '--detach', str(baseline), BASE], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        try:
            before = json.loads(subprocess.check_output([sys.executable, '-c', program], cwd=baseline, env=env, text=True))
        finally:
            subprocess.run(['git', 'worktree', 'remove', '--force', str(baseline)], cwd=ROOT, check=True)
    after = json.loads(subprocess.check_output([sys.executable, '-c', program], cwd=ROOT, env=env, text=True))
    if before != after:
        raise AssertionError(f'Unexpected upstream numerical change: before={before}, after={after}')
    return {'all_equal': True, 'base_commit': BASE, 'seed_count': len(SEEDS), 'annual_transitions': 20,
            'calendar_years': 21, 'result_hashes': after}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='review_validation.json')
    args = parser.parse_args()
    from asset_simulation.model.engine import run_global_macro
    from asset_simulation.model.oil_shipping_world import run_oil_shipping_world
    from asset_simulation.model.single_route_pricing import (
        build_lagged_prompt_supply_path, monthly_gulf_east_asia_pricing_inputs,
        run_seeded_gulf_east_asia_pricing, simulate_gulf_east_asia_price_path,
    )
    legacy, cfg = old_pricing_module()
    months = tuple({'year': y, 'month': m, 'days': calendar.monthrange(y, m)[1], 'cargo_mbd': 9.3}
                   for y in range(2025, 2046) for m in range(1, 13))
    cpi = {y: 100 for y in range(2025, 2046)}
    old_path = legacy['simulate_gulf_east_asia_price_path'](
        months, seed=42, cpi_by_year=cpi,
        prompt_supply_by_turn=legacy['build_lagged_prompt_supply_path'](months, config=cfg), config=cfg)
    new_path = simulate_gulf_east_asia_price_path(months, seed=42, cpi_by_year=cpi,
                                                prompt_supply_by_turn=build_lagged_prompt_supply_path(months))
    old_constant, new_constant = window_summary(old_path), window_summary(new_path)
    assert old_constant['maximum_real_tce'] - old_constant['minimum_real_tce'] > 20000
    assert new_constant['maximum_real_tce'] - new_constant['minimum_real_tce'] < 1500
    assert new_constant['maximum_abs_inventory_gap_days'] < 0.20
    print('CONSTANT DAILY DEMAND:', json.dumps({'old': old_constant, 'repaired': new_constant}, sort_keys=True), flush=True)

    per_seed = []
    for seed in SEEDS:
        macro = run_global_macro(seed, 20)
        shipping = run_oil_shipping_world(macro)
        inputs = monthly_gulf_east_asia_pricing_inputs(shipping)
        prices = {int(r['year']): float(r['cpi_price_level_index_2025_100']) for r in macro.rows}
        old = legacy['simulate_gulf_east_asia_price_path'](
            inputs, seed=seed, cpi_by_year=prices,
            prompt_supply_by_turn=legacy['build_lagged_prompt_supply_path'](inputs, config=cfg), config=cfg)
        repaired = run_seeded_gulf_east_asia_pricing(macro, shipping)
        tight = run_seeded_gulf_east_asia_pricing(macro, shipping, temporary_supply_delta_by_turn={t: -6 for t in range(36, 45)})
        loose = run_seeded_gulf_east_asia_pricing(macro, shipping, temporary_supply_delta_by_turn={t: 6 for t in range(36, 45)})
        per_seed.append({'seed': seed, 'old_adapter': old['summary'], 'repaired_adapter': repaired['summary'],
                         'repaired_minus_6_prompt': tight['summary'], 'repaired_plus_6_prompt': loose['summary']})
        print('REAL SEED:', json.dumps(per_seed[-1], sort_keys=True), flush=True)
    upstream = verify_upstream_unchanged()
    print('UPSTREAM HASH REGRESSION:', json.dumps(upstream, sort_keys=True), flush=True)
    output = {
        'schema_version': 'asset-simulation-review-validation-v1',
        'base_main_sha': BASE, 'legacy_pricing_sha': OLD,
        'pricing_coefficients_changed': False,
        'calendar_control': {'old_adapter': old_constant, 'repaired_adapter': new_constant},
        'constant_control_warmup_turns': 36,
        'seeded_window': {'annual_transitions': 20, 'calendar_years': 21, 'shipping_turns': 756},
        'per_seed': per_seed,
        'upstream_output_regression': upstream,
        'interpretation': 'Behavioral regressions, not a fit to observed tanker-market freight prices.',
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
