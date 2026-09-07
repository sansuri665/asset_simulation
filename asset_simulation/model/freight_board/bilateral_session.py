"""Single-writer Preview3 negotiation, firmness and one-turn route locks."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping, Sequence

from ..global_shipping_contract import integer
from ..registry import sha256_json
from ..shipping_v3.types import BallastOrder
from .bilateral_formation import (
    BilateralFormationResult,
    BilateralFormationSpec,
    VERSION,
    form_bilateral_cargo_plan,
    make_bilateral_formation_spec,
)
from .board import quote_trial
from .session import BoardSession
from .types import BoardSpec, TrialResult


@dataclass(frozen=True)
class FirmCommitment:
    snapshot_id: str
    indication_id: str
    trial: TrialResult
    locked_ship_ids: tuple[int, ...]
    locked_cargo_plan: tuple[tuple[str, int], ...]
    cargo_revision_bbl: tuple[tuple[str, int], ...]

    @property
    def identity(self) -> str:
        return sha256_json({
            "snapshot": self.snapshot_id,
            "indication": self.indication_id,
            "trial": self.trial.identity,
            "ships": self.locked_ship_ids,
            "cargo": self.locked_cargo_plan,
            "revision": self.cargo_revision_bbl,
        })


class BilateralSession:
    """Allow free indications, then exactly one binding plan or cancellation.

    Once firm, neither ship IDs nor route cargo can be replaced. Committing or
    cancelling advances the underlying physical turn, so an unused or withdrawn
    firm ship cannot be offered to a second route in the same turn.
    """

    def __init__(
        self,
        board_spec: BoardSpec,
        *,
        formation_spec: BilateralFormationSpec | None = None,
        board_state=None,
        previous_spot_offer_bbl: Mapping[str, int] | None = None,
        **initial_options,
    ):
        self.board_spec = board_spec
        self.formation_spec = make_bilateral_formation_spec() if formation_spec is None else formation_spec
        self.board = BoardSession(board_spec, state=board_state, **initial_options)
        self._previous_spot = None if previous_spot_offer_bbl is None else dict(previous_spot_offer_bbl)
        self._snapshot = None
        self._indications: dict[str, BilateralFormationResult] = {}
        self._firm: FirmCommitment | None = None

    @property
    def state(self):
        return self.board.state

    @property
    def phase(self) -> str:
        if self._snapshot is None:
            return "ready"
        return "firm" if self._firm is not None else "indicative"

    @property
    def previous_spot_offer_bbl(self) -> dict[str, int] | None:
        return None if self._previous_spot is None else dict(self._previous_spot)

    def open_turn(self, **inputs):
        if self._snapshot is not None:
            raise ValueError("Preview3 turn already open")
        self._snapshot = self.board.open_turn(**inputs)
        self._indications = {}
        self._firm = None
        return self._snapshot

    def indicate(
        self,
        trial_ship_plan: Mapping[str, Sequence[int]],
        natural_cargo_plan: Mapping[str, int],
        *,
        ballast_orders: Sequence[BallastOrder] = (),
    ) -> BilateralFormationResult:
        if self._snapshot is None:
            raise ValueError("open a Preview3 turn before requesting an indication")
        if self._firm is not None:
            raise ValueError("firm ship and cargo intentions are locked for this turn")
        result = form_bilateral_cargo_plan(
            self._snapshot,
            trial_ship_plan,
            natural_cargo_plan,
            previous_spot_offer_bbl=self._previous_spot,
            ballast_orders=ballast_orders,
            formation_spec=self.formation_spec,
        )
        self._indications[result.identity] = result
        return result

    def lock_firm(
        self,
        indication: BilateralFormationResult,
        *,
        cargo_revision_bbl: Mapping[str, int] | None = None,
    ) -> FirmCommitment:
        if self._snapshot is None or self._firm is not None:
            raise ValueError("no open indicative phase available for firm locking")
        stored = self._indications.get(indication.identity)
        if stored != indication or indication.snapshot_id != self._snapshot.snapshot_id:
            raise ValueError("firm plan must come from this session's current indicative phase")
        origins = self.board_spec.origins
        indicative = {route.origin: route.cargo_bbl for route in indication.final_trial.plan.routes}
        firm = dict(indicative)
        if cargo_revision_bbl is not None:
            if set(cargo_revision_bbl) != set(origins):
                raise ValueError("firm cargo revision must identify every origin")
            natural = dict(indication.natural_cargo_plan)
            seller_caps = indication.report()["seller_offer_bbl"]
            for origin in origins:
                integer(cargo_revision_bbl[origin], "firm cargo revision")
                limit = round(natural[origin] * self.formation_spec.firm_cargo_revision_fraction)
                if abs(cargo_revision_bbl[origin] - indicative[origin]) > limit:
                    raise ValueError("firm cargo revision exceeds the declared tolerance")
                if cargo_revision_bbl[origin] > seller_caps[origin]:
                    raise ValueError("firm cargo revision exceeds the seller offer")
                firm[origin] = cargo_revision_bbl[origin]
        ship_plan = {route.origin: route.ordered_ship_ids for route in indication.final_trial.plan.routes}
        trial = quote_trial(
            self._snapshot,
            ship_plan,
            firm,
            ballast_orders=indication.final_trial.plan.ballasts,
        )
        if not trial.report()["valid"]:
            raise ValueError("firm revision creates an infeasible inventory plan")
        locked_ids = tuple(
            ship_id for route in trial.plan.routes for ship_id in route.ordered_ship_ids
        ) + tuple(order.ship_id for order in trial.plan.ballasts)
        if len(set(locked_ids)) != len(locked_ids):
            raise ValueError("a firm ship cannot be offered or ballasted twice")
        revision = tuple((origin, firm[origin] - indicative[origin]) for origin in origins)
        self._firm = FirmCommitment(
            self._snapshot.snapshot_id,
            indication.identity,
            trial,
            locked_ids,
            tuple((origin, firm[origin]) for origin in origins),
            revision,
        )
        return self._firm

    def commit_firm(self) -> dict:
        if self._firm is None:
            raise ValueError("lock one firm plan before commit")
        indication = self._indications[self._firm.indication_id]
        record = self.board.commit(self._firm.trial)
        self._previous_spot = dict(indication.seller_spot_offer)
        record.update({
            "preview3_commitment_id": self._firm.identity,
            "indication_id": indication.identity,
            "locked_ship_ids": self._firm.locked_ship_ids,
            "locked_cargo_plan_bbl": dict(self._firm.locked_cargo_plan),
            "firm_cargo_revision_bbl": dict(self._firm.cargo_revision_bbl),
            "firm_plan_cannot_retarget_within_turn": True,
        })
        self._snapshot = None
        self._indications = {}
        self._firm = None
        return record

    def cancel_firm(self) -> dict:
        """Cancel the firm plan by settling a zero-action turn, when feasible.

        Cancellation does not restore same-turn optionality. The physical turn
        is consumed; released source oil and import obligations remain real.
        """
        if self._firm is None or self._snapshot is None:
            raise ValueError("only a firm Preview3 plan can be cancelled")
        empty_ships = {origin: () for origin in self.board_spec.origins}
        zero_cargo = {origin: 0 for origin in self.board_spec.origins}
        cancellation = quote_trial(self._snapshot, empty_ships, zero_cargo)
        if not cancellation.report()["valid"]:
            raise ValueError("cancellation would breach a physical inventory bound")
        locked = self._firm
        indication = self._indications[locked.indication_id]
        record = self.board.commit(cancellation)
        self._previous_spot = dict(indication.seller_spot_offer)
        record.update({
            "cancelled_commitment_id": locked.identity,
            "cancelled_locked_ship_ids": locked.locked_ship_ids,
            "same_turn_retargeting_allowed": False,
            "cancellation_consumed_turn": True,
        })
        self._snapshot = None
        self._indications = {}
        self._firm = None
        return record

    def checkpoint(self) -> dict:
        """Persist only settled Preview3 state, including seller offer memory."""
        if self._snapshot is not None:
            raise ValueError("checkpoint Preview3 only after commit or cancellation")
        payload = {
            "board": self.board.checkpoint(),
            "formation_spec_hash": self.formation_spec.identity,
            "previous_spot_offer_bbl": None if self._previous_spot is None else dict(self._previous_spot),
        }
        return {
            "schema": VERSION,
            "board_spec_hash": self.board_spec.identity,
            "payload": payload,
            "payload_hash": sha256_json(payload),
        }

    @classmethod
    def restore(
        cls,
        checkpoint: Mapping,
        board_spec: BoardSpec,
        *,
        formation_spec: BilateralFormationSpec | None = None,
    ) -> "BilateralSession":
        formation = make_bilateral_formation_spec() if formation_spec is None else formation_spec
        cp = json.loads(json.dumps(checkpoint, allow_nan=False))
        if (cp.get("schema") != VERSION or cp.get("board_spec_hash") != board_spec.identity
                or sha256_json(cp.get("payload")) != cp.get("payload_hash")):
            raise ValueError("Preview3 checkpoint schema, board spec or checksum mismatch")
        payload = cp["payload"]
        if payload.get("formation_spec_hash") != formation.identity:
            raise ValueError("Preview3 checkpoint uses a different formation specification")
        previous = payload.get("previous_spot_offer_bbl")
        if previous is not None:
            if set(previous) != set(board_spec.origins):
                raise ValueError("Preview3 checkpoint seller memory is incomplete")
            for amount in previous.values():
                integer(amount, "checkpoint seller spot offer")
        board = BoardSession.restore(payload["board"], board_spec)
        return cls(
            board_spec,
            formation_spec=formation,
            board_state=board.state,
            previous_spot_offer_bbl=previous,
        )
