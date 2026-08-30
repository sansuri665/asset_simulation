"""Calendar-spread v0.2.2 candidate with canonical visible-history coordinates.

v0.2.1 established strategy-book ownership and a dedicated PM style layer, but
its hardened v0.1.2 reference primitives inherited a schema mismatch: the market
owner publishes year/month on parent monthly nodes while the spread history
reader expects those coordinates on weekly children.  As a result the visible
curve component had no real weekly history.

v0.2.2 preserves v0.2.1 decision semantics and inserts one metadata-only adapter
at the strategy boundary.  The adapter deep-copies the market payload, inherits
parent year/month into weekly children, and never changes prices, liquidity,
market identity or write-back state.
"""

from __future__ import annotations

from typing import Any, Mapping

from .oil_calendar_spread_market_history import (
    OIL_CALENDAR_SPREAD_MARKET_HISTORY_ADAPTER_VERSION,
    normalize_oil_calendar_spread_market_history,
)
from .oil_calendar_spread_strategy_v2 import (
    OIL_CALENDAR_SPREAD_STRATEGY_V2_ID,
    build_oil_calendar_spread_strategy_v2_decision,
)
from .registry import sha256_json


OIL_CALENDAR_SPREAD_STRATEGY_V22_MODEL_VERSION = (
    "asset-simulation-oil-calendar-spread-strategy-v0.2.2"
)
OIL_CALENDAR_SPREAD_STRATEGY_V22_ID = OIL_CALENDAR_SPREAD_STRATEGY_V2_ID


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("calendar spread v0.2.2 contains a non-finite value")
        return round(value, 8)
    return value


def build_oil_calendar_spread_strategy_v22_decision(
    market: Mapping[str, Any],
    forecast_vintage: Mapping[str, Any],
    *,
    authorized_strategy_capital_usd: float,
    strategy_book: Mapping[str, Any],
    strategy_research_profile: Mapping[str, Any] | None = None,
    thesis_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a v0.2.2 decision from a strategy-local normalized market view."""

    normalized_market, adapter_report = normalize_oil_calendar_spread_market_history(
        market
    )
    base = build_oil_calendar_spread_strategy_v2_decision(
        normalized_market,
        forecast_vintage,
        authorized_strategy_capital_usd=authorized_strategy_capital_usd,
        strategy_book=strategy_book,
        strategy_research_profile=strategy_research_profile,
        thesis_state=thesis_state,
    )
    base_identity = dict(base["identity"])
    payload = {key: value for key, value in base.items() if key != "identity"}
    strategy = dict(payload["strategy"])
    strategy.update(
        {
            "candidate_model_version": OIL_CALENDAR_SPREAD_STRATEGY_V22_MODEL_VERSION,
            "prior_candidate_model_version": str(base_identity["model_version"]),
            "market_history_adapter_owner": (
                OIL_CALENDAR_SPREAD_MARKET_HISTORY_ADAPTER_VERSION
            ),
        }
    )
    payload["strategy"] = strategy

    signal = dict(payload["signal"])
    signal["visible_history_coordinate_owner"] = (
        "oil_calendar_spread_market_history_adapter"
    )
    payload["signal"] = signal

    information = dict(payload["informationPolicy"])
    information.update(
        {
            "market_history_metadata_only_adapter": True,
            "market_history_adapter_changed_prices": False,
            "market_history_adapter_changed_liquidity": False,
            "market_history_adapter_write_back": False,
        }
    )
    payload["informationPolicy"] = information
    payload["marketHistoryAdapter"] = adapter_report
    payload["baseDecisionIdentity"] = base_identity

    rounded_payload = _round_nested(payload)
    identity = {
        "model_version": OIL_CALENDAR_SPREAD_STRATEGY_V22_MODEL_VERSION,
        "strategy_id": OIL_CALENDAR_SPREAD_STRATEGY_V22_ID,
        "base_decision_identity_hash": str(base_identity["identity_hash"]),
        "base_decision_result_hash": str(base_identity["result_hash"]),
        "market_history_adapter_model_version": (
            OIL_CALENDAR_SPREAD_MARKET_HISTORY_ADAPTER_VERSION
        ),
        "market_history_adapter_result_hash": str(adapter_report["result_hash"]),
        "strategy_book_identity_hash": str(
            base_identity["strategy_book_identity_hash"]
        ),
        "strategy_profile_hash": str(base_identity["strategy_profile_hash"]),
        "upstream_global_identity_hash": str(
            base_identity["upstream_global_identity_hash"]
        ),
        "forecast_vintage_id": str(base_identity["forecast_vintage_id"]),
        "write_back": False,
        "result_hash": sha256_json(rounded_payload),
    }
    identity["identity_hash"] = sha256_json(identity)
    return _round_nested({"identity": identity, **rounded_payload})
