"""Read-only 01/05/09 oil curve and half-month histories for the game.

The owner publishes a complete visible spot-reference history, a continuous
main series that rolls before the expiry-month turn, and four named delivery-
month contracts. Each named contract has a 16-month / 32-turn lifecycle. Curve
prices use inventory-conditioned convenience yield plus persistent long-slope,
near-pressure, and curvature factors and never read a later weekly bar or later
annual macro state. Futures-only weekly
volume and open interest use a bounded global-liquidity process and migrate
between named contracts around each roll; the spot reference remains untouched.
"""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from typing import Any, Iterable, Mapping

from .commodity_overlay import run_commodity_overlay
from .engine import GlobalMacroRun
from .math_utils import clamp
from .performance_cache import deterministic_projection_cache
from .random_stream import normal
from .registry import load_registered_assets, sha256_json


OIL_FUTURES_MODEL_VERSION = "asset-simulation-oil-futures-overlay-v0.8.0"


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("oil futures output contains a non-finite value")
        return round(value, 8)
    return value


def _month_serial(year: int, month: int) -> int:
    return int(year) * 12 + int(month) - 1


def _date_from_serial(serial: int) -> tuple[int, int]:
    return serial // 12, serial % 12 + 1


def _half_turn_serial(year: int, month: int, half: int) -> int:
    return _month_serial(year, month) * 2 + int(half) - 1


def _week_serial(year: int, month: int, week: int) -> int:
    return _month_serial(year, month) * 4 + int(week) - 1


def _delivery_contract_id(year: int, month: int) -> str:
    return f"OIL-{int(year) % 100:02d}{int(month):02d}"


def _delivery_name(year: int, month: int) -> str:
    return f"原油{int(year) % 100:02d}{int(month):02d}"


def _listed_expiries(
    year: int,
    month: int,
    *,
    expiry_months: Iterable[int],
    count: int,
) -> tuple[tuple[int, int], ...]:
    expiries: list[tuple[int, int]] = []
    cursor_year = int(year)
    while len(expiries) < count:
        for expiry_month in expiry_months:
            if cursor_year == year and int(expiry_month) < month:
                continue
            expiries.append((cursor_year, int(expiry_month)))
            if len(expiries) == count:
                break
        cursor_year += 1
    return tuple(expiries)


def _latest_completed_macro_row(
    rows: tuple[Mapping[str, Any], ...],
    as_of_year: int,
) -> Mapping[str, Any]:
    completed = [row for row in rows if int(row["year"]) < as_of_year]
    return completed[-1] if completed else rows[0]


def _recent_annualized_trend(
    closes: list[float],
    *,
    window_periods: int,
    periods_per_year: int,
    bounds: list[float] | tuple[float, float],
) -> float:
    periods = min(int(window_periods), len(closes) - 1)
    if periods <= 0:
        return 0.0
    old = float(closes[-periods - 1])
    current = float(closes[-1])
    if old <= 0.0 or current <= 0.0:
        return 0.0
    raw = 100.0 * math.log(current / old) * float(periods_per_year) / periods
    low, high = map(float, bounds)
    return clamp(raw, low, high)


def _curve_targets(
    macro_row: Mapping[str, Any],
    closes: list[float],
    *,
    month: int,
    curve_config: Mapping[str, Any],
) -> dict[str, float]:
    funding_low, funding_high = map(float, curve_config["funding_bounds_pct"])
    funding = clamp(float(macro_row[curve_config["funding_field"]]), funding_low, funding_high)
    volatility = clamp(float(macro_row["global_oil_volatility_regime_index"]), 0.70, 1.50)
    demand = float(macro_row["global_oil_demand_index"])
    supply = float(macro_row["global_oil_supply_index"])
    flow_gap = 100.0 * (demand - supply) / max(1.0, demand)
    tightness = float(macro_row["global_oil_inventory_tightness_index"])
    trend = _recent_annualized_trend(
        closes,
        window_periods=int(curve_config["trend_window_months"]) * 2,
        periods_per_year=24,
        bounds=curve_config["trend_bounds_pct"],
    )
    storage = float(curve_config["storage_base_pct"]) + float(
        curve_config["storage_volatility_scale"]
    ) * (volatility - 1.0)
    convenience_low, convenience_high = map(
        float, curve_config["convenience_yield_bounds_pct"]
    )
    convenience_yield = clamp(
        float(curve_config["convenience_yield_base_pct"])
        + float(curve_config["convenience_yield_tightness_scale"]) * tightness
        + float(curve_config["convenience_yield_flow_gap_scale"]) * flow_gap
        + float(curve_config["convenience_yield_trend_scale"]) * trend,
        convenience_low,
        convenience_high,
    )
    risk_premium = float(curve_config["risk_premium_base_pct"]) + float(
        curve_config["risk_premium_volatility_scale"]
    ) * max(0.0, volatility - 1.0)
    long_low, long_high = map(float, curve_config["long_slope_bounds_pct"])
    long_target = clamp(
        funding + storage + risk_premium - convenience_yield,
        long_low,
        long_high,
    )
    tight_excess = max(0.0, tightness - float(curve_config["near_tightness_threshold"]))
    loose_excess = max(
        0.0, -tightness - float(curve_config["near_looseness_threshold"])
    )
    season_angle = 2.0 * math.pi * (month - 1) / 12.0
    near_low, near_high = map(float, curve_config["near_pressure_bounds_pct"])
    near_target = clamp(
        float(curve_config["near_tightness_scale"]) * tightness
        + float(curve_config["near_flow_gap_scale"]) * flow_gap
        + float(curve_config["near_trend_scale"]) * trend
        + float(curve_config["near_tightness_nonlinear_scale"]) * tight_excess**2
        + float(curve_config["near_looseness_nonlinear_scale"]) * loose_excess**2
        + float(curve_config["near_seasonal_scale"]) * math.sin(season_angle),
        near_low,
        near_high,
    )
    curvature_low, curvature_high = map(float, curve_config["curvature_bounds_pct"])
    curvature_target = clamp(
        float(curve_config["curvature_trend_scale"]) * trend
        + float(curve_config["curvature_seasonal_scale"]) * math.cos(season_angle),
        curvature_low,
        curvature_high,
    )
    return {
        "funding_pct": funding,
        "storage_pct": storage,
        "convenience_yield_pct": convenience_yield,
        "term_risk_premium_pct": risk_premium,
        "inventory_tightness_index": tightness,
        "flow_gap_pct": flow_gap,
        "recent_annualized_trend_pct": trend,
        "volatility_regime_index": volatility,
        "long_slope_target_pct": long_target,
        "near_pressure_target_pct": near_target,
        "curvature_target_pct": curvature_target,
    }


