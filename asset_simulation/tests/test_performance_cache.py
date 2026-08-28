from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep
import unittest

from asset_simulation.model.commodity_overlay import run_commodity_overlay
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_investment_competition import (
    OilInvestmentCompetitionSession,
)
from asset_simulation.model.performance_cache import deterministic_projection_cache
from asset_simulation.model.registry import (
    clear_registered_assets_cache,
    load_registered_assets,
    sha256_json,
)
from asset_simulation.server import clear_cache
from asset_simulation.tests.support import cached_global_run


class PerformanceCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def test_registered_assets_are_loaded_once_until_explicit_clear(self) -> None:
        first = load_registered_assets()
        second = load_registered_assets()
        self.assertIs(first, second)
        info = load_registered_assets.cache_info()
        self.assertGreaterEqual(info.hits, 1)
        self.assertEqual(1, info.currsize)

        clear_registered_assets_cache()
        third = load_registered_assets()
        self.assertIsNot(first, third)
        self.assertEqual(first, third)

    def test_commodity_projection_reuses_identical_world(self) -> None:
        run = cached_global_run(42, 6)
        first = run_commodity_overlay(run)
        second = run_commodity_overlay(run)
        self.assertIs(first, second)
        info = run_commodity_overlay.cache_info()
        self.assertEqual(1, info["misses"])
        self.assertGreaterEqual(info["hits"], 1)

    def test_oil_futures_projection_reuses_identical_cutoff(self) -> None:
        run = cached_global_run(42, 6)
        first = oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        second = oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        later = oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=2,
        )
        self.assertIs(first, second)
        self.assertIsNot(first, later)
        info = oil_futures_payload.cache_info()
        self.assertEqual(2, info["misses"])
        self.assertGreaterEqual(info["hits"], 1)

    def test_service_clear_cache_clears_downstream_projection_caches(self) -> None:
        run = cached_global_run(42, 6)
        run_commodity_overlay(run)
        oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        self.assertGreater(
            run_commodity_overlay.cache_info()["currentEntries"],
            0,
        )
        self.assertGreater(
            oil_futures_payload.cache_info()["currentEntries"],
            0,
        )

        clear_cache()

        self.assertEqual(
            0,
            run_commodity_overlay.cache_info()["currentEntries"],
        )
        self.assertEqual(
            0,
            oil_futures_payload.cache_info()["currentEntries"],
        )

    def test_cache_clear_does_not_change_deterministic_market_result(self) -> None:
        run = cached_global_run(42, 6)
        first = oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=2,
            as_of_half=1,
        )
        clear_cache()
        rebuilt = oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=2,
            as_of_half=1,
        )
        self.assertIsNot(first, rebuilt)
        self.assertEqual(first, rebuilt)

    def test_competition_pipeline_does_not_mutate_cached_inputs(self) -> None:
        run = cached_global_run(42, 60)
        assets = load_registered_assets()
        market = oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        assets_hash = sha256_json(assets)
        market_hash = sha256_json(market)

        session = OilInvestmentCompetitionSession(run)
        session.payload(as_of_year=2030, as_of_month=3, as_of_half=2)
        repeated_market = oil_futures_payload(
            run,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )

        self.assertIs(market, repeated_market)
        self.assertEqual(market_hash, sha256_json(repeated_market))
        self.assertEqual(assets_hash, sha256_json(load_registered_assets()))

    def test_duplicate_concurrent_misses_are_computed_once(self) -> None:
        class FakeRun:
            identity = {"identity_hash": "concurrent-test-world"}

        call_count = 0
        count_lock = Lock()

        @deterministic_projection_cache(max_entries=2)
        def projection(global_run: FakeRun, cutoff: int) -> dict[str, int]:
            nonlocal call_count
            with count_lock:
                call_count += 1
            sleep(0.02)
            return {"cutoff": cutoff}

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(lambda _: projection(FakeRun(), 1), range(8))
            )

        self.assertEqual(1, call_count)
        self.assertEqual(1, len({id(result) for result in results}))
        self.assertEqual(7, projection.cache_info()["hits"])

    def test_projection_cache_evicts_least_recently_used_entry(self) -> None:
        class FakeRun:
            identity = {"identity_hash": "eviction-test-world"}

        calls: list[int] = []

        @deterministic_projection_cache(max_entries=2)
        def projection(global_run: FakeRun, cutoff: int) -> int:
            calls.append(cutoff)
            return cutoff

        run = FakeRun()
        projection(run, 1)
        projection(run, 2)
        projection(run, 1)
        projection(run, 3)
        projection(run, 2)

        self.assertEqual([1, 2, 3, 2], calls)
        self.assertEqual(1, projection.cache_info()["hits"])
        self.assertEqual(4, projection.cache_info()["misses"])
        self.assertEqual(2, projection.cache_info()["currentEntries"])


if __name__ == "__main__":
    unittest.main()
