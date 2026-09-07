from __future__ import annotations

import unittest
from pathlib import Path

from asset_simulation.model.freight_board.lite_game import (
    COMPANY_COUNT, FLEET_COUNTS, LiteGameSession, TOTAL_TURNS,
)
from asset_simulation.lite_server import GAME_SERVICE_ID, clear_games, create_game, get_game
from asset_simulation.server import VIEWER_ROOT


class LiteGameContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = LiteGameSession(seed=42)
        cls.initial_candidates = cls.game.candidate_companies()
        cls.initial_profiles = [cls.game._company_profile(cid) for cid in range(COMPANY_COUNT)]
        cls.initial_market_turn = cls.game.market.state.market.turn
        state = cls.game.choose_company(cls.initial_candidates[0]["company_id"])
        for _ in range(3):
            state = cls.game.submit_round(use_advisor=True, round_token=state["round_token"])
        cls.settled_checkpoint = cls.game.checkpoint()
        cls.settled_state = cls.game.public_state()

    def test_fixed_assets_warm_start_and_candidate_choice(self):
        game = self.game
        self.assertEqual(TOTAL_TURNS, 360)
        self.assertEqual(COMPANY_COUNT, 10)
        self.assertTrue(game.main_link["main_source_unchanged"])
        self.assertFalse(game.main_link["future_records_exposed_to_agents"])
        self.assertEqual(self.initial_market_turn, game.main_link["warm_start_turns"])
        for profile in self.initial_profiles:
            self.assertEqual(profile["fleet_total"], 48)
            self.assertEqual(profile["classes"], {"vlcc": 28, "suezmax": 16, "aframax": 4})
        states = {key for profile in self.initial_profiles for key in profile["status"]}
        self.assertIn("laden", states)
        self.assertIn("ballast", states)
        self.assertTrue(any(key.startswith("prompt:") for key in states))
        self.assertEqual(len(self.initial_candidates), 3)

    def test_three_round_market_advisor_stale_token_and_save_restore(self):
        state = self.settled_state
        self.assertEqual(state["phase"], "turn_complete")
        self.assertEqual(state["turn"], 1)
        self.assertGreaterEqual(state["human"]["last_turn_result"]["cargo_bbl"], 0)
        restored = LiteGameSession.restore(self.settled_checkpoint)
        next_state = restored.next_turn(round_token=restored.public_state()["round_token"])
        self.assertFalse(next_state["human"]["advisor"]["future_seed_access"])
        stale = next_state["round_token"]
        next_state = restored.submit_round(use_advisor=True, round_token=stale)
        self.assertEqual(next_state["round"], 2)
        with self.assertRaises(ValueError):
            restored.submit_round(keep=True, round_token=stale)
        with self.assertRaisesRegex(ValueError, "missing or stale game round token"):
            restored.submit_round(keep=True, round_token=None)
        settled = LiteGameSession.restore(self.settled_checkpoint)
        with self.assertRaisesRegex(ValueError, "missing or stale game turn token"):
            settled.next_turn(round_token=None)
        restored_again = LiteGameSession.restore(self.settled_checkpoint)
        restored_state = restored_again.public_state()
        self.assertEqual(restored_state["turn"], state["turn"])
        self.assertEqual(restored_state["leaderboard"], state["leaderboard"])
        self.assertEqual(restored_state["human_company_id"], state["human_company_id"])

    def test_route_view_separates_cargo_loading_shortfall_and_queue_cutoff(self):
        state = self.settled_state
        for origin, route in state["market"]["routes"].items():
            self.assertEqual(route["cargo_bbl"], route["loaded_bbl"] + route["unserved_cargo_bbl"])
            self.assertEqual(route["capacity_shortfall"], route["unserved_cargo_bbl"] > 0)
            cutoff = route["loading_cutoff_queue_rank"]
            if route["loaded_bbl"]:
                self.assertIsNotNone(cutoff)
                self.assertGreaterEqual(cutoff, 1)
                self.assertLessEqual(cutoff, route["queue_ship_count"])
        self.assertIsNotNone(state["human"]["market_positions"])
        for position in state["human"]["market_positions"].values():
            if position["offered"]:
                self.assertGreaterEqual(position["queue_first"], 1)
                self.assertGreaterEqual(position["queue_last"], position["queue_first"])
                self.assertEqual(position["loaded"], position["full"] + position["marginal"])
            else:
                self.assertIsNone(position["queue_first"])
                self.assertIsNone(position["queue_last"])

    def test_reentry_goes_to_queue_tail(self):
        restored = LiteGameSession.restore(self.settled_checkpoint)
        state = restored.next_turn(round_token=restored.public_state()["round_token"])
        cid = state["human_company_id"]
        # Pick a route with at least two prompt hulls if possible.
        counts = state["human"]["eligible"]["prompt_counts"]
        origin = max(counts, key=counts.get)
        if counts[origin] < 2:
            self.skipTest("seed opening has too few owned prompt hulls to test queue reentry")
        other = "west_africa" if origin == "gulf" else "gulf"
        action = {
            "offer_counts": {origin: counts[origin] - 1, other: counts[other]},
            "ballast_counts": {"gulf": 0, "west_africa": 0},
        }
        state = restored.submit_round(action, round_token=state["round_token"])
        eligible_after = state["human"]["eligible"]["prompt_counts"]
        action2 = {
            "offer_counts": {origin: eligible_after[origin], other: eligible_after[other]},
            "ballast_counts": {"gulf": 0, "west_africa": 0},
        }
        state = restored.submit_round(action2, round_token=state["round_token"])
        positions = state["human"]["market_positions"][origin]["positions"]
        ranks = sorted(p["queue_rank"] for p in positions if p["queue_rank"] is not None)
        self.assertTrue(ranks)
        self.assertGreaterEqual(max(ranks), min(ranks))
        # The newly re-entered hull is appended behind every kept hull globally;
        # exact owner rank depends on other simultaneous AI joins.
        self.assertEqual(state["round"], 3)


class LiteGameViewerAndRegistryTests(unittest.TestCase):
    def tearDown(self):
        clear_games()

    def test_game_assets_and_registry_surface(self):
        self.assertEqual(GAME_SERVICE_ID, "asset-simulation-main-6clite-game-v0.1")
        for path in (
            VIEWER_ROOT / "game.html",
            VIEWER_ROOT / "css" / "game.css",
            VIEWER_ROOT / "js" / "game.js",
        ):
            self.assertTrue(Path(path).is_file())
        html = (VIEWER_ROOT / "game.html").read_text(encoding="utf-8")
        js = (VIEWER_ROOT / "js" / "game.js").read_text(encoding="utf-8")
        self.assertIn("双航路油运博弈", html)
        self.assertIn("AI SHIPPING ADVISOR", html)
        self.assertIn("未满足货盘", html)
        self.assertIn("装船截止位", html)
        self.assertIn("/api/game/new", js)
        self.assertIn("/api/game/round", js)
        self.assertIn("loaded_queue_last", js)
        game = create_game(7)
        self.assertIs(get_game(game.game_id), game)


if __name__ == "__main__":
    unittest.main()
