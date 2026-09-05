"""Physical stock and known arrivals: placing an order is not receiving oil."""
from __future__ import annotations
from collections import Counter
from dataclasses import asdict
import math
from typing import Sequence
from .types import BoardSnapshot, InventoryBand


def stock_report(stock: int, band: InventoryBand) -> dict:
    deviation = stock - band.normal_bbl
    scale = band.soft_high_bbl if deviation >= 0 else -band.soft_low_bbl
    return {'stock_bbl': stock, 'deviation_bbl': deviation, 'normal_bbl': band.normal_bbl,
            'soft_pressure': math.tanh(deviation / scale),
            'hard_bound_violation': not band.hard_low_bbl <= deviation <= band.hard_high_bbl,
            'at_lower_boundary': deviation <= band.hard_low_bbl,
            'at_upper_boundary': deviation >= band.hard_high_bbl, 'bounds': asdict(band)}


def inventory_projection(snapshot: BoardSnapshot, assigned: Sequence[dict]) -> dict:
    """No future demand extrapolation and no stock clipping to hide infeasibility."""
    spec, state = snapshot.spec, snapshot.opened_market
    tick = state.turn
    releases = dict(snapshot.releases_bbl)
    loaded, trial_due = Counter(), Counter()
    for item in assigned:
        loaded[item['origin']] += item['assigned_cargo_bbl']
        trial_due[item['ready_turn']] += item['assigned_cargo_bbl']
    sources = {}
    for origin, band in spec.source_bands:
        old = sum(b.remaining_bbl for b in state.batches if b.origin == origin)
        sources[origin] = {'opening': stock_report(old, band), 'released_bbl': releases[origin],
                           'loaded_bbl': loaded[origin],
                           'closing': stock_report(old + releases[origin] - loaded[origin], band)}
    arrivals, due = Counter(), Counter()
    for ship in state.ships:
        if ship.movement and ship.movement.kind == 'laden':
            arrivals[ship.movement.ready_turn] += ship.movement.cargo_bbl
    for r in snapshot.requirements:
        due[r.due_turn] += r.volume_bbl
    delivered = sum(b.delivered_bbl for b in state.batches)
    consumed = sum(r.volume_bbl for r in snapshot.requirements if r.due_turn <= tick)
    opening = snapshot.base.initial_destination_bbl + delivered - consumed
    stock = opening
    end = max(tick + spec.config()['inventory_projection_turns'], max(arrivals, default=tick),
              max(trial_due, default=tick), max(due, default=tick))
    path = []
    for when in range(tick, end + 1):
        if when > tick:
            stock += arrivals[when] + trial_due[when] - due[when]
        path.append({'turn': when, 'known_existing_arrivals_bbl': arrivals[when],
                     'trial_arrivals_bbl': trial_due[when], 'known_requirement_bbl': due[when],
                     **stock_report(stock, spec.destination_band)})
    violations = [f'source:{o}' for o, row in sources.items() if row['closing']['hard_bound_violation']]
    violations += [f'destination:turn:{r["turn"]}' for r in path if r['hard_bound_violation']]
    closing_source = sum(r['closing']['stock_bbl'] for r in sources.values())
    transit = sum(b.transit_bbl for b in state.batches) + sum(loaded.values())
    initial = sum(v for _, v in snapshot.base.initial_cargo_by_origin_bbl)
    released = sum(v for _, v in snapshot.base.cumulative_releases_bbl) + sum(releases.values())
    residual = closing_source + transit + opening + consumed - initial - released - snapshot.base.initial_destination_bbl
    return {'sources': sources,
            'destination': {'opening': stock_report(opening, spec.destination_band),
                            'closing_current_turn': stock_report(opening, spec.destination_band),
                            'cumulative_requirement_bbl': consumed,
                            'trial_ordered_bbl_is_not_current_imports': True, 'future_path': path,
                            'projection_scope': 'existing_and_trial_arrivals_minus_explicit_known_requirements'},
            'hard_bound_violations': violations, 'valid': not violations,
            'physical_mass_balance_residual_bbl': residual}
