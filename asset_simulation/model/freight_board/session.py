"""A single state owner commits each opening once; other sessions are forks."""
from __future__ import annotations
from dataclasses import asdict
import json
from threading import RLock
from typing import Mapping
from ..registry import sha256_json
from ..shipping_v3.checkpoint import dump_state, load_state
from .board import _commit_trial, initial_board, open_board, validate_board_state
from .types import BoardSpec, BoardState, ImportRequirement, TrialResult, VERSION


class BoardSession:
    def __init__(self, spec: BoardSpec, state: BoardState | None = None, **initial_options):
        self.spec = spec
        self._state = state if state is not None else initial_board(spec, **initial_options)
        validate_board_state(self._state, spec)
        self._snapshot = None
        self._lock = RLock()

    @property
    def state(self) -> BoardState:
        return self._state

    def open_turn(self, **inputs):
        with self._lock:
            if self._snapshot is not None:
                raise ValueError('turn already open; reuse its frozen snapshot')
            self._snapshot = open_board(self._state, self.spec, **inputs)
            return self._snapshot

    def commit(self, trial: TrialResult):
        with self._lock:
            if self._snapshot is None or trial.snapshot_id != self._snapshot.snapshot_id:
                raise ValueError('snapshot consumed, stale or not owned by this session')
            if self._snapshot.base.identity != self._state.identity:
                raise ValueError('state version changed since opening')
            next_state, record = _commit_trial(self._snapshot, trial)
            self._state = next_state
            self._snapshot = None
            return record

    def checkpoint(self) -> dict:
        with self._lock:
            if self._snapshot is not None:
                raise ValueError('checkpoint settled states only')
            s = self._state
            payload = {'market': dump_state(s.market, self.spec.market),
                       'requirements': [asdict(r) for r in s.requirements],
                       'initial_destination_bbl': s.initial_destination_bbl,
                       'initial_cargo_by_origin_bbl': s.initial_cargo_by_origin_bbl,
                       'cumulative_releases_bbl': s.cumulative_releases_bbl}
            return {'schema': VERSION, 'spec_hash': self.spec.identity,
                    'payload': payload, 'payload_hash': sha256_json(payload)}

    @classmethod
    def restore(cls, checkpoint: Mapping, spec: BoardSpec):
        cp = json.loads(json.dumps(checkpoint, allow_nan=False))
        if cp['schema'] != VERSION or cp['spec_hash'] != spec.identity or sha256_json(cp['payload']) != cp['payload_hash']:
            raise ValueError('checkpoint schema, spec or checksum mismatch')
        p = cp['payload']
        state = BoardState(spec.identity, load_state(p['market'], spec.market),
                           tuple(ImportRequirement(**r) for r in p['requirements']), p['initial_destination_bbl'],
                           tuple(tuple(x) for x in p['initial_cargo_by_origin_bbl']),
                           tuple(tuple(x) for x in p['cumulative_releases_bbl']))
        return cls(spec, state)
