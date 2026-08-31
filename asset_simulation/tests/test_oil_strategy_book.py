from __future__ import annotations

import unittest

from asset_simulation.model.oil_strategy_book import (
    OIL_STRATEGY_BOOK_MODEL_VERSION,
    aggregate_oil_strategy_books,
    build_oil_strategy_book,
    resolve_oil_strategy_book,
)
from asset_simulation.model.registry import load_registered_assets


class OilStrategyBookTests(unittest.TestCase):
    def test_registered_contract_and_book_identity_are_deterministic(self) -> None:
        assets = load_registered_assets()
        self.assertEqual(
            OIL_STRATEGY_BOOK_MODEL_VERSION,
            assets["oil_strategy_book_contract"]["model_version"],
        )
        first = build_oil_strategy_book(
            institution_id="PLAYER",
            strategy_id="oil.short.directional.v1",
            positions={"OIL-3005": 100, "OIL-3009": 0},
        )
        repeat = build_oil_strategy_book(
            institution_id="PLAYER",
            strategy_id="oil.short.directional.v1",
            positions={"OIL-3005": 100},
        )
        self.assertEqual(first, repeat)
        self.assertEqual({"OIL-3005": 100}, first["positions"])
        self.assertFalse(first["governance"]["synthetic_positions_allowed"])

    def test_synthetic_or_non_named_positions_are_rejected(self) -> None:
        for contract_id in ("OIL-SPREAD", "OIL-REF", "WTI-3005", "OIL-30055"):
            with self.subTest(contract_id=contract_id):
                with self.assertRaises(ValueError):
                    build_oil_strategy_book(
                        institution_id="PLAYER",
                        strategy_id="test",
                        positions={contract_id: 1},
                    )

    def test_strategy_ownership_survives_account_aggregation(self) -> None:
        directional = build_oil_strategy_book(
            institution_id="PLAYER",
            strategy_id="oil.short.directional.v1",
            positions={"OIL-3005": 100},
        )
        spread = build_oil_strategy_book(
            institution_id="PLAYER",
            strategy_id="oil.short.relative_value.calendar_spread.v1",
            positions={"OIL-3005": 50, "OIL-3009": -50},
        )
        aggregate = aggregate_oil_strategy_books([directional, spread])
        self.assertEqual(
            {"OIL-3005": 150, "OIL-3009": -50}, aggregate["account_positions"]
        )
        self.assertEqual(
            {"OIL-3005": 100},
            aggregate["book_contributions"][directional["book_id"]],
        )
        self.assertEqual(
            {"OIL-3005": 50, "OIL-3009": -50},
            aggregate["book_contributions"][spread["book_id"]],
        )
        self.assertTrue(aggregate["governance"]["strategy_ownership_preserved"])
        self.assertFalse(aggregate["governance"]["formal_account_mutated"])

    def test_opposite_strategy_positions_can_net_in_account_without_disappearing(self) -> None:
        long_book = build_oil_strategy_book(
            institution_id="PLAYER",
            strategy_id="long-sleeve",
            positions={"OIL-3005": 100},
        )
        short_book = build_oil_strategy_book(
            institution_id="PLAYER",
            strategy_id="short-sleeve",
            positions={"OIL-3005": -100},
        )
        aggregate = aggregate_oil_strategy_books([long_book, short_book])
        self.assertEqual({}, aggregate["account_positions"])
        self.assertEqual({"OIL-3005": 100}, long_book["positions"])
        self.assertEqual({"OIL-3005": -100}, short_book["positions"])

    def test_mutated_book_or_wrong_owner_is_rejected(self) -> None:
        book = build_oil_strategy_book(
            institution_id="PLAYER",
            strategy_id="spread",
            positions={"OIL-3005": 20, "OIL-3009": -20},
        )
        mutated = {**book, "positions": {"OIL-3005": 21, "OIL-3009": -20}}
        with self.assertRaisesRegex(ValueError, "modified"):
            resolve_oil_strategy_book(mutated)
        with self.assertRaisesRegex(ValueError, "different strategy"):
            resolve_oil_strategy_book(book, expected_strategy_id="directional")
        other = build_oil_strategy_book(
            institution_id="NORTHSTAR",
            strategy_id="other",
            positions={"OIL-3005": 1},
        )
        with self.assertRaisesRegex(ValueError, "across institutions"):
            aggregate_oil_strategy_books([book, other])


if __name__ == "__main__":
    unittest.main()
