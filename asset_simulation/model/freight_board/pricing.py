"""Pure trial prices; class-normalized capacity, no automatic route optimizer.

Each class has its own fixed route divisor. Normalizing contributions before
adding them prevents a new hull from raising prices by moving a denominator.
"""
from __future__ import annotations
import math
from ..shipping_v3.pricing import urgency_signal
from .types import BoardSnapshot


def quote_plan(snapshot: BoardSnapshot, plan, allocations: dict, inventory: dict) -> dict:
    spec, state = snapshot.spec, snapshot.opened_market
    cfg, legacy = spec.config(), spec.market.config()
    p, weights = legacy['pricing'], cfg['arrival_weights']
    horizon = len(weights) - 1
    signals = {s.origin: s for s in state.signals}
    selected = {sid for r in plan.routes for sid in r.ordered_ship_ids}
    new_ballasts = {b.ship_id: b.target_origin for b in plan.ballasts}
    templates = {(p.origin, p.destination, p.vessel_class): p for p in spec.market.physical.ballast_legs}
    dest_stock_signal = inventory['destination']['opening']['soft_pressure']
    routes, globally_counted = {}, set()
    for route in plan.routes:
        o = route.origin
        lane = spec.market.physical.lane(o)
        by_class = {s.class_id: {'trial': 0, 'reserve': 0, 'committed': [0]*len(weights),
                                'proposed': [0]*len(weights)} for s in lane.services}
        evidence, beyond = [], []
        for ship in state.ships:
            basis, h, target = None, 0, None
            if ship.movement:
                if ship.movement.kind == 'ballast':
                    target, h = ship.movement.destination, ship.movement.ready_turn - state.turn
                    basis = 'committed'
            elif ship.ship_id in new_ballasts:
                target = new_ballasts[ship.ship_id]
                h, basis = templates[ship.location, target, ship.class_id].ready_turn, 'proposed'
            elif ship.location == o and ship.class_id in by_class:
                target, basis = o, 'trial' if ship.ship_id in selected else 'reserve'
            if target != o or ship.class_id not in by_class:
                continue
            item = {'ship_id': ship.ship_id, 'class_id': ship.class_id, 'capacity_bbl': ship.capacity_bbl,
                    'horizon_turns': h, 'target_origin': o, 'basis': basis}
            if ship.ship_id in globally_counted:
                raise ValueError('a hull was priced more than once')
            globally_counted.add(ship.ship_id)
            if h > horizon:
                beyond.append(item)
                continue
            evidence.append(item)
            if basis in ('trial', 'reserve'):
                by_class[ship.class_id][basis] += ship.capacity_bbl
            else:
                by_class[ship.class_id][basis][h] += ship.capacity_bbl
        effective, norm_rows = 0.0, {}
        for service in lane.services:
            data = by_class[service.class_id]
            # Quotes include NEW proposed ballots; h=return_lag is thus in the
            # normal POST-trial schedule, unlike the legacy pre-decision quote.
            profile = [cfg['normal_prompt_multiplier']] + [float(h <= service.return_leg.ready_turn)
                                                            for h in range(1, len(weights))]
            divisor = sum(w*n for w, n in zip(weights, profile))
            weighted = data['trial'] + cfg['reserve_weight'] * data['reserve']
            weighted += sum(w*(a+b) for w, a, b in zip(weights, data['committed'], data['proposed']))
            normalized = weighted / divisor
            effective += normalized
            norm_rows[service.class_id] = {**data, 'normal_profile': profile, 'normalizer': divisor,
                                          'return_turns': service.return_leg.ready_turn,
                                          'cycle_turns': service.cycle_turns,
                                          'weighted_capacity_bbl': weighted, 'normalized_capacity_bbl': normalized}
        smooth = lane.reference_daily_bbl * 10 * p['liquidity_fraction']
        ratio = (route.cargo_bbl + smooth) / (effective + smooth)
        supply_signal = p['supply_demand_log_sensitivity'] * math.log(ratio)
        old = signals[o]
        source_stock_signal = inventory['sources'][o]['opening']['soft_pressure']
        stock_log = cfg['inventory_log_premium_limit'] * (source_stock_signal - dest_stock_signal) / 2
        raw_backlog, priced_days = urgency_signal((old.pressure_days + state.destination_pressure) / 2, legacy)
        rho = p['price_persistence']
        market = rho * old.market_log_signal + (1-rho) * (supply_signal + stock_log)
        urgency = rho * old.urgency_log_signal + (1-rho) * raw_backlog
        base, low, high = p['baseline_real_tce_2025_usd_per_day'], p['numeric_minimum_real_tce'], p['numeric_maximum_real_tce']
        unguarded = math.log(base) + market + urgency
        final_log = min(math.log(high), max(math.log(low), unguarded))
        value = math.exp(final_log)
        per_bbl = value * lane.reference_cycle_turns * 10 / p['reference_capacity_bbl']
        trace = {'trial_cargo_bbl': route.cargo_bbl,
                 'trial_capacity_bbl': sum(d['trial'] for d in by_class.values()),
                 'reserve_capacity_bbl': sum(d['reserve'] for d in by_class.values()),
                 'reserve_weight': cfg['reserve_weight'], 'arrival_weights': weights,
                 'per_class_capacity_and_normalization': norm_rows,
                 'normalized_capacity_bbl': effective, 'liquidity_capacity_bbl': smooth,
                 'capacity_ratio': ratio, 'supply_sensitivity': p['supply_demand_log_sensitivity'],
                 'raw_local_supply_log_signal': supply_signal, 'shared_market_log_signal': 0.0,
                 'source_stock_soft_signal': source_stock_signal, 'destination_stock_soft_signal': dest_stock_signal,
                 'inventory_log_premium_limit': cfg['inventory_log_premium_limit'],
                 'bounded_inventory_log_premium': stock_log, 'raw_backlog_log_signal': raw_backlog,
                 'priced_backlog_days': priced_days, 'previous_market_log_signal': old.market_log_signal,
                 'previous_urgency_log_signal': old.urgency_log_signal, 'price_persistence': rho,
                 'settled_market_log_signal_before_guard': market, 'settled_urgency_log_signal': urgency,
                 'stored_market_log_signal': final_log - math.log(base) - urgency,
                 'baseline_real_tce': base, 'unguarded_log_real_tce': unguarded,
                 'numeric_guard_range': [low, high], 'numeric_guard_hit': unguarded != final_log, 'cpi': snapshot.cpi}
        classes = {}
        for service in lane.services:
            tce = per_bbl * service.capacity_bbl / (service.cycle_turns * 10)
            classes[service.class_id] = {'capacity_bbl': service.capacity_bbl,
                'reference_cycle_days': service.cycle_turns * 10, 'full_load_indicative_tce_real': tce,
                'full_load_indicative_tce_nominal': tce * snapshot.cpi / 100,
                'scope': 'same_per_barrel_service_value_not_independent_class_price_or_profit'}
        ship_rows = []
        for assigned in allocations[o]['ships']:
            days = lane.service(assigned['class_id']).cycle_turns * 10
            ship_rows.append({**assigned, 'service_value_real_usd': per_bbl * assigned['assigned_cargo_bbl'],
                              'full_load_reference_tce': per_bbl * assigned['capacity_bbl'] / days,
                              'actual_load_reference_tce': per_bbl * assigned['assigned_cargo_bbl'] / days})
        routes[o] = {'pair_id': lane.pair_id, 'turn': state.turn,
                     'trial_cargo_bbl': route.cargo_bbl, 'trial_ship_count': len(route.ordered_ship_ids),
                     'current_prompt_capacity_bbl': trace['trial_capacity_bbl'],
                     'reserve_capacity_bbl': trace['reserve_capacity_bbl'], 'normalized_capacity_bbl': effective,
                     'route_benchmark_real_tce': round(value, 4),
                     'route_benchmark_nominal_tce': round(value * snapshot.cpi / 100, 4),
                     'net_service_value_real_usd_per_bbl': per_bbl,
                     'net_service_value_nominal_usd_per_bbl': per_bbl * snapshot.cpi / 100,
                     'class_quotes': classes, 'allocation': {**allocations[o], 'ships': ship_rows},
                     'availability_evidence': evidence, 'known_or_proposed_beyond_horizon': beyond,
                     'market_status': 'no_order' if not route.cargo_bbl else
                        'no_current_trial_capacity' if trace['trial_capacity_bbl'] == 0 else 'trial_indication',
                     'price_observation_available': route.cargo_bbl > 0, 'is_transaction_price': False,
                     'can_execute_future_capacity_now': False, 'explanation': trace}
    return {'routes': routes, 'weighted_local_residual': 0.0,
            'pricing_model': 'post_trial_class_normalized_capacity_no_auto_optimization',
            'scheduled_cargo_counted_once_bbl': sum(r.cargo_bbl for r in plan.routes)}


def recompute_trial_price(e: dict) -> float:
    """Rebuild from quantities, weights and memories, not just final log price."""
    effective = 0.0
    for row in e['per_class_capacity_and_normalization'].values():
        weighted = row['trial'] + e['reserve_weight'] * row['reserve']
        weighted += sum(w*(a+b) for w, a, b in zip(e['arrival_weights'], row['committed'], row['proposed']))
        effective += weighted / sum(w*n for w, n in zip(e['arrival_weights'], row['normal_profile']))
    ratio = (e['trial_cargo_bbl'] + e['liquidity_capacity_bbl']) / (effective + e['liquidity_capacity_bbl'])
    inventory = e['inventory_log_premium_limit'] * (e['source_stock_soft_signal'] - e['destination_stock_soft_signal']) / 2
    rho = e['price_persistence']
    market = rho*e['previous_market_log_signal'] + (1-rho)*(e['supply_sensitivity']*math.log(ratio) + inventory)
    urgency = rho*e['previous_urgency_log_signal'] + (1-rho)*e['raw_backlog_log_signal']
    x = math.log(e['baseline_real_tce']) + market + urgency
    low, high = e['numeric_guard_range']
    return round(math.exp(min(math.log(high), max(math.log(low), x))), 4)
