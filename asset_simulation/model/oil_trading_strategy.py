"""Capacity-constrained half-month oil trading strategy simulation.

The strategy layer consumes only the visible futures payload, one published
short-term forecast vintage, and the current account state.  It never receives
the hidden future path.  A separate settlement step advances the world by one
half-month, derives one neutral aggregate execution price from the newly
realized weekly bars, and marks named-contract positions to market.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .engine import GlobalMacroRun
from .math_utils import clamp
from .oil_futures_overlay import oil_futures_payload
from .oil_short_term_forecast import (
    build_institution_profile,
    generate_oil_short_term_forecast,
)
from .oil_strategy_research import (
    resolve_oil_strategy_research_profile,
    resolve_oil_strategy_runtime_policy,
)
from .oil_execution_desk import (
    adjust_visible_execution_weights,
    resolve_oil_execution_runtime_policy,
)
from .corporate_risk_control import (
    approve_oil_strategy_targets,
    resolve_corporate_risk_profile,
)
from .oil_strategy_risk import (
    apply_oil_strategy_risk_mandate,
    build_investment_committee_strategy_approval,
    build_oil_strategy_risk_review,
)
from .oil_strategy_thesis import (
    apply_oil_strategy_thesis_invalidation,
    evaluate_oil_strategy_thesis_state,
    resolve_oil_strategy_thesis_state,
)
from .registry import load_registered_assets, sha256_json


OIL_TRADING_STRATEGY_MODEL_VERSION = (
    "asset-simulation-oil-trading-strategy-v1.1.0"
)
STRATEGY_CONTRACT_ID = "oil_trading_strategy_v8"
ROLE_ORDER = ("main", "next_main")


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("oil trading strategy contains a non-finite value")
        return round(value, 8)
    return value


def _month_serial(year: int, month: int) -> int:
    return int(year) * 12 + int(month) - 1


def _date_from_month_serial(serial: int) -> tuple[int, int]:
    return int(serial) // 12, int(serial) % 12 + 1


def _half_turn_serial(year: int, month: int, half: int) -> int:
    return _month_serial(year, month) * 2 + int(half) - 1


def _turn_from_serial(serial: int) -> tuple[int, int, int]:
    month_serial, half_index = divmod(int(serial), 2)
    year, month = _date_from_month_serial(month_serial)
    return year, month, half_index + 1


def _week_serial(year: int, month: int, week: int) -> int:
    return _month_serial(year, month) * 4 + int(week) - 1


def _cutoff_week_serial(year: int, month: int, half: int) -> int:
    return _week_serial(year, month, 2 if int(half) == 1 else 4)


def _validate_registered_assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_trading_strategy_config"]
    contract = assets["oil_trading_strategy_contract"]
    if config["model_version"] != OIL_TRADING_STRATEGY_MODEL_VERSION:
        raise ValueError("registered oil trading strategy config version mismatch")
    if contract["contract_id"] != STRATEGY_CONTRACT_ID:
        raise ValueError("registered oil trading strategy contract id mismatch")
    signal = config["signal"]
    continuation_component_weights = (
        float(signal["continuation_visible_trend_weight"]),
        float(signal["continuation_forecast_path_weight"]),
    )
    if (
        float(signal["minimum_uncertainty_log"]) <= 0.0
        or float(signal["path_direction_full_strength_z"]) <= 0.0
        or int(signal["visible_trend_lookback_weeks"]) < 2
        or float(signal["visible_trend_full_strength_range_multiples"]) <= 0.0
        or any(value < 0.0 for value in continuation_component_weights)
        or not math.isclose(sum(continuation_component_weights), 1.0)
    ):
        raise ValueError("oil trading strategy signal normalization is invalid")
    thesis = config["thesis_invalidation"]
    scales = {
        key: float(value)
        for key, value in thesis["status_target_scale"].items()
    }
    if (
        set(scales) != {"active", "watch", "invalidated"}
        or not 0.0 < scales["invalidated"] <= scales["watch"] <= scales["active"]
        or not math.isclose(scales["active"], 1.0)
        or int(thesis["consecutive_failure_turns_to_invalidate"]) < 1
        or float(thesis["severe_band_breach_z"]) <= 0.0
        or float(thesis["minimum_direction_move_log"]) <= 0.0
        or not 0.0 <= float(thesis["direction_reversal_signal_threshold"]) <= 1.0
        or bool(thesis["ability_score_used"])
    ):
        raise ValueError("oil trading strategy thesis invalidation config is invalid")
    if config.get("strategy_research_profile_owner") != "oil_strategy_research_v2":
        raise ValueError("oil trading strategy research profile owner mismatch")
    if config.get("strategy_risk_owner") != "oil_strategy_risk_v1":
        raise ValueError("oil trading strategy risk owner mismatch")
    if config.get("execution_desk_profile_owner") != "oil_execution_desk_v1":
        raise ValueError("oil trading strategy execution profile owner mismatch")
    if config.get("corporate_risk_profile_owner") != "corporate_risk_control_v2":
        raise ValueError("oil trading strategy corporate risk profile owner mismatch")
    if config.get("investment_decision_owner") != (
        "investment_decision_committee_system_proxy"
    ):
        raise ValueError("oil trading strategy investment decision owner mismatch")
    execution = config["execution"]
    if not 0.0 < float(
        execution["forced_reduction_trade_limit_utilization"]
    ) <= 1.0:
        raise ValueError(
            "oil trading strategy forced reduction utilization must be in (0, 1]"
        )
    if execution["aggregate_price_method"] != (
        "newly_realized_weekly_ohlc4_volume_weighted"
    ):
        raise ValueError("oil trading strategy aggregate price method is unsupported")
    if execution["intrawindow_path_method"] != (
        "ohlc_directional_canonical_path_with_penetration"
    ):
        raise ValueError("oil trading strategy intrawindow path method is unsupported")
    if not bool(execution["friction_enabled"]) or not bool(execution["fees_enabled"]):
        raise ValueError("oil trading strategy v8 requires friction and fees")
    friction = config["execution_friction"]
    spread = friction["spread"]
    expected_roles = set(ROLE_ORDER) | {"legacy_exit"}
    for key in ("base_full_spread_ticks", "reference_weekly_volume_lots"):
        if set(spread[key]) != expected_roles or any(
            float(value) <= 0.0 for value in spread[key].values()
        ):
            raise ValueError(f"oil trading strategy spread {key} is invalid")
    for key in ("liquidity_multiplier_bounds", "volatility_multiplier_bounds"):
        low, high = map(float, spread[key])
        if not 0.0 < low <= high:
            raise ValueError(f"oil trading strategy spread {key} is invalid")
    tick_low, tick_high = map(int, spread["full_spread_tick_bounds"])
    if tick_low <= 0 or tick_high < tick_low:
        raise ValueError("oil trading strategy spread tick bounds are invalid")
    if any(
        float(spread[key]) <= 0.0
        for key in (
            "liquidity_exponent",
            "volatility_exponent",
            "reference_weekly_volatility",
        )
    ):
        raise ValueError("oil trading strategy spread curve is invalid")
    slippage = friction["slippage"]
    if any(
        float(slippage[key]) <= 0.0
        for key in (
            "impact_coefficient",
            "urgency_multiplier_min",
            "urgency_multiplier_max",
            "maximum_slippage_bps_per_side",
        )
    ) or float(slippage["urgency_multiplier_min"]) > float(
        slippage["urgency_multiplier_max"]
    ):
        raise ValueError("oil trading strategy slippage config is invalid")
    fees = friction["fees"]
    fee_components = {
        "exchange": float(fees["exchange_fee_usd_per_lot_side"]),
        "clearing": float(fees["clearing_fee_usd_per_lot_side"]),
        "broker": float(fees["broker_fee_usd_per_lot_side"]),
    }
    if any(value < 0.0 for value in fee_components.values()) or float(
        fees["cash_settlement_fee_usd_per_lot"]
    ) < 0.0:
        raise ValueError("oil trading strategy fee components are invalid")
    eligible = set(fees["rebate_eligible_components"])
    if not eligible or not eligible <= set(fee_components):
        raise ValueError("oil trading strategy rebate components are invalid")
    tiers = list(fees["rebate_tiers"])
    thresholds = [int(item["minimum_trailing_gross_lots"]) for item in tiers]
    rates = [float(item["rebate_rate"]) for item in tiers]
    if (
        not tiers
        or thresholds[0] != 0
        or thresholds != sorted(set(thresholds))
        or any(value < 0 for value in thresholds)
        or any(not 0.0 <= value <= 1.0 for value in rates)
        or int(fees["rebate_lookback_turns"]) <= 0
    ):
        raise ValueError("oil trading strategy rebate tiers are invalid")
    round_trip_execution = friction["round_trip_execution"]
    if any(
        float(round_trip_execution[key]) <= 0.0
        for key in (
            "edge_quality_full_strength_pct",
            "budget_utilization_exponent",
            "entry_full_fill_range_fraction",
            "exit_full_fill_range_fraction",
        )
    ):
        raise ValueError("oil trading strategy round-trip execution curve is invalid")
    if any(
        not 0.0 < float(round_trip_execution[key]) <= 1.0
        for key in (
            "entry_full_fill_range_fraction",
            "exit_full_fill_range_fraction",
        )
    ):
        raise ValueError("oil trading strategy penetration bounds are invalid")
    if not 0.0 <= float(
        round_trip_execution["signal_utilization_floor"]
    ) <= 1.0:
        raise ValueError("oil trading strategy round-trip execution bounds are invalid")
    return assets, config, contract


def _resolve_fee_profile(
    fee_state: Mapping[str, Any] | None,
    friction_config: Mapping[str, Any],
    execution_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fees = friction_config["fees"]
    raw_state = dict(fee_state or {})
    rolling_lots = raw_state.get("rolling_gross_turnover_lots", 0)
    if isinstance(rolling_lots, bool) or not isinstance(rolling_lots, int):
        raise ValueError("oil strategy rolling fee volume must be integer lots")
    if rolling_lots < 0:
        raise ValueError("oil strategy rolling fee volume cannot be negative")
    selected = dict(fees["rebate_tiers"][0])
    for tier in fees["rebate_tiers"]:
        if rolling_lots >= int(tier["minimum_trailing_gross_lots"]):
            selected = dict(tier)
    components = {
        "exchange": float(fees["exchange_fee_usd_per_lot_side"]),
        "clearing": float(fees["clearing_fee_usd_per_lot_side"]),
        "broker": float(fees["broker_fee_usd_per_lot_side"]),
    }
    fee_policy = dict((execution_policy or {}).get("fee_efficiency", {}))
    broker_multiplier = float(fee_policy.get("broker_fee_multiplier", 1.0))
    rebate_realization = float(
        fee_policy.get("rebate_realization_multiplier", 1.0)
    )
    components["broker"] *= broker_multiplier
    eligible_components = list(fees["rebate_eligible_components"])
    gross_fee_per_side = sum(components.values())
    eligible_fee_per_side = sum(
        components[key] for key in eligible_components
    )
    rebate_rate = clamp(float(selected["rebate_rate"]) * rebate_realization, 0.0, 1.0)
    rebate_per_side = eligible_fee_per_side * rebate_rate
    net_fee_per_side = max(0.0, gross_fee_per_side - rebate_per_side)
    return {
        "rolling_gross_turnover_lots": rolling_lots,
        "lookback_turns": int(fees["rebate_lookback_turns"]),
        "tier_minimum_gross_lots": int(
            selected["minimum_trailing_gross_lots"]
        ),
        "rebate_rate": rebate_rate,
        "fee_components_usd_per_lot_side": components,
        "rebate_eligible_components": eligible_components,
        "gross_fee_usd_per_lot_side": gross_fee_per_side,
        "eligible_fee_usd_per_lot_side": eligible_fee_per_side,
        "rebate_usd_per_lot_side": rebate_per_side,
        "net_fee_usd_per_lot_side": net_fee_per_side,
        "cash_settlement_fee_usd_per_lot": float(
            fees["cash_settlement_fee_usd_per_lot"]
        ),
        "broker_fee_multiplier": broker_multiplier,
        "rebate_realization_multiplier": rebate_realization,
        "hard_exchange_and_clearing_fees_modified": False,
    }


def _weekly_volatility(week: Mapping[str, Any]) -> float:
    open_price = float(week["open"])
    high_price = float(week["high"])
    low_price = float(week["low"])
    close_price = float(week["close"])
    if min(open_price, high_price, low_price, close_price) <= 0.0:
        raise ValueError("oil strategy weekly OHLC prices must be positive")
    log_range = math.log(high_price / low_price)
    log_close_open = math.log(close_price / open_price)
    variance = 0.5 * log_range**2 - (2.0 * math.log(2.0) - 1.0) * (
        log_close_open**2
    )
    return math.sqrt(max(1e-10, variance))


def _weekly_spread_profile(
    role: str,
    week: Mapping[str, Any],
    *,
    tick_size_usd: float,
    friction_config: Mapping[str, Any],
) -> dict[str, float | int]:
    spread = friction_config["spread"]
    resolved_role = role if role in spread["base_full_spread_ticks"] else "legacy_exit"
    volume = max(1.0, float(week.get("volume_lots", 0)))
    reference_volume = float(
        spread["reference_weekly_volume_lots"][resolved_role]
    )
    volatility = _weekly_volatility(week)
    liquidity_low, liquidity_high = map(
        float, spread["liquidity_multiplier_bounds"]
    )
    volatility_low, volatility_high = map(
        float, spread["volatility_multiplier_bounds"]
    )
    liquidity_multiplier = clamp(
        (reference_volume / volume) ** float(spread["liquidity_exponent"]),
        liquidity_low,
        liquidity_high,
    )
    volatility_multiplier = clamp(
        (
            volatility / float(spread["reference_weekly_volatility"])
        )
        ** float(spread["volatility_exponent"]),
        volatility_low,
        volatility_high,
    )
    tick_low, tick_high = map(int, spread["full_spread_tick_bounds"])
    raw_ticks = (
        float(spread["base_full_spread_ticks"][resolved_role])
        * liquidity_multiplier
        * volatility_multiplier
    )
    full_spread_ticks = int(
        clamp(float(math.ceil(raw_ticks)), float(tick_low), float(tick_high))
    )
    return {
        "full_spread_ticks": full_spread_ticks,
        "full_spread_usd_per_bbl": full_spread_ticks * float(tick_size_usd),
        "weekly_volatility": volatility,
        "weekly_volatility_bps": 10_000.0 * volatility,
        "liquidity_multiplier": liquidity_multiplier,
        "volatility_multiplier": volatility_multiplier,
    }


def _slippage_bps(
    side_lots: int,
    market_volume_lots: int,
    weekly_volatility: float,
    turnover_profile: Mapping[str, Any],
    friction_config: Mapping[str, Any],
    *,
    execution_policy: Mapping[str, Any] | None = None,
    role: str = "main",
) -> dict[str, float | bool]:
    slippage = friction_config["slippage"]
    lots = max(0, int(side_lots))
    volume = max(1, int(market_volume_lots))
    participation = lots / volume
    normalized = float(turnover_profile["normalized_intensity"])
    urgency = float(slippage["urgency_multiplier_min"]) + normalized * (
        float(slippage["urgency_multiplier_max"])
        - float(slippage["urgency_multiplier_min"])
    )
    impact_multiplier = float(
        dict((execution_policy or {}).get("impact_control", {})).get(
            "slippage_multiplier", 1.0
        )
    )
    roll_multiplier = (
        float(
            dict((execution_policy or {}).get("roll_coordination", {})).get(
                "roll_cost_multiplier", 1.0
            )
        )
        if role == "legacy_exit"
        else 1.0
    )
    raw_bps = (
        float(slippage["impact_coefficient"])
        * float(weekly_volatility)
        * 10_000.0
        * math.sqrt(participation)
        * urgency
        * impact_multiplier
        * roll_multiplier
    )
    maximum = float(slippage["maximum_slippage_bps_per_side"])
    return {
        "participation_rate": participation,
        "urgency_multiplier": urgency,
        "execution_desk_impact_multiplier": impact_multiplier,
        "roll_coordination_multiplier": roll_multiplier,
        "raw_slippage_bps": raw_bps,
        "slippage_bps": min(raw_bps, maximum),
        "slippage_capped": raw_bps > maximum,
    }


def _execution_cost_bucket(
    *,
    buy_lots: int,
    sell_lots: int,
    raw_buy_notional_usd: float,
    raw_sell_notional_usd: float,
    full_spread_usd_per_bbl: float,
    buy_slippage_bps: float,
    sell_slippage_bps: float,
    contract_size_bbl: float,
    fee_profile: Mapping[str, Any],
    execution_policy: Mapping[str, Any] | None = None,
    role: str = "main",
) -> dict[str, float | int]:
    """Attribute pooled side execution costs to one economic trade bucket."""

    resolved_buy_lots = max(0, int(buy_lots))
    resolved_sell_lots = max(0, int(sell_lots))
    gross_turnover = resolved_buy_lots + resolved_sell_lots
    spread_multiplier = float(
        dict((execution_policy or {}).get("price_execution", {})).get(
            "spread_cost_multiplier", 1.0
        )
    )
    roll_multiplier = (
        float(
            dict((execution_policy or {}).get("roll_coordination", {})).get(
                "roll_cost_multiplier", 1.0
            )
        )
        if role == "legacy_exit"
        else 1.0
    )
    spread_cost = (
        gross_turnover
        * 0.5
        * float(full_spread_usd_per_bbl)
        * float(contract_size_bbl)
        * spread_multiplier
        * roll_multiplier
    )
    slippage_cost = (
        float(raw_buy_notional_usd) * float(buy_slippage_bps) / 10_000.0
        + float(raw_sell_notional_usd)
        * float(sell_slippage_bps)
        / 10_000.0
    )
    gross_fee = gross_turnover * float(
        fee_profile["gross_fee_usd_per_lot_side"]
    )
    eligible_fee = gross_turnover * float(
        fee_profile["eligible_fee_usd_per_lot_side"]
    )
    fee_rebate = min(
        gross_fee,
        eligible_fee * float(fee_profile["rebate_rate"]),
    )
    net_fee = max(0.0, gross_fee - fee_rebate)
    execution_cost = spread_cost + slippage_cost + net_fee
    return {
        "buy_lots": resolved_buy_lots,
        "sell_lots": resolved_sell_lots,
        "gross_turnover_lots": gross_turnover,
        "raw_buy_notional_usd": float(raw_buy_notional_usd),
        "raw_sell_notional_usd": float(raw_sell_notional_usd),
        "spread_cost_usd": spread_cost,
        "slippage_cost_usd": slippage_cost,
        "gross_fee_usd": gross_fee,
        "fee_rebate_usd": fee_rebate,
        "net_fee_usd": net_fee,
        "execution_cost_usd": execution_cost,
        "spread_cost_multiplier": spread_multiplier,
        "roll_coordination_multiplier": roll_multiplier,
    }


def _estimate_round_trip_cost(
    role: str,
    visible_week: Mapping[str, Any],
    *,
    estimated_side_lots: int,
    forecast_price_usd: float,
    contract_size_bbl: float,
    tick_size_usd: float,
    turnover_profile: Mapping[str, Any],
    fee_profile: Mapping[str, Any],
    friction_config: Mapping[str, Any],
    execution_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    spread = _weekly_spread_profile(
        role,
        visible_week,
        tick_size_usd=tick_size_usd,
        friction_config=friction_config,
    )
    slippage = _slippage_bps(
        estimated_side_lots,
        int(visible_week.get("volume_lots", 0)),
        float(spread["weekly_volatility"]),
        turnover_profile,
        friction_config,
        execution_policy=execution_policy,
        role=role,
    )
    price = max(1e-9, float(forecast_price_usd))
    spread_multiplier = float(
        dict((execution_policy or {}).get("price_execution", {})).get(
            "spread_cost_multiplier", 1.0
        )
    )
    roll_multiplier = (
        float(
            dict((execution_policy or {}).get("roll_coordination", {})).get(
                "roll_cost_multiplier", 1.0
            )
        )
        if role == "legacy_exit"
        else 1.0
    )
    spread_cost_pct = (
        float(spread["full_spread_usd_per_bbl"])
        * spread_multiplier
        * roll_multiplier
        / price
        * 100.0
    )
    slippage_cost_pct = 2.0 * float(slippage["slippage_bps"]) / 100.0
    fee_cost_pct = (
        2.0
        * float(fee_profile["net_fee_usd_per_lot_side"])
        / (price * float(contract_size_bbl))
        * 100.0
    )
    total = spread_cost_pct + slippage_cost_pct + fee_cost_pct
    return {
        "estimated_round_trip_cost_pct": total,
        "estimated_spread_cost_pct": spread_cost_pct,
        "estimated_slippage_cost_pct": slippage_cost_pct,
        "estimated_net_fee_cost_pct": fee_cost_pct,
        "estimated_side_lots": int(estimated_side_lots),
        "visible_volume_lots": int(visible_week.get("volume_lots", 0)),
        "spread": spread,
        "slippage": slippage,
    }


def _contract_map(market: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item["contract_id"]): item
        for item in market["curve"]["contracts"]
    }


def _reference_price(market: Mapping[str, Any]) -> float:
    price = float(market.get("reference", {}).get("price_usd", 0.0))
    if not math.isfinite(price) or price <= 0.0:
        raise ValueError("oil strategy market lacks a positive reference price")
    return price


def _assign_roll_transfer_attribution(
    reports: list[dict[str, Any]],
) -> int:
    """Match old-contract exits to same-direction main-contract openings."""

    for report in reports:
        report["roll_transfer_lots"] = 0
    matched_total = 0
    for direction in (-1, 1):
        exits: list[list[Any]] = []
        destinations: list[list[Any]] = []
        for report in reports:
            start = int(report["starting_position_lots"])
            end = int(report["ending_position_lots"])
            executed = int(report["executed_delta_lots"])
            if str(report["role"]) == "legacy_exit" and direction * start > 0:
                position_reduction = direction * start - max(0, direction * end)
                exit_amount = max(
                    0,
                    min(abs(end - start), position_reduction),
                )
                if exit_amount:
                    exits.append([report, exit_amount])
            if str(report["role"]) == "main" and direction * executed > 0:
                opening_amount = max(
                    0,
                    min(abs(executed), direction * end - max(0, direction * start)),
                )
                if opening_amount:
                    destinations.append([report, opening_amount])
        exit_index = 0
        destination_index = 0
        while exit_index < len(exits) and destination_index < len(destinations):
            amount = min(exits[exit_index][1], destinations[destination_index][1])
            if amount <= 0:
                break
            exits[exit_index][0]["roll_transfer_lots"] += amount
            destinations[destination_index][0]["roll_transfer_lots"] += amount
            exits[exit_index][1] -= amount
            destinations[destination_index][1] -= amount
            matched_total += amount
            if exits[exit_index][1] == 0:
                exit_index += 1
            if destinations[destination_index][1] == 0:
                destination_index += 1
    for report in reports:
        net_delta = abs(int(report["executed_delta_lots"]))
        roll_share = (
            0.0
            if net_delta == 0
            else min(1.0, int(report["roll_transfer_lots"]) / net_delta)
        )
        net_gross = float(report["net_adjustment_gross_pnl_usd"])
        net_cost = float(report["net_adjustment_execution_cost_usd"])
        report["roll_execution_share"] = roll_share
        report["roll_execution_gross_pnl_usd"] = net_gross * roll_share
        report["roll_execution_cost_usd"] = net_cost * roll_share
        settlement_delta = abs(int(report.get("settlement_delta_lots", 0)))
        settlement_roll_share = (
            0.0
            if settlement_delta == 0
            else min(1.0, int(report["roll_transfer_lots"]) / settlement_delta)
        )
        report["roll_cash_settlement_fee_usd"] = (
            float(report.get("cash_settlement_fee_usd", 0.0))
            * settlement_roll_share
        )
        report["nonroll_cash_settlement_fee_usd"] = (
            float(report.get("cash_settlement_fee_usd", 0.0))
            - report["roll_cash_settlement_fee_usd"]
        )
        report["directional_rebalance_gross_pnl_usd"] = (
            net_gross - report["roll_execution_gross_pnl_usd"]
        )
        report["directional_rebalance_execution_cost_usd"] = (
            net_cost - report["roll_execution_cost_usd"]
        )
    return matched_total


def _validate_cutoff(year: int, month: int, half: int) -> None:
    if not 1 <= int(month) <= 12:
        raise ValueError("strategy month must be between 1 and 12")
    if int(half) not in (1, 2):
        raise ValueError("strategy half must be 1 or 2")


def _signal_from_contract_forecast(
    forecast: Mapping[str, Any],
    signal_config: Mapping[str, Any],
    turnover_profile: Mapping[str, Any] | None = None,
    visible_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    bars = sorted(
        (dict(item) for item in forecast.get("weekly", ())),
        key=lambda item: int(item["horizon_weeks"]),
    )
    anchor = float(forecast["anchor_price_usd"])
    reversion_weight = float(signal_config["reversion_weight"])
    continuation_weight = float(signal_config["continuation_weight"])
    if (
        min(reversion_weight, continuation_weight) < 0.0
        or not math.isclose(reversion_weight + continuation_weight, 1.0)
    ):
        raise ValueError("oil strategy continuation-reversion weights are invalid")
    if anchor <= 0.0 or not bars:
        return {
            "signal": 0.0,
            "raw_signal": 0.0,
            "reversion_signal": 0.0,
            "continuation_signal": 0.0,
            "visible_trend_signal": 0.0,
            "visible_trend": {"available": False, "signal": 0.0},
            "reversion_weight": reversion_weight,
            "continuation_weight": continuation_weight,
            "band_location_signal": 0.0,
            "path_direction_signal": 0.0,
            "path_direction": {"available": False, "signal": 0.0},
            "anchor_price_usd": anchor,
            "horizon_components": [],
        }

    grouped: dict[int, dict[str, Any]] = {}
    horizons = list(signal_config["horizon_weeks"])
    weights = list(signal_config["horizon_weights"])
    for requested_horizon, weight in zip(horizons, weights):
        selected = next(
            (
                item
                for item in bars
                if int(item["horizon_weeks"]) >= int(requested_horizon)
            ),
            bars[-1],
        )
        address = int(selected["week_serial"])
        packed = grouped.setdefault(
            address,
            {
                "requested_horizons": [],
                "weight": 0.0,
                "bar": selected,
            },
        )
        packed["requested_horizons"].append(int(requested_horizon))
        packed["weight"] += float(weight)

    minimum_uncertainty = float(signal_config["minimum_uncertainty_log"])
    path_full_strength_z = float(signal_config["path_direction_full_strength_z"])
    components: list[dict[str, Any]] = []
    location_signal = 0.0
    for packed in grouped.values():
        bar = packed["bar"]
        center_log_return = math.log(float(bar["close"]) / anchor)
        uncertainty_log = max(
            minimum_uncertainty,
            0.5
            * math.log(
                float(bar["confidence_high"])
                / float(bar["confidence_low"])
            ),
        )
        band_position = clamp(-center_log_return / uncertainty_log, -1.0, 1.0)
        component_signal = -band_position
        location_signal += float(packed["weight"]) * component_signal
        components.append(
            {
                "requested_horizons": packed["requested_horizons"],
                "selected_horizon_weeks": int(bar["horizon_weeks"]),
                "target_week": str(bar["target_week"]),
                "weight": float(packed["weight"]),
                "forecast_close_usd": float(bar["close"]),
                "confidence_low_usd": float(bar["confidence_low"]),
                "confidence_high_usd": float(bar["confidence_high"]),
                "center_log_return": center_log_return,
                "uncertainty_log": uncertainty_log,
                "current_band_position": band_position,
                "band_location_signal": component_signal,
            }
        )

    path_direction_signal = 0.0
    path_direction_available = False
    path_report: dict[str, Any] = {
        "available": False,
        "lower_bound_shift_log": 0.0,
        "upper_bound_shift_log": 0.0,
        "coherent_shift_log": 0.0,
        "normalization_log": 0.0,
        "signal": 0.0,
    }
    ordered_components = sorted(
        components, key=lambda item: int(item["selected_horizon_weeks"])
    )
    if (
        len(ordered_components) >= 2
        and int(ordered_components[0]["selected_horizon_weeks"])
        < int(ordered_components[-1]["selected_horizon_weeks"])
    ):
        near = ordered_components[0]
        far = ordered_components[-1]
        lower_shift = math.log(
            float(far["confidence_low_usd"])
            / float(near["confidence_low_usd"])
        )
        upper_shift = math.log(
            float(far["confidence_high_usd"])
            / float(near["confidence_high_usd"])
        )
        coherent_shift = 0.0
        if lower_shift > 0.0 and upper_shift > 0.0:
            coherent_shift = min(lower_shift, upper_shift)
        elif lower_shift < 0.0 and upper_shift < 0.0:
            coherent_shift = max(lower_shift, upper_shift)
        normalization = max(
            minimum_uncertainty,
            0.5
            * (
                float(near["uncertainty_log"])
                + float(far["uncertainty_log"])
            ),
        )
        path_direction_signal = clamp(
            coherent_shift / normalization / path_full_strength_z,
            -1.0,
            1.0,
        )
        path_direction_available = not math.isclose(coherent_shift, 0.0, abs_tol=1e-12)
        path_report = {
            "available": True,
            "coherent": path_direction_available,
            "near_horizon_weeks": int(near["selected_horizon_weeks"]),
            "far_horizon_weeks": int(far["selected_horizon_weeks"]),
            "lower_bound_shift_log": lower_shift,
            "upper_bound_shift_log": upper_shift,
            "coherent_shift_log": coherent_shift,
            "normalization_log": normalization,
            "signal": path_direction_signal,
        }

    visible_weeks = []
    if visible_contract is not None:
        visible_weeks = [
            dict(week)
            for month in visible_contract.get("monthly", ())
            for week in month.get("weekly", ())
        ]
    lookback = int(signal_config["visible_trend_lookback_weeks"])
    recent_visible = visible_weeks[-lookback:]
    visible_trend_available = len(recent_visible) >= 2
    visible_trend_signal = 0.0
    visible_trend_log = 0.0
    visible_range_log = 0.0
    if visible_trend_available:
        first_close = float(recent_visible[0]["close"])
        last_close = float(recent_visible[-1]["close"])
        if first_close > 0.0 and last_close > 0.0:
            visible_trend_log = math.log(last_close / first_close)
            range_logs = [
                math.log(float(week["high"]) / float(week["low"]))
                for week in recent_visible
                if float(week["low"]) > 0.0
                and float(week["high"]) >= float(week["low"])
            ]
            visible_range_log = (
                sum(range_logs) / len(range_logs)
                if range_logs
                else minimum_uncertainty
            )
            visible_trend_signal = clamp(
                visible_trend_log
                / max(
                    minimum_uncertainty,
                    visible_range_log
                    * float(
                        signal_config[
                            "visible_trend_full_strength_range_multiples"
                        ]
                    ),
                ),
                -1.0,
                1.0,
            )
        else:
            visible_trend_available = False
    visible_trend_report = {
        "available": visible_trend_available,
        "lookback_weeks": len(recent_visible),
        "trend_log": visible_trend_log,
        "average_weekly_range_log": visible_range_log,
        "signal": visible_trend_signal,
    }

    continuation_components = []
    if visible_trend_available:
        continuation_components.append(
            (
                visible_trend_signal,
                float(signal_config["continuation_visible_trend_weight"]),
            )
        )
    if path_direction_available:
        continuation_components.append(
            (
                path_direction_signal,
                float(signal_config["continuation_forecast_path_weight"]),
            )
        )
    continuation_weight_total = sum(weight for _, weight in continuation_components)
    continuation_signal = (
        0.0
        if continuation_weight_total <= 0.0
        else sum(value * weight for value, weight in continuation_components)
        / continuation_weight_total
    )

    raw_signal = (
        reversion_weight * location_signal
        + continuation_weight * continuation_signal
    )

    deadband = float(
        signal_config["deadband_abs_signal"]
        if turnover_profile is None
        else turnover_profile["signal_deadband_abs"]
    )
    if abs(raw_signal) <= deadband:
        signal = 0.0
    else:
        signal = math.copysign(
            (abs(raw_signal) - deadband) / max(1e-9, 1.0 - deadband),
            raw_signal,
        )
    return {
        "signal": clamp(signal, -1.0, 1.0),
        "raw_signal": raw_signal,
        "reversion_signal": location_signal,
        "continuation_signal": continuation_signal,
        "visible_trend_signal": visible_trend_signal,
        "visible_trend": visible_trend_report,
        "reversion_weight": reversion_weight,
        "continuation_weight": continuation_weight,
        "band_location_signal": location_signal,
        "path_direction_signal": path_direction_signal,
        "path_direction": path_report,
        "anchor_price_usd": anchor,
        "signal_deadband_abs": deadband,
        "horizon_components": components,
    }


def _build_weekly_turnover_setups(
    forecast: Mapping[str, Any],
    turnover_profile: Mapping[str, Any],
    *,
    parent_signal: float,
    execution_weights: list[float],
    visible_reference_weeks: list[Mapping[str, Any]],
    role: str,
    gross_turnover_budget_lots: int,
    market_gross_execution_limit_lots: int,
    contract_size_bbl: float,
    tick_size_usd: float,
    fee_profile: Mapping[str, Any],
    friction_config: Mapping[str, Any],
    execution_policy: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Freeze the next two weekly directional round-trip setups at decision time."""

    minimum_edge_pct = float(turnover_profile["minimum_trade_edge_pct"])
    setups: list[dict[str, Any]] = []
    weekly = sorted(
        (dict(item) for item in forecast.get("weekly", ())),
        key=lambda item: int(item["horizon_weeks"]),
    )[:2]
    weights = list(execution_weights or [1.0] * len(weekly))
    if len(weights) != len(weekly) or sum(max(0.0, value) for value in weights) <= 0.0:
        weights = [1.0] * len(weekly)
    weight_sum = sum(max(0.0, value) for value in weights)
    normalized_weights = [max(0.0, value) / weight_sum for value in weights]
    reference_weeks = list(visible_reference_weeks)[-len(weekly):]
    if not reference_weeks:
        raise ValueError("oil strategy cost gate requires visible reference weeks")
    while len(reference_weeks) < len(weekly):
        reference_weeks.insert(0, reference_weeks[0])
    effective_gross_budget = min(
        int(gross_turnover_budget_lots),
        int(market_gross_execution_limit_lots),
    )
    round_trip_execution = friction_config["round_trip_execution"]
    net_edge_floor_pct = float(turnover_profile["net_edge_floor_pct"])
    signal_floor = float(
        round_trip_execution["signal_utilization_floor"]
    )
    signal_utilization = signal_floor + (1.0 - signal_floor) * clamp(
        abs(float(parent_signal)), 0.0, 1.0
    )
    for bar, execution_weight, visible_week in zip(
        weekly, normalized_weights, reference_weeks, strict=True
    ):
        predicted_open = float(bar["open"])
        predicted_high = float(bar["high"])
        predicted_low = float(bar["low"])
        predicted_close = float(bar["close"])
        if min(predicted_open, predicted_high, predicted_low, predicted_close) <= 0.0:
            continue
        estimated_side_lots = math.floor(
            effective_gross_budget * execution_weight / 2.0
        )
        cost_estimate = _estimate_round_trip_cost(
            role,
            visible_week,
            estimated_side_lots=estimated_side_lots,
            forecast_price_usd=predicted_open,
            contract_size_bbl=contract_size_bbl,
            tick_size_usd=tick_size_usd,
            turnover_profile=turnover_profile,
            fee_profile=fee_profile,
            friction_config=friction_config,
            execution_policy=execution_policy,
        )
        estimated_cost_pct = float(
            cost_estimate["estimated_round_trip_cost_pct"]
        )
        forecast_expected_edge_pct = 100.0 * abs(
            math.log(predicted_close / predicted_open)
        )
        expected_net_edge_pct = forecast_expected_edge_pct - estimated_cost_pct
        excess_net_edge_pct = expected_net_edge_pct - net_edge_floor_pct
        edge_quality = clamp(
            excess_net_edge_pct
            / float(round_trip_execution["edge_quality_full_strength_pct"]),
            0.0,
            1.0,
        )
        budget_utilization = (
            edge_quality
            ** float(round_trip_execution["budget_utilization_exponent"])
            * signal_utilization
        )
        required_edge_pct = max(
            minimum_edge_pct,
            estimated_cost_pct + net_edge_floor_pct,
        )
        edge = required_edge_pct / 100.0
        direction = "long" if parent_signal > 0.0 else "short"
        if direction == "long":
            entry_price = predicted_open * (1.0 - 0.5 * edge)
            exit_price = entry_price * (1.0 + edge)
            feasible = predicted_low <= entry_price and predicted_high >= exit_price
        else:
            entry_price = predicted_open * (1.0 + 0.5 * edge)
            exit_price = entry_price * (1.0 - edge)
            feasible = predicted_high >= entry_price and predicted_low <= exit_price
        predicted_range_pct = 100.0 * (
            predicted_high / max(1e-9, predicted_low) - 1.0
        )
        setups.append(
            {
                "target_week": str(bar["target_week"]),
                "week_serial": int(bar["week_serial"]),
                "horizon_weeks": int(bar["horizon_weeks"]),
                "direction": direction,
                "planned": bool(
                    feasible
                    and not math.isclose(parent_signal, 0.0, abs_tol=1e-12)
                    and budget_utilization > 0.0
                ),
                "entry_price_usd": entry_price,
                "exit_price_usd": exit_price,
                "minimum_edge_pct": minimum_edge_pct,
                "required_edge_after_cost_pct": required_edge_pct,
                "net_edge_floor_pct": net_edge_floor_pct,
                "forecast_expected_edge_pct": forecast_expected_edge_pct,
                "expected_net_edge_pct": expected_net_edge_pct,
                "excess_net_edge_pct": excess_net_edge_pct,
                "edge_quality": edge_quality,
                "signal_utilization": signal_utilization,
                "round_trip_budget_utilization": budget_utilization,
                "estimated_cost": cost_estimate,
                "predicted_range_pct": predicted_range_pct,
                "predicted_open_usd": predicted_open,
                "predicted_close_usd": predicted_close,
                "execution_weight": execution_weight,
            }
        )
    return setups


