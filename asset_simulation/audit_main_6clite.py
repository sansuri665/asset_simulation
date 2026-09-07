"""Small playable-contract audit for Main-6C lite."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .model.freight_board.lite_game import COMPANY_COUNT, LiteGameSession, TOTAL_TURNS


def run(seed: int, turns: int) -> dict:
    game = LiteGameSession(seed=seed)
    candidates = game.candidate_companies()
    state = game.choose_company(candidates[0]["company_id"])
    completed = 0
    for turn in range(turns):
        for _ in range(3):
            state = game.submit_round(use_advisor=True, round_token=state["round_token"])
        completed += 1
        if turn + 1 < turns:
            state = game.next_turn(round_token=state["round_token"])
    checkpoint = game.checkpoint()
    restored = LiteGameSession.restore(checkpoint)
    profiles = [game._company_profile(cid) for cid in range(COMPANY_COUNT)]
    gates = {
        "ten_year_360_turn_campaign": TOTAL_TURNS == 360,
        "three_human_market_rounds_commit": completed == turns and game.turn_index == turns,
        "same_seed_world_is_hidden_from_agents": not game.main_link["future_records_exposed_to_agents"],
        "main_world_read_only": game.main_link["main_source_unchanged"],
        "fixed_equal_assets": all(p["fleet_total"] == 48 and p["classes"] == {"vlcc": 28, "suezmax": 16, "aframax": 4} for p in profiles),
        "warm_start_is_mixed": any("laden" in p["status"] for p in profiles) and any(any(k.startswith("prompt:") for k in p["status"]) for p in profiles),
        "advisor_has_no_future_seed_access": state.get("human", {}).get("advisor") is None or state["human"]["advisor"]["future_seed_access"] is False,
        "save_restore_preserves_leaderboard": restored.public_state()["leaderboard"] == game.public_state()["leaderboard"],
    }
    return {
        "model": "main-6clite-fixed-fleet-game-v0.1.0",
        "seed": seed,
        "turns_audited": turns,
        "candidate_companies": candidates,
        "leaderboard": game.public_state()["leaderboard"],
        "gates": gates,
        "all_gates_pass": all(gates.values()),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--turns", type=int, default=2)
    p.add_argument("--output", type=Path, default=Path("main-6clite-audit.json"))
    a = p.parse_args()
    if not 1 <= a.turns <= 12:
        p.error("audit turns must be between 1 and 12")
    result = run(a.seed, a.turns)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"gates": result["gates"], "all_gates_pass": result["all_gates_pass"]}))
    if not result["all_gates_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
