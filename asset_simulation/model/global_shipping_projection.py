"""Read-only Stage6A demand/ship-class projection for research, not execution.

The public demand world aggregates eleven cells as other_routes. Replaying the
same seeded route owner restores those cells, with explicit equality checks.
No flow is allocated again from an invented alternative trade matrix.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterator, Mapping

from .global_shipping_contract import CLASSES, apportion_barrels, finite, load_catalog, route_class_reference
from .oil_shipping_routes import initial_route_state, advance_route_network, _balance_matrix, _calibrated_pair_weights
from .registry import load_registered_assets, sha256_json


class ProjectionError(ValueError):
    """The inherited trade projection could not be reproduced exactly enough."""


def whole_barrels(cargo_mbd: float, days: int) -> int:
    finite(cargo_mbd, 'cargo rate')
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise ValueError('day count must be a positive integer')
    return int((Decimal(str(cargo_mbd)) * days * 1000000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def replay_pair_months(world: Any, cat: Mapping[str, Any], *,
                       upstream_config: Mapping[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """Restore all 25 OD cells using existing owner equations, current/past only.

    Ordinary upstream worlds only. Scenarios must be passed explicitly through
    a future owner API, not guessed from incomplete aggregated observations.
    """
    cfg = load_registered_assets()['oil_shipping_demand_config'] if upstream_config is None else upstream_config
    network = cfg['route_network']
    if sha256_json(network) != cat['upstream_network_hash']:
        raise ProjectionError('catalogue and source network differ')
    if world.identity.get('config_hash') != sha256_json(cfg):
        raise ProjectionError('world was generated using a different upstream configuration')
    if world.identity.get('scenario_hash') != sha256_json({}):
        raise ProjectionError('this research replay requires a normal world without hidden scenarios')
    route_state = initial_route_state(cfg)
    for index, month in enumerate(world.turns):
        source_days = finite(month['days'], 'source month days', positive=True)
        if not source_days.is_integer() or not 28 <= source_days <= 31:
            raise ProjectionError('invalid source calendar month length')
        if month['turn_index'] != index or month['seed'] != world.seed:
            raise ProjectionError('world must contain the complete ordered monthly prefix')
        route_state, replayed = advance_route_network(
            route_state, month, month, seed=world.seed, turn_index=index,
            days=month['days'], config=cfg,
        )
        actual = {r['route_id']: r for r in month['routes']}
        if len(actual) != len(month['routes']) or set(actual) != {r['route_id'] for r in replayed['routes']}:
            raise ProjectionError('source route list is incomplete or duplicated')
        max_error = 0.0
        for row in replayed['routes']:
            source = actual[row['route_id']]
            for field in ('cargo_mbd', 'baseline_haul_nm', 'effective_haul_nm'):
                error = abs(row[field] - source[field])
                max_error = max(max_error, error)
                # Published base_dislocation is rounded to 8 decimals before replay.
                tolerance = 1e-7 if field == 'cargo_mbd' else max(1e-6, abs(source[field]) * 1e-7)
                if error > tolerance:
                    raise ProjectionError(f'route replay mismatch: {row["route_id"]}.{field}: {error}')
        regions = {r['region_id']: r for r in month['regional_balances']}
        rows = {r: regions[r]['net_seaborne_balance_mbd'] for r in network['export_region_ids']}
        columns = {r: -regions[r]['net_seaborne_balance_mbd'] for r in network['import_region_ids']}
        flows = _balance_matrix(rows, columns, _calibrated_pair_weights(route_state['pair_preferences'], network=network))
        grouped = {}
        for pid, flow in flows.items():
            group = cat['lanes'][pid]['display_route_id']
            grouped[group] = grouped.get(group, 0.0) + flow
        grouped_error = max(abs(value - actual[key]['cargo_mbd']) for key, value in grouped.items())
        if grouped_error > 1e-7:
            raise ProjectionError('restored 25-pair cargo does not equal the original 14+pool display')
        yield {'year': month['year'], 'month': month['month'], 'source_calendar_days': int(source_days),
               'pairs_mbd': flows, 'upstream_routes': actual,
               'maximum_route_replay_error': max_error, 'maximum_group_cargo_error_mbd': grouped_error,
               'upstream_month_cargo_mbd': month['seaborne_cargo_mbd']}


def project_pair_turn(cat: Mapping[str, Any], pair_id: str, cargo_mbd: float) -> dict[str, Any]:
    """A 10-day planning slice, not a queue and not three copies of its demand."""
    finite(cargo_mbd, 'pair cargo')
    if pair_id not in cat['lanes']:
        raise ValueError('unknown pair')
    lane = cat['lanes'][pair_id]
    total = whole_barrels(cargo_mbd, 10)
    split = apportion_barrels(total, lane['class_share_bps'])
    classes = []
    for c in CLASSES:
        ref = route_class_reference(cat, pair_id, c)
        amount = split[c]
        full_lots, remainder = divmod(amount, ref['cargo_bbl'])
        equivalent = amount / ref['cargo_bbl']
        classes.append({
            'class_id': c, 'allocated_plan_bbl': amount, 'share_bps': lane['class_share_bps'][c],
            'reference_parcel_bbl': ref['cargo_bbl'], 'fixture_equivalent': equivalent,
            'whole_parcels_in_this_slice': full_lots, 'remainder_bbl_in_this_slice': remainder,
            'oneway_service_ship_days': equivalent * ref['delivery_lag_turns'] * 10,
            'same_origin_roundtrip_ship_days': equivalent * ref['roundtrip_reference_turns'] * 10,
            'same_origin_roundtrip_vessel_equivalent': equivalent * ref['roundtrip_reference_turns'],
            'model_path_tonne_nm': amount / cat['units']['barrels_per_tonne'] * ref['laden_distance_nm'],
            'catalog_path_id': ref['path_id'], 'geography_ready': ref['geography_ready'],
        })
    return {'pair_id': pair_id, 'display_route_id': lane['display_route_id'],
            'is_residual_pair': lane['is_residual_pair'], 'turn_days': 10,
            'scheduled_plan_bbl': total, 'classes': classes,
            'class_split_residual_bbl': total - sum(row['allocated_plan_bbl'] for row in classes),
            'dispatch_or_price_present': False}


def summarize_world_projection(world: Any, cat: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Research summary: all cargo covered, unresolved geography shown separately.

    Roundtrip equivalent is workload under a reference return assumption, NOT
    required actual global fleet, observed ships, or a dispatch allocation.
    Dynamic accounting tonne-miles remain separate from catalogue path lengths.
    """
    cat = load_catalog() if cat is None else cat
    totals = {c: {'plan_bbl': 0, 'geography_ready_bbl': 0, 'unresolved_geography_bbl': 0,
                  'roundtrip_reference_vessel_turns': 0.0} for c in CLASSES}
    operating_total = calendar_total = residual_total = month_count = 0
    max_replay = max_group = max_rounding = 0.0
    upstream_tonne_nm = model_path_tonne_nm = 0.0
    source_before = sha256_json({'turns': world.turns, 'identity': world.identity})
    for month in replay_pair_months(world, cat):
        month_count += 1
        max_replay = max(max_replay, month['maximum_route_replay_error'])
        max_group = max(max_group, month['maximum_group_cargo_error_mbd'])
        for pid, rate in month['pairs_mbd'].items():
            p = project_pair_turn(cat, pid, rate)
            op = p['scheduled_plan_bbl'] * 3
            source = whole_barrels(rate, month['source_calendar_days'])
            operating_total += op
            calendar_total += source
            if p['is_residual_pair']:
                residual_total += op
            max_rounding = max(max_rounding, abs(op - rate * 30 * 1e6))
            lane = cat['lanes'][pid]
            observed = month['upstream_routes'][lane['display_route_id']]
            # Residual pairs use a common dislocation factor in the upstream owner.
            distance = lane['accounting_distance_nm'] * observed['effective_haul_nm'] / observed['baseline_haul_nm']
            upstream_tonne_nm += op / cat['units']['barrels_per_tonne'] * distance
            for row in p['classes']:
                c, amount = row['class_id'], row['allocated_plan_bbl'] * 3
                totals[c]['plan_bbl'] += amount
                bucket = 'geography_ready_bbl' if row['geography_ready'] else 'unresolved_geography_bbl'
                totals[c][bucket] += amount
                totals[c]['roundtrip_reference_vessel_turns'] += row['same_origin_roundtrip_vessel_equivalent'] * 3
                model_path_tonne_nm += row['model_path_tonne_nm'] * 3
                if row['whole_parcels_in_this_slice'] * row['reference_parcel_bbl'] + row['remainder_bbl_in_this_slice'] != row['allocated_plan_bbl']:
                    raise ProjectionError('parcel/remainder accounting failed')
            if p['class_split_residual_bbl']:
                raise ProjectionError('class split duplicated or lost oil')
    if not month_count:
        raise ProjectionError('world has no monthly prefix')
    if source_before != sha256_json({'turns': world.turns, 'identity': world.identity}):
        raise ProjectionError('read-only projection mutated its source world')
    for row in totals.values():
        row['mean_roundtrip_reference_vessel_equivalent'] = row['roundtrip_reference_vessel_turns'] / (month_count * 3)
    result = {'scope': 'research_catalogue_workload_not_global_market', 'seed': world.seed,
              'catalog_hash': cat['catalog_hash'], 'upstream_identity_hash': world.identity['identity_hash'],
              'month_count': month_count, 'operating_turn_count': month_count * 3,
              'operating_day_count': month_count * 30,
              'source_calendar_plan_bbl': calendar_total, 'operating_plan_bbl': operating_total,
              'clock_projection_difference_bbl': operating_total - calendar_total,
              'residual_11_pair_plan_bbl': residual_total, 'class_totals': totals,
              'class_split_residual_bbl': operating_total - sum(v['plan_bbl'] for v in totals.values()),
              'maximum_pair_month_rounding_error_bbl': max_rounding,
              'maximum_route_replay_error': max_replay,
              'maximum_group_cargo_error_mbd': max_group,
              'upstream_effective_tonne_nm_on_operating_clock': upstream_tonne_nm,
              'catalogue_selected_path_tonne_nm': model_path_tonne_nm,
              'tonne_mile_difference_scope': 'different_geographic_path_assumptions_not_a_conservation_error',
              'source_unchanged': True, 'global_fleet_size_inferred': False,
              'price_present': False, 'cost_present': False}
    result['result_hash'] = sha256_json(result)
    return result
