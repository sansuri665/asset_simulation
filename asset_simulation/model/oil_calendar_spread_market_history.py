"""Canonicalize visible named-contract week coordinates for calendar-spread research.

The oil-futures market owner publishes named-contract history as:

    curve.contracts[].monthly[{year, month, weekly:[{week, OHLC, ...}]}]

The calendar-spread v0.1.2 reader expected each weekly child to repeat year/month
(or to carry a week_serial), so real market payloads lost every historical week.
This adapter fixes only that schema-boundary mismatch by inheriting the parent
month coordinates into weekly children. Prices, liquidity, market identity and
all other values are preserved exactly.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .registry import sha256_json


OIL_CALENDAR_SPREAD_MARKET_HISTORY_ADAPTER_VERSION = (
    "asset-simulation-oil-calendar-spread-market-history-adapter-v0.1.0"
)


def _int_coordinate(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return int(value)


def normalize_oil_calendar_spread_market_history(
    market: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a deep-copied market with parent year/month inherited by weeks.

    Existing explicit weekly coordinates are validated, never overwritten when
    contradictory. The transformation is metadata-only and does not write back
    to the market owner.
    """

    original = deepcopy(dict(market))
    normalized = deepcopy(original)
    curve = normalized.get("curve")
    if not isinstance(curve, dict):
        raise ValueError("calendar-spread history adapter requires market.curve")
    contracts = curve.get("contracts")
    if not isinstance(contracts, list):
        raise ValueError("calendar-spread history adapter requires curve contracts")

    inherited_year_count = 0
    inherited_month_count = 0
    explicit_week_serial_count = 0
    week_count = 0
    month_count = 0
    contract_count = 0
    for contract in contracts:
        if not isinstance(contract, dict):
            raise ValueError("calendar-spread curve contract must be an object")
        contract_count += 1
        monthly = contract.get("monthly", [])
        if not isinstance(monthly, list):
            raise ValueError("calendar-spread contract monthly history must be a list")
        for month in monthly:
            if not isinstance(month, dict):
                raise ValueError("calendar-spread monthly history must be an object")
            parent_year = _int_coordinate(month.get("year"), "parent month year")
            parent_month = _int_coordinate(month.get("month"), "parent month number")
            if not 1 <= parent_month <= 12:
                raise ValueError("parent month number must be between 1 and 12")
            month_count += 1
            weekly = month.get("weekly", [])
            if not isinstance(weekly, list):
                raise ValueError("calendar-spread monthly weekly history must be a list")
            for week in weekly:
                if not isinstance(week, dict):
                    raise ValueError("calendar-spread weekly history must be an object")
                week_number = _int_coordinate(week.get("week"), "weekly week number")
                if not 1 <= week_number <= 5:
                    raise ValueError("weekly week number must be between 1 and 5")
                week_count += 1
                if "year" in week:
                    explicit_year = _int_coordinate(week["year"], "weekly explicit year")
                    if explicit_year != parent_year:
                        raise ValueError("weekly year conflicts with parent month year")
                else:
                    week["year"] = parent_year
                    inherited_year_count += 1
                if "month" in week:
                    explicit_month = _int_coordinate(
                        week["month"], "weekly explicit month"
                    )
                    if explicit_month != parent_month:
                        raise ValueError("weekly month conflicts with parent month number")
                else:
                    week["month"] = parent_month
                    inherited_month_count += 1
                if "week_serial" in week:
                    explicit_week_serial_count += 1

    original_identity = dict(original.get("identity", {}))
    normalized_identity = dict(normalized.get("identity", {}))
    if original_identity != normalized_identity:
        raise ValueError("calendar-spread history adapter changed market identity")
    if original.get("asOf") != normalized.get("asOf"):
        raise ValueError("calendar-spread history adapter changed market cutoff")

    report = {
        "schemaVersion": "asset-simulation-oil-calendar-spread-market-history-adapter-report-v1",
        "model_version": OIL_CALENDAR_SPREAD_MARKET_HISTORY_ADAPTER_VERSION,
        "contract_count": contract_count,
        "month_count": month_count,
        "weekly_child_count": week_count,
        "weekly_year_coordinates_inherited": inherited_year_count,
        "weekly_month_coordinates_inherited": inherited_month_count,
        "weekly_children_with_explicit_week_serial": explicit_week_serial_count,
        "market_identity_preserved": True,
        "market_cutoff_preserved": True,
        "prices_modified": False,
        "liquidity_modified": False,
        "market_write_back": False,
        "original_payload_hash": sha256_json(original),
        "normalized_payload_hash": sha256_json(normalized),
    }
    report["result_hash"] = sha256_json(report)
    return normalized, report
