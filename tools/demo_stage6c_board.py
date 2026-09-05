"""Two explicit trial choices on one snapshot; final B is chosen by this caller."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from asset_simulation.model.freight_board import (
    BoardSession, ImportRequirement, make_board_spec, quote_trial,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    spec = make_board_spec()
    board = BoardSession(spec, fleet_counts={'vlcc': 12, 'suezmax': 8, 'aframax': 4}, initialization='cold')
    snap = board.open_turn(source_release_bbl={'gulf': 5_000_000, 'west_africa': 1_000_000},
                          new_requirements=(ImportRequirement('demo-import', 4, 6_000_000),))
    ships = {o: [s.ship_id for s in snap.opened_market.ships if s.location == o] for o in spec.origins}
    before = board.state.identity
    a = quote_trial(snap, ships, {'gulf': 5_000_000, 'west_africa': 1_000_000})
    b = quote_trial(snap, ships, {'gulf': 5_500_000, 'west_africa': 500_000})
    assert board.state.identity == before
    result = board.commit(b)
    try:
        board.commit(b)
        raise AssertionError('duplicate commit was not rejected')
    except ValueError:
        pass
    out = {'trial_a': a.report(), 'trial_b': b.report(), 'committed_choice': 'B',
           'one_commit_only': True, 'final_turn': board.state.market.turn,
           'execution_id': result['execution_id'],
           'scope': 'caller chose B explicitly; no cost or optimization'}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'final_turn': out['final_turn'], 'execution_id': out['execution_id'],
                      'quotes': {name: {o: trial.report()['routes'][o]['route_benchmark_real_tce']
                                       for o in spec.origins} for name, trial in [('A', a), ('B', b)]}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
