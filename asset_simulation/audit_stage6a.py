"""Generate reproducible Stage6A tables and ordinary-Seed coverage audits."""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import json
from pathlib import Path
from typing import Any

from .model.engine import run_global_macro
from .model.oil_shipping_world import run_oil_shipping_world
from .model.registry import sha256_json
from .model.global_shipping_contract import (
    CLASSES, CONFIG_PATH, load_catalog, route_class_reference, sea_turns,
)
from .model.global_shipping_projection import summarize_world_projection


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open('w', newline='', encoding='utf-8-sig') as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def export_reference_tables(folder: Path, cat: dict[str, Any]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    routes = [route_class_reference(cat, pid, c) for pid in cat['lanes'] for c in CLASSES]
    legs = []
    for leg in cat['ballast_legs'].values():
        for c in CLASSES:
            speed = cat['vessel_classes'][c]['ballast_speed_knots']
            distance = leg['distance_nm']
            days = distance / (24 * speed) if distance is not None else None
            turns = sea_turns(distance, speed) if distance is not None else None
            legs.append({'from': leg['from'], 'to': leg['to'], 'class_id': c, 'distance_nm': distance,
                         'speed_knots': speed, 'reference_days': days, 'sea_turns': turns,
                         'rounding_error_days': turns * 10 - days if days is not None else None,
                         'geography_ready': leg['geography_ready'], 'basis': leg['basis'],
                         'confidence': leg['confidence'], 'cargo_bbl': 0, 'discharge_turns': 0})
    shares = [{'pair_id': r['pair_id'], 'display_route_id': r['display_route_id'],
               'reference_cargo_mbd': r['reference_cargo_mbd'], 'geography_ready': r['geography_ready'],
               **{c + '_percent': r['class_share_bps'][c] / 100 for c in CLASSES},
               'status': r['share_status'], 'benchmark_analogues': '|'.join(r['benchmark_analogues'])}
              for r in cat['lanes'].values()]
    _write_csv(folder / 'route_class_reference.csv', routes)
    _write_csv(folder / 'ballast_class_reference.csv', legs)
    _write_csv(folder / 'route_class_share_priors.csv', shares)
    return {'route_class_row_count': len(routes), 'ballast_class_row_count': len(legs),
            'geography_ready_pair_count': sum(r['geography_ready'] for r in cat['lanes'].values()),
            'unresolved_pair_count': sum(not r['geography_ready'] for r in cat['lanes'].values()),
            'maximum_abs_laden_rounding_error_days': max(abs(r['laden_rounding_error_days']) for r in routes),
            'maximum_abs_ballast_rounding_error_days': max(abs(r['ballast_rounding_error_days']) for r in routes),
            'reference_class_cargo_mbd': {c: sum(r['reference_cargo_mbd'] * r['class_share_bps'][c] / 10000 for r in cat['lanes'].values()) for c in CLASSES},
            'reference_unresolved_cargo_mbd': sum(r['reference_cargo_mbd'] for r in cat['lanes'].values() if not r['geography_ready']),
            'table_hash': sha256_json({'route_class': routes, 'ballast_class': legs, 'shares': shares})}


def share_sensitivity(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Uncertainty analysis, not fitting assumed shares to an arbitrary fleet."""
    output = []
    for delta in (-1000, 0, 1000):
        candidate = deepcopy(raw)
        for destinations in candidate['class_share_matrix_bps'].values():
            for values in destinations.values():
                if delta >= 0:
                    change = min(delta, values[1])
                else:
                    change = -min(-delta, values[0])
                values[0] += change
                values[1] -= change
        cat = load_catalog(raw=candidate)
        output.append({'vlcc_vs_suez_share_shift_bps': delta,
                       'reference_total_cargo_mbd': sum(l['reference_cargo_mbd'] for l in cat['lanes'].values()),
                       'roundtrip_reference_equivalents': {
                           c: sum(route_class_reference(cat, pid, c)['reference_cycle_vessels'] for pid in cat['lanes'])
                           for c in CLASSES},
                       'scope': 'conditional_reference_workload_not_inferred_real_fleet'})
    return output


def run_audit(seeds: list[int], years: int, tables_dir: Path) -> dict[str, Any]:
    cat = load_catalog(); before = sha256_json(cat)
    tables = export_reference_tables(tables_dir, cat)
    results = []
    for seed in seeds:
        world = run_oil_shipping_world(run_global_macro(seed=seed, years=years))
        results.append(summarize_world_projection(world, cat))
    checks = {
        'catalog_covers_25_od_pairs': len(cat['lanes']) == 25,
        'route_by_class_covers_75_combinations': tables['route_class_row_count'] == 75,
        'ballast_reference_has_50_directed_choices': len(cat['ballast_legs']) == 50,
        'all_plan_barrels_partitioned_once': all(r['class_split_residual_bbl'] == 0 for r in results),
        'eleven_residual_pairs_not_discarded': all(r['residual_11_pair_plan_bbl'] > 0 for r in results),
        'ordinary_seed_replay_matches_public_cargo': all(r['maximum_group_cargo_error_mbd'] < 1e-7 for r in results),
        'each_operating_month_is_30_days': all(r['operating_day_count'] == r['month_count'] * 30 for r in results),
        'integer_clock_rounding_is_disclosed': all(r['maximum_pair_month_rounding_error_bbl'] <= 1.50001 for r in results),
        'source_worlds_and_catalog_unmodified': all(r['source_unchanged'] for r in results) and before == sha256_json(cat),
        'no_false_global_fleet_or_price_claim': all(not r['global_fleet_size_inferred'] and not r['price_present'] and not r['cost_present'] for r in results),
    }
    return {'audit_id': 'stage6a-global-contract-audit-v1', 'catalog_hash': cat['catalog_hash'],
            'annual_transitions': years, 'calendar_label_years_per_seed': years + 1,
            'seeds': seeds, 'tables': tables,
            'share_sensitivity': share_sensitivity(json.loads(CONFIG_PATH.read_text())),
            'gates': checks, 'all_gates_pass': all(checks.values()), 'per_seed': results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', default='0,1,5,7,42')
    parser.add_argument('--years', type=int, default=20)
    parser.add_argument('--output', type=Path, default=Path('stage6a-audit.json'))
    parser.add_argument('--tables-dir', type=Path, default=Path('stage6a-tables'))
    args = parser.parse_args()
    seeds = list(dict.fromkeys(int(s) for s in args.seeds.split(',')))
    if not seeds:
        parser.error('at least one seed is required')
    result = run_audit(seeds, args.years, args.tables_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'all_gates_pass': result['all_gates_pass'], 'gates': result['gates']}))
    if not result['all_gates_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
