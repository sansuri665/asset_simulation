"""Pure dynamic trials and final-quote bridge to the v3 physical executor.

Source releases are physical oil. Trial orders are desired loading amounts,
not new oil. Import requirements are immutable and independent of orders.
"""
from __future__ import annotations
from dataclasses import asdict, replace
import json
from typing import Mapping, Sequence
from ..bounded_route_pricing import align_pressure_with_gap, bounded_pressure
from ..global_shipping_contract import integer
from ..multi_origin_pricing import finite_signed
from ..registry import sha256_json
from ..shipping_v3.engine import initial_market, prepare_turn, settle_turn, validate_state, _snapshot_id
from ..shipping_v3.types import BallastOrder, BatchOrder, CargoBatch, Decision, LoadOrder
from .inventory import inventory_projection, stock_report
from .pricing import quote_plan
from .types import (BoardSnapshot, BoardSpec, BoardState, ImportRequirement, RouteTrial,
                    TrialPlan, TrialResult, canonical)


def validate_board_state(state: BoardState, spec: BoardSpec) -> None:
    if state.spec_hash != spec.identity or state.market.phase != 'ready':
        raise ValueError('wrong or unsettled board state')
    validate_state(state.market, spec.market)
    for entries in (state.initial_cargo_by_origin_bbl, state.cumulative_releases_bbl):
        if tuple(o for o, _ in entries) != spec.origins:
            raise ValueError('complete ordered source accounting required')
        for _, amount in entries:
            integer(amount, 'source barrels')
    integer(state.initial_destination_bbl, 'initial destination inventory')
    ids = set()
    for requirement in state.requirements:
        requirement.validate()
        if requirement.due_turn > state.market.turn + spec.config()['maximum_known_requirement_horizon_turns']:
            raise ValueError('stored requirement exceeds supported known horizon')
        if requirement.requirement_id in ids:
            raise ValueError('duplicate import requirement')
        ids.add(requirement.requirement_id)
    for o in spec.origins:
        cargo = sum(b.total_bbl for b in state.market.batches if b.origin == o)
        if cargo != dict(state.initial_cargo_by_origin_bbl)[o] + dict(state.cumulative_releases_bbl)[o]:
            raise ValueError('source oil created, lost or released twice')


def initial_board(spec: BoardSpec, fleet_counts: Mapping[str, int] | None = None, *,
                  initialization: str = 'phased', source_stock_bbl: Mapping[str, int] | None = None,
                  destination_stock_bbl: int | None = None) -> BoardState:
    market = initial_market(spec.market, fleet_counts, initialization=initialization)
    stocks = {o: b.normal_bbl for o, b in spec.source_bands} if source_stock_bbl is None else dict(source_stock_bbl)
    if set(stocks) != set(spec.origins):
        raise ValueError('initial stock must identify both sources')
    requirements = tuple(ImportRequirement('initial:' + b.batch_id, b.due_turn, b.total_bbl)
                         for b in market.batches if b.transit_bbl)
    batches = list(market.batches)
    for o, band in spec.source_bands:
        integer(stocks[o], 'initial source stock')
        if stock_report(stocks[o], band)['hard_bound_violation']:
            raise ValueError('initial source stock outside declared envelope')
        if stocks[o]:
            # Explicit initial physical buffer, not a new import obligation.
            batches.append(CargoBatch(f'bootstrap:shore:{o}', o, spec.market.destination, -1,
                                     dict(spec.market.due_lags)[o], stocks[o], stocks[o], bootstrap=True))
    market = replace(market, batches=tuple(batches))
    dest = spec.destination_band.normal_bbl if destination_stock_bbl is None else destination_stock_bbl
    integer(dest, 'initial destination stock')
    if stock_report(dest, spec.destination_band)['hard_bound_violation']:
        raise ValueError('initial destination stock outside declared envelope')
    result = BoardState(spec.identity, market, requirements, dest,
                        tuple((o, sum(b.total_bbl for b in batches if b.origin == o)) for o in spec.origins),
                        tuple((o, 0) for o in spec.origins))
    validate_board_state(result, spec)
    return result


