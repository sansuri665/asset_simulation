"""Constants and deterministic world helpers for Main-6C Lite."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..registry import sha256_json
from ..shipping_v3.types import Vessel
from .main_bridge import build_main_linked_preview3_inputs
from .types import ImportRequirement


VERSION = "main-6clite-fixed-fleet-game-v0.1.0"
START_YEAR = 2030
GAME_YEARS = 10
TURNS_PER_YEAR = 36
TOTAL_TURNS = GAME_YEARS * TURNS_PER_YEAR
COMPANY_COUNT = 10
FLEET_COUNTS = {"vlcc": 280, "suezmax": 160, "aframax": 40}
ROUND_NAMES = {1: "View", 2: "React", 3: "Commit"}

COMPANY_NAMES = (
    "Northstar Tankers", "Blue Meridian", "Orion Maritime", "Harborline Shipping",
    "Atlas Crude", "Eastern Cape", "Longwake Tankers", "Pelagic Lines",
    "Straitline Maritime", "Crown Seas",
)

# Strategy weights are intentionally simple and public. They change how the same
# information is interpreted; they never change information access.
POLICIES = (
    {"label": "追价型", "future_weight": 0.35, "stickiness": 0.20, "withdraw_unused": 0.25, "bias": 0.08},
    {"label": "前瞻型", "future_weight": 1.10, "stickiness": 0.30, "withdraw_unused": 0.45, "bias": -0.02},
    {"label": "惰性型", "future_weight": 0.55, "stickiness": 0.80, "withdraw_unused": 0.20, "bias": 0.00},
    {"label": "逆向型", "future_weight": 1.35, "stickiness": 0.35, "withdraw_unused": 0.55, "bias": -0.08},
    {"label": "队列型", "future_weight": 0.45, "stickiness": 0.70, "withdraw_unused": 0.70, "bias": 0.03},
    {"label": "均衡型", "future_weight": 0.75, "stickiness": 0.50, "withdraw_unused": 0.40, "bias": 0.00},
    {"label": "追价型-B", "future_weight": 0.45, "stickiness": 0.25, "withdraw_unused": 0.35, "bias": -0.05},
    {"label": "前瞻型-B", "future_weight": 0.95, "stickiness": 0.45, "withdraw_unused": 0.50, "bias": 0.06},
    {"label": "惰性型-B", "future_weight": 0.65, "stickiness": 0.90, "withdraw_unused": 0.15, "bias": 0.02},
    {"label": "逆向型-B", "future_weight": 1.20, "stickiness": 0.40, "withdraw_unused": 0.60, "bias": -0.04},
)
ADVISOR_POLICY = {"label": "AI参谋", "future_weight": 0.85, "stickiness": 0.55,
                  "withdraw_unused": 0.45, "bias": 0.00}


_INPUT_CACHE: dict[tuple[int, str], tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...], dict[str, Any]]] = {}


def _hash_rank(*parts: Any) -> int:
    return int(sha256_json(list(parts))[:16], 16)


def _rebase_inputs(seed: int, spec) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...], dict[str, Any]]:
    """Read Main and build a six-month 2029 warm start plus 2030-2039 game."""
    cache_key = (seed, spec.identity)
    if cache_key in _INPUT_CACHE:
        return _INPUT_CACHE[cache_key]
    raw, source = build_main_linked_preview3_inputs(spec, seed=seed, years=14)
    warm = [row for row in raw if row["year"] == START_YEAR - 1 and row["month"] >= 7]
    selected = [row for row in raw if START_YEAR <= row["year"] < START_YEAR + GAME_YEARS]
    if len(warm) != 18 or len(selected) != TOTAL_TURNS:
        raise ValueError("Main-linked world did not provide the required warm start and ten game years")
    base_turn = warm[0]["turn"]

    def convert(rows, *, game_rows: bool):
        out = []
        for index, row in enumerate(rows):
            requirements = tuple(
                ImportRequirement(
                    requirement.requirement_id,
                    requirement.due_turn - base_turn,
                    requirement.volume_bbl,
                )
                for requirement in row["new_requirements"]
            )
            out.append({
                **row,
                "turn": index if game_rows else row["turn"] - base_turn,
                "engine_turn": row["turn"] - base_turn,
                "source_turn": row["turn"],
                "new_requirements": requirements,
            })
        return tuple(out)

    warm_rows = convert(warm, game_rows=False)
    game_rows = convert(selected, game_rows=True)
    identity_rows = [
        {**row, "new_requirements": [asdict(r) for r in row["new_requirements"]]}
        for row in (*warm_rows, *game_rows)
    ]
    link = {
        "seed": seed,
        "warm_start": "2029-07_to_2029-12_neutral_six_month_market",
        "warm_start_turns": len(warm_rows),
        "start_year": START_YEAR,
        "end_year": START_YEAR + GAME_YEARS - 1,
        "turn_count": len(game_rows),
        "preview3_main_link_hash": source["adapter_hash"],
        "main_source_unchanged": source["main_source_unchanged"],
        "records_hash": sha256_json(identity_rows),
        "future_records_exposed_to_agents": False,
    }
    link["identity"] = sha256_json(link)
    result = (warm_rows, game_rows, link)
    _INPUT_CACHE[cache_key] = result
    return result

def _ship_status(ship: Vessel, turn: int, destination: str) -> str:
    if ship.movement is not None:
        return "laden" if ship.movement.kind == "laden" else "ballast"
    if ship.location == destination:
        return "east_asia_idle"
    return f"prompt:{ship.location}"

