"""Annual oil-price anchor retained for the global macro world.

The demand, supply and tightness values in this module are statistical price
signals. Physical barrels, inventories and shipping demand are owned by the
separate monthly oil-shipping world.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal


def annual_price_envelope(
    *,
    seed: int,
    year_index: int,
    open_real: float,
    close_real: float,
    inventory_tightness_index: float,
    oil_demand_index: float,
    oil_supply_index: float,
    dollar_yoy_change_pct: float,
    real_bounds: tuple[float, float] | list[float],
    volatility_regime_index: float = 1.0,
) -> dict[str, float]:
    """Build a read-only intra-year range around an annual oil settlement."""

    open_real = float(open_real)
    close_real = float(close_real)
    body_high = max(open_real, close_real)
    body_low = min(open_real, close_real)
    signed_body_log = 100.0 * math.log(close_real / open_real) if open_real > 0.0 else 0.0
    body_log = abs(signed_body_log)
    flow_gap_pct = (float(oil_demand_index) - float(oil_supply_index)) / max(
        1.0, float(oil_demand_index)
    ) * 100.0
    tightness = float(inventory_tightness_index)
    volatility_regime = clamp(float(volatility_regime_index), 0.70, 1.50)
    sigma = clamp(
        43.5 * volatility_regime
        + 0.32 * abs(tightness)
        + 0.28 * abs(flow_gap_pct)
        + 0.12 * abs(float(dollar_yoy_change_pct)),
        28.0,
        68.0,
    )
    wick_budget = clamp(0.72 * sigma - 0.62 * body_log, 4.0, 48.0)
    split = clamp(
        0.50
        - 0.34 * math.tanh(signed_body_log / 9.0)
        + 0.001 * tightness
        + 0.06 * normal(seed, "oil_intrayear_range", year_index),
        0.12,
        0.88,
    )
    high_real = body_high * math.exp(wick_budget * split / 100.0)
    low_real = body_low * math.exp(-wick_budget * (1.0 - split) / 100.0)
    bound_low, bound_high = map(float, real_bounds)
    return {
        "real_high_index": clamp(high_real, body_high, bound_high),
        "real_low_index": clamp(low_real, bound_low, body_low),
    }


def expand_envelope_to_steps(
    *,
    seed: int,
    year_index: int,
    open_px: float,
    close_px: float,
    high_px: float,
    low_px: float,
    steps: int,
    shock_scale: float,
    max_step: float,
    stream_prefix: str,
    step_field: str,
    quiet_body: float = 2.8,
    quiet_span_scale: float = 3.0,
) -> tuple[dict[str, float], ...]:
    """Expand an annual envelope into chained, deterministic OHLC bars."""

    open_px = float(open_px)
    close_px = float(close_px)
    high_px = max(float(high_px), open_px, close_px)
    low_px = min(float(low_px), open_px, close_px)
    if open_px <= 0.0 or close_px <= 0.0 or high_px <= 0.0 or low_px <= 0.0:
        raise ValueError("envelope prices must be positive")
    if steps < 2:
        raise ValueError("envelope expansion needs at least two steps")

    leftover_up = high_px - max(open_px, close_px)
    leftover_down = min(open_px, close_px) - low_px
    corridor_fraction = 0.97 if steps <= 4 else 0.90
    high_cap = (
        high_px
        if leftover_up <= 1e-12
        else max(open_px, close_px) + corridor_fraction * leftover_up
    )
    low_cap = (
        low_px
        if leftover_down <= 1e-12
        else min(open_px, close_px) - corridor_fraction * leftover_down
    )
    log_open = math.log(open_px)
    log_close = math.log(close_px)
    trend_step = (log_close - log_open) / steps
    shocks: list[float] = []
    for step_index in range(1, steps + 1):
        raw_shock = shock_scale * normal(
            seed,
            f"{stream_prefix}_step_{step_index}",
            year_index,
        )
        shock_ratio = abs(raw_shock) / max_step
        shocks.append(raw_shock / (1.0 + shock_ratio**6) ** (1.0 / 6.0))
    shock_mean = sum(shocks) / steps
    raw_logs = [log_open]
    for shock in shocks:
        raw_logs.append(raw_logs[-1] + trend_step + shock - shock_mean)
    raw_logs[-1] = log_close

    log_high_cap = math.log(high_cap)
    log_low_cap = math.log(low_cap)
    corridor_span = log_high_cap - log_low_cap
    if corridor_span <= 1e-12:
        reflected_logs = [log_open] * (steps + 1)
    else:
        reflected_logs = []
        for raw_log in raw_logs:
            offset = (raw_log - log_low_cap) % (2.0 * corridor_span)
            reflected_logs.append(
                log_low_cap + offset
                if offset <= corridor_span
                else log_high_cap - (offset - corridor_span)
            )
    prices = [math.exp(value) for value in reflected_logs]
    prices[0] = open_px
    prices[-1] = close_px

    bars: list[dict[str, float]] = []
    for step_index in range(1, steps + 1):
        step_open = prices[step_index - 1]
        step_close = prices[step_index]
        signed_body_log = 100.0 * math.log(step_close / step_open)
        body_log = abs(signed_body_log)
        body_high = max(step_open, step_close)
        body_low = min(step_open, step_close)
        quiet_fraction = (
            clamp((quiet_body - body_log) / quiet_body, 0.0, 1.0)
            if quiet_body > 0.0
            else 0.0
        )
        wick_budget = clamp(0.48 * body_log + 2.8, 2.0, 12.0)
        split = clamp(
            0.50
            - 0.34 * math.tanh(signed_body_log / 9.0) * (1.0 - 0.55 * quiet_fraction)
            + 0.08 * normal(seed, f"{stream_prefix}_wick_{step_index}", year_index),
            0.12 + 0.18 * quiet_fraction,
            0.88 - 0.18 * quiet_fraction,
        )
        quiet_span = quiet_span_scale * quiet_fraction
        quiet_tilt = 0.22 * normal(
            seed,
            f"{stream_prefix}_quiet_wick_{step_index}",
            year_index,
        )
        upper_log = wick_budget * split + quiet_span * clamp(
            1.0 + quiet_tilt, 0.55, 1.45
        )
        lower_log = wick_budget * (1.0 - split) + quiet_span * clamp(
            1.0 - quiet_tilt, 0.55, 1.45
        )
        step_high = min(high_px, body_high * math.exp(upper_log / 100.0))
        step_low = max(low_px, body_low * math.exp(-lower_log / 100.0))
        bars.append(
            {
                step_field: float(step_index),
                "open": round(step_open, 8),
                "high": round(max(step_high, body_high), 8),
                "low": round(min(step_low, body_low), 8),
                "close": round(step_close, 8),
            }
        )

    high_bar = max(range(steps), key=lambda index: max(bars[index]["open"], bars[index]["close"]))
    low_bar = min(range(steps), key=lambda index: min(bars[index]["open"], bars[index]["close"]))
    if high_bar == low_bar:
        alternatives = [index for index in range(steps) if index != high_bar]
        if leftover_up >= leftover_down:
            low_bar = min(
                alternatives,
                key=lambda index: min(bars[index]["open"], bars[index]["close"]),
            )
        else:
            high_bar = max(
                alternatives,
                key=lambda index: max(bars[index]["open"], bars[index]["close"]),
            )
    bars[high_bar]["high"] = round(high_px, 8)
    bars[low_bar]["low"] = round(low_px, 8)
    return tuple(bars)


def expand_annual_to_months(
    *,
    seed: int,
    year_index: int,
    open_px: float,
    close_px: float,
    high_px: float,
    low_px: float,
    volatility_regime_index: float = 1.0,
) -> tuple[dict[str, float], ...]:
    """Expand one annual price envelope into 12 monthly OHLC bars."""

    return expand_envelope_to_steps(
        seed=seed,
        year_index=year_index,
        open_px=open_px,
        close_px=close_px,
        high_px=high_px,
        low_px=low_px,
        steps=12,
        shock_scale=0.096 * clamp(float(volatility_regime_index), 0.75, 1.40),
        max_step=0.21,
        stream_prefix="oil_month",
        step_field="month",
    )


def initial_state(config: Mapping[str, Any]) -> dict[str, float]:
    initial = config["initial_conditions"]
    return {
        "oil_demand_index": float(initial["oil_demand_index"]),
        "oil_supply_index": float(initial["oil_supply_index"]),
        "inventory_tightness_index": float(initial["oil_inventory_tightness_index"]),
        "return_momentum_pct": float(initial["oil_return_momentum_pct"]),
        "volatility_regime_index": float(initial["oil_volatility_regime_index"]),
        "real_oil_price_index": float(initial["real_oil_price_index"]),
        "real_commodity_price_index": float(initial["real_commodity_price_index"]),
        "energy_cost_pressure_index": float(initial["energy_cost_pressure_index"]),
    }


def step(
    state: Mapping[str, float],
    real: Mapping[str, float],
    inflation: Mapping[str, float],
    funding: Mapping[str, float],
    *,
    seed: int,
    year_index: int,
    config: Mapping[str, Any],
    impulses: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    bounds = config["bounds"]
    demand_news = normal(seed, "oil_demand", year_index)
    supply_news = normal(seed, "oil_supply", year_index)
    oil_price_news = normal(seed, "oil_price", year_index)
    trend_news = normal(seed, "oil_trend", year_index)
    volatility_news = normal(seed, "oil_volatility", year_index)
    commodity_news = normal(seed, "commodity_price", year_index)
    impulses = {} if impulses is None else impulses
    supply_impulse = float(impulses.get("oil_supply_growth_impulse_pp", 0.0))

    demand_growth = (
        0.58 * float(real["realized_growth_pct"])
        - 0.62
        + 0.10 * float(real["output_gap_pct"])
        - 0.035 * (state["real_oil_price_index"] - 100.0)
        + 0.55 * demand_news
    )
    supply_growth = (
        0.72
        + 0.025 * (state["real_oil_price_index"] - 100.0)
        + 0.65 * supply_news
        + supply_impulse
    )
    demand = state["oil_demand_index"] * (1.0 + clamp(demand_growth, -4.5, 5.0) / 100.0)
    supply = state["oil_supply_index"] * (1.0 + clamp(supply_growth, -4.5, 5.0) / 100.0)
    flow_gap_pct = (demand - supply) / max(1.0, demand) * 100.0
    inventory_tightness = clamp(
        0.60 * state["inventory_tightness_index"] + 1.05 * flow_gap_pct,
        -18.0,
        22.0,
    )
    # A symmetric, persistent ordinary-volatility state. Named crisis jumps
    # remain reserved for the explicit impulse ports.
    volatility_regime = clamp(
        0.78 * state["volatility_regime_index"]
        + 0.22
        + 0.055 * volatility_news,
        *map(float, bounds["oil_volatility_regime_index"]),
    )
    # Oil trends can persist for a few annual settlements without turning
    # the price level into an unbounded random walk.
    return_momentum = clamp(
        0.52 * state["return_momentum_pct"]
        + 0.20 * flow_gap_pct
        + 0.06 * inventory_tightness
        + 5.5 * volatility_regime * trend_news,
        *map(float, bounds["oil_return_momentum_pct"]),
    )
    real_oil_log_return = clamp(
        0.45 * flow_gap_pct
        + 0.16 * inventory_tightness
        + 0.58 * return_momentum
        - 0.075 * (state["real_oil_price_index"] - 100.0)
        - 0.15 * float(funding["dollar_yoy_change_pct"])
        + 1.5 * commodity_news
        + 9.5 * volatility_regime * oil_price_news,
        -34.0,
        44.0,
    )
    raw_real_oil = state["real_oil_price_index"] * math.exp(real_oil_log_return / 100.0)
    oil_bound_low, oil_bound_high = map(float, bounds["real_oil_price_index"])
    # Damped log reflection keeps a rare overshoot inside the process rails
    # without pinning several annual closes to exactly the same boundary.
    if raw_real_oil < oil_bound_low:
        real_oil = oil_bound_low * math.exp(0.35 * math.log(oil_bound_low / raw_real_oil))
    elif raw_real_oil > oil_bound_high:
        real_oil = oil_bound_high * math.exp(-0.35 * math.log(raw_real_oil / oil_bound_high))
    else:
        real_oil = raw_real_oil
    real_oil = clamp(real_oil, oil_bound_low, oil_bound_high)
    settled_real_oil_log_return = 100.0 * math.log(real_oil / state["real_oil_price_index"])
    real_oil_yoy = (real_oil / state["real_oil_price_index"] - 1.0) * 100.0
    nominal_brent = float(config["anchors"]["brent_oil_price_usd"]) * real_oil / 100.0 * float(
        inflation["cpi_price_level_index"]
    ) / 100.0
    previous_nominal_brent = float(config["anchors"]["brent_oil_price_usd"]) * state[
        "real_oil_price_index"
    ] / 100.0 * float(inflation["previous_cpi_price_level_index"]) / 100.0
    oil_yoy = (nominal_brent / previous_nominal_brent - 1.0) * 100.0
    real_commodity_return = clamp(
        0.34 * real_oil_yoy
        + 0.44 * (float(real["realized_growth_pct"]) - float(real["potential_growth_pct"]))
        - 0.18 * float(funding["dollar_yoy_change_pct"])
        - 0.025 * (state["real_commodity_price_index"] - 100.0)
        + 1.0 * commodity_news,
        -18.0,
        24.0,
    )
    real_commodity_index = clamp(
        state["real_commodity_price_index"] * (1.0 + real_commodity_return / 100.0),
        *map(float, bounds["real_commodity_price_index"]),
    )
    real_commodity_yoy = (
        real_commodity_index / state["real_commodity_price_index"] - 1.0
    ) * 100.0
    nominal_commodity_index = (
        real_commodity_index * float(inflation["cpi_price_level_index"]) / 100.0
    )
    previous_nominal_commodity_index = (
        state["real_commodity_price_index"]
        * float(inflation["previous_cpi_price_level_index"])
        / 100.0
    )
    commodity_yoy = (
        nominal_commodity_index / previous_nominal_commodity_index - 1.0
    ) * 100.0
    energy_pressure = clamp(
        0.64 * state["energy_cost_pressure_index"]
        + 0.36 * 50.0
        + 0.42 * real_oil_yoy
        + 0.10 * real_commodity_yoy,
        *map(float, bounds["energy_cost_pressure_index"]),
    )

    next_state = {
        "oil_demand_index": demand,
        "oil_supply_index": supply,
        "inventory_tightness_index": inventory_tightness,
        "return_momentum_pct": return_momentum,
        "volatility_regime_index": volatility_regime,
        "real_oil_price_index": real_oil,
        "real_commodity_price_index": real_commodity_index,
        "energy_cost_pressure_index": energy_pressure,
    }
    diagnostics = {
        "oil_demand_growth_pct": demand_growth,
        "oil_supply_growth_pct": supply_growth,
        "exogenous_oil_supply_growth_impulse_pp": supply_impulse,
        "oil_flow_gap_pct": flow_gap_pct,
        "oil_price_news_pct": 9.5 * volatility_regime * oil_price_news,
        "oil_return_momentum_pct": return_momentum,
        "oil_volatility_regime_index": volatility_regime,
        "raw_real_oil_log_return_pct": real_oil_log_return,
        "real_oil_log_return_pct": settled_real_oil_log_return,
        "real_oil_yoy_change_pct": real_oil_yoy,
        "brent_oil_price_usd": nominal_brent,
        "oil_yoy_change_pct": oil_yoy,
        "real_commodity_yoy_change_pct": real_commodity_yoy,
        "broad_commodity_index": nominal_commodity_index,
        "commodity_yoy_change_pct": commodity_yoy,
    }
    return next_state, diagnostics
