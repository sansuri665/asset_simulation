"""Replaceable TEST policies, deliberately not part of the price engine.

Full lots are packed first; at most one tail hull per origin may depart if its
load factor meets a configurable test threshold. Routing covers work with
capacity; it never reads price, costs, future Seed demand or a class quota.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace

from ..mixed_cargo_market import choose_full_load_mix
from .types import BallastOrder, Decision, LoadOrder, MarketSnapshot, MarketSpec, shift_plan


def demo_decision(snapshot: MarketSnapshot, spec: MarketSpec) -> Decision:
    state = snapshot.opened_state; cfg = spec.config(); policy = cfg['demo_policy']
    scheduled = snapshot.scheduled(); ships = list(state.ships)
    loads = []
    owed = {o: sum(b.remaining_bbl for b in state.batches if b.origin==o) for o in spec.origins}
    for lane in spec.physical.lanes:
        o = lane.origin; services = {s.class_id: s for s in lane.services}
        ids = {c: [s.ship_id for s in ships if s.location==o and s.class_id==c] for c in services}
        chosen = choose_full_load_mix(owed[o], {c:s.capacity_bbl for c,s in services.items()}, {c:len(v) for c,v in ids.items()})
        for c, count in chosen.items():
            for sid in ids[c][:count]:
                loads.append(LoadOrder(sid, services[c].capacity_bbl))
                owed[o] -= services[c].capacity_bbl
        occupied = {order.ship_id for order in loads}
        tail_candidates = [s for s in ships if s.location==o and s.ship_id not in occupied and s.class_id in services
                           and 0 < owed[o] < s.capacity_bbl
                           and owed[o]/s.capacity_bbl >= policy['minimum_tail_load_fraction']]
        if tail_candidates:
            tail = min(tail_candidates, key=lambda s: (s.capacity_bbl, s.ship_id))
            loads.append(LoadOrder(tail.ship_id, owed[o])); owed[o] = 0
    for order in loads:
        ship = ships[order.ship_id-1]
        template = spec.physical.lane(ship.location).service(ship.class_id).outbound
        # Private allocation preview only; actual manifests are built by settle.
        ships[ship.ship_id-1] = replace(ship, location=None, movement=shift_plan(template,state.turn,cargo_bbl=order.cargo_bbl))
    lanes = {l.origin:l for l in spec.physical.lanes}
    plans = {(p.origin,p.destination,p.vessel_class):p for p in spec.physical.ballast_legs}
    def assigned(ship):
        if ship.movement:
            return ship.movement.origin if ship.movement.kind=='laden' else ship.movement.destination
        return ship.location if ship.location in spec.origins else None
    committed = Counter()
    for ship in ships:
        o = assigned(ship)
        if o:
            s = lanes[o].service(ship.class_id)
            committed[o] += s.capacity_bbl/s.cycle_turns
    targets = {}
    signals = {s.origin:s for s in state.signals}
    for o,lane in lanes.items():
        rho = policy['forecast_persistence']
        recent = rho*signals[o].forecast_bbl+(1-rho)*scheduled[o]
        recovery = min(.25*owed[o], .2*scheduled[o]) if scheduled[o] else min(owed[o],lane.reference_daily_bbl*10)
        targets[o] = (recent+recovery)*(1+policy['spare_loading_fraction']/lane.reference_cycle_turns)
    orders = []
    def choose(ship, require_gap=False):
        candidates=[]
        for o,lane in lanes.items():
            if o==ship.location or not any(s.class_id==ship.class_id for s in lane.services):
                continue
            if not scheduled[o] and owed[o] < ship.capacity_bbl*policy['minimum_tail_load_fraction']:
                continue
            gap=targets[o]-committed[o]
            if require_gap and gap<=0:
                continue
            candidates.append((gap/max(targets[o],lane.reference_daily_bbl),-plans[ship.location,o,ship.class_id].ready_turn,o))
        return max(candidates)[2] if candidates else None
    def send(index, o):
        ship=ships[index]; old=assigned(ship)
        if old:
            s=lanes[old].service(ship.class_id);committed[old]-=s.capacity_bbl/s.cycle_turns
        s=lanes[o].service(ship.class_id);committed[o]+=s.capacity_bbl/s.cycle_turns
        orders.append(BallastOrder(ship.ship_id,o))
        ships[index]=replace(ship,location=None,movement=shift_plan(plans[ship.location,o,ship.class_id],state.turn))
    for i,ship in enumerate(ships):
        if ship.location==spec.destination:
            target=ship.home_origin if policy['routing_mode']=='home_return' else choose(ship)
            if target is not None:
                send(i,target)
    count=0
    if policy['routing_mode']=='pooled':
        for i,ship in enumerate(ships):
            o=ship.location
            if o not in lanes or count>=policy['cross_origin_limit']:
                continue
            local=sum(s.capacity_bbl for s in ships if s.location==o)
            incoming=sum(s.capacity_bbl for s in ships if s.movement and s.movement.kind=='ballast'
                         and s.movement.destination==o and s.movement.ready_turn<=state.turn+1)
            if owed[o]>=ship.capacity_bbl*policy['minimum_tail_load_fraction'] or committed[o]<=targets[o] or local+incoming-ship.capacity_bbl<scheduled[o]:
                continue
            target=choose(ship,True)
            if target is not None:
                send(i,target);count+=1
    return Decision(snapshot.snapshot_id, tuple(loads), tuple(orders),
                    f'demo_full_then_one_tail_{policy["minimum_tail_load_fraction"]:g}_{policy["routing_mode"]}')


def hold_all(snapshot: MarketSnapshot, spec: MarketSpec) -> Decision:
    """Explicit no-action policy. Idle at an origin is still offered supply."""
    return Decision(snapshot.snapshot_id, policy='external_hold_all')
