"""No-lookahead real-leg execution for the short-horizon oil calendar spread.

v0.1.0 proved the two-real-leg execution path but scheduled the two future
weekly windows with volume that only became known inside those windows.  v0.1.1
fixes that information boundary: schedule weights are frozen from named-contract
weekly liquidity visible at the decision cutoff.  Newly realized OHLC/volume is
used only to settle the window's realized execution price, impact and cost.

The module remains Gate A only.  It returns fills and a Strategy Book settlement
preview but never mutates Strategy Book, Formal Account, cash, margin or market.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .math_utils import clamp
from .oil_calendar_spread_pair_execution import (
    OIL_CALENDAR_SPREAD_PAIR_EXECUTION_MODEL_VERSION as V10_MODEL_VERSION,
    _aggregate_leg,
    _aligned_new_execution_windows,
    _contract_map,
    _execute_leg_bucket,
    _half_turn_serial,
    _signed_allocations,
    _visible_weeks,
)
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
    _resolve_fee_profile,
)
from .registry import load_registered_assets, sha256_json


OIL_CALENDAR_SPREAD_PAIR_EXECUTION_V11_MODEL_VERSION = (
    "asset-simulation-oil-calendar-spread-pair-execution-v0.1.1"
)
OIL_CALENDAR_SPREAD_PAIR_EXECUTION_V11_CONTRACT_ID = (
    "oil_calendar_spread_pair_execution_v11"
)


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("calendar-spread pair execution v0.1.1 contains a non-finite value")
        return round(value, 8)
    return value


def _validate_registered_assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_calendar_spread_pair_execution_v11_config"]
    contract = assets["oil_calendar_spread_pair_execution_v11_contract"]
    if config["model_version"] != OIL_CALENDAR_SPREAD_PAIR_EXECUTION_V11_MODEL_VERSION:
        raise ValueError("registered pair execution v0.1.1 config version mismatch")
    if contract["contract_id"] != OIL_CALENDAR_SPREAD_PAIR_EXECUTION_V11_CONTRACT_ID:
        raise ValueError("registered pair execution v0.1.1 contract id mismatch")
    if contract["model_version"] != OIL_CALENDAR_SPREAD_PAIR_EXECUTION_V11_MODEL_VERSION:
        raise ValueError("registered pair execution v0.1.1 contract version mismatch")
    if config["strategy_id"] != OIL_CALENDAR_SPREAD_STRATEGY_V22_ID:
        raise ValueError("pair execution v0.1.1 strategy id mismatch")
    if config["strategy_decision_model_version"] != OIL_CALENDAR_SPREAD_STRATEGY_V22_MODEL_VERSION:
        raise ValueError("pair execution v0.1.1 strategy decision owner mismatch")
    if config["execution_desk_model_version"] != OIL_EXECUTION_DESK_MODEL_VERSION:
        raise ValueError("pair execution v0.1.1 execution-desk owner mismatch")
    if config["execution_primitive_reference"] != V10_MODEL_VERSION:
        raise ValueError("pair execution v0.1.1 primitive reference mismatch")
    if config["friction_primitive_model_version"] != OIL_TRADING_STRATEGY_MODEL_VERSION:
        raise ValueError("pair execution v0.1.1 friction owner mismatch")
    window = dict(config["window_policy"])
    if window["schedule_information_cutoff"] != "decision_start_cutoff":
        raise ValueError("pair execution scheduling must be frozen at the decision cutoff")
    if window["realized_volume_use"] != "impact_and_cost_only_not_scheduling":
        raise ValueError("realized future volume may not own pair scheduling")
    if bool(window["synthetic_spread_fill_allowed"]):
        raise ValueError("pair execution v0.1.1 may not create synthetic fills")
    if not bool(config["cost_policy"]["pair_execution_price_uses_new_pair_fills_only"]):
        raise ValueError("pair execution price must exclude remediation fills")
    return assets, config, contract


def _cutoff_week_serial(as_of: Mapping[str, Any]) -> int:
    year = int(as_of["year"])
    month = int(as_of["month"])
    half = int(as_of["half"])
    if half not in (1, 2):
        raise ValueError("calendar-spread execution cutoff half must be 1 or 2")
    return (year * 12 + month - 1) * 4 + (2 if half == 1 else 4) - 1


def _normalized_weights(values: Sequence[float]) -> list[float]:
    positive = [max(0.0, float(value)) for value in values]
    total = sum(positive)
    if total <= 0.0:
        if not positive:
            return []
        return [1.0 / len(positive)] * len(positive)
    return [value / total for value in positive]


def _frozen_schedule(
    main_contract: Mapping[str, Any],
    next_contract: Mapping[str, Any],
    *,
    start_as_of: Mapping[str, Any],
    execution_policy: Mapping[str, Any],
    lookback_weeks: int,
    execution_windows: int,
) -> dict[str, Any]:
    """Freeze future-window weights from liquidity visible before execution begins."""

    cutoff = _cutoff_week_serial(start_as_of)
    main_weeks = _visible_weeks(main_contract)
    next_weeks = _visible_weeks(next_contract)
    common = sorted(
        serial
        for serial in set(main_weeks).intersection(next_weeks)
        if serial <= cutoff
    )
    lookback = max(1, int(lookback_weeks))
    selected = common[-lookback:]
    if len(selected) < min(2, lookback):
        raise ValueError("pair execution lacks enough pre-decision shared liquidity history")

    main_visible = [max(0.0, float(main_weeks[serial]["volume_lots"])) for serial in selected]
    next_visible = [max(0.0, float(next_weeks[serial]["volume_lots"])) for serial in selected]
    pair_visible = [
        min(main, next_main)
        for main, next_main in zip(main_visible, next_visible, strict=True)
    ]

    # The current candidate has two future windows and a two-week visible lookback.
    # If a later config asks for more windows than visible observations, repeat the
    # latest visible liquidity rather than reading future volume.
    def expand(values: Sequence[float]) -> list[float]:
        raw = list(map(float, values))
        if not raw:
            raw = [1.0]
        while len(raw) < int(execution_windows):
            raw.append(raw[-1])
        return raw[-int(execution_windows):]

    main_inputs = expand(main_visible)
    next_inputs = expand(next_visible)
    pair_inputs = expand(pair_visible)
    main_weights = adjust_visible_execution_weights(main_inputs, execution_policy)
    next_weights = adjust_visible_execution_weights(next_inputs, execution_policy)
    pair_weights = adjust_visible_execution_weights(pair_inputs, execution_policy)
    if not (
        len(main_weights) == len(next_weights) == len(pair_weights) == int(execution_windows)
    ):
        raise ValueError("execution-desk scheduling returned the wrong window count")

    schedule_inputs = {
        "information_cutoff": str(start_as_of["label"]),
        "visible_week_serials": selected,
        "main_visible_volume_lots": main_visible,
        "next_main_visible_volume_lots": next_visible,
        "pair_visible_volume_lots": pair_visible,
        "expanded_main_schedule_inputs": main_inputs,
        "expanded_next_main_schedule_inputs": next_inputs,
        "expanded_pair_schedule_inputs": pair_inputs,
        "main_execution_weights": list(map(float, main_weights)),
        "next_main_execution_weights": list(map(float, next_weights)),
        "pair_execution_weights": list(map(float, pair_weights)),
        "future_window_volume_used": False,
    }
    schedule_inputs["schedule_input_hash"] = sha256_json(schedule_inputs)
    return _round_nested(schedule_inputs)


def _bucket_summary(records: Sequence[Mapping[str, Any]], bucket: str) -> dict[str, Any]:
    return _aggregate_leg(
        [item for item in records if str(item.get("bucket")) == str(bucket)]
    )


def _cost_sum(main: Mapping[str, Any], next_main: Mapping[str, Any]) -> dict[str, float]:
    result = {
        key: float(main[key]) + float(next_main[key])
        for key in (
            "spread_cost_usd",
            "slippage_cost_usd",
            "gross_fee_usd",
            "fee_rebate_usd",
            "net_fee_usd",
            "execution_cost_usd",
            "neutral_execution_cost_usd",
            "execution_value_added_usd",
        )
    }
    if not math.isclose(
        result["execution_cost_usd"],
        result["spread_cost_usd"] + result["slippage_cost_usd"] + result["net_fee_usd"],
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise ValueError("pair execution v0.1.1 cost identity failed")
    return result


def execute_oil_calendar_spread_pair_turn_v11(
    start_market: Mapping[str, Any],
    end_market: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    strategy_book: Mapping[str, Any],
    execution_desk_profile: Mapping[str, Any] | None = None,
    fee_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one frozen spread mandate with pre-decision scheduling information only."""

    assets, config, contract = _validate_registered_assets()
    decision_identity = dict(decision.get("identity", {}))
    if str(decision_identity.get("model_version", "")) != OIL_CALENDAR_SPREAD_STRATEGY_V22_MODEL_VERSION:
        raise ValueError("pair execution v0.1.1 requires a v0.2.2 strategy decision")
    if str(decision_identity.get("strategy_id", "")) != OIL_CALENDAR_SPREAD_STRATEGY_V22_ID:
        raise ValueError("pair execution v0.1.1 received the wrong strategy id")
    if not bool(start_market.get("ok")) or not bool(end_market.get("ok")):
        raise ValueError("pair execution v0.1.1 requires successful market payloads")

    start_as_of = dict(start_market["asOf"])
    end_as_of = dict(end_market["asOf"])
    start_serial = _half_turn_serial(
        start_as_of["year"], start_as_of["month"], start_as_of["half"]
    )
    end_serial = _half_turn_serial(
        end_as_of["year"], end_as_of["month"], end_as_of["half"]
    )
    if end_serial != start_serial + 1:
        raise ValueError("pair execution v0.1.1 requires adjacent half-month cutoffs")
    if str(decision["asOf"]["label"]) != str(start_as_of["label"]):
        raise ValueError("pair execution v0.1.1 decision cutoff does not match start market")
    world = str(start_market["identity"]["upstream_global_identity_hash"])
    if str(end_market["identity"]["upstream_global_identity_hash"]) != world:
        raise ValueError("pair execution v0.1.1 markets belong to different worlds")
    if str(decision_identity["upstream_global_identity_hash"]) != world:
        raise ValueError("pair execution v0.1.1 decision belongs to another world")

    decision_book = dict(decision["strategyBook"])
    resolved_book = resolve_oil_strategy_book(
        strategy_book,
        expected_institution_id=str(decision_book["institution_id"]),
        expected_strategy_id=OIL_CALENDAR_SPREAD_STRATEGY_V22_ID,
    )
    if str(resolved_book["identity"]["identity_hash"]) != str(
        decision_book["book_identity_hash"]
    ):
        raise ValueError("pair execution v0.1.1 strategy book changed after decision freeze")

    legs = {role: dict(decision["legs"][role]) for role in ("main", "next_main")}
    main_id = str(legs["main"]["contract_id"])
    next_id = str(legs["next_main"]["contract_id"])
    start_contracts = _contract_map(start_market)
    end_contracts = _contract_map(end_market)
    for contract_id in (main_id, next_id):
        if contract_id not in start_contracts or contract_id not in end_contracts:
            raise ValueError("pair execution v0.1.1 lacks one frozen real leg")

    mandate = dict(decision["pairedExecutionMandate"])
    pair_request = int(mandate["requested_pair_delta_units"])
    if (
        int(mandate["requested_main_delta_lots"]) != pair_request
        or int(mandate["requested_next_main_delta_lots"]) != -pair_request
    ):
        raise ValueError("pair execution v0.1.1 mandate is not one-to-one")
    remediation = dict(mandate.get("imbalanceRemediation", {}))
    remediation_main = int(remediation.get("requested_main_delta_lots", 0))
    remediation_next = int(remediation.get("requested_next_main_delta_lots", 0))
    main_turn_limit = max(0, int(mandate["main_turn_liquidity_lots"]))
    next_turn_limit = max(0, int(mandate["next_main_turn_liquidity_lots"]))
    if abs(remediation_main) + abs(pair_request) > main_turn_limit:
        raise ValueError("pair execution v0.1.1 main request exceeds frozen turn limit")
    if abs(remediation_next) + abs(pair_request) > next_turn_limit:
        raise ValueError("pair execution v0.1.1 next-main request exceeds frozen turn limit")

    execution_profile, execution_policy = resolve_oil_execution_runtime_policy(
        execution_desk_profile
    )
    completion_policy = dict(execution_policy["completion_reliability"])
    completion_ratio = clamp(
        float(completion_policy["normal_order_completion_ratio"]), 0.0, 1.0
    )
    normal_capacity_multiplier = max(
        0.0, float(completion_policy["normal_trade_capacity_multiplier"])
    )
    pair_turn_limit = min(
        max(0, int(mandate["pair_turn_liquidity_units"])),
        max(0, main_turn_limit - abs(remediation_main)),
        max(0, next_turn_limit - abs(remediation_next)),
    )
    completion_limited_units = math.floor(abs(pair_request) * completion_ratio)
    desk_capacity_units = math.floor(pair_turn_limit * min(1.0, normal_capacity_multiplier))
    executed_pair_abs = min(
        abs(pair_request), completion_limited_units, desk_capacity_units
    )
    executed_pair_units = (
        executed_pair_abs
        if pair_request > 0
        else -executed_pair_abs
        if pair_request < 0
        else 0
    )

    execution_windows = int(config["window_policy"]["execution_windows_per_half_turn"])
    schedule = _frozen_schedule(
        start_contracts[main_id],
        start_contracts[next_id],
        start_as_of=start_as_of,
        execution_policy=execution_policy,
        lookback_weeks=int(config["window_policy"]["schedule_visible_lookback_weeks"]),
        execution_windows=execution_windows,
    )
    windows = _aligned_new_execution_windows(
        end_contracts[main_id],
        end_contracts[next_id],
        start_as_of=start_as_of,
        end_as_of=end_as_of,
        expected_count=execution_windows,
    )
    pair_alloc = _signed_allocations(
        executed_pair_units, schedule["pair_execution_weights"]
    )
    remediation_main_alloc = _signed_allocations(
        remediation_main, schedule["main_execution_weights"]
    )
    remediation_next_alloc = _signed_allocations(
        remediation_next, schedule["next_main_execution_weights"]
    )

    turnover_intensity = clamp(
        float(mandate.get("turnover_intensity", 50.0)), 0.0, 100.0
    )
    turnover_profile = {"normalized_intensity": turnover_intensity / 100.0}
    friction_config = assets["oil_trading_strategy_config"]["execution_friction"]
    fee_profile = _resolve_fee_profile(
        fee_state, friction_config, execution_policy=execution_policy
    )
    neutral_fee_profile = _resolve_fee_profile(fee_state, friction_config)
    specification = dict(end_market["contractSpecification"])
    contract_size = float(specification["contract_size_bbl"])
    tick_size = float(specification["minimum_price_fluctuation_usd_per_bbl"])

    starting_main = int(resolved_book["positions"].get(main_id, 0))
    starting_next = int(resolved_book["positions"].get(next_id, 0))
    running_main = starting_main
    running_next = starting_next
    starting_state = _extract_spread_position(running_main, running_next)
    temporary_peak = int(starting_state["absolute_leg_imbalance_lots"])
    main_records: list[dict[str, Any]] = []
    next_records: list[dict[str, Any]] = []
    window_reports: list[dict[str, Any]] = []

    for index, window in enumerate(windows):
        main_remediation = _execute_leg_bucket(
            delta_lots=int(remediation_main_alloc[index]),
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
        temporary_peak = max(
            temporary_peak,
            int(
                _extract_spread_position(running_main, running_next)[
                    "absolute_leg_imbalance_lots"
                ]
            ),
        )
        next_remediation = _execute_leg_bucket(
            delta_lots=int(remediation_next_alloc[index]),
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
            temporary_peak, int(after_remediation["absolute_leg_imbalance_lots"])
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
        # State application is atomic at the weekly boundary.  Each leg keeps its
        # own fill and cost record; v0.1.1 deliberately does not invent sub-week
        # asynchronous micro-timing before a dedicated legging scheduler exists.
        running_main += int(main_pair["delta_lots"])
        running_next += int(next_pair["delta_lots"])
        after_pair = _extract_spread_position(running_main, running_next)
        temporary_peak = max(
            temporary_peak, int(after_pair["absolute_leg_imbalance_lots"])
        )

        main_records.extend((main_remediation, main_pair))
        next_records.extend((next_remediation, next_pair))
        window_reports.append(
            {
                "window_index": index,
                "week": str(window["label"]),
                "week_serial": int(window["week_serial"]),
                "frozen_pair_execution_weight": float(
                    schedule["pair_execution_weights"][index]
                ),
                "frozen_main_execution_weight": float(
                    schedule["main_execution_weights"][index]
                ),
                "frozen_next_main_execution_weight": float(
                    schedule["next_main_execution_weights"][index]
                ),
                "realized_main_market_volume_lots": int(
                    window["main"]["volume_lots"]
                ),
                "realized_next_main_market_volume_lots": int(
                    window["next_main"]["volume_lots"]
                ),
                "realized_volume_used_for_schedule": False,
                "pair_units": pair_units,
                "main_remediation_delta_lots": int(main_remediation["delta_lots"]),
                "next_main_remediation_delta_lots": int(
                    next_remediation["delta_lots"]
                ),
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
    main_pair_summary = _bucket_summary(main_records, "new_pair")
    next_pair_summary = _bucket_summary(next_records, "new_pair")
    main_remediation_summary = _bucket_summary(
        main_records, "mandatory_residual_remediation"
    )
    next_remediation_summary = _bucket_summary(
        next_records, "mandatory_residual_remediation"
    )

    executed_main_delta = int(main_summary["executed_delta_lots"])
    executed_next_delta = int(next_summary["executed_delta_lots"])
    if executed_main_delta != remediation_main + executed_pair_units:
        raise ValueError("pair execution v0.1.1 main allocation identity failed")
    if executed_next_delta != remediation_next - executed_pair_units:
        raise ValueError("pair execution v0.1.1 next-main allocation identity failed")
    if int(main_summary["gross_turnover_lots"]) > main_turn_limit:
        raise ValueError("pair execution v0.1.1 main gross turnover exceeded hard limit")
    if int(next_summary["gross_turnover_lots"]) > next_turn_limit:
        raise ValueError("pair execution v0.1.1 next-main gross turnover exceeded hard limit")

    ending_state = _extract_spread_position(running_main, running_next)
    requested_pair_abs = abs(pair_request)
    pair_completion_ratio = (
        1.0
        if requested_pair_abs == 0
        else abs(executed_pair_units) / requested_pair_abs
    )
    total_costs = _cost_sum(main_summary, next_summary)
    pair_costs = _cost_sum(main_pair_summary, next_pair_summary)
    remediation_costs = _cost_sum(
        main_remediation_summary, next_remediation_summary
    )

    pair_neutral_spread = None
    pair_all_in_spread = None
    if executed_pair_units:
        main_neutral = main_pair_summary["average_neutral_execution_price_usd"]
        next_neutral = next_pair_summary["average_neutral_execution_price_usd"]
        main_all_in = main_pair_summary["average_all_in_execution_price_usd"]
        next_all_in = next_pair_summary["average_all_in_execution_price_usd"]
        if None in (main_neutral, next_neutral, main_all_in, next_all_in):
            raise ValueError("pair execution v0.1.1 new-pair price aggregation is incomplete")
        pair_neutral_spread = float(main_neutral) - float(next_neutral)
        pair_all_in_spread = float(main_all_in) - float(next_all_in)

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
        "schemaVersion": "asset-simulation-oil-calendar-spread-pair-execution-report-v11",
        "strategy_id": OIL_CALENDAR_SPREAD_STRATEGY_V22_ID,
        "status": status,
        "startAsOf": start_as_of,
        "endAsOf": end_as_of,
        "schedule": schedule,
        "executionDesk": {
            "profile": {
                "appointment": dict(execution_profile["appointment"]),
                "capability_radar": dict(execution_profile["capability_radar"]),
                "execution_style": dict(execution_profile["execution_style"]),
                "capability_total_score": float(
                    execution_profile["capability_total_score"]
                ),
                "profile_hash": str(execution_profile["profile_hash"]),
            },
            "resolved_policy": execution_policy,
            "normal_order_completion_ratio": completion_ratio,
            "normal_trade_capacity_multiplier": normal_capacity_multiplier,
        },
        "mandate": {
            "requested_pair_delta_units": pair_request,
            "requested_remediation_main_delta_lots": remediation_main,
            "requested_remediation_next_main_delta_lots": remediation_next,
            "main_turn_liquidity_lots": main_turn_limit,
            "next_main_turn_liquidity_lots": next_turn_limit,
            "pair_turn_liquidity_units": pair_turn_limit,
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
            "main": {
                "contract_id": main_id,
                **main_summary,
                "newPair": main_pair_summary,
                "remediation": main_remediation_summary,
            },
            "next_main": {
                "contract_id": next_id,
                **next_summary,
                "newPair": next_pair_summary,
                "remediation": next_remediation_summary,
            },
        },
        "weeklyWindows": window_reports,
        "pairExecution": {
            "spread_definition": "P_main - P_next_main",
            "neutral_pair_execution_spread_usd_per_bbl": pair_neutral_spread,
            "all_in_pair_execution_spread_usd_per_bbl": pair_all_in_spread,
            "execution_price_bucket": "new_pair_only",
            "new_pair_fills_balanced_within_each_window": True,
            "synthetic_spread_fill_created": False,
            "temporary_leg_imbalance_peak_lots": temporary_peak,
            "starting_absolute_leg_imbalance_lots": int(
                starting_state["absolute_leg_imbalance_lots"]
            ),
            "ending_absolute_leg_imbalance_lots": ending_imbalance,
            "costs": pair_costs,
        },
        "remediation": {
            "required": bool(remediation_main or remediation_next),
            "executed_main_delta_lots": int(
                main_remediation_summary["executed_delta_lots"]
            ),
            "executed_next_main_delta_lots": int(
                next_remediation_summary["executed_delta_lots"]
            ),
            "costs": remediation_costs,
        },
        "costs": {
            **total_costs,
            "tca_benchmark": "neutral_score_50_same_realized_fills",
        },
        "strategyBookSettlementPreview": {
            "book_id": str(resolved_book["book_id"]),
            "book_identity_hash_before": str(
                resolved_book["identity"]["identity_hash"]
            ),
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
            "schedule_frozen_from_predecision_visible_liquidity": True,
            "future_window_volume_used_for_schedule": False,
            "newly_realized_ohlc_used_for_realized_execution_only": True,
            "newly_realized_volume_used_for_impact_only": True,
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
        "model_version": OIL_CALENDAR_SPREAD_PAIR_EXECUTION_V11_MODEL_VERSION,
        "strategy_id": OIL_CALENDAR_SPREAD_STRATEGY_V22_ID,
        "config_id": str(config["config_id"]),
        "config_hash": assets[
            "oil_calendar_spread_pair_execution_v11_config_hash"
        ],
        "field_contract_id": str(contract["contract_id"]),
        "field_contract_hash": assets[
            "oil_calendar_spread_pair_execution_v11_contract_hash"
        ],
        "strategy_decision_identity_hash": str(decision_identity["identity_hash"]),
        "strategy_book_identity_hash": str(
            resolved_book["identity"]["identity_hash"]
        ),
        "execution_profile_hash": str(execution_profile["profile_hash"]),
        "start_market_result_hash": str(start_market["identity"]["result_hash"]),
        "end_market_result_hash": str(end_market["identity"]["result_hash"]),
        "schedule_input_hash": str(schedule["schedule_input_hash"]),
        "write_back": False,
        "result_hash": sha256_json(rounded_result),
    }
    identity["identity_hash"] = sha256_json(identity)
    return _round_nested({"identity": identity, **rounded_result})
