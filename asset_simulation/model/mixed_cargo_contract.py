"""Stage6B v0.2 physical services. Cargo belongs to a route, never a class.

Extract ONLY geography/compatibility, speeds and fixed class capacities from
Stage6A. Its benchmark parcels and class-share priors are not runtime inputs.
Legacy Stage6A/6B modules remain unchanged as reproducible controls.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .global_shipping_contract import CLASSES, MovementPlan, integer, load_catalog, sea_turns
from .multi_origin_pricing import finite_signed
from .registry import sha256_json

VERSION = 'stage6b-class-agnostic-v0.2.0'
CONFIG = Path(__file__).resolve().parents[1] / 'config/stage6b_mixed_fleet_v0.2.json'


def validate_config(cfg: Mapping[str, Any]) -> None:
    if cfg['model_version'] != VERSION or type(cfg['operating_turn_days']) is not int or cfg['operating_turn_days'] != 10:
        raise ValueError('invalid mixed-market version or operating clock')
    if cfg['execution'] != {'policy': 'max_volume_full_load', 'partial_loads': False}:
        raise ValueError('only the explicit full-load test policy is supported')
    integer(cfg['warmup_turns'], 'warmup')
    if set(cfg['default_fleet_counts']) != set(CLASSES):
        raise ValueError('register all three classes, including zero fleets')
    for c, n in cfg['default_fleet_counts'].items():
        integer(n, c)
    for group in ('routing', 'pricing', 'pressure'):
        for k, v in cfg[group].items():
            if k != 'mode' and finite_signed(v, k) < 0:
                raise ValueError(f'{k} must be nonnegative')
    r, p, h = (cfg[k] for k in ('routing', 'pricing', 'pressure'))
    if r['mode'] not in ('pooled', 'home_return'):
        raise ValueError('unknown routing policy')
    integer(r['cross_origin_limit'], 'cross-origin limit')
    integer(p['reference_capacity_bbl'], 'reference capacity')
    if not 0 <= r['forecast_persistence'] < 1 or not 0 <= p['price_persistence'] < 1:
        raise ValueError('invalid persistence')
    if not 0 <= p['shared_signal_weight'] <= 1:
        raise ValueError('shared weight must be in [0,1]')
    if min(p['reference_capacity_bbl'], p['liquidity_fraction'], p['reference_prompt_multiplier'],
           p['local_signal_limit'], p['local_evidence_capacity_bbl'], *h.values()) <= 0:
        raise ValueError('positive quote scales required')
    if not 0 < p['minimum_real_tce_2025_usd_per_day'] < p['baseline_real_tce_2025_usd_per_day'] < p['maximum_real_tce_2025_usd_per_day']:
        raise ValueError('price bounds must bracket the reference benchmark')


def load_mixed_config() -> dict[str, Any]:
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    validate_config(cfg)
    return cfg


@dataclass(frozen=True)
class Service:
    class_id: str
    capacity_bbl: int
    outbound: MovementPlan
    return_leg: MovementPlan

    @property
    def cycle_turns(self) -> int:
        return self.outbound.ready_turn + self.return_leg.ready_turn


@dataclass(frozen=True)
class Lane:
    origin: str
    pair_id: str
    display_route_id: str
    reference_daily_bbl: float
    reference_delivery_turns: int
    reference_cycle_turns: int
    services: tuple[Service, ...]

    def service(self, class_id: str) -> Service:
        for service in self.services:
            if service.class_id == class_id:
                return service
        raise ValueError(f'{class_id} is incompatible with {self.pair_id}')


@dataclass(frozen=True)
class MixedSpec:
    destination: str
    lanes: tuple[Lane, ...]
    capacities: tuple[tuple[str, int], ...]
    ballast_legs: tuple[MovementPlan, ...]
    physical_hash: str
    config_json: str

    @property
    def origins(self) -> tuple[str, ...]:
        return tuple(l.origin for l in self.lanes)

    @property
    def identity(self) -> str:
        return spec_identity(self)

    def config(self) -> dict[str, Any]:
        return json.loads(self.config_json)

    def lane(self, origin: str) -> Lane:
        for lane in self.lanes:
            if lane.origin == origin:
                return lane
        raise ValueError('unknown origin')


@lru_cache(maxsize=64)
def spec_identity(spec: MixedSpec) -> str:
    return sha256_json(asdict(spec))


def at_turn(template: MovementPlan, turn: int) -> MovementPlan:
    integer(turn, 'departure turn')
    return replace(template, depart_turn=turn, ready_turn=turn + template.ready_turn)


def make_mixed_spec(*, origins: Sequence[str] | None = None, destination: str | None = None,
                    catalog: Mapping[str, Any] | None = None,
                    config: Mapping[str, Any] | None = None) -> MixedSpec:
    cfg = load_mixed_config() if config is None else json.loads(json.dumps(config, allow_nan=False))
    validate_config(cfg)
    cat = load_catalog() if catalog is None else catalog
    if cat['catalog_hash'] != sha256_json({k: v for k, v in cat.items() if k != 'catalog_hash'}):
        raise ValueError('catalogue fingerprint mismatch')
    origins = cfg['default_origins'] if origins is None else origins
    destination = cfg['destination'] if destination is None else destination
    if isinstance(origins, str) or not origins or any(not isinstance(o, str) for o in origins) or len(set(origins)) != len(origins):
        raise ValueError('unique nonempty origin sequence required')
    origins = tuple(sorted(origins))
    if destination in origins:
        raise ValueError('destination must differ from all origins')
    if cat['clock']['operating_turn_days'] != 10 or cat['clock']['loading_turns'] != 0 or cat['clock']['discharge_turns'] != 1:
        raise ValueError('unsupported physical clock')
    caps = {}
    for c in CLASSES:
        vessel = cat['vessel_classes'][c]
        cargo = vessel['default_cargo_tonnes'] * cat['units']['barrels_per_tonne']
        if cargo <= 0 or not math.isclose(cargo, round(cargo), abs_tol=1e-7):
            raise ValueError('class capacity must be positive whole barrels')
        caps[c] = round(cargo)
    lanes, legs = [], []
    for o in origins:
        pid = f'{o}::{destination}'
        lane = cat['lanes'].get(pid)
        if lane is None or not lane['geography_ready']:
            raise ValueError('only resolved representative-basin lanes may execute')
        services = []
        for c in CLASSES:
            vessel = cat['vessel_classes'][c]
            selected = lane['default_path_by_class'][c]
            path = lane['paths'][selected]
            if c not in path['allowed_laden_classes']:
                continue
            back = cat['ballast_legs'][f'{destination}::{o}']
            if not back['geography_ready'] or c not in back['allowed_classes']:
                continue
            dist, speed = path['distance_nm'], vessel['laden_speed_knots']
            lt = sea_turns(dist, speed)
            out = MovementPlan('laden', o, destination, c, selected, 0, lt, 1, lt+1,
                               caps[c], dist, dist/(24*speed), '')
            bd, bs = back['distance_nm'], vessel['ballast_speed_knots']
            bt = sea_turns(bd, bs)
            ret = MovementPlan('ballast', destination, o, c, 'basin_reference', 0, bt, 0, bt,
                               0, bd, bd/(24*bs), '')
            services.append(Service(c, caps[c], out, ret))
        if not services:
            raise ValueError('lane has no executable class')
        ref = next((s for s in services if s.class_id == 'vlcc'), services[0])
        daily = finite_signed(lane['reference_cargo_mbd'], 'reference cargo') * 1e6
        if daily <= 0:
            raise ValueError('positive total reference cargo required')
        lanes.append(Lane(o, pid, lane['display_route_id'], daily,
                          ref.outbound.ready_turn, ref.cycle_turns, tuple(services)))
    for a in (destination, *origins):
        for b in origins:
            if a == b:
                continue
            leg = cat['ballast_legs'][f'{a}::{b}']
            if not leg['geography_ready']:
                raise ValueError('unresolved ballast geography')
            for c in CLASSES:
                if c not in leg['allowed_classes']:
                    continue
                d, speed = leg['distance_nm'], cat['vessel_classes'][c]['ballast_speed_knots']
                ticks = sea_turns(d, speed)
                legs.append(MovementPlan('ballast', a, b, c, 'basin_reference', 0, ticks, 0, ticks,
                                         0, d, d/(24*speed), ''))
    # No class-share or benchmark-parcel field enters this fingerprint.
    physical = sha256_json({'lanes': [asdict(l) for l in lanes], 'legs': [asdict(l) for l in legs], 'capacities': caps})
    legs = [replace(l, catalog_hash=physical) for l in legs]
    lanes = [replace(l, services=tuple(replace(s, outbound=replace(s.outbound, catalog_hash=physical),
                                             return_leg=replace(s.return_leg, catalog_hash=physical)) for s in l.services)) for l in lanes]
    return MixedSpec(destination, tuple(lanes), tuple(caps.items()), tuple(legs), physical,
                     json.dumps(cfg, sort_keys=True, separators=(',', ':'), allow_nan=False))
