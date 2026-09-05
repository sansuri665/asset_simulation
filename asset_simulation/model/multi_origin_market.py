"""Stage6B: selected VLCC origins compete through one destination's empty fleet.

Integer barrels, unique ship IDs, frozen Stage6A MovementPlans. No cost model,
source substitution, demand destruction or new ships. The router is an explicit
causal service heuristic, NOT a solved market equilibrium or a shipping company.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .bounded_route_pricing import align_pressure_with_gap, bounded_pressure
from .global_shipping_contract import (
    MovementPlan, apportion_barrels, ballast_plan, integer, laden_plan,
    load_catalog, route_class_reference,
)
from .global_shipping_projection import whole_barrels
from .multi_origin_pricing import finite_signed, quote_origin_route
from .registry import sha256_json

MODEL_VERSION = 'asset-simulation-stage6b-multi-origin-v0.1.0'
CONFIG_PATH = Path(__file__).resolve().parents[1] / 'config/stage6b_multi_origin_v0.1.json'


def load_network_config() -> dict[str, Any]:
    cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    validate_config(cfg)
    return cfg


def validate_config(cfg: Mapping[str, Any]) -> None:
    if cfg['model_version'] != MODEL_VERSION or type(cfg['operating_turn_days']) is not int or cfg['operating_turn_days'] != 10:
        raise ValueError('invalid model or operating clock')
    if cfg['vessel_class'] != 'vlcc':
        raise ValueError('Stage6B executes only the selected VLCC submarket')
    for key in ('default_fleet_size', 'warmup_turns'):
        integer(cfg[key], key)
    r, p, e, h = (cfg[k] for k in ('routing', 'pricing', 'execution', 'pressure'))
    for group in (r, p, e, h):
        for key, value in group.items():
            if key != 'mode' and finite_signed(value, key) < 0:
                raise ValueError(f'{key} must be nonnegative')
    if r['mode'] not in ('pooled', 'home_return'):
        raise ValueError('routing mode must be pooled or home_return')
    integer(r['cross_origin_max_ships_per_turn'], 'cross-origin limit')
    if not 0 <= r['forecast_persistence'] < 1 or not 0 <= p['price_persistence'] < 1:
        raise ValueError('persistence must lie in [0,1)')
    for value in e.values():
        if not 0 <= value <= 1:
            raise ValueError('execution fractions must lie in [0,1]')
    for key in ('reference_prompt_multiplier', 'liquidity_fraction_of_reference_loading_window'):
        if p[key] <= 0:
            raise ValueError('positive quote reference required')
    if not 0 < p['minimum_real_tce_2025_usd_per_day'] < p['baseline_real_tce_2025_usd_per_day'] < p['maximum_real_tce_2025_usd_per_day']:
        raise ValueError('price bounds must bracket the baseline')
    if not 0 <= p['maximum_quote_recovery_fraction'] <= 1 or not 0 <= p['quote_recovery_fraction'] <= 1:
        raise ValueError('invalid quote recovery fraction')
    if any(value <= 0 for value in h.values()):
        raise ValueError('pressure scales and half-life must be positive')


@dataclass(frozen=True)
class LaneSpec:
    origin: str
    pair_id: str
    display_route_id: str
    parcel_bbl: int
    reference_daily_bbl: float
    share_bps: tuple[int, int, int]
    outbound: MovementPlan
    return_leg: MovementPlan

    @property
    def cycle_turns(self) -> int:
        return self.outbound.ready_turn + self.return_leg.ready_turn


@dataclass(frozen=True)
class NetworkSpec:
    destination: str
    lanes: tuple[LaneSpec, ...]
    ballast_legs: tuple[MovementPlan, ...]
    catalog_hash: str
    config_json: str

    @property
    def origins(self) -> tuple[str, ...]:
        return tuple(l.origin for l in self.lanes)

    @property
    def identity(self) -> str:
        return sha256_json(asdict(self))

    def config(self) -> dict[str, Any]:
        return json.loads(self.config_json)


def make_network_spec(*, origins: Sequence[str] | None = None,
                      destination: str | None = None,
                      catalog: Mapping[str, Any] | None = None,
                      config: Mapping[str, Any] | None = None) -> NetworkSpec:
    cat = load_catalog() if catalog is None else catalog
    if cat['catalog_hash'] != sha256_json({k: v for k, v in cat.items() if k != 'catalog_hash'}):
        raise ValueError('catalogue contents do not match their fingerprint')
    cfg = load_network_config() if config is None else dict(config)
    validate_config(cfg)
    origins = cfg['default_origins'] if origins is None else origins
    destination = cfg['destination'] if destination is None else destination
    if isinstance(origins, str) or not origins or any(not isinstance(o, str) for o in origins) or len(set(origins)) != len(origins):
        raise ValueError('origins must be a nonempty unique list')
    lanes = []
    for origin in sorted(origins):
        pid = f'{origin}::{destination}'
        ref = route_class_reference(cat, pid, 'vlcc')
        out = laden_plan(cat, pid, 'vlcc', 0)
        back = ballast_plan(cat, destination, origin, 'vlcc', 0)
        lane = cat['lanes'][pid]
        reference = lane['reference_cargo_mbd'] * lane['class_share_bps']['vlcc'] / 10000 * 1e6
        if reference <= 0:
            raise ValueError('selected lane must have positive reference VLCC work')
        lanes.append(LaneSpec(origin, pid, lane['display_route_id'], ref['cargo_bbl'], reference,
                              tuple(lane['class_share_bps'][c] for c in ('vlcc', 'suezmax', 'aframax')), out, back))
    legs = tuple(ballast_plan(cat, a, b, 'vlcc', 0)
                 for a in (destination, *sorted(origins)) for b in sorted(origins) if a != b)
    return NetworkSpec(destination, tuple(lanes), legs, cat['catalog_hash'],
                       json.dumps(cfg, sort_keys=True, separators=(',', ':'), allow_nan=False))


@dataclass(frozen=True)
class Ship:
    ship_id: int
    home_origin: str  # Only used by the explicitly segmented comparison policy.
    last_origin: str
    location: str | None
    movement: MovementPlan | None = None


@dataclass(frozen=True)
class LaneLedger:
    origin: str
    reference_due: tuple[tuple[int, int], ...]
    initial_in_transit_bbl: int = 0
    scheduled_bbl: int = 0
    loaded_bbl: int = 0
    delivered_bbl: int = 0
    reference_delivered_bbl: int = 0
    origin_pressure: float = 0.0
    previous_quote: float = 35000.0
    forecast_lots: float = 0.0

    @property
    def owed_bbl(self) -> int:
        return self.scheduled_bbl - self.loaded_bbl

    @property
    def destination_deviation_bbl(self) -> int:
        return self.delivered_bbl - self.reference_delivered_bbl

    @property
    def in_transit_bbl(self) -> int:
        return self.initial_in_transit_bbl + self.loaded_bbl - self.delivered_bbl


@dataclass(frozen=True)
class NetworkState:
    spec_hash: str
    fleet_size: int
    next_turn: int
    elapsed_turns: int
    ships: tuple[Ship, ...]
    ledgers: tuple[LaneLedger, ...]
    destination_pressure: float = 0.0


def validate_state(state: NetworkState, spec: NetworkSpec) -> None:
    if state.spec_hash != spec.identity:
        raise ValueError('state belongs to a different network specification')
    integer(state.next_turn, 'next turn'); integer(state.elapsed_turns, 'elapsed turns')
    integer(state.fleet_size, 'fixed fleet size')
    if len(state.ships) != state.fleet_size or state.next_turn != max(l.cycle_turns for l in spec.lanes) + state.elapsed_turns:
        raise ValueError('fixed fleet size or operating clock changed')
    if tuple(l.origin for l in state.ledgers) != spec.origins:
        raise ValueError('lane ledgers must be complete and ordered')
    if tuple(s.ship_id for s in state.ships) != tuple(range(1, len(state.ships) + 1)):
        raise ValueError('fixed ship IDs lost, duplicated or reordered')
    actual = Counter()
    parcel = {l.origin: l.parcel_bbl for l in spec.lanes}
    templates = {('laden', l.origin, spec.destination): l.outbound for l in spec.lanes}
    templates.update({('ballast', p.origin, p.destination): p for p in spec.ballast_legs})
    for ship in state.ships:
        integer(ship.ship_id, 'ship ID')
        if ship.home_origin not in spec.origins or ship.last_origin not in spec.origins:
            raise ValueError('unknown ship origin')
        plan = ship.movement
        if plan is None:
            if ship.location not in (*spec.origins, spec.destination):
                raise ValueError('open ship needs a known physical location')
            continue
        if ship.location is not None or plan.catalog_hash != spec.catalog_hash or plan.vessel_class != 'vlcc':
            raise ValueError('moving ship cannot simultaneously be prompt')
        if plan.kind not in ('laden', 'ballast') or plan.depart_turn >= state.next_turn or plan.ready_turn < state.next_turn:
            raise ValueError('invalid or unprocessed movement time')
        if plan.ready_turn != plan.depart_turn + plan.sea_turns + plan.discharge_turns or plan.sea_turns < 1:
            raise ValueError('invalid frozen duration')
        template = templates.get((plan.kind, plan.origin, plan.destination))
        if template is None or plan != _at(template, plan.depart_turn):
            raise ValueError('frozen movement differs from its declared path')
        if plan.kind == 'laden':
            if plan.origin not in spec.origins or plan.destination != spec.destination or plan.discharge_turns != 1 or plan.cargo_bbl != parcel[plan.origin]:
                raise ValueError('invalid loaded voyage or parcel')
            actual[plan.origin] += plan.cargo_bbl
        elif plan.cargo_bbl != 0 or plan.discharge_turns != 0 or plan.destination not in spec.origins or plan.origin not in (*spec.origins, spec.destination):
            raise ValueError('invalid ballast movement')
    limit = spec.config()['pressure']['limit_days']
    if abs(finite_signed(state.destination_pressure, 'destination pressure')) > limit + 1e-10:
        raise ValueError('shared pressure outside signal bounds')
    for ledger in state.ledgers:
        for name in ('initial_in_transit_bbl', 'scheduled_bbl', 'loaded_bbl', 'delivered_bbl', 'reference_delivered_bbl'):
            integer(getattr(ledger, name), name)
        if ledger.owed_bbl < 0 or ledger.in_transit_bbl != actual[ledger.origin]:
            raise ValueError('source or actual in-transit barrel balance failed')
        if tuple(sorted(ledger.reference_due)) != ledger.reference_due or len(dict(ledger.reference_due)) != len(ledger.reference_due):
            raise ValueError('duplicate or unordered reference delivery times')
        for tick, amount in ledger.reference_due:
            integer(tick, 'reference time'); integer(amount, 'reference cargo')
            if tick < state.next_turn:
                raise ValueError('overdue reference entry was not processed')
        if sum(amount for _, amount in ledger.reference_due) != ledger.initial_in_transit_bbl + ledger.scheduled_bbl - ledger.reference_delivered_bbl:
            raise ValueError('reference pipeline balance failed')
        residual = ledger.owed_bbl + ledger.in_transit_bbl - sum(a for _, a in ledger.reference_due) + ledger.destination_deviation_bbl
        if residual:
            raise ValueError('plan deviations do not conserve cargo')
        if abs(finite_signed(ledger.origin_pressure, 'origin pressure')) > limit + 1e-10 or finite_signed(ledger.forecast_lots, 'forecast') < 0:
            raise ValueError('invalid pressure or causal forecast')
        if finite_signed(ledger.previous_quote, 'previous quote') <= 0:
            raise ValueError('previous quote must be positive')


def _allocate(total: int, weights: Sequence[float]) -> list[int]:
    scale = sum(weights)
    exact = [total * w / scale for w in weights]
    counts = [math.floor(x) for x in exact]
    for i in sorted(range(len(weights)), key=lambda i: (-(exact[i] - counts[i]), i))[:total - sum(counts)]:
        counts[i] += 1
    return counts


def _at(template: MovementPlan, depart: int) -> MovementPlan:
    return replace(template, depart_turn=depart, ready_turn=depart + template.ready_turn)


def initial_network(spec: NetworkSpec, fleet_size: int | None = None, *, initialization: str = 'phased') -> NetworkState:
    cfg = spec.config()
    n = cfg['default_fleet_size'] if fleet_size is None else fleet_size
    integer(n, 'fleet size')
    if initialization not in ('phased', 'cold'):
        raise ValueError('initialization must be phased or cold')
    # Use only static reference work, never future Seed peaks, to place ships.
    flow = [l.reference_daily_bbl * 10 / l.parcel_bbl for l in spec.lanes]
    work = [f * l.cycle_turns for f, l in zip(flow, spec.lanes)]
    active = min(n, math.floor(sum(work) + 0.5)) if initialization == 'phased' else 0
    active_counts, idle_counts = _allocate(active, work), _allocate(n - active, flow)
    tick = max(l.cycle_turns for l in spec.lanes)  # nonnegative warm-start plan dates
    ships, ledgers = [], []
    for lane, inflight, idle, rate in zip(spec.lanes, active_counts, idle_counts, flow):
        due = Counter()
        phases = [inflight // lane.cycle_turns + (a < inflight % lane.cycle_turns) for a in range(lane.cycle_turns)]
        for age, count in enumerate(phases):
            loaded = age < lane.outbound.ready_turn
            plan = _at(lane.outbound, tick - 1 - age) if loaded else _at(lane.return_leg, tick - 1 - age + lane.outbound.ready_turn)
            for _ in range(count):
                ships.append(Ship(len(ships) + 1, lane.origin, lane.origin, None, plan))
                if loaded:
                    due[plan.ready_turn] += plan.cargo_bbl
        for _ in range(idle):
            ships.append(Ship(len(ships) + 1, lane.origin, lane.origin, lane.origin))
        ledgers.append(LaneLedger(lane.origin, tuple(sorted(due.items())), sum(due.values()),
                                  previous_quote=cfg['pricing']['baseline_real_tce_2025_usd_per_day'], forecast_lots=rate))
    state = NetworkState(spec.identity, n, tick, 0, tuple(ships), tuple(ledgers))
    validate_state(state, spec)
    return state


def _assigned(ship: Ship, origins: tuple[str, ...]) -> str | None:
    if ship.movement:
        return ship.movement.origin if ship.movement.kind == 'laden' else ship.movement.destination
    return ship.location if ship.location in origins else None


def _route_empty_ships(ships: list[Ship], spec: NetworkSpec, tick: int,
                       ledgers: Mapping[str, LaneLedger], scheduled: Mapping[str, int],
                       quotes: Mapping[str, Mapping[str, Any]]) -> tuple[list[Ship], dict[str, Any]]:
    """Allocate each empty ship once; already promised ballast is counted.

    Targets are causal reference-cycle coverage, not prompt supply. With any
    current work, destination spare ships go to origins even if all targets
    are met: excess tonnage must NOT vanish in an unpriced offshore reservoir.
    """
    cfg = spec.config(); r = cfg['routing']; e = cfg['execution']
    lanes = {l.origin: l for l in spec.lanes}
    plans = {(p.origin, p.destination): p for p in spec.ballast_legs}
    committed = Counter(_assigned(s, spec.origins) for s in ships)
    requested, target = {}, {}
    for o, lane in lanes.items():
        recovery = min(e['backlog_recovery_fraction'] * ledgers[o].owed_bbl,
                       e['maximum_extra_fraction_of_new_plan'] * scheduled[o]) / lane.parcel_bbl
        requested[o] = ledgers[o].forecast_lots + recovery
        if scheduled[o] == 0 and ledgers[o].owed_bbl >= lane.parcel_bbl:
            requested[o] = max(requested[o], min(ledgers[o].owed_bbl / lane.parcel_bbl, lane.reference_daily_bbl * 10 / lane.parcel_bbl))
        target[o] = requested[o] * lane.cycle_turns + r['target_spare_fraction_of_loading_window'] * max(requested[o], 1)
    before = {o: committed[o] for o in spec.origins}
    moves = []

    def choose(location: str, require_gap: bool = False) -> str | None:
        candidates = []
        for o in spec.origins:
            if o == location or (scheduled[o] == 0 and ledgers[o].owed_bbl < lanes[o].parcel_bbl):
                continue
            gap = target[o] - committed[o]
            if require_gap and gap <= 0.5:
                continue
            relative_gap = gap / max(target[o], 1)
            price = quotes[o]['real_tce_2025_usd_per_day']
            tilt = r['bounded_price_tiebreak_weight'] * math.tanh(math.log(price / cfg['pricing']['baseline_real_tce_2025_usd_per_day']))
            candidates.append((relative_gap + tilt, -plans[location, o].ready_turn, o))
        return max(candidates)[2] if candidates else None

    def send(i: int, target_origin: str) -> None:
        ship = ships[i]; origin = ship.location
        if origin is None or target_origin == origin:
            raise ValueError('only an open, distinct-node ship can reposition')
        plan = _at(plans[origin, target_origin], tick)
        source_assignment = _assigned(ship, spec.origins)
        if source_assignment is not None:
            committed[source_assignment] -= 1
        committed[target_origin] += 1
        ships[i] = replace(ship, location=None, movement=plan)
        moves.append({'ship_id': ship.ship_id, 'from': origin, 'to': target_origin,
                      'last_loading_origin': ship.last_origin,
                      'changed_loading_origin': ship.last_origin != target_origin,
                      'depart_turn': tick, 'ready_turn': plan.ready_turn,
                      'distance_nm': plan.distance_nm})

    for i, ship in enumerate(ships):
        if ship.location != spec.destination:
            continue
        selected = ship.home_origin if r['mode'] == 'home_return' else choose(spec.destination)
        if selected is not None:
            send(i, selected)
    cross = 0
    if r['mode'] == 'pooled':
        for i, ship in enumerate(ships):
            o = ship.location
            if o not in spec.origins or cross >= r['cross_origin_max_ships_per_turn']:
                continue
            # Do not move an empty ship away from cargo it could serve, nor
            # drain the next origin loading window simply to chase a high quote.
            local_open = sum(s.location == o for s in ships)
            arriving_next = sum(s.movement is not None and s.movement.kind == 'ballast' and s.movement.destination == o and s.movement.ready_turn <= tick + 1 for s in ships)
            if ledgers[o].owed_bbl >= lanes[o].parcel_bbl or committed[o] <= target[o] + 1 or local_open + arriving_next <= math.ceil(requested[o]) + 1:
                continue
            selected = choose(o, require_gap=True)
            if selected is not None:
                send(i, selected); cross += 1
    return ships, {'target_committed_vessels': target, 'committed_before_routing': before,
                   'committed_after_routing': {o: committed[o] for o in spec.origins},
                   'ballast_orders': moves, 'routing_mode': r['mode'],
                   'router_scope': 'causal_service_coverage_heuristic_not_profit_optimization'}


def step_network(state: NetworkState, spec: NetworkSpec, *, scheduled_by_origin_bbl: Mapping[str, int],
                 cpi: float = 100.0, include_events: bool = False) -> tuple[NetworkState, dict[str, Any]]:
    validate_state(state, spec)
    if set(scheduled_by_origin_bbl) != set(spec.origins):
        raise ValueError('provide each selected origin once, including zero flows')
    for value in scheduled_by_origin_bbl.values():
        integer(value, 'scheduled cargo')
    if finite_signed(cpi, 'CPI') <= 0:
        raise ValueError('CPI must be positive')
    cfg = spec.config(); tick = state.next_turn
    ships = list(state.ships); actual_due = Counter(); deliveries = []; ballast_arrivals = []
    for i, ship in enumerate(ships):
        p = ship.movement
        if p is None or p.ready_turn > tick:
            continue
        if p.kind == 'laden':
            actual_due[p.origin] += p.cargo_bbl
            deliveries.append({'ship_id': ship.ship_id, 'origin': p.origin, 'cargo_bbl': p.cargo_bbl, 'ready_turn': tick})
        else:
            ballast_arrivals.append({'ship_id': ship.ship_id, 'origin': p.origin, 'destination': p.destination, 'ready_turn': tick})
        ships[i] = replace(ship, location=p.destination, movement=None)
    reference_due = {l.origin: dict(l.reference_due).get(tick, 0) for l in state.ledgers}
    destination_open = sum(l.destination_deviation_bbl for l in state.ledgers) + sum(actual_due.values()) - sum(reference_due.values())
    total_daily = sum(l.reference_daily_bbl for l in spec.lanes)
    destination_pressure = bounded_pressure(state.destination_pressure,
        (sum(reference_due.values()) - sum(actual_due.values())) / total_daily, config=cfg, decay=False)
    destination_pressure = align_pressure_with_gap(destination_pressure, -destination_open)
    destination_open_ships = sum(s.location == spec.destination for s in ships)
    incoming_ballast = {o: sum(s.movement is not None and s.movement.kind == 'ballast' and s.movement.destination == o for s in ships) for o in spec.origins}
    ledgers, rows, quotes, departures = {}, {}, {}, []
    for lane, old in zip(spec.lanes, state.ledgers):
        o = lane.origin; scheduled = scheduled_by_origin_bbl[o]
        prompt_ids = [i for i, s in enumerate(ships) if s.location == o]
        quote = quote_origin_route(pair_id=lane.pair_id, scheduled_bbl=scheduled, parcel_bbl=lane.parcel_bbl,
            reference_daily_bbl=lane.reference_daily_bbl, prompt_ships=len(prompt_ids),
            origin_pressure_days=old.origin_pressure, destination_pressure_days=destination_pressure,
            previous_real_tce=old.previous_quote, cpi=cpi, config=cfg)
        quotes[o] = quote
        e = cfg['execution']
        recovery = min(e['backlog_recovery_fraction'] * old.owed_bbl, e['maximum_extra_fraction_of_new_plan'] * scheduled)
        intended = math.ceil((scheduled + recovery) / lane.parcel_bbl) if scheduled else (old.owed_bbl // lane.parcel_bbl)
        ready = (old.owed_bbl + scheduled) // lane.parcel_bbl
        eligible = min(intended, ready)
        loaded_count = min(eligible, len(prompt_ids)); loaded = loaded_count * lane.parcel_bbl
        for i in prompt_ids[:loaded_count]:
            ship = ships[i]; movement = _at(lane.outbound, tick)
            ships[i] = replace(ship, location=None, movement=movement, last_origin=o)
            departures.append({'ship_id': ship.ship_id, 'origin': o, 'cargo_bbl': lane.parcel_bbl,
                               'depart_turn': tick, 'ready_turn': movement.ready_turn})
        schedule = {t: b for t, b in old.reference_due if t > tick}
        reference_tick = tick + lane.outbound.ready_turn
        if scheduled:
            schedule[reference_tick] = schedule.get(reference_tick, 0) + scheduled
        pressure = bounded_pressure(old.origin_pressure, (scheduled - loaded) / lane.reference_daily_bbl, config=cfg, decay=True)
        owed = old.owed_bbl + scheduled - loaded
        pressure = align_pressure_with_gap(pressure, owed)
        forecast = cfg['routing']['forecast_persistence'] * old.forecast_lots + (1 - cfg['routing']['forecast_persistence']) * scheduled / lane.parcel_bbl
        new = LaneLedger(o, tuple(sorted(schedule.items())), old.initial_in_transit_bbl,
                         old.scheduled_bbl + scheduled, old.loaded_bbl + loaded,
                         old.delivered_bbl + actual_due[o], old.reference_delivered_bbl + reference_due[o],
                         pressure, quote['real_tce_2025_usd_per_day'], forecast)
        ledgers[o] = new
        rows[o] = {**quote, 'scheduled_bbl': scheduled, 'loaded_bbl': loaded, 'delivered_bbl': actual_due[o],
                   'reference_delivered_bbl': reference_due[o], 'parcel_bbl': lane.parcel_bbl,
                   'executed_lots': loaded_count, 'ready_lots': ready, 'eligible_lots': eligible,
                   'unfilled_lot_observations': eligible - loaded_count, 'execution_request_bbl': scheduled + recovery,
                   'executed_benchmark_real_tce': quote['real_tce_2025_usd_per_day'] if loaded_count else None,
                   'origin_unshipped_bbl': new.owed_bbl, 'actual_in_transit_bbl': new.in_transit_bbl,
                   'destination_contribution_bbl': new.destination_deviation_bbl,
                   'origin_pressure_close': pressure, 'forecast_lots': forecast}
    ships, routing = _route_empty_ships(ships, spec, tick, ledgers, scheduled_by_origin_bbl, quotes)
    destination_close_pressure = bounded_pressure(destination_pressure, 0, config=cfg, decay=True)
    result = NetworkState(state.spec_hash, state.fleet_size, tick + 1, state.elapsed_turns + 1, tuple(ships),
                          tuple(ledgers[o] for o in spec.origins), destination_close_pressure)
    validate_state(result, spec)
    statuses = Counter('OPEN' if s.movement is None else s.movement.state_at(tick) for s in ships)
    total_plan_residual = sum(l.owed_bbl + l.in_transit_bbl - sum(v for _, v in l.reference_due) + l.destination_deviation_bbl for l in result.ledgers)
    orders = routing['ballast_orders']
    record = {'shipping_turn_index': state.elapsed_turns, 'internal_movement_turn': tick,
              'operating_day_start': state.elapsed_turns * 10, 'turn_days': 10,
              'routes': rows, 'shared_destination_deviation_bbl': destination_open,
              'destination_pressure_open': destination_pressure, 'destination_pressure_close': destination_close_pressure,
              'fleet_size': len(ships), 'state_counts': dict(statuses),
              'destination_empty_ships_before_routing': destination_open_ships,
              'incoming_ballast_excluded_from_prompt': incoming_ballast,
              'open_by_node': {o: sum(s.location == o for s in ships) for o in (*spec.origins, spec.destination)},
              'ballast_orders_count': len(orders),
              'cross_origin_orders_count': sum(m['from'] in spec.origins for m in orders),
              'switched_origin_orders_count': sum(m['changed_loading_origin'] for m in orders),
              'fleet_conservation_residual': sum(statuses.values()) - len(state.ships),
              'cargo_conservation_residual_bbl': sum(l.owed_bbl + l.in_transit_bbl + l.delivered_bbl - l.initial_in_transit_bbl - l.scheduled_bbl for l in result.ledgers),
              'plan_conservation_residual_bbl': total_plan_residual,
              'target_committed_vessels': routing['target_committed_vessels'],
              'committed_after_routing': routing['committed_after_routing']}
    if include_events:
        record.update({'departures': departures, 'deliveries': deliveries,
                       'ballast_arrivals': ballast_arrivals, 'ballast_orders': orders})
    return result, record


def build_seeded_inputs(spec: NetworkSpec, *, seed: int, years: int) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """Use existing displayed named lanes, same Stage6A integer class split.

    Selected routes are explicit; aggregate/unresolved geography was rejected
    when building the spec. Catalogue distances stay fixed in this experiment;
    upstream effective haul is retained as diagnostics, not silently applied
    as a second distance multiplier.
    """
    from .engine import run_global_macro
    from .oil_shipping_world import run_oil_shipping_world
    macro = run_global_macro(seed=seed, years=years)
    world = run_oil_shipping_world(macro)
    before = sha256_json({'macro': macro.rows, 'shipping': world.turns})
    cpi = {int(r['year']): r['cpi_price_level_index_2025_100'] for r in macro.rows}
    records = []; total_operating = total_calendar = 0
    excluded_classes = 0
    for m in world.turns:
        public = {r['route_id']: r for r in m['routes']}
        scheduled, source, diagnostic_haul = {}, {}, {}
        for lane in spec.lanes:
            if lane.display_route_id == 'other_routes':
                raise ValueError('cannot recover an unresolved route from an aggregate quote')
            rate = public[lane.display_route_id]['cargo_mbd']
            split = apportion_barrels(whole_barrels(rate, 10), dict(zip(('vlcc','suezmax','aframax'), lane.share_bps)))
            scheduled[lane.origin] = split['vlcc']
            source[lane.origin] = rate
            diagnostic_haul[lane.origin] = public[lane.display_route_id]['effective_haul_nm']
            total_operating += split['vlcc'] * 3
            total_calendar += apportion_barrels(whole_barrels(rate, int(m['days'])), dict(zip(('vlcc','suezmax','aframax'), lane.share_bps)))['vlcc']
            excluded_classes += (split['suezmax'] + split['aframax']) * 3
        info = max(macro.start_year, int(m['year']) - 1)
        for part in (1, 2, 3):
            records.append({'label': f"{m['year']}-{m['month']:02d}.{part}", 'year': int(m['year']),
                            'month': int(m['month']), 'turn_in_month': part,
                            'scheduled_by_origin_bbl': dict(scheduled), 'source_route_cargo_mbd': dict(source),
                            'upstream_effective_haul_nm_diagnostic': dict(diagnostic_haul),
                            'cpi': cpi[info], 'cpi_information_year': info})
    after = sha256_json({'macro': macro.rows, 'shipping': world.turns})
    if after != before:
        raise ValueError('read-only projection mutated the upstream world')
    identity = {'seed': seed, 'annual_transitions': years, 'covered_calendar_labels': years + 1,
                'source_hash': before, 'selected_vlcc_operating_plan_bbl': total_operating,
                'selected_vlcc_source_calendar_bbl': total_calendar,
                'clock_projection_difference_bbl': total_operating - total_calendar,
                'excluded_non_vlcc_selected_route_bbl': excluded_classes,
                'unselected_routes_scope': 'outside_this_submarket_no_reserved_global_fleet_inferred',
                'source_unchanged': True, 'input_hash': sha256_json(records)}
    return tuple(records), identity


def _quantile(values: Sequence[float], p: float) -> float:
    values = sorted(values); pos = (len(values) - 1) * p; i = math.floor(pos)
    return values[i] + (pos - i) * (values[min(i + 1, len(values) - 1)] - values[i])


def summarize_network(records: Sequence[Mapping[str, Any]], spec: NetworkSpec, *, warmup_turns: int = 36) -> dict[str, Any]:
    integer(warmup_turns, 'warmup')
    if warmup_turns >= len(records):
        raise ValueError('summary requires post-warmup records')
    rows = records[warmup_turns:]; routes = {}
    for lane in spec.lanes:
        rr = [r['routes'][lane.origin] for r in rows]
        prices = [r['real_tce_2025_usd_per_day'] for r in rr]
        nominal = [r['nominal_tce_usd_per_day'] for r in rr]
        executed = [r['executed_benchmark_real_tce'] for r in rr if r['executed_lots'] > 0]
        daily = sum(r['scheduled_bbl'] for r in rr) / len(rr) / 10
        routes[lane.origin] = {'mean_selected_cargo_mbd': daily / 1e6,
            'tce_real_p05': _quantile(prices, .05), 'tce_real_median': _quantile(prices, .5),
            'tce_real_p95': _quantile(prices, .95), 'tce_real_min': min(prices), 'tce_real_max': max(prices),
            'tce_nominal_median': _quantile(nominal, .5), 'tce_nominal_max': max(nominal),
            'max_unshipped_plan_days': max(r['origin_unshipped_bbl'] for r in rr) / daily if daily else None,
            'closing_unshipped_bbl': rr[-1]['origin_unshipped_bbl'],
            'mean_prompt_ships': sum(r['prompt_supply_vlcc'] for r in rr) / len(rr),
            'no_supply_turns': sum(r['prompt_supply_vlcc'] == 0 for r in rr),
            'mean_dispatched_ships': sum(r['executed_lots'] for r in rr) / len(rr),
            'mean_assigned_ships': sum(r['committed_after_routing'][lane.origin] for r in rows) / len(rows),
            'near_upper_bound_turns': sum(r['near_upper_price_bound'] for r in rr),
            'executed_benchmark_median': _quantile(executed,.5) if executed else None,
            'executed_benchmark_p95': _quantile(executed,.95) if executed else None,
            'executed_benchmark_max': max(executed) if executed else None}
    return {'observed_turns': len(rows), 'operating_days': len(rows) * 10, 'fleet_size': rows[0]['fleet_size'],
            'routes': routes, 'switched_origin_orders': sum(r['switched_origin_orders_count'] for r in rows),
            'cross_origin_orders': sum(r['cross_origin_orders_count'] for r in rows),
            'mean_destination_idle_ships': sum(r['open_by_node'][spec.destination] for r in rows) / len(rows),
            'max_abs_shared_destination_deviation_bbl': max(abs(r['shared_destination_deviation_bbl']) for r in rows),
            'maximum_pressure_days': max(abs(r['destination_pressure_open']) for r in rows),
            'conservation_exact': all(r['fleet_conservation_residual'] == r['cargo_conservation_residual_bbl'] == r['plan_conservation_residual_bbl'] == 0 for r in records)}


def run_network(spec: NetworkSpec, inputs: Sequence[Mapping[str, Any]], *, fleet_size: int | None = None,
                warmup_turns: int = 36, initialization: str = 'phased', include_events: bool = False) -> dict[str, Any]:
    state = initial_network(spec, fleet_size, initialization=initialization); initial = state
    rows = []
    for item in inputs:
        state, row = step_network(state, spec, scheduled_by_origin_bbl=item['scheduled_by_origin_bbl'],
                                   cpi=item.get('cpi', 100), include_events=include_events)
        for key in ('label', 'year', 'month', 'turn_in_month', 'cpi_information_year'):
            if key in item:
                row[key] = item[key]
        rows.append(row)
    result = {'model_version': MODEL_VERSION, 'scope': 'selected_multi_origin_vlcc_research_market_not_global_equilibrium',
              'spec_hash': spec.identity, 'spec': asdict(spec), 'input_hash': sha256_json(inputs),
              'initial_state_hash': sha256_json(asdict(initial)), 'final_state_hash': sha256_json(asdict(state)),
              'summary': summarize_network(rows, spec, warmup_turns=warmup_turns), 'turns': rows}
    result['result_hash'] = sha256_json(result)
    return result


def run_seeded_multi_origin(*, seed: int = 42, years: int = 20, fleet_size: int | None = None,
                            origins: Sequence[str] | None = None, include_events: bool = False,
                            config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec = make_network_spec(origins=origins, config=config)
    inputs, source = build_seeded_inputs(spec, seed=seed, years=years)
    result = run_network(spec, inputs, fleet_size=fleet_size, warmup_turns=spec.config()['warmup_turns'], include_events=include_events)
    result['source'] = source
    result['result_hash'] = sha256_json({k: v for k, v in result.items() if k != 'result_hash'})
    return result
