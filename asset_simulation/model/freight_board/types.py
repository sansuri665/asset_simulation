"""Immutable contracts for the cost-free, two-origin dynamic trial board."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Mapping
from ..global_shipping_contract import integer
from ..multi_origin_pricing import finite_signed
from ..registry import sha256_json
from ..shipping_v3.types import BallastOrder, MarketSpec, MarketState, make_market_spec

VERSION = 'stage6c-dynamic-freight-board-preview-v0.1.0'
CONFIG = Path(__file__).resolve().parents[2] / 'config/stage6c_preview_v0.1.json'


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True)
class InventoryBand:
    normal_bbl: int
    hard_low_bbl: int
    soft_low_bbl: int
    soft_high_bbl: int
    hard_high_bbl: int

    def validate(self) -> None:
        if any(type(v) is not int for v in asdict(self).values()):
            raise ValueError('inventory quantities must be integer barrels')
        if not (self.normal_bbl >= 0 and -self.normal_bbl <= self.hard_low_bbl < self.soft_low_bbl < 0
                < self.soft_high_bbl < self.hard_high_bbl):
            raise ValueError('invalid soft/hard bands or negative physical minimum')


@dataclass(frozen=True)
class ImportRequirement:
    requirement_id: str
    due_turn: int
    volume_bbl: int

    def validate(self) -> None:
        if not isinstance(self.requirement_id, str) or not self.requirement_id:
            raise ValueError('stable import requirement ID required')
        integer(self.due_turn, 'requirement due turn')
        integer(self.volume_bbl, 'required barrels')
        if not self.volume_bbl:
            raise ValueError('omit zero-volume requirements')


@dataclass(frozen=True)
class BoardSpec:
    market: MarketSpec
    source_bands: tuple[tuple[str, InventoryBand], ...]
    destination_band: InventoryBand
    config_json: str

    @property
    def origins(self) -> tuple[str, ...]:
        return self.market.origins

    @property
    def identity(self) -> str:
        return _identity(self)

    def config(self) -> dict:
        return json.loads(self.config_json)


@lru_cache(maxsize=64)
def _identity(spec: BoardSpec) -> str:
    return sha256_json(asdict(spec))


def make_board_spec(*, market: MarketSpec | None = None, config: Mapping | None = None,
                    source_bands: Mapping[str, InventoryBand] | None = None,
                    destination_band: InventoryBand | None = None) -> BoardSpec:
    market = make_market_spec() if market is None else market
    if market.origins != ('gulf', 'west_africa') or market.destination != 'east_asia':
        raise ValueError('preview supports Gulf/West Africa to East Asia, not the globe')
    cfg = json.loads(CONFIG.read_text(encoding='utf-8')) if config is None else json.loads(canonical(config))
    if cfg['model_version'] != VERSION or type(cfg['operating_turn_days']) is not int or cfg['operating_turn_days'] != 10:
        raise ValueError('wrong board version or clock')
    weights = cfg['arrival_weights']
    if not isinstance(weights, list) or not 1 <= len(weights) <= 4 or weights[0] != 1:
        raise ValueError('current weight and up to three future weights required')
    if any(not 0 <= finite_signed(w, 'arrival weight') <= 1 for w in weights):
        raise ValueError('arrival weights must be finite in [0,1]')
    if any(a < b for a, b in zip(weights, weights[1:])):
        raise ValueError('future weights must not increase')
    if not 0 <= finite_signed(cfg['reserve_weight'], 'reserve weight') < 1:
        raise ValueError('reserve weight must be in [0,1)')
    if finite_signed(cfg['normal_prompt_multiplier'], 'normal prompt') <= 0:
        raise ValueError('normal prompt must be positive')
    if not 0 <= finite_signed(cfg['inventory_log_premium_limit'], 'inventory premium') <= 1:
        raise ValueError('inventory log premium must be in [0,1]')
    integer(cfg['inventory_projection_turns'], 'inventory horizon')
    integer(cfg['maximum_known_requirement_horizon_turns'], 'requirement horizon')
    if cfg['maximum_known_requirement_horizon_turns'] < cfg['inventory_projection_turns']:
        raise ValueError('requirement horizon must cover the inventory reporting horizon')
    required = max(s.outbound.ready_turn for l in market.physical.lanes for s in l.services)
    if cfg['inventory_projection_turns'] < required:
        raise ValueError('inventory horizon must include all trial arrivals')

    def band(group: str, daily: float) -> InventoryBand:
        vals = {k.replace('_days', '_bbl'): round(finite_signed(v, k) * daily) for k, v in cfg[group].items()}
        b = InventoryBand(**vals)
        b.validate()
        return b

    sources = dict(source_bands) if source_bands is not None else {
        l.origin: band('source_inventory', l.reference_daily_bbl) for l in market.physical.lanes}
    if set(sources) != set(market.origins):
        raise ValueError('one inventory band per source required')
    dest = destination_band or band('destination_inventory', sum(l.reference_daily_bbl for l in market.physical.lanes))
    for b in (*sources.values(), dest):
        b.validate()
    return BoardSpec(market, tuple((o, sources[o]) for o in market.origins), dest, canonical(cfg))


@dataclass(frozen=True)
class BoardState:
    spec_hash: str
    market: MarketState
    requirements: tuple[ImportRequirement, ...]
    initial_destination_bbl: int
    initial_cargo_by_origin_bbl: tuple[tuple[str, int], ...]
    cumulative_releases_bbl: tuple[tuple[str, int], ...]

    @property
    def identity(self) -> str:
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class BoardSnapshot:
    snapshot_id: str
    spec: BoardSpec
    base: BoardState
    opened_market: MarketState
    releases_bbl: tuple[tuple[str, int], ...]
    requirements: tuple[ImportRequirement, ...]
    export_limits_bbl: tuple[tuple[str, int], ...]
    cpi: float


@dataclass(frozen=True)
class RouteTrial:
    origin: str
    cargo_bbl: int
    ordered_ship_ids: tuple[int, ...]


@dataclass(frozen=True)
class TrialPlan:
    routes: tuple[RouteTrial, ...]
    ballasts: tuple[BallastOrder, ...] = ()


@dataclass(frozen=True)
class TrialResult:
    snapshot_id: str
    plan: TrialPlan
    report_json: str

    def report(self) -> dict:
        return json.loads(self.report_json)

    @property
    def identity(self) -> str:
        return sha256_json({'snapshot': self.snapshot_id, 'plan': asdict(self.plan), 'report': self.report()})
