"""Optional time-only opportunity diagnostics; never affect route decisions."""
from __future__ import annotations

from ..global_shipping_contract import integer
from .types import MarketSnapshot, MarketSpec


def time_only_opportunities(snapshot: MarketSnapshot, spec: MarketSpec, ship_id: int,
                            *, cargo_bbl: int | None = None) -> list[dict]:
    integer(ship_id,'ship ID');state=snapshot.opened_state
    if not 1<=ship_id<=len(state.ships):
        raise ValueError('unknown ship')
    ship=state.ships[ship_id-1]
    if ship.movement is not None:
        raise ValueError('only an open hull has selectable next opportunities')
    amount=ship.capacity_bbl if cargo_bbl is None else cargo_bbl
    integer(amount,'hypothetical cargo')
    if not 0<amount<=ship.capacity_bbl:
        raise ValueError('hypothetical load outside vessel capacity')
    quotes=snapshot.quotes()['routes'];plans={(p.origin,p.destination,p.vessel_class):p for p in spec.physical.ballast_legs}
    alternatives=[]
    for lane in spec.physical.lanes:
        if not any(s.class_id==ship.class_id for s in lane.services):
            continue
        service=lane.service(ship.class_id)
        relocation=0 if ship.location==lane.origin else plans[ship.location,lane.origin,ship.class_id].ready_turn
        # End EVERY alternative at the same discharged destination. Do not
        # charge a second nominal roundtrip or duplicate the ballast leg.
        elapsed_days=(relocation+service.outbound.ready_turn)*10
        net=quotes[lane.origin]['net_service_value_real_usd_per_bbl']*amount
        alternatives.append({'ship_id':ship_id,'origin':lane.origin,'start_node':ship.location,
                             'common_terminal_node':spec.destination,'cargo_bbl':amount,
                             'relocation_days':relocation*10,'outbound_and_discharge_days':service.outbound.ready_turn*10,
                             'total_time_to_common_terminal_days':elapsed_days,
                             'net_service_reference_value_real':net,
                             'time_adjusted_service_value_real_usd_per_day':net/elapsed_days,
                             'additional_return_leg_charged':False,
                             'scope':'time_only_indication_not_profit; no_fuel_port_waiting_or_future_rate_prediction'})
    return sorted(alternatives,key=lambda x:(-x['time_adjusted_service_value_real_usd_per_day'],x['origin']))
