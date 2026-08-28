from __future__ import annotations

import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_investment_competition import (
    OilInvestmentCompetitionSession,
    build_competition_participants,
)


class OilInvestmentCompetitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.macro_run = run_global_macro(42, 6)

    def test_seed_draws_one_complete_committee_per_participant(self) -> None:
        participants = build_competition_participants(42)
        self.assertEqual(participants, build_competition_participants(42))
        self.assertEqual(4, len(participants))
        self.assertEqual(1, sum(item["is_player"] for item in participants))
        self.assertEqual(
            {"forecast", "strategy", "risk", "execution"},
            set(participants[0]["appointments"]),
        )
        self.assertEqual(4, len({item["roster_hash"] for item in participants}))
        self.assertNotEqual(
            [item["roster_hash"] for item in participants],
            [item["roster_hash"] for item in build_competition_participants(99)],
        )

    def test_all_institutions_settle_against_the_same_turn(self) -> None:
        session = OilInvestmentCompetitionSession(self.macro_run)
        opening = session.payload(as_of_year=2030, as_of_month=1, as_of_half=1)
        self.assertEqual(0, opening["completed_turns"])
        self.assertIsNone(opening["latest_report"])

        settled = session.payload(as_of_year=2030, as_of_month=1, as_of_half=2)
        self.assertEqual(1, settled["completed_turns"])
        report = settled["latest_report"]
        self.assertEqual(4, len(report["participants"]))
        self.assertEqual({1, 2, 3, 4}, {item["rank"] for item in report["participants"]})
        self.assertTrue(all(item["traded_lots"] >= 0 for item in report["participants"]))
        self.assertTrue(all("execution_cost_usd" in item for item in report["participants"]))
        self.assertTrue(
            all("account_ledger" in item for item in report["participants"])
        )
        self.assertTrue(
            all(
                item["cash_balance_usd"]
                >= item["maintenance_margin_usd"] - 0.01
                for item in report["participants"]
                if item["account_status"] != "insolvent"
            )
        )
        self.assertTrue(
            all(
                abs(item["account_ledger"]["cash_identity_error_usd"]) <= 0.01
                for item in report["participants"]
            )
        )
        self.assertTrue(settled["governance"]["same_market_path"])
        self.assertTrue(settled["governance"]["player_and_ai_use_same_runtime"])
        self.assertTrue(settled["governance"]["formal_futures_accounts"])
        self.assertFalse(settled["governance"]["market_write_back"])

    def test_forward_replay_builds_history_and_reset_can_rewind(self) -> None:
        session = OilInvestmentCompetitionSession(self.macro_run)
        advanced = session.payload(as_of_year=2030, as_of_month=2, as_of_half=1)
        self.assertEqual(2, advanced["completed_turns"])
        self.assertEqual(2, len(advanced["report_history"]))
        self.assertEqual([1, 2, 3, 4], [item["rank"] for item in advanced["leaderboard"]])

        reset = session.payload(as_of_year=2030, as_of_month=1, as_of_half=1)
        self.assertEqual(0, reset["completed_turns"])
        self.assertEqual([], reset["report_history"])


if __name__ == "__main__":
    unittest.main()
