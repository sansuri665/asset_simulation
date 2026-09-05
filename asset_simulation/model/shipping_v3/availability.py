"""Rebuild exact-arrival buckets from frozen physical plans on every call.

Buckets are marginal arrivals, NOT cumulative fleets. An open ship is counted
once at h=0; a committed ballast ship once at its ready_turn; an uncommitted
empty at the destination or a loaded ship has no guessed next origin.
"""
from __future__ import annotations

from typing import Any

from .types import MarketSpec, MarketState


def build_availability(state: MarketState, spec: MarketSpec) -> dict[str, Any]:
    if state.spec_hash != spec.identity:
        raise ValueError('availability state/spec mismatch')
    horizon = len(spec.config()['availability']['arrival_weights']) - 1
    buckets = {o: [{'horizon_turns': h, 'ready_turn': state.turn + h,
                    'capacity_bbl': 0, 'by_class_bbl': {}, 'ships': []}
                   for h in range(horizon + 1)] for o in spec.origins}
    beyond, uncommitted, loaded, incompatible = [], [], [], []
    seen = set()
    for ship in state.ships:
        if ship.ship_id in seen:
            raise ValueError('ship ID duplicated in availability input')
        seen.add(ship.ship_id)
        plan = ship.movement
        if plan is None:
            if ship.location == spec.destination:
                uncommitted.append(ship.ship_id)
                continue
            if ship.location not in spec.origins:
                raise ValueError('unknown open-ship geography')
            target, h = ship.location, 0
        elif plan.kind == 'ballast':
            if ship.location is not None or plan.destination not in spec.origins or plan.ready_turn < state.turn:
                raise ValueError('invalid scheduled ballast')
            target, h = plan.destination, plan.ready_turn - state.turn
        elif plan.kind == 'laden':
            loaded.append(ship.ship_id)
            continue
        else:
            raise ValueError('unknown vessel movement')
        services = {s.class_id for s in spec.physical.lane(target).services}
        if ship.class_id not in services:
            incompatible.append(ship.ship_id)
            continue
        item = {'ship_id': ship.ship_id, 'class_id': ship.class_id,
                'capacity_bbl': ship.capacity_bbl, 'target_origin': target,
                'ready_turn': state.turn + h, 'horizon_turns': h,
                'basis': 'open_here' if plan is None else 'committed_ballast'}
        if h > horizon:
            beyond.append(item)
        else:
            bucket = buckets[target][h]
            bucket['capacity_bbl'] += ship.capacity_bbl
            bucket['by_class_bbl'][ship.class_id] = bucket['by_class_bbl'].get(ship.class_id, 0) + ship.capacity_bbl
            bucket['ships'].append(item)
    return {'routes': buckets, 'horizon_turns': horizon, 'current_turn': state.turn,
            'known_beyond_horizon': beyond,
            'uncommitted_destination_ship_ids': uncommitted,
            'loaded_without_committed_next_origin_ship_ids': loaded,
            'incompatible_open_ship_ids': incompatible,
            'scope': 'one_ship_one_exact_arrival_bucket; no_future_dispatch_assumptions'}
