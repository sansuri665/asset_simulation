"""Persistent, no-lookahead thesis invalidation for the oil direction strategy."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .math_utils import clamp
from .registry import sha256_json


THESIS_STATUSES = ("active", "watch", "invalidated")


def _sign(value: float, threshold: float) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def resolve_oil_strategy_thesis_state(
    state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw = dict(state or {})
    contracts: dict[str, dict[str, Any]] = {}
    for contract_id, item_value in dict(raw.get("contracts", {})).items():
        item = dict(item_value)
        status = str(item.get("status", "active"))
        if status not in THESIS_STATUSES:
            raise ValueError("oil strategy thesis status is invalid")
        band_failures = int(item.get("consecutive_band_breaches", 0))
        direction_failures = int(item.get("consecutive_direction_misses", 0))
        if min(band_failures, direction_failures) < 0:
            raise ValueError("oil strategy thesis failure counters cannot be negative")
        last_signal = float(item.get("last_signal", 0.0))
        if not math.isfinite(last_signal) or not -1.0 <= last_signal <= 1.0:
            raise ValueError("oil strategy thesis last signal is invalid")
        contracts[str(contract_id)] = {
            "status": status,
            "consecutive_band_breaches": band_failures,
            "consecutive_direction_misses": direction_failures,
            "recovery_turns": max(0, int(item.get("recovery_turns", 0))),
            "last_signal": last_signal,
            "last_evaluation": dict(item.get("last_evaluation", {})),
        }
    return {
        "schemaVersion": "asset-simulation-oil-strategy-thesis-state-v1",
        "contracts": contracts,
    }


def apply_oil_strategy_thesis_invalidation(
    *,
    contract_id: str,
    current_position_lots: int,
    proposed_target_lots: int,
    signal: float,
    state: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    resolved = resolve_oil_strategy_thesis_state(state)
    previous = dict(resolved["contracts"].get(str(contract_id), {}))
    status = str(previous.get("status", "active"))
    scale = float(policy["status_target_scale"][status])
    proposed = int(proposed_target_lots)
    current = int(current_position_lots)
    adjusted = int(round(proposed * scale))
    previous_signal = float(previous.get("last_signal", 0.0))
    reversal_threshold = float(policy["direction_reversal_signal_threshold"])
    reversal = (
        abs(previous_signal) >= reversal_threshold
        and abs(float(signal)) >= reversal_threshold
        and previous_signal * float(signal) < 0.0
    )
    action = "unchanged" if adjusted == proposed else "scaled_after_prior_miss"
    if (
        bool(policy["direction_reversal_requires_exit_first"])
        and reversal
        and current != 0
        and current * previous_signal > 0.0
        and current * float(signal) < 0.0
    ):
        adjusted = 0
        action = "exit_before_direction_reversal"
    if (
        status == "invalidated"
        and not bool(policy["invalidated_can_increase_same_direction"])
        and current != 0
        and adjusted * current > 0
        and abs(adjusted) > abs(current)
    ):
        adjusted = current
        action = "invalidated_no_same_direction_increase"
    return adjusted, {
        "status": status,
        "target_scale": scale,
        "previous_signal": previous_signal,
        "current_signal": float(signal),
        "material_direction_reversal": reversal,
        "action": action,
        "pre_thesis_target_position_lots": proposed,
        "thesis_adjusted_target_position_lots": adjusted,
    }


def evaluate_oil_strategy_thesis_state(
    decision: Mapping[str, Any],
    end_market: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate the frozen prior forecast only after the next market is realized."""

    thesis = dict(decision.get("thesisInvalidation", {}))
    policy = dict(thesis.get("policy", {}))
    if not policy:
        raise ValueError("oil strategy decision lacks thesis invalidation policy")
    before = resolve_oil_strategy_thesis_state(thesis.get("stateBefore"))
    end_contracts = {
        str(item["contract_id"]): item
        for item in end_market.get("curve", {}).get("contracts", ())
    }
    failure_limit = int(policy["consecutive_failure_turns_to_invalidate"])
    severe_threshold = float(policy["severe_band_breach_z"])
    direction_threshold = float(policy["minimum_direction_move_log"])
    contracts: dict[str, dict[str, Any]] = {}
    evaluations: list[dict[str, Any]] = []
    for target_value in decision.get("targets", ()):
        target = dict(target_value)
        contract_id = str(target["contract_id"])
        if str(target.get("role")) == "legacy_exit" or contract_id not in end_contracts:
            continue
        components = sorted(
            (dict(item) for item in target.get("horizon_components", ())),
            key=lambda item: int(item["selected_horizon_weeks"]),
        )
        if not components:
            continue
        component = components[0]
        anchor = float(target["anchor_price_usd"])
        actual = float(end_contracts[contract_id]["price_usd"])
        low = float(component["confidence_low_usd"])
        high = float(component["confidence_high_usd"])
        center = float(component["forecast_close_usd"])
        uncertainty = max(1e-9, float(component["uncertainty_log"]))
        outside_log = 0.0
        if actual < low:
            outside_log = math.log(low / actual)
        elif actual > high:
            outside_log = math.log(actual / high)
        breach_z = outside_log / uncertainty
        band_breach = outside_log > 0.0
        severe_breach = breach_z >= severe_threshold
        predicted_direction = _sign(math.log(center / anchor), direction_threshold)
        actual_direction = _sign(math.log(actual / anchor), direction_threshold)
        direction_miss = (
            predicted_direction != 0
            and actual_direction != 0
            and predicted_direction != actual_direction
        )
        previous = dict(before["contracts"].get(contract_id, {}))
        band_count = int(previous.get("consecutive_band_breaches", 0))
        direction_count = int(previous.get("consecutive_direction_misses", 0))
        if band_breach:
            band_count += 1
        else:
            band_count = max(0, band_count - 1)
        if direction_miss:
            direction_count += 1
        else:
            direction_count = max(0, direction_count - 1)
        failed = band_breach or direction_miss
        previous_status = str(previous.get("status", "active"))
        if severe_breach or max(band_count, direction_count) >= failure_limit:
            status = "invalidated"
        elif failed or previous_status == "invalidated" or max(band_count, direction_count) > 0:
            status = "watch"
        else:
            status = "active"
        recovery_turns = 0 if failed else int(previous.get("recovery_turns", 0)) + 1
        evaluation = {
            "contract_id": contract_id,
            "target_week": component["target_week"],
            "anchor_price_usd": anchor,
            "forecast_close_usd": center,
            "confidence_low_usd": low,
            "confidence_high_usd": high,
            "realized_price_usd": actual,
            "band_breach": band_breach,
            "band_breach_z": breach_z,
            "severe_band_breach": severe_breach,
            "predicted_direction": predicted_direction,
            "realized_direction": actual_direction,
            "direction_miss": direction_miss,
            "status_before": previous_status,
            "status_after": status,
        }
        contracts[contract_id] = {
            "status": status,
            "consecutive_band_breaches": band_count,
            "consecutive_direction_misses": direction_count,
            "recovery_turns": recovery_turns,
            "last_signal": float(target.get("signal", 0.0)),
            "last_evaluation": evaluation,
        }
        evaluations.append(evaluation)
    state = {
        "schemaVersion": "asset-simulation-oil-strategy-thesis-state-v1",
        "contracts": contracts,
    }
    return {
        "state": state,
        "evaluations": evaluations,
        "state_hash": sha256_json(state),
        "informationPolicy": {
            "prior_published_forecast_only": True,
            "newly_realized_settlement_market_only": True,
            "configured_research_ability_used": False,
            "hidden_future_used": False,
        },
    }
