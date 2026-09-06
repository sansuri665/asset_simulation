"""Stage6C-preview2 cargo-formation audit: transparent cargo-side feedback, no ship optimizer."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import unittest

from .model.freight_board import BoardSession, ImportRequirement, make_board_spec
from .model.freight_board.cargo_formation import form_cargo_plan
from .model.registry import sha256_json

V = 1_971_000
ZERO = {'gulf': 0, 'west_africa': 0}


def offered(snapshot):
    return {o: tuple(s.ship_id for s in snapshot.opened_market.ships if s.location == o)
            for o in snapshot.spec.origins}


def controls():
    spec = make_board_spec()
    natural = {'gulf': 5*V, 'west_africa': 2*V}
    band = spec.destination_band
    inventory_cases = {}
    for name, stock in (
        ('high', band.normal_bbl + band.soft_high_bbl//2),
        ('normal', band.normal_bbl),
        ('low', band.normal_bbl + band.soft_low_bbl//2),
    ):
        s = BoardSession(spec, fleet_counts={'vlcc': 24, 'suezmax': 12}, initialization='cold',
                         destination_stock_bbl=stock)
        snap = s.open_turn(source_release_bbl=ZERO)
        formed = form_cargo_plan(snap, offered(snap), natural)
        r = formed.report()
        inventory_cases[name] = {
            'final_plan_bbl': r['final_cargo_plan_bbl'],
            'final_total_bbl': r['final_total_bbl'],
            'inventory_draw_bbl': r['inventory_draw_vs_natural_bbl'],
            'inventory_build_bbl': r['inventory_build_vs_natural_bbl'],
            'needs_ship_response': r['needs_ship_response'],
            'iterations': len(r['iterations']),
        }
    shortage_cases = {}
    for name, stock in (
        ('high_inventory', band.normal_bbl + band.soft_high_bbl//2),
        ('low_inventory', band.normal_bbl + band.soft_low_bbl//2),
    ):
        s = BoardSession(spec, fleet_counts={'vlcc': 28, 'suezmax': 12}, initialization='cold',
                         source_stock_bbl=ZERO, destination_stock_bbl=stock)
        snap = s.open_turn(source_release_bbl={'gulf': 2*V, 'west_africa': 8*V})
        formed = form_cargo_plan(snap, offered(snap), {'gulf': 5*V, 'west_africa': V})
        shortage_cases[name] = formed.report()
    s = BoardSession(spec, fleet_counts={'vlcc': 24, 'suezmax': 12}, initialization='cold')
    snap = s.open_turn(source_release_bbl=ZERO)
    ships = offered(snap)
    natural_feedback = {'gulf': 2*V, 'west_africa': V}
    balanced = form_cargo_plan(snap, ships, natural_feedback)
    waf_tight = form_cargo_plan(
        snap, {'gulf': ships['gulf'], 'west_africa': ships['west_africa'][:1]}, natural_feedback
    )
    balanced_report = balanced.report()
    waf_tight_report = waf_tight.report()
    before = sha256_json(asdict(snap))
    reference = form_cargo_plan(snap, ships, natural_feedback)
    for _ in range(200):
        form_cargo_plan(snap, ships, natural_feedback)
    pure = before == sha256_json(asdict(snap)) and reference == form_cargo_plan(snap, ships, natural_feedback)
    urgent = BoardSession(spec, fleet_counts={'vlcc': 12}, initialization='cold', destination_stock_bbl=1)
    ux = urgent.open_turn(source_release_bbl=ZERO, new_requirements=(ImportRequirement('urgent', 0, 2),))
    broken = form_cargo_plan(ux, {'gulf': (), 'west_africa': ()}, {'gulf': V, 'west_africa': V}).report()
    balanced_plan = balanced_report['final_cargo_plan_bbl']
    waf_tight_plan = waf_tight_report['final_cargo_plan_bbl']
    return {
        'inventory_cases': inventory_cases,
        'gulf_shortage': {
            k: {
                'final_plan_bbl': v['final_cargo_plan_bbl'],
                'final_total_bbl': v['final_total_bbl'],
                'inventory_draw_bbl': v['inventory_draw_vs_natural_bbl'],
                'inventory_build_bbl': v['inventory_build_vs_natural_bbl'],
                'needs_ship_response': v['needs_ship_response'],
                'stop_reason': v['stop_reason'],
            } for k, v in shortage_cases.items()
        },
        'ship_feedback': {
            'natural_plan_bbl': natural_feedback,
            'balanced_plan': balanced_plan,
            'waf_tight_plan': waf_tight_plan,
            'balanced_prices': balanced_report['final_route_service_value_real_usd_per_bbl'],
            'waf_tight_prices': waf_tight_report['final_route_service_value_real_usd_per_bbl'],
            'same_snapshot': balanced.snapshot_id == waf_tight.snapshot_id,
            'formed_plan_changed': balanced_plan != waf_tight_plan,
            'cargo_shifted_away_from_tight_waf':
                waf_tight_plan['gulf'] > balanced_plan['gulf'] and
                waf_tight_plan['west_africa'] < balanced_plan['west_africa'],
        },
        'pure_after_200_reformations': pure,
        'urgent_shortage_not_faked': (not broken['final_trial_valid']) and broken['needs_ship_response'],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=Path('stage6c-preview2-audit.json'))
    a = p.parse_args()
    c = controls()
    suite = unittest.defaultTestLoader.loadTestsFromName('asset_simulation.tests.test_cargo_formation')
    tested = unittest.TestResult()
    suite.run(tested)
    high = c['inventory_cases']['high']
    low = c['inventory_cases']['low']
    gs_hi = c['gulf_shortage']['high_inventory']
    gs_lo = c['gulf_shortage']['low_inventory']
    ship = c['ship_feedback']
    gates = {
        'cargo_formation_contract_tests': tested.wasSuccessful(),
        'same_snapshot_reformation_is_pure': c['pure_after_200_reformations'],
        'high_inventory_defers_more': high['inventory_draw_bbl'] > low['inventory_draw_bbl'],
        'low_inventory_replaces_more_gulf_shortage':
            gs_lo['final_plan_bbl']['west_africa'] > gs_hi['final_plan_bbl']['west_africa'],
        'urgent_current_shortage_not_repaired_by_future_order': c['urgent_shortage_not_faked'],
        'ship_plan_changes_formed_cargo_without_mutation':
            ship['same_snapshot'] and ship['formed_plan_changed'] and ship['cargo_shifted_away_from_tight_waf'],
    }
    out = {
        'model': 'stage6c-preview2-dynamic-cargo-formation-v0.1.0',
        'parent_branch': 'stage6c-preview-dynamic-freight-board',
        'parent_commit': 'd788a7c72aa7256dc8fc51d70f614f45308ecd81',
        'controls': c,
        'contract_test_count': tested.testsRun,
        'contract_test_failures': [(str(t), trace) for t, trace in (*tested.failures, *tested.errors)],
        'gates': gates,
        'all_gates_pass': all(gates.values()),
        'scope': [
            'Natural OD cargo is an initial prior, not a hard source share.',
            'The cargo layer reads an external ship intention; it never inserts or removes ships.',
            'Opening inventory is committed reality; trial cargo changes future inventory only.',
            'No crude-price, grade, refinery-margin, owner-profit or voyage-cost model is introduced.',
        ],
    }
    out['audit_hash'] = sha256_json(out)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'gates': gates, 'all_gates_pass': out['all_gates_pass']}))
    if not out['all_gates_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
