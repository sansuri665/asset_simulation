"""Causal batch/ship engine: open -> quote -> external decision -> settle.

No market decision mutates the source state. Full cargo histories are kept;
only signed price pressure decays. A partial load locks one entire hull for
the same declared voyage. Departure quotes are not retroactively repriced.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, replace
import json
import math
from typing import Any, Mapping, Sequence

from ..bounded_route_pricing import align_pressure_with_gap, bounded_pressure
from ..global_shipping_contract import CLASSES, integer
from ..mixed_cargo_market import build_mixed_inputs, initial_mixed_market
from ..multi_origin_pricing import finite_signed
from ..registry import sha256_json
from .availability import build_availability
from .pricing import quote_routes, recompute_price, urgency_signal
from .types import (
    VERSION, BatchOrder, CargoBatch, CargoSlice, Decision, MarketSnapshot,
    MarketSpec, MarketState, OriginSignal, Vessel, make_market_spec,
    shift_plan, stable_fingerprint,
)


def validate_state(state: MarketState, spec: MarketSpec) -> None:
    if state.spec_hash != spec.identity or state.phase not in ('ready', 'open'):
        raise ValueError('state/spec or lifecycle phase mismatch')
    integer(state.turn, 'turn')
    actual_registry = tuple((s.ship_id, s.class_id, s.capacity_bbl, s.home_origin) for s in state.ships)
    if actual_registry != state.initial_registry or tuple(s.ship_id for s in state.ships) != tuple(range(1, len(state.ships)+1)):
        raise ValueError('permanent hull registry changed')
    cfg = spec.config(); capacities = dict(spec.physical.capacities)
    if tuple(s.origin for s in state.signals) != spec.origins:
        raise ValueError('one signal state per origin required')
    lo, _ = urgency_signal(-cfg['pressure']['limit_days'], cfg)
    hi, _ = urgency_signal(cfg['pressure']['limit_days'], cfg)
    for signal in state.signals:
        if abs(finite_signed(signal.pressure_days, 'origin pressure')) > cfg['pressure']['limit_days'] + 1e-10:
            raise ValueError('origin pressure outside bounds')
        finite_signed(signal.market_log_signal, 'market signal')
        if not lo-1e-9 <= finite_signed(signal.urgency_log_signal, 'urgency signal') <= hi+1e-9:
            raise ValueError('unbounded stored urgency')
        if finite_signed(signal.forecast_bbl, 'demo coverage estimate') < 0:
            raise ValueError('negative demo coverage')
    if abs(finite_signed(state.destination_pressure, 'destination pressure')) > cfg['pressure']['limit_days'] + 1e-10:
        raise ValueError('destination pressure outside bounds')
    batches = {}
    for batch in state.batches:
        if not isinstance(batch.batch_id, str) or not batch.batch_id or batch.batch_id in batches:
            raise ValueError('empty or duplicate batch ID')
        if batch.origin not in spec.origins or batch.destination != spec.destination:
            raise ValueError('batch has wrong route')
        for name in ('total_bbl', 'remaining_bbl', 'delivered_bbl', 'due_turn'):
            integer(getattr(batch, name), name)
        if type(batch.created_turn) is not int or batch.created_turn > state.turn:
            raise ValueError('invalid batch creation time')
        if type(batch.bootstrap) is not bool or (batch.created_turn < 0) != batch.bootstrap:
            raise ValueError('only bootstrap cargo may precede turn zero')
        if batch.bootstrap != batch.batch_id.startswith('bootstrap:'):
            raise ValueError('bootstrap namespace mismatch')
        if batch.due_turn < batch.created_turn or batch.total_bbl <= 0 or batch.transit_bbl < 0:
            raise ValueError('invalid batch quantity or immutable service date')
        batches[batch.batch_id] = batch
    templates = {('laden', l.origin, spec.destination, s.class_id): s.outbound
                 for l in spec.physical.lanes for s in l.services}
    templates.update({('ballast', p.origin, p.destination, p.vessel_class): p for p in spec.physical.ballast_legs})
    manifests = Counter()
    for ship in state.ships:
        if ship.class_id not in capacities or ship.capacity_bbl != capacities[ship.class_id] or ship.home_origin not in spec.origins or ship.last_origin not in spec.origins:
            raise ValueError('fixed ship capacity or class changed')
        p = ship.movement
        if p is None:
            if ship.location not in (*spec.origins, spec.destination) or ship.manifest or ship.booked_net_service_value_per_bbl is not None:
                raise ValueError('open hull has unknown position or retained cargo')
            continue
        if ship.location is not None or p.vessel_class != ship.class_id or p.catalog_hash != spec.physical.physical_hash:
            raise ValueError('moving ship is simultaneously open or has changed class/path')
        if p.ready_turn < state.turn or (state.phase == 'ready' and p.depart_turn >= state.turn):
            raise ValueError('unprocessed or future movement')
        template = templates.get((p.kind, p.origin, p.destination, ship.class_id))
        if template is None or replace(p, depart_turn=0, ready_turn=p.ready_turn-p.depart_turn, cargo_bbl=template.cargo_bbl) != template:
            raise ValueError('frozen movement geometry or duration changed')
        if p.kind == 'ballast':
            if p.cargo_bbl or ship.manifest or ship.booked_net_service_value_per_bbl is not None:
                raise ValueError('ballast cannot carry cargo or earn service value')
            continue
        integer(p.cargo_bbl, 'voyage cargo')
        if not 0 < p.cargo_bbl <= ship.capacity_bbl or sum(x.cargo_bbl for x in ship.manifest) != p.cargo_bbl:
            raise ValueError('partial-load manifest does not match actual voyage cargo')
        if len({x.batch_id for x in ship.manifest}) != len(ship.manifest):
            raise ValueError('duplicate slice for batch in same ship')
        for piece in ship.manifest:
            integer(piece.cargo_bbl, 'manifest slice')
            batch = batches.get(piece.batch_id)
            if not piece.cargo_bbl or batch is None or batch.origin != p.origin or batch.created_turn > p.depart_turn:
                raise ValueError('manifest refers to missing, wrong-route or future cargo')
            manifests[piece.batch_id] += piece.cargo_bbl
        if p.depart_turn < 0:
            if not all(batches[x.batch_id].bootstrap for x in ship.manifest):
                raise ValueError('prehistory voyage contains new game cargo')
        elif ship.booked_net_service_value_per_bbl is None or finite_signed(ship.booked_net_service_value_per_bbl, 'booked reference value') <= 0:
            raise ValueError('new laden movement needs its frozen quote benchmark')
    if any(b.transit_bbl != manifests[b.batch_id] for b in state.batches):
        raise ValueError('batch transit differs from actual vessel manifests')


def initial_market(spec: MarketSpec, fleet_counts: Mapping[str, int] | None = None, *,
                   initialization: str = 'phased') -> MarketState:
    """Static-reference initialization only; no future seeded cargo consulted."""
    counts = spec.config()['default_fleet_counts'] if fleet_counts is None else fleet_counts
    legacy = initial_mixed_market(spec.physical, counts, initialization=initialization)
    offset = legacy.next_turn
    ships, batches = [], []
    capacities = dict(spec.physical.capacities)
    for hull in legacy.ships:
        p = hull.movement
        manifest = ()
        if p is not None:
            p = replace(p, depart_turn=p.depart_turn-offset, ready_turn=p.ready_turn-offset)
            if p.kind == 'laden':
                bid = f'bootstrap:{hull.ship_id}'
                manifest = (CargoSlice(bid, p.cargo_bbl),)
                batches.append(CargoBatch(bid, p.origin, spec.destination, p.depart_turn, p.ready_turn,
                                          p.cargo_bbl, 0, 0, True))
        ships.append(Vessel(hull.ship_id, hull.class_id, capacities[hull.class_id], hull.home_origin,
                            hull.last_origin, hull.location, p, manifest))
    signals = tuple(OriginSignal(l.origin, forecast_bbl=l.reference_daily_bbl*10) for l in spec.physical.lanes)
    registry = tuple((s.ship_id, s.class_id, s.capacity_bbl, s.home_origin) for s in ships)
    result = MarketState(spec.identity, registry, 0, tuple(ships), tuple(batches), signals)
    validate_state(result, spec)
    return result


def cargo_metrics(state: MarketState, spec: MarketSpec, *, through_turn: int) -> dict[str, Any]:
    rows = {}
    for origin in spec.origins:
        batches = [b for b in state.batches if b.origin == origin]
        queued = [b for b in batches if b.remaining_bbl]
        total = sum(b.total_bbl for b in batches)
        remaining = sum(b.remaining_bbl for b in batches)
        delivered = sum(b.delivered_bbl for b in batches)
        transit = sum(b.transit_bbl for b in batches)
        due = sum(b.total_bbl for b in batches if b.due_turn <= through_turn)
        oldest = max((through_turn-b.created_turn for b in queued), default=0)
        rows[origin] = {
            'origin_unshipped_bbl': remaining, 'actual_in_transit_bbl': transit,
            'cumulative_delivered_bbl': delivered, 'cumulative_reference_delivered_bbl': due,
            'reference_in_transit_bbl': total-due,
            'destination_deviation_bbl': delivered-due,
            'oldest_unshipped_age_turns': oldest,
            'volume_weighted_unshipped_age_turns': sum((through_turn-b.created_turn)*b.remaining_bbl for b in queued)/remaining if remaining else 0.0,
            'overdue_unshipped_bbl': sum(b.remaining_bbl for b in queued if b.due_turn <= through_turn),
            'overdue_undelivered_bbl': sum(b.total_bbl-b.delivered_bbl for b in batches if b.due_turn <= through_turn),
            'oldest_undelivered_lateness_turns': max((through_turn-b.due_turn for b in batches if b.due_turn<=through_turn and b.delivered_bbl<b.total_bbl), default=0),
            'barrel_conservation_residual': total-remaining-transit-delivered,
            'plan_conservation_residual': remaining+transit-(total-due)+(delivered-due),
        }
    return rows


def _snapshot_id(state: MarketState, scheduled: str, quotes: str, events: str, pressure: float, cpi: float) -> str:
    return stable_fingerprint((state, scheduled, quotes, events, pressure, cpi))


def prepare_turn(state: MarketState, spec: MarketSpec, *,
                 scheduled_by_origin_bbl: Mapping[str, int] | None = None,
                 new_batches: Sequence[BatchOrder] | None = None,
                 cpi: float = 100.0) -> MarketSnapshot:
    """Complete arrivals and quote once, before looking at any new decision."""
    validate_state(state, spec)
    if state.phase != 'ready':
        raise ValueError('an already opened snapshot cannot be opened again')
    if scheduled_by_origin_bbl is not None and new_batches is not None:
        raise ValueError('provide aggregate current cargo OR explicit batches, never both')
    cpi = finite_signed(cpi, 'CPI')
    if cpi <= 0:
        raise ValueError('CPI must be positive')
    if scheduled_by_origin_bbl is not None:
        if set(scheduled_by_origin_bbl) != set(spec.origins):
            raise ValueError('each route needs its current cargo, including zero')
        lags = dict(spec.due_lags); new = []
        for o in spec.origins:
            volume = scheduled_by_origin_bbl[o]; integer(volume, 'new cargo')
            if volume:
                new.append(BatchOrder(f'cargo:{o}:{state.turn}', o, volume, state.turn+lags[o]))
    else:
        new = list(new_batches or ())
    batches = {b.batch_id: b for b in state.batches}
    scheduled = {o: 0 for o in spec.origins}
    for order in new:
        if not isinstance(order, BatchOrder) or not isinstance(order.batch_id, str) or not order.batch_id or order.batch_id.startswith('bootstrap:') or order.batch_id in batches:
            raise ValueError('new cargo requires a unique ordinary batch ID')
        integer(order.volume_bbl, 'batch volume'); integer(order.due_turn, 'due turn')
        if order.volume_bbl == 0 or order.origin not in spec.origins or order.due_turn < state.turn:
            raise ValueError('invalid new batch route, amount or service date')
        batches[order.batch_id] = CargoBatch(order.batch_id, order.origin, spec.destination, state.turn,
                                           order.due_turn, order.volume_bbl, order.volume_bbl)
        scheduled[order.origin] += order.volume_bbl
    ships, deliveries, ballast_arrivals = [], [], []
    reference_due = sum(b.total_bbl for b in batches.values() if b.due_turn == state.turn)
    actual_due = 0
    for ship in state.ships:
        p = ship.movement
        if p is not None and p.ready_turn == state.turn:
            if p.kind == 'laden':
                actual_due += p.cargo_bbl
                pieces = []
                for piece in ship.manifest:
                    b = batches[piece.batch_id]
                    batches[b.batch_id] = replace(b, delivered_bbl=b.delivered_bbl+piece.cargo_bbl)
                    pieces.append({'batch_id': b.batch_id, 'cargo_bbl': piece.cargo_bbl,
                                   'due_turn': b.due_turn, 'lateness_turns': state.turn-b.due_turn,
                                   'bootstrap': b.bootstrap})
                deliveries.append({'ship_id': ship.ship_id, 'class_id': ship.class_id, 'origin': p.origin,
                                   'cargo_bbl': p.cargo_bbl, 'turn': state.turn, 'batch_slices': pieces,
                                   'frozen_net_service_value_per_bbl': ship.booked_net_service_value_per_bbl})
            else:
                ballast_arrivals.append({'ship_id': ship.ship_id, 'origin': p.origin, 'destination': p.destination,
                                        'turn': state.turn})
            ship = replace(ship, location=p.destination, movement=None, manifest=(), booked_net_service_value_per_bbl=None)
        ships.append(ship)
    cfg = spec.config(); daily = sum(l.reference_daily_bbl for l in spec.physical.lanes)
    deviation = sum(b.delivered_bbl-(b.total_bbl if b.due_turn<=state.turn else 0) for b in batches.values())
    pressure = bounded_pressure(state.destination_pressure, (reference_due-actual_due)/daily, config=cfg, decay=False)
    pressure = align_pressure_with_gap(pressure, -deviation)
    opened = replace(state, ships=tuple(ships), batches=tuple(batches.values()), phase='open')
    validate_state(opened, spec)
    availability = build_availability(opened, spec)
    quote = quote_routes(spec, scheduled_by_origin_bbl=scheduled, availability=availability,
                         signals={s.origin: s for s in state.signals}, destination_pressure=pressure, cpi=cpi)
    quote['availability'] = availability
    events = {'deliveries': deliveries, 'ballast_arrivals': ballast_arrivals,
              'new_batch_ids': [b.batch_id for b in new], 'reference_due_bbl': reference_due,
              'actual_arrived_bbl': actual_due}
    sj = json.dumps(scheduled, sort_keys=True, separators=(',', ':'))
    qj = json.dumps(quote, sort_keys=True, separators=(',', ':'), allow_nan=False)
    ej = json.dumps(events, sort_keys=True, separators=(',', ':'), allow_nan=False)
    sid = _snapshot_id(opened, sj, qj, ej, pressure, cpi)
    return MarketSnapshot(sid, opened, sj, qj, ej, pressure, cpi)


def settle_turn(snapshot: MarketSnapshot, spec: MarketSpec, decision: Decision, *,
                include_events: bool = False) -> tuple[MarketState, dict[str, Any]]:
    """Apply a given plan atomically, never requoting within this turn."""
    state = snapshot.opened_state
    validate_state(state, spec)
    sid = _snapshot_id(state, snapshot.scheduled_json, snapshot.quote_json, snapshot.events_json,
                       snapshot.destination_pressure_open, snapshot.cpi)
    if state.phase != 'open' or sid != snapshot.snapshot_id or decision.snapshot_id != sid:
        raise ValueError('stale, altered or wrong-turn decision snapshot')
    scheduled = snapshot.scheduled(); quote = snapshot.quotes(); cfg = spec.config()
    all_ids = [x.ship_id for x in (*decision.loads, *decision.ballasts)]
    if len(set(all_ids)) != len(all_ids):
        raise ValueError('one hull can only depart once per turn')
    ships = list(state.ships); batches = {b.batch_id: b for b in state.batches}
    departures, ballast_orders = [], []
    loaded = Counter(); loaded_by_class = {o: Counter() for o in spec.origins}
    dispatched = {o: Counter() for o in spec.origins}; used_capacity = Counter()
    partial_count = Counter()
    for order in decision.loads:
        integer(order.ship_id, 'ship ID'); integer(order.cargo_bbl, 'actual load')
        if not 1 <= order.ship_id <= len(ships):
            raise ValueError('unknown load vessel')
        ship = ships[order.ship_id-1]
        if ship.movement is not None or ship.location not in spec.origins:
            raise ValueError('load vessel is not physically open at an origin')
        if not 0 < order.cargo_bbl <= ship.capacity_bbl:
            raise ValueError('actual load must be positive and no greater than vessel capacity')
        o = ship.location; service = spec.physical.lane(o).service(ship.class_id)
        queue = sorted((b for b in batches.values() if b.origin==o and b.remaining_bbl),
                       key=lambda b: (b.due_turn, b.created_turn, b.batch_id))
        if sum(b.remaining_bbl for b in queue) < order.cargo_bbl:
            raise ValueError('load exceeds actual shared cargo; future cargo cannot be borrowed')
        todo = order.cargo_bbl; manifest = []
        for b in queue:
            if not todo:
                break
            take = min(todo, b.remaining_bbl); todo -= take
            manifest.append(CargoSlice(b.batch_id, take))
            batches[b.batch_id] = replace(b, remaining_bbl=b.remaining_bbl-take)
        plan = shift_plan(service.outbound, state.turn, cargo_bbl=order.cargo_bbl)
        price = quote['routes'][o]['net_service_value_real_usd_per_bbl']
        ships[ship.ship_id-1] = replace(ship, location=None, movement=plan, manifest=tuple(manifest),
                                      last_origin=o, booked_net_service_value_per_bbl=price)
        loaded[o] += order.cargo_bbl; used_capacity[o] += ship.capacity_bbl
        loaded_by_class[o][ship.class_id] += order.cargo_bbl; dispatched[o][ship.class_id] += 1
        partial_count[o] += order.cargo_bbl < ship.capacity_bbl
        departures.append({'ship_id': ship.ship_id, 'class_id': ship.class_id, 'origin': o,
                           'depart_turn': state.turn, 'ready_turn': plan.ready_turn,
                           'capacity_bbl': ship.capacity_bbl, 'cargo_bbl': order.cargo_bbl,
                           'load_factor': order.cargo_bbl/ship.capacity_bbl,
                           'batch_slices': [asdict(m) for m in manifest],
                           'booked_net_service_reference_value_real': price*order.cargo_bbl,
                           'reference_cycle_days': service.cycle_turns*10,
                           'actual_load_reference_tce_real': price*order.cargo_bbl/(service.cycle_turns*10),
                           'not_cash_or_invoice': True})
    plans = {(p.origin, p.destination, p.vessel_class): p for p in spec.physical.ballast_legs}
    for order in decision.ballasts:
        integer(order.ship_id, 'ballast ship ID')
        if not 1 <= order.ship_id <= len(ships):
            raise ValueError('unknown ballast vessel')
        ship = ships[order.ship_id-1]
        if ship.movement is not None or order.target_origin not in spec.origins or ship.location == order.target_origin:
            raise ValueError('in-transit voyages cannot be retargeted; ballast requires a distinct open location')
        spec.physical.lane(order.target_origin).service(ship.class_id)
        template = plans.get((ship.location, order.target_origin, ship.class_id))
        if template is None:
            raise ValueError('unknown or incompatible ballast route')
        p = shift_plan(template, state.turn)
        ships[ship.ship_id-1] = replace(ship, location=None, movement=p)
        ballast_orders.append({'ship_id': ship.ship_id, 'class_id': ship.class_id, 'origin': ship.location,
                               'target': order.target_origin, 'ready_turn': p.ready_turn,
                               'changed_origin': ship.last_origin != order.target_origin})
    metrics_state = replace(state, ships=tuple(ships), batches=tuple(batches.values()))
    metrics = cargo_metrics(metrics_state, spec, through_turn=state.turn)
    signals = []
    for old in state.signals:
        o = old.origin; daily = spec.physical.lane(o).reference_daily_bbl
        pressure = bounded_pressure(old.pressure_days, (scheduled[o]-loaded[o])/daily, config=cfg, decay=True)
        pressure = align_pressure_with_gap(pressure, metrics[o]['origin_unshipped_bbl'])
        e = quote['routes'][o]['explanation']; rho = cfg['demo_policy']['forecast_persistence']
        signals.append(OriginSignal(o, pressure, e['stored_market_log_signal'], e['settled_urgency_log_signal'],
                                    rho*old.forecast_bbl+(1-rho)*scheduled[o]))
    final = replace(metrics_state, turn=state.turn+1, phase='ready', signals=tuple(signals),
                    destination_pressure=bounded_pressure(snapshot.destination_pressure_open, 0, config=cfg, decay=True))
    validate_state(final, spec)
    events = json.loads(snapshot.events_json)
    rows = {}
    for o in spec.origins:
        q = quote['routes'][o]
        rows[o] = {**q, **metrics[o], 'scheduled_cargo_bbl': scheduled[o], 'loaded_bbl': loaded[o],
                   'loaded_by_class_bbl': {c: loaded_by_class[o][c] for c in CLASSES},
                   'dispatched_by_class': {c: dispatched[o][c] for c in CLASSES},
                   'dispatched_capacity_bbl': used_capacity[o], 'partial_load_ships': partial_count[o],
                   'mean_dispatched_load_factor': loaded[o]/used_capacity[o] if used_capacity[o] else None,
                   'executed_benchmark': q['route_benchmark_real_tce'] if loaded[o] else None,
                   'quote_reconstruction_error': abs(recompute_price(q['explanation'])-q['route_benchmark_real_tce'])}
    availability = quote['availability']
    compact_availability = {o: [{k: v for k, v in b.items() if k != 'ships'} | {'ship_count': len(b['ships'])}
                                for b in availability['routes'][o]] for o in spec.origins}
    committed = Counter()
    for ship in ships:
        if ship.movement:
            origin = ship.movement.origin if ship.movement.kind=='laden' else ship.movement.destination
        else:
            origin = ship.location
        if origin in spec.origins:
            service = spec.physical.lane(origin).service(ship.class_id)
            committed[origin] += ship.capacity_bbl/service.cycle_turns
    record = {'shipping_turn_index': state.turn, 'operating_day_start': 10*state.turn, 'turn_days': 10,
              'snapshot_id': sid, 'decision_policy': decision.policy, 'routes': rows,
              'availability': compact_availability, 'availability_hash': sha256_json(availability),
              'weighted_local_residual': quote['weighted_local_residual'],
              'shared_destination_deviation_bbl': sum(r['destination_deviation_bbl'] for r in metrics.values()),
              'destination_pressure_open': snapshot.destination_pressure_open,
              'committed_service_bbl_per_turn': dict(committed),
              'fleet_counts': {c: sum(s.class_id==c for s in ships) for c in CLASSES},
              'fleet_conservation_residual': len(ships)-len(state.initial_registry),
              'barrel_conservation_residual': sum(m['barrel_conservation_residual'] for m in metrics.values()),
              'plan_conservation_residual': sum(m['plan_conservation_residual'] for m in metrics.values()),
              'switched_origin_orders': sum(b['changed_origin'] for b in ballast_orders),
              'delivered_late_bbl': sum(x['cargo_bbl'] for d in events['deliveries'] for x in d['batch_slices'] if not x['bootstrap'] and x['lateness_turns']>0),
              'delivered_lateness_bbl_turns': sum(x['cargo_bbl']*max(0,x['lateness_turns']) for d in events['deliveries'] for x in d['batch_slices'] if not x['bootstrap']),
              'ordinary_delivered_bbl': sum(x['cargo_bbl'] for d in events['deliveries'] for x in d['batch_slices'] if not x['bootstrap'])}
    if include_events:
        record.update(events=events, departures=departures, ballast_orders=ballast_orders,
                      availability_details=availability)
    return final, record


def step_market(state: MarketState, spec: MarketSpec, *, scheduled_by_origin_bbl: Mapping[str, int] | None = None,
                new_batches: Sequence[BatchOrder] | None = None, cpi: float = 100.0,
                decision_factory=None, include_events: bool = False) -> tuple[MarketState, dict[str, Any]]:
    snapshot = prepare_turn(state, spec, scheduled_by_origin_bbl=scheduled_by_origin_bbl, new_batches=new_batches, cpi=cpi)
    if decision_factory is None:
        from .policies import demo_decision
        decision = demo_decision(snapshot, spec)
    else:
        decision = decision_factory(snapshot, spec)
    return settle_turn(snapshot, spec, decision, include_events=include_events)


def _quantile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values); t = (len(v)-1)*p; i = int(t)
    return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(t-i)


def summarize(records: Sequence[Mapping[str, Any]], *, warmup_turns: int = 36) -> dict[str, Any]:
    integer(warmup_turns, 'warmup')
    if warmup_turns >= len(records):
        raise ValueError('post-warmup observations required')
    rows = records[warmup_turns:]; routes = {}
    for origin in rows[0]['routes']:
        rr = [r['routes'][origin] for r in rows]; prices = [r['route_benchmark_real_tce'] for r in rr]
        daily = sum(r['scheduled_cargo_bbl'] for r in rr)/len(rr)/10
        mean = sum(prices)/len(prices); loaded = sum(r['loaded_bbl'] for r in rr)
        capacity = sum(r['dispatched_capacity_bbl'] for r in rr)
        executed = [r['route_benchmark_real_tce'] for r in rr if r['loaded_bbl']]
        routes[origin] = {
            'mean_full_cargo_mbd': daily/1e6, 'benchmark_median': _quantile(prices,.5),
            'benchmark_p95': _quantile(prices,.95), 'benchmark_min': min(prices), 'benchmark_max': max(prices),
            'benchmark_cv': math.sqrt(sum((x-mean)**2 for x in prices)/len(prices))/mean,
            'executed_window_benchmark_p95': _quantile(executed,.95),
            'executed_volume_weighted_benchmark': sum(r['route_benchmark_real_tce']*r['loaded_bbl'] for r in rr)/loaded if loaded else None,
            'no_current_supply_turns': sum(r['current_prompt_capacity_bbl']==0 for r in rr),
            'no_execution_turns': sum(r['loaded_bbl']==0 for r in rr),
            'numeric_price_guard_turns': sum(r['explanation']['numeric_guard_hit'] for r in rr),
            'over_100k_turns': sum(x>100000 for x in prices), 'over_150k_turns': sum(x>150000 for x in prices),
            'max_unshipped_days': max(r['origin_unshipped_bbl'] for r in rr)/daily if daily else None,
            'closing_unshipped_bbl': rr[-1]['origin_unshipped_bbl'], 'loaded_bbl': loaded,
            'max_oldest_unshipped_age_turns': max(r['oldest_unshipped_age_turns'] for r in rr),
            'partial_load_ship_count': sum(r['partial_load_ships'] for r in rr),
            'mean_dispatched_load_factor': loaded/capacity if capacity else None,
            'actual_cargo_share_by_class': {c: sum(r['loaded_by_class_bbl'][c] for r in rr)/loaded if loaded else None for c in CLASSES},
        }
    delivered = sum(r['ordinary_delivered_bbl'] for r in rows)
    return {'observed_turns': len(rows), 'operating_days': len(rows)*10, 'fleet_counts': rows[0]['fleet_counts'],
            'routes': routes, 'switched_origin_orders': sum(r['switched_origin_orders'] for r in rows),
            'mean_delivered_lateness_turns': sum(r['delivered_lateness_bbl_turns'] for r in rows)/delivered if delivered else None,
            'late_delivered_fraction': sum(r['delivered_late_bbl'] for r in rows)/delivered if delivered else None,
            'max_quote_reconstruction_error': max(x['quote_reconstruction_error'] for r in records for x in r['routes'].values()),
            'conservation_exact': all(r['fleet_conservation_residual']==r['barrel_conservation_residual']==r['plan_conservation_residual']==0 for r in records)}


def run_market(spec: MarketSpec, inputs: Sequence[Mapping[str, Any]], *, fleet_counts: Mapping[str,int] | None = None,
               initialization: str = 'phased', warmup_turns: int | None = None,
               include_events: bool = False, decision_factory=None) -> dict[str, Any]:
    state = initial_market(spec, fleet_counts, initialization=initialization)
    first_hash = sha256_json(asdict(state)); rows = []
    for item in inputs:
        state, row = step_market(state, spec, scheduled_by_origin_bbl=item.get('scheduled_by_origin_bbl'),
                                 new_batches=item.get('new_batches'), cpi=item.get('cpi',100.0),
                                 decision_factory=decision_factory, include_events=include_events)
        for key in ('label','year','month','turn_in_month','cpi_information_year'):
            if key in item:
                row[key] = item[key]
        rows.append(row)
    result = {'model_version': VERSION, 'spec_hash': spec.identity, 'spec': asdict(spec),
              'input_hash': stable_fingerprint(tuple(inputs)), 'initial_state_hash': first_hash,
              'final_state_hash': sha256_json(asdict(state)),
              'summary': summarize(rows, warmup_turns=spec.config()['warmup_turns'] if warmup_turns is None else warmup_turns),
              'turns': rows, 'scope': 'research_service_indications_not_cost_or_global_equilibrium'}
    result['result_hash'] = sha256_json(result)
    return result


def run_seeded_market(*, seed: int = 42, years: int = 20, fleet_counts: Mapping[str,int] | None = None,
                      origins: Sequence[str] | None = None, config: Mapping[str,Any] | None = None,
                      include_events: bool = False) -> dict[str,Any]:
    spec = make_market_spec(origins=origins, config=config)
    inputs, source = build_mixed_inputs(spec.physical, seed=seed, years=years)
    result = run_market(spec, inputs, fleet_counts=fleet_counts, include_events=include_events)
    result['source'] = source
    result['result_hash'] = sha256_json({k:v for k,v in result.items() if k!='result_hash'})
    return result