def _board_snapshot_id(spec, base, releases, requirements, limits, cpi, opened) -> str:
    return sha256_json({'spec': spec.identity, 'base': base.identity, 'releases': releases,
                        'requirements': [asdict(r) for r in requirements], 'export_limits': limits,
                        'cpi': cpi, 'opened_state': asdict(opened)})


def open_board(state: BoardState, spec: BoardSpec, *, source_release_bbl: Mapping[str, int],
               new_requirements: Sequence[ImportRequirement] = (),
               export_limits_bbl: Mapping[str, int] | None = None, cpi: float = 100.0) -> BoardSnapshot:
    """Freeze arrivals, pending supply and known import tasks; do not advance state."""
    validate_board_state(state, spec)
    if set(source_release_bbl) != set(spec.origins):
        raise ValueError('supply must identify both origins')
    for amount in source_release_bbl.values():
        integer(amount, 'physical source release')
    cpi = finite_signed(cpi, 'CPI')
    if cpi <= 0:
        raise ValueError('CPI must be positive')
    requirements = list(state.requirements)
    ids = {r.requirement_id for r in requirements}
    for r in new_requirements:
        if not isinstance(r, ImportRequirement):
            raise ValueError('expected explicit ImportRequirement')
        r.validate()
        if r.requirement_id in ids or r.requirement_id.startswith('initial:') or r.due_turn < state.market.turn:
            raise ValueError('cannot duplicate, rewrite or backdate import needs')
        if r.due_turn - state.market.turn > spec.config()['maximum_known_requirement_horizon_turns']:
            raise ValueError('requirement exceeds the explicit bounded preview horizon')
        ids.add(r.requirement_id)
        requirements.append(r)
    # Reuse arrival processing; discard the inherited preliminary indication.
    legacy = prepare_turn(state.market, spec.market, new_batches=(), cpi=cpi)
    opened = legacy.opened_state
    delivered = sum(b.delivered_bbl for b in opened.batches)
    consumed = sum(r.volume_bbl for r in requirements if r.due_turn <= opened.turn)
    stock = state.initial_destination_bbl + delivered - consumed
    just_due = sum(r.volume_bbl for r in requirements if r.due_turn == opened.turn)
    actual_due = json.loads(legacy.events_json)['actual_arrived_bbl']
    daily = sum(l.reference_daily_bbl for l in spec.market.physical.lanes)
    pressure = bounded_pressure(state.market.destination_pressure, (just_due-actual_due)/daily,
                                config=spec.market.config(), decay=False)
    pressure = align_pressure_with_gap(pressure, spec.destination_band.normal_bbl-stock)
    opened = replace(opened, destination_pressure=pressure)
    limits = {o: sum(b.remaining_bbl for b in opened.batches if b.origin == o)+source_release_bbl[o]
              for o in spec.origins} if export_limits_bbl is None else dict(export_limits_bbl)
    if set(limits) != set(spec.origins):
        raise ValueError('one current export limit per origin required')
    for value in limits.values():
        integer(value, 'export limit')
    releases = tuple((o, source_release_bbl[o]) for o in spec.origins)
    limits = tuple((o, limits[o]) for o in spec.origins)
    reqs = tuple(sorted(requirements, key=lambda r: (r.due_turn, r.requirement_id)))
    sid = _board_snapshot_id(spec, state, releases, reqs, limits, cpi, opened)
    return BoardSnapshot(sid, spec, state, opened, releases, reqs, limits, cpi)


def _check_snapshot(snapshot: BoardSnapshot) -> None:
    if snapshot.snapshot_id != _board_snapshot_id(snapshot.spec, snapshot.base, snapshot.releases_bbl,
            snapshot.requirements, snapshot.export_limits_bbl, snapshot.cpi, snapshot.opened_market):
        raise ValueError('altered snapshot input')
    if snapshot.opened_market.turn != snapshot.base.market.turn or snapshot.opened_market.spec_hash != snapshot.spec.market.identity:
        raise ValueError('opened state does not match snapshot')


