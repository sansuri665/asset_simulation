"""Exact, versioned checkpoints; queues and manifests survive restarts."""
from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any, Mapping

from ..global_shipping_contract import MovementPlan
from ..registry import sha256_json
from .engine import validate_state
from .types import VERSION, CargoBatch, CargoSlice, MarketSpec, MarketState, OriginSignal, Vessel


def dump_state(state: MarketState, spec: MarketSpec) -> dict[str, Any]:
    validate_state(state,spec)
    if state.phase!='ready':
        raise ValueError('checkpoint only after a settled turn or at initialization')
    payload=asdict(state)
    return {'schema':VERSION,'spec_hash':spec.identity,'state':payload,'state_hash':sha256_json(payload)}


def load_state(checkpoint: Mapping[str,Any], spec: MarketSpec) -> MarketState:
    cp=json.loads(json.dumps(checkpoint,allow_nan=False))
    if cp['schema']!=VERSION or cp['spec_hash']!=spec.identity or cp['state_hash']!=sha256_json(cp['state']):
        raise ValueError('checkpoint schema, spec or checksum mismatch')
    raw=cp['state'];ships=[]
    for row in raw['ships']:
        row=dict(row)
        row['movement']=MovementPlan(**row['movement']) if row['movement'] else None
        row['manifest']=tuple(CargoSlice(**x) for x in row['manifest'])
        ships.append(Vessel(**row))
    raw['ships']=tuple(ships)
    raw['batches']=tuple(CargoBatch(**x) for x in raw['batches'])
    raw['signals']=tuple(OriginSignal(**x) for x in raw['signals'])
    raw['initial_registry']=tuple(tuple(x) for x in raw['initial_registry'])
    state=MarketState(**raw);validate_state(state,spec)
    if state.phase!='ready':
        raise ValueError('checkpoint contains an unsettled snapshot')
    return state
