from __future__ import annotations

import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_futures_overlay import (
    _rebuild_oil_futures_payload,
    oil_futures_payload,
)
from asset_simulation.model.oil_futures_world import get_oil_futures_world
from asset_simulation.model.oil_short_term_forecast import (
    generate_institution_profile_for_score_range,
    generate_oil_short_term_forecast,
)
from asset_simulation.model.oil_short_term_forecast_session import (
    OilShortTermForecastSession,
)
from asset_simulation.model.oil_investment_competition import (
    OilInvestmentCompetitionSession,
)
from asset_simulation.server import (
    build_oil_short_term_forecast_payload,
    clear_cache,
)


class P0BIncrementalWorldTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def test_incremental_world_matches_legacy_rebuild_at_key_cutoffs(self) -> None:
        run = run_global_macro(42, 12)
        for year, month, half in (
            (2030, 1, 1),
            (2030, 1, 2),
            (2030, 5, 1),
            (2030, 5, 2),
            (2031, 12, 2),
        ):
            with self.subTest(cutoff=(year, month, half)):
                expected = _rebuild_oil_futures_payload(
                    run,
                    as_of_year=year,
                    as_of_month=month,
                    as_of_half=half,
                )
                actual = oil_futures_payload(
                    run,
                    as_of_year=year,
                    as_of_month=month,
                    as_of_half=half,
                )
                self.assertEqual(expected, actual)
                self.assertEqual(
                    expected["identity"]["result_hash"],
                    actual["identity"]["result_hash"],
                )

    def test_future_history_build_does_not_change_earlier_asof_view(self) -> None:
        run = run_global_macro(42, 12)
        world = get_oil_futures_world(run)
        early = oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        world.ensure(year=2035, month=12, half=2)
        rebuilt_early = world.payload(
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        self.assertEqual(early, rebuilt_early)

    def test_contract_history_query_reuses_world_without_market_payload(self) -> None:
        run = run_global_macro(42, 12)
        market = oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        contract = next(
            item
            for item in market["curve"]["contracts"]
            if item["contract_id"] == "OIL-3005"
        )
        world = get_oil_futures_world(run)
        monthly = world.contract_monthly_history(
            contract_id="OIL-3005",
            as_of_year=2030,
            as_of_month=5,
            as_of_half=2,
        )
        legacy = _rebuild_oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=5,
            as_of_half=2,
        )
        legacy_contract = next(
            item
            for item in legacy["curve"]["contracts"]
            if item["contract_id"] == "OIL-3005"
        )
        self.assertEqual(legacy_contract["monthly"], monthly)
        self.assertGreaterEqual(world.stats()["processed_half_turns"], 1)

    def test_forecast_session_matches_manual_continuous_vintage_chain(self) -> None:
        run = run_global_macro(42, 12)
        profile = generate_institution_profile_for_score_range(
            seed=42,
            score_min=65,
            score_max=75,
        )
        first = generate_oil_short_term_forecast(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
            institution_profile=profile,
        )
        second = generate_oil_short_term_forecast(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=2,
            institution_profile=profile,
            previous_vintage=first,
        )
        third = generate_oil_short_term_forecast(
            run,
            as_of_year=2030,
            as_of_month=2,
            as_of_half=1,
            institution_profile=profile,
            previous_vintage=second,
        )

        session = OilShortTermForecastSession(run, profile)
        direct = session.payload(
            as_of_year=2030,
            as_of_month=2,
            as_of_half=1,
        )
        self.assertEqual(third, direct)
        self.assertEqual(
            second["identity"]["vintage_id"],
            direct["identity"]["previous_vintage_id"],
        )

    def test_server_forecast_builder_is_continuous_beyond_one_revision(self) -> None:
        run = run_global_macro(42, 12)
        profile = generate_institution_profile_for_score_range(
            seed=42,
            score_min=65,
            score_max=75,
        )
        direct = build_oil_short_term_forecast_payload(
            run,
            as_of_year=2030,
            as_of_month=2,
            as_of_half=1,
            institution_profile=profile,
        )
        self.assertEqual(
            "generated_research_42_650_750:2030-01-H2",
            direct["identity"]["previous_vintage_id"],
        )

    def test_compact_report_history_keeps_full_catalog_and_lazy_lookup(self) -> None:
        run = run_global_macro(42, 6)
        session = OilInvestmentCompetitionSession(run)
        compact = session.payload(
            as_of_year=2030,
            as_of_month=3,
            as_of_half=2,
            history_limit=1,
        )
        self.assertEqual(5, compact["completed_turns"])
        self.assertEqual(1, len(compact["report_history"]))
        self.assertEqual(5, len(compact["report_catalog"]))
        oldest_id = compact["report_catalog"][-1]["report_id"]
        fetched = session.report_payload(oldest_id)
        self.assertTrue(fetched["ok"])
        self.assertEqual(oldest_id, fetched["report"]["report_id"])

    def test_full_competition_payload_remains_backward_compatible(self) -> None:
        run = run_global_macro(42, 6)
        session = OilInvestmentCompetitionSession(run)
        full = session.payload(
            as_of_year=2030,
            as_of_month=2,
            as_of_half=1,
        )
        self.assertEqual(full["completed_turns"], len(full["report_history"]))
        self.assertNotIn("report_catalog", full)


if __name__ == "__main__":
    unittest.main()
