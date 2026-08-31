"""Real-leg execution adapter for the short-horizon oil calendar spread.

This module is the Gate-A bridge between a frozen v0.2.2 calendar-spread
``pairedExecutionMandate`` and the existing appointed oil execution desk.  It
executes two real named futures legs over the next realized half-month, using
only the newly realized weekly OHLC/volume, the frozen strategy mandate and the
appointed execution profile.

The adapter deliberately stops before settlement ownership.  It returns fills,
execution costs and a strategy-book ending-position preview, but does not mutate
the Strategy Book or Formal Account and does not own cash, margin or financing.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .math_utils import clamp
from .oil_calendar_spread_strategy import _extract_spread_position
from .oil_calendar_spread_strategy_v22 import (
    OIL_CALENDAR_SPREAD_STRATEGY_V22_ID,
    OIL_CALENDAR_SPREAD_STRATEGY_V22_MODEL_VERSION,
)
from .oil_execution_desk import (
    OIL_EXECUTION_DESK_MODEL_VERSION,
    adjust_visible_execution_weights,
    resolve_oil_execution_runtime_policy,
)
from .oil_strategy_book import resolve_oil_strategy_book
from .oil_trading_strategy import (
    OIL_TRADING_STRATEGY_MODEL_VERSION,
    _execution_cost_bucket,
    _resolve_fee_profile,
    _slippage_bps,
    _weekly_spread_profile,
)
from .registry import load_registered_assets, sha256_json


OIL_CALENDAR_SPREAD_PAIR_EXECUTION_MODEL_VERSION = (
    "asset-simulation-oil-calendar-spread-pair-execution-v0.1.0"
)
OIL_CALENDAR_SPREAD_PAIR_EXECUTION_CONTRACT_ID = (
    "oil_calendar_spread_pair_execution_v1"
)


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("calendar-spread pair execution contains a non-finite value")
        return round(value, 8)
    return value


def _assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_calendar_spread_pair_execution_config"]
    contract = assets["oil_calendar_spread_pair_execution_contract"]
    if config["model_version"] != OIL_CALENDAR_SPREAD_PAIR_EXECUTION_MODEL_VERSION:
        raise ValueError("registered calendar-spread pair execution config version mismatch")
    if contract["contract_id"] != OIL_CALENDAR_SPREAD_PAIR_EXECUTION_CONTRACT_ID:
        raise ValueError("registered calendar-spread pair execution contract id mismatch")
    if contract["model_version"] != OIL_CALENDAR_SPREAD_PAIR_EXECUTION_MODEL_VERSION:
        raise ValueError("registered calendar-spread pair execution contract version mismatch")
    if config["strategy_id"] != OIL_CALENDAR_SPREAD_STRATEGY_V22_ID:
        raise ValueError("calendar-spread pair execution strategy id mismatch")
    if config["strategy_decision_model_version"] != OIL_CALENDAR_SPREAD_STRATEGY_V22_MODEL_VERSION:
        raise ValueError("calendar-spread pair execution decision owner mismatch")
    if config["execution_desk_model_version"] != OIL_EXECUTION_DESK_MODEL_VERSION:
        raise ValueError("calendar-spread pair execution desk version mismatch")
    if config["friction_primitive_model_version"] != OIL_TRADING_STRATEGY_MODEL_VERSION:
        raise ValueError("calendar-spread pair execution friction owner mismatch")
    if bool(config["window_policy"]["synthetic_spread_fill_allowed"]):
        raise ValueError("calendar-spread pair execution may not create synthetic fills")
    return assets, config, contract


def _month_serial(year: int, month: int) -> int:
    return int(year) * 12 + int(month) - 1


def _half_turn_serial(year: int, month: int, half: int) -> int:
    return _month_serial(year, month) * 2 + int(half) - 1


def _week_serial(year: int, month: int, week: int) -> int:
    return _month_serial(year, month) * 4 + int(week) - 1


def _cutoff_week_serial(as_of: Mapping[str, Any]) -> int:
    half = int(as_of["half"])
    if half not in (1, 2):
        raise ValueError("calendar-spread execution half must be 1 or 2")
    return _week_serial(
        int(as_of["year"]),
        int(as_of["month"]),
        2 if half == 1 else 4,
    )


def _contract_map(market: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in market.get("curve", {}).get("contracts", ()):
        item = dict(value)
        contract_id = str(item.get("contract_id", ""))
        if not contract_id or contract_id in result:
            raise ValueError("calendar-spread execution market contract ids must be unique")
        result[contract_id] = item
    return result


def _visible_weeks(contract: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for month_value in contract.get("monthly", ()):
        month = dict(month_value)
        year = int(month["year"])
        month_number = int(month["month"])
        for week_value in month.get("weekly", ()):
            week = dict(week_value)
            week_number = int(week["week"])
            serial = _week_serial(year, month_number, week_number)
            packed = {
                "year": year,
                "month": month_number,
                "week": week_number,
                "week_serial": serial,
                "open": float(week["open"]),
                "high": float(week["high"]),
                "low": float(week["low"]),
                "close": float(week["close"]),
                "volume_lots": int(week.get("volume_lots", 0)),
            }
            if min(packed[key] for key in ("open", "high", "low", "close")) <= 0.0:
                raise ValueError("calendar-spread execution weekly prices must be positive")
            result[serial] = packed
    return result


def _aligned_new_execution_windows(
    main_contract: Mapping[str, Any],
    next_contract: Mapping[str, Any],
    *,
    start_as_of: Mapping[str, Any],
    end_as_of: Mapping[str, Any],
    expected_count: int,
) -> list[dict[str, Any]]:
    start_cutoff = _cutoff_week_serial(start_as_of)
    end_cutoff = _cutoff_week_serial(end_as_of)
    main_weeks = _visible_weeks(main_contract)
    next_weeks = _visible_weeks(next_contract)
    serials = sorted(
        serial
        for serial in set(main_weeks).intersection(next_weeks)
        if start_cutoff < serial <= end_cutoff
    )
    if len(serials) != int(expected_count):
        raise ValueError(
            "calendar-spread pair execution requires exactly the newly realized shared weekly windows"
        )
    return [
        {
            "week_serial": serial,
            "label": (
                f"{main_weeks[serial]['year']:04d}-"
                f"{main_weeks[serial]['month']:02d}-W{main_weeks[serial]['week']}"
            ),
            "main": main_weeks[serial],
            "next_main": next_weeks[serial],
        }
        for serial in serials
    ]


def _allocate_integer(total: int, weights: Sequence[float]) -> list[int]:
    amount = int(total)
    if amount < 0:
        raise ValueError("calendar-spread execution allocation must be nonnegative")
    if not weights:
        return []
    positive = [max(0.0, float(value)) for value in weights]
    denominator = sum(positive)
    if denominator <= 0.0:
        positive = [1.0] * len(positive)
        denominator = float(len(positive))
    raw = [amount * value / denominator for value in positive]
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


def _signed_allocations(total_delta: int, weights: Sequence[float]) -> list[int]:
    delta = int(total_delta)
    absolute = _allocate_integer(abs(delta), weights)
    sign = 1 if delta > 0 else -1 if delta < 0 else 0
    return [sign * value for value in absolute]


def _neutral_week_price(week: Mapping[str, Any]) -> float:
    price = 0.25 * sum(float(week[key]) for key in ("open", "high", "low", "close"))
    return clamp(price, float(week["low"]), float(week["high"]))


def _execute_leg_bucket(
    *,
    delta_lots: int,
    role: str,
    week: Mapping[str, Any],
    contract_size_bbl: float,
    tick_size_usd: float,
    turnover_profile: Mapping[str, Any],
    fee_profile: Mapping[str, Any],
    neutral_fee_profile: Mapping[str, Any],
    friction_config: Mapping[str, Any],
    execution_policy: Mapping[str, Any],
    bucket: str,
) -> dict[str, Any]:
    delta = int(delta_lots)
    lots = abs(delta)
    neutral_price = _neutral_week_price(week)
    if lots == 0:
        return {
            "bucket": bucket,
            "delta_lots": 0,
            "side": None,
            "neutral_price_usd": neutral_price,
            "all_in_price_usd": None,
            "raw_notional_usd": 0.0,
            "spread_cost_usd": 0.0,
            "slippage_cost_usd": 0.0,
            "gross_fee_usd": 0.0,
            "fee_rebate_usd": 0.0,
            "net_fee_usd": 0.0,
            "execution_cost_usd": 0.0,
            "neutral_execution_cost_usd": 0.0,
            "execution_value_added_usd": 0.0,
            "market_volume_lots": int(week.get("volume_lots", 0)),
        }
    side = "buy" if delta > 0 else "sell"
    buy_lots = lots if delta > 0 else 0
    sell_lots = lots if delta < 0 else 0
    raw_notional = lots * neutral_price * contract_size_bbl
    spread = _weekly_spread_profile(
        role,
        week,
        tick_size_usd=tick_size_usd,
        friction_config=friction_config,
    )
    slippage = _slippage_bps(
        lots,
        int(week.get("volume_lots", 0)),
        float(spread["weekly_volatility"]),
        turnover_profile,
        friction_config,
        execution_policy=execution_policy,
        role=role,
    )
    neutral_slippage = _slippage_bps(
        lots,
        int(week.get("volume_lots", 0)),
        float(spread["weekly_volatility"]),
        turnover_profile,
        friction_config,
        role=role,
    )
    actual = _execution_cost_bucket(
        buy_lots=buy_lots,
        sell_lots=sell_lots,
        raw_buy_notional_usd=raw_notional if buy_lots else 0.0,
        raw_sell_notional_usd=raw_notional if sell_lots else 0.0,
        full_spread_usd_per_bbl=float(spread["full_spread_usd_per_bbl"]),
        buy_slippage_bps=float(slippage["slippage_bps"]) if buy_lots else 0.0,
        sell_slippage_bps=float(slippage["slippage_bps"]) if sell_lots else 0.0,
        contract_size_bbl=contract_size_bbl,
        fee_profile=fee_profile,
        execution_policy=execution_policy,
        role=role,
    )
    neutral = _execution_cost_bucket(
        buy_lots=buy_lots,
        sell_lots=sell_lots,
        raw_buy_notional_usd=raw_notional if buy_lots else 0.0,
        raw_sell_notional_usd=raw_notional if sell_lots else 0.0,
        full_spread_usd_per_bbl=float(spread["full_spread_usd_per_bbl"]),
        buy_slippage_bps=float(neutral_slippage["slippage_bps"]) if buy_lots else 0.0,
        sell_slippage_bps=float(neutral_slippage["slippage_bps"]) if sell_lots else 0.0,
        contract_size_bbl=contract_size_bbl,
        fee_profile=neutral_fee_profile,
        role=role,
    )
    execution_cost = float(actual["execution_cost_usd"])
    all_in_shift = execution_cost / (lots * contract_size_bbl)
    all_in_price = neutral_price + all_in_shift if delta > 0 else neutral_price - all_in_shift
    return {
        "bucket": bucket,
        "delta_lots": delta,
        "side": side,
        "neutral_price_usd": neutral_price,
        "all_in_price_usd": all_in_price,
        "raw_notional_usd": raw_notional,
        "market_volume_lots": int(week.get("volume_lots", 0)),
        "spread": spread,
        "slippage": slippage,
        "spread_cost_usd": float(actual["spread_cost_usd"]),
        "slippage_cost_usd": float(actual["slippage_cost_usd"]),
        "gross_fee_usd": float(actual["gross_fee_usd"]),
        "fee_rebate_usd": float(actual["fee_rebate_usd"]),
        "net_fee_usd": float(actual["net_fee_usd"]),
        "execution_cost_usd": execution_cost,
        "neutral_execution_cost_usd": float(neutral["execution_cost_usd"]),
        "execution_value_added_usd": float(neutral["execution_cost_usd"]) - execution_cost,
    }


def _aggregate_leg(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    active = [dict(item) for item in records if int(item["delta_lots"]) != 0]
    executed_delta = sum(int(item["delta_lots"]) for item in active)
    buy_lots = sum(max(0, int(item["delta_lots"])) for item in active)
    sell_lots = sum(max(0, -int(item["delta_lots"])) for item in active)
    gross_lots = buy_lots + sell_lots
    raw_notional = sum(float(item["raw_notional_usd"]) for item in active)
    cost_keys = (
        "spread_cost_usd",
        "slippage_cost_usd",
        "gross_fee_usd",
        "fee_rebate_usd",
        "net_fee_usd",
        "execution_cost_usd",
        "neutral_execution_cost_usd",
        "execution_value_added_usd",
    )
    costs = {key: sum(float(item[key]) for item in active) for key in cost_keys}
    average_neutral = None
    average_all_in = None
    if gross_lots:
        average_neutral = sum(
            abs(int(item["delta_lots"])) * float(item["neutral_price_usd"])
            for item in active
        ) / gross_lots
        average_all_in = sum(
            abs(int(item["delta_lots"])) * float(item["all_in_price_usd"])
            for item in active
        ) / gross_lots
    return {
        "executed_delta_lots": executed_delta,
        "buy_lots": buy_lots,
        "sell_lots": sell_lots,
        "gross_turnover_lots": gross_lots,
        "traded_notional_usd": raw_notional,
        "average_neutral_execution_price_usd": average_neutral,
        "average_all_in_execution_price_usd": average_all_in,
        **costs,
        "fills": active,
    }


def execute_oil_calendar_spread_pair_turn(
    start_market: Mapping[str, Any],
    end_market: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    strategy_book: Mapping[str, Any],
    execution_desk_profile: Mapping[str, Any] | None = None,
    fee_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one frozen calendar-spread pair mandate on two real futures legs."""

    assets, config, contract = _assets()
    if str(decision.get("identity", {}).get("model_version", "")) != OIL_CALENDAR_SPREAD_STRATEGY_V22_MODEL_VERSION:
        raise ValueError("pair execution requires a calendar-spread v0.2.2 decision")
    if str(decision.get("identity", {}).get("strategy_id", "")) != OIL_CALENDAR_SPREAD_STRATEGY_V22_ID:
        raise ValueError("pair execution received the wrong strategy id")
    if not bool(start_market.get("ok")) or not bool(end_market.get("ok")):
        raise ValueError("pair execution requires successful start and end market payloads")

    start_as_of = dict(start_market["asOf"])
    end_as_of = dict(end_market["asOf"])
    start_serial = _half_turn_serial(start_as_of["year"], start_as_of["month"], start_as_of["half"])
    end_serial = _half_turn_serial(end_as_of["year"], end_as_of["month"], end_as_of["half"])
    if end_serial != start_serial + 1:
        raise ValueError("pair execution requires adjacent half-month cutoffs")
    if str(decision["asOf"]["label"]) != str(start_as_of["label"]):
        raise ValueError("pair execution decision cutoff does not match start market")
    if str(start_market["identity"]["upstream_global_identity_hash"]) != str(
        end_market["identity"]["upstream_global_identity_hash"]
    ):
        raise ValueError("pair execution markets must share one deterministic world")
    if str(decision["identity"]["upstream_global_identity_hash"]) != str(
        start_market["identity"]["upstream_global_identity_hash"]
    ):
        raise ValueError("pair execution decision belongs to another market world")

    decision_book = dict(decision["strategyBook"])
    resolved_book = resolve_oil_strategy_book(
        strategy_book,
        expected_institution_id=str(decision_book["institution_id"]),
        expected_strategy_id=OIL_CALENDAR_SPREAD_STRATEGY_V22_ID,
    )
    if str(resolved_book["identity"]["identity_hash"]) != str(
        decision_book["book_identity_hash"]
    ):
        raise ValueError("pair execution strategy book changed after decision freeze")

    legs = {key: dict(decision["legs"][key]) for key in ("main", "next_main")}
    main_id = str(legs["main"]["contract_id"])
    next_id = str(legs["next_main"]["contract_id"])
    start_contracts = _contract_map(start_market)
    end_contracts = _contract_map(end_market)
    if main_id not in start_contracts or next_id not in start_contracts:
        raise ValueError("pair execution start market lacks one frozen strategy leg")
    if main_id not in end_contracts or next_id not in end_contracts:
        raise ValueError("pair execution end market lacks one frozen strategy leg")

    mandate = dict(decision["pairedExecutionMandate"])
    pair_request = int(mandate["requested_pair_delta_units"])
    if int(mandate["requested_main_delta_lots"]) != pair_request or int(
        mandate["requested_next_main_delta_lots"]
    ) != -pair_request:
        raise ValueError("pair execution mandate is not a balanced one-to-one request")
    remediation = dict(mandate.get("imbalanceRemediation", {}))
    remediation_main = int(remediation.get("requested_main_delta_lots", 0))
    remediation_next = int(remediation.get("requested_next_main_delta_lots", 0))
    main_turn_limit = int(mandate["main_turn_liquidity_lots"])
    next_turn_limit = int(mandate["next_main_turn_liquidity_lots"])
    if abs(remediation_main) + abs(pair_request) > main_turn_limit:
        raise ValueError("pair execution main request exceeds frozen turn limit")
    if abs(remediation_next) + abs(pair_request) > next_turn_limit:
        raise ValueError("pair execution next-main request exceeds frozen turn limit")

    execution_profile, execution_policy = resolve_oil_execution_runtime_policy(
        execution_desk_profile
    )
    completion = dict(execution_policy["completion_reliability"])
    completion_ratio = clamp(float(completion["normal_order_completion_ratio"]), 0.0, 1.0)
    normal_capacity_multiplier = max(
        0.0, float(completion["normal_trade_capacity_multiplier"])
    )
    pair_turn_limit = min(
        int(mandate["pair_turn_liquidity_units"]),
        max(0, main_turn_limit - abs(remediation_main)),
        max(0, next_turn_limit - abs(remediation_next)),
    )
    completion_limited_units = math.floor(abs(pair_request) * completion_ratio)
    desk_capacity_units = math.floor(
        pair_turn_limit * min(1.0, normal_capacity_multiplier)
    )
    executed_pair_abs = min(
        abs(pair_request), completion_limited_units, desk_capacity_units
    )
    executed_pair_units = (
        executed_pair_abs if pair_request > 0 else -executed_pair_abs if pair_request < 0 else 0
    )

    windows = _aligned_new_execution_windows(
        end_contracts[main_id],
        end_contracts[next_id],
        start_as_of=start_as_of,
        end_as_of=end_as_of,
        expected_count=int(config["window_policy"]["expected_newly_realized_weeks"]),
    )
    main_base_weights = [max(0.0, float(item["main"]["volume_lots"])) for item in windows]
    next_base_weights = [max(0.0, float(item["next_main"]["volume_lots"])) for item in windows]
    pair_base_weights = [
        min(main, next_main)
        for main, next_main in zip(main_base_weights, next_base_weights, strict=True)
    ]
    main_weights = adjust_visible_execution_weights(main_base_weights, execution_policy)
    next_weights = adjust_visible_execution_weights(next_base_weights, execution_policy)
    pair_weights = adjust_visible_execution_weights(pair_base_weights, execution_policy)
    remediation_main_alloc = _signed_allocations(remediation_main, main_weights)
    remediation_next_alloc = _signed_allocations(remediation_next, next_weights)
    pair_alloc = _signed_allocations(executed_pair_units, pair_weights)

    strategy_turnover_intensity = float(mandate.get("turnover_intensity", 50.0))
    turnover_profile = {
        "normalized_intensity": clamp(strategy_turnover_intensity / 100.0, 0.0, 1.0)
    }
    friction_config = assets["oil_trading_strategy_config"]["execution_friction"]
    fee_profile = _resolve_fee_profile(
        fee_state,
        friction_config,
        execution_policy=execution_policy,
    )
    neutral_fee_profile = _resolve_fee_profile(fee_state, friction_config)
    specification = dict(end_market["contractSpecification"])
    contract_size = float(specification["contract_size_bbl"])
    tick_size = float(specification["minimum_price_fluctuation_usd_per_bbl"])

    main_records: list[dict[str, Any]] = []
    next_records: list[dict[str, Any]] = []
    starting_main = int(resolved_book["positions"].get(main_id, 0))
    starting_next = int(resolved_book["positions"].get(next_id, 0))
    running_main = starting_main
    running_next = starting_next
    starting_position_state = _extract_spread_position(running_main, running_next)
    temporary_peak = int(starting_position_state["absolute_leg_imbalance_lots"])
    window_reports: list[dict[str, Any]] = []

    for index, window in enumerate(windows):
        main_remediation = _execute_leg_bucket(
            delta_lots=remediation_main_alloc[index],
            role="main",
            week=window["main"],
            contract_size_bbl=contract_size,
            tick_size_usd=tick_size,
            turnover_profile=turnover_profile,
            fee_profile=fee_profile,
            neutral_fee_profile=neutral_fee_profile,
            friction_config=friction_config,
            execution_policy=execution_policy,
            bucket="mandatory_residual_remediation",
        )
        running_main += int(main_remediation["delta_lots"])
        after_main_remediation = _extract_spread_position(running_main, running_next)
        temporary_peak = max(
            temporary_peak,
            int(after_main_remediation["absolute_leg_imbalance_lots"]),
        )
        next_remediation = _execute_leg_bucket(
            delta_lots=remediation_next_alloc[index],
            role="next_main",
            week=window["next_main"],
            contract_size_bbl=contract_size,
            tick_size_usd=tick_size,
            turnover_profile=turnover_profile,
            fee_profile=fee_profile,
            neutral_fee_profile=neutral_fee_profile,
            friction_config=friction_config,
            execution_policy=execution_policy,
            bucket="mandatory_residual_remediation",
        )
        running_next += int(next_remediation["delta_lots"])
        after_remediation = _extract_spread_position(running_main, running_next)
        temporary_peak = max(
            temporary_peak,
            int(after_remediation["absolute_leg_imbalance_lots"]),
        )

        pair_units = int(pair_alloc[index])
        main_pair = _execute_leg_bucket(
            delta_lots=pair_units,
            role="main",
            week=window["main"],
            contract_size_bbl=contract_size,
            tick_size_usd=tick_size,
            turnover_profile=turnover_profile,
            fee_profile=fee_profile,
            neutral_fee_profile=neutral_fee_profile,
            friction_config=friction_config,
            execution_policy=execution_policy,
            bucket="new_pair",
        )
        next_pair = _execute_leg_bucket(
            delta_lots=-pair_units,
            role="next_main",
            week=window["next_main"],
            contract_size_bbl=contract_size,
            tick_size_usd=tick_size,
            turnover_profile=turnover_profile,
            fee_profile=fee_profile,
            neutral_fee_profile=neutral_fee_profile,
            friction_config=friction_config,
            execution_policy=execution_policy,
            bucket="new_pair",
        )
        # Pair state is applied atomically at the weekly window boundary.  Costs
        # remain real-leg costs, but v0.1 does not invent asynchronous microtime.
        running_main += int(main_pair["delta_lots"])
        running_next += int(next_pair["delta_lots"])
        after_pair = _extract_spread_position(running_main, running_next)
        temporary_peak = max(
            temporary_peak,
            int(after_pair["absolute_leg_imbalance_lots"]),
        )
        main_records.extend((main_remediation, main_pair))
        next_records.extend((next_remediation, next_pair))
        window_reports.append(
            {
                "window_index": index,
                "week": str(window["label"]),
                "week_serial": int(window["week_serial"]),
                "pair_execution_weight": float(pair_weights[index]),
                "main_execution_weight": float(main_weights[index]),
                "next_main_execution_weight": float(next_weights[index]),
                "requested_pair_units_executed_in_window": pair_units,
                "main_remediation_delta_lots": int(main_remediation["delta_lots"]),
                "next_main_remediation_delta_lots": int(next_remediation["delta_lots"]),
                "main_pair_delta_lots": int(main_pair["delta_lots"]),
                "next_main_pair_delta_lots": int(next_pair["delta_lots"]),
                "position_after_window": {
                    "main_lots": running_main,
                    "next_main_lots": running_next,
                    **after_pair,
                },
            }
        )

    main_summary = _aggregate_leg(main_records)
    next_summary = _aggregate_leg(next_records)
    executed_main_delta = int(main_summary["executed_delta_lots"])
    executed_next_delta = int(next_summary["executed_delta_lots"])
    expected_main_delta = remediation_main + executed_pair_units
    expected_next_delta = remediation_next - executed_pair_units
    if executed_main_delta != expected_main_delta or executed_next_delta != expected_next_delta:
        raise ValueError("pair execution leg allocation identity failed")
    if abs(executed_main_delta) > main_turn_limit or abs(executed_next_delta) > next_turn_limit:
        raise ValueError("pair execution exceeded a frozen hard turn limit")

    ending_state = _extract_spread_position(running_main, running_next)
    requested_pair_abs = abs(pair_request)
    pair_completion_ratio = (
        1.0 if requested_pair_abs == 0 else abs(executed_pair_units) / requested_pair_abs
    )
    execution_cost = float(main_summary["execution_cost_usd"]) + float(
        next_summary["execution_cost_usd"]
    )
    neutral_execution_cost = float(
        main_summary["neutral_execution_cost_usd"]
    ) + float(next_summary["neutral_execution_cost_usd"])
    spread_cost = float(main_summary["spread_cost_usd"]) + float(
        next_summary["spread_cost_usd"]
    )
    slippage_cost = float(main_summary["slippage_cost_usd"]) + float(
        next_summary["slippage_cost_usd"]
    )
    net_fee = float(main_summary["net_fee_usd"]) + float(next_summary["net_fee_usd"])
    if not math.isclose(execution_cost, spread_cost + slippage_cost + net_fee, abs_tol=1e-6):
        raise ValueError("pair execution cost identity failed")

    pair_all_in_spread = None
    pair_neutral_spread = None
    if executed_pair_units and main_summary["average_all_in_execution_price_usd"] is not None and next_summary["average_all_in_execution_price_usd"] is not None:
        pair_all_in_spread = float(main_summary["average_all_in_execution_price_usd"]) - float(
            next_summary["average_all_in_execution_price_usd"]
        )
        pair_neutral_spread = float(main_summary["average_neutral_execution_price_usd"]) - float(
            next_summary["average_neutral_execution_price_usd"]
        )

    ending_imbalance = int(ending_state["absolute_leg_imbalance_lots"])
    if ending_imbalance:
        status = "residual_remains_after_mandated_remediation"
    elif pair_completion_ratio >= 1.0:
        status = "balanced_complete"
    elif executed_pair_units:
        status = "balanced_partial"
    else:
        status = "no_new_pair_fill"

    result = {
        "schemaVersion": "asset-simulation-oil-calendar-spread-pair-execution-report-v1",
        "strategy_id": OIL_CALENDAR_SPREAD_STRATEGY_V22_ID,
        "status": status,
        "startAsOf": start_as_of,
        "endAsOf": end_as_of,
        "executionDesk": {
            "profile": {
                "appointment": dict(execution_profile["appointment"]),
                "capability_radar": dict(execution_profile["capability_radar"]),
                "execution_style": dict(execution_profile["execution_style"]),
                "capability_total_score": float(execution_profile["capability_total_score"]),
                "profile_hash": str(execution_profile["profile_hash"]),
            },
            "resolved_policy": execution_policy,
            "normal_order_completion_ratio": completion_ratio,
            "normal_trade_capacity_multiplier": normal_capacity_multiplier,
        },
        "mandate": {
            "requested_pair_delta_units": pair_request,
            "requested_main_delta_lots": int(mandate["requested_main_delta_lots"]),
            "requested_next_main_delta_lots": int(mandate["requested_next_main_delta_lots"]),
            "requested_remediation_main_delta_lots": remediation_main,
            "requested_remediation_next_main_delta_lots": remediation_next,
            "main_turn_liquidity_lots": main_turn_limit,
            "next_main_turn_liquidity_lots": next_turn_limit,
            "pair_turn_liquidity_units": pair_turn_limit,
            "temporary_leg_imbalance_tolerance_lots": int(
                mandate.get("temporary_leg_imbalance_tolerance_lots", 0)
            ),
        },
        "completion": {
            "requested_pair_units": requested_pair_abs,
            "completion_limited_pair_units": completion_limited_units,
            "desk_capacity_pair_units": desk_capacity_units,
            "executed_pair_units": executed_pair_units,
            "executed_pair_lots": abs(executed_pair_units),
            "pair_completion_ratio": pair_completion_ratio,
            "execution_can_expand_authorized_order": False,
            "mandatory_remediation_bypassed_style_completion": True,
        },
        "legs": {
            "main": {"contract_id": main_id, **main_summary},
            "next_main": {"contract_id": next_id, **next_summary},
        },
        "weeklyWindows": window_reports,
        "pairExecution": {
            "spread_definition": "P_main - P_next_main",
            "neutral_pair_execution_spread_usd_per_bbl": pair_neutral_spread,
            "all_in_pair_execution_spread_usd_per_bbl": pair_all_in_spread,
            "new_pair_fills_balanced_within_each_window": True,
            "synthetic_spread_fill_created": False,
            "temporary_leg_imbalance_peak_lots": temporary_peak,
            "starting_absolute_leg_imbalance_lots": int(
                starting_position_state["absolute_leg_imbalance_lots"]
            ),
            "ending_absolute_leg_imbalance_lots": ending_imbalance,
        },
        "costs": {
            "spread_cost_usd": spread_cost,
            "slippage_cost_usd": slippage_cost,
            "net_fee_usd": net_fee,
            "execution_cost_usd": execution_cost,
            "neutral_execution_cost_usd": neutral_execution_cost,
            "execution_value_added_usd": neutral_execution_cost - execution_cost,
            "tca_benchmark": "neutral_score_50_same_realized_fills",
        },
        "strategyBookSettlementPreview": {
            "book_id": str(resolved_book["book_id"]),
            "book_identity_hash_before": str(resolved_book["identity"]["identity_hash"]),
            "starting_positions": dict(resolved_book["positions"]),
            "executed_deltas": {
                main_id: executed_main_delta,
                next_id: executed_next_delta,
            },
            "ending_positions_preview": {
                key: value
                for key, value in sorted(
                    {
                        **dict(resolved_book["positions"]),
                        main_id: running_main,
                        next_id: running_next,
                    }.items()
                )
                if int(value) != 0
            },
            "strategy_book_mutated": False,
            "formal_account_mutated": False,
            "cash_or_margin_settled": False,
        },
        "informationPolicy": {
            "decision_was_frozen_at_start_cutoff": True,
            "newly_realized_weeks_only": True,
            "future_beyond_end_cutoff_used": False,
            "execution_profile_changes_target_intent": False,
            "execution_profile_changes_hard_market_limits": False,
            "market_write_back": False,
            "strategy_book_write_back": False,
            "formal_account_write_back": False,
        },
    }
    rounded_result = _round_nested(result)
    identity = {
        "model_version": OIL_CALENDAR_SPREAD_PAIR_EXECUTION_MODEL_VERSION,
        "strategy_id": OIL_CALENDAR_SPREAD_STRATEGY_V22_ID,
        "config_id": str(config["config_id"]),
        "config_hash": assets["oil_calendar_spread_pair_execution_config_hash"],
        "field_contract_id": str(contract["contract_id"]),
        "field_contract_hash": assets["oil_calendar_spread_pair_execution_contract_hash"],
        "strategy_decision_identity_hash": str(decision["identity"]["identity_hash"]),
        "strategy_book_identity_hash": str(resolved_book["identity"]["identity_hash"]),
        "execution_profile_hash": str(execution_profile["profile_hash"]),
        "start_market_result_hash": str(start_market["identity"]["result_hash"]),
        "end_market_result_hash": str(end_market["identity"]["result_hash"]),
        "write_back": False,
        "result_hash": sha256_json(rounded_result),
    }
    identity["identity_hash"] = sha256_json(identity)
    return _round_nested({"identity": identity, **rounded_result})
