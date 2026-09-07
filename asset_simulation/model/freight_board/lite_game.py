"""Main-6C Lite fixed-fleet three-round shipping game."""
from __future__ import annotations

from threading import RLock
from typing import Any

from ..registry import sha256_json
from .bilateral_formation import make_preview3_board_spec
from .bilateral_session import BilateralSession
from .lite_game_support import (
    VERSION, START_YEAR, GAME_YEARS, TURNS_PER_YEAR, TOTAL_TURNS, COMPANY_COUNT, FLEET_COUNTS,
    ROUND_NAMES, COMPANY_NAMES, POLICIES, ADVISOR_POLICY, _rebase_inputs,
)
from .lite_game_setup import _LiteSetupMixin
from .lite_game_market import _LiteMarketMixin
from .lite_game_view import _LiteViewMixin

class LiteGameSession(_LiteViewMixin, _LiteMarketMixin, _LiteSetupMixin):
    """One deterministic local game. Human and AIs submit to one Preview3 market."""

    def __init__(self, *, seed: int = 42):
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        self.seed = seed
        self.spec = make_preview3_board_spec()
        self.warm_inputs, self.inputs, self.main_link = _rebase_inputs(seed, self.spec)
        self.market = BilateralSession(
            self.spec,
            fleet_counts=FLEET_COUNTS,
            initialization="phased",
        )
        self._run_warm_start()
        self.ownership = self._assign_ownership()
        self.human_company_id: int | None = None
        self.turn_index = 0
        self.phase = "choose_company"
        self.round_no = 0
        self.snapshot = None
        self.current_indication = None
        self.public_market: dict[str, Any] | None = None
        self.queues = {origin: [] for origin in self.spec.origins}
        self.company_actions: dict[int, dict[str, Any]] = {}
        self.round_history: list[dict[str, Any]] = []
        self.turn_history: list[dict[str, Any]] = []
        self.stats = {
            cid: {
                "gross_service_value_real_usd": 0.0,
                "cargo_carried_bbl": 0,
                "full_fixtures": 0,
                "marginal_fixtures": 0,
                "ballast_orders": 0,
                "idle_prompt_ship_turns": 0,
            }
            for cid in range(COMPANY_COUNT)
        }
        self._lock = RLock()
        self.game_id = sha256_json({"version": VERSION, "seed": seed, "world": self.main_link["identity"]})[:16]
