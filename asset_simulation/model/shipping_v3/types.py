"""Immutable Stage6B-v3 contracts. All public turns start at zero.

Negative departure/release turns are allowed ONLY for explicitly registered
bootstrap voyages; they represent the known prehistory at initialization.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..global_shipping_contract import CLASSES, MovementPlan, integer
from ..mixed_cargo_contract import MixedSpec, make_mixed_spec
from ..multi_origin_pricing import finite_signed
from ..registry import sha256_json

VERSION = "stage6b-transparent-market-v0.3.0"
CONFIG = Path(__file__).resolve().parents[2] / "config/stage6b_market_v0.3.json"


def validate_config(cfg: Mapping[str, Any]) -> None:
    if cfg['model_version'] != VERSION or type(cfg['operating_turn_days']) is not int or cfg['operating_turn_days'] != 10:
        raise ValueError('invalid v3 version or ten-day clock')
    for key in ('warmup_turns',):
        integer(cfg[key], key)
    if set(cfg['default_fleet_counts']) != set(CLASSES):
        raise ValueError('register all class counts including zero')
    for c, count in cfg['default_fleet_counts'].items():
        integer(count, c)
    weights = cfg['availability']['arrival_weights']
    if not isinstance(weights, list) or not 1 <= len(weights) <= 4:
        raise ValueError('availability horizon must be zero to three future turns')
    for value in weights:
        if not 0 <= finite_signed(value, 'arrival weight') <= 1:
            raise ValueError('arrival weights must be in [0,1]')
    if weights[0] != 1 or any(a < b for a, b in zip(weights, weights[1:])):
        raise ValueError('current weight must equal one; future weights must not increase')
    if finite_signed(cfg['availability']['reference_prompt_multiplier'], 'normal prompt') <= 0:
        raise ValueError('positive reference prompt multiplier required')
    p, h, d = cfg['pricing'], cfg['pressure'], cfg['demo_policy']
    for group in (p, h):
        for key, value in group.items():
            if finite_signed(value, key) < 0:
                raise ValueError(f'{key} must be nonnegative')
    if min(p['reference_capacity_bbl'], p['liquidity_fraction'], p['local_signal_limit'],
           p['local_evidence_capacity_bbl'], *h.values()) <= 0:
        raise ValueError('positive price and pressure scales required')
    integer(p['reference_capacity_bbl'], 'reference capacity')
    if not 0 <= p['price_persistence'] < 1 or not 0 <= p['shared_signal_weight'] <= 1:
        raise ValueError('invalid price persistence or shared weight')
    if not 0 <= p['urgency_recovery_fraction'] * h['limit_days'] / 10 < 1:
        raise ValueError('bounded negative urgency must leave positive quote work')
    if not 0 < p['numeric_minimum_real_tce'] < p['baseline_real_tce_2025_usd_per_day'] < p['numeric_maximum_real_tce']:
        raise ValueError('numeric safety range must bracket baseline')
    if not 0 < finite_signed(d['minimum_tail_load_fraction'], 'tail threshold') <= 1:
        raise ValueError('demo tail threshold must be in (0,1]')
    if not 0 <= finite_signed(d['forecast_persistence'], 'demo persistence') < 1:
        raise ValueError('invalid demo forecast persistence')
    if finite_signed(d['spare_loading_fraction'], 'spare fraction') < 0:
        raise ValueError('negative demo spare fraction')
    integer(d['cross_origin_limit'], 'cross-origin limit')
    if d['routing_mode'] not in ('pooled', 'home_return'):
        raise ValueError('invalid demo routing policy')
    for pair, lag in cfg['cargo_due_lag_by_pair'].items():
        if not isinstance(pair, str):
            raise ValueError('invalid due-date pair')
        integer(lag, 'cargo service lag')
        if lag == 0:
            raise ValueError('default service lag must be positive')


def load_config() -> dict[str, Any]:
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    validate_config(cfg)
    return cfg


@dataclass(frozen=True)
class MarketSpec:
    physical: MixedSpec
    due_lags: tuple[tuple[str, int], ...]
    config_json: str

    @property
    def origins(self) -> tuple[str, ...]:
        return self.physical.origins

    @property
    def destination(self) -> str:
        return self.physical.destination

    @property
    def identity(self) -> str:
        return _spec_hash(self)

    def config(self) -> dict[str, Any]:
        return json.loads(self.config_json)


@lru_cache(maxsize=64)
def _spec_hash(spec: MarketSpec) -> str:
    return sha256_json(asdict(spec))


def make_market_spec(*, origins: Sequence[str] | None = None, destination: str | None = None,
                     catalog: Mapping[str, Any] | None = None,
                     config: Mapping[str, Any] | None = None) -> MarketSpec:
    cfg = load_config() if config is None else json.loads(json.dumps(config, allow_nan=False))
    validate_config(cfg)
    origins = cfg['default_origins'] if origins is None else origins
    destination = cfg['destination'] if destination is None else destination
    physical = make_mixed_spec(origins=origins, destination=destination, catalog=catalog)
    lags = []
    for lane in physical.lanes:
        if lane.pair_id not in cfg['cargo_due_lag_by_pair']:
            raise ValueError('provide an explicit cargo service lag for every selected route')
        lags.append((lane.origin, cfg['cargo_due_lag_by_pair'][lane.pair_id]))
    return MarketSpec(physical, tuple(lags), json.dumps(cfg, sort_keys=True, separators=(',', ':'), allow_nan=False))


@dataclass(frozen=True)
class BatchOrder:
    batch_id: str
    origin: str
    volume_bbl: int
    due_turn: int


@dataclass(frozen=True)
class CargoBatch:
    batch_id: str
    origin: str
    destination: str
    created_turn: int
    due_turn: int
    total_bbl: int
    remaining_bbl: int
    delivered_bbl: int = 0
    bootstrap: bool = False

    @property
    def transit_bbl(self) -> int:
        return self.total_bbl - self.remaining_bbl - self.delivered_bbl


@dataclass(frozen=True)
class CargoSlice:
    batch_id: str
    cargo_bbl: int


@dataclass(frozen=True)
class Vessel:
    ship_id: int
    class_id: str
    capacity_bbl: int
    home_origin: str
    last_origin: str
    location: str | None
    movement: MovementPlan | None = None
    manifest: tuple[CargoSlice, ...] = ()
    booked_net_service_value_per_bbl: float | None = None


@dataclass(frozen=True)
class OriginSignal:
    origin: str
    pressure_days: float = 0.0
    market_log_signal: float = 0.0
    urgency_log_signal: float = 0.0
    forecast_bbl: float = 0.0


@dataclass(frozen=True)
class MarketState:
    spec_hash: str
    initial_registry: tuple[tuple[int, str, int, str], ...]
    turn: int
    ships: tuple[Vessel, ...]
    batches: tuple[CargoBatch, ...]
    signals: tuple[OriginSignal, ...]
    destination_pressure: float = 0.0
    phase: str = "ready"


@dataclass(frozen=True)
class LoadOrder:
    ship_id: int
    cargo_bbl: int


@dataclass(frozen=True)
class BallastOrder:
    ship_id: int
    target_origin: str


@dataclass(frozen=True)
class Decision:
    snapshot_id: str
    loads: tuple[LoadOrder, ...] = ()
    ballasts: tuple[BallastOrder, ...] = ()
    policy: str = 'external'


@dataclass(frozen=True)
class MarketSnapshot:
    """A quote is frozen before a decision; recomputation never moves ships."""
    snapshot_id: str
    opened_state: MarketState
    scheduled_json: str
    quote_json: str
    events_json: str
    destination_pressure_open: float
    cpi: float

    def quotes(self) -> dict[str, Any]:
        return json.loads(self.quote_json)

    def scheduled(self) -> dict[str, int]:
        return json.loads(self.scheduled_json)


def shift_plan(template: MovementPlan, depart: int, *, cargo_bbl: int = 0) -> MovementPlan:
    integer(depart, 'departure turn')
    integer(cargo_bbl, 'actual load')
    return replace(template, depart_turn=depart, ready_turn=depart + template.ready_turn, cargo_bbl=cargo_bbl)


def stable_fingerprint(value: Any) -> str:
    """Immutable dataclass repr is deterministic (tuples/order are validated).

    Snapshots are in-process decision tokens, not cross-language public IDs.
    Public run/config/report hashes continue to use canonical JSON.
    """
    return hashlib.sha256(repr(value).encode('utf-8')).hexdigest()
