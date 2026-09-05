"""One pricing formula, separate local supply and one shared destination signal.

This is the Stage5A bounded formula generalized to route-specific reference
work and parcels. It does NOT allocate ships, create oil or inject an extra
'global market' multiplier. Both prior pricing modules remain unchanged.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from .bounded_route_pricing import soft_price, inverse_soft_price
from .registry import sha256_json


def finite_signed(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number, not bool')
    return float(value)


def quote_origin_route(*, pair_id: str, scheduled_bbl: int, parcel_bbl: int,
                       reference_daily_bbl: float, prompt_ships: int,
                       origin_pressure_days: float, destination_pressure_days: float,
                       previous_real_tce: float, cpi: float,
                       config: Mapping[str, Any]) -> dict[str, Any]:
    """Price only locally open ships. Destination empty ships are not prompt.

    Origin pressure and destination pressure each receive half weight. The
    destination component is normalized by total selected-market daily flow
    BEFORE this function, never by each tiny origin's individual flow.
    """
    for name, value in (('scheduled_bbl', scheduled_bbl), ('parcel_bbl', parcel_bbl), ('prompt_ships', prompt_ships)):
        if type(value) is not int or value < 0:
            raise ValueError(f'{name} must be a nonnegative integer')
    if parcel_bbl == 0 or finite_signed(reference_daily_bbl, 'reference flow') <= 0:
        raise ValueError('positive reference flow and parcel required')
    cpi = finite_signed(cpi, 'CPI')
    previous_real_tce = finite_signed(previous_real_tce, 'previous TCE')
    if cpi <= 0 or previous_real_tce <= 0:
        raise ValueError('CPI and previous quote must be positive')
    p, pressure = config['pricing'], config['pressure']
    limit, scale = pressure['limit_days'], pressure['soft_scale_days']
    origin = finite_signed(origin_pressure_days, 'origin pressure')
    destination = finite_signed(destination_pressure_days, 'destination pressure')
    if max(abs(origin), abs(destination)) > limit + 1e-10:
        raise ValueError('pressure must be bounded before pricing')
    combined = 0.5 * (origin + destination)
    priced_days = limit * math.tanh(combined / scale) / math.tanh(limit / scale)
    # Extra urgent work is a QUOTE signal, not another copy of the cargo queue.
    correction = max(-p['maximum_quote_recovery_fraction'], min(
        p['maximum_quote_recovery_fraction'], p['quote_recovery_fraction'] * priced_days / 10.0))
    demand = scheduled_bbl * (1 + correction) / parcel_bbl
    ref_demand = reference_daily_bbl * 10 / parcel_bbl
    smoothing = ref_demand * p['liquidity_fraction_of_reference_loading_window']
    ref_prompt = ref_demand * p['reference_prompt_multiplier']
    relative = ((demand + smoothing) / (prompt_ships + smoothing)) / ((ref_demand + smoothing) / (ref_prompt + smoothing))
    supply_signal = p['supply_demand_log_sensitivity'] * math.log(relative)
    urgency_signal = p['inventory_urgency_log_sensitivity_per_day'] * priced_days
    settled = p['price_persistence'] * inverse_soft_price(previous_real_tce, config) + (1 - p['price_persistence']) * (supply_signal + urgency_signal)
    low, high = p['minimum_real_tce_2025_usd_per_day'], p['maximum_real_tce_2025_usd_per_day']
    real = soft_price(settled, config) if scheduled_bbl else max(low, min(high, previous_real_tce))
    return {
        'pair_id': pair_id, 'turn_days': 10,
        'market_status': 'no_new_demand' if not scheduled_bbl else 'no_supply' if not prompt_ships else 'indicative_quote',
        'price_observation_available': bool(scheduled_bbl), 'is_transaction_price': False,
        'structural_cargo_mbd': scheduled_bbl / 1e7,
        'prompt_supply_vlcc': prompt_ships, 'pricing_demand_vlcc_equivalent': demand,
        'reference_prompt_vlcc': ref_prompt, 'liquidity_smoothing_vlcc': smoothing,
        'relative_tightness': relative, 'origin_pressure_days': origin,
        'shared_destination_pressure_days': destination,
        'combined_pricing_pressure_days': combined, 'priced_pressure_days': priced_days,
        'supply_demand_log_signal': supply_signal, 'inventory_urgency_log_signal': urgency_signal,
        'settled_price_signal': settled,
        'real_tce_2025_usd_per_day': round(real, 2),
        'nominal_tce_usd_per_day': round(real * cpi / 100, 2), 'cpi': cpi,
        'near_upper_price_bound': real >= low + 0.95 * (high - low),
        'price_scope': 'route_indication_not_negotiated_cash_revenue',
        'pricing_config_hash': sha256_json({'pricing': p, 'pressure': pressure}),
    }
