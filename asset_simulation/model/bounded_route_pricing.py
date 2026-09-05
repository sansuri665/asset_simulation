"""Fixed-window indicative TCE with bounded, short-memory service pressure.

The physical cargo ledger is NEVER clipped, forgotten, cancelled or repriced by
this component. A separate signed signal remembers recent transport-plan
slippage. Its memory and the final quote are bounded, not the actual barrels.
The main v0.2.1 kernel remains available unchanged as a regression control.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from .registry import sha256_json
from .single_route_pricing import (
    _finite, _validate_config, price_single_route_turn,
)

MODEL_VERSION = "asset-simulation-bounded-route-pricing-v0.1.0"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "bounded_route_pricing_v0.1.json"
OPERATING_TURN_DAYS = 10


def validate_bounded_config(config: Mapping[str, Any]) -> None:
    _validate_config(config)
    if config.get("bounded_model_version") != MODEL_VERSION:
        raise ValueError("bounded pricing config version mismatch")
    if config["reference_turn_days"] != OPERATING_TURN_DAYS:
        raise ValueError("bounded pricing requires a ten-day reference window")
    pressure = config["pressure"]
    for key in ("limit_days", "soft_scale_days", "half_life_turns"):
        if _finite(pressure[key], key) <= 0:
            raise ValueError(f"{key} must be positive")
    p = config["pricing"]
    low, base, high = (p[k] for k in (
        "minimum_real_tce_2025_usd_per_day", "baseline_real_tce_2025_usd_per_day",
        "maximum_real_tce_2025_usd_per_day"))
    if not 0 < low < base < high:
        raise ValueError("soft price bounds must bracket a positive baseline")


def load_bounded_pricing_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    validate_bounded_config(config)
    return config


def bounded_pressure(previous_days: float, gap_change_days: float, *,
                     config: Mapping[str, Any], decay: bool) -> float:
    """Advance a SIGNAL, not an inventory. No unit of oil is removed here.

    With no new plan-gap changes, the signal halves every half_life_turns.
    Clip the state before future updates (anti-windup): severe old history
    cannot hide an arbitrarily large pressure stock behind a price cap.
    """
    limit = float(config["pressure"]["limit_days"])
    old = _finite(previous_days, "previous pressure")
    change = _finite(gap_change_days, "gap change")
    if abs(old) > limit + 1e-10:
        raise ValueError("pressure state exceeds its signal bounds")
    persistence = 2.0 ** (-1.0 / float(config["pressure"]["half_life_turns"])) if decay else 1.0
    return max(-limit, min(limit, persistence * old + change))


def align_pressure_with_gap(pressure: float, signed_gap_bbl: int | float) -> float:
    """Catching up old missed cargo is relief, not a new surplus panic."""
    if signed_gap_bbl > 0:
        return max(0.0, pressure)
    if signed_gap_bbl < 0:
        return min(0.0, pressure)
    return 0.0


def _soft_price_parameters(config: Mapping[str, Any]) -> tuple[float, ...]:
    pricing = config["pricing"]
    low = float(pricing["minimum_real_tce_2025_usd_per_day"])
    base = float(pricing["baseline_real_tce_2025_usd_per_day"])
    high = float(pricing["maximum_real_tce_2025_usd_per_day"])
    p = (base - low) / (high - low)
    offset = math.log(p / (1.0 - p))
    # Match the old exp curve's local derivative at the ordinary anchor.
    slope = base / ((high - low) * p * (1.0 - p))
    return low, base, high, offset, slope


def soft_price(signal: float, config: Mapping[str, Any]) -> float:
    low, _, high, offset, slope = _soft_price_parameters(config)
    z = offset + slope * signal
    if z >= 0:
        probability = 1.0 / (1.0 + math.exp(-min(z, 700.0)))
    else:
        e = math.exp(max(z, -700.0))
        probability = e / (1.0 + e)
    return low + (high - low) * probability


def inverse_soft_price(price: float, config: Mapping[str, Any]) -> float:
    low, _, high, offset, slope = _soft_price_parameters(config)
    probability = max(1e-12, min(1.0 - 1e-12, (price - low) / (high - low)))
    return (math.log(probability / (1.0 - probability)) - offset) / slope


def price_bounded_route_turn(*, structural_cargo_mbd: float, turn_days: int,
        prompt_supply_vlcc: float, pricing_pressure_days: float = 0.0,
        origin_inventory_deviation_mmbbl: float = 0.0,
        destination_inventory_deviation_mmbbl: float = 0.0,
        previous_real_tce_2025_usd_per_day: float | None = None,
        cpi_price_level_index_2025_100: float = 100.0,
        config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Pure quote: current work, prompt ships, bounded signal -> indicative TCE.

    Raw G/E are diagnostics only. They do not silently regenerate the maximum
    premium from a decades-old backlog. There is no price-based dispatch or
    long-run demand destruction. This is not a negotiated clearing price.
    """
    cfg = load_bounded_pricing_config() if config is None else config
    validate_bounded_config(cfg)
    if isinstance(turn_days, bool) or not isinstance(turn_days, int) or turn_days != OPERATING_TURN_DAYS:
        raise ValueError("each operating turn must be exactly ten days")
    pressure = _finite(pricing_pressure_days, "pricing pressure")
    origin = _finite(origin_inventory_deviation_mmbbl, "origin deviation")
    destination = _finite(destination_inventory_deviation_mmbbl, "destination deviation")
    rate = _finite(structural_cargo_mbd, "cargo rate")
    limit = float(cfg["pressure"]["limit_days"])
    scale = float(cfg["pressure"]["soft_scale_days"])
    if abs(pressure) > limit + 1e-10:
        raise ValueError("price pressure must be bounded before quoting")
    urgency = math.tanh(pressure / scale) / math.tanh(limit / scale)
    priced_days = limit * urgency
    reference_rate = float(cfg["reference_route_cargo_mbd"])
    denominator = rate if rate > 0 else reference_rate
    equivalent_gap = priced_days * denominator
    # Use the unchanged kernel's D/S and inventory-recovery arithmetic only.
    # Previous-price persistence is applied below in invertible soft-price space.
    quote = price_single_route_turn(
        structural_cargo_mbd=rate, turn_days=turn_days,
        prompt_supply_vlcc=prompt_supply_vlcc,
        origin_inventory_deviation_mmbbl=equivalent_gap,
        destination_inventory_deviation_mmbbl=-equivalent_gap,
        previous_real_tce_2025_usd_per_day=previous_real_tce_2025_usd_per_day,
        cpi_price_level_index_2025_100=cpi_price_level_index_2025_100, config=cfg,
    )
    low, base, high, _, _ = _soft_price_parameters(cfg)
    previous = base if previous_real_tce_2025_usd_per_day is None else _finite(previous_real_tce_2025_usd_per_day, "previous TCE")
    persistence = float(cfg["pricing"]["price_persistence"])
    raw_signal = float(quote["raw_log_price_signal"])
    settled = persistence * inverse_soft_price(previous, cfg) + (1.0 - persistence) * raw_signal
    real = max(low, min(high, previous)) if rate == 0 else soft_price(settled, cfg)
    quote.update({
        "model_version": MODEL_VERSION, "pricing_config_hash": sha256_json(cfg),
        "origin_inventory_deviation_mmbbl": origin,
        "destination_inventory_deviation_mmbbl": destination,
        "inventory_gap_mmbbl": (0.5 * origin - 0.5 * destination),
        "inventory_gap_days": (0.5 * origin - 0.5 * destination) / denominator,
        "pricing_pressure_days": pressure, "priced_inventory_pressure_days": priced_days,
        "inventory_urgency": urgency,
        "settled_log_price_signal": settled,
        "raw_real_tce_2025_usd_per_day": real,
        "real_tce_2025_usd_per_day": round(real, 2),
        "nominal_tce_usd_per_day": round(_finite(real * float(cpi_price_level_index_2025_100) / 100.0, "nominal TCE"), 2),
        "minimum_price_guard_hit": real <= low,
        "maximum_price_guard_hit": real >= high,
        "near_upper_price_bound": real >= low + 0.95 * (high - low),
        "near_lower_price_bound": real <= low + 0.05 * (high - low),
        "price_bound_scope": "game_benchmark_2025_usd_not_cost_or_empirical_wtp",
        "raw_inventory_used_directly_for_quote": False,
    })
    return quote
