"""Stage6B v0.2: one route cargo ledger and a conserved heterogeneous fleet.

No class quotas, reference shares, costs or demand destruction. A supplied
allocation may replace the full-load test packer and the capacity-only router.
Neither policy is a claim about owners' economic choices.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
import math
from typing import Any, Mapping, Sequence

from .bounded_route_pricing import align_pressure_with_gap, bounded_pressure
from .global_shipping_contract import CLASSES, MovementPlan, integer
from .global_shipping_projection import whole_barrels
from .mixed_cargo_contract import VERSION, MixedSpec, at_turn, make_mixed_spec
from .mixed_cargo_pricing import quote_mixed_routes
from .multi_origin_pricing import finite_signed
from .registry import sha256_json


@dataclass(frozen=True)
class MixedShip:
    ship_id: int
    class_id: str
    home_origin: str
    last_origin: str
    location: str | None
    movement: MovementPlan | None = None


@dataclass(frozen=True)
class CargoLedger:
    origin: str
    reference_due: tuple[tuple[int, int], ...]
    initial_in_transit_bbl: int = 0
    scheduled_bbl: int = 0
    loaded_bbl: int = 0
    delivered_bbl: int = 0
    reference_delivered_bbl: int = 0
    pressure: float = 0.0
    previous_price_signal: float = 0.0
    forecast_bbl_per_turn: float = 0.0

    @property
    def owed_bbl(self) -> int:
        return self.scheduled_bbl - self.loaded_bbl

    @property
    def transit_bbl(self) -> int:
        return self.initial_in_transit_bbl + self.loaded_bbl - self.delivered_bbl

    @property
    def destination_bbl(self) -> int:
        return self.delivered_bbl - self.reference_delivered_bbl


@dataclass(frozen=True)
class MixedState:
    spec_hash: str
    initial_registry: tuple[tuple[int, str, str], ...]
    next_turn: int
    elapsed_turns: int
    ships: tuple[MixedShip, ...]
    ledgers: tuple[CargoLedger, ...]
    destination_pressure: float = 0.0


def _offset(spec: MixedSpec) -> int:
    return max(s.cycle_turns for l in spec.lanes for s in l.services)


def validate_mixed_state(state: MixedState, spec: MixedSpec) -> None:
    if state.spec_hash != spec.identity:
        raise ValueError('state/spec mismatch')
    integer(state.elapsed_turns, 'elapsed turns'); integer(state.next_turn, 'turn')
    if state.next_turn != _offset(spec) + state.elapsed_turns:
        raise ValueError('operating clock changed')
    if tuple((s.ship_id, s.class_id, s.home_origin) for s in state.ships) != state.initial_registry:
        raise ValueError('permanent ship IDs, classes or homes changed')
    if tuple(s.ship_id for s in state.ships) != tuple(range(1, len(state.ships)+1)):
        raise ValueError('ship IDs duplicated, reordered or lost')
    if tuple(l.origin for l in state.ledgers) != spec.origins:
        raise ValueError('one complete ordered cargo ledger per route required')
    templates = {('laden', l.origin, spec.destination, s.class_id): s.outbound for l in spec.lanes for s in l.services}
    templates.update({('ballast', p.origin, p.destination, p.vessel_class): p for p in spec.ballast_legs})
    actual = Counter()
    for ship in state.ships:
        if ship.class_id not in CLASSES or ship.home_origin not in spec.origins or ship.last_origin not in spec.origins:
            raise ValueError('invalid permanent ship data')
        if ship.movement is None:
            if ship.location not in (*spec.origins, spec.destination):
                raise ValueError('open ship must have a real node')
            continue
        p = ship.movement
        if ship.location is not None or p.vessel_class != ship.class_id or p.catalog_hash != spec.physical_hash:
            raise ValueError('moving ship cannot be locally prompt or change type')
        if p.depart_turn >= state.next_turn or p.ready_turn < state.next_turn:
            raise ValueError('invalid or unprocessed voyage time')
        template = templates.get((p.kind, p.origin, p.destination, ship.class_id))
        if template is None or p != at_turn(template, p.depart_turn):
            raise ValueError('frozen movement or fixed class capacity changed')
        if p.kind == 'laden':
            actual[p.origin] += p.cargo_bbl
    limit = spec.config()['pressure']['limit_days']
    if abs(finite_signed(state.destination_pressure, 'destination pressure')) > limit:
        raise ValueError('destination pressure outside bounds')
    for ledger in state.ledgers:
        for k in ('initial_in_transit_bbl','scheduled_bbl','loaded_bbl','delivered_bbl','reference_delivered_bbl'):
            integer(getattr(ledger,k),k)
        if ledger.owed_bbl < 0 or ledger.transit_bbl != actual[ledger.origin]:
            raise ValueError('actual cargo conservation failed')
        if tuple(sorted(ledger.reference_due)) != ledger.reference_due or len(dict(ledger.reference_due)) != len(ledger.reference_due):
            raise ValueError('duplicate or unordered reference entries')
        for tick, bbl in ledger.reference_due:
            integer(tick, 'reference time'); integer(bbl, 'reference cargo')
            if tick < state.next_turn:
                raise ValueError('unprocessed reference arrival')
        reference = sum(b for _, b in ledger.reference_due)
        if reference != ledger.initial_in_transit_bbl + ledger.scheduled_bbl - ledger.reference_delivered_bbl:
            raise ValueError('reference cargo conservation failed')
        if ledger.owed_bbl + ledger.transit_bbl - reference + ledger.destination_bbl:
            raise ValueError('plan deviation conservation failed')
        if abs(finite_signed(ledger.pressure,'origin pressure')) > limit or finite_signed(ledger.forecast_bbl_per_turn,'forecast') < 0:
            raise ValueError('invalid pressure or forecast')
        finite_signed(ledger.previous_price_signal, 'previous signal')


def _allocate(total: int, weights: Sequence[float]) -> list[int]:
    if not weights or sum(weights) <= 0:
        if total:
            raise ValueError('nonempty compatible fleet allocation required')
        return [0] * len(weights)
    raw = [total*w/sum(weights) for w in weights]
    counts = [math.floor(v) for v in raw]
    for i in sorted(range(len(weights)), key=lambda i: (-(raw[i]-counts[i]), i))[:total-sum(counts)]:
        counts[i] += 1
    return counts


def initial_mixed_market(spec: MixedSpec, fleet_counts: Mapping[str,int] | None = None, *,
                         initialization: str = 'phased') -> MixedState:
    counts = spec.config()['default_fleet_counts'] if fleet_counts is None else fleet_counts
    if not set(counts) <= set(CLASSES) or initialization not in ('phased','cold'):
        raise ValueError('invalid fleet classes or initialization')
    for c, n in counts.items():
        integer(n,c)
    tick = _offset(spec); ships = []; due = {o:Counter() for o in spec.origins}
    for c in CLASSES:
        n = counts.get(c,0)
        compatible = [l for l in spec.lanes if any(s.class_id==c for s in l.services)]
        if n and not compatible:
            raise ValueError('class has no compatible lane in this submarket')
        weights = [l.reference_daily_bbl*l.service(c).cycle_turns for l in compatible]
        for lane, count in zip(compatible, _allocate(n,weights)):
            service = lane.service(c)
            # Only total lane flow and this explicitly supplied fleet exist here;
            # historical class mix is never used, even during initialization.
            for j in range(count):
                if initialization == 'cold':
                    ships.append(MixedShip(len(ships)+1,c,lane.origin,lane.origin,lane.origin))
                    continue
                age = j % service.cycle_turns
                if age < service.outbound.ready_turn:
                    plan = at_turn(service.outbound, tick-1-age)
                    due[lane.origin][plan.ready_turn] += service.capacity_bbl
                else:
                    plan = at_turn(service.return_leg, tick-1-age+service.outbound.ready_turn)
                ships.append(MixedShip(len(ships)+1,c,lane.origin,lane.origin,None,plan))
    ledgers = tuple(CargoLedger(l.origin,tuple(sorted(due[l.origin].items())),sum(due[l.origin].values()),
                              forecast_bbl_per_turn=l.reference_daily_bbl*10) for l in spec.lanes)
    state = MixedState(spec.identity,tuple((s.ship_id,s.class_id,s.home_origin) for s in ships),tick,0,tuple(ships),ledgers)
    validate_mixed_state(state,spec)
    return state


@lru_cache(maxsize=4096)
def _pack_cached(capacities: tuple[int,...], available: tuple[int,...], target_bbl: int) -> tuple[int,...]:
    """Exact bounded subset sum via bitsets. Max loaded volume, NOT profit.

    All hulls take their fixed full class load. A residual remains in the same
    cargo ledger. Earlier (larger) classes break equal-volume ties; this is an
    explicit demo policy, not a reference share or economically optimal mix.
    """
    unit = math.gcd(*capacities)
    target = min(target_bbl, sum(c*n for c,n in zip(capacities,available))) // unit
    mask = (1 << (target+1))-1
    reachable = 1; history = []
    for i in sorted(range(len(capacities)),key=lambda j:(-capacities[j],j)):
        weight = capacities[i]//unit
        remaining = min(available[i], target//weight); batch = 1
        while remaining:
            count = min(batch,remaining); shift = count*weight
            history.append((reachable,i,count,shift))
            reachable = (reachable | (reachable << shift)) & mask
            remaining -= count; batch *= 2
    value = reachable.bit_length()-1; counts=[0]*len(capacities)
    for before,i,count,shift in reversed(history):
        if not ((before >> value)&1):
            counts[i] += count; value -= shift
    assert value == 0
    return tuple(counts)


def choose_full_load_mix(ready_bbl: int, capacities: Mapping[str,int], available: Mapping[str,int]) -> dict[str,int]:
    integer(ready_bbl,'ready barrels')
    if set(capacities) != set(available) or not capacities:
        raise ValueError('capacity and availability classes must match')
    keys=tuple(sorted(capacities))
    for c in keys:
        integer(capacities[c],c);integer(available[c],c)
        if capacities[c]==0:
            raise ValueError('positive full-load capacity required')
    return dict(zip(keys,_pack_cached(tuple(capacities[c] for c in keys),tuple(available[c] for c in keys),ready_bbl)))


def _assigned(ship: MixedShip, spec: MixedSpec) -> str | None:
    if ship.movement:
        return ship.movement.origin if ship.movement.kind=='laden' else ship.movement.destination
    return ship.location if ship.location in spec.origins else None


def _route_empty(ships: list[MixedShip], spec: MixedSpec, tick: int,
                 ledgers: Mapping[str,CargoLedger], scheduled: Mapping[str,int],
                 external_orders: Mapping[int,str] | None) -> tuple[list[MixedShip],dict[str,Any]]:
    cfg=spec.config();r=cfg['routing'];lanes={l.origin:l for l in spec.lanes}
    plans={(p.origin,p.destination,p.vessel_class):p for p in spec.ballast_legs}
    committed=Counter(); targets={}
    for ship in ships:
        o=_assigned(ship,spec)
        if o:
            service=lanes[o].service(ship.class_id)
            committed[o] += service.capacity_bbl/service.cycle_turns
    for o,l in lanes.items():
        recent=ledgers[o].forecast_bbl_per_turn
        recovery=min(0.25*ledgers[o].owed_bbl,0.2*scheduled[o])
        if not scheduled[o] and ledgers[o].owed_bbl:
            recovery=min(ledgers[o].owed_bbl,l.reference_daily_bbl*10)
        targets[o]=(recent+recovery)*(1+r['spare_loading_fraction']/l.reference_cycle_turns)
    moves=[]
    def send(i: int, o: str) -> None:
        ship=ships[i]; a=ship.location
        if a is None or o==a or o not in lanes or (a,o,ship.class_id) not in plans:
            raise ValueError('invalid, unavailable or zero-distance ballast instruction')
        target_service=lanes[o].service(ship.class_id)
        old=_assigned(ship,spec)
        if old:
            service=lanes[old].service(ship.class_id)
            committed[old] -= service.capacity_bbl/service.cycle_turns
        committed[o] += target_service.capacity_bbl/target_service.cycle_turns
        plan=at_turn(plans[a,o,ship.class_id],tick)
        ships[i]=replace(ship,location=None,movement=plan)
        moves.append({'ship_id':ship.ship_id,'class_id':ship.class_id,'from':a,'to':o,
                      'changed_origin':ship.last_origin!=o,'depart_turn':tick,'ready_turn':plan.ready_turn})
    def choose(ship:MixedShip,require_gap=False):
        choices=[]
        for o,l in lanes.items():
            if o==ship.location or not any(s.class_id==ship.class_id for s in l.services):
                continue
            service=l.service(ship.class_id)
            if not scheduled[o] and ledgers[o].owed_bbl < service.capacity_bbl:
                continue
            gap=targets[o]-committed[o]
            if require_gap and gap <= 0:
                continue
            choices.append((gap/max(targets[o],l.reference_daily_bbl),-plans[ship.location,o,ship.class_id].ready_turn,o))
        return max(choices)[2] if choices else None
    if external_orders is not None:
        for sid,o in sorted(external_orders.items()):
            integer(sid,'ship ID')
            if not 1<=sid<=len(ships):
                raise ValueError('unknown ship ID in ballast instructions')
            send(sid-1,o)
    else:
        for i,ship in enumerate(ships):
            if ship.location==spec.destination:
                o=ship.home_origin if r['mode']=='home_return' else choose(ship)
                if o is not None:
                    send(i,o)
        cross=0
        if r['mode']=='pooled':
            for i,ship in enumerate(ships):
                o=ship.location
                if o not in lanes or cross>=r['cross_origin_limit']:
                    continue
                service=lanes[o].service(ship.class_id)
                local=sum(lanes[o].service(s.class_id).capacity_bbl for s in ships if s.location==o)
                incoming=sum(lanes[o].service(s.class_id).capacity_bbl for s in ships
                             if s.movement and s.movement.kind=='ballast' and s.movement.destination==o and s.movement.ready_turn<=tick+1)
                if ledgers[o].owed_bbl>=service.capacity_bbl or committed[o]<=targets[o] or local+incoming-service.capacity_bbl<scheduled[o]:
                    continue
                target=choose(ship,True)
                if target:
                    send(i,target);cross+=1
    return ships,{'ballast_orders':moves,'committed_service_bbl_per_turn':dict(committed),
                  'target_service_bbl_per_turn':targets,
                  'routing_policy':'external' if external_orders is not None else r['mode']}


def step_mixed_market(state: MixedState, spec: MixedSpec, *, scheduled_by_origin_bbl: Mapping[str,int],
                      cpi: float=100., dispatch_by_origin_class: Mapping[str,Mapping[str,int]] | None=None,
                      ballast_orders: Mapping[int,str] | None=None,
                      include_events: bool=False) -> tuple[MixedState,dict[str,Any]]:
    """One causal 10-day step. Optional plans never alter the quoted supply.

    Dispatch selects counts of actual local ship IDs. A manual zero means idle,
    not withdrawal from offered market supply. Costs/agents belong elsewhere.
    """
    validate_mixed_state(state,spec)
    if set(scheduled_by_origin_bbl)!=set(spec.origins):
        raise ValueError('one shared cargo input per origin, including zero, required')
    for q in scheduled_by_origin_bbl.values():integer(q,'scheduled barrels')
    if dispatch_by_origin_class is not None and set(dispatch_by_origin_class)!=set(spec.origins):
        raise ValueError('manual dispatch must explicitly cover all origins')
    cfg=spec.config();tick=state.next_turn;ships=list(state.ships)
    delivered=Counter();deliveries=[];ballast_arrivals=[]
    for i,s in enumerate(ships):
        p=s.movement
        if p is not None and p.ready_turn==tick:
            event={'ship_id':s.ship_id,'class_id':s.class_id,'origin':p.origin,'destination':p.destination,
                   'cargo_bbl':p.cargo_bbl,'ready_turn':tick}
            if p.kind=='laden':
                delivered[p.origin]+=p.cargo_bbl;deliveries.append(event)
            else:ballast_arrivals.append(event)
            ships[i]=replace(s,location=p.destination,movement=None)
    due={l.origin:dict(l.reference_due).get(tick,0) for l in state.ledgers}
    destination=sum(l.destination_bbl for l in state.ledgers)+sum(delivered.values())-sum(due.values())
    total_daily=sum(l.reference_daily_bbl for l in spec.lanes)
    pressure=bounded_pressure(state.destination_pressure,(sum(due.values())-sum(delivered.values()))/total_daily,
                             config=cfg,decay=False)
    pressure=align_pressure_with_gap(pressure,-destination)
    prompt={o:{c:sum(s.location==o and s.class_id==c for s in ships) for c in CLASSES} for o in spec.origins}
    quotes=quote_mixed_routes(spec,scheduled_by_origin_bbl=scheduled_by_origin_bbl,prompt_by_origin_class=prompt,
                             origin_pressures={l.origin:l.pressure for l in state.ledgers},destination_pressure=pressure,
                             previous_signals={l.origin:l.previous_price_signal for l in state.ledgers},cpi=cpi)
    ledgers={};rows={};departures=[]
    for lane,old in zip(spec.lanes,state.ledgers):
        o=lane.origin;q=scheduled_by_origin_bbl[o];ready=old.owed_bbl+q
        services={s.class_id:s for s in lane.services}
        capacities={c:s.capacity_bbl for c,s in services.items()}
        available={c:prompt[o][c] for c in services}
        if dispatch_by_origin_class is None:
            chosen=choose_full_load_mix(ready,capacities,available)
        else:
            requested=dispatch_by_origin_class[o]
            if not set(requested)<=set(services):
                raise ValueError('manual mix contains incompatible or unknown class')
            chosen={c:requested.get(c,0) for c in services}
            for c,n in chosen.items():
                integer(n,c)
                if n>available[c]:raise ValueError('manual mix exceeds actual prompt ships')
            if sum(capacities[c]*n for c,n in chosen.items())>ready:
                raise ValueError('manual full loads exceed actual shared cargo')
        loaded_by_class={c:capacities.get(c,0)*chosen.get(c,0) for c in CLASSES}
        loaded=sum(loaded_by_class.values())
        for c,n in chosen.items():
            ids=[i for i,s in enumerate(ships) if s.location==o and s.class_id==c][:n]
            for i in ids:
                ship=ships[i];plan=at_turn(services[c].outbound,tick)
                ships[i]=replace(ship,location=None,movement=plan,last_origin=o)
                departures.append({'ship_id':ship.ship_id,'class_id':c,'origin':o,'cargo_bbl':plan.cargo_bbl,
                                   'depart_turn':tick,'ready_turn':plan.ready_turn})
        reference={t:b for t,b in old.reference_due if t>tick}
        # One reference delivery plan, independent of the CHOSEN ship mix.
        if q:
            rt=tick+lane.reference_delivery_turns;reference[rt]=reference.get(rt,0)+q
        op=bounded_pressure(old.pressure,(q-loaded)/lane.reference_daily_bbl,config=cfg,decay=True)
        op=align_pressure_with_gap(op,ready-loaded)
        rho=cfg['routing']['forecast_persistence']
        ledger=CargoLedger(o,tuple(sorted(reference.items())),old.initial_in_transit_bbl,
                           old.scheduled_bbl+q,old.loaded_bbl+loaded,old.delivered_bbl+delivered[o],
                           old.reference_delivered_bbl+due[o],op,quotes['routes'][o]['settled_signal'],
                           rho*old.forecast_bbl_per_turn+(1-rho)*q)
        ledgers[o]=ledger
        quote=quotes['routes'][o]
        rows[o]={**quote,'loaded_bbl':loaded,'delivered_bbl':delivered[o],'reference_delivered_bbl':due[o],
                 'loaded_by_class_bbl':loaded_by_class,'dispatched_by_class':{c:chosen.get(c,0) for c in CLASSES},
                 'origin_unshipped_bbl':ledger.owed_bbl,'actual_in_transit_bbl':ledger.transit_bbl,
                 'destination_contribution_bbl':ledger.destination_bbl,'reference_in_transit_bbl':sum(reference.values()),
                 'origin_pressure_close':op,'execution_policy':'external' if dispatch_by_origin_class is not None else 'max_volume_full_load',
                 'executed_class_benchmarks':{c:(quote['class_quotes'][c]['indicative_tce_real_2025_usd_per_day']
                                              if chosen.get(c,0)>0 else None) for c in services},
                 'unfilled_compatible_work_bbl':ready-loaded}
    ships,routing=_route_empty(ships,spec,tick,ledgers,scheduled_by_origin_bbl,ballast_orders)
    next_state=MixedState(state.spec_hash,state.initial_registry,tick+1,state.elapsed_turns+1,tuple(ships),
                         tuple(ledgers[o] for o in spec.origins),bounded_pressure(pressure,0,config=cfg,decay=True))
    validate_mixed_state(next_state,spec)
    events=departures+routing['ballast_orders']
    if len({e['ship_id'] for e in events})!=len(events):
        raise ValueError('ship dispatched more than once in one turn')
    record={'shipping_turn_index':state.elapsed_turns,'internal_movement_turn':tick,'turn_days':10,
            'routes':rows,'common_supply_signal':quotes['common_supply_signal'],
            'weighted_local_residual':quotes['weighted_local_residual'],
            'shared_destination_deviation_bbl':destination,'destination_pressure_open':pressure,
            'fleet_counts':{c:sum(s.class_id==c for s in ships) for c in CLASSES},
            'open_by_node_class':{o:{c:sum(s.location==o and s.class_id==c for s in ships) for c in CLASSES}
                                  for o in (*spec.origins,spec.destination)},
            'committed_service_bbl_per_turn':routing['committed_service_bbl_per_turn'],
            'switched_origin_orders':sum(m['changed_origin'] for m in routing['ballast_orders']),
            'fleet_conservation_residual':len(ships)-len(state.initial_registry),
            'barrel_conservation_residual':sum(l.owed_bbl+l.transit_bbl+l.delivered_bbl-l.initial_in_transit_bbl-l.scheduled_bbl for l in ledgers.values()),
            'plan_conservation_residual':sum(l.owed_bbl+l.transit_bbl-sum(b for _,b in l.reference_due)+l.destination_bbl for l in ledgers.values())}
    if include_events:record.update(departures=departures,deliveries=deliveries,ballast_arrivals=ballast_arrivals,
                                    ballast_orders=routing['ballast_orders'])
    return next_state,record


def build_mixed_inputs(spec: MixedSpec, *, seed: int, years: int) -> tuple[tuple[dict[str,Any],...],dict[str,Any]]:
    from .engine import run_global_macro
    from .oil_shipping_world import run_oil_shipping_world
    macro=run_global_macro(seed=seed,years=years);world=run_oil_shipping_world(macro)
    before=sha256_json({'macro':macro.rows,'world':world.turns})
    cpi={int(r['year']):r['cpi_price_level_index_2025_100'] for r in macro.rows}
    records=[];calendar_total=0;operating_total=0
    for m in world.turns:
        public={r['route_id']:r for r in m['routes']};scheduled={};source={}
        for lane in spec.lanes:
            if lane.display_route_id=='other_routes':
                raise ValueError('select explicit resolved lanes; no invented aggregate geography')
            rate=public[lane.display_route_id]['cargo_mbd']
            scheduled[lane.origin]=whole_barrels(rate,10);source[lane.origin]=rate
            calendar_total+=whole_barrels(rate,int(m['days']));operating_total+=3*scheduled[lane.origin]
        info=max(macro.start_year,m['year']-1)
        for i in (1,2,3):
            records.append({'year':m['year'],'month':m['month'],'turn_in_month':i,
                            'label':f"{m['year']}-{m['month']:02d}.{i}",'scheduled_by_origin_bbl':dict(scheduled),
                            'source_route_cargo_mbd':dict(source),'cpi':cpi[info],'cpi_information_year':info})
    if before!=sha256_json({'macro':macro.rows,'world':world.turns}):
        raise ValueError('upstream world was mutated')
    return tuple(records),{'seed':seed,'annual_transitions':years,'source_hash':before,'source_unchanged':True,
                           'selected_full_route_operating_bbl':operating_total,'selected_full_route_calendar_bbl':calendar_total,
                           'clock_projection_difference_bbl':operating_total-calendar_total,
                           'class_partition_applied':False,'outside_scope':'unselected routes, not other classes of selected cargo',
                           'input_hash':sha256_json(records)}


def _quantile(values:Sequence[float],p:float):
    if not values:return None
    v=sorted(values);x=(len(v)-1)*p;i=int(x)
    return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(x-i)


def summarize_mixed(records:Sequence[Mapping[str,Any]],spec:MixedSpec,*,warmup_turns:int=36)->dict[str,Any]:
    integer(warmup_turns,'warmup')
    if warmup_turns>=len(records):raise ValueError('post-warmup observations required')
    rows=records[warmup_turns:];routes={}
    for lane in spec.lanes:
        rr=[r['routes'][lane.origin] for r in rows];total=sum(r['loaded_bbl'] for r in rr)
        prices=[r['route_benchmark_real_tce'] for r in rr];daily=sum(r['scheduled_cargo_bbl'] for r in rr)/len(rr)/10
        mean=sum(prices)/len(prices)
        classes={}
        for c in CLASSES:
            cp=[r['class_quotes'][c]['indicative_tce_real_2025_usd_per_day'] for r in rr if c in r['class_quotes']]
            cv=[(r['class_quotes'][c]['indicative_tce_real_2025_usd_per_day'],r['loaded_by_class_bbl'][c])
                for r in rr if c in r['class_quotes'] and r['loaded_by_class_bbl'][c]]
            cb=sum(b for _,b in cv)
            classes[c]={'loaded_bbl':cb,'realized_cargo_share':cb/total if total else None,
                        'indicative_tce_median':_quantile(cp,.5),
                        'executed_volume_weighted_tce':sum(p*b for p,b in cv)/cb if cb else None}
        routes[lane.origin]={'mean_full_cargo_mbd':daily/1e6,'benchmark_median':_quantile(prices,.5),
                            'benchmark_p95':_quantile(prices,.95),'benchmark_min':min(prices),'benchmark_max':max(prices),
                            'benchmark_cv':math.sqrt(sum((p-mean)**2 for p in prices)/len(prices))/mean,
                            'benchmark_nominal_median':_quantile([r['route_benchmark_nominal_tce'] for r in rr],.5),
                            'no_supply_turns':sum(r['compatible_prompt_capacity_bbl']==0 for r in rr),
                            'executed_volume_weighted_benchmark':sum(r['route_benchmark_real_tce']*r['loaded_bbl'] for r in rr)/total if total else None,
                            'max_unshipped_days':max(r['origin_unshipped_bbl'] for r in rr)/daily if daily else None,
                            'closing_unshipped_bbl':rr[-1]['origin_unshipped_bbl'],
                            'loaded_bbl':total,'classes':classes}
    return {'observed_turns':len(rows),'operating_days':len(rows)*10,'fleet_counts':rows[0]['fleet_counts'],
            'routes':routes,'switched_origin_orders':sum(r['switched_origin_orders'] for r in rows),
            'conservation_exact':all(r['fleet_conservation_residual']==r['barrel_conservation_residual']==r['plan_conservation_residual']==0 for r in records)}


def run_mixed_market(spec:MixedSpec,inputs:Sequence[Mapping[str,Any]],*,fleet_counts:Mapping[str,int]|None=None,
                     warmup_turns:int=36,initialization:str='phased',include_events:bool=False)->dict[str,Any]:
    state=initial_mixed_market(spec,fleet_counts,initialization=initialization);initial=state;rows=[]
    for item in inputs:
        state,row=step_mixed_market(state,spec,scheduled_by_origin_bbl=item['scheduled_by_origin_bbl'],cpi=item.get('cpi',100),
                                   dispatch_by_origin_class=item.get('dispatch_by_origin_class'),
                                   ballast_orders=item.get('ballast_orders'),include_events=include_events)
        for k in ('year','month','turn_in_month','label','cpi_information_year'):
            if k in item:row[k]=item[k]
        rows.append(row)
    result={'model_version':VERSION,'scope':'selected_full_cargo_mixed_fleet_research_not_global_or_cost_equilibrium',
            'spec':asdict(spec),'spec_hash':spec.identity,'input_hash':sha256_json(inputs),
            'initial_state_hash':sha256_json(asdict(initial)),'final_state_hash':sha256_json(asdict(state)),
            'summary':summarize_mixed(rows,spec,warmup_turns=warmup_turns),'turns':rows}
    result['result_hash']=sha256_json(result)
    return result


def run_seeded_mixed_market(*,seed:int=42,years:int=20,fleet_counts:Mapping[str,int]|None=None,
                            origins:Sequence[str]|None=None,include_events:bool=False,
                            config:Mapping[str,Any]|None=None)->dict[str,Any]:
    spec=make_mixed_spec(origins=origins,config=config)
    inputs,source=build_mixed_inputs(spec,seed=seed,years=years)
    result=run_mixed_market(spec,inputs,fleet_counts=fleet_counts,warmup_turns=spec.config()['warmup_turns'],include_events=include_events)
    result['source']=source
    result['result_hash']=sha256_json({k:v for k,v in result.items() if k!='result_hash'})
    return result
