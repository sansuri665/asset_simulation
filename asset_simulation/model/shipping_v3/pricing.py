"""Transparent scheduled-supply quote; no dispatch, costs or equilibrium loop.

Future capacity affects only an indication. It never authorizes a current
load. A normal arrival schedule is normalized, not added as free excess
supply. Current work is held constant ONLY as a normalization convention,
not a forecast or an additional future cargo plan.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from ..global_shipping_contract import integer
from ..multi_origin_pricing import finite_signed
from .types import MarketSpec, OriginSignal


def urgency_signal(days: float, cfg: Mapping[str, Any]) -> tuple[float, float]:
    p, h = cfg['pricing'], cfg['pressure']
    if abs(finite_signed(days, 'urgency days')) > h['limit_days'] + 1e-10:
        raise ValueError('urgency pressure outside configured bounds')
    priced = h['limit_days'] * math.tanh(days / h['soft_scale_days']) / math.tanh(h['limit_days'] / h['soft_scale_days'])
    correction = p['urgency_recovery_fraction'] * priced / 10
    signal = p['supply_demand_log_sensitivity'] * math.log1p(correction)
    signal += p['inventory_urgency_log_sensitivity_per_day'] * priced
    return signal, priced


def quote_routes(spec: MarketSpec, *, scheduled_by_origin_bbl: Mapping[str, int],
                 availability: Mapping[str, Any], signals: Mapping[str, OriginSignal],
                 destination_pressure: float, cpi: float = 100.0) -> dict[str, Any]:
    if any(set(m) != set(spec.origins) for m in (scheduled_by_origin_bbl, availability['routes'], signals)):
        raise ValueError('each origin must be present exactly once')
    cfg = spec.config(); p = cfg['pricing']; a = cfg['availability']
    weights = a['arrival_weights']
    normalizer = a['reference_prompt_multiplier'] + sum(weights[1:])
    cpi = finite_signed(cpi, 'CPI')
    if cpi <= 0 or abs(finite_signed(destination_pressure, 'destination pressure')) > cfg['pressure']['limit_days']:
        raise ValueError('invalid CPI or destination pressure')
    raw = {}
    counted = set()
    for lane in spec.physical.lanes:
        o = lane.origin; q = scheduled_by_origin_bbl[o]; integer(q, 'scheduled barrels')
        buckets = availability['routes'][o]
        if len(buckets) != len(weights):
            raise ValueError('availability horizon differs from pricing convention')
        capacity = []
        for index, bucket in enumerate(buckets):
            integer(bucket['capacity_bbl'], 'availability capacity')
            if bucket['horizon_turns'] != index:
                raise ValueError('availability buckets must be exact ordered arrivals')
            # Pure callers may supply measured capacity buckets without ship
            # details. Physical-engine snapshots always carry full ship IDs.
            if 'ships' in bucket:
                if sum(s['capacity_bbl'] for s in bucket['ships']) != bucket['capacity_bbl']:
                    raise ValueError('capacity not backed by offered ships')
                for item in bucket['ships']:
                    if item['ship_id'] in counted:
                        raise ValueError('same hull used by more than one horizon/route')
                    counted.add(item['ship_id'])
            capacity.append(bucket['capacity_bbl'])
        weighted = sum(w * s for w, s in zip(weights, capacity))
        # At steady ordinary coverage A0 = 1.06*Q and A1=A2=Q,
        # normalized available work equals Q. Without this term, simply
        # lengthening the horizon would artificially depress every quote.
        normalized = weighted / normalizer
        smoothing = lane.reference_daily_bbl * 10 * p['liquidity_fraction']
        ratio = (q + smoothing) / (normalized + smoothing)
        supply = p['supply_demand_log_sensitivity'] * math.log(ratio)
        sig = signals[o]
        if sig.origin != o:
            raise ValueError('origin signal identity mismatch')
        if abs(finite_signed(sig.pressure_days, 'origin pressure')) > cfg['pressure']['limit_days']:
            raise ValueError('origin pressure outside bounds')
        for name in ('market_log_signal', 'urgency_log_signal'):
            finite_signed(getattr(sig, name), name)
        urgency, priced = urgency_signal((sig.pressure_days + destination_pressure) / 2, cfg)
        raw[o] = {'q': q, 'reference': lane.reference_daily_bbl * 10,
                  'capacities': capacity, 'weighted': weighted, 'normalized': normalized,
                  'smoothing': smoothing, 'ratio': ratio, 'supply': supply,
                  'urgency': urgency, 'priced': priced, 'previous': sig}
    active = [o for o in spec.origins if raw[o]['q'] > 0]
    total = sum(raw[o]['reference'] for o in active)
    route_weights = {o: raw[o]['reference'] / total for o in active} if total else {}
    common = sum(route_weights[o] * raw[o]['supply'] for o in active)
    limit = p['local_signal_limit']
    r0 = {o: raw[o]['reference'] / (raw[o]['reference'] + p['local_evidence_capacity_bbl'])
          * limit * math.tanh((raw[o]['supply'] - common) / limit) for o in active}
    center = sum(route_weights[o] * r0[o] for o in active)
    residual = {o: r0[o] - center for o in active}
    # Recentring alone can double the original bound. Uniform rescaling keeps
    # BOTH the explicit local bound and the weighted zero-sum invariant.
    shrink = max(1.0, max((abs(v) / limit for v in residual.values()), default=0.0))
    residual = {o: value / shrink for o, value in residual.items()}
    rows = {}
    for lane in spec.physical.lanes:
        o = lane.origin; s = raw[o]; previous = s['previous']
        market_raw = p['shared_signal_weight'] * (common + residual.get(o, 0.0)) + (1-p['shared_signal_weight']) * s['supply']
        rho = p['price_persistence']
        # A quiet new-cargo window is not a new price observation. Existing
        # batches remain executable at the carried indicative value.
        market = rho * previous.market_log_signal + (1-rho) * market_raw if s['q'] else previous.market_log_signal
        urgency = rho * previous.urgency_log_signal + (1-rho) * s['urgency'] if s['q'] else previous.urgency_log_signal
        umin, _ = urgency_signal(-cfg['pressure']['limit_days'], cfg)
        umax, _ = urgency_signal(cfg['pressure']['limit_days'], cfg)
        if not umin - 1e-9 <= urgency <= umax + 1e-9:
            raise ValueError('previous urgency contains hidden unbounded price memory')
        log_price = math.log(p['baseline_real_tce_2025_usd_per_day']) + market + urgency
        low, high = p['numeric_minimum_real_tce'], p['numeric_maximum_real_tce']
        guarded = min(math.log(high), max(math.log(low), log_price))
        price = math.exp(guarded)
        numeric_hit = log_price < math.log(low) or log_price > math.log(high)
        # Last-resort anti-windup; applies only when the explicit debug guard
        # fires. Do not hide many turns of stored excess behind the guard.
        stored_market = guarded - math.log(p['baseline_real_tce_2025_usd_per_day']) - urgency
        net_per_bbl = price * lane.reference_cycle_turns * 10 / p['reference_capacity_bbl']
        classes = {}
        for service in lane.services:
            value = net_per_bbl * service.capacity_bbl
            tce = value / (service.cycle_turns * 10)
            classes[service.class_id] = {
                'capacity_bbl': service.capacity_bbl,
                'reference_cycle_days': service.cycle_turns * 10,
                'full_load_indicative_tce_real': tce,
                'full_load_indicative_tce_nominal': finite_signed(tce * cpi / 100, 'nominal TCE'),
                'full_load_net_service_value_real': value,
                'current_executable': any(x.get('class_id') == service.class_id for x in availability['routes'][o][0].get('ships', [])),
                'scope': 'full_load_capacity_time_conversion_not_profit_or_independent_class_market',
            }
        trace = {
            'scheduled_new_cargo_bbl': s['q'],
            'exact_arrival_capacity_bbl': s['capacities'], 'arrival_weights': weights,
            'weighted_scheduled_capacity_bbl': s['weighted'], 'normal_schedule_divisor': normalizer,
            'normalized_capacity_bbl_per_current_window': s['normalized'],
            'reference_window_bbl': s['reference'], 'liquidity_capacity_bbl': s['smoothing'],
            'capacity_ratio': s['ratio'], 'supply_sensitivity': p['supply_demand_log_sensitivity'],
            'local_supply_log_signal': s['supply'], 'common_market_log_signal': common,
            'bounded_centered_local_residual': residual.get(o, 0.0),
            'shared_signal_weight': p['shared_signal_weight'],
            'market_raw_log_signal': market_raw,
            'origin_pressure_days': previous.pressure_days, 'destination_pressure_days': destination_pressure,
            'priced_pressure_days': s['priced'], 'raw_urgency_log_signal': s['urgency'],
            'urgency_log_bounds': [umin, umax], 'price_persistence': rho,
            'previous_market_log_signal': previous.market_log_signal,
            'previous_urgency_log_signal': previous.urgency_log_signal,
            'settled_market_log_signal_before_guard': market, 'settled_urgency_log_signal': urgency,
            'stored_market_log_signal': stored_market,
            'baseline_real_tce': p['baseline_real_tce_2025_usd_per_day'],
            'unguarded_log_real_tce': log_price, 'numeric_guard_range': [low, high],
            'numeric_guard_hit': numeric_hit, 'cpi': cpi,
            'normalization_scope': 'constant_current_work_reference; NOT a future_demand_forecast',
        }
        rows[o] = {'pair_id': lane.pair_id, 'turn': availability['current_turn'],
                   'current_prompt_capacity_bbl': s['capacities'][0],
                   'route_benchmark_real_tce': round(price, 4),
                   'route_benchmark_nominal_tce': round(finite_signed(price*cpi/100, 'nominal price'), 4),
                   'net_service_value_real_usd_per_bbl': net_per_bbl,
                   'class_quotes': classes, 'explanation': trace,
                   'market_status': 'no_new_demand' if not s['q'] else 'no_current_supply' if not s['capacities'][0] else 'indicative',
                   'price_observation_available': bool(s['q']), 'is_transaction_price': False,
                   'can_execute_future_capacity_now': False}
    return {'routes': rows, 'common_supply_signal': common,
            'weighted_local_residual': sum(route_weights[o]*residual[o] for o in active),
            'scheduled_cargo_counted_once_bbl': sum(scheduled_by_origin_bbl.values()),
            'pricing_model': 'scheduled_capacity_with_separate_bounded_urgency_v3'}


def recompute_price(explanation: Mapping[str, Any]) -> float:
    """Independent arithmetic reconstruction of a quote from its trace."""
    e = explanation
    x = math.log(e['baseline_real_tce']) + e['settled_market_log_signal_before_guard'] + e['settled_urgency_log_signal']
    low, high = e['numeric_guard_range']
    return round(math.exp(max(math.log(low), min(math.log(high), x))), 4)