def _advance_curve_factors(
    *,
    seed: int,
    month_address: int,
    previous: Mapping[str, float] | None,
    targets: Mapping[str, float],
    curve_config: Mapping[str, Any],
) -> dict[str, float]:
    specs = (
        ("long_slope_pct", "long_slope_target_pct", "long_slope_persistence", "long_slope_news_scale", "long_slope_bounds_pct", "long"),
        ("near_pressure_pct", "near_pressure_target_pct", "near_pressure_persistence", "near_pressure_news_scale", "near_pressure_bounds_pct", "near"),
        ("curvature_pct", "curvature_target_pct", "curvature_persistence", "curvature_news_scale", "curvature_bounds_pct", "curvature"),
    )
    factors: dict[str, float] = {}
    for field, target_field, persistence_field, news_field, bounds_field, stream in specs:
        target = float(targets[target_field])
        persistence = float(curve_config[persistence_field])
        prior = target if previous is None else float(previous[field])
        raw = persistence * prior + (1.0 - persistence) * target + float(
            curve_config[news_field]
        ) * normal(seed, f"oil_futures_curve.{stream}", month_address)
        low, high = map(float, curve_config[bounds_field])
        factors[field] = clamp(raw, low, high)
    return factors


def _advance_market_liquidity(
    *,
    seed: int,
    week_address: int,
    macro_row: Mapping[str, Any],
    anchor_macro_row: Mapping[str, Any],
    reference_close: float,
    previous_reference_close: float | None,
    previous: Mapping[str, float] | None,
    liquidity_config: Mapping[str, Any],
) -> dict[str, float]:
    """Advance one globally scaled, bounded weekly futures-liquidity state."""

    structural_persistence = float(liquidity_config["structural_persistence"])
    prior_structural_log = 0.0 if previous is None else float(previous["structural_log"])
    structural_log = structural_persistence * prior_structural_log + float(
        liquidity_config["structural_news_scale"]
    ) * normal(seed, "oil_futures_liquidity.structural", week_address)

    anchor_gdp = max(1e-9, float(anchor_macro_row["global_gdp_trillion_usd"]))
    anchor_demand = max(1e-9, float(anchor_macro_row["global_oil_demand_index"]))
    gdp_ratio = max(1e-9, float(macro_row["global_gdp_trillion_usd"]) / anchor_gdp)
    demand_ratio = max(1e-9, float(macro_row["global_oil_demand_index"]) / anchor_demand)
    structural_scale = (
        gdp_ratio ** float(liquidity_config["gdp_elasticity"])
        * demand_ratio ** float(liquidity_config["oil_demand_elasticity"])
        * math.exp(structural_log)
    )
    structural_low, structural_high = map(
        float, liquidity_config["structural_scale_bounds"]
    )
    structural_scale = clamp(structural_scale, structural_low, structural_high)

    open_interest_target = float(
        liquidity_config["base_open_interest_lots"]
    ) * structural_scale
    if previous is None:
        open_interest = open_interest_target
    else:
        open_interest_persistence = float(
            liquidity_config["open_interest_persistence"]
        )
        open_interest = (
            open_interest_persistence * float(previous["total_open_interest_lots"])
            + (1.0 - open_interest_persistence) * open_interest_target
            + float(liquidity_config["open_interest_news_scale"])
            * open_interest_target
            * normal(seed, "oil_futures_liquidity.open_interest", week_address)
        )
    oi_low, oi_high = map(float, liquidity_config["open_interest_bounds_lots"])
    open_interest = clamp(open_interest, oi_low, oi_high)

    activity_persistence = float(liquidity_config["activity_persistence"])
    prior_activity_log = 0.0 if previous is None else float(previous["activity_log"])
    activity_log = activity_persistence * prior_activity_log + float(
        liquidity_config["activity_news_scale"]
    ) * normal(seed, "oil_futures_liquidity.activity", week_address)
    activity_low, activity_high = map(float, liquidity_config["activity_scale_bounds"])
    activity_scale = clamp(math.exp(activity_log), activity_low, activity_high)
    activity_log = math.log(activity_scale)

    absolute_log_return = 0.0
    if previous_reference_close is not None and previous_reference_close > 0.0:
        absolute_log_return = abs(math.log(reference_close / previous_reference_close))
    return_ratio = absolute_log_return / max(
        1e-9, float(liquidity_config["return_reference_abs_log"])
    )
    return_scale = min(
        float(liquidity_config["return_activity_cap"]),
        1.0 + float(liquidity_config["return_activity_scale"]) * return_ratio,
    )
    volatility_regime = clamp(
        float(macro_row["global_oil_volatility_regime_index"]), 0.70, 1.50
    )
    turnover_ratio = (
        float(liquidity_config["base_weekly_turnover_ratio"])
        * activity_scale
        * volatility_regime ** float(liquidity_config["volatility_regime_elasticity"])
        * return_scale
    )
    turnover_low, turnover_high = map(
        float, liquidity_config["turnover_ratio_bounds"]
    )
    turnover_ratio = clamp(turnover_ratio, turnover_low, turnover_high)
    weekly_volume = open_interest * turnover_ratio
    volume_low, volume_high = map(float, liquidity_config["weekly_volume_bounds_lots"])
    weekly_volume = clamp(weekly_volume, volume_low, volume_high)

    return {
        "structural_log": structural_log,
        "structural_scale": structural_scale,
        "activity_log": activity_log,
        "activity_scale": activity_scale,
        "total_open_interest_lots": float(round(open_interest)),
        "weekly_turnover_ratio": turnover_ratio,
        "weekly_volume_lots": float(round(weekly_volume)),
        "absolute_reference_log_return": absolute_log_return,
    }


