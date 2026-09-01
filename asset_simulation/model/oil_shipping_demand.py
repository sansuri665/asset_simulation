"""Monthly trade-distance environment for the crude-route allocator."""

from __future__ import annotations

from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal


def initial_shipping_state(config: Mapping[str, Any]) -> dict[str, float]:
    shipping = config["shipping_environment"]
    return {
        "trade_dislocation_index": float(
            shipping["initial_trade_dislocation_index"]
        )
    }


def advance_shipping_demand(
    state: Mapping[str, float],
    physical_turn: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    config: Mapping[str, Any],
    impulse: Mapping[str, Any] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Advance distance conditions only; regional balances own cargo volume."""

    del physical_turn
    impulse = {} if impulse is None else impulse
    shipping = config["shipping_environment"]
    low_dislocation, high_dislocation = map(
        float,
        shipping["trade_dislocation_bounds"],
    )
    base_dislocation = clamp(
        1.0
        + float(shipping["trade_dislocation_persistence"])
        * (float(state["trade_dislocation_index"]) - 1.0)
        + float(shipping["trade_dislocation_news_scale"])
        * normal(seed, "oil_shipping_monthly_trade_dislocation", turn_index),
        low_dislocation,
        high_dislocation,
    )
    haul_impulse_pct = float(impulse.get("average_haul_impulse_pct", 0.0))
    effective_dislocation = clamp(
        base_dislocation * (1.0 + haul_impulse_pct / 100.0),
        low_dislocation,
        high_dislocation,
    )
    return (
        {"trade_dislocation_index": base_dislocation},
        {
            "base_trade_dislocation_index": base_dislocation,
            "trade_dislocation_index": effective_dislocation,
        },
    )
