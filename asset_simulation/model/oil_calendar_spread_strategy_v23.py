"""Calendar-spread v0.2.3: zero-error construction is an identity mapping.

v0.2.2 restored real visible spread history.  Continuous realized-fill replay
then exposed a separate owner-boundary defect inherited from v0.2.1: even when
construction error was exactly zero, the construction layer intercepted a
policy-intended direction reversal and forced the submitted target to zero.
That duplicated the thesis owner's exit-before-reversal rule and broke the
registered neutral/score-100 compatibility guarantee once a real nonzero book
existed.

v0.2.3 keeps v0.2.2 signal, PM, risk, thesis and history semantics.  It changes
only construction ownership: exact zero construction error maps ideal target to
submitted target unchanged.  Thesis remains the sole owner of the later
exit-before-direction-reversal discipline.  The conservative construction guard
is retained only when construction error is actually nonzero.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .math_utils import clamp
from .oil_calendar_spread_market_history import (
    normalize_oil_calendar_spread_market_history,
)
from .oil_calendar_spread_research import (
    CALENDAR_SPREAD_STYLE_DIMENSIONS,
    resolve_oil_calendar_spread_runtime_policy,
)
from .oil_calendar_spread_strategy import (
    OIL_CALENDAR_SPREAD_STRATEGY_MODEL_VERSION as REFERENCE_MODEL_VERSION,
    _apply_spread_position_persistence,
    _apply_thesis_policy,
    _paired_execution_mandate,
    _position_risk_metrics,
    _responsive_target,
    build_oil_calendar_spread_research_decision,
    resolve_oil_calendar_spread_thesis_state,
)
from .oil_calendar_spread_strategy_v2 import (
    OIL_CALENDAR_SPREAD_STRATEGY_V2_ID,
    _dedicated_signal_mix,
    _pair_construction_errors,
    _visible_state_hash,
)
from .oil_strategy_book import resolve_oil_strategy_book
from .oil_strategy_research import (
    build_oil_strategy_construction_adjustments,
    resolve_oil_strategy_research_profile,
)
from .registry import load_registered_assets, sha256_json


OIL_CALENDAR_SPREAD_STRATEGY_V23_MODEL_VERSION = (
    "asset-simulation-oil-calendar-spread-strategy-v0.2.3"
)
OIL_CALENDAR_SPREAD_STRATEGY_V23_CONTRACT_ID = "oil_calendar_spread_strategy_v23"
OIL_CALENDAR_SPREAD_STRATEGY_V23_ID = OIL_CALENDAR_SPREAD_STRATEGY_V2_ID
BASE_MODEL_VERSION = "asset-simulation-oil-calendar-spread-strategy-v0.2.2"


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("oil calendar spread v0.2.3 contains a non-finite value")
        return round(value, 8)
    return value


def _validate_registered_assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    patch = assets["oil_calendar_spread_strategy_v23_config"]
    contract = assets["oil_calendar_spread_strategy_v23_contract"]
    base = assets["oil_calendar_spread_strategy_v22_config"]
    if patch["model_version"] != OIL_CALENDAR_SPREAD_STRATEGY_V23_MODEL_VERSION:
        raise ValueError("registered calendar spread v0.2.3 config version mismatch")
    if patch["base_model_version"] != BASE_MODEL_VERSION:
        raise ValueError("registered calendar spread v0.2.3 base version mismatch")
    if base["model_version"] != BASE_MODEL_VERSION:
        raise ValueError("calendar spread v0.2.3 registered base config mismatch")
    if patch["strategy_id"] != OIL_CALENDAR_SPREAD_STRATEGY_V23_ID:
        raise ValueError("registered calendar spread v0.2.3 strategy id mismatch")
    if contract["contract_id"] != OIL_CALENDAR_SPREAD_STRATEGY_V23_CONTRACT_ID:
        raise ValueError("registered calendar spread v0.2.3 contract id mismatch")
    if contract["model_version"] != OIL_CALENDAR_SPREAD_STRATEGY_V23_MODEL_VERSION:
        raise ValueError("registered calendar spread v0.2.3 contract version mismatch")
    fix = dict(patch["construction_semantics_fix"])
    required_true = (
        "zero_error_construction_is_identity",
        "zero_error_submitted_target_equals_ideal_policy_target",
        "thesis_owns_exit_before_direction_reversal",
        "construction_does_not_own_thesis_reversal",
        "nonzero_construction_error_safety_guard_retained",
        "construction_cannot_create_direction_absent_ideal_direction",
        "construction_cannot_flip_against_ideal_direction",
    )
    if not all(bool(fix.get(key)) for key in required_true):
        raise ValueError("calendar spread v0.2.3 construction ownership config is invalid")
    if dict(base["strategy_taxonomy"])["strategy_type"] != "relative_value_calendar_spread":
        raise ValueError("calendar spread v0.2.3 base taxonomy mismatch")
    return assets, patch, contract, base


def _apply_pair_construction_v23(
    *,
    current_spread_units: int,
    ideal_target_spread_units: int,
    capacity_units: int,
    target_scale_error: float,
    transition_gap_error: float,
) -> dict[str, Any]:
    """Apply bounded process error without stealing thesis reversal ownership."""

    capacity = max(0, int(capacity_units))
    current = int(clamp(float(current_spread_units), -float(capacity), float(capacity)))
    ideal = int(clamp(float(ideal_target_spread_units), -float(capacity), float(capacity)))
    target_error = float(target_scale_error)
    transition_error = float(transition_gap_error)
    zero_error = (
        math.isclose(target_error, 0.0, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(transition_error, 0.0, rel_tol=0.0, abs_tol=1e-12)
    )

    if zero_error:
        scaled_ideal = ideal
        ideal_gap = ideal - current
        submitted = ideal
        direction_guard_action = "zero_error_identity"
    else:
        scaled_ideal = int(
            round(
                clamp(
                    float(ideal) * (1.0 + target_error),
                    -float(capacity),
                    float(capacity),
                )
            )
        )
        ideal_gap = scaled_ideal - current
        submitted = current + int(round(ideal_gap * (1.0 + transition_error)))
        submitted = int(clamp(float(submitted), -float(capacity), float(capacity)))
        direction_guard_action = "unchanged"

        if ideal == 0:
            if current == 0:
                submitted = 0
            elif submitted * current < 0:
                submitted = 0
                direction_guard_action = "prevented_zero_target_overshoot"
        elif current == 0:
            if submitted * ideal < 0:
                submitted = 0
                direction_guard_action = "prevented_created_reverse_direction"
        elif current * ideal < 0:
            # A low-capability process error is not allowed to implement the
            # cross-zero reversal itself.  The intended reversal remains visible
            # in ideal; the downstream thesis owner decides the exit-first step.
            if submitted * ideal > 0:
                submitted = 0
                direction_guard_action = "nonzero_error_reversal_guard"
        elif submitted * ideal < 0:
            submitted = 0
            direction_guard_action = "prevented_same_side_overshoot"

    return {
        "ideal_policy_target_spread_units": ideal,
        "scaled_ideal_target_spread_units": scaled_ideal,
        "ideal_transition_gap_units": ideal_gap,
        "construction_submitted_target_spread_units": submitted,
        "pair_target_scale_error": target_error,
        "pair_transition_gap_error": transition_error,
        "zero_error_construction_identity": zero_error,
        "direction_guard_action": direction_guard_action,
        "construction_created_or_reversed_signal": False,
        "thesis_reversal_policy_applied_here": False,
    }


def build_oil_calendar_spread_strategy_v23_decision(
    market: Mapping[str, Any],
    forecast_vintage: Mapping[str, Any],
    *,
    authorized_strategy_capital_usd: float,
    strategy_book: Mapping[str, Any],
    strategy_research_profile: Mapping[str, Any] | None = None,
    thesis_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build current strategy candidate with corrected construction ownership."""

    assets, patch, contract, config = _validate_registered_assets()
    authorized_capital = float(authorized_strategy_capital_usd)
    if not math.isfinite(authorized_capital) or authorized_capital <= 0.0:
        raise ValueError("authorized strategy capital must be finite and positive")

    normalized_market, history_adapter = normalize_oil_calendar_spread_market_history(
        market
    )
    supplied_book = resolve_oil_strategy_book(
        strategy_book,
        expected_strategy_id=OIL_CALENDAR_SPREAD_STRATEGY_V23_ID,
    )
    source_profile = resolve_oil_strategy_research_profile(strategy_research_profile)
    dedicated_profile, strategy_policy, reference_profile = (
        resolve_oil_calendar_spread_runtime_policy(strategy_research_profile)
    )

    reference = build_oil_calendar_spread_research_decision(
        normalized_market,
        forecast_vintage,
        authorized_strategy_capital_usd=authorized_capital,
        positions=supplied_book["positions"],
        strategy_research_profile=reference_profile,
        thesis_state=thesis_state,
    )
    signal_report = _dedicated_signal_mix(reference["signal"], strategy_policy)
    main_contract_id = str(reference["legs"]["main"]["contract_id"])
    next_main_contract_id = str(reference["legs"]["next_main"]["contract_id"])
    current_main_lots = int(supplied_book["positions"].get(main_contract_id, 0))
    current_next_lots = int(supplied_book["positions"].get(next_main_contract_id, 0))
    current_risk = dict(reference["strategyRiskAdapter"]["current"])
    capacity = dict(reference["strategyRiskAdapter"]["capacity"])
    risk_capacity_units = int(capacity["risk_capacity_units"])
    current_spread_units = int(current_risk["spread_units"])
    dedicated_ideal_target = int(
        round(float(signal_report["signal"]) * risk_capacity_units)
    )

    state_hash = _visible_state_hash(
        normalized_market,
        forecast_vintage,
        supplied_book,
        authorized_strategy_capital_usd=authorized_capital,
    )
    construction_adjustments = build_oil_strategy_construction_adjustments(
        source_profile,
        visible_state_hash=state_hash,
        contract_ids=[main_contract_id, next_main_contract_id],
    )
    construction_errors = _pair_construction_errors(
        construction_adjustments,
        main_contract_id=main_contract_id,
        next_main_contract_id=next_main_contract_id,
    )
    construction = _apply_pair_construction_v23(
        current_spread_units=current_spread_units,
        ideal_target_spread_units=dedicated_ideal_target,
        capacity_units=risk_capacity_units,
        target_scale_error=float(construction_errors["pair_target_scale_error"]),
        transition_gap_error=float(construction_errors["pair_transition_gap_error"]),
    )

    persistent_target = _apply_spread_position_persistence(
        current_spread_units=current_spread_units,
        proposed_target_units=int(
            construction["construction_submitted_target_spread_units"]
        ),
        capacity_units=risk_capacity_units,
        position_persistence=float(strategy_policy["execution"]["position_persistence"]),
    )
    resolved_thesis_state = resolve_oil_calendar_spread_thesis_state(thesis_state)
    thesis_adjusted_target, thesis_action = _apply_thesis_policy(
        current_spread_units=current_spread_units,
        proposed_target_units=persistent_target,
        signal=float(signal_report["signal"]),
        thesis_state=resolved_thesis_state,
        thesis_config=config["thesis_invalidation"],
    )
    responsive_target = _responsive_target(
        current_units=current_spread_units,
        target_units=thesis_adjusted_target,
        adjustment_speed=float(strategy_policy["execution"]["adjustment_speed"]),
        capacity_units=risk_capacity_units,
    )
    target_spread_units = int(
        clamp(
            float(responsive_target),
            -float(risk_capacity_units),
            float(risk_capacity_units),
        )
    )
    target_main_lots = target_spread_units
    target_next_lots = -target_spread_units

    target_risk = _position_risk_metrics(
        main_lots=target_main_lots,
        next_main_lots=target_next_lots,
        main_price_usd=float(reference["legs"]["main"]["price_usd"]),
        next_main_price_usd=float(reference["legs"]["next_main"]["price_usd"]),
        contract_size_bbl=float(capacity["contract_size_bbl"]),
        initial_margin_rate=float(capacity["initial_margin_rate"]),
        spread_volatility_usd_per_bbl=float(capacity["spread_volatility_usd_per_bbl"]),
    )
    if int(target_risk["absolute_leg_imbalance_lots"]) != 0:
        raise ValueError("calendar spread v0.2.3 target lost exact pair balance")

    current_position = {
        "spread_units": current_spread_units,
        "residual_main_lots": int(current_risk["residual_main_lots"]),
        "residual_next_main_lots": int(current_risk["residual_next_main_lots"]),
    }
    execution_mandate = _paired_execution_mandate(
        current_position=current_position,
        target_spread_units=target_spread_units,
        risk_capacity=capacity,
        strategy_policy=strategy_policy,
        config=config,
    )

    construction_is_exact = all(
        math.isclose(float(value), 0.0, rel_tol=0.0, abs_tol=1e-12)
        for value in (
            construction_errors["pair_target_scale_error"],
            construction_errors["pair_transition_gap_error"],
            construction_errors["curve_lifecycle_planning_error"],
        )
    )
    dedicated_style_is_neutral = all(
        math.isclose(
            float(dedicated_profile["style_radar"][key]),
            float(patch["compatibility_gate"]["neutral_dedicated_style_score"]),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        for key in CALENDAR_SPREAD_STYLE_DIMENSIONS
    )
    reference_target = int(reference["target"]["target_spread_units"])
    compatibility_mode = construction_is_exact and dedicated_style_is_neutral
    if compatibility_mode and target_spread_units != reference_target:
        raise ValueError(
            "calendar spread v0.2.3 neutral zero-error path must reproduce reference target"
        )
    if construction_is_exact and int(
        construction["construction_submitted_target_spread_units"]
    ) != int(construction["ideal_policy_target_spread_units"]):
        raise ValueError("zero-error calendar-spread construction must be identity")

    construction_report = {
        "schemaVersion": "asset-simulation-oil-calendar-spread-construction-v23",
        "source_owner": "oil_strategy_research_v2",
        "strategy_profile_hash": source_profile["profile_hash"],
        "visible_state_hash": state_hash,
        "construction_adjustment_hash": construction_adjustments["identity"][
            "result_hash"
        ],
        "construction_capability_radar": dict(
            source_profile["construction_capability_radar"]
        ),
        "strategy_specific_interpretation": {
            "exposure_construction": "spread_exposure_construction",
            "transition_planning": "pair_transition_planning",
            "contract_lifecycle_planning": "curve_lifecycle_planning",
        },
        **construction,
        **construction_errors,
        "curve_lifecycle_application": str(
            config["construction_adapter"]["lifecycle_error_application"]
        ),
        "curve_lifecycle_error_applied_to_target": False,
        "exact_pair_balance_preserved": True,
        "zero_error_identity_required": True,
        "thesis_is_reversal_policy_owner": True,
        "reference_target_reproduced_when_compatibility_mode": (
            (not compatibility_mode) or target_spread_units == reference_target
        ),
    }

    result = {
        "schemaVersion": "asset-simulation-oil-calendar-spread-decision-v23",
        "asOf": dict(reference["asOf"]),
        "strategy": {
            "strategy_id": OIL_CALENDAR_SPREAD_STRATEGY_V23_ID,
            "display_name": str(config["display_name"]),
            "strategy_taxonomy": dict(config["strategy_taxonomy"]),
            "runtime_status": "research_candidate_not_default_competition_engine",
            "candidate_model_version": OIL_CALENDAR_SPREAD_STRATEGY_V23_MODEL_VERSION,
            "base_model_version": BASE_MODEL_VERSION,
            "reference_engine_model_version": REFERENCE_MODEL_VERSION,
            "strategy_research_profile": {
                "appointment": dict(source_profile["appointment"]),
                "source_style_radar": dict(source_profile["style_radar"]),
                "source_style_tags": list(source_profile["style_tags"]),
                "source_profile_hash": source_profile["profile_hash"],
                "dedicated_style_owner": "oil_calendar_spread_research_v1",
                "dedicated_style_radar": dict(dedicated_profile["style_radar"]),
                "dedicated_style_tags": list(dedicated_profile["style_tags"]),
                "dedicated_style_profile_hash": dedicated_profile["profile_hash"],
                "preference_total_score": None,
                "alpha_score": None,
                "construction_capability_radar": dict(
                    source_profile["construction_capability_radar"]
                ),
            },
        },
        "strategyBook": {
            "owner": "oil_strategy_book_v1",
            "book_id": supplied_book["book_id"],
            "strategy_id": supplied_book["strategy_id"],
            "institution_id": supplied_book["institution_id"],
            "positions": dict(supplied_book["positions"]),
            "book_identity_hash": supplied_book["identity"]["identity_hash"],
            "aggregate_account_positions_consumed": False,
        },
        "pairIdentity": dict(reference["pairIdentity"]),
        "legs": {
            "main": {
                **dict(reference["legs"]["main"]),
                "current_position_lots": current_main_lots,
                "target_position_lots": target_main_lots,
            },
            "next_main": {
                **dict(reference["legs"]["next_main"]),
                "current_position_lots": current_next_lots,
                "target_position_lots": target_next_lots,
            },
        },
        "signal": {
            **signal_report,
            "visible_history_coordinate_owner": (
                "oil_calendar_spread_market_history_adapter"
            ),
        },
        "construction": construction_report,
        "target": {
            "current_spread_units": current_spread_units,
            "ideal_policy_target_spread_units": int(
                construction["ideal_policy_target_spread_units"]
            ),
            "construction_submitted_target_spread_units": int(
                construction["construction_submitted_target_spread_units"]
            ),
            "persistent_target_spread_units": persistent_target,
            "thesis_adjusted_target_spread_units": thesis_adjusted_target,
            "target_spread_units": target_spread_units,
            "reference_target_spread_units": reference_target,
            "target_main_lots": target_main_lots,
            "target_next_main_lots": target_next_lots,
            "lot_ratio_main_to_next": "1:-1",
        },
        "strategyRiskAdapter": {
            "schemaVersion": "asset-simulation-oil-calendar-spread-risk-adapter-v23",
            "capacity": capacity,
            "current": current_risk,
            "target": target_risk,
            "checks": {
                "target_leg_balance_ok": target_main_lots + target_next_lots == 0,
                "target_within_risk_capacity": abs(target_spread_units)
                <= risk_capacity_units,
                "target_margin_within_budget": float(target_risk["margin_usage_usd"])
                <= float(capacity["capital_deployment_budget_usd"]) + 1e-9,
                "residuals_come_from_strategy_book_only": True,
            },
        },
        "pairedExecutionMandate": execution_mandate,
        "thesisInvalidation": {
            "schemaVersion": "asset-simulation-oil-calendar-spread-thesis-decision-v23",
            "policy": dict(config["thesis_invalidation"]),
            "stateBefore": resolved_thesis_state,
            "action": thesis_action,
            "reversal_policy_owner": "calendar_spread_thesis",
        },
        "marketHistoryAdapter": history_adapter,
        "informationPolicy": {
            "visible_market_at_current_cutoff_only": True,
            "published_forecast_vintage_only": True,
            "strategy_owned_book_only": True,
            "aggregate_account_positions_available": False,
            "dedicated_style_projection_uses_future": False,
            "hidden_future_available": False,
            "forecast_truth_available": False,
            "market_history_metadata_only_adapter": True,
            "market_write_back": False,
        },
        "scope": {
            "included": [
                "current_main",
                "adjacent_next_main",
                "one_to_one_lot_ratio",
                "strategy_owned_position_book",
                "dedicated_calendar_spread_pm_style",
                "bounded_pm_construction",
                "zero_error_construction_identity",
            ],
            "excluded": list(config["scope_exclusions"]),
        },
    }
    rounded_result = _round_nested(result)
    identity = {
        "model_version": OIL_CALENDAR_SPREAD_STRATEGY_V23_MODEL_VERSION,
        "strategy_id": OIL_CALENDAR_SPREAD_STRATEGY_V23_ID,
        "config_id": str(patch["config_id"]),
        "config_hash": assets["oil_calendar_spread_strategy_v23_config_hash"],
        "base_config_hash": assets["oil_calendar_spread_strategy_v22_config_hash"],
        "field_contract_id": str(contract["contract_id"]),
        "field_contract_hash": assets[
            "oil_calendar_spread_strategy_v23_contract_hash"
        ],
        "strategy_book_identity_hash": supplied_book["identity"]["identity_hash"],
        "source_strategy_profile_hash": source_profile["profile_hash"],
        "dedicated_style_profile_hash": dedicated_profile["profile_hash"],
        "reference_decision_hash": reference["identity"]["result_hash"],
        "construction_adjustment_hash": construction_adjustments["identity"][
            "result_hash"
        ],
        "market_history_adapter_result_hash": history_adapter["result_hash"],
        "upstream_global_identity_hash": str(
            normalized_market["identity"]["upstream_global_identity_hash"]
        ),
        "forecast_vintage_id": str(
            forecast_vintage.get("identity", {}).get("vintage_id", "")
        ),
        "write_back": False,
        "result_hash": sha256_json(rounded_result),
    }
    identity["identity_hash"] = sha256_json(identity)
    return _round_nested({"identity": identity, **rounded_result})