def _contract_liquidity_shares(
    contracts: list[Mapping[str, Any]],
    *,
    main_contract_id: str,
    current_year: int,
    current_month: int,
    week: int,
    liquidity_config: Mapping[str, Any],
) -> tuple[list[float], list[float]]:
    main_index = next(
        index
        for index, item in enumerate(contracts)
        if str(item["contract_id"]) == main_contract_id
    )
    nearest = contracts[0]
    weeks_to_expiry = max(
        0.0,
        float(
            (
                _month_serial(
                    int(nearest["expiry_year"]), int(nearest["expiry_month"])
                )
                - _month_serial(int(current_year), int(current_month))
            )
            * 4
            + (4 - int(week))
        ),
    )
    window = max(1.0, float(liquidity_config["roll_migration_window_weeks"]))
    linear_progress = clamp(1.0 - weeks_to_expiry / window, 0.0, 1.0)
    migration_progress = linear_progress * linear_progress * (
        3.0 - 2.0 * linear_progress
    )
    volume_before = list(map(float, liquidity_config["volume_share_before_roll"]))
    volume_after = list(map(float, liquidity_config["volume_share_after_roll"]))
    open_interest_before = list(
        map(float, liquidity_config["open_interest_share_before_roll"])
    )
    open_interest_after = list(
        map(float, liquidity_config["open_interest_share_after_roll"])
    )
    volume_shares = [
        before + (after - before) * migration_progress
        for before, after in zip(volume_before, volume_after, strict=True)
    ]
    open_interest_shares = [
        before + (after - before) * migration_progress
        for before, after in zip(
            open_interest_before, open_interest_after, strict=True
        )
    ]

    if float(contracts[0]["months_to_expiry"]) <= 0.0:
        expiry_volume_share = 0.015 if int(week) == 3 else 0.005
        expiry_oi_share = 0.006 if int(week) == 3 else 0.0
        volume_shares[main_index] += max(0.0, volume_shares[0] - expiry_volume_share)
        open_interest_shares[main_index] += max(
            0.0, open_interest_shares[0] - expiry_oi_share
        )
        volume_shares[0] = expiry_volume_share
        open_interest_shares[0] = expiry_oi_share

    return volume_shares, open_interest_shares


def _allocate_lots(
    total_lots: int,
    contracts: list[Mapping[str, Any]],
    shares: list[float],
    *,
    seed: int,
    week_address: int,
    stream: str,
    noise_scale: float,
) -> list[int]:
    weighted = [
        max(0.0, share)
        * math.exp(
            float(noise_scale)
            * normal(seed, f"oil_futures_liquidity.{stream}.{item['contract_id']}", week_address)
        )
        for item, share in zip(contracts, shares, strict=True)
    ]
    total_weight = sum(weighted)
    if total_weight <= 0.0:
        raise ValueError("oil futures liquidity allocation has no positive weight")
    allocated = [int(round(total_lots * weight / total_weight)) for weight in weighted]
    allocated[max(range(len(weighted)), key=weighted.__getitem__)] += total_lots - sum(allocated)
    return allocated


def _refresh_turn_liquidity(turn: dict[str, Any]) -> None:
    weeks = list(turn.get("weekly", ()))
    if not weeks or not all("volume_lots" in week for week in weeks):
        return
    turn["volume_lots"] = sum(int(week["volume_lots"]) for week in weeks)
    turn["open_interest_lots"] = int(weeks[-1]["open_interest_lots"])
    turn["open_interest_change_lots"] = sum(
        int(week["open_interest_change_lots"]) for week in weeks
    )