def _recent_visible_volume_weights(
    contract: Mapping[str, Any],
    *,
    count: int = 2,
) -> list[float]:
    visible = [
        max(0, int(week.get("volume_lots", 0)))
        for week in _recent_visible_weeks(contract, count=count)
    ]
    if len(visible) < count or sum(visible) <= 0:
        return [1.0 / count] * count
    total = float(sum(visible))
    return [value / total for value in visible]


def _recent_visible_weeks(
    contract: Mapping[str, Any],
    *,
    count: int = 2,
) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    for month in contract.get("monthly", ()):
        for week in month.get("weekly", ()):
            visible.append(dict(week))
    return visible[-count:]


def _apply_position_persistence(
    *,
    current_position_lots: int,
    ideal_target_lots: int,
    risk_capacity_lots: int,
    position_persistence: float,
) -> int:
    """Let patient directors retain risk without ever expanding capacity.

    Persistence applies only when a new signal would reduce or reverse an
    existing position.  It never enlarges a same-direction target and the
    result remains inside the current resolved risk capacity.
    """

    capacity = max(0, int(risk_capacity_lots))
    if capacity == 0:
        return 0
    ideal = int(clamp(float(ideal_target_lots), -float(capacity), float(capacity)))
    current = int(
        clamp(float(current_position_lots), -float(capacity), float(capacity))
    )
    persistence = clamp(float(position_persistence), 0.0, 1.0)
    if current == 0 or abs(ideal) >= abs(current) and current * ideal > 0:
        return ideal
    if current * ideal >= 0:
        retained = ideal + persistence * (current - ideal)
    else:
        retained = ideal + 0.5 * persistence * current
    return int(
        clamp(float(round(retained)), -float(capacity), float(capacity))
    )


