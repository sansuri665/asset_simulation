"""Public state, leaderboard and checkpoint views for Main-6C Lite."""
from __future__ import annotations

import json
from threading import RLock
from typing import Any, Mapping

from ..registry import sha256_json
from .bilateral_formation import make_preview3_board_spec
from .bilateral_session import BilateralSession
from .lite_game_support import (COMPANY_COUNT, COMPANY_NAMES, POLICIES, GAME_YEARS, ROUND_NAMES, START_YEAR, TOTAL_TURNS, VERSION, _rebase_inputs, _ship_status)

class _LiteViewMixin:
    @property
    def round_token(self) -> str:
        return sha256_json({
            "game": self.game_id,
            "turn": self.turn_index,
            "phase": self.phase,
            "round": self.round_no,
            "market": None if self.public_market is None else sha256_json(self.public_market),
        })[:20]


    def _leaderboard(self) -> list[dict[str, Any]]:
        rows = []
        for cid in range(COMPANY_COUNT):
            row = {"company_id": cid, "name": COMPANY_NAMES[cid], **self.stats[cid]}
            rows.append(row)
        rows.sort(key=lambda row: (-row["gross_service_value_real_usd"], -row["cargo_carried_bbl"], row["company_id"]))
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        return rows


    def _fleet_view(self, cid: int) -> dict[str, Any]:
        state = self.snapshot.opened_market if self.snapshot is not None else self.market.state.market
        status: dict[str, int] = {}
        classes: dict[str, int] = {}
        for ship in state.ships:
            if self.ownership[ship.ship_id] != cid:
                continue
            key = _ship_status(ship, state.turn, self.spec.market.destination)
            status[key] = status.get(key, 0) + 1
            classes[ship.class_id] = classes.get(ship.class_id, 0) + 1
        return {"total": sum(classes.values()), "classes": classes, "status": status}


    def _date_view(self) -> dict[str, Any] | None:
        if self.turn_index >= TOTAL_TURNS:
            return {"year": START_YEAR + GAME_YEARS - 1, "month": 12, "turn_in_month": 2, "label": "2039-12 / T3"}
        row = self.inputs[self.turn_index]
        return {
            "year": row["year"], "month": row["month"], "turn_in_month": row["turn_in_month"],
            "label": f"{row['year']}-{row['month']:02d} / T{row['turn_in_month']}",
        }


    def public_state(self) -> dict[str, Any]:
        with self._lock:
            base = {
                "ok": True,
                "schema": VERSION,
                "game_id": self.game_id,
                "seed": self.seed,
                "start_year": START_YEAR,
                "end_year": START_YEAR + GAME_YEARS - 1,
                "turn": self.turn_index,
                "total_turns": TOTAL_TURNS,
                "phase": self.phase,
                "round": self.round_no,
                "round_name": ROUND_NAMES.get(self.round_no),
                "round_token": self.round_token,
                "date": self._date_view(),
                "main_link": self.main_link,
                "human_company_id": self.human_company_id,
                "candidates": self.candidate_companies() if self.phase == "choose_company" else [],
                "companies": [
                    {"company_id": cid, "name": COMPANY_NAMES[cid], "policy": "Human + Advisor" if cid == self.human_company_id else POLICIES[cid]["label"]}
                    for cid in range(COMPANY_COUNT)
                ],
                "leaderboard": self._leaderboard(),
                "market": self.public_market,
                "can_save": self.phase in {"turn_complete", "game_over"},
                "history_tail": self.turn_history[-12:],
                "current_rounds": [
                    {
                        "round": row["round"],
                        "round_name": row["round_name"],
                        "routes": {origin: {
                            "benchmark_real_tce": row["market"]["routes"][origin]["benchmark_real_tce"],
                            "cargo_bbl": row["market"]["routes"][origin]["cargo_bbl"],
                            "queue_ship_count": row["market"]["routes"][origin]["queue_ship_count"],
                        } for origin in self.spec.origins},
                    }
                    for row in self.round_history
                ],
            }
            if self.human_company_id is not None:
                cid = self.human_company_id
                eligible = self._eligible(cid)
                if self.current_indication is not None:
                    market_positions = self._company_market_row(self.current_indication, cid)
                elif self.public_market is not None:
                    market_positions = self.public_market.get("companies", {}).get(str(cid))
                else:
                    market_positions = None
                base["human"] = {
                    "company": self._company_profile(cid),
                    "fleet": self._fleet_view(cid),
                    "eligible": {
                        "prompt_counts": {origin: len(eligible["prompt"][origin]) for origin in self.spec.origins},
                        "east_asia_idle_count": len(eligible["east_asia_idle"]),
                    },
                    "current_action": self._counts_from_action(cid, self.company_actions[cid]) if self.company_actions else None,
                    "market_positions": market_positions,
                    "advisor": self.advisor(),
                    "last_turn_result": None if not self.turn_history else self.turn_history[-1]["company_results"][str(cid)],
                }
            return json.loads(json.dumps(base, allow_nan=False))


    def checkpoint(self) -> dict[str, Any]:
        with self._lock:
            if self.phase not in {"turn_complete", "game_over"}:
                raise ValueError("save after a committed physical turn")
            payload = {
                "seed": self.seed,
                "human_company_id": self.human_company_id,
                "turn_index": self.turn_index,
                "ownership": sorted(self.ownership.items()),
                "stats": self.stats,
                "turn_history": self.turn_history,
                "market": self.market.checkpoint(),
                "main_link_identity": self.main_link["identity"],
            }
            return {"schema": VERSION, "payload": payload, "payload_hash": sha256_json(payload)}


    @classmethod
    def restore(cls, checkpoint: Mapping[str, Any]) -> "LiteGameSession":
        cp = json.loads(json.dumps(checkpoint, allow_nan=False))
        if cp.get("schema") != VERSION or sha256_json(cp.get("payload")) != cp.get("payload_hash"):
            raise ValueError("Lite game checkpoint checksum or schema mismatch")
        payload = cp["payload"]
        obj = cls.__new__(cls)
        obj.seed = payload["seed"]
        obj.spec = make_preview3_board_spec()
        obj.warm_inputs, obj.inputs, obj.main_link = _rebase_inputs(obj.seed, obj.spec)
        if payload.get("main_link_identity") != obj.main_link["identity"]:
            raise ValueError("Lite save belongs to another Main-linked world")
        obj.market = BilateralSession.restore(payload["market"], obj.spec)
        obj.ownership = obj._assign_ownership()
        ownership = {int(k): int(v) for k, v in payload["ownership"]}
        if ownership != obj.ownership:
            raise ValueError("Lite save changed deterministic vessel ownership")
        obj.human_company_id = payload["human_company_id"]
        obj.turn_index = payload["turn_index"]
        obj.stats = {int(cid): dict(row) for cid, row in payload["stats"].items()}
        obj.turn_history = list(payload["turn_history"])
        obj.phase = "game_over" if obj.turn_index >= TOTAL_TURNS else "turn_complete"
        obj.round_no = 0
        obj.snapshot = None
        obj.current_indication = None
        obj.public_market = obj.turn_history[-1]["final_market"] if obj.turn_history else None
        obj.company_actions = {}
        obj.queues = {origin: [] for origin in obj.spec.origins}
        obj.round_history = []
        obj._lock = RLock()
        obj.game_id = sha256_json({"version": VERSION, "seed": obj.seed, "world": obj.main_link["identity"]})[:16]
        return obj