def _log_basis_pct(
    tau_years: float,
    *,
    factors: Mapping[str, float],
    curve_config: Mapping[str, Any],
) -> float:
    if tau_years <= 0.0:
        return 0.0
    near_decay = float(curve_config["near_decay_years"])
    curvature_decay = float(curve_config["curvature_decay_years"])
    return (
        float(factors["long_slope_pct"]) * tau_years
        + float(factors["near_pressure_pct"]) * (1.0 - math.exp(-tau_years / near_decay))
        + float(factors["curvature_pct"]) * tau_years * math.exp(-tau_years / curvature_decay)
    )


def _curve_contracts(
    *,
    as_of_year: int,
    as_of_month: int,
    as_of_half: int,
    spot: float,
    factors: Mapping[str, float],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    curve_config = config["curve"]
    lifecycle = int(config["contract_lifecycle_months"])
    contracts: list[dict[str, Any]] = []
    expiries = _listed_expiries(
        as_of_year,
        as_of_month,
        expiry_months=config["expiry_months"],
        count=int(config["listed_contract_count"]),
    )
    for index, (expiry_year, expiry_month) in enumerate(expiries):
        half_turns_to_expiry = (
            _half_turn_serial(expiry_year, expiry_month, 2)
            - _half_turn_serial(as_of_year, as_of_month, as_of_half)
        )
        months_to_expiry = half_turns_to_expiry / 2.0
        tau = months_to_expiry / 12.0
        log_basis = _log_basis_pct(tau, factors=factors, curve_config=curve_config)
        price = spot * math.exp(log_basis / 100.0)
        basis = price - spot
        listing_year, listing_month = _date_from_serial(
            _month_serial(expiry_year, expiry_month) - (lifecycle - 1)
        )
        contracts.append(
            {
                "contract_id": _delivery_contract_id(expiry_year, expiry_month),
                "code": _delivery_contract_id(expiry_year, expiry_month),
                "name": _delivery_name(expiry_year, expiry_month),
                "listing_year": listing_year,
                "listing_month": listing_month,
                "listing_label": f"{listing_year}-{listing_month:02d}",
                "expiry_year": expiry_year,
                "expiry_month": expiry_month,
                "expiry_label": f"{expiry_year}-{expiry_month:02d}",
                "lifecycle_months": lifecycle,
                "lifecycle_turns": lifecycle * 2,
                "half_turns_to_expiry": half_turns_to_expiry,
                "months_to_expiry": months_to_expiry,
                "futures_price_usd": price,
                "spot_reference_price_usd": spot,
                "basis_usd": basis,
                "basis_pct": 100.0 * basis / spot,
                "annualized_carry_pct": 0.0 if tau <= 0.0 else log_basis / tau,
                "status": "expiring" if months_to_expiry == 0 else "nearest" if index == 0 else "listed",
            }
        )
    return contracts


def _curve_state(contracts: list[Mapping[str, Any]], *, threshold_pct: float) -> str:
    front = float(contracts[0]["futures_price_usd"])
    far = float(contracts[-1]["futures_price_usd"])
    change = 100.0 * (far / front - 1.0)
    if change > threshold_pct:
        return "contango"
    if change < -threshold_pct:
        return "backwardation"
    return "flat"


def _reference_half_turns(
    global_run: GlobalMacroRun,
    *,
    as_of_year: int,
    as_of_month: int,
    as_of_half: int,
) -> list[dict[str, Any]]:
    overlay = run_commodity_overlay(global_run)
    visible: list[dict[str, Any]] = []
    for row in overlay.contracts["brent"]:
        year = int(row["year"])
        if year > as_of_year:
            break
        for month in row.get("monthly", ()):
            month_number = int(month["month"])
            if year == as_of_year and month_number > as_of_month:
                break
            weeks = [
                {
                    "week": int(week["week"]),
                    "open": float(week["open"]),
                    "high": float(week["high"]),
                    "low": float(week["low"]),
                    "close": float(week["close"]),
                }
                for week in month.get("weekly", ())
            ]
            maximum_half = as_of_half if year == as_of_year and month_number == as_of_month else 2
            for half in range(1, maximum_half + 1):
                half_weeks = weeks[(half - 1) * 2 : half * 2]
                if not half_weeks:
                    continue
                visible.append(
                    {
                        "year": year,
                        "month": month_number,
                        "half": half,
                        "open": float(half_weeks[0]["open"]),
                        "high": max(float(week["high"]) for week in half_weeks),
                        "low": min(float(week["low"]) for week in half_weeks),
                        "close": float(half_weeks[-1]["close"]),
                        "weekly": half_weeks,
                    }
                )
    return visible


def _contract_turn_bar(
    reference: Mapping[str, Any],
    contract: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    spot_close = float(reference["close"])
    futures_close = float(contract["futures_price_usd"])
    current_log_basis = math.log(futures_close / spot_close)
    previous_log_basis = current_log_basis if previous is None else math.log(
        float(previous["close"]) / float(previous["spot_close"])
    )
    weeks = list(reference.get("weekly", ()))
    contract_weeks: list[dict[str, Any]] = []
    for index, week in enumerate(weeks):
        start_weight = index / max(1, len(weeks))
        end_weight = (index + 1) / max(1, len(weeks))
        start_basis = previous_log_basis + (current_log_basis - previous_log_basis) * start_weight
        end_basis = previous_log_basis + (current_log_basis - previous_log_basis) * end_weight
        open_px = float(week["open"]) * math.exp(start_basis)
        close_px = float(week["close"]) * math.exp(end_basis)
        upper_factor = math.exp(max(start_basis, end_basis))
        lower_factor = math.exp(min(start_basis, end_basis))
        contract_weeks.append(
            {
                "week": int(week["week"]),
                "open": open_px,
                "high": max(open_px, close_px, float(week["high"]) * upper_factor),
                "low": min(open_px, close_px, float(week["low"]) * lower_factor),
                "close": close_px,
            }
        )
    if contract_weeks:
        open_px = contract_weeks[0]["open"]
        high_px = max(week["high"] for week in contract_weeks)
        low_px = min(week["low"] for week in contract_weeks)
        close_px = contract_weeks[-1]["close"]
    else:
        factor = futures_close / spot_close
        open_px = float(reference["open"]) * factor
        high_px = float(reference["high"]) * factor
        low_px = float(reference["low"]) * factor
        close_px = futures_close
    return {
        "year": int(reference["year"]),
        "month": int(reference["month"]),
        "half": int(reference["half"]),
        "open": open_px,
        "high": max(high_px, open_px, close_px),
        "low": min(low_px, open_px, close_px),
        "close": close_px,
        "weekly": contract_weeks,
        "spot_close": spot_close,
        "basis_pct": float(contract["basis_pct"]),
        "months_to_expiry": float(contract["months_to_expiry"]),
    }


def _multiply_price_history(turns: list[dict[str, Any]], ratio: float) -> None:
    for month in turns:
        for key in ("open", "high", "low", "close"):
            month[key] = float(month[key]) * ratio
        month["adjustment_multiplier"] = float(month.get("adjustment_multiplier", 1.0)) * ratio
        for week in month["weekly"]:
            for key in ("open", "high", "low", "close"):
                week[key] = float(week[key]) * ratio


def _aggregate_turns_to_months(turns: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    months: list[dict[str, Any]] = []
    for turn in turns:
        year = int(turn["year"])
        month = int(turn["month"])
        if not months or (int(months[-1]["year"]), int(months[-1]["month"])) != (year, month):
            months.append(
                {
                    "year": year,
                    "month": month,
                    "visible_half": int(turn["half"]),
                    "open": float(turn["open"]),
                    "high": float(turn["high"]),
                    "low": float(turn["low"]),
                    "close": float(turn["close"]),
                    "weekly": [dict(week) for week in turn.get("weekly", ())],
                }
            )
        else:
            packed = months[-1]
            packed["visible_half"] = int(turn["half"])
            packed["high"] = max(float(packed["high"]), float(turn["high"]))
            packed["low"] = min(float(packed["low"]), float(turn["low"]))
            packed["close"] = float(turn["close"])
            packed["weekly"].extend(dict(week) for week in turn.get("weekly", ()))
        packed = months[-1]
        for source, target in (
            ("source_contract_id", "source_contract_id"),
            ("roll_from_contract_id", "roll_from_contract_id"),
            ("adjustment_multiplier", "adjustment_multiplier"),
        ):
            if turn.get(source) is not None:
                packed[target] = turn[source]
    for month in months:
        weeks = list(month.get("weekly", ()))
        if weeks and all("volume_lots" in week for week in weeks):
            month["volume_lots"] = sum(int(week["volume_lots"]) for week in weeks)
            month["open_interest_lots"] = int(weeks[-1]["open_interest_lots"])
            month["open_interest_change_lots"] = sum(
                int(week["open_interest_change_lots"]) for week in weeks
            )
    return months


def _summary_from_months(months: list[Mapping[str, Any]]) -> dict[str, float]:
    latest = months[-1]
    previous = months[-2] if len(months) > 1 else None
    change = 0.0 if previous is None else 100.0 * (
        float(latest["close"]) / float(previous["close"]) - 1.0
    )
    summary: dict[str, float] = {
        "price_usd": float(latest["close"]),
        "monthly_change_pct": change,
        "monthly_high_usd": float(latest["high"]),
        "monthly_low_usd": float(latest["low"]),
    }
    latest_weeks = list(latest.get("weekly", ()))
    if latest_weeks and "volume_lots" in latest_weeks[-1]:
        summary.update(
            {
                "latest_weekly_volume_lots": float(latest_weeks[-1]["volume_lots"]),
                "open_interest_lots": float(latest_weeks[-1]["open_interest_lots"]),
                "open_interest_change_lots": float(
                    latest_weeks[-1]["open_interest_change_lots"]
                ),
            }
        )
    return summary


def _participant_limits_for_contract(
    *,
    contract: Mapping[str, Any],
    contract_turns: list[Mapping[str, Any]],
    price_usd: float,
    specification: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish the current mega-institution limits for one named future.

    These values are deliberately derived from visible contract liquidity only.
    They are reserved inputs for a later order engine and do not mutate prices,
    volume, open interest, or player state in the read-only market projection.
    """

    visible_weeks = [
        week
        for turn in contract_turns
        for week in turn.get("weekly", ())
        if "volume_lots" in week and "open_interest_lots" in week
    ]
    if not visible_weeks:
        raise ValueError("participant limits require visible futures liquidity")

    reference_week_count = int(policy["turn_volume_reference_weeks"])
    recent_weeks = visible_weeks[-reference_week_count:]
    recent_volume_lots = sum(int(week["volume_lots"]) for week in recent_weeks)
    equivalent_week_count = int(policy["turn_volume_equivalent_weeks"])
    turn_equivalent_volume_lots = int(
        round(recent_volume_lots * equivalent_week_count / len(recent_weeks))
    )
    current_open_interest_lots = int(visible_weeks[-1]["open_interest_lots"])
    open_interest_rate_pct = float(
        policy["single_contract_open_interest_rate_pct"]
    )
    open_interest_share_limit_lots = math.floor(
        current_open_interest_lots * open_interest_rate_pct / 100.0
    )
    hard_cap_lots = int(policy["single_contract_hard_cap_lots"])
    position_limit_lots = min(open_interest_share_limit_lots, hard_cap_lots)
    binding_position_rule = (
        "open_interest_share"
        if open_interest_share_limit_lots <= hard_cap_lots
        else "hard_cap"
    )

    months_to_expiry = float(contract["months_to_expiry"])
    applicable_stepdowns = [
        item
        for item in policy.get("expiry_stepdown", ())
        if months_to_expiry <= float(item["maximum_months_to_expiry"])
    ]
    if applicable_stepdowns:
        expiry_cap_lots = min(
            int(item["position_cap_lots"]) for item in applicable_stepdowns
        )
        if expiry_cap_lots < position_limit_lots:
            position_limit_lots = expiry_cap_lots
            binding_position_rule = "expiry_stepdown"

    new_trades_allowed = not (
        months_to_expiry
        < float(specification["last_tradable_months_before_expiry"])
        and not bool(policy["expiry_month_new_trades_enabled"])
    )
    turn_trade_limit_lots = (
        int(
            round(
                turn_equivalent_volume_lots
                * float(policy["turn_volume_rate_pct"])
                / 100.0
            )
        )
        if new_trades_allowed
        else 0
    )
    position_limit_notional_usd = (
        position_limit_lots
        * float(specification["contract_size_bbl"])
        * float(price_usd)
    )

    return {
        "policy_id": str(policy["policy_id"]),
        "entity_scale": str(policy["entity_scale"]),
        "current_open_interest_lots": current_open_interest_lots,
        "single_contract_open_interest_rate_pct": open_interest_rate_pct,
        "single_contract_oi_share_limit_lots": open_interest_share_limit_lots,
        "single_contract_hard_cap_lots": hard_cap_lots,
        "single_contract_position_limit_lots": position_limit_lots,
        "all_contract_gross_position_cap_lots": int(
            policy["all_contract_gross_position_cap_lots"]
        ),
        "recent_volume_reference_weeks": len(recent_weeks),
        "recent_volume_reference_lots": recent_volume_lots,
        "turn_volume_equivalent_weeks": equivalent_week_count,
        "turn_equivalent_volume_lots": turn_equivalent_volume_lots,
        "turn_volume_rate_pct": float(policy["turn_volume_rate_pct"]),
        "turn_trade_limit_lots": turn_trade_limit_lots,
        "position_limit_notional_usd": position_limit_notional_usd,
        "position_limit_initial_margin_usd": position_limit_notional_usd
        * float(specification["initial_margin_rate_pct"])
        / 100.0,
        "new_trades_allowed": new_trades_allowed,
        "binding_position_rule": binding_position_rule,
        "status": str(policy["status"]),
    }


@deterministic_projection_cache(max_entries=16)
def oil_futures_payload(
    global_run: GlobalMacroRun,
    *,
    as_of_year: int,
    as_of_month: int,
    as_of_half: int = 2,
) -> dict[str, Any]:
    assets = load_registered_assets()
    config = assets["oil_futures_overlay_config"]
    contract = assets["oil_futures_overlay_contract"]
    if config["model_version"] != OIL_FUTURES_MODEL_VERSION:
        raise ValueError("registered oil futures overlay config version mismatch")
    if contract["contract_id"] != "oil_futures_overlay_v8":
        raise ValueError("registered oil futures overlay contract id mismatch")
    specification = config["contract_specification"]
    participant_policy = config["participant_limits"]
    expected_tick_value = (
        float(specification["contract_size_bbl"])
        * float(specification["minimum_price_fluctuation_usd_per_bbl"])
    )
    if not math.isclose(
        float(specification["tick_value_usd_per_lot"]),
        expected_tick_value,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("oil futures tick value does not match contract size and price tick")
    if not 0.0 < float(specification["maintenance_margin_rate_pct"]) < float(
        specification["initial_margin_rate_pct"]
    ) < 100.0:
        raise ValueError("oil futures margin rates are invalid")
    if not 0.0 < float(participant_policy["single_contract_open_interest_rate_pct"]) < 100.0:
        raise ValueError("oil futures participant open-interest limit is invalid")
    if not 0.0 < float(participant_policy["turn_volume_rate_pct"]) < 100.0:
        raise ValueError("oil futures participant turnover limit is invalid")
    if not 1 <= int(participant_policy["turn_volume_equivalent_weeks"]) <= int(
        participant_policy["turn_volume_reference_weeks"]
    ):
        raise ValueError("oil futures participant turnover smoothing window is invalid")
    if not 1 <= int(as_of_month) <= 12:
        raise ValueError("as_of_month must be between 1 and 12")
    if int(as_of_half) not in (1, 2):
        raise ValueError("as_of_half must be 1 or 2")
    years = [int(row["year"]) for row in global_run.rows]
    if int(as_of_year) < min(years) or int(as_of_year) > max(years):
        raise ValueError("as_of_year is outside the generated global run")

    reference_turns = _reference_half_turns(
        global_run,
        as_of_year=int(as_of_year),
        as_of_month=int(as_of_month),
        as_of_half=int(as_of_half),
    )
    if not reference_turns:
        raise ValueError("oil reference has no visible half-month data at the cutoff")

    curve_config = config["curve"]
    liquidity_config = config["liquidity"]
    if str(liquidity_config.get("roll_migration_curve")) != "smoothstep":
        raise ValueError("oil futures roll migration curve must be smoothstep")
    anchor_macro_row = _latest_completed_macro_row(
        global_run.rows, int(liquidity_config["anchor_year"])
    )
    closes: list[float] = []
    factors: dict[str, float] | None = None
    liquidity_state: dict[str, float] | None = None
    liquidity_weeks: list[dict[str, Any]] = []
    previous_reference_week_close: float | None = None
    previous_contract_open_interest: dict[str, int] = {}
    histories: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    main_turns: list[dict[str, Any]] = []
    rolls: list[dict[str, Any]] = []
    previous_main_id: str | None = None
    latest_contracts: list[dict[str, Any]] = []
    latest_inputs: dict[str, float] = {}

    for reference in reference_turns:
        year = int(reference["year"])
        month = int(reference["month"])
        half = int(reference["half"])
        closes.append(float(reference["close"]))
        macro_row = _latest_completed_macro_row(global_run.rows, year)
        targets = _curve_targets(macro_row, closes, month=month, curve_config=curve_config)
        factors = _advance_curve_factors(
            seed=global_run.seed,
            month_address=_half_turn_serial(year, month, half),
            previous=factors,
            targets=targets,
            curve_config=curve_config,
        )
        curve_contracts = _curve_contracts(
            as_of_year=year,
            as_of_month=month,
            as_of_half=half,
            spot=float(reference["close"]),
            factors=factors,
            config=config,
        )
        for item in curve_contracts:
            contract_id = str(item["contract_id"])
            previous_bar = histories[contract_id][-1] if histories[contract_id] else None
            histories[contract_id].append(_contract_turn_bar(reference, item, previous_bar))

        roll_threshold = float(config["main_roll_months_before_expiry"])
        main_item = curve_contracts[1] if float(
            curve_contracts[0]["months_to_expiry"]
        ) < roll_threshold else curve_contracts[0]
        main_id = str(main_item["contract_id"])
        for week_index, reference_week in enumerate(reference.get("weekly", ())):
            week_number = int(reference_week["week"])
            week_address = _week_serial(year, month, week_number)
            liquidity_state = _advance_market_liquidity(
                seed=global_run.seed,
                week_address=week_address,
                macro_row=macro_row,
                anchor_macro_row=anchor_macro_row,
                reference_close=float(reference_week["close"]),
                previous_reference_close=previous_reference_week_close,
                previous=liquidity_state,
                liquidity_config=liquidity_config,
            )
            volume_shares, open_interest_shares = _contract_liquidity_shares(
                curve_contracts,
                main_contract_id=main_id,
                current_year=year,
                current_month=month,
                week=week_number,
                liquidity_config=liquidity_config,
            )
            volume_allocations = _allocate_lots(
                int(liquidity_state["weekly_volume_lots"]),
                curve_contracts,
                volume_shares,
                seed=global_run.seed,
                week_address=week_address,
                stream="contract_volume",
                noise_scale=float(liquidity_config["contract_volume_noise_scale"]),
            )
            open_interest_allocations = _allocate_lots(
                int(liquidity_state["total_open_interest_lots"]),
                curve_contracts,
                open_interest_shares,
                seed=global_run.seed,
                week_address=week_address,
                stream="contract_open_interest",
                noise_scale=float(
                    liquidity_config["contract_open_interest_noise_scale"]
                ),
            )
            for index, item in enumerate(curve_contracts):
                contract_id = str(item["contract_id"])
                contract_week = histories[contract_id][-1]["weekly"][week_index]
                open_interest = int(open_interest_allocations[index])
                previous_open_interest = previous_contract_open_interest.get(contract_id, 0)
                contract_week.update(
                    {
                        "volume_lots": int(volume_allocations[index]),
                        "open_interest_lots": open_interest,
                        "open_interest_change_lots": open_interest
                        - previous_open_interest,
                    }
                )
                previous_contract_open_interest[contract_id] = open_interest
            liquidity_weeks.append(
                {
                    "year": year,
                    "month": month,
                    "week": week_number,
                    "volume_lots": int(liquidity_state["weekly_volume_lots"]),
                    "open_interest_lots": int(
                        liquidity_state["total_open_interest_lots"]
                    ),
                    "turnover_ratio": float(
                        liquidity_state["weekly_turnover_ratio"]
                    ),
                    "structural_scale": float(liquidity_state["structural_scale"]),
                    "activity_scale": float(liquidity_state["activity_scale"]),
                }
            )
            previous_reference_week_close = float(reference_week["close"])
        for item in curve_contracts:
            _refresh_turn_liquidity(histories[str(item["contract_id"])][-1])

        roll_from: str | None = None
        if previous_main_id is not None and main_id != previous_main_id:
            new_history = histories[main_id]
            if len(new_history) < 2 or not main_turns:
                raise ValueError("main roll lacks a common prior settlement")
            old_prior = float(main_turns[-1]["close"])
            new_prior = float(new_history[-2]["close"])
            link_ratio = new_prior / old_prior
            _multiply_price_history(main_turns, link_ratio)
            rolls.append(
                {
                    "year": year,
                    "month": month,
                    "half": half,
                    "label": f"{year}-{month:02d}-H{half}",
                    "from_contract_id": previous_main_id,
                    "to_contract_id": main_id,
                    "old_prior_close_usd": old_prior,
                    "new_prior_close_usd": new_prior,
                    "back_adjustment_ratio": link_ratio,
                }
            )
            roll_from = previous_main_id
        main_bar = copy.deepcopy(histories[main_id][-1])
        main_bar["source_contract_id"] = main_id
        main_bar["roll_from_contract_id"] = roll_from
        main_bar["adjustment_multiplier"] = 1.0
        main_turns.append(main_bar)
        previous_main_id = main_id
        latest_contracts = curve_contracts
        latest_inputs = {**targets, **factors}

    if factors is None or not latest_contracts:
        raise ValueError("oil futures curve did not settle")

    current_contracts: list[dict[str, Any]] = []
    for item in latest_contracts:
        packed = dict(item)
        contract_turns = histories[str(item["contract_id"])]
        packed["visible_turn_count"] = len(contract_turns)
        packed["monthly"] = _aggregate_turns_to_months(contract_turns)
        packed.update(_summary_from_months(packed["monthly"]))
        packed["participantLimits"] = _participant_limits_for_contract(
            contract=packed,
            contract_turns=contract_turns,
            price_usd=float(packed["price_usd"]),
            specification=specification,
            policy=participant_policy,
        )
        packed["is_main_source"] = str(item["contract_id"]) == previous_main_id
        current_contracts.append(packed)

    references = _aggregate_turns_to_months(reference_turns)
    main_months = _aggregate_turns_to_months(main_turns)
    reference_summary = _summary_from_months(references)
    main_summary = _summary_from_months(main_months)
    active_contract = next(
        item for item in current_contracts if item["contract_id"] == previous_main_id
    )
    curve_state = _curve_state(
        current_contracts,
        threshold_pct=float(curve_config["flat_curve_threshold_pct"]),
    )
    result = {
        "schemaVersion": "asset-simulation-oil-futures-response-v8",
        "asOf": {
            "year": int(as_of_year),
            "month": int(as_of_month),
            "half": int(as_of_half),
            "label": f"{int(as_of_year)}-{int(as_of_month):02d}-H{int(as_of_half)}",
        },
        "contractSpecification": dict(specification),
        "participantLimitsPolicy": {
            **dict(participant_policy),
            "applies_to": "futures_only",
            "enforced": False,
        },
        "futuresLiquidity": {
            "market": "global_oil_benchmark",
            "spot_included": False,
            "weekly": liquidity_weeks,
            "latest": dict(liquidity_weeks[-1]),
        },
        "reference": {
            "instrument_id": "OIL-REF",
            "code": "SPOT",
            "name": "原油现货参考",
            "unit": "usd_per_bbl",
            "tradable": False,
            "monthly": references,
            **reference_summary,
        },
        "curve": {
            "state": curve_state,
            "nearest_contract_id": current_contracts[0]["contract_id"],
            "main_contract_id": previous_main_id,
            "far_contract_id": current_contracts[-1]["contract_id"],
            "contracts": current_contracts,
            "inputs": latest_inputs,
        },
        "mainContinuous": {
            "instrument_id": "OIL-MAIN",
            "code": "MAIN",
            "name": "原油主连",
            "unit": "usd_per_bbl",
            "active_contract_id": previous_main_id,
            "adjustment": "backward_ratio_adjusted",
            "roll_months_before_expiry": int(config["main_roll_months_before_expiry"]),
            "rolls": rolls,
            "tradable": False,
            "monthly": main_months,
            "participantLimits": copy.deepcopy(active_contract["participantLimits"]),
            **main_summary,
        },
    }
    identity = {
        "schema_version": "asset-simulation-oil-futures-identity-v8",
        "model_version": OIL_FUTURES_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_futures_overlay_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_futures_overlay_contract_hash"],
        "upstream_global_identity_hash": global_run.identity["identity_hash"],
        "information_cutoff": "visible_half_month_weeks_and_latest_completed_annual_macro_row",
        "listed_contract_count": int(config["listed_contract_count"]),
        "contract_lifecycle_months": int(config["contract_lifecycle_months"]),
        "contract_lifecycle_turns": int(config["contract_lifecycle_months"]) * 2,
        "turns_per_year": 24,
        "curve_factors": ["long_slope_pct", "near_pressure_pct", "curvature_pct"],
        "liquidity_model": "bounded_global_liquidity_with_eight_week_smoothstep_roll",
        "liquidity_scope": "futures_only_spot_unchanged",
        "participant_limits_policy_id": str(participant_policy["policy_id"]),
        "participant_limits_status": str(participant_policy["status"]),
        "player_price_feedback": False,
        "write_back": False,
        "orders_enabled": False,
        "result_hash": sha256_json(result),
    }
    return _round_nested({"ok": True, "identity": identity, **result})