def build_oil_strategy_decision(
    market: Mapping[str, Any],
    forecast_vintage: Mapping[str, Any],
    *,
    positions: Mapping[str, int] | None = None,
    equity_usd: float | None = None,
    strategy_research_profile: Mapping[str, Any] | None = None,
    execution_desk_profile: Mapping[str, Any] | None = None,
    corporate_risk_profile: Mapping[str, Any] | None = None,
    strategy_risk_profile: Mapping[str, Any] | None = None,
    risk_state: Mapping[str, Any] | None = None,
    strategy_risk_state: Mapping[str, Any] | None = None,
    thesis_state: Mapping[str, Any] | None = None,
    capital_authorization_pct_of_company_equity: float | None = None,
    turnover_intensity: float | None = None,
    fee_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert one visible forecast vintage into named-contract target positions.

    The function deliberately has no ``GlobalMacroRun`` argument.  It cannot
    inspect hidden actual bars or construct a future market payload.
    """

    assets, config, contract = _validate_registered_assets()
    resolved_thesis_state = resolve_oil_strategy_thesis_state(thesis_state)
    thesis_policy = dict(config["thesis_invalidation"])
    if not bool(market.get("ok")) or not bool(forecast_vintage.get("ok")):
        raise ValueError("oil strategy requires successful market and forecast payloads")
    market_as_of = market["asOf"]
    forecast_as_of = forecast_vintage["asOf"]
    if (
        int(market_as_of["year"]),
        int(market_as_of["month"]),
        int(market_as_of["half"]),
    ) != (
        int(forecast_as_of["year"]),
        int(forecast_as_of["month"]),
        int(forecast_as_of["half"]),
    ):
        raise ValueError("oil strategy market and forecast cutoffs must match")
    if market["identity"]["upstream_global_identity_hash"] != (
        forecast_vintage["identity"]["upstream_global_identity_hash"]
    ):
        raise ValueError("oil strategy market and forecast worlds must match")

    raw_positions = dict(positions or {})
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in raw_positions.values()
    ):
        raise ValueError("oil strategy positions must be integer lots")
    current_positions = {
        str(key): int(value) for key, value in raw_positions.items()
    }
    current_equity = float(
        config["initial_reference_equity_usd"]
        if equity_usd is None
        else equity_usd
    )
    if not math.isfinite(current_equity) or current_equity <= 0.0:
        raise ValueError("oil strategy equity must be positive and finite")

    strategy_profile, strategy_policy = resolve_oil_strategy_runtime_policy(
        strategy_research_profile,
        turnover_development_override=turnover_intensity,
    )
    resolved_corporate_risk_profile = resolve_corporate_risk_profile(
        corporate_risk_profile
    )
    resolved_strategy_risk_profile = (
        resolved_corporate_risk_profile
        if strategy_risk_profile is None
        else resolve_corporate_risk_profile(strategy_risk_profile)
    )
    strategy_risk_review = build_oil_strategy_risk_review(
        strategy_profile,
        resolved_strategy_risk_profile,
    )
    investment_committee_approval = (
        build_investment_committee_strategy_approval(
            strategy_risk_review,
            company_equity_usd=current_equity,
            capital_authorization_pct_of_company_equity=(
                capital_authorization_pct_of_company_equity
            ),
        )
    )
    allocated_strategy_capital = float(
        investment_committee_approval["capitalAuthorization"][
            "authorized_capital_usd"
        ]
    )
    execution_profile, execution_policy = resolve_oil_execution_runtime_policy(
        execution_desk_profile
    )
    turnover_profile = dict(strategy_policy["execution"])
    signal_config = {
        **config["signal"],
        "horizon_weeks": list(strategy_policy["signal"]["horizon_weeks"]),
        "horizon_weights": list(strategy_policy["signal"]["horizon_weights"]),
        "reversion_weight": float(
            strategy_policy["signal"]["reversion_weight"]
        ),
        "continuation_weight": float(
            strategy_policy["signal"]["continuation_weight"]
        ),
    }
    friction_config = config["execution_friction"]
    neutral_fee_profile = _resolve_fee_profile(fee_state, friction_config)
    fee_profile = _resolve_fee_profile(
        fee_state, friction_config, execution_policy=execution_policy
    )
    contracts = _contract_map(market)
    specification = market["contractSpecification"]
    contract_size = float(specification["contract_size_bbl"])
    tick_size = float(
        specification["minimum_price_fluctuation_usd_per_bbl"]
    )
    initial_margin_rate = float(specification["initial_margin_rate_pct"]) / 100.0
    risk = strategy_policy["risk"]
    role_weights = risk["role_weights"]
    gross_cap_lots = int(
        market["participantLimitsPolicy"]["all_contract_gross_position_cap_lots"]
    )
    capital_deployment_pct = float(
        risk["capital_deployment_pct_of_allocated_equity"]
    )
    capital_deployment_budget = (
        allocated_strategy_capital
        * capital_deployment_pct
        / 100.0
    )

    targets: dict[str, dict[str, Any]] = {}
    for forecast in forecast_vintage.get("forecasts", ()):
        role = str(forecast["role"])
        contract_id = str(forecast["contract_id"])
        if role not in ROLE_ORDER or contract_id not in contracts:
            raise ValueError("oil strategy forecast target is unavailable in the market")
        market_contract = contracts[contract_id]
        limits = market_contract["participantLimits"]
        signal_report = _signal_from_contract_forecast(
            forecast,
            signal_config,
            turnover_profile,
            visible_contract=market_contract,
        )
        role_weight = float(role_weights[role])
        market_role_capacity = min(
            int(limits["single_contract_position_limit_lots"]),
            math.floor(gross_cap_lots * role_weight),
        )
        price = float(market_contract["price_usd"])
        margin_per_lot = price * contract_size * initial_margin_rate
        capital_deployment_capacity = math.floor(
            capital_deployment_budget * role_weight / max(1e-9, margin_per_lot)
        )
        risk_capacity = max(
            0, min(market_role_capacity, capital_deployment_capacity)
        )
        binding_capacity = (
            "market_position_limit"
            if market_role_capacity <= capital_deployment_capacity
            else "capital_deployment_budget"
        )
        if not bool(limits["new_trades_allowed"]):
            risk_capacity = 0
            binding_capacity = "new_trades_closed"
        pre_persistence_ideal_target = int(
            round(float(signal_report["signal"]) * risk_capacity)
        )
        pre_thesis_target = _apply_position_persistence(
            current_position_lots=int(current_positions.get(contract_id, 0)),
            ideal_target_lots=pre_persistence_ideal_target,
            risk_capacity_lots=risk_capacity,
            position_persistence=float(turnover_profile["position_persistence"]),
        )
        ideal_target, thesis_action = apply_oil_strategy_thesis_invalidation(
            contract_id=contract_id,
            current_position_lots=int(current_positions.get(contract_id, 0)),
            proposed_target_lots=pre_thesis_target,
            signal=float(signal_report["signal"]),
            state=resolved_thesis_state,
            policy=thesis_policy,
        )
        turnover_reference = max(abs(ideal_target), abs(int(current_positions.get(contract_id, 0))))
        thesis_turnover_scale = float(thesis_action["target_scale"])
        if thesis_action["action"] == "exit_before_direction_reversal":
            thesis_turnover_scale = 0.0
        gross_turnover_budget = math.floor(
            turnover_reference
            * float(turnover_profile["gross_turnover_multiplier"])
            * thesis_turnover_scale
        )
        targets[contract_id] = {
            "contract_id": contract_id,
            "role": role,
            **signal_report,
            "role_weight": role_weight,
            "single_contract_position_limit_lots": int(
                limits["single_contract_position_limit_lots"]
            ),
            "gross_role_capacity_lots": math.floor(gross_cap_lots * role_weight),
            "capital_deployment_capacity_lots": capital_deployment_capacity,
            "risk_capacity_lots": risk_capacity,
            "binding_capacity": binding_capacity,
            "pre_persistence_ideal_target_lots": pre_persistence_ideal_target,
            "pre_thesis_target_position_lots": pre_thesis_target,
            "thesis_adjusted_target_position_lots": ideal_target,
            "thesis_status": thesis_action["status"],
            "thesis_target_scale": thesis_action["target_scale"],
            "thesis_turnover_scale": thesis_turnover_scale,
            "thesis_action": thesis_action,
            "ideal_target_lots": ideal_target,
            "target_position_lots": ideal_target,
            "turnover_reference_lots": turnover_reference,
            "gross_turnover_budget_lots": gross_turnover_budget,
            "market_gross_execution_limit_lots": int(
                limits["turn_trade_limit_lots"]
            ),
            "weekly_turnover_setups": _build_weekly_turnover_setups(
                forecast,
                turnover_profile,
                parent_signal=float(signal_report["signal"]),
                execution_weights=adjust_visible_execution_weights(
                    _recent_visible_volume_weights(market_contract),
                    execution_policy,
                ),
                visible_reference_weeks=_recent_visible_weeks(market_contract),
                role=role,
                gross_turnover_budget_lots=gross_turnover_budget,
                market_gross_execution_limit_lots=int(
                    limits["turn_trade_limit_lots"]
                ),
                contract_size_bbl=contract_size,
                tick_size_usd=tick_size,
                fee_profile=fee_profile,
                friction_config=friction_config,
                execution_policy=execution_policy,
            ),
        }

    for contract_id, position in current_positions.items():
        if contract_id not in targets and position != 0:
            targets[contract_id] = {
                "contract_id": contract_id,
                "role": "legacy_exit",
                "signal": 0.0,
                "raw_signal": 0.0,
                "reversion_signal": 0.0,
                "continuation_signal": 0.0,
                "visible_trend_signal": 0.0,
                "visible_trend": {"available": False, "signal": 0.0},
                "reversion_weight": float(signal_config["reversion_weight"]),
                "continuation_weight": float(
                    signal_config["continuation_weight"]
                ),
                "band_location_signal": 0.0,
                "path_direction_signal": 0.0,
                "path_direction": {"available": False, "signal": 0.0},
                "anchor_price_usd": (
                    None
                    if contract_id not in contracts
                    else float(contracts[contract_id]["price_usd"])
                ),
                "horizon_components": [],
                "role_weight": 0.0,
                "single_contract_position_limit_lots": (
                    0
                    if contract_id not in contracts
                    else int(
                        contracts[contract_id]["participantLimits"][
                            "single_contract_position_limit_lots"
                        ]
                    )
                ),
                "gross_role_capacity_lots": 0,
                "capital_deployment_capacity_lots": 0,
                "risk_capacity_lots": 0,
                "binding_capacity": "legacy_exit",
                "pre_persistence_ideal_target_lots": 0,
                "pre_thesis_target_position_lots": 0,
                "thesis_adjusted_target_position_lots": 0,
                "thesis_status": str(
                    resolved_thesis_state["contracts"]
                    .get(contract_id, {})
                    .get("status", "active")
                ),
                "thesis_target_scale": 1.0,
                "thesis_turnover_scale": 0.0,
                "thesis_action": {
                    "status": str(
                        resolved_thesis_state["contracts"]
                        .get(contract_id, {})
                        .get("status", "active")
                    ),
                    "target_scale": 1.0,
                    "previous_signal": float(
                        resolved_thesis_state["contracts"]
                        .get(contract_id, {})
                        .get("last_signal", 0.0)
                    ),
                    "current_signal": 0.0,
                    "material_direction_reversal": False,
                    "action": "legacy_exit",
                    "pre_thesis_target_position_lots": 0,
                    "thesis_adjusted_target_position_lots": 0,
                },
                "ideal_target_lots": 0,
                "target_position_lots": 0,
                "turnover_reference_lots": 0,
                "gross_turnover_budget_lots": 0,
                "market_gross_execution_limit_lots": (
                    0
                    if contract_id not in contracts
                    else int(
                        contracts[contract_id]["participantLimits"][
                            "turn_trade_limit_lots"
                        ]
                    )
                ),
                "weekly_turnover_setups": [],
            }

    targets, strategy_risk = apply_oil_strategy_risk_mandate(
        market,
        targets,
        positions=current_positions,
        strategy_equity_usd=current_equity,
        committee_approval=investment_committee_approval,
        risk_state=strategy_risk_state,
    )
    targets, corporate_risk = approve_oil_strategy_targets(
        market,
        targets,
        positions=current_positions,
        equity_usd=current_equity,
        risk_profile=resolved_corporate_risk_profile,
        risk_state=risk_state,
    )
    for contract_id, item in targets.items():
        strategy_target = int(item["strategy_intent_target_position_lots"])
        approved_target = int(item["risk_approved_target_position_lots"])
        original_budget = int(item["gross_turnover_budget_lots"])
        if strategy_target == 0:
            risk_turnover_scale = 0.0
        else:
            risk_turnover_scale = min(
                1.0, abs(approved_target) / max(1, abs(strategy_target))
            )
        item["strategy_gross_turnover_budget_lots"] = original_budget
        item["risk_turnover_budget_scale"] = risk_turnover_scale
        item["gross_turnover_budget_lots"] = math.floor(
            original_budget * risk_turnover_scale
        )

    total_target_gross = sum(
        abs(int(item["target_position_lots"])) for item in targets.values()
    )
    if total_target_gross > gross_cap_lots:
        raise ValueError("oil strategy target construction exceeded its gross risk budget")

    result = {
        "schemaVersion": "asset-simulation-oil-strategy-decision-v8",
        "asOf": dict(market_as_of),
        "strategy": {
            "strategy_id": str(config["strategy_id"]),
            "display_name": str(config["display_name"]),
            "strategy_research_profile": {
                "appointment": strategy_profile["appointment"],
                "style_radar": strategy_profile["style_radar"],
                "style_tags": strategy_profile["style_tags"],
                "preference_total_score": None,
                "profile_hash": strategy_profile["profile_hash"],
                "governance": strategy_profile["governance"],
            },
            "resolved_policy": strategy_policy,
            "turnover_profile": turnover_profile,
            "adjustment_speed": float(turnover_profile["adjustment_speed"]),
            "normal_turn_trade_limit_utilization": float(
                turnover_profile["normal_net_trade_limit_utilization"]
            ),
            "fee_profile": fee_profile,
            "neutral_fee_profile": neutral_fee_profile,
            "friction_enabled": True,
            "fees_enabled": True,
        },
        "executionDesk": {
            "profile": {
                "appointment": execution_profile["appointment"],
                "capability_radar": execution_profile["capability_radar"],
                "execution_style": execution_profile["execution_style"],
                "capability_total_score": execution_profile["capability_total_score"],
                "capability_tags": execution_profile["capability_tags"],
                "profile_hash": execution_profile["profile_hash"],
            },
            "resolved_policy": execution_policy,
            "neutral_baseline_score": 50.0,
        },
        "investmentDecision": investment_committee_approval,
        "thesisInvalidation": {
            "schemaVersion": "asset-simulation-oil-strategy-thesis-decision-v1",
            "policy": thesis_policy,
            "stateBefore": resolved_thesis_state,
            "state_hash": sha256_json(resolved_thesis_state),
            "actions": {
                key: dict(targets[key]["thesis_action"])
                for key in sorted(targets)
            },
        },
        "strategyRisk": strategy_risk,
        "corporateRisk": corporate_risk,
        "institution": {
            "institution_id": str(
                forecast_vintage["institution"]["institution_id"]
            ),
            "profile_hash": str(
                forecast_vintage["institution"]["profile_hash"]
            ),
        },
        "accountBefore": {
            "equity_usd": current_equity,
            "positions": current_positions,
            "position_hash": sha256_json(current_positions),
            "fee_state": {
                "rolling_gross_turnover_lots": int(
                    fee_profile["rolling_gross_turnover_lots"]
                ),
                "lookback_turns": int(fee_profile["lookback_turns"]),
            },
        },
        "riskBudget": {
            "gross_market_cap_lots": gross_cap_lots,
            "target_gross_lots": total_target_gross,
            "strategy_intent_gross_lots": strategy_risk["approvalSummary"][
                "strategy_intent_gross_lots"
            ],
            "strategy_risk_approved_gross_lots": strategy_risk[
                "approvalSummary"
            ]["approved_gross_lots"],
            "strategy_risk_clipped_gross_lots": strategy_risk[
                "approvalSummary"
            ]["clipped_gross_lots"],
            "strategy_target_gross_lots": corporate_risk["approval_summary"][
                "strategy_target_gross_lots"
            ],
            "company_risk_clipped_gross_lots": corporate_risk[
                "approval_summary"
            ]["clipped_gross_lots"],
            "capital_authorization_pct_of_company_equity": float(
                investment_committee_approval["capitalAuthorization"][
                    "authorized_pct_of_company_equity"
                ]
            ),
            "risk_recommended_capital_pct_of_company_equity": float(
                investment_committee_approval["capitalAuthorization"][
                    "recommended_pct_of_company_equity"
                ]
            ),
            "allocated_strategy_capital_usd": allocated_strategy_capital,
            "capital_deployment_budget_usd": capital_deployment_budget,
            "capital_deployment_pct_of_allocated_equity": capital_deployment_pct,
            "binding_capacity_by_contract": {
                key: targets[key]["binding_capacity"] for key in sorted(targets)
            },
        },
        "targets": [
            targets[key]
            for key in sorted(
                targets,
                key=lambda key: (
                    ROLE_ORDER.index(targets[key]["role"])
                    if targets[key]["role"] in ROLE_ORDER
                    else len(ROLE_ORDER),
                    key,
                ),
            )
        ],
        "informationPolicy": {
            "market_cutoff": str(market_as_of["label"]),
            "forecast_vintage_id": str(
                forecast_vintage["identity"]["vintage_id"]
            ),
            "hidden_future_available": False,
            "future_market_payload_available": False,
            "configured_capability_score_used": False,
            "strategy_radar_player_editable": False,
            "strategy_profile_selection_method": "appoint_generated_personnel",
            "future_volume_used_for_execution_weights": False,
            "fee_tier_uses_prior_turns_only": True,
            "execution_scores_are_continuous": True,
            "execution_desk_reads_future_weeks": False,
            "strategy_risk_reads_future_weeks": False,
            "strategy_risk_can_expand_strategy_intent": False,
            "thesis_invalidation_reads_future_weeks": False,
            "thesis_invalidation_uses_configured_ability_score": False,
            "thesis_invalidation_can_expand_pre_thesis_target": False,
            "investment_committee_owns_capital_authorization": True,
            "corporate_risk_reads_future_weeks": False,
            "corporate_risk_can_expand_strategy_intent": False,
        },
    }
    identity = {
        "schema_version": "asset-simulation-oil-strategy-decision-identity-v8",
        "model_version": OIL_TRADING_STRATEGY_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_trading_strategy_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_trading_strategy_contract_hash"],
        "upstream_market_result_hash": market["identity"]["result_hash"],
        "upstream_forecast_result_hash": forecast_vintage["identity"]["result_hash"],
        "strategy_id": config["strategy_id"],
        "strategy_personnel_id": strategy_profile["appointment"]["personnel_id"],
        "strategy_profile_hash": strategy_profile["profile_hash"],
        "strategy_risk_review_hash": strategy_risk_review["identity"][
            "result_hash"
        ],
        "strategy_risk_approval_hash": investment_committee_approval[
            "identity"
        ]["result_hash"],
        "thesis_state_hash": sha256_json(resolved_thesis_state),
        "turnover_intensity": turnover_profile["turnover_intensity"],
        "trailing_fee_volume_lots": fee_profile[
            "rolling_gross_turnover_lots"
        ],
        "execution_personnel_id": execution_profile["appointment"]["personnel_id"],
        "execution_profile_hash": execution_profile["profile_hash"],
        "corporate_risk_personnel_id": corporate_risk["profile"][
            "appointment"
        ]["personnel_id"],
        "corporate_risk_profile_hash": corporate_risk["profile"]["profile_hash"],
        "write_back": False,
        "result_hash": sha256_json(result),
    }
    return _round_nested({"ok": True, "identity": identity, **result})


def _flatten_visible_weeks(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    weeks: list[dict[str, Any]] = []
    for month in contract.get("monthly", ()):
        year = int(month["year"])
        month_number = int(month["month"])
        for week in month.get("weekly", ()):
            week_number = int(week["week"])
            weeks.append(
                {
                    "year": year,
                    "month": month_number,
                    "week": week_number,
                    "week_serial": _week_serial(year, month_number, week_number),
                    "open": float(week["open"]),
                    "high": float(week["high"]),
                    "low": float(week["low"]),
                    "close": float(week["close"]),
                    "volume_lots": int(week.get("volume_lots", 0)),
                }
            )
    return weeks


def _aggregate_execution_price(
    contract: Mapping[str, Any],
    *,
    start_year: int,
    start_month: int,
    start_half: int,
    end_year: int,
    end_month: int,
    end_half: int,
) -> tuple[float, list[dict[str, Any]]]:
    start_cutoff = _cutoff_week_serial(start_year, start_month, start_half)
    end_cutoff = _cutoff_week_serial(end_year, end_month, end_half)
    realized = [
        item
        for item in _flatten_visible_weeks(contract)
        if start_cutoff < int(item["week_serial"]) <= end_cutoff
    ]
    if not realized:
        raise ValueError("oil strategy execution window contains no newly realized weeks")
    typical_prices = [
        0.25
        * (
            float(item["open"])
            + float(item["high"])
            + float(item["low"])
            + float(item["close"])
        )
        for item in realized
    ]
    total_volume = sum(int(item["volume_lots"]) for item in realized)
    if total_volume > 0:
        price = sum(
            typical * int(item["volume_lots"])
            for typical, item in zip(typical_prices, realized)
        ) / total_volume
    else:
        price = sum(typical_prices) / len(typical_prices)
    lower = min(float(item["low"]) for item in realized)
    upper = max(float(item["high"]) for item in realized)
    return clamp(price, lower, upper), realized


def _allocate_integer(total: int, weights: list[float]) -> list[int]:
    """Allocate a non-negative integer exactly while preserving stable order."""

    amount = int(total)
    if amount < 0:
        raise ValueError("oil strategy integer allocation must be non-negative")
    if not weights:
        return []
    positive = [max(0.0, float(value)) for value in weights]
    weight_sum = sum(positive)
    if weight_sum <= 0.0:
        positive = [1.0] * len(weights)
        weight_sum = float(len(weights))
    raw = [amount * value / weight_sum for value in positive]
    allocated = [math.floor(value) for value in raw]
    remainder = amount - sum(allocated)
    order = sorted(
        range(len(raw)),
        key=lambda index: (raw[index] - allocated[index], -index),
        reverse=True,
    )
    for index in order[:remainder]:
        allocated[index] += 1
    return allocated


def _canonical_week_path(week: Mapping[str, Any]) -> list[float]:
    open_price = float(week["open"])
    high_price = float(week["high"])
    low_price = float(week["low"])
    close_price = float(week["close"])
    if close_price >= open_price:
        return [open_price, low_price, high_price, close_price]
    return [open_price, high_price, low_price, close_price]


def _path_price(points: list[float], moment: float) -> float:
    bounded = clamp(float(moment), 0.0, float(len(points) - 1))
    left = min(int(math.floor(bounded)), len(points) - 2)
    fraction = bounded - left
    return points[left] + fraction * (points[left + 1] - points[left])


def _first_limit_hit(
    points: list[float],
    *,
    side: str,
    limit_price: float,
    start_moment: float = 0.0,
) -> tuple[float, float] | None:
    """Find the first fill on a deterministic piecewise-linear OHLC path."""

    if side not in ("buy", "sell"):
        raise ValueError("oil strategy limit side must be buy or sell")
    condition = (
        (lambda price: price <= limit_price + 1e-12)
        if side == "buy"
        else (lambda price: price >= limit_price - 1e-12)
    )
    final_moment = float(len(points) - 1)
    moment = clamp(float(start_moment), 0.0, final_moment)
    price = _path_price(points, moment)
    if condition(price):
        return moment, price
    first_segment = min(int(math.floor(moment)), len(points) - 2)
    for segment in range(first_segment, len(points) - 1):
        segment_start = max(moment, float(segment))
        start_price = _path_price(points, segment_start)
        end_price = float(points[segment + 1])
        if condition(start_price):
            return segment_start, start_price
        crosses = (
            end_price <= limit_price < start_price
            if side == "buy"
            else end_price >= limit_price > start_price
        )
        if crosses and not math.isclose(end_price, start_price):
            fraction = (limit_price - start_price) / (end_price - start_price)
            hit_moment = segment_start + fraction * (
                float(segment + 1) - segment_start
            )
            return hit_moment, limit_price
    return None


def _limit_penetration_fill(
    points: list[float],
    *,
    side: str,
    limit_price: float,
    hit_moment: float,
    full_fill_range_fraction: float,
) -> dict[str, float]:
    """Convert post-touch price penetration into a deterministic fill ratio."""

    if side not in ("buy", "sell"):
        raise ValueError("oil strategy penetration side must be buy or sell")
    weekly_range = max(points) - min(points)
    if weekly_range <= 0.0:
        return {
            "fill_ratio": 0.0,
            "penetration_usd_per_bbl": 0.0,
            "full_fill_penetration_usd_per_bbl": 0.0,
        }
    start = clamp(float(hit_moment), 0.0, float(len(points) - 1))
    future_prices = [_path_price(points, start)]
    future_prices.extend(
        float(points[index])
        for index in range(int(math.floor(start)) + 1, len(points))
    )
    if side == "buy":
        penetration = max(0.0, float(limit_price) - min(future_prices))
    else:
        penetration = max(0.0, max(future_prices) - float(limit_price))
    full_fill_penetration = weekly_range * float(full_fill_range_fraction)
    fill_ratio = clamp(
        penetration / max(1e-12, full_fill_penetration), 0.0, 1.0
    )
    return {
        "fill_ratio": fill_ratio,
        "penetration_usd_per_bbl": penetration,
        "full_fill_penetration_usd_per_bbl": full_fill_penetration,
    }


def _weekly_execution_ledger(
    realized_weeks: list[dict[str, Any]],
    *,
    net_delta_lots: int,
    gross_turnover_budget_lots: int,
    starting_position_lots: int,
    position_limit_lots: int,
    weekly_setups: list[Mapping[str, Any]],
    contract_size_bbl: float,
    role: str,
    tick_size_usd: float,
    turnover_profile: Mapping[str, Any],
    fee_profile: Mapping[str, Any],
    neutral_fee_profile: Mapping[str, Any],
    friction_config: Mapping[str, Any],
    settlement_price_usd: float,
    execution_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute and exactly attribute net adjustments and weekly round trips."""

    if not realized_weeks:
        raise ValueError("oil strategy weekly execution ledger requires realized weeks")
    net_delta = int(net_delta_lots)
    gross_budget = max(abs(net_delta), int(gross_turnover_budget_lots))
    setups = {
        int(item["week_serial"]): dict(item) for item in weekly_setups
    }
    frozen_weights = [
        float(setups.get(int(week["week_serial"]), {}).get("execution_weight", 1.0))
        for week in realized_weeks
    ]
    net_absolute = _allocate_integer(abs(net_delta), frozen_weights)
    net_allocations = [
        int(math.copysign(value, net_delta)) if net_delta else 0
        for value in net_absolute
    ]
    extra_allocations = _allocate_integer(
        max(0, gross_budget - abs(net_delta)), frozen_weights
    )
    running_position = int(starting_position_lots)
    ledger: list[dict[str, Any]] = []
    execution_config = friction_config["round_trip_execution"]
    for week, weekly_net, weekly_extra in zip(
        realized_weeks, net_allocations, extra_allocations, strict=True
    ):
        neutral_price = 0.25 * (
            float(week["open"])
            + float(week["high"])
            + float(week["low"])
            + float(week["close"])
        )
        running_position += int(weekly_net)
        setup = setups.get(int(week["week_serial"]))
        pair_budget = int(weekly_extra) // 2
        budget_utilization = (
            0.0
            if setup is None
            else clamp(
                float(setup.get("round_trip_budget_utilization", 0.0)),
                0.0,
                1.0,
            )
        )
        sized_pair_budget = math.floor(pair_budget * budget_utilization)
        planned_round_trip_lots = 0
        round_trip_lots = 0
        entry_price: float | None = None
        exit_price: float | None = None
        exit_reason: str | None = None
        direction: str | None = None
        entry_triggered = False
        entry_fill = {
            "fill_ratio": 0.0,
            "penetration_usd_per_bbl": 0.0,
            "full_fill_penetration_usd_per_bbl": 0.0,
        }
        target_exit_hit = False
        target_exit_triggered = False
        target_exit_fill = {
            "fill_ratio": 0.0,
            "penetration_usd_per_bbl": 0.0,
            "full_fill_penetration_usd_per_bbl": 0.0,
        }
        target_exit_lots = 0
        weekly_close_exit_lots = 0
        round_trip_pnl = 0.0
        if setup is not None:
            direction = str(setup["direction"])
        if (
            setup is not None
            and bool(setup["planned"])
            and sized_pair_budget > 0
        ):
            temporary_position_room = (
                int(position_limit_lots) - running_position
                if direction == "long"
                else int(position_limit_lots) + running_position
            )
            planned_round_trip_lots = max(
                0, min(sized_pair_budget, temporary_position_room)
            )
            if planned_round_trip_lots > 0:
                points = _canonical_week_path(week)
                entry_side = "buy" if direction == "long" else "sell"
                exit_side = "sell" if direction == "long" else "buy"
                entry_hit = _first_limit_hit(
                    points,
                    side=entry_side,
                    limit_price=float(setup["entry_price_usd"]),
                )
                if entry_hit is not None and entry_hit[0] < len(points) - 1 - 1e-12:
                    entry_triggered = True
                    entry_price = float(entry_hit[1])
                    entry_fill = _limit_penetration_fill(
                        points,
                        side=entry_side,
                        limit_price=float(setup["entry_price_usd"]),
                        hit_moment=float(entry_hit[0]),
                        full_fill_range_fraction=float(
                            execution_config[
                                "entry_full_fill_range_fraction"
                            ]
                        ),
                    )
                    round_trip_lots = math.floor(
                        planned_round_trip_lots
                        * float(entry_fill["fill_ratio"])
                    )
                    if round_trip_lots > 0:
                        exit_hit = _first_limit_hit(
                            points,
                            side=exit_side,
                            limit_price=float(setup["exit_price_usd"]),
                            start_moment=float(entry_hit[0]) + 1e-10,
                        )
                        if exit_hit is not None:
                            target_exit_hit = True
                            target_exit_fill = _limit_penetration_fill(
                                points,
                                side=exit_side,
                                limit_price=float(setup["exit_price_usd"]),
                                hit_moment=float(exit_hit[0]),
                                full_fill_range_fraction=float(
                                    execution_config[
                                        "exit_full_fill_range_fraction"
                                    ]
                                ),
                            )
                            target_exit_lots = math.floor(
                                round_trip_lots
                                * float(target_exit_fill["fill_ratio"])
                            )
                        weekly_close_exit_lots = (
                            round_trip_lots - target_exit_lots
                        )
                        target_exit_triggered = target_exit_lots > 0
                        exit_price = (
                            target_exit_lots
                            * float(setup["exit_price_usd"])
                            + weekly_close_exit_lots * float(week["close"])
                        ) / round_trip_lots
                        if target_exit_lots == round_trip_lots:
                            exit_reason = "planned_target"
                        elif target_exit_lots > 0:
                            exit_reason = "partial_target_then_weekly_close"
                        else:
                            exit_reason = "weekly_close_flatten"
                        signed_edge = (
                            exit_price - entry_price
                            if direction == "long"
                            else entry_price - exit_price
                        )
                        round_trip_pnl = (
                            signed_edge * round_trip_lots * contract_size_bbl
                        )

        net_buy_lots = max(0, int(weekly_net))
        net_sell_lots = max(0, -int(weekly_net))
        round_trip_buy_lots = round_trip_lots
        round_trip_sell_lots = round_trip_lots
        buy_lots = net_buy_lots + round_trip_buy_lots
        sell_lots = net_sell_lots + round_trip_sell_lots
        gross_turnover = buy_lots + sell_lots
        net_raw_buy_notional = (
            net_buy_lots * neutral_price * contract_size_bbl
        )
        net_raw_sell_notional = (
            net_sell_lots * neutral_price * contract_size_bbl
        )
        round_trip_raw_buy_notional = 0.0
        round_trip_raw_sell_notional = 0.0
        if round_trip_lots and entry_price is not None and exit_price is not None:
            if direction == "long":
                round_trip_raw_buy_notional = (
                    round_trip_lots * entry_price * contract_size_bbl
                )
                round_trip_raw_sell_notional = (
                    round_trip_lots * exit_price * contract_size_bbl
                )
            else:
                round_trip_raw_sell_notional = (
                    round_trip_lots * entry_price * contract_size_bbl
                )
                round_trip_raw_buy_notional = (
                    round_trip_lots * exit_price * contract_size_bbl
                )
        raw_buy_notional = (
            net_raw_buy_notional + round_trip_raw_buy_notional
        )
        raw_sell_notional = (
            net_raw_sell_notional + round_trip_raw_sell_notional
        )
        week_notional = raw_buy_notional + raw_sell_notional
        spread_profile = _weekly_spread_profile(
            role,
            week,
            tick_size_usd=tick_size_usd,
            friction_config=friction_config,
        )
        buy_slippage = _slippage_bps(
            buy_lots,
            int(week["volume_lots"]),
            float(spread_profile["weekly_volatility"]),
            turnover_profile,
            friction_config,
            execution_policy=execution_policy,
            role=role,
        )
        sell_slippage = _slippage_bps(
            sell_lots,
            int(week["volume_lots"]),
            float(spread_profile["weekly_volatility"]),
            turnover_profile,
            friction_config,
            execution_policy=execution_policy,
            role=role,
        )
        neutral_buy_slippage = _slippage_bps(
            buy_lots,
            int(week["volume_lots"]),
            float(spread_profile["weekly_volatility"]),
            turnover_profile,
            friction_config,
            role=role,
        )
        neutral_sell_slippage = _slippage_bps(
            sell_lots,
            int(week["volume_lots"]),
            float(spread_profile["weekly_volatility"]),
            turnover_profile,
            friction_config,
            role=role,
        )
        net_adjustment = _execution_cost_bucket(
            buy_lots=net_buy_lots,
            sell_lots=net_sell_lots,
            raw_buy_notional_usd=net_raw_buy_notional,
            raw_sell_notional_usd=net_raw_sell_notional,
            full_spread_usd_per_bbl=float(
                spread_profile["full_spread_usd_per_bbl"]
            ),
            buy_slippage_bps=float(buy_slippage["slippage_bps"]),
            sell_slippage_bps=float(sell_slippage["slippage_bps"]),
            contract_size_bbl=contract_size_bbl,
            fee_profile=fee_profile,
            execution_policy=execution_policy,
            role=role,
        )
        round_trip = _execution_cost_bucket(
            buy_lots=round_trip_buy_lots,
            sell_lots=round_trip_sell_lots,
            raw_buy_notional_usd=round_trip_raw_buy_notional,
            raw_sell_notional_usd=round_trip_raw_sell_notional,
            full_spread_usd_per_bbl=float(
                spread_profile["full_spread_usd_per_bbl"]
            ),
            buy_slippage_bps=float(buy_slippage["slippage_bps"]),
            sell_slippage_bps=float(sell_slippage["slippage_bps"]),
            contract_size_bbl=contract_size_bbl,
            fee_profile=fee_profile,
            execution_policy=execution_policy,
            role=role,
        )
        neutral_cost = _execution_cost_bucket(
            buy_lots=buy_lots,
            sell_lots=sell_lots,
            raw_buy_notional_usd=raw_buy_notional,
            raw_sell_notional_usd=raw_sell_notional,
            full_spread_usd_per_bbl=float(
                spread_profile["full_spread_usd_per_bbl"]
            ),
            buy_slippage_bps=float(neutral_buy_slippage["slippage_bps"]),
            sell_slippage_bps=float(neutral_sell_slippage["slippage_bps"]),
            contract_size_bbl=contract_size_bbl,
            fee_profile=neutral_fee_profile,
            role=role,
        )
        net_adjustment_gross_pnl = (
            int(weekly_net)
            * (float(settlement_price_usd) - neutral_price)
            * contract_size_bbl
        )
        net_adjustment["gross_pnl_before_cost_usd"] = (
            net_adjustment_gross_pnl
        )
        net_adjustment["net_pnl_after_cost_usd"] = (
            net_adjustment_gross_pnl
            - float(net_adjustment["execution_cost_usd"])
        )
        round_trip["gross_pnl_before_cost_usd"] = round_trip_pnl
        round_trip["net_pnl_after_cost_usd"] = round_trip_pnl - float(
            round_trip["execution_cost_usd"]
        )
        spread_cost = float(net_adjustment["spread_cost_usd"]) + float(
            round_trip["spread_cost_usd"]
        )
        slippage_cost = float(net_adjustment["slippage_cost_usd"]) + float(
            round_trip["slippage_cost_usd"]
        )
        gross_fee = float(net_adjustment["gross_fee_usd"]) + float(
            round_trip["gross_fee_usd"]
        )
        fee_rebate = float(net_adjustment["fee_rebate_usd"]) + float(
            round_trip["fee_rebate_usd"]
        )
        net_fee = float(net_adjustment["net_fee_usd"]) + float(
            round_trip["net_fee_usd"]
        )
        execution_cost = float(
            net_adjustment["execution_cost_usd"]
        ) + float(round_trip["execution_cost_usd"])
        buy_spread_cost = (
            buy_lots
            * 0.5
            * float(spread_profile["full_spread_usd_per_bbl"])
            * contract_size_bbl
            * float(net_adjustment["spread_cost_multiplier"])
            * float(net_adjustment["roll_coordination_multiplier"])
        )
        sell_spread_cost = (
            sell_lots
            * 0.5
            * float(spread_profile["full_spread_usd_per_bbl"])
            * contract_size_bbl
            * float(net_adjustment["spread_cost_multiplier"])
            * float(net_adjustment["roll_coordination_multiplier"])
        )
        buy_slippage_cost = (
            raw_buy_notional * float(buy_slippage["slippage_bps"]) / 10_000.0
        )
        sell_slippage_cost = (
            raw_sell_notional * float(sell_slippage["slippage_bps"]) / 10_000.0
        )
        raw_average_buy_price = (
            None
            if buy_lots == 0
            else raw_buy_notional / (buy_lots * contract_size_bbl)
        )
        raw_average_sell_price = (
            None
            if sell_lots == 0
            else raw_sell_notional / (sell_lots * contract_size_bbl)
        )
        all_in_buy_price = (
            None
            if buy_lots == 0
            else raw_average_buy_price
            + (
                buy_spread_cost
                + buy_slippage_cost
                + net_fee * buy_lots / max(1, gross_turnover)
            )
            / (buy_lots * contract_size_bbl)
        )
        all_in_sell_price = (
            None
            if sell_lots == 0
            else raw_average_sell_price
            - (
                sell_spread_cost
                + sell_slippage_cost
                + net_fee * sell_lots / max(1, gross_turnover)
            )
            / (sell_lots * contract_size_bbl)
        )
        if not math.isclose(
            execution_cost,
            spread_cost + slippage_cost + net_fee,
            abs_tol=1e-6,
        ):
            raise ValueError("oil strategy weekly cost buckets do not reconcile")
        ledger.append(
            {
                "week": f"{week['year']:04d}-{week['month']:02d}-W{week['week']}",
                "week_serial": int(week["week_serial"]),
                "market_volume_lots": int(week["volume_lots"]),
                "frozen_execution_weight": (
                    None if setup is None else float(setup["execution_weight"])
                ),
                "net_delta_lots": int(weekly_net),
                "net_execution_price_usd": (
                    neutral_price if weekly_net else None
                ),
                "gross_budget_lots": abs(int(weekly_net)) + int(weekly_extra),
                "round_trip_pair_budget_lots": pair_budget,
                "round_trip_budget_utilization": budget_utilization,
                "sized_round_trip_budget_lots": sized_pair_budget,
                "planned_round_trip_lots": planned_round_trip_lots,
                "buy_lots": buy_lots,
                "sell_lots": sell_lots,
                "gross_turnover_lots": gross_turnover,
                "round_trip_lots": round_trip_lots,
                "turnover_setup_planned": bool(
                    setup is not None and setup.get("planned")
                ),
                "turnover_direction": direction,
                "planned_entry_price_usd": (
                    None if setup is None else float(setup["entry_price_usd"])
                ),
                "planned_exit_price_usd": (
                    None if setup is None else float(setup["exit_price_usd"])
                ),
                "entry_triggered": entry_triggered,
                "entry_fill_ratio": float(entry_fill["fill_ratio"]),
                "entry_penetration_usd_per_bbl": float(
                    entry_fill["penetration_usd_per_bbl"]
                ),
                "entry_full_fill_penetration_usd_per_bbl": float(
                    entry_fill["full_fill_penetration_usd_per_bbl"]
                ),
                "target_exit_hit": target_exit_hit,
                "target_exit_triggered": target_exit_triggered,
                "target_exit_fill_ratio": float(
                    target_exit_fill["fill_ratio"]
                ),
                "target_exit_penetration_usd_per_bbl": float(
                    target_exit_fill["penetration_usd_per_bbl"]
                ),
                "target_exit_lots": target_exit_lots,
                "weekly_close_exit_lots": weekly_close_exit_lots,
                "round_trip_entry_price_usd": entry_price,
                "round_trip_exit_price_usd": exit_price,
                "round_trip_exit_reason": exit_reason,
                "round_trip_pnl_usd": round_trip_pnl,
                "raw_buy_notional_usd": raw_buy_notional,
                "raw_sell_notional_usd": raw_sell_notional,
                "raw_average_buy_price_usd": raw_average_buy_price,
                "raw_average_sell_price_usd": raw_average_sell_price,
                "all_in_buy_price_usd": all_in_buy_price,
                "all_in_sell_price_usd": all_in_sell_price,
                "spread": spread_profile,
                "spread_cost_usd": spread_cost,
                "buy_slippage": buy_slippage,
                "sell_slippage": sell_slippage,
                "slippage_cost_usd": slippage_cost,
                "gross_fee_usd": gross_fee,
                "fee_rebate_usd": fee_rebate,
                "net_fee_usd": net_fee,
                "execution_cost_usd": execution_cost,
                "tca": {
                    "benchmark": "neutral_score_50_same_realized_orders",
                    "actual_execution_cost_usd": execution_cost,
                    "neutral_execution_cost_usd": float(
                        neutral_cost["execution_cost_usd"]
                    ),
                    "execution_value_added_usd": float(
                        neutral_cost["execution_cost_usd"]
                    ) - execution_cost,
                    "hard_cash_settlement_fee_included": False,
                },
                "net_adjustment": net_adjustment,
                "round_trip": round_trip,
                "gross_execution_pnl_before_cost_usd": (
                    net_adjustment_gross_pnl + round_trip_pnl
                ),
                "net_execution_pnl_after_cost_usd": (
                    float(net_adjustment["net_pnl_after_cost_usd"])
                    + float(round_trip["net_pnl_after_cost_usd"])
                ),
                "traded_notional_usd": week_notional,
            }
        )
    buy_lots = sum(int(item["buy_lots"]) for item in ledger)
    sell_lots = sum(int(item["sell_lots"]) for item in ledger)
    gross_turnover = buy_lots + sell_lots
    round_trip_lots = sum(int(item["round_trip_lots"]) for item in ledger)
    if buy_lots - sell_lots != net_delta:
        raise ValueError("oil strategy weekly ledger net identity failed")
    if gross_turnover != abs(net_delta) + 2 * round_trip_lots:
        raise ValueError("oil strategy weekly ledger gross identity failed")
    if gross_turnover > gross_budget:
        raise ValueError("oil strategy weekly ledger exceeded its gross budget")
    net_execution_price: float | None = None
    if net_delta:
        net_execution_price = sum(
            abs(int(item["net_delta_lots"]))
            * float(item["net_execution_price_usd"])
            for item in ledger
            if item["net_delta_lots"]
        ) / abs(net_delta)
    bucket_keys = (
        "spread_cost_usd",
        "slippage_cost_usd",
        "gross_fee_usd",
        "fee_rebate_usd",
        "net_fee_usd",
        "execution_cost_usd",
        "gross_pnl_before_cost_usd",
        "net_pnl_after_cost_usd",
    )
    net_adjustment_summary = {
        key: sum(float(item["net_adjustment"][key]) for item in ledger)
        for key in bucket_keys
    }
    round_trip_summary = {
        key: sum(float(item["round_trip"][key]) for item in ledger)
        for key in bucket_keys
    }
    net_adjustment_summary.update(
        {
            "net_delta_lots": net_delta,
            "gross_turnover_lots": abs(net_delta),
        }
    )
    round_trip_summary.update(
        {
            "round_trip_lots": round_trip_lots,
            "gross_turnover_lots": 2 * round_trip_lots,
        }
    )
    total_spread_cost = sum(
        float(item["spread_cost_usd"]) for item in ledger
    )
    total_slippage_cost = sum(
        float(item["slippage_cost_usd"]) for item in ledger
    )
    total_gross_fee = sum(float(item["gross_fee_usd"]) for item in ledger)
    total_fee_rebate = sum(
        float(item["fee_rebate_usd"]) for item in ledger
    )
    total_net_fee = sum(float(item["net_fee_usd"]) for item in ledger)
    total_execution_cost = (
        total_spread_cost + total_slippage_cost + total_net_fee
    )
    total_neutral_execution_cost = sum(
        float(item["tca"]["neutral_execution_cost_usd"]) for item in ledger
    )
    total_gross_execution_pnl = sum(
        float(item["gross_execution_pnl_before_cost_usd"])
        for item in ledger
    )
    total_net_execution_pnl = sum(
        float(item["net_execution_pnl_after_cost_usd"]) for item in ledger
    )
    if not math.isclose(
        total_gross_execution_pnl - total_execution_cost,
        total_net_execution_pnl,
        abs_tol=1e-4,
    ):
        raise ValueError("oil strategy weekly execution PnL does not reconcile")
    return {
        "weeks": ledger,
        "net_delta_lots": net_delta,
        "net_execution_price_usd": net_execution_price,
        "buy_lots": buy_lots,
        "sell_lots": sell_lots,
        "gross_turnover_lots": gross_turnover,
        "round_trip_lots": round_trip_lots,
        "round_trip_pnl_usd": float(
            round_trip_summary["gross_pnl_before_cost_usd"]
        ),
        "net_adjustment": net_adjustment_summary,
        "round_trip": round_trip_summary,
        "gross_execution_pnl_before_cost_usd": total_gross_execution_pnl,
        "net_execution_pnl_after_cost_usd": total_net_execution_pnl,
        "spread_cost_usd": total_spread_cost,
        "slippage_cost_usd": total_slippage_cost,
        "gross_fee_usd": total_gross_fee,
        "fee_rebate_usd": total_fee_rebate,
        "net_fee_usd": total_net_fee,
        "execution_cost_usd": total_execution_cost,
        "tca": {
            "benchmark": "neutral_score_50_same_realized_orders",
            "actual_execution_cost_usd": total_execution_cost,
            "neutral_execution_cost_usd": total_neutral_execution_cost,
            "execution_value_added_usd": (
                total_neutral_execution_cost - total_execution_cost
            ),
            "hard_cash_settlement_fee_included": False,
        },
        "traded_notional_usd": sum(
            float(item["traded_notional_usd"]) for item in ledger
        ),
        "unused_gross_budget_lots": gross_budget - gross_turnover,
    }


def _nonincreasing_target(
    current_position: int,
    desired_target: int,
    position_limit: int,
) -> int:
    bounded_current = int(
        clamp(float(current_position), -float(position_limit), float(position_limit))
    )
    if current_position == 0:
        return 0
    if desired_target == 0 or current_position * desired_target <= 0:
        return 0
    if abs(desired_target) < abs(current_position):
        return int(
            clamp(float(desired_target), -abs(bounded_current), abs(bounded_current))
        )
    return bounded_current


def _decompose_delta(current_position: int, delta: int) -> tuple[int, int]:
    tentative = current_position + delta
    if delta == 0:
        return 0, 0
    if current_position == 0 or (
        tentative != 0
        and current_position * tentative > 0
        and abs(tentative) > abs(current_position)
    ):
        return 0, delta
    if tentative == 0 or (
        current_position * tentative > 0
        and abs(tentative) < abs(current_position)
    ):
        return delta, 0
    reduction = -current_position
    increase = tentative
    return reduction, increase


def settle_oil_strategy_turn(
    start_market: Mapping[str, Any],
    end_market: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    positions: Mapping[str, int] | None = None,
    equity_usd: float,
    allow_equity_exhaustion: bool = False,
) -> dict[str, Any]:
    """Execute one frozen decision over the next realized half-month window."""

    assets, config, contract = _validate_registered_assets()
    start_as_of = start_market["asOf"]
    end_as_of = end_market["asOf"]
    start_serial = _half_turn_serial(
        start_as_of["year"], start_as_of["month"], start_as_of["half"]
    )
    end_serial = _half_turn_serial(
        end_as_of["year"], end_as_of["month"], end_as_of["half"]
    )
    if end_serial != start_serial + 1:
        raise ValueError("oil strategy settlement requires adjacent half-month cutoffs")
    if str(decision["asOf"]["label"]) != str(start_as_of["label"]):
        raise ValueError("oil strategy decision cutoff does not match settlement start")
    if start_market["identity"]["upstream_global_identity_hash"] != (
        end_market["identity"]["upstream_global_identity_hash"]
    ):
        raise ValueError("oil strategy settlement markets must share one world")

    raw_positions = dict(positions or {})
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in raw_positions.values()
    ):
        raise ValueError("oil strategy settlement positions must be integer lots")
    current_positions = {
        str(key): int(value) for key, value in raw_positions.items()
    }
    current_equity = float(equity_usd)
    if not math.isfinite(current_equity) or current_equity <= 0.0:
        raise ValueError("oil strategy settlement equity must be positive and finite")
    target_details = {
        str(item["contract_id"]): dict(item)
        for item in decision.get("targets", ())
    }
    targets = {
        contract_id: int(item["target_position_lots"])
        for contract_id, item in target_details.items()
    }
    roles = {
        str(item["contract_id"]): str(item["role"])
        for item in decision.get("targets", ())
    }
    start_contracts = _contract_map(start_market)
    end_contracts = _contract_map(end_market)
    contract_ids = sorted(
        set(current_positions) | set(targets),
        key=lambda key: (
            ROLE_ORDER.index(roles[key])
            if roles.get(key) in ROLE_ORDER
            else len(ROLE_ORDER),
            key,
        ),
    )
    specification = end_market["contractSpecification"]
    contract_size = float(specification["contract_size_bbl"])
    reference_start_price = _reference_price(start_market)
    reference_end_price = _reference_price(end_market)
    reference_price_delta = reference_end_price - reference_start_price
    tick_size = float(
        specification["minimum_price_fluctuation_usd_per_bbl"]
    )
    initial_margin_rate = float(specification["initial_margin_rate_pct"]) / 100.0
    execution_config = config["execution"]
    friction_config = config["execution_friction"]
    decision_turnover_profile = decision["strategy"]["turnover_profile"]
    decision_fee_profile = decision["strategy"]["fee_profile"]
    neutral_fee_profile = decision["strategy"].get(
        "neutral_fee_profile", decision_fee_profile
    )
    decision_execution_policy = decision.get("executionDesk", {}).get(
        "resolved_policy", {}
    )
    adjustment_speed = float(decision_turnover_profile["adjustment_speed"])
    normal_limit_utilization = float(
        decision_turnover_profile["normal_net_trade_limit_utilization"]
    )
    completion_multiplier = float(
        dict(
            decision_execution_policy.get("completion_reliability", {})
        ).get("normal_trade_completion_multiplier", 1.0)
    )
    completed_normal_limit_utilization = min(
        1.0, normal_limit_utilization * completion_multiplier
    )
    forced_limit_utilization = float(
        execution_config["forced_reduction_trade_limit_utilization"]
    )
    gross_cap_lots = int(
        end_market["participantLimitsPolicy"]["all_contract_gross_position_cap_lots"]
    )

    provisional: dict[str, dict[str, Any]] = {}
    for contract_id in contract_ids:
        starting_position = int(current_positions.get(contract_id, 0))
        desired_target = int(targets.get(contract_id, 0))
        start_contract = start_contracts.get(contract_id)
        end_contract = end_contracts.get(contract_id)
        if starting_position != 0 and (start_contract is None or end_contract is None):
            raise ValueError("oil strategy cannot mark an unavailable open contract")
        if end_contract is None:
            continue
        limits = end_contract["participantLimits"]
        target_detail = target_details.get(contract_id, {})
        position_limit = int(limits["single_contract_position_limit_lots"])
        final_settlement = (
            int(end_contract["expiry_year"]) == int(end_as_of["year"])
            and int(end_contract["expiry_month"]) == int(end_as_of["month"])
            and int(end_as_of["half"]) == 2
        )
        if final_settlement:
            compliant_target = 0
            required_reduction = -starting_position
            planned_delta = 0
            requested_delta = 0
        else:
            compliant_target = int(
                clamp(float(desired_target), -position_limit, position_limit)
            )
            if not bool(limits["new_trades_allowed"]):
                compliant_target = _nonincreasing_target(
                    starting_position, compliant_target, position_limit
                )
            required_position = int(
                clamp(float(starting_position), -position_limit, position_limit)
            )
            required_reduction = required_position - starting_position
            discretionary_gap = compliant_target - required_position
            planned_delta = required_reduction + int(
                round(adjustment_speed * discretionary_gap)
            )
            requested_delta = planned_delta
        reduction_delta, increase_delta = _decompose_delta(
            starting_position, requested_delta
        )
        provisional[contract_id] = {
            "contract_id": contract_id,
            "role": roles.get(contract_id, "legacy_exit"),
            "strategy_intent_target_lots": int(
                target_detail.get(
                    "strategy_intent_target_position_lots", desired_target
                )
            ),
            "strategy_risk_approved_target_lots": int(
                target_detail.get(
                    "strategy_risk_approved_target_position_lots", desired_target
                )
            ),
            "company_risk_approved_target_lots": int(
                target_detail.get(
                    "company_risk_approved_target_position_lots", desired_target
                )
            ),
            "starting_position_lots": starting_position,
            "desired_target_lots": desired_target,
            "compliant_target_lots": compliant_target,
            "required_risk_reduction_lots": required_reduction,
            "planned_delta_lots": planned_delta,
            "requested_delta_lots": requested_delta,
            "reduction_delta_lots": reduction_delta,
            "increase_delta_lots": increase_delta,
            "position_limit_lots": position_limit,
            "hard_turn_trade_limit_lots": int(limits["turn_trade_limit_lots"]),
            "planned_gross_turnover_budget_lots": int(
                target_detail.get("gross_turnover_budget_lots", 0)
            ),
            "weekly_turnover_setups": list(
                target_detail.get("weekly_turnover_setups", ())
            ),
            "new_trades_allowed": bool(limits["new_trades_allowed"]),
            "binding_position_rule": str(limits["binding_position_rule"]),
            "final_settlement": final_settlement,
            "start_price_usd": (
                float(end_contract["price_usd"])
                if start_contract is None
                else float(start_contract["price_usd"])
            ),
            "end_price_usd": float(end_contract["price_usd"]),
        }

    executed: dict[str, int] = {key: 0 for key in provisional}
    for contract_id in contract_ids:
        item = provisional.get(contract_id)
        if item is None or item["final_settlement"]:
            continue
        hard_limit = int(item["hard_turn_trade_limit_lots"])
        if hard_limit <= 0:
            continue
        reduction = int(item["reduction_delta_lots"])
        if reduction:
            utilization = (
                forced_limit_utilization
                if int(item["required_risk_reduction_lots"]) != 0
                else completed_normal_limit_utilization
            )
            reduction_limit = math.floor(hard_limit * utilization)
            executed[contract_id] += int(
                math.copysign(min(abs(reduction), reduction_limit), reduction)
            )

    gross_after_reductions = sum(
        abs(int(item["starting_position_lots"]) + executed[contract_id])
        for contract_id, item in provisional.items()
        if not item["final_settlement"]
    )
    gross_room = max(0, gross_cap_lots - gross_after_reductions)
    for contract_id in contract_ids:
        item = provisional.get(contract_id)
        if (
            item is None
            or item["final_settlement"]
            or not bool(item["new_trades_allowed"])
        ):
            continue
        increase = int(item["increase_delta_lots"])
        if not increase or gross_room <= 0:
            continue
        hard_limit = int(item["hard_turn_trade_limit_lots"])
        normal_limit = math.floor(
            hard_limit * completed_normal_limit_utilization
        )
        remaining_trade_room = max(0, normal_limit - abs(executed[contract_id]))
        current_after_reduction = (
            int(item["starting_position_lots"]) + executed[contract_id]
        )
        single_room = max(
            0,
            int(item["position_limit_lots"]) - abs(current_after_reduction),
        )
        amount = min(abs(increase), remaining_trade_room, single_room, gross_room)
        if amount:
            executed[contract_id] += int(math.copysign(amount, increase))
            gross_room -= amount

    ending_positions: dict[str, int] = {}
    reports: list[dict[str, Any]] = []
    turn_pnl = 0.0
    traded_lots = 0
    net_traded_lots = 0
    total_buy_lots = 0
    total_sell_lots = 0
    total_round_trip_lots = 0
    total_round_trip_pnl = 0.0
    total_carry_gross_pnl = 0.0
    total_direction_carry_gross_pnl = 0.0
    total_contract_selection_gross_pnl = 0.0
    total_net_adjustment_gross_pnl = 0.0
    total_net_adjustment_execution_cost = 0.0
    total_net_adjustment_net_pnl = 0.0
    total_round_trip_execution_cost = 0.0
    total_round_trip_net_pnl = 0.0
    total_gross_pnl_before_cost = 0.0
    total_spread_cost = 0.0
    total_slippage_cost = 0.0
    total_gross_fee = 0.0
    total_fee_rebate = 0.0
    total_net_fee = 0.0
    total_cash_settlement_fee = 0.0
    total_execution_cost = 0.0
    total_neutral_execution_cost = 0.0
    settled_lots = 0
    traded_notional = 0.0
    for contract_id in contract_ids:
        item = provisional.get(contract_id)
        if item is None:
            continue
        starting_position = int(item["starting_position_lots"])
        end_price = float(item["end_price_usd"])
        start_price = float(item["start_price_usd"])
        market_delta = int(executed[contract_id])
        execution_price: float | None = None
        execution_weeks: list[dict[str, Any]] = []
        weekly_ledger: dict[str, Any] = {
            "weeks": [],
            "net_delta_lots": market_delta,
            "net_execution_price_usd": None,
            "buy_lots": 0,
            "sell_lots": 0,
            "gross_turnover_lots": 0,
            "round_trip_lots": 0,
            "round_trip_pnl_usd": 0.0,
            "net_adjustment": {
                "net_delta_lots": market_delta,
                "gross_turnover_lots": 0,
                "spread_cost_usd": 0.0,
                "slippage_cost_usd": 0.0,
                "gross_fee_usd": 0.0,
                "fee_rebate_usd": 0.0,
                "net_fee_usd": 0.0,
                "execution_cost_usd": 0.0,
                "gross_pnl_before_cost_usd": 0.0,
                "net_pnl_after_cost_usd": 0.0,
            },
            "round_trip": {
                "round_trip_lots": 0,
                "gross_turnover_lots": 0,
                "spread_cost_usd": 0.0,
                "slippage_cost_usd": 0.0,
                "gross_fee_usd": 0.0,
                "fee_rebate_usd": 0.0,
                "net_fee_usd": 0.0,
                "execution_cost_usd": 0.0,
                "gross_pnl_before_cost_usd": 0.0,
                "net_pnl_after_cost_usd": 0.0,
            },
            "gross_execution_pnl_before_cost_usd": 0.0,
            "net_execution_pnl_after_cost_usd": 0.0,
            "spread_cost_usd": 0.0,
            "slippage_cost_usd": 0.0,
            "gross_fee_usd": 0.0,
            "fee_rebate_usd": 0.0,
            "net_fee_usd": 0.0,
            "execution_cost_usd": 0.0,
            "traded_notional_usd": 0.0,
            "unused_gross_budget_lots": 0,
            "tca": {
                "benchmark": "neutral_score_50_same_realized_orders",
                "actual_execution_cost_usd": 0.0,
                "neutral_execution_cost_usd": 0.0,
                "execution_value_added_usd": 0.0,
                "hard_cash_settlement_fee_included": False,
            },
        }
        hard_turn_limit = int(item["hard_turn_trade_limit_lots"])
        planned_gross_budget = int(item["planned_gross_turnover_budget_lots"])
        effective_gross_budget = min(
            hard_turn_limit,
            max(abs(market_delta), planned_gross_budget),
        )
        if not bool(item["new_trades_allowed"]):
            effective_gross_budget = abs(market_delta)
        if not bool(item["final_settlement"]) and effective_gross_budget > 0:
            _, execution_weeks = _aggregate_execution_price(
                end_contracts[contract_id],
                start_year=int(start_as_of["year"]),
                start_month=int(start_as_of["month"]),
                start_half=int(start_as_of["half"]),
                end_year=int(end_as_of["year"]),
                end_month=int(end_as_of["month"]),
                end_half=int(end_as_of["half"]),
            )
            weekly_ledger = _weekly_execution_ledger(
                execution_weeks,
                net_delta_lots=market_delta,
                gross_turnover_budget_lots=effective_gross_budget,
                starting_position_lots=starting_position,
                position_limit_lots=int(item["position_limit_lots"]),
                weekly_setups=list(item["weekly_turnover_setups"]),
                contract_size_bbl=contract_size,
                role=str(item["role"]),
                tick_size_usd=tick_size,
                turnover_profile=decision_turnover_profile,
                fee_profile=decision_fee_profile,
                neutral_fee_profile=neutral_fee_profile,
                friction_config=friction_config,
                settlement_price_usd=end_price,
                execution_policy=decision_execution_policy,
            )
            execution_price = weekly_ledger["net_execution_price_usd"]
        carry_gross_pnl = (
            starting_position * (end_price - start_price) * contract_size
        )
        direction_carry_gross_pnl = (
            starting_position * reference_price_delta * contract_size
        )
        contract_selection_gross_pnl = (
            carry_gross_pnl - direction_carry_gross_pnl
        )
        net_adjustment = dict(weekly_ledger["net_adjustment"])
        round_trip = dict(weekly_ledger["round_trip"])
        gross_pnl = carry_gross_pnl + float(
            weekly_ledger["gross_execution_pnl_before_cost_usd"]
        )
        if bool(item["final_settlement"]):
            ending_position = 0
            settlement_delta = -starting_position
            settled_lots += abs(settlement_delta)
        else:
            ending_position = starting_position + market_delta
            settlement_delta = 0
            if ending_position:
                ending_positions[contract_id] = ending_position
        cash_settlement_fee = (
            abs(settlement_delta)
            * float(decision_fee_profile["cash_settlement_fee_usd_per_lot"])
        )
        spread_cost = float(weekly_ledger["spread_cost_usd"])
        slippage_cost = float(weekly_ledger["slippage_cost_usd"])
        gross_fee = float(weekly_ledger["gross_fee_usd"]) + cash_settlement_fee
        fee_rebate = float(weekly_ledger["fee_rebate_usd"])
        net_fee = float(weekly_ledger["net_fee_usd"]) + cash_settlement_fee
        execution_cost = spread_cost + slippage_cost + net_fee
        pnl = gross_pnl - execution_cost
        position_excess = max(
            0, abs(ending_position) - int(item["position_limit_lots"])
        )
        turn_pnl += pnl
        contract_gross_turnover = int(weekly_ledger["gross_turnover_lots"])
        contract_round_trip_lots = int(weekly_ledger["round_trip_lots"])
        contract_buy_lots = int(weekly_ledger["buy_lots"])
        contract_sell_lots = int(weekly_ledger["sell_lots"])
        traded_lots += contract_gross_turnover
        net_traded_lots += abs(market_delta)
        total_buy_lots += contract_buy_lots
        total_sell_lots += contract_sell_lots
        total_round_trip_lots += contract_round_trip_lots
        total_round_trip_pnl += float(weekly_ledger["round_trip_pnl_usd"])
        total_carry_gross_pnl += carry_gross_pnl
        total_direction_carry_gross_pnl += direction_carry_gross_pnl
        total_contract_selection_gross_pnl += contract_selection_gross_pnl
        total_net_adjustment_gross_pnl += float(
            net_adjustment["gross_pnl_before_cost_usd"]
        )
        total_net_adjustment_execution_cost += float(
            net_adjustment["execution_cost_usd"]
        )
        total_net_adjustment_net_pnl += float(
            net_adjustment["net_pnl_after_cost_usd"]
        )
        total_round_trip_execution_cost += float(
            round_trip["execution_cost_usd"]
        )
        total_round_trip_net_pnl += float(
            round_trip["net_pnl_after_cost_usd"]
        )
        total_gross_pnl_before_cost += gross_pnl
        total_spread_cost += spread_cost
        total_slippage_cost += slippage_cost
        total_gross_fee += gross_fee
        total_fee_rebate += fee_rebate
        total_net_fee += net_fee
        total_cash_settlement_fee += cash_settlement_fee
        total_execution_cost += execution_cost
        total_neutral_execution_cost += float(
            weekly_ledger["tca"]["neutral_execution_cost_usd"]
        )
        traded_notional += float(weekly_ledger["traded_notional_usd"])
        reports.append(
            {
                **item,
                "executed_delta_lots": market_delta,
                "unfilled_delta_lots": int(item["requested_delta_lots"])
                - market_delta,
                "settlement_delta_lots": settlement_delta,
                "ending_position_lots": ending_position,
                "position_limit_excess_lots": position_excess,
                "aggregate_execution_price_usd": execution_price,
                "gross_turnover_budget_lots": effective_gross_budget,
                "buy_lots": contract_buy_lots,
                "sell_lots": contract_sell_lots,
                "gross_turnover_lots": contract_gross_turnover,
                "round_trip_lots": contract_round_trip_lots,
                "round_trip_pnl_usd": float(
                    weekly_ledger["round_trip_pnl_usd"]
                ),
                "carry_gross_pnl_usd": carry_gross_pnl,
                "direction_carry_gross_pnl_usd": direction_carry_gross_pnl,
                "contract_selection_gross_pnl_usd": (
                    contract_selection_gross_pnl
                ),
                "net_adjustment": net_adjustment,
                "round_trip": round_trip,
                "net_adjustment_gross_pnl_usd": float(
                    net_adjustment["gross_pnl_before_cost_usd"]
                ),
                "net_adjustment_execution_cost_usd": float(
                    net_adjustment["execution_cost_usd"]
                ),
                "net_adjustment_net_pnl_usd": float(
                    net_adjustment["net_pnl_after_cost_usd"]
                ),
                "round_trip_gross_pnl_usd": float(
                    round_trip["gross_pnl_before_cost_usd"]
                ),
                "round_trip_execution_cost_usd": float(
                    round_trip["execution_cost_usd"]
                ),
                "round_trip_net_pnl_usd": float(
                    round_trip["net_pnl_after_cost_usd"]
                ),
                "gross_pnl_before_cost_usd": gross_pnl,
                "spread_cost_usd": spread_cost,
                "slippage_cost_usd": slippage_cost,
                "execution_gross_fee_usd": float(
                    weekly_ledger["gross_fee_usd"]
                ),
                "cash_settlement_fee_usd": cash_settlement_fee,
                "gross_fee_usd": gross_fee,
                "fee_rebate_usd": fee_rebate,
                "net_fee_usd": net_fee,
                "execution_cost_usd": execution_cost,
                "tca": dict(weekly_ledger["tca"]),
                "net_pnl_after_cost_usd": pnl,
                "unused_gross_budget_lots": int(
                    weekly_ledger["unused_gross_budget_lots"]
                ),
                "weekly_executions": list(weekly_ledger["weeks"]),
                "execution_window_weeks": [
                    str(week["week"])
                    for week in weekly_ledger["weeks"]
                    if int(week["gross_turnover_lots"]) > 0
                ],
                "turn_pnl_usd": pnl,
                "ending_initial_margin_usd": (
                    abs(ending_position)
                    * end_price
                    * contract_size
                    * initial_margin_rate
                ),
                "clipped_by_trade_limit": abs(market_delta)
                < abs(int(item["requested_delta_lots"])),
                "clipped_gross_budget_by_market_limit": planned_gross_budget
                > hard_turn_limit,
                "fees_usd": net_fee,
                "friction_bps": (
                    0.0
                    if float(weekly_ledger["traded_notional_usd"]) <= 0.0
                    else 10_000.0
                    * (spread_cost + slippage_cost)
                    / float(weekly_ledger["traded_notional_usd"])
                ),
            }
        )

    roll_transfer_lots = _assign_roll_transfer_attribution(reports)
    total_roll_execution_gross_pnl = sum(
        float(item["roll_execution_gross_pnl_usd"]) for item in reports
    )
    total_roll_execution_cost = sum(
        float(item["roll_execution_cost_usd"]) for item in reports
    )
    total_roll_cash_settlement_fee = sum(
        float(item["roll_cash_settlement_fee_usd"]) for item in reports
    )
    total_nonroll_cash_settlement_fee = sum(
        float(item["nonroll_cash_settlement_fee_usd"]) for item in reports
    )
    total_roll_cost = total_roll_execution_cost + total_roll_cash_settlement_fee
    total_directional_rebalance_gross_pnl = sum(
        float(item["directional_rebalance_gross_pnl_usd"])
        for item in reports
    )
    total_directional_rebalance_execution_cost = sum(
        float(item["directional_rebalance_execution_cost_usd"])
        for item in reports
    )
    ending_equity = current_equity + turn_pnl
    if ending_equity <= 0.0 and not allow_equity_exhaustion:
        raise ValueError("oil strategy reference equity was exhausted")
    gross_ending_lots = sum(abs(value) for value in ending_positions.values())
    total_margin = sum(
        float(item["ending_initial_margin_usd"]) for item in reports
    )
    if total_buy_lots - total_sell_lots != sum(executed.values()):
        raise ValueError("oil strategy turn net execution identity failed")
    if total_buy_lots + total_sell_lots != traded_lots:
        raise ValueError("oil strategy turn gross execution identity failed")
    if total_fee_rebate < 0.0 or total_fee_rebate > total_gross_fee + 1e-8:
        raise ValueError("oil strategy fee rebate identity failed")
    if total_net_fee < -1e-8:
        raise ValueError("oil strategy net fee cannot be negative")
    if not math.isclose(
        total_gross_pnl_before_cost,
        total_carry_gross_pnl
        + total_net_adjustment_gross_pnl
        + total_round_trip_pnl,
        abs_tol=1e-4,
    ):
        raise ValueError("oil strategy gross PnL attribution failed")
    if not math.isclose(
        total_carry_gross_pnl,
        total_direction_carry_gross_pnl
        + total_contract_selection_gross_pnl,
        abs_tol=1e-4,
    ):
        raise ValueError("oil strategy direction-selection attribution failed")
    if not math.isclose(
        total_net_adjustment_gross_pnl,
        total_roll_execution_gross_pnl
        + total_directional_rebalance_gross_pnl,
        abs_tol=1e-4,
    ):
        raise ValueError("oil strategy roll gross attribution failed")
    if not math.isclose(
        total_net_adjustment_execution_cost,
        total_roll_execution_cost
        + total_directional_rebalance_execution_cost,
        abs_tol=1e-4,
    ):
        raise ValueError("oil strategy roll cost attribution failed")
    if not math.isclose(
        total_cash_settlement_fee,
        total_roll_cash_settlement_fee + total_nonroll_cash_settlement_fee,
        abs_tol=1e-4,
    ):
        raise ValueError("oil strategy roll settlement-fee attribution failed")
    if not math.isclose(
        total_execution_cost,
        total_net_adjustment_execution_cost
        + total_round_trip_execution_cost
        + total_cash_settlement_fee,
        abs_tol=1e-4,
    ):
        raise ValueError("oil strategy execution-cost attribution failed")
    if not math.isclose(
        turn_pnl,
        total_carry_gross_pnl
        + total_net_adjustment_net_pnl
        + total_round_trip_net_pnl
        - total_cash_settlement_fee,
        abs_tol=1e-4,
    ):
        raise ValueError("oil strategy net PnL attribution failed")
    if not math.isclose(
        turn_pnl,
        total_gross_pnl_before_cost - total_execution_cost,
        abs_tol=1e-4,
    ):
        raise ValueError("oil strategy PnL cost attribution failed")
    result = {
        "schemaVersion": "asset-simulation-oil-strategy-turn-report-v8",
        "fromAsOf": dict(start_as_of),
        "toAsOf": dict(end_as_of),
        "decision_id": str(decision["identity"]["result_hash"]),
        "marketAttribution": {
            "reference_instrument_id": str(
                start_market.get("reference", {}).get("instrument_id", "OIL-REF")
            ),
            "reference_start_price_usd": reference_start_price,
            "reference_end_price_usd": reference_end_price,
            "reference_change_pct": 100.0
            * (reference_end_price / reference_start_price - 1.0),
            "method": "named_contract_carry_decomposed_against_spot_reference_change",
        },
        "thesisInvalidation": evaluate_oil_strategy_thesis_state(
            decision, end_market
        ),
        "strategyRisk": {
            "risk_status": decision["strategyRisk"]["state"]["risk_status"],
            "drawdown_pct": decision["strategyRisk"]["state"][
                "strategy_drawdown_pct"
            ],
            "drawdown_scale": decision["strategyRisk"]["state"][
                "strategy_drawdown_scale"
            ],
            "approval_summary": decision["strategyRisk"]["approvalSummary"],
            "review_hash": decision["strategyRisk"]["identity"]["review_hash"],
            "approval_hash": decision["strategyRisk"]["identity"]["approval_hash"],
        },
        "corporateRisk": {
            "risk_status": decision["corporateRisk"]["state"]["risk_status"],
            "drawdown_pct": decision["corporateRisk"]["state"]["drawdown_pct"],
            "drawdown_scale": decision["corporateRisk"]["state"]["drawdown_scale"],
            "approval_summary": decision["corporateRisk"]["approval_summary"],
            "profile_hash": decision["corporateRisk"]["profile"]["profile_hash"],
        },
        "contracts": reports,
        "accountAfter": {
            "equity_usd": ending_equity,
            "turn_pnl_usd": turn_pnl,
            "gross_pnl_before_cost_usd": total_gross_pnl_before_cost,
            "execution_cost_usd": total_execution_cost,
            "tca": {
                "benchmark": "neutral_score_50_same_realized_orders",
                "actual_execution_cost_usd": total_execution_cost
                - total_cash_settlement_fee,
                "neutral_execution_cost_usd": total_neutral_execution_cost,
                "execution_value_added_usd": total_neutral_execution_cost
                - (total_execution_cost - total_cash_settlement_fee),
                "hard_cash_settlement_fee_included": False,
            },
            "positions": ending_positions,
            "gross_position_lots": gross_ending_lots,
            "gross_position_cap_lots": gross_cap_lots,
            "initial_margin_usd": total_margin,
            "margin_to_equity_pct": (
                None
                if ending_equity <= 0.0
                else 100.0 * total_margin / ending_equity
            ),
        },
        "executionSummary": {
            "traded_lots": traded_lots,
            "net_traded_lots": net_traded_lots,
            "buy_lots": total_buy_lots,
            "sell_lots": total_sell_lots,
            "gross_turnover_lots": traded_lots,
            "round_trip_lots": total_round_trip_lots,
            "round_trip_pnl_usd": total_round_trip_pnl,
            "carry_gross_pnl_usd": total_carry_gross_pnl,
            "direction_carry_gross_pnl_usd": total_direction_carry_gross_pnl,
            "contract_selection_gross_pnl_usd": (
                total_contract_selection_gross_pnl
            ),
            "roll_transfer_lots": roll_transfer_lots,
            "roll_execution_gross_pnl_usd": total_roll_execution_gross_pnl,
            "roll_execution_cost_usd": total_roll_execution_cost,
            "roll_cash_settlement_fee_usd": total_roll_cash_settlement_fee,
            "roll_cost_usd": total_roll_cost,
            "nonroll_cash_settlement_fee_usd": (
                total_nonroll_cash_settlement_fee
            ),
            "directional_rebalance_gross_pnl_usd": (
                total_directional_rebalance_gross_pnl
            ),
            "directional_rebalance_execution_cost_usd": (
                total_directional_rebalance_execution_cost
            ),
            "net_adjustment_gross_pnl_usd": (
                total_net_adjustment_gross_pnl
            ),
            "net_adjustment_execution_cost_usd": (
                total_net_adjustment_execution_cost
            ),
            "net_adjustment_net_pnl_usd": total_net_adjustment_net_pnl,
            "round_trip_gross_pnl_usd": total_round_trip_pnl,
            "round_trip_execution_cost_usd": (
                total_round_trip_execution_cost
            ),
            "round_trip_net_pnl_usd": total_round_trip_net_pnl,
            "gross_pnl_before_cost_usd": total_gross_pnl_before_cost,
            "spread_cost_usd": total_spread_cost,
            "slippage_cost_usd": total_slippage_cost,
            "gross_fee_usd": total_gross_fee,
            "fee_rebate_usd": total_fee_rebate,
            "net_fee_usd": total_net_fee,
            "cash_settlement_fee_usd": total_cash_settlement_fee,
            "execution_cost_usd": total_execution_cost,
            "tca": {
                "benchmark": "neutral_score_50_same_realized_orders",
                "actual_execution_cost_usd": total_execution_cost
                - total_cash_settlement_fee,
                "neutral_execution_cost_usd": total_neutral_execution_cost,
                "execution_value_added_usd": total_neutral_execution_cost
                - (total_execution_cost - total_cash_settlement_fee),
                "hard_cash_settlement_fee_included": False,
            },
            "settled_lots": settled_lots,
            "traded_notional_usd": traded_notional,
            "fee_usd": total_net_fee,
            "friction_bps": (
                0.0
                if traded_notional <= 0.0
                else 10_000.0
                * (total_spread_cost + total_slippage_cost)
                / traded_notional
            ),
            "fee_profile": decision_fee_profile,
            "completion_multiplier": completion_multiplier,
            "completed_normal_limit_utilization": (
                completed_normal_limit_utilization
            ),
            "position_limit_excess_lots": sum(
                int(item["position_limit_excess_lots"]) for item in reports
            ),
            "future_weeks_used_for_decision": False,
            "newly_realized_weeks_used_for_settlement": True,
        },
        "pnlAttribution": {
            "direction_carry_gross_pnl_usd": total_direction_carry_gross_pnl,
            "contract_selection_gross_pnl_usd": (
                total_contract_selection_gross_pnl
            ),
            "roll_execution_gross_pnl_usd": total_roll_execution_gross_pnl,
            "directional_rebalance_gross_pnl_usd": (
                total_directional_rebalance_gross_pnl
            ),
            "round_trip_gross_pnl_usd": total_round_trip_pnl,
            "gross_pnl_before_cost_usd": total_gross_pnl_before_cost,
            "roll_execution_cost_usd": total_roll_execution_cost,
            "roll_cash_settlement_fee_usd": total_roll_cash_settlement_fee,
            "roll_cost_usd": total_roll_cost,
            "directional_rebalance_execution_cost_usd": (
                total_directional_rebalance_execution_cost
            ),
            "round_trip_execution_cost_usd": total_round_trip_execution_cost,
            "nonroll_cash_settlement_fee_usd": (
                total_nonroll_cash_settlement_fee
            ),
            "cash_settlement_fee_usd": total_cash_settlement_fee,
            "execution_cost_usd": total_execution_cost,
            "net_pnl_after_cost_usd": turn_pnl,
            "roll_transfer_lots": roll_transfer_lots,
        },
    }
    identity = {
        "schema_version": "asset-simulation-oil-strategy-turn-identity-v8",
        "model_version": OIL_TRADING_STRATEGY_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_trading_strategy_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_trading_strategy_contract_hash"],
        "upstream_start_market_result_hash": start_market["identity"]["result_hash"],
        "upstream_end_market_result_hash": end_market["identity"]["result_hash"],
        "strategy_personnel_id": decision["strategy"][
            "strategy_research_profile"
        ]["appointment"]["personnel_id"],
        "strategy_profile_hash": decision["strategy"][
            "strategy_research_profile"
        ]["profile_hash"],
        "strategy_risk_review_hash": decision["strategyRisk"]["identity"][
            "review_hash"
        ],
        "strategy_risk_approval_hash": decision["strategyRisk"]["identity"][
            "approval_hash"
        ],
        "execution_personnel_id": decision.get("executionDesk", {})
        .get("profile", {})
        .get("appointment", {})
        .get("personnel_id"),
        "execution_profile_hash": decision.get("executionDesk", {})
        .get("profile", {})
        .get("profile_hash"),
        "corporate_risk_personnel_id": decision["corporateRisk"]["profile"][
            "appointment"
        ]["personnel_id"],
        "corporate_risk_profile_hash": decision["corporateRisk"]["profile"][
            "profile_hash"
        ],
        "write_back": False,
        "result_hash": sha256_json(result),
    }
    return _round_nested({"ok": True, "identity": identity, **result})


