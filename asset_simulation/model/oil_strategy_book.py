"""Strategy-owned oil-futures position books for multi-strategy composition.

A strategy book is deliberately smaller than a formal futures account.  It owns
only strategy attribution for real named-contract positions.  Cash, margin,
interest, financing, liquidation and bankruptcy remain formal-account concerns.

The key boundary is that a strategy consumes its own book, while portfolio and
account layers may aggregate many books.  This prevents one strategy from
mistaking another strategy's position for an imbalance that it should repair.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .registry import load_registered_assets, sha256_json


OIL_STRATEGY_BOOK_MODEL_VERSION = "asset-simulation-oil-strategy-book-v0.1.0"
OIL_STRATEGY_BOOK_CONTRACT_ID = "oil_strategy_book_v1"
_NAMED_OIL_CONTRACT = re.compile(r"^OIL-\d{4}$")


def _validate_registered_contract() -> tuple[dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    contract = assets["oil_strategy_book_contract"]
    if contract["contract_id"] != OIL_STRATEGY_BOOK_CONTRACT_ID:
        raise ValueError("registered oil strategy book contract id mismatch")
    if contract.get("model_version") != OIL_STRATEGY_BOOK_MODEL_VERSION:
        raise ValueError("registered oil strategy book model version mismatch")
    return assets, contract


def _canonical_positions(positions: Mapping[str, Any] | None) -> dict[str, int]:
    raw = dict(positions or {})
    canonical: dict[str, int] = {}
    for contract_id, value in raw.items():
        contract = str(contract_id)
        if not _NAMED_OIL_CONTRACT.fullmatch(contract):
            raise ValueError(
                "oil strategy books may contain only real named OIL-YYMM futures contracts"
            )
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("oil strategy book positions must be signed integer lots")
        lots = int(value)
        if lots:
            canonical[contract] = lots
    return dict(sorted(canonical.items()))


def build_oil_strategy_book(
    *,
    institution_id: str,
    strategy_id: str,
    positions: Mapping[str, Any] | None = None,
    book_id: str | None = None,
) -> dict[str, Any]:
    """Build one deterministic strategy-owned position book."""

    assets, contract = _validate_registered_contract()
    institution = str(institution_id).strip()
    strategy = str(strategy_id).strip()
    if not institution or not strategy:
        raise ValueError("oil strategy book institution and strategy ids must not be empty")
    resolved_book_id = str(book_id or f"{institution}:{strategy}").strip()
    if not resolved_book_id:
        raise ValueError("oil strategy book id must not be empty")
    canonical_positions = _canonical_positions(positions)
    result = {
        "schemaVersion": "asset-simulation-oil-strategy-book-v1",
        "owner": "strategy_book",
        "institution_id": institution,
        "strategy_id": strategy,
        "book_id": resolved_book_id,
        "positions": canonical_positions,
        "gross_position_lots": sum(abs(value) for value in canonical_positions.values()),
        "net_position_lots": sum(canonical_positions.values()),
        "contract_count": len(canonical_positions),
        "governance": {
            "cash_owner": "formal_account",
            "margin_owner": "formal_account",
            "pnl_owner": "formal_account_with_strategy_attribution",
            "market_write_back": False,
            "synthetic_positions_allowed": False,
        },
    }
    positions_hash = sha256_json(canonical_positions)
    identity = {
        "model_version": OIL_STRATEGY_BOOK_MODEL_VERSION,
        "field_contract_id": str(contract["contract_id"]),
        "field_contract_hash": assets["oil_strategy_book_contract_hash"],
        "institution_id": institution,
        "strategy_id": strategy,
        "book_id": resolved_book_id,
        "positions_hash": positions_hash,
        "write_back": False,
        "result_hash": sha256_json(result),
    }
    identity["identity_hash"] = sha256_json(identity)
    return {"identity": identity, **result}


def resolve_oil_strategy_book(
    book: Mapping[str, Any],
    *,
    expected_institution_id: str | None = None,
    expected_strategy_id: str | None = None,
) -> dict[str, Any]:
    """Canonicalize a supplied book and reject mutated deterministic records."""

    supplied = dict(book)
    rebuilt = build_oil_strategy_book(
        institution_id=str(supplied.get("institution_id", "")),
        strategy_id=str(supplied.get("strategy_id", "")),
        positions=supplied.get("positions", {}),
        book_id=str(supplied.get("book_id", "")) or None,
    )
    supplied_identity = supplied.get("identity")
    if supplied_identity is not None:
        supplied_hash = str(dict(supplied_identity).get("identity_hash", ""))
        if not supplied_hash or supplied_hash != rebuilt["identity"]["identity_hash"]:
            raise ValueError("oil strategy book was modified after construction")
    if (
        expected_institution_id is not None
        and rebuilt["institution_id"] != str(expected_institution_id)
    ):
        raise ValueError("oil strategy book belongs to a different institution")
    if expected_strategy_id is not None and rebuilt["strategy_id"] != str(
        expected_strategy_id
    ):
        raise ValueError("oil strategy book belongs to a different strategy")
    return rebuilt


def aggregate_oil_strategy_books(
    books: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """Aggregate named-contract positions without erasing strategy ownership."""

    if not books:
        raise ValueError("oil strategy book aggregation requires at least one book")
    resolved = [resolve_oil_strategy_book(book) for book in books]
    institution_ids = {str(book["institution_id"]) for book in resolved}
    if len(institution_ids) != 1:
        raise ValueError("cannot aggregate oil strategy books across institutions")
    book_ids = [str(book["book_id"]) for book in resolved]
    if len(book_ids) != len(set(book_ids)):
        raise ValueError("oil strategy book ids must be unique within an aggregation")
    strategy_ids = [str(book["strategy_id"]) for book in resolved]
    if len(strategy_ids) != len(set(strategy_ids)):
        raise ValueError("oil strategy ids must be unique within a v1 aggregation")

    account_positions: dict[str, int] = {}
    contributions: dict[str, dict[str, int]] = {}
    for book in resolved:
        book_id = str(book["book_id"])
        contributions[book_id] = dict(book["positions"])
        for contract_id, lots in book["positions"].items():
            account_positions[contract_id] = account_positions.get(contract_id, 0) + int(lots)
    account_positions = {
        key: value for key, value in sorted(account_positions.items()) if value
    }
    result = {
        "schemaVersion": "asset-simulation-oil-strategy-book-aggregation-v1",
        "institution_id": next(iter(institution_ids)),
        "strategy_count": len(resolved),
        "book_ids": book_ids,
        "strategy_ids": strategy_ids,
        "book_contributions": contributions,
        "account_positions": account_positions,
        "gross_account_position_lots": sum(abs(value) for value in account_positions.values()),
        "net_account_position_lots": sum(account_positions.values()),
        "governance": {
            "strategy_ownership_preserved": True,
            "aggregation_is_not_execution_netting": True,
            "formal_account_mutated": False,
            "market_write_back": False,
        },
    }
    result["aggregation_hash"] = sha256_json(result)
    return result
