"""Small Stage6C-preview2 demo: same frozen world, cargo reacts to inventory and ship intentions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from asset_simulation.model.freight_board import BoardSession, make_board_spec
from asset_simulation.model.freight_board.cargo_formation import form_cargo_plan

V = 1_971_000
ZERO = {'gulf': 0, 'west_africa': 0}


def offered(snapshot):
    return {o: tuple(s.ship_id for s in snapshot.opened_market.ships if s.location == o)
            for o in snapshot.spec.origins}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=Path('stage6c-preview2-demo.json'))
    a = p.parse_args()
    spec = make_board_spec()
    natural = {'gulf': 5*V, 'west_africa': 2*V}
    cases = {}
    for name, stock in (
        ('high_inventory', spec.destination_band.normal_bbl + spec.destination_band.soft_high_bbl//2),
        ('low_inventory', spec.destination_band.normal_bbl + spec.destination_band.soft_low_bbl//2),
    ):
        session = BoardSession(spec, fleet_counts={'vlcc': 24, 'suezmax': 12}, initialization='cold',
                               destination_stock_bbl=stock)
        snap = session.open_turn(source_release_bbl=ZERO)
        result = form_cargo_plan(snap, offered(snap), natural)
        report = result.report()
        cases[name] = {
            'natural_plan_bbl': report['natural_cargo_plan_bbl'],
            'final_plan_bbl': report['final_cargo_plan_bbl'],
            'inventory_draw_bbl': report['inventory_draw_vs_natural_bbl'],
            'inventory_build_bbl': report['inventory_build_vs_natural_bbl'],
            'route_service_value_real_usd_per_bbl': report['final_route_service_value_real_usd_per_bbl'],
            'iterations': report['iterations'],
        }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: {'final_plan_bbl': v['final_plan_bbl'],
                         'inventory_draw_bbl': v['inventory_draw_bbl'],
                         'inventory_build_bbl': v['inventory_build_bbl']} for k, v in cases.items()}))


if __name__ == '__main__':
    main()