def simulate_oil_trading_strategy(
    global_run: GlobalMacroRun,
    *,
    start_year: int = 2030,
    start_month: int = 1,
    start_half: int = 1,
    end_year: int = 2031,
    end_month: int = 1,
    end_half: int = 1,
    institution_profile: Mapping[str, Any] | None = None,
    strategy_research_profile: Mapping[str, Any] | None = None,
    execution_desk_profile: Mapping[str, Any] | None = None,
    corporate_risk_profile: Mapping[str, Any] | None = None,
    strategy_risk_profile: Mapping[str, Any] | None = None,
    capital_authorization_pct_of_company_equity: float | None = None,
    turnover_intensity: float | None = None,
) -> dict[str, Any]:
    """Replay one continuous-turnover strategy through adjacent half-month turns."""

    assets, config, contract = _validate_registered_assets()
    _validate_cutoff(start_year, start_month, start_half)
    _validate_cutoff(end_year, end_month, end_half)
    start_serial = _half_turn_serial(start_year, start_month, start_half)
    end_serial = _half_turn_serial(end_year, end_month, end_half)
    if end_serial <= start_serial:
        raise ValueError("oil strategy simulation end must follow start")
    min_year = min(int(row["year"]) for row in global_run.rows)
    max_year = max(int(row["year"]) for row in global_run.rows)
    if start_year < min_year or end_year > max_year:
        raise ValueError("oil strategy simulation is outside the generated world")

    profile = (
        build_institution_profile()
        if institution_profile is None
        else dict(institution_profile)
    )
    resolved_strategy_profile, resolved_strategy_policy = (
        resolve_oil_strategy_runtime_policy(
            strategy_research_profile,
            turnover_development_override=turnover_intensity,
        )
    )
    resolved_execution_profile, resolved_execution_policy = (
        resolve_oil_execution_runtime_policy(execution_desk_profile)
    )
    resolved_corporate_risk_profile = resolve_corporate_risk_profile(
        corporate_risk_profile
    )
    resolved_strategy_risk_profile = (
        resolved_corporate_risk_profile
        if strategy_risk_profile is None
        else resolve_corporate_risk_profile(strategy_risk_profile)
    )
    positions: dict[str, int] = {}
    initial_equity = float(config["initial_reference_equity_usd"])
    equity = initial_equity
    current_market = oil_futures_payload(
        global_run,
        as_of_year=start_year,
        as_of_month=start_month,
        as_of_half=start_half,
    )
    previous_vintage: Mapping[str, Any] | None = None
    turns: list[dict[str, Any]] = []
    equity_curve = [initial_equity]
    total_traded_lots = 0
    total_net_traded_lots = 0
    total_buy_lots = 0
    total_sell_lots = 0
    total_round_trip_lots = 0
    total_round_trip_pnl = 0.0
    total_carry_gross_pnl = 0.0
    total_direction_carry_gross_pnl = 0.0
    total_contract_selection_gross_pnl = 0.0
    total_roll_transfer_lots = 0
    total_roll_execution_gross_pnl = 0.0
    total_roll_execution_cost = 0.0
    total_roll_cash_settlement_fee = 0.0
    total_roll_cost = 0.0
    total_nonroll_cash_settlement_fee = 0.0
    total_directional_rebalance_gross_pnl = 0.0
    total_directional_rebalance_execution_cost = 0.0
    total_net_adjustment_gross_pnl = 0.0
    total_net_adjustment_execution_cost = 0.0
    total_net_adjustment_net_pnl = 0.0
    total_round_trip_execution_cost = 0.0
    total_round_trip_net_pnl = 0.0
    total_gross_pnl_before_cost = 0.0
    total_spread_cost = 0.0
    total_slippage_cost = 0.0
    total_gross_fee = 0.0
    total_fee_rebate = 0.0
    total_net_fee = 0.0
    total_cash_settlement_fee = 0.0
    total_execution_cost = 0.0
    total_neutral_execution_cost = 0.0
    total_settled_lots = 0
    total_traded_notional = 0.0
    maximum_margin_to_equity = 0.0
    limit_excess_turns = 0
    fee_lookback_turns = int(
        config["execution_friction"]["fees"]["rebate_lookback_turns"]
    )
    gross_turnover_history: list[int] = []
    corporate_risk_state: dict[str, Any] | None = None
    strategy_risk_state: dict[str, Any] | None = None
    thesis_state: dict[str, Any] | None = None
    thesis_status_counts = {key: 0 for key in ("active", "watch", "invalidated")}
    corporate_risk_status_counts = {key: 0 for key in ("normal", "watch", "restricted", "reduce_only")}
    strategy_risk_status_counts = {key: 0 for key in ("normal", "watch", "restricted", "reduce_only")}
    total_company_risk_clipped_gross_lots = 0
    total_strategy_risk_clipped_gross_lots = 0

    for turn_serial in range(start_serial, end_serial):
        year, month, half = _turn_from_serial(turn_serial)
        next_year, next_month, next_half = _turn_from_serial(turn_serial + 1)
        vintage = generate_oil_short_term_forecast(
            global_run,
            as_of_year=year,
            as_of_month=month,
            as_of_half=half,
            institution_profile=profile,
            previous_vintage=previous_vintage,
        )
        decision = build_oil_strategy_decision(
            current_market,
            vintage,
            positions=positions,
            equity_usd=equity,
            strategy_research_profile=resolved_strategy_profile,
            execution_desk_profile=resolved_execution_profile,
            corporate_risk_profile=resolved_corporate_risk_profile,
            strategy_risk_profile=resolved_strategy_risk_profile,
            risk_state=corporate_risk_state,
            strategy_risk_state=strategy_risk_state,
            thesis_state=thesis_state,
            capital_authorization_pct_of_company_equity=(
                capital_authorization_pct_of_company_equity
            ),
            turnover_intensity=turnover_intensity,
            fee_state={
                "rolling_gross_turnover_lots": sum(
                    gross_turnover_history[-fee_lookback_turns:]
                )
            },
        )
        next_market = oil_futures_payload(
            global_run,
            as_of_year=next_year,
            as_of_month=next_month,
            as_of_half=next_half,
        )
        settlement = settle_oil_strategy_turn(
            current_market,
            next_market,
            decision,
            positions=positions,
            equity_usd=equity,
        )
        corporate_risk_state = dict(decision["corporateRisk"]["state"])
        strategy_risk_state = dict(decision["strategyRisk"]["state"])
        thesis_state = dict(settlement["thesisInvalidation"]["state"])
        for thesis_contract in thesis_state.get("contracts", {}).values():
            thesis_status_counts[str(thesis_contract["status"])] += 1
        strategy_risk_status_counts[
            str(strategy_risk_state["risk_status"])
        ] += 1
        total_strategy_risk_clipped_gross_lots += int(
            decision["strategyRisk"]["approvalSummary"]["clipped_gross_lots"]
        )
        corporate_risk_status_counts[
            str(corporate_risk_state["risk_status"])
        ] += 1
        total_company_risk_clipped_gross_lots += int(
            decision["corporateRisk"]["approval_summary"]["clipped_gross_lots"]
        )
        positions = {
            str(key): int(value)
            for key, value in settlement["accountAfter"]["positions"].items()
        }
        equity = float(settlement["accountAfter"]["equity_usd"])
        equity_curve.append(equity)
        total_traded_lots += int(settlement["executionSummary"]["traded_lots"])
        total_net_traded_lots += int(
            settlement["executionSummary"]["net_traded_lots"]
        )
        total_buy_lots += int(settlement["executionSummary"]["buy_lots"])
        total_sell_lots += int(settlement["executionSummary"]["sell_lots"])
        total_round_trip_lots += int(
            settlement["executionSummary"]["round_trip_lots"]
        )
        total_round_trip_pnl += float(
            settlement["executionSummary"]["round_trip_pnl_usd"]
        )
        total_carry_gross_pnl += float(
            settlement["executionSummary"]["carry_gross_pnl_usd"]
        )
        total_direction_carry_gross_pnl += float(
            settlement["executionSummary"]["direction_carry_gross_pnl_usd"]
        )
        total_contract_selection_gross_pnl += float(
            settlement["executionSummary"][
                "contract_selection_gross_pnl_usd"
            ]
        )
        total_roll_transfer_lots += int(
            settlement["executionSummary"]["roll_transfer_lots"]
        )
        total_roll_execution_gross_pnl += float(
            settlement["executionSummary"]["roll_execution_gross_pnl_usd"]
        )
        total_roll_execution_cost += float(
            settlement["executionSummary"]["roll_execution_cost_usd"]
        )
        total_roll_cash_settlement_fee += float(
            settlement["executionSummary"][
                "roll_cash_settlement_fee_usd"
            ]
        )
        total_roll_cost += float(
            settlement["executionSummary"]["roll_cost_usd"]
        )
        total_nonroll_cash_settlement_fee += float(
            settlement["executionSummary"][
                "nonroll_cash_settlement_fee_usd"
            ]
        )
        total_directional_rebalance_gross_pnl += float(
            settlement["executionSummary"][
                "directional_rebalance_gross_pnl_usd"
            ]
        )
        total_directional_rebalance_execution_cost += float(
            settlement["executionSummary"][
                "directional_rebalance_execution_cost_usd"
            ]
        )
        total_net_adjustment_gross_pnl += float(
            settlement["executionSummary"][
                "net_adjustment_gross_pnl_usd"
            ]
        )
        total_net_adjustment_execution_cost += float(
            settlement["executionSummary"][
                "net_adjustment_execution_cost_usd"
            ]
        )
        total_net_adjustment_net_pnl += float(
            settlement["executionSummary"]["net_adjustment_net_pnl_usd"]
        )
        total_round_trip_execution_cost += float(
            settlement["executionSummary"][
                "round_trip_execution_cost_usd"
            ]
        )
        total_round_trip_net_pnl += float(
            settlement["executionSummary"]["round_trip_net_pnl_usd"]
        )
        total_gross_pnl_before_cost += float(
            settlement["executionSummary"]["gross_pnl_before_cost_usd"]
        )
        total_spread_cost += float(
            settlement["executionSummary"]["spread_cost_usd"]
        )
        total_slippage_cost += float(
            settlement["executionSummary"]["slippage_cost_usd"]
        )
        total_gross_fee += float(
            settlement["executionSummary"]["gross_fee_usd"]
        )
        total_fee_rebate += float(
            settlement["executionSummary"]["fee_rebate_usd"]
        )
        total_net_fee += float(
            settlement["executionSummary"]["net_fee_usd"]
        )
        total_cash_settlement_fee += float(
            settlement["executionSummary"]["cash_settlement_fee_usd"]
        )
        total_execution_cost += float(
            settlement["executionSummary"]["execution_cost_usd"]
        )
        total_neutral_execution_cost += float(
            settlement["executionSummary"]["tca"][
                "neutral_execution_cost_usd"
            ]
        )
        total_settled_lots += int(settlement["executionSummary"]["settled_lots"])
        total_traded_notional += float(
            settlement["executionSummary"]["traded_notional_usd"]
        )
        maximum_margin_to_equity = max(
            maximum_margin_to_equity,
            float(settlement["accountAfter"]["margin_to_equity_pct"]),
        )
        if int(settlement["executionSummary"]["position_limit_excess_lots"]) > 0:
            limit_excess_turns += 1
        gross_turnover_history.append(
            int(settlement["executionSummary"]["gross_turnover_lots"])
        )
        turns.append(
            {
                "turn_index": len(turns) + 1,
                "decision": decision,
                "settlement": settlement,
            }
        )
        previous_vintage = vintage
        current_market = next_market

    peak = equity_curve[0]
    maximum_drawdown = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        maximum_drawdown = min(maximum_drawdown, value / peak - 1.0)
    profitable_turns = sum(
        float(item["settlement"]["accountAfter"]["turn_pnl_usd"]) > 0.0
        for item in turns
    )
    losing_turns = sum(
        float(item["settlement"]["accountAfter"]["turn_pnl_usd"]) < 0.0
        for item in turns
    )
    expected_net_pnl = total_gross_pnl_before_cost - total_execution_cost
    actual_net_pnl = equity - initial_equity
    if not math.isclose(actual_net_pnl, expected_net_pnl, abs_tol=0.01):
        raise ValueError("oil strategy simulation cost attribution does not reconcile")
    if total_fee_rebate > total_gross_fee + 0.01:
        raise ValueError("oil strategy simulation fee rebate exceeds gross fee")
    if total_net_fee < -0.01:
        raise ValueError("oil strategy simulation net fee cannot be negative")
    if not math.isclose(
        total_gross_pnl_before_cost,
        total_carry_gross_pnl
        + total_net_adjustment_gross_pnl
        + total_round_trip_pnl,
        abs_tol=0.01,
    ):
        raise ValueError("oil strategy simulation gross attribution failed")
    if not math.isclose(
        total_carry_gross_pnl,
        total_direction_carry_gross_pnl
        + total_contract_selection_gross_pnl,
        abs_tol=0.01,
    ):
        raise ValueError("oil strategy simulation direction-selection attribution failed")
    if not math.isclose(
        total_net_adjustment_gross_pnl,
        total_roll_execution_gross_pnl
        + total_directional_rebalance_gross_pnl,
        abs_tol=0.01,
    ):
        raise ValueError("oil strategy simulation roll gross attribution failed")
    if not math.isclose(
        total_net_adjustment_execution_cost,
        total_roll_execution_cost
        + total_directional_rebalance_execution_cost,
        abs_tol=0.01,
    ):
        raise ValueError("oil strategy simulation roll cost attribution failed")
    if not math.isclose(
        total_roll_cost,
        total_roll_execution_cost + total_roll_cash_settlement_fee,
        abs_tol=0.01,
    ):
        raise ValueError("oil strategy simulation total roll cost attribution failed")
    if not math.isclose(
        total_cash_settlement_fee,
        total_roll_cash_settlement_fee + total_nonroll_cash_settlement_fee,
        abs_tol=0.01,
    ):
        raise ValueError("oil strategy simulation settlement fee attribution failed")
    if not math.isclose(
        total_execution_cost,
        total_net_adjustment_execution_cost
        + total_round_trip_execution_cost
        + total_cash_settlement_fee,
        abs_tol=0.01,
    ):
        raise ValueError("oil strategy simulation category costs do not reconcile")
    if not math.isclose(
        actual_net_pnl,
        total_carry_gross_pnl
        + total_net_adjustment_net_pnl
        + total_round_trip_net_pnl
        - total_cash_settlement_fee,
        abs_tol=0.01,
    ):
        raise ValueError("oil strategy simulation net attribution failed")
    result = {
        "schemaVersion": "asset-simulation-oil-strategy-simulation-v8",
        "strategy": {
            "strategy_id": str(config["strategy_id"]),
            "display_name": str(config["display_name"]),
            "model_version": OIL_TRADING_STRATEGY_MODEL_VERSION,
            "strategy_research_profile": resolved_strategy_profile,
            "resolved_policy": resolved_strategy_policy,
            "turnover_profile": resolved_strategy_policy["execution"],
        },
        "institution": profile,
        "executionDesk": {
            "profile": resolved_execution_profile,
            "resolved_policy": resolved_execution_policy,
        },
        "corporateRisk": {
            "profile": resolved_corporate_risk_profile,
            "ending_state": corporate_risk_state,
        },
        "strategyRisk": {
            "review": turns[0]["decision"]["strategyRisk"]["review"],
            "committeeApproval": turns[0]["decision"]["investmentDecision"],
            "ending_state": strategy_risk_state,
            "capital_authorization_pct_of_company_equity": (
                float(
                    turns[0]["decision"]["riskBudget"][
                        "capital_authorization_pct_of_company_equity"
                    ]
                )
            ),
        },
        "thesisInvalidation": {
            "ending_state": thesis_state,
            "status_counts": thesis_status_counts,
            "configured_research_ability_used": False,
        },
        "period": {
            "start": f"{start_year:04d}-{start_month:02d}-H{start_half}",
            "end": f"{end_year:04d}-{end_month:02d}-H{end_half}",
            "completed_turns": len(turns),
        },
        "summary": {
            "initial_equity_usd": initial_equity,
            "ending_equity_usd": equity,
            "net_pnl_usd": equity - initial_equity,
            "return_pct": 100.0 * (equity / initial_equity - 1.0),
            "maximum_drawdown_pct": 100.0 * maximum_drawdown,
            "profitable_turns": profitable_turns,
            "losing_turns": losing_turns,
            "flat_turns": len(turns) - profitable_turns - losing_turns,
            "total_traded_lots": total_traded_lots,
            "total_net_traded_lots": total_net_traded_lots,
            "total_buy_lots": total_buy_lots,
            "total_sell_lots": total_sell_lots,
            "total_round_trip_lots": total_round_trip_lots,
            "round_trip_pnl_usd": total_round_trip_pnl,
            "carry_gross_pnl_usd": total_carry_gross_pnl,
            "direction_carry_gross_pnl_usd": total_direction_carry_gross_pnl,
            "contract_selection_gross_pnl_usd": (
                total_contract_selection_gross_pnl
            ),
            "roll_transfer_lots": total_roll_transfer_lots,
            "roll_execution_gross_pnl_usd": total_roll_execution_gross_pnl,
            "roll_execution_cost_usd": total_roll_execution_cost,
            "roll_cash_settlement_fee_usd": total_roll_cash_settlement_fee,
            "roll_cost_usd": total_roll_cost,
            "nonroll_cash_settlement_fee_usd": (
                total_nonroll_cash_settlement_fee
            ),
            "directional_rebalance_gross_pnl_usd": (
                total_directional_rebalance_gross_pnl
            ),
            "directional_rebalance_execution_cost_usd": (
                total_directional_rebalance_execution_cost
            ),
            "net_adjustment_gross_pnl_usd": (
                total_net_adjustment_gross_pnl
            ),
            "net_adjustment_execution_cost_usd": (
                total_net_adjustment_execution_cost
            ),
            "net_adjustment_net_pnl_usd": total_net_adjustment_net_pnl,
            "round_trip_gross_pnl_usd": total_round_trip_pnl,
            "round_trip_execution_cost_usd": (
                total_round_trip_execution_cost
            ),
            "round_trip_net_pnl_usd": total_round_trip_net_pnl,
            "gross_pnl_before_cost_usd": total_gross_pnl_before_cost,
            "spread_cost_usd": total_spread_cost,
            "slippage_cost_usd": total_slippage_cost,
            "gross_fee_usd": total_gross_fee,
            "fee_rebate_usd": total_fee_rebate,
            "net_fee_usd": total_net_fee,
            "cash_settlement_fee_usd": total_cash_settlement_fee,
            "execution_cost_usd": total_execution_cost,
            "tca": {
                "benchmark": "neutral_score_50_same_realized_orders",
                "actual_execution_cost_usd": total_execution_cost
                - total_cash_settlement_fee,
                "neutral_execution_cost_usd": total_neutral_execution_cost,
                "execution_value_added_usd": total_neutral_execution_cost
                - (total_execution_cost - total_cash_settlement_fee),
                "hard_cash_settlement_fee_included": False,
            },
            "total_settled_lots": total_settled_lots,
            "total_traded_notional_usd": total_traded_notional,
            "maximum_margin_to_equity_pct": maximum_margin_to_equity,
            "position_limit_excess_turns": limit_excess_turns,
            "corporate_risk_status_counts": corporate_risk_status_counts,
            "strategy_risk_status_counts": strategy_risk_status_counts,
            "strategy_risk_clipped_gross_lots": (
                total_strategy_risk_clipped_gross_lots
            ),
            "company_risk_clipped_gross_lots": (
                total_company_risk_clipped_gross_lots
            ),
            "ending_positions": positions,
            "fees_usd": total_net_fee,
            "friction_bps": (
                0.0
                if total_traded_notional <= 0.0
                else 10_000.0
                * (total_spread_cost + total_slippage_cost)
                / total_traded_notional
            ),
            "ending_fee_profile": _resolve_fee_profile(
                {
                    "rolling_gross_turnover_lots": sum(
                        gross_turnover_history[-fee_lookback_turns:]
                    )
                },
                config["execution_friction"],
                execution_policy=resolved_execution_policy,
            ),
        },
        "turns": turns,
    }
    identity = {
        "schema_version": "asset-simulation-oil-strategy-simulation-identity-v7",
        "model_version": OIL_TRADING_STRATEGY_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_trading_strategy_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_trading_strategy_contract_hash"],
        "upstream_global_identity_hash": global_run.identity["identity_hash"],
        "seed": global_run.seed,
        "strategy_id": config["strategy_id"],
        "strategy_personnel_id": resolved_strategy_profile["appointment"][
            "personnel_id"
        ],
        "strategy_profile_hash": resolved_strategy_profile["profile_hash"],
        "strategy_risk_review_hash": turns[0]["decision"]["strategyRisk"][
            "identity"
        ]["review_hash"],
        "strategy_risk_approval_hash": turns[0]["decision"]["strategyRisk"][
            "identity"
        ]["approval_hash"],
        "execution_personnel_id": resolved_execution_profile["appointment"][
            "personnel_id"
        ],
        "execution_profile_hash": resolved_execution_profile["profile_hash"],
        "corporate_risk_personnel_id": resolved_corporate_risk_profile[
            "appointment"
        ]["personnel_id"],
        "corporate_risk_profile_hash": resolved_corporate_risk_profile[
            "profile_hash"
        ],
        "turnover_intensity": resolved_strategy_policy["execution"][
            "turnover_intensity"
        ],
        "institution_id": profile["institution_id"],
        "start_turn": result["period"]["start"],
        "end_turn": result["period"]["end"],
        "write_back": False,
        "result_hash": sha256_json(result),
    }
    return _round_nested({"ok": True, "identity": identity, **result})