def quote_trial(snapshot: BoardSnapshot, trial_ship_plan: Mapping[str, Sequence[int]],
                trial_cargo_plan: Mapping[str, int], trial_inventory_plan: Mapping[int, int] | None = None,
                *, ballast_orders: Sequence[BallastOrder] = ()) -> TrialResult:
    """Pure trial. Optional inventory plan asserts derived stocks, never invents them.

    Illegal physical inputs raise. Otherwise a constraint-breaking inventory
    plan returns valid=False with the exact violation times and untouched stocks.
    """
    _check_snapshot(snapshot)
    spec = snapshot.spec
    if set(trial_ship_plan) != set(spec.origins) or set(trial_cargo_plan) != set(spec.origins):
        raise ValueError('provide both routes, including zero orders and empty ship lists')
    plan = TrialPlan(tuple(RouteTrial(o, trial_cargo_plan[o], tuple(trial_ship_plan[o])) for o in spec.origins),
                     tuple(ballast_orders))
    state = snapshot.opened_market
    ships, used, allocations = {s.ship_id: s for s in state.ships}, set(), {}
    for route in plan.routes:
        o = route.origin
        integer(route.cargo_bbl, 'trial cargo')
        ready = sum(b.remaining_bbl for b in state.batches if b.origin == o) + dict(snapshot.releases_bbl)[o]
        if route.cargo_bbl > ready or route.cargo_bbl > dict(snapshot.export_limits_bbl)[o]:
            raise ValueError('order exceeds this origin\'s physical oil or export limit')
        todo, rows = route.cargo_bbl, []
        for sid in route.ordered_ship_ids:
            integer(sid, 'trial ship ID')
            ship = ships.get(sid)
            if sid in used or ship is None or ship.movement is not None or ship.location != o:
                raise ValueError('trial ship must be unique, open and physically at that origin')
            service = spec.market.physical.lane(o).service(ship.class_id)
            used.add(sid)
            load = min(todo, ship.capacity_bbl)
            todo -= load
            rows.append({'ship_id': sid, 'origin': o, 'class_id': ship.class_id, 'capacity_bbl': ship.capacity_bbl,
                         'assigned_cargo_bbl': load, 'load_factor': load/ship.capacity_bbl,
                         'status': 'unused' if load == 0 else 'full' if load == ship.capacity_bbl else 'marginal',
                         'ready_turn': state.turn + service.outbound.ready_turn})
        marginal = next((r for r in rows if r['status'] == 'marginal'), None)
        allocations[o] = {'ships': rows, 'full_ship_count': sum(r['status'] == 'full' for r in rows),
                          'marginal_ship_id': marginal['ship_id'] if marginal else None,
                          'marginal_load_bbl': marginal['assigned_cargo_bbl'] if marginal else 0,
                          'marginal_load_factor': marginal['load_factor'] if marginal else None,
                          'unused_ship_count': sum(r['status'] == 'unused' for r in rows),
                          'unserved_trial_cargo_bbl': todo, 'loaded_bbl': route.cargo_bbl-todo,
                          'physically_ready_cargo_bbl': ready}
    templates = {(p.origin, p.destination, p.vessel_class): p for p in spec.market.physical.ballast_legs}
    for order in plan.ballasts:
        if not isinstance(order, BallastOrder):
            raise ValueError('expected BallastOrder')
        integer(order.ship_id, 'ballast ID')
        ship = ships.get(order.ship_id)
        if ship is None or ship.movement or order.ship_id in used:
            raise ValueError('duplicate, unknown or moving ship in proposed ballast plan')
        if (ship.location, order.target_origin, ship.class_id) not in templates:
            raise ValueError('unknown, same-node or incompatible ballast path')
        spec.market.physical.lane(order.target_origin).service(ship.class_id)
        used.add(order.ship_id)
    assigned = [r for a in allocations.values() for r in a['ships'] if r['assigned_cargo_bbl']]
    inventory = inventory_projection(snapshot, assigned)
    if inventory['physical_mass_balance_residual_bbl']:
        raise ValueError('source, transit and destination conservation failed')
    if trial_inventory_plan is not None:
        derived = {r['turn']: r['stock_bbl'] for r in inventory['destination']['future_path']}
        for turn, stock in trial_inventory_plan.items():
            integer(turn, 'inventory plan turn')
            if type(stock) is not int or derived.get(turn) != stock:
                raise ValueError('inventory plan must equal time-lagged arrivals minus requirements')
    prices = quote_plan(snapshot, plan, allocations, inventory)
    report = {**prices, 'snapshot_id': snapshot.snapshot_id, 'plan_hash': sha256_json(asdict(plan)),
              'valid': inventory['valid'], 'inventory': inventory,
              'trial_kind': 'counterfactual_only_no_ship_or_batch_created',
              'actual_required_imports_counted_once_bbl': sum(r.volume_bbl for r in snapshot.requirements),
              'new_source_release_counted_once_bbl': sum(v for _, v in snapshot.releases_bbl)}
    return TrialResult(snapshot.snapshot_id, plan, canonical(report))


