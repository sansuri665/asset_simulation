"""Pure class-agnostic cargo pricing, separate from dispatch and costs.

One set of barrels faces the SUM of compatible prompt capacities. There is no
class demand curve or class quota. TCE conversions use a stated reference
cycle, not a claim that net service value is a gross freight invoice.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from .bounded_route_pricing import soft_price
from .global_shipping_contract import CLASSES, integer
from .mixed_cargo_contract import MixedSpec
from .multi_origin_pricing import finite_signed


def quote_mixed_routes(spec: MixedSpec, *, scheduled_by_origin_bbl: Mapping[str, int],
                       prompt_by_origin_class: Mapping[str, Mapping[str, int]],
                       origin_pressures: Mapping[str, float], destination_pressure: float,
                       previous_signals: Mapping[str, float], cpi: float = 100.0) -> dict[str, Any]:
    """Current offered supply -> common reference benchmark + route indications.

    Shared signal is a volume-weighted mean of LOCAL supply signals, not an
    additional global scarcity multiplier. Centered local residuals are bounded
    and attenuated for thin observations. Disable sharing for isolated controls.
    """
    for mapping in (scheduled_by_origin_bbl, prompt_by_origin_class, origin_pressures, previous_signals):
        if set(mapping) != set(spec.origins):
            raise ValueError('each origin must be supplied exactly once')
    cpi = finite_signed(cpi, 'CPI')
    if cpi <= 0:
        raise ValueError('CPI must be positive')
    cfg = spec.config(); p = cfg['pricing']; h = cfg['pressure']
    limit, scale = h['limit_days'], h['soft_scale_days']
    dest = finite_signed(destination_pressure, 'destination pressure')
    if abs(dest) > limit:
        raise ValueError('destination signal outside bounds')
    snapshots = {}
    for lane in spec.lanes:
        o = lane.origin; q = scheduled_by_origin_bbl[o]
        integer(q, 'scheduled barrels')
        counts = prompt_by_origin_class[o]
        if set(counts) != set(CLASSES):
            raise ValueError('provide all three prompt counts, including zeros')
        for c, count in counts.items():
            integer(count, c)
        services = {s.class_id: s for s in lane.services}
        capacity = sum(counts[c] * s.capacity_bbl for c, s in services.items())
        reference = lane.reference_daily_bbl * 10
        smoothing = reference * p['liquidity_fraction']
        relative = p['reference_prompt_multiplier'] * (q + smoothing) / (capacity + smoothing)
        local_supply = p['supply_demand_log_sensitivity'] * math.log(relative)
        origin = finite_signed(origin_pressures[o], 'origin pressure')
        prev = finite_signed(previous_signals[o], 'previous signal')
        if abs(origin) > limit:
            raise ValueError('origin signal outside bounds')
        combined = (origin + dest) / 2
        priced = limit * math.tanh(combined/scale) / math.tanh(limit/scale)
        # Bounded urgency only; it is never added to the physical cargo queue.
        correction = 0.25 * priced / 10
        urgency = p['supply_demand_log_sensitivity'] * math.log((q*(1+correction)+smoothing)/(q+smoothing))
        urgency += p['inventory_urgency_log_sensitivity_per_day'] * priced
        evidence = reference / (reference + p['local_evidence_capacity_bbl'])
        snapshots[o] = {'capacity': capacity, 'reference': reference, 'relative': relative,
                        'supply': local_supply, 'urgency': urgency, 'previous': prev,
                        'priced_pressure': priced, 'evidence': evidence, 'services': services}
    active = [o for o in spec.origins if scheduled_by_origin_bbl[o] > 0]
    total_weight = sum(snapshots[o]['reference'] for o in active)
    weights = {o: snapshots[o]['reference']/total_weight for o in active} if active else {}
    common = sum(weights[o] * snapshots[o]['supply'] for o in active)
    local_limit = p['local_signal_limit']
    residuals = {o: snapshots[o]['evidence'] * local_limit * math.tanh((snapshots[o]['supply']-common)/local_limit) for o in active}
    mean_residual = sum(weights[o]*residuals[o] for o in active)
    rows = {}
    for lane in spec.lanes:
        o = lane.origin; s = snapshots[o]; q = scheduled_by_origin_bbl[o]
        residual = residuals.get(o, 0.) - mean_residual if o in active else 0.
        shared = common + residual
        weight = p['shared_signal_weight']
        market_signal = weight*shared + (1-weight)*s['supply']
        raw = market_signal + s['urgency']
        settled = p['price_persistence']*s['previous'] + (1-p['price_persistence'])*raw if q else s['previous']
        benchmark = soft_price(settled, cfg)
        # Reference same-origin cycle is an explicitly published comparison
        # convention. Future actual routing/cost accounts must not reuse it as
        # earned cash or double-charge an independently executed ballast leg.
        net_per_bbl = benchmark * lane.reference_cycle_turns * 10 / p['reference_capacity_bbl']
        classes = {}
        for c, service in s['services'].items():
            days = service.cycle_turns * 10
            net_value = net_per_bbl * service.capacity_bbl
            tce = net_value / days
            classes[c] = {
                'capacity_bbl': service.capacity_bbl,
                'prompt_ships': prompt_by_origin_class[o][c],
                'executable_now': prompt_by_origin_class[o][c] > 0,
                'reference_cycle_days': days,
                'full_load_net_service_value_real_usd': round(net_value, 4),
                'indicative_tce_real_2025_usd_per_day': round(tce, 4),
                'indicative_tce_nominal_usd_per_day': round(tce*cpi/100, 4),
                'independent_class_scarcity_premium': False,
            }
        rows[o] = {
            'pair_id': lane.pair_id, 'scheduled_cargo_bbl': q,
            'compatible_prompt_capacity_bbl': s['capacity'],
            'prompt_by_class': dict(prompt_by_origin_class[o]),
            'relative_capacity_tightness': s['relative'],
            'raw_local_supply_signal': s['supply'], 'common_supply_signal': common,
            'centered_local_residual': residual, 'local_evidence_weight': s['evidence'],
            'priced_pressure_days': s['priced_pressure'], 'urgency_signal': s['urgency'],
            'settled_signal': settled,
            'route_benchmark_real_tce': round(benchmark, 4),
            'route_benchmark_nominal_tce': round(benchmark*cpi/100, 4),
            'net_service_value_real_usd_per_bbl': net_per_bbl,
            'net_service_value_nominal_usd_per_bbl': net_per_bbl*cpi/100,
            'class_quotes': classes,
            'market_status': 'no_new_demand' if not q else 'no_supply' if s['capacity']==0 else 'indicative',
            'price_observation_available': bool(q), 'is_transaction_price': False,
            'price_scope': 'net_transport_service_benchmark_not_gross_freight_or_cash_income',
        }
    return {'routes': rows, 'common_supply_signal': common,
            'weighted_local_residual': sum(weights[o]*rows[o]['centered_local_residual'] for o in active),
            'aggregate_compatible_prompt_capacity_bbl': sum(s['capacity'] for s in snapshots.values()),
            'scheduled_cargo_counted_once_bbl': sum(scheduled_by_origin_bbl.values())}
