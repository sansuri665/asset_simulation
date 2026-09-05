"""Stage6A physical catalogue and immutable movement plans, NOT a market.

References, game priors and unresolved geography stay distinguishable. The
existing demand world, Stage5A fleet and both pricing kernels remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .registry import load_registered_assets, sha256_json

MODEL_VERSION = 'asset-simulation-stage6a-contract-v0.1.0'
CONFIG_PATH = Path(__file__).resolve().parents[1] / 'config/global_shipping_physical_v0.1.json'
CLASSES = ('vlcc', 'suezmax', 'aframax')
TURN_DAYS = 10


def finite(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number, not a boolean')
    number = float(value)
    if number < 0 or (positive and number == 0):
        raise ValueError(f'{name} must be {"positive" if positive else "nonnegative"}')
    return number


def integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{name} must be a nonnegative integer')
    return value


def sea_turns(distance_nm: float, speed_knots: float) -> int:
    """Game projection, not a navigation ETA: nearest half-up, positive leg >=1.

    A nearest-turn arrival may precede the continuous reference by <5 days;
    the difference is reported. Ceil-every-leg would break Stage5A's 2+1+2.
    """
    distance = finite(distance_nm, 'distance')
    speed = finite(speed_knots, 'speed', positive=True)
    return 0 if distance == 0 else max(1, math.floor(distance / (24 * speed * TURN_DAYS) + 0.5))


def apportion_barrels(total_bbl: int, shares_bps: Mapping[str, int]) -> dict[str, int]:
    """Largest remainder: each barrel goes to exactly one class, never three."""
    integer(total_bbl, 'total barrels')
    if set(shares_bps) != set(CLASSES):
        raise ValueError('all and only the three modeled classes are required')
    for value in shares_bps.values():
        integer(value, 'basis-point share')
    if sum(shares_bps.values()) != 10000:
        raise ValueError('cargo shares must total 10000 basis points')
    result = {c: total_bbl * shares_bps[c] // 10000 for c in CLASSES}
    order = sorted(CLASSES, key=lambda c: (-(total_bbl * shares_bps[c] % 10000), CLASSES.index(c)))
    for c in order[:total_bbl - sum(result.values())]:
        result[c] += 1
    return result


def _validate_raw(raw: Mapping[str, Any], network: Mapping[str, Any]) -> None:
    if raw['model_version'] != MODEL_VERSION or raw['upstream_network_hash'] != sha256_json(network):
        raise ValueError('catalog version or inherited network fingerprint mismatch')
    expected_clock = {'operating_turn_days': 10, 'turns_per_month': 3,
                      'operating_days_per_year': 360, 'loading_turns': 0, 'discharge_turns': 1}
    for key, value in expected_clock.items():
        if type(raw['clock'][key]) is not int or raw['clock'][key] != value:
            raise ValueError(f'clock.{key} violates the fixed operating clock')
    if raw['clock']['sea_rounding'] != 'nearest_half_up_minimum_one':
        raise ValueError('unsupported sea-time projection')
    if raw['clock']['projection'] != 'preserve_daily_rate_not_calendar_volume':
        raise ValueError('unsupported source-clock projection')
    if raw['clock']['departure_snapshot_frozen'] is not True:
        raise ValueError('voyage assumptions must freeze at departure')
    if tuple(raw['class_order']) != CLASSES or set(raw['vessel_classes']) != set(CLASSES):
        raise ValueError('invalid vessel class universe')
    if raw['scope']['catalog_only'] is not True or raw['scope']['global_fleet_simulator'] is not False:
        raise ValueError('Stage6A cannot claim a global fleet simulation')
    for kind in ('export', 'import'):
        if raw[f'{kind}_region_ids'] != network[f'{kind}_region_ids']:
            raise ValueError('region IDs must match the upstream network')
    exports, imports = raw['export_region_ids'], raw['import_region_ids']
    if set(raw['regions']) != set(exports + imports):
        raise ValueError('region catalogue incomplete')
    for region, spec in raw['regions'].items():
        if spec['role'] != ('export' if region in exports else 'import'):
            raise ValueError('region role mismatch')
        if spec['geography'] not in ('representative_basin', 'aggregate_unresolved'):
            raise ValueError('unknown geography scope')
    for region in ('other_export_regions', 'rest_of_world'):
        if raw['regions'][region]['geography'] != 'aggregate_unresolved':
            raise ValueError('aggregate regions require a subbasin contract before navigation')
    finite(raw['units']['barrels_per_tonne'], 'barrel conversion', positive=True)
    for c, vessel in raw['vessel_classes'].items():
        for field in ('reference_dwt_tonnes', 'default_cargo_tonnes', 'maximum_model_cargo_tonnes',
                      'laden_speed_knots', 'ballast_speed_knots'):
            finite(vessel[field], f'{c}.{field}', positive=True)
        if not vessel['default_cargo_tonnes'] <= vessel['maximum_model_cargo_tonnes'] < vessel['reference_dwt_tonnes']:
            raise ValueError('cargo parcel and deadweight must not be conflated')
        if not set(vessel['sources']) <= set(raw['sources']):
            raise ValueError('unknown vessel provenance')
    matrix = raw['class_share_matrix_bps']
    if set(matrix) != set(exports):
        raise ValueError('share matrix lacks export rows')
    expected_pairs = {f'{o}::{d}' for o in exports for d in imports}
    for o in exports:
        if set(matrix[o]) != set(imports):
            raise ValueError('share matrix lacks destination columns')
        for values in matrix[o].values():
            if len(values) != 3:
                raise ValueError('share cell needs three classes')
            apportion_barrels(1, dict(zip(CLASSES, values)))
    for name in ('parcel_overrides_tonnes', 'path_overrides', 'benchmark_analogues', 'lane_notes'):
        if not set(raw[name]) <= expected_pairs:
            raise ValueError(f'unknown pair in {name}')
    for parcels in raw['parcel_overrides_tonnes'].values():
        if not set(parcels) <= set(CLASSES):
            raise ValueError('unknown parcel class')
        for c, value in parcels.items():
            if finite(value, 'parcel', positive=True) > raw['vessel_classes'][c]['maximum_model_cargo_tonnes']:
                raise ValueError('parcel exceeds class service envelope')
    concrete_exports = [r for r in exports if raw['regions'][r]['geography'] == 'representative_basin']
    expected_cross = {frozenset((a, b)) for a in concrete_exports for b in concrete_exports if a != b}
    observed_cross = set()
    for key, distance in raw['export_cross_distance_priors_nm'].items():
        parts = key.split('::')
        if len(parts) != 2 or not set(parts) <= set(concrete_exports) or parts[0] == parts[1]:
            raise ValueError('invalid cross-export ballast pair')
        pair = frozenset(parts)
        if pair in observed_cross:
            raise ValueError('duplicate reverse cross-export prior')
        observed_cross.add(pair)
        finite(distance, 'ballast prior', positive=True)
    if observed_cross != expected_cross:
        raise ValueError('cross-export priors incomplete')


def load_catalog(*, raw: Mapping[str, Any] | None = None,
                 upstream_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a fresh expanded catalogue; no mutable cached configuration leaks."""
    raw = json.loads(CONFIG_PATH.read_text(encoding='utf-8')) if raw is None else deepcopy(raw)
    upstream = load_registered_assets()['oil_shipping_demand_config'] if upstream_config is None else upstream_config
    network = upstream['route_network']
    _validate_raw(raw, network)
    if raw['units']['barrels_per_tonne'] != upstream['units']['barrels_per_metric_tonne']:
        raise ValueError('upstream and vessel barrel conversions disagree')
    explicit = {f"{r['origin_id']}::{r['destination_id']}": r for r in network['explicit_routes']}
    regions, lanes, ballast = deepcopy(raw['regions']), {}, {}
    exports, imports = raw['export_region_ids'], raw['import_region_ids']
    for o in exports:
        for d in imports:
            pid = f'{o}::{d}'
            ready = all(regions[x]['geography'] == 'representative_basin' for x in (o, d))
            ex = explicit.get(pid)
            parcels = {c: raw['vessel_classes'][c]['default_cargo_tonnes'] for c in CLASSES}
            parcels.update(raw['parcel_overrides_tonnes'].get(pid, {}))
            baseline = network['pair_distances_nm'][o][d]
            default_path = {'path_id': 'reference', 'distance_nm': baseline,
                            'allowed_laden_classes': list(CLASSES), 'source': 'upstream',
                            'confidence': 'basin_reference',
                            'chokepoints': list(ex['chokepoints']) if ex else ['model_basin_proxy'],
                            'access_note': raw['lane_notes'].get(pid, 'Basin service envelope; not a terminal/draft certificate')}
            override = raw['path_overrides'].get(pid, {})
            paths_list = deepcopy(override.get('paths', [default_path]))
            if not paths_list or len({p['path_id'] for p in paths_list}) != len(paths_list):
                raise ValueError('duplicate or empty route path catalogue')
            paths = {p['path_id']: p for p in paths_list}
            defaults = override.get('default_path_by_class', {c: 'reference' for c in CLASSES})
            if set(defaults) != set(CLASSES):
                raise ValueError('all classes need a default reference path')
            for p in paths.values():
                finite(p['distance_nm'], 'path distance', positive=True)
                if not p['allowed_laden_classes'] or not set(p['allowed_laden_classes']) <= set(CLASSES):
                    raise ValueError('invalid path class eligibility')
                if p['source'] not in raw['sources']:
                    raise ValueError('unknown path provenance')
            for c, p in defaults.items():
                if p not in paths or c not in paths[p]['allowed_laden_classes']:
                    raise ValueError('default path is not available to this class')
            lane = {'pair_id': pid, 'origin': o, 'destination': d,
                    'display_route_id': ex['route_id'] if ex else network['other_pool']['route_id'],
                    'is_residual_pair': ex is None, 'geography_ready': ready,
                    'reference_cargo_mbd': network['volume_calibration']['reference_pair_cargo_mbd'][o][d],
                    'accounting_distance_nm': baseline,
                    'class_share_bps': dict(zip(CLASSES, raw['class_share_matrix_bps'][o][d])),
                    'share_status': 'design_prior_not_observed', 'share_confidence': 'low',
                    'cargo_tonnes_by_class': parcels, 'default_path_by_class': dict(defaults),
                    'benchmark_analogues': raw['benchmark_analogues'].get(pid, []), 'paths': paths}
            lanes[pid] = lane
            ballast[f'{d}::{o}'] = {'from': d, 'to': o, 'distance_nm': baseline, 'geography_ready': ready,
                                   'source': 'design', 'basis': 'reverse_basin_reference',
                                   'allowed_classes': list(CLASSES), 'confidence': 'low'}
    for o in exports:
        for d in exports:
            distance = raw['export_cross_distance_priors_nm'].get(f'{o}::{d}',
                       raw['export_cross_distance_priors_nm'].get(f'{d}::{o}'))
            if o == d:
                distance = 0
            ballast[f'{o}::{d}'] = {'from': o, 'to': d, 'distance_nm': distance,
                                    'geography_ready': all(regions[x]['geography'] == 'representative_basin' for x in (o, d)),
                                    'source': 'design', 'basis': 'stay' if o == d else 'cross_export_prior',
                                    'allowed_classes': list(CLASSES), 'confidence': 'low'}
    cat = {'model_version': MODEL_VERSION, 'clock': deepcopy(raw['clock']), 'units': deepcopy(raw['units']),
           'scope': deepcopy(raw['scope']), 'sources': deepcopy(raw['sources']), 'regions': regions,
           'vessel_classes': deepcopy(raw['vessel_classes']), 'lanes': lanes, 'ballast_legs': ballast,
           'raw_config_hash': sha256_json(raw), 'upstream_network_hash': sha256_json(network)}
    cat['catalog_hash'] = sha256_json(cat)
    return cat