def _commit_trial(snapshot: BoardSnapshot, trial: TrialResult) -> tuple[BoardState, dict]:
    """Internal deterministic adapter; BoardSession owns exactly-once persistence."""
    if trial.snapshot_id != snapshot.snapshot_id:
        raise ValueError('trial belongs to another opening')
    fresh = quote_trial(snapshot, {r.origin: r.ordered_ship_ids for r in trial.plan.routes},
                        {r.origin: r.cargo_bbl for r in trial.plan.routes}, ballast_orders=trial.plan.ballasts)
    if fresh.identity != trial.identity:
        raise ValueError('altered trial report or plan')
    report = fresh.report()
    if not report['valid']:
        raise ValueError('infeasible inventory plan: ' + ', '.join(report['inventory']['hard_bound_violations']))
    spec, tick = snapshot.spec, snapshot.base.market.turn
    releases = dict(snapshot.releases_bbl)
    new = tuple(BatchOrder(f'board-release:{o}:{tick}', o, amount, tick+dict(spec.market.due_lags)[o])
                for o, amount in snapshot.releases_bbl if amount)
    prepared = prepare_turn(snapshot.base.market, spec.market, new_batches=new, cpi=snapshot.cpi)
    # Override only the final quote. v3 retains ownership of geometry, FIFO,
    # real manifests, actual load amounts and integer-barrel conservation.
    quotes = {'routes': report['routes'], 'weighted_local_residual': 0.0,
              'availability': prepared.quotes()['availability']}
    qj = canonical(quotes)
    pressure = snapshot.opened_market.destination_pressure
    sid = _snapshot_id(prepared.opened_state, prepared.scheduled_json, qj, prepared.events_json, pressure, snapshot.cpi)
    prepared = replace(prepared, snapshot_id=sid, quote_json=qj, destination_pressure_open=pressure)
    loads = tuple(LoadOrder(r['ship_id'], r['assigned_cargo_bbl']) for o in spec.origins
                  for r in report['routes'][o]['allocation']['ships'] if r['assigned_cargo_bbl'])
    market, physical = settle_turn(prepared, spec.market,
        Decision(sid, loads, trial.plan.ballasts, 'external_dynamic_board_final_plan'), include_events=True)
    cumulative = dict(snapshot.base.cumulative_releases_bbl)
    for o in spec.origins:
        cumulative[o] += releases[o]
    result = replace(snapshot.base, market=market, requirements=snapshot.requirements,
                     cumulative_releases_bbl=tuple((o, cumulative[o]) for o in spec.origins))
    validate_board_state(result, spec)
    for departure in physical['departures']:
        expected = report['routes'][departure['origin']]['net_service_value_real_usd_per_bbl'] * departure['cargo_bbl']
        if abs(expected-departure['booked_net_service_reference_value_real']) > 1e-7:
            raise ValueError('final quote changed during settlement')
    return result, {'execution_id': sha256_json({'snapshot': snapshot.snapshot_id, 'plan': asdict(trial.plan)}),
                    'snapshot_id': snapshot.snapshot_id, 'turn': tick, 'final_trial': report,
                    'physical_execution': physical, 'closing_state_hash': result.identity, 'not_invoice_or_cash': True}
