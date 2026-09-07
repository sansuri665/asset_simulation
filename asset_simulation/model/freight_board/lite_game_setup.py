"""Warm start, ownership and turn opening for Main-6C Lite."""
from __future__ import annotations

from typing import Any, Mapping

from ..shipping_v3.types import BallastOrder
from .bilateral_formation import form_seller_offers
from .lite_game_support import (COMPANY_COUNT, COMPANY_NAMES, POLICIES, TOTAL_TURNS, _hash_rank, _ship_status)

class _LiteSetupMixin:
    def _neutral_ballasts(self, snapshot, seller_offers: Mapping[str, Mapping[str, Any]]) -> tuple[BallastOrder, ...]:
        capacity = {origin: 0 for origin in self.spec.origins}
        for ship in snapshot.opened_market.ships:
            if ship.movement is None and ship.location in capacity:
                capacity[ship.location] += ship.capacity_bbl
            elif ship.movement is not None and ship.movement.kind == "ballast" and ship.movement.destination in capacity:
                capacity[ship.movement.destination] += ship.capacity_bbl
        desired = {origin: max(1, row["final_offer_bbl"]) for origin, row in seller_offers.items()}
        orders = []
        idle = sorted(
            (ship for ship in snapshot.opened_market.ships
             if ship.movement is None and ship.location == self.spec.market.destination),
            key=lambda ship: ship.ship_id,
        )
        for ship in idle:
            compatible = [
                origin for origin in self.spec.origins
                if ship.class_id in {service.class_id for service in self.spec.market.physical.lane(origin).services}
            ]
            target = min(compatible, key=lambda origin: (capacity[origin] / desired[origin], self.spec.origins.index(origin)))
            orders.append(BallastOrder(ship.ship_id, target))
            capacity[target] += ship.capacity_bbl
        return tuple(orders)


    def _run_warm_start(self) -> None:
        """Create a real mixed 2030 opening without giving game agents prehistory choices."""
        for row in self.warm_inputs:
            snapshot = self.market.open_turn(
                source_release_bbl=row["source_release_bbl"],
                new_requirements=row["new_requirements"],
                cpi=row["cpi"],
            )
            sellers = form_seller_offers(
                snapshot, row["natural_cargo_plan_bbl"],
                previous_spot_offer_bbl=self.market.previous_spot_offer_bbl,
                formation_spec=self.market.formation_spec,
            )
            ship_plan = {
                origin: tuple(ship.ship_id for ship in snapshot.opened_market.ships
                              if ship.movement is None and ship.location == origin)
                for origin in self.spec.origins
            }
            indication = self.market.indicate(
                ship_plan, row["natural_cargo_plan_bbl"],
                ballast_orders=self._neutral_ballasts(snapshot, sellers),
            )
            if not indication.report()["final_trial_valid"]:
                raise ValueError("neutral warm start failed to find a feasible Preview3 market")
            self.market.lock_firm(indication)
            self.market.commit_firm()


    def _assign_ownership(self) -> dict[int, int]:
        registry = self.market.state.market.initial_registry
        ownership: dict[int, int] = {}
        classes = sorted({row[1] for row in registry})
        for class_id in classes:
            ids = [ship_id for ship_id, cls, _, _ in registry if cls == class_id]
            ids.sort(key=lambda sid: (_hash_rank(self.seed, "owner", class_id, sid), sid))
            for i, ship_id in enumerate(ids):
                ownership[ship_id] = i % COMPANY_COUNT
        if len(ownership) != len(registry):
            raise ValueError("every hull needs exactly one company owner")
        return ownership


    def _company_profile(self, cid: int) -> dict[str, Any]:
        ships = [s for s in self.market.state.market.ships if self.ownership[s.ship_id] == cid]
        status: dict[str, int] = {}
        classes: dict[str, int] = {}
        eta: list[int] = []
        for ship in ships:
            key = _ship_status(ship, self.market.state.market.turn, self.spec.market.destination)
            status[key] = status.get(key, 0) + 1
            classes[ship.class_id] = classes.get(ship.class_id, 0) + 1
            if ship.movement is not None:
                eta.append(max(0, ship.movement.ready_turn - self.market.state.market.turn))
        return {
            "company_id": cid,
            "name": COMPANY_NAMES[cid],
            "policy": POLICIES[cid]["label"],
            "fleet_total": len(ships),
            "classes": classes,
            "status": status,
            "arrivals_next_3_turns": sum(x <= 3 for x in eta),
        }


    def candidate_companies(self) -> list[dict[str, Any]]:
        ids = list(range(COMPANY_COUNT))
        ids.sort(key=lambda cid: (_hash_rank(self.seed, "human-candidate", cid), cid))
        return [self._company_profile(cid) for cid in ids[:3]]


    def choose_company(self, company_id: int) -> dict[str, Any]:
        with self._lock:
            if self.phase != "choose_company":
                raise ValueError("company already selected")
            allowed = {row["company_id"] for row in self.candidate_companies()}
            if company_id not in allowed:
                raise ValueError("choose one of the three offered companies")
            self.human_company_id = company_id
            self.phase = "between_turns"
            self._open_turn()
            return self.public_state()


    def _eligible(self, cid: int) -> dict[str, Any]:
        if self.snapshot is None:
            state = self.market.state.market
        else:
            state = self.snapshot.opened_market
        prompt = {origin: [] for origin in self.spec.origins}
        ea = []
        for ship in state.ships:
            if self.ownership[ship.ship_id] != cid or ship.movement is not None:
                continue
            if ship.location in prompt:
                prompt[ship.location].append(ship.ship_id)
            elif ship.location == self.spec.market.destination:
                ea.append(ship.ship_id)
        for ids in prompt.values():
            ids.sort()
        ea.sort()
        return {"prompt": prompt, "east_asia_idle": ea}


    def _default_action(self, cid: int) -> dict[str, Any]:
        eligible = self._eligible(cid)
        return {
            "offers": {origin: tuple(eligible["prompt"][origin]) for origin in self.spec.origins},
            "ballast_targets": {},
        }


    def _queue_key(self, origin: str, ship_id: int) -> tuple[int, int]:
        return _hash_rank(self.seed, "queue", self.turn_index, origin, ship_id), ship_id


    def _open_turn(self) -> None:
        if self.phase not in {"between_turns"}:
            raise ValueError("finish the previous game turn first")
        if self.turn_index >= TOTAL_TURNS:
            self.phase = "game_over"
            return
        row = self.inputs[self.turn_index]
        self.snapshot = self.market.open_turn(
            source_release_bbl=row["source_release_bbl"],
            new_requirements=row["new_requirements"],
            cpi=row["cpi"],
        )
        self.company_actions = {cid: self._default_action(cid) for cid in range(COMPANY_COUNT)}
        self.queues = {
            origin: sorted(
                [sid for action in self.company_actions.values() for sid in action["offers"][origin]],
                key=lambda sid: self._queue_key(origin, sid),
            )
            for origin in self.spec.origins
        }
        natural = row["natural_cargo_plan_bbl"]
        self.current_indication = self.market.indicate(self.queues, natural)
        self.public_market = self._market_view(self.current_indication)
        self.round_no = 1
        self.round_history = []
        self.phase = "round"