def _lookup(cat: Mapping[str, Any], pair_id: str, vessel_class: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if pair_id not in cat['lanes'] or vessel_class not in cat['vessel_classes']:
        raise ValueError('unknown lane or vessel class')
    return cat['lanes'][pair_id], cat['vessel_classes'][vessel_class]


def route_class_reference(cat: Mapping[str, Any], pair_id: str, vessel_class: str, *,
                          path_id: str | None = None, effective_distance_nm: float | None = None) -> dict[str, Any]:
    """Reference workload incl. OPTIONAL same-origin return; no ship assignment.

    effective_distance_nm REPLACES the selected laden distance. It is not
    multiplied again by upstream dislocation. A routing owner must supply a
    class/path-specific value; a mixed upstream average is not a valid route.
    """
    lane, vessel = _lookup(cat, pair_id, vessel_class)
    selected = lane['default_path_by_class'][vessel_class] if path_id is None else path_id
    if selected not in lane['paths'] or vessel_class not in lane['paths'][selected]['allowed_laden_classes']:
        raise ValueError('vessel class cannot use selected laden path')
    path = lane['paths'][selected]
    distance = path['distance_nm'] if effective_distance_nm is None else finite(effective_distance_nm, 'effective laden distance', positive=True)
    reverse = cat['ballast_legs'][f"{lane['destination']}::{lane['origin']}"]
    laden_days = distance / (24 * vessel['laden_speed_knots'])
    ballast_days = reverse['distance_nm'] / (24 * vessel['ballast_speed_knots'])
    lt, bt = sea_turns(distance, vessel['laden_speed_knots']), sea_turns(reverse['distance_nm'], vessel['ballast_speed_knots'])
    tonnes = lane['cargo_tonnes_by_class'][vessel_class]
    barrels_float = tonnes * cat['units']['barrels_per_tonne']
    if not math.isclose(barrels_float, round(barrels_float), rel_tol=0, abs_tol=1e-7):
        raise ValueError('parcel must resolve to whole barrels')
    barrels = int(round(barrels_float))
    share = lane['class_share_bps'][vessel_class] / 10000
    cycle = lt + 1 + bt
    return {'pair_id': pair_id, 'display_route_id': lane['display_route_id'], 'class_id': vessel_class,
            'path_id': selected, 'geography_ready': lane['geography_ready'], 'share_bps': lane['class_share_bps'][vessel_class],
            'cargo_tonnes': tonnes, 'cargo_bbl': barrels, 'laden_distance_nm': distance,
            'ballast_return_distance_nm': reverse['distance_nm'], 'accounting_distance_nm': lane['accounting_distance_nm'],
            'laden_speed_knots': vessel['laden_speed_knots'], 'ballast_speed_knots': vessel['ballast_speed_knots'],
            'reference_laden_days': laden_days, 'reference_ballast_days': ballast_days,
            'laden_turns': lt, 'discharge_turns': 1, 'ballast_return_turns': bt,
            'delivery_lag_turns': lt + 1, 'roundtrip_reference_turns': cycle,
            'laden_rounding_error_days': lt * 10 - laden_days,
            'ballast_rounding_error_days': bt * 10 - ballast_days,
            'reference_cycle_vessels': lane['reference_cargo_mbd'] * 1e6 * share / barrels * cycle * 10,
            'return_scope': 'same_origin_roundtrip_diagnostic_not_mandatory_route',
            'distance_source': path['source'] if effective_distance_nm is None else 'caller_departure_snapshot',
            'price_present': False}


@dataclass(frozen=True)
class MovementPlan:
    kind: str
    origin: str
    destination: str
    vessel_class: str
    path_id: str
    depart_turn: int
    sea_turns: int
    discharge_turns: int
    ready_turn: int
    cargo_bbl: int
    distance_nm: float
    reference_sea_days: float
    catalog_hash: str

    def state_at(self, turn: int) -> str:
        integer(turn, 'turn')
        if turn < self.depart_turn:
            return 'NOT_STARTED'
        if turn < self.depart_turn + self.sea_turns:
            return 'LADEN' if self.kind == 'laden' else 'BALLAST'
        if turn < self.ready_turn:
            return 'DISCHARGING'
        return 'OPEN_AT_DESTINATION'

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value['plan_hash'] = sha256_json(value)
        return value


def laden_plan(cat: Mapping[str, Any], pair_id: str, vessel_class: str, depart_turn: int, *,
               path_id: str | None = None, effective_distance_nm: float | None = None) -> MovementPlan:
    integer(depart_turn, 'departure turn')
    lane, _ = _lookup(cat, pair_id, vessel_class)
    if not lane['geography_ready']:
        raise ValueError('choose a concrete subbasin before making a physical voyage plan')
    ref = route_class_reference(cat, pair_id, vessel_class, path_id=path_id, effective_distance_nm=effective_distance_nm)
    return MovementPlan('laden', lane['origin'], lane['destination'], vessel_class, ref['path_id'],
                        depart_turn, ref['laden_turns'], 1, depart_turn + ref['delivery_lag_turns'],
                        ref['cargo_bbl'], ref['laden_distance_nm'], ref['reference_laden_days'], cat['catalog_hash'])


def ballast_plan(cat: Mapping[str, Any], origin: str, destination: str,
                 vessel_class: str, depart_turn: int) -> MovementPlan:
    integer(depart_turn, 'departure turn')
    if vessel_class not in cat['vessel_classes']:
        raise ValueError('unknown vessel class')
    leg = cat['ballast_legs'].get(f'{origin}::{destination}')
    if leg is None or not leg['geography_ready'] or leg['distance_nm'] is None:
        raise ValueError('unknown/unresolved ballast leg; cannot teleport a ship')
    speed = cat['vessel_classes'][vessel_class]['ballast_speed_knots']
    turns = sea_turns(leg['distance_nm'], speed)
    return MovementPlan('ballast', origin, destination, vessel_class, leg['basis'], depart_turn,
                        turns, 0, depart_turn + turns, 0, leg['distance_nm'],
                        leg['distance_nm'] / (24 * speed), cat['catalog_hash'])
