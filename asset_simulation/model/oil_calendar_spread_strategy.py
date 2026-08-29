"""Research-stage short-horizon crude-oil calendar-spread strategy.

The strategy trades the current main and next-main named futures contracts as a
real two-leg position.  It never creates a synthetic spread security in the
account.  A positive spread unit is long one main lot and short one next-main
lot; a negative unit is the reverse.

Decision time is information-isolated: only the current visible futures payload,
one published two-contract forecast vintage, the appointed PM profile, current
positions, and committee-authorized capital are accepted.  Realized end-of-turn
prices enter only the separate thesis and PnL evaluation helpers.
"""

from __future__ import annotations

import math
from statistics import fmean, pstdev
from typing import Any, Mapping

from .math_utils import clamp
from .oil_strategy_research import resolve_oil_strategy_runtime_policy
from .registry import load_registered_assets, sha256_json


OIL_CALENDAR_SPREAD_STRATEGY_MODEL_VERSION = (
    "asset-simulation-oil-calendar-spread-strategy-v0.1.2"
)
OIL_CALENDAR_SPREAD_STRATEGY_CONTRACT_ID = "oil_calendar_spread_strategy_v1"
CALENDAR_SPREAD_ROLE_ORDER = ("main", "next_main")
CALENDAR_SPREAD_THESIS_STATUSES = ("active", "watch", "invalidated")


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("oil calendar spread strategy contains a non-finite value")
        return round(value, 8)
    return value


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _integer_lots(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be integer lots")
    return int(value)


def _nonnegative_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be a nonnegative integer")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return int(value)


def _validate_registered_assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_calendar_spread_strategy_config"]
    contract = assets["oil_calendar_spread_strategy_contract"]
    if config["model_version"] != OIL_CALENDAR_SPREAD_STRATEGY_MODEL_VERSION:
        raise ValueError("registered oil calendar spread strategy config version mismatch")
    if contract["contract_id"] != OIL_CALENDAR_SPREAD_STRATEGY_CONTRACT_ID:
        raise ValueError("registered oil calendar spread strategy contract id mismatch")
    if tuple(config["contract_roles"]) != CALENDAR_SPREAD_ROLE_ORDER:
        raise ValueError("oil calendar spread contract roles are out of order")

    signal = config["signal"]
    horizons = [int(value) for value in signal["horizon_weeks"]]
    if horizons != sorted(set(horizons)) or horizons != [2, 4]:
        raise ValueError("oil calendar spread v1 must use the 2-week and 4-week horizons")
    forecast_weight = float(signal["forecast_component_weight"])
    visible_weight = float(signal["visible_curve_component_weight"])
    if min(forecast_weight, visible_weight) < 0.0 or not math.isclose(
        forecast_weight + visible_weight, 1.0
    ):
        raise ValueError("oil calendar spread signal component weights must sum to one")
    if (
        int(signal["historical_lookback_weeks"]) < 4
        or int(signal["momentum_lookback_weeks"]) < 2
        or int(signal["minimum_mean_reversion_history_weeks"]) < 3
        or float(signal["minimum_normalized_scale"]) <= 0.0
        or float(signal["forecast_full_strength_scale_multiples"]) <= 0.0
        or float(signal["momentum_full_strength_scale_multiples"]) <= 0.0
        or float(signal["mean_reversion_full_strength_z"]) <= 0.0
    ):
        raise ValueError("oil calendar spread signal normalization is invalid")

    risk = config["risk_adapter"]
    if (
        not 0.0 < float(risk["maximum_stressed_spread_loss_pct_of_deployed_capital"]) <= 100.0
        or float(risk["spread_volatility_stress_multiplier"]) <= 0.0
        or float(risk["minimum_stressed_spread_move_usd_per_bbl"]) <= 0.0
        or not 0.0 < float(risk["maximum_margin_pct_of_deployed_capital"]) <= 100.0
        or float(risk["minimum_main_expiry_buffer_months"]) < 0.0
        or int(risk["maximum_target_leg_imbalance_lots"]) != 0
        or not 0.0 <= float(risk["maximum_temporary_execution_imbalance_fraction"]) <= 1.0
        or int(risk["maximum_temporary_execution_imbalance_lots"]) < 0
        or not 0.0 <= float(risk["maximum_residual_directional_pnl_share"]) <= 1.0
        or bool(risk["dynamic_spread_margin_credit_enabled"])
    ):
        raise ValueError("oil calendar spread risk adapter config is invalid")

    thesis = config["thesis_invalidation"]
    if tuple(thesis["statuses"]) != CALENDAR_SPREAD_THESIS_STATUSES:
        raise ValueError("oil calendar spread thesis statuses are invalid")
    scales = {key: float(value) for key, value in thesis["status_target_scale"].items()}
    if (
        set(scales) != set(CALENDAR_SPREAD_THESIS_STATUSES)
        or not 0.0 < scales["invalidated"] <= scales["watch"] <= scales["active"]
        or not math.isclose(scales["active"], 1.0)
        or int(thesis["consecutive_failure_turns_to_invalidate"]) < 1
        or float(thesis["severe_forecast_error_z"]) <= 0.0
        or float(thesis["minimum_direction_move_normalized"]) <= 0.0
        or not 0.0 <= float(thesis["direction_reversal_signal_threshold"]) <= 1.0
    ):
        raise ValueError("oil calendar spread thesis invalidation config is invalid")
    return assets, config, contract



def _normalized_spread(
    main_price_usd: float,
    next_price_usd: float,
    *,
    reference_price_usd: float | None = None,
) -> tuple[float, float]:
    """Return main-minus-next spread scaled by one explicit reference price.

    The default reference is the contemporaneous average leg price.  Signal
    changes pass the *decision-time* reference explicitly so a common parallel
    move in both legs cannot manufacture a relative-value signal.
    """

    main_price = _finite_number(main_price_usd, "main price")
    next_price = _finite_number(next_price_usd, "next-main price")
    if min(main_price, next_price) <= 0.0:
        raise ValueError("oil calendar spread leg prices must be positive")
    local_reference = 0.5 * (main_price + next_price)
    reference = (
        local_reference
        if reference_price_usd is None
        else _finite_number(reference_price_usd, "spread normalization reference")
    )
    if reference <= 0.0:
        raise ValueError("oil calendar spread normalization reference must be positive")
    return (main_price - next_price) / reference, reference


def _dollar_spread(main_price_usd: float, next_price_usd: float) -> float:
    main_price = _finite_number(main_price_usd, "main price")
    next_price = _finite_number(next_price_usd, "next-main price")
    if min(main_price, next_price) <= 0.0:
        raise ValueError("oil calendar spread leg prices must be positive")
    return main_price - next_price

def _contract_map(market: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    curve = dict(market.get("curve", {}))
    contracts: dict[str, dict[str, Any]] = {}
    for item_value in curve.get("contracts", ()):
        item = dict(item_value)
        contract_id = str(item.get("contract_id", ""))
        if not contract_id or contract_id in contracts:
            raise ValueError("oil calendar spread market contracts require unique IDs")
        contracts[contract_id] = item
    return contracts


def _forecast_map(forecast_vintage: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    forecasts: dict[str, dict[str, Any]] = {}
    for item_value in forecast_vintage.get("forecasts", ()):
        item = dict(item_value)
        role = str(item.get("role", ""))
        if role in forecasts:
            raise ValueError("oil calendar spread forecast roles must be unique")
        forecasts[role] = item
    if set(forecasts) != set(CALENDAR_SPREAD_ROLE_ORDER):
        raise ValueError("oil calendar spread requires exactly main and next_main forecasts")
    return forecasts




def _validate_current_adjacent_pair(
    market: Mapping[str, Any],
    forecasts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Require the forecast legs to be the market owner's current main and next listed contract."""

    curve = dict(market.get("curve", {}))
    market_main_id = str(curve.get("main_contract_id", ""))
    listed = [dict(item) for item in curve.get("contracts", ())]
    if not market_main_id or not listed:
        raise ValueError("oil calendar spread market lacks current main identity")
    listed_ids = [str(item.get("contract_id", "")) for item in listed]
    if market_main_id not in listed_ids:
        raise ValueError("oil calendar spread market main is not in the listed curve")
    main_index = listed_ids.index(market_main_id)
    if main_index + 1 >= len(listed_ids):
        raise ValueError("oil calendar spread current main has no adjacent next contract")
    expected_next_id = listed_ids[main_index + 1]

    forecast_main_id = str(forecasts["main"].get("contract_id", ""))
    forecast_next_id = str(forecasts["next_main"].get("contract_id", ""))
    if forecast_main_id != market_main_id:
        raise ValueError(
            "oil calendar spread main forecast is not the current market main"
        )
    if forecast_next_id != expected_next_id:
        raise ValueError(
            "oil calendar spread next_main forecast is not adjacent to the current main"
        )
    return {
        "market_main_contract_id": market_main_id,
        "expected_next_main_contract_id": expected_next_id,
        "main_curve_index": main_index,
        "adjacent_pair_valid": True,
    }

def _week_key(week: Mapping[str, Any]) -> tuple[Any, ...] | None:
    if "week_serial" in week:
        value = week["week_serial"]
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return ("week_serial", int(value))
    required = ("year", "month", "week")
    if not all(key in week for key in required):
        return None
    values = tuple(week[key] for key in required)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        return None
    return ("calendar", *map(int, values))


def _visible_week_closes(contract: Mapping[str, Any]) -> dict[tuple[Any, ...], float]:
    result: dict[tuple[Any, ...], float] = {}
    for month_value in contract.get("monthly", ()):
        month = dict(month_value)
        for week_value in month.get("weekly", ()):
            week = dict(week_value)
            key = _week_key(week)
            if key is None:
                continue
            close = _finite_number(week.get("close"), "visible weekly close")
            if close <= 0.0:
                raise ValueError("visible weekly closes must be positive")
            result[key] = close
    return result



def _aligned_visible_spread_history(
    main_contract: Mapping[str, Any],
    next_contract: Mapping[str, Any],
    *,
    current_main_price_usd: float,
    current_next_price_usd: float,
    lookback_weeks: int,
) -> dict[str, Any]:
    """Align visible legs and express every historical spread on one current reference.

    Using one decision-time denominator keeps momentum/reversion direction
    identical to dollar-spread direction.  Common parallel oil-price moves
    therefore do not create a false calendar-spread change.
    """

    main_weeks = _visible_week_closes(main_contract)
    next_weeks = _visible_week_closes(next_contract)
    common_keys = sorted(set(main_weeks).intersection(next_weeks))
    current_reference = 0.5 * (
        _finite_number(current_main_price_usd, "current main price")
        + _finite_number(current_next_price_usd, "current next-main price")
    )
    if current_reference <= 0.0:
        raise ValueError("oil calendar spread current reference must be positive")

    levels_usd: list[float] = []
    levels_normalized: list[float] = []
    observations: list[dict[str, Any]] = []
    for key in common_keys[-max(1, int(lookback_weeks)):]:
        main_close = main_weeks[key]
        next_close = next_weeks[key]
        spread_usd = _dollar_spread(main_close, next_close)
        normalized, _ = _normalized_spread(
            main_close,
            next_close,
            reference_price_usd=current_reference,
        )
        levels_usd.append(spread_usd)
        levels_normalized.append(normalized)
        observations.append(
            {
                "week_key": list(key),
                "main_close_usd": main_close,
                "next_main_close_usd": next_close,
                "spread_usd_per_bbl": spread_usd,
                "normalized_spread": normalized,
                "normalization_reference_price_usd": current_reference,
            }
        )

    current_spread_usd = _dollar_spread(
        current_main_price_usd, current_next_price_usd
    )
    current_normalized, _ = _normalized_spread(
        current_main_price_usd,
        current_next_price_usd,
        reference_price_usd=current_reference,
    )
    if not levels_usd or not math.isclose(
        levels_usd[-1], current_spread_usd, rel_tol=0.0, abs_tol=1e-12
    ):
        levels_usd.append(current_spread_usd)
        levels_normalized.append(current_normalized)
        observations.append(
            {
                "week_key": ["decision_cutoff"],
                "main_close_usd": float(current_main_price_usd),
                "next_main_close_usd": float(current_next_price_usd),
                "spread_usd_per_bbl": current_spread_usd,
                "normalized_spread": current_normalized,
                "normalization_reference_price_usd": current_reference,
            }
        )
    return {
        "levels_usd_per_bbl": levels_usd,
        "levels_normalized": levels_normalized,
        "observations": observations,
        "current_spread_usd_per_bbl": current_spread_usd,
        "current_normalized_spread": current_normalized,
        "current_reference_price_usd": current_reference,
    }

def _select_forecast_bar(forecast: Mapping[str, Any], requested_horizon: int) -> dict[str, Any]:
    horizon = _nonnegative_integer(requested_horizon, "requested forecast horizon")
    if horizon == 0:
        raise ValueError("requested forecast horizon must be positive")
    matching: list[dict[str, Any]] = []
    for value in forecast.get("weekly", ()):
        item = dict(value)
        item_horizon = _nonnegative_integer(
            item.get("horizon_weeks"), "forecast horizon weeks"
        )
        if item_horizon == horizon:
            matching.append(item)
    if len(matching) != 1:
        raise ValueError(
            "oil calendar spread forecast must contain exactly one bar for "
            f"the requested {horizon}-week horizon"
        )
    return matching[0]



def _forecast_spread_components(
    forecasts: Mapping[str, Mapping[str, Any]],
    *,
    current_spread_usd_per_bbl: float,
    current_reference_price_usd: float,
    requested_horizons: list[int],
    requested_weights: list[float],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    current_spread = _finite_number(
        current_spread_usd_per_bbl, "current dollar spread"
    )
    current_reference = _finite_number(
        current_reference_price_usd, "current spread reference price"
    )
    if current_reference <= 0.0:
        raise ValueError("current spread reference price must be positive")

    for horizon, weight in zip(requested_horizons, requested_weights, strict=True):
        main_bar = _select_forecast_bar(forecasts["main"], horizon)
        next_bar = _select_forecast_bar(forecasts["next_main"], horizon)
        main_target_week = str(main_bar.get("target_week", "")).strip()
        next_target_week = str(next_bar.get("target_week", "")).strip()
        if not main_target_week or main_target_week != next_target_week:
            raise ValueError(
                "oil calendar spread forecast legs must share one nonempty target week"
            )
        main_target_week_serial = _nonnegative_integer(
            main_bar.get("week_serial"), "main forecast target week serial"
        )
        next_target_week_serial = _nonnegative_integer(
            next_bar.get("week_serial"), "next-main forecast target week serial"
        )
        if main_target_week_serial != next_target_week_serial:
            raise ValueError(
                "oil calendar spread forecast legs must share one target week serial"
            )
        main_close = _finite_number(main_bar["close"], "main forecast close")
        next_close = _finite_number(next_bar["close"], "next-main forecast close")
        forecast_spread_usd = _dollar_spread(main_close, next_close)
        forecast_change_usd = forecast_spread_usd - current_spread
        forecast_normalized, _ = _normalized_spread(
            main_close,
            next_close,
            reference_price_usd=current_reference,
        )
        forecast_change_normalized = forecast_change_usd / current_reference

        main_low = _finite_number(main_bar["confidence_low"], "main confidence low")
        main_high = _finite_number(main_bar["confidence_high"], "main confidence high")
        next_low = _finite_number(next_bar["confidence_low"], "next-main confidence low")
        next_high = _finite_number(next_bar["confidence_high"], "next-main confidence high")
        if not 0.0 < main_low < main_high or not 0.0 < next_low < next_high:
            raise ValueError("oil calendar spread forecast confidence bands are invalid")
        main_uncertainty = 0.5 * math.log(main_high / main_low)
        next_uncertainty = 0.5 * math.log(next_high / next_low)
        pair_uncertainty = (
            math.sqrt(main_uncertainty**2 + next_uncertainty**2) / math.sqrt(2.0)
        )
        components.append(
            {
                "requested_horizon_weeks": int(horizon),
                "main_selected_horizon_weeks": int(main_bar["horizon_weeks"]),
                "next_main_selected_horizon_weeks": int(next_bar["horizon_weeks"]),
                "target_week": main_target_week,
                "target_week_serial": main_target_week_serial,
                "main_target_week": main_target_week,
                "next_main_target_week": next_target_week,
                "weight": float(weight),
                "forecast_main_close_usd": main_close,
                "forecast_next_main_close_usd": next_close,
                "forecast_reference_price_usd": current_reference,
                "forecast_spread_usd_per_bbl": forecast_spread_usd,
                "forecast_normalized_spread": forecast_normalized,
                "forecast_spread_change_usd_per_bbl": forecast_change_usd,
                "forecast_spread_change_normalized": forecast_change_normalized,
                "pair_uncertainty_log": pair_uncertainty,
                "pair_uncertainty_usd_per_bbl": pair_uncertainty * current_reference,
            }
        )
    return components

def _pm_horizon_weights(
    strategy_policy: Mapping[str, Any], requested_horizons: list[int]
) -> list[float]:
    signal = dict(strategy_policy["signal"])
    policy_horizons = [int(value) for value in signal["horizon_weeks"]]
    policy_weights = [float(value) for value in signal["horizon_weights"]]
    if len(policy_horizons) != len(policy_weights):
        raise ValueError("PM horizon policy is invalid")
    mapping = dict(zip(policy_horizons, policy_weights, strict=True))
    selected = [max(0.0, float(mapping.get(horizon, 0.0))) for horizon in requested_horizons]
    total = sum(selected)
    if total <= 0.0:
        return [1.0 / len(requested_horizons)] * len(requested_horizons)
    return [value / total for value in selected]



def _spread_signal(
    history: Mapping[str, Any],
    forecasts: Mapping[str, Mapping[str, Any]],
    *,
    strategy_policy: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one spread signal whose sign is always the sign of dollar spread change."""

    signal_config = config["signal"]
    levels_usd = [float(value) for value in history["levels_usd_per_bbl"]]
    current_spread_usd = float(history["current_spread_usd_per_bbl"])
    current_reference = float(history["current_reference_price_usd"])
    current_normalized = float(history["current_normalized_spread"])
    changes_usd = [
        right - left for left, right in zip(levels_usd, levels_usd[1:])
    ]
    minimum_scale_normalized = float(signal_config["minimum_normalized_scale"])
    minimum_scale_usd = minimum_scale_normalized * current_reference
    change_volatility_usd = (
        pstdev(changes_usd)
        if len(changes_usd) >= 2
        else (abs(changes_usd[-1]) if changes_usd else minimum_scale_usd)
    )
    change_volatility_usd = max(minimum_scale_usd, change_volatility_usd)

    requested_horizons = [int(value) for value in signal_config["horizon_weeks"]]
    horizon_weights = _pm_horizon_weights(strategy_policy, requested_horizons)
    components = _forecast_spread_components(
        forecasts,
        current_spread_usd_per_bbl=current_spread_usd,
        current_reference_price_usd=current_reference,
        requested_horizons=requested_horizons,
        requested_weights=horizon_weights,
    )
    forecast_change_usd = sum(
        float(item["weight"])
        * float(item["forecast_spread_change_usd_per_bbl"])
        for item in components
    )
    forecast_change_normalized = forecast_change_usd / current_reference
    forecast_uncertainty_usd = sum(
        float(item["weight"]) * float(item["pair_uncertainty_usd_per_bbl"])
        for item in components
    )
    forecast_scale_usd = max(
        minimum_scale_usd,
        change_volatility_usd,
        forecast_uncertainty_usd
        * float(signal_config["forecast_uncertainty_scale_weight"]),
    )
    forecast_signal = clamp(
        forecast_change_usd
        / forecast_scale_usd
        / float(signal_config["forecast_full_strength_scale_multiples"]),
        -1.0,
        1.0,
    )

    momentum_lookback = min(
        int(signal_config["momentum_lookback_weeks"]), len(levels_usd)
    )
    momentum_change_usd = (
        0.0
        if momentum_lookback < 2
        else levels_usd[-1] - levels_usd[-momentum_lookback]
    )
    momentum_signal = clamp(
        momentum_change_usd
        / change_volatility_usd
        / float(signal_config["momentum_full_strength_scale_multiples"]),
        -1.0,
        1.0,
    )

    minimum_reversion_history = int(
        signal_config["minimum_mean_reversion_history_weeks"]
    )
    mean_reversion_available = len(levels_usd) >= minimum_reversion_history
    historical_center_usd = fmean(levels_usd[:-1] or levels_usd)
    level_volatility_usd = (
        pstdev(levels_usd[:-1])
        if len(levels_usd[:-1]) >= 2
        else minimum_scale_usd
    )
    level_volatility_usd = max(minimum_scale_usd, level_volatility_usd)
    level_z = (
        0.0
        if not mean_reversion_available
        else (current_spread_usd - historical_center_usd) / level_volatility_usd
    )
    mean_reversion_signal = clamp(
        -level_z / float(signal_config["mean_reversion_full_strength_z"]),
        -1.0,
        1.0,
    )

    pm_signal = dict(strategy_policy["signal"])
    continuation_weight = float(pm_signal["continuation_weight"])
    reversion_weight = float(pm_signal["reversion_weight"])
    visible_curve_signal = (
        continuation_weight * momentum_signal
        + reversion_weight * mean_reversion_signal
    )
    forecast_component_weight = float(signal_config["forecast_component_weight"])
    visible_component_weight = float(
        signal_config["visible_curve_component_weight"]
    )
    raw_signal = (
        forecast_component_weight * forecast_signal
        + visible_component_weight * visible_curve_signal
    )
    deadband = float(pm_signal["signal_deadband_abs"])
    if abs(raw_signal) <= deadband:
        signal = 0.0
    else:
        signal = math.copysign(
            (abs(raw_signal) - deadband) / max(1e-9, 1.0 - deadband),
            raw_signal,
        )
    return {
        "spread_definition": "P_main - P_next_main",
        "normalization": str(signal_config["spread_normalization"]),
        "normalization_reference_price_usd": current_reference,
        "current_spread_usd_per_bbl": current_spread_usd,
        "current_normalized_spread": current_normalized,
        "forecast_spread_change_usd_per_bbl": forecast_change_usd,
        "forecast_spread_change_normalized": forecast_change_normalized,
        "forecast_signal": forecast_signal,
        "curve_momentum_change_usd_per_bbl": momentum_change_usd,
        "curve_momentum_change_normalized": momentum_change_usd / current_reference,
        "curve_momentum_signal": momentum_signal,
        "curve_mean_reversion_z": level_z,
        "curve_mean_reversion_signal": mean_reversion_signal,
        "visible_curve_signal": visible_curve_signal,
        "continuation_weight": continuation_weight,
        "reversion_weight": reversion_weight,
        "forecast_component_weight": forecast_component_weight,
        "visible_curve_component_weight": visible_component_weight,
        "raw_signal": raw_signal,
        "signal_deadband_abs": deadband,
        "signal": clamp(signal, -1.0, 1.0),
        "historical_observation_count": len(levels_usd),
        "historical_change_volatility_usd_per_bbl": change_volatility_usd,
        "historical_change_volatility_normalized": (
            change_volatility_usd / current_reference
        ),
        "historical_level_center_usd_per_bbl": historical_center_usd,
        "historical_level_center_normalized": historical_center_usd / current_reference,
        "historical_level_volatility_usd_per_bbl": level_volatility_usd,
        "historical_level_volatility_normalized": (
            level_volatility_usd / current_reference
        ),
        "mean_reversion_available": mean_reversion_available,
        "forecast_normalization_scale_usd_per_bbl": forecast_scale_usd,
        "forecast_normalization_scale": forecast_scale_usd / current_reference,
        "forecast_pair_uncertainty_usd_per_bbl": forecast_uncertainty_usd,
        "forecast_pair_uncertainty_log": forecast_uncertainty_usd / current_reference,
        "horizon_weights": horizon_weights,
        "horizon_components": components,
        "directional_consistency": {
            "forecast_signal_matches_dollar_spread_change": (
                math.isclose(forecast_change_usd, 0.0, abs_tol=1e-12)
                and math.isclose(forecast_signal, 0.0, abs_tol=1e-12)
            )
            or forecast_change_usd * forecast_signal > 0.0,
        },
    }


def _months_to_expiry(contract: Mapping[str, Any]) -> float:
    """Use the futures-market owner's published remaining maturity verbatim."""

    if "months_to_expiry" not in contract:
        raise ValueError(
            "oil calendar spread requires market-owner months_to_expiry"
        )
    months = _finite_number(contract["months_to_expiry"], "market months to expiry")
    if months < 0.0:
        raise ValueError("market months_to_expiry cannot be negative")
    if "half_turns_to_expiry" in contract:
        half_turns = _finite_number(
            contract["half_turns_to_expiry"], "market half-turns to expiry"
        )
        if not math.isclose(months, half_turns / 2.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("market maturity fields are internally inconsistent")
    return months

def _extract_spread_position(main_lots: int, next_main_lots: int) -> dict[str, int]:
    main = int(main_lots)
    next_main = int(next_main_lots)
    if main > 0 and next_main < 0:
        spread_units = min(main, -next_main)
    elif main < 0 and next_main > 0:
        spread_units = -min(-main, next_main)
    else:
        spread_units = 0
    residual_main = main - spread_units
    residual_next = next_main + spread_units
    return {
        "spread_units": spread_units,
        "residual_main_lots": residual_main,
        "residual_next_main_lots": residual_next,
        "signed_leg_imbalance_lots": residual_main + residual_next,
        "absolute_leg_imbalance_lots": abs(residual_main) + abs(residual_next),
    }


def _apply_spread_position_persistence(
    *,
    current_spread_units: int,
    proposed_target_units: int,
    capacity_units: int,
    position_persistence: float,
) -> int:
    capacity = max(0, int(capacity_units))
    proposed = int(clamp(float(proposed_target_units), -float(capacity), float(capacity)))
    current = int(clamp(float(current_spread_units), -float(capacity), float(capacity)))
    persistence = clamp(float(position_persistence), 0.0, 1.0)
    if current == 0 or (current * proposed > 0 and abs(proposed) >= abs(current)):
        return proposed
    if current * proposed >= 0:
        retained = proposed + persistence * (current - proposed)
    else:
        retained = proposed + 0.5 * persistence * current
    return int(clamp(float(round(retained)), -float(capacity), float(capacity)))


def resolve_oil_calendar_spread_thesis_state(
    state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw = dict(state or {})
    status = str(raw.get("status", "active"))
    if status not in CALENDAR_SPREAD_THESIS_STATUSES:
        raise ValueError("oil calendar spread thesis status is invalid")
    failures = int(raw.get("consecutive_direction_misses", 0))
    if failures < 0:
        raise ValueError("oil calendar spread thesis failure count cannot be negative")
    last_signal = _finite_number(raw.get("last_signal", 0.0), "calendar spread thesis last signal")
    if not -1.0 <= last_signal <= 1.0:
        raise ValueError("oil calendar spread thesis last signal must be in [-1, 1]")
    return {
        "schemaVersion": "asset-simulation-oil-calendar-spread-thesis-state-v1",
        "status": status,
        "consecutive_direction_misses": failures,
        "recovery_turns": max(0, int(raw.get("recovery_turns", 0))),
        "last_signal": last_signal,
        "last_evaluation": dict(raw.get("last_evaluation", {})),
    }


def _apply_thesis_policy(
    *,
    current_spread_units: int,
    proposed_target_units: int,
    signal: float,
    thesis_state: Mapping[str, Any],
    thesis_config: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    state = resolve_oil_calendar_spread_thesis_state(thesis_state)
    status = str(state["status"])
    scale = float(thesis_config["status_target_scale"][status])
    proposed = int(proposed_target_units)
    adjusted = int(round(proposed * scale))
    current = int(current_spread_units)
    threshold = float(thesis_config["direction_reversal_signal_threshold"])
    previous_signal = float(state["last_signal"])
    signal_reversal = (
        abs(previous_signal) >= threshold
        and abs(float(signal)) >= threshold
        and previous_signal * float(signal) < 0.0
    )
    position_reversal = current != 0 and current * float(signal) < 0.0 and abs(float(signal)) >= threshold
    material_reversal = signal_reversal or position_reversal
    action = "unchanged" if adjusted == proposed else "scaled_after_prior_miss"
    if (
        bool(thesis_config["direction_reversal_requires_exit_first"])
        and material_reversal
        and current != 0
    ):
        adjusted = 0
        action = "exit_before_direction_reversal"
    if (
        status == "invalidated"
        and not bool(thesis_config["invalidated_can_increase_same_direction"])
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
        "material_direction_reversal": material_reversal,
        "action": action,
        "pre_thesis_target_spread_units": proposed,
        "thesis_adjusted_target_spread_units": adjusted,
    }


def _responsive_target(
    *, current_units: int, target_units: int, adjustment_speed: float, capacity_units: int
) -> int:
    capacity = max(0, int(capacity_units))
    current = int(clamp(float(current_units), -float(capacity), float(capacity)))
    target = int(clamp(float(target_units), -float(capacity), float(capacity)))
    speed = clamp(float(adjustment_speed), 0.0, 1.0)
    gap = target - current
    if gap == 0 or speed <= 0.0:
        return current
    step = int(round(abs(gap) * speed))
    if step == 0:
        step = 1
    step = min(abs(gap), step)
    result = current + (step if gap > 0 else -step)
    if current * target < 0 and current * result < 0 and abs(result) > abs(current):
        raise ValueError("oil calendar spread responsiveness crossed zero incorrectly")
    return int(clamp(float(result), -float(capacity), float(capacity)))



def _risk_capacity(
    market: Mapping[str, Any],
    *,
    main_contract: Mapping[str, Any],
    next_contract: Mapping[str, Any],
    main_price_usd: float,
    next_price_usd: float,
    historical_change_volatility_usd_per_bbl: float,
    authorized_strategy_capital_usd: float,
    capital_deployment_pct: float,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    specification = dict(market["contractSpecification"])
    contract_size = _finite_number(specification["contract_size_bbl"], "contract size")
    configured_size = float(config["sizing"]["contract_size_bbl"])
    if not math.isclose(contract_size, configured_size):
        raise ValueError("oil calendar spread contract size differs from its v1 mandate")
    initial_margin_rate = _finite_number(
        specification["initial_margin_rate_pct"], "initial margin rate"
    ) / 100.0
    if not 0.0 < initial_margin_rate < 1.0:
        raise ValueError("oil calendar spread initial margin rate is invalid")

    risk_config = config["risk_adapter"]
    deployment_budget = (
        float(authorized_strategy_capital_usd)
        * float(capital_deployment_pct)
        / 100.0
    )
    pair_margin_per_unit = (
        (float(main_price_usd) + float(next_price_usd))
        * contract_size
        * initial_margin_rate
    )
    margin_budget = deployment_budget * float(
        risk_config["maximum_margin_pct_of_deployed_capital"]
    ) / 100.0
    margin_capacity = math.floor(margin_budget / max(1e-9, pair_margin_per_unit))

    main_limits = dict(main_contract["participantLimits"])
    next_limits = dict(next_contract["participantLimits"])
    main_position_limit = int(main_limits["single_contract_position_limit_lots"])
    next_position_limit = int(next_limits["single_contract_position_limit_lots"])
    gross_market_cap = int(
        market["participantLimitsPolicy"]["all_contract_gross_position_cap_lots"]
    )
    market_capacity = min(
        main_position_limit, next_position_limit, gross_market_cap // 2
    )

    spread_vol_usd_per_bbl = max(
        0.0,
        _finite_number(
            historical_change_volatility_usd_per_bbl,
            "historical dollar spread volatility",
        ),
    )
    stressed_move = max(
        float(risk_config["minimum_stressed_spread_move_usd_per_bbl"]),
        spread_vol_usd_per_bbl
        * float(risk_config["spread_volatility_stress_multiplier"]),
    )
    stressed_loss_budget = deployment_budget * float(
        risk_config["maximum_stressed_spread_loss_pct_of_deployed_capital"]
    ) / 100.0
    spread_volatility_capacity = math.floor(
        stressed_loss_budget / max(1e-9, stressed_move * contract_size)
    )

    main_months = _months_to_expiry(main_contract)
    next_months = _months_to_expiry(next_contract)
    expiry_order_valid = next_months > main_months
    main_expiry_buffer_ok = main_months >= float(
        risk_config["minimum_main_expiry_buffer_months"]
    )
    main_new_trades = bool(main_limits["new_trades_allowed"])
    next_new_trades = bool(next_limits["new_trades_allowed"])
    expiry_roll_mismatch = not (
        expiry_order_valid
        and main_expiry_buffer_ok
        and main_new_trades
        and next_new_trades
    )

    capacity_candidates = {
        "conservative_pair_margin": max(0, margin_capacity),
        "market_leg_and_gross_limits": max(0, market_capacity),
        "stressed_spread_volatility": max(0, spread_volatility_capacity),
    }
    risk_capacity = min(capacity_candidates.values()) if capacity_candidates else 0
    if expiry_roll_mismatch:
        risk_capacity = 0
    binding_capacity = (
        "expiry_or_roll_mismatch"
        if expiry_roll_mismatch
        else min(capacity_candidates, key=capacity_candidates.get)
    )
    return {
        "authorized_strategy_capital_usd": float(authorized_strategy_capital_usd),
        "capital_deployment_pct_of_authorized_capital": float(
            capital_deployment_pct
        ),
        "capital_deployment_budget_usd": deployment_budget,
        "contract_size_bbl": contract_size,
        "initial_margin_rate": initial_margin_rate,
        "pair_margin_per_unit_usd": pair_margin_per_unit,
        "margin_capacity_units": max(0, margin_capacity),
        "main_position_limit_lots": main_position_limit,
        "next_main_position_limit_lots": next_position_limit,
        "all_contract_gross_position_cap_lots": gross_market_cap,
        "market_capacity_units": max(0, market_capacity),
        "spread_volatility_usd_per_bbl": spread_vol_usd_per_bbl,
        "stressed_spread_move_usd_per_bbl": stressed_move,
        "stressed_spread_loss_budget_usd": stressed_loss_budget,
        "spread_volatility_capacity_units": max(0, spread_volatility_capacity),
        "main_turn_liquidity_lots": int(main_limits["turn_trade_limit_lots"]),
        "next_main_turn_liquidity_lots": int(next_limits["turn_trade_limit_lots"]),
        "pair_turn_liquidity_units": min(
            int(main_limits["turn_trade_limit_lots"]),
            int(next_limits["turn_trade_limit_lots"]),
        ),
        "main_months_to_expiry": main_months,
        "next_main_months_to_expiry": next_months,
        "maturity_source": "oil_futures_overlay.market_owner",
        "expiry_order_valid": expiry_order_valid,
        "main_expiry_buffer_ok": main_expiry_buffer_ok,
        "main_new_trades_allowed": main_new_trades,
        "next_main_new_trades_allowed": next_new_trades,
        "expiry_roll_mismatch": expiry_roll_mismatch,
        "capacity_candidates_units": capacity_candidates,
        "risk_capacity_units": max(0, risk_capacity),
        "binding_capacity": binding_capacity,
        "dynamic_spread_margin_credit_used": False,
    }

def _position_risk_metrics(
    *,
    main_lots: int,
    next_main_lots: int,
    main_price_usd: float,
    next_main_price_usd: float,
    contract_size_bbl: float,
    initial_margin_rate: float,
    spread_volatility_usd_per_bbl: float,
) -> dict[str, Any]:
    extracted = _extract_spread_position(main_lots, next_main_lots)
    leg_gross = (
        abs(int(main_lots)) * float(main_price_usd)
        + abs(int(next_main_lots)) * float(next_main_price_usd)
    ) * float(contract_size_bbl)
    marked_net = (
        int(main_lots) * float(main_price_usd)
        + int(next_main_lots) * float(next_main_price_usd)
    ) * float(contract_size_bbl)
    delta_lots = int(main_lots) + int(next_main_lots)
    margin = leg_gross * float(initial_margin_rate)
    return {
        **extracted,
        "main_lots": int(main_lots),
        "next_main_lots": int(next_main_lots),
        "leg_gross_exposure_usd": leg_gross,
        "net_directional_delta_lots": delta_lots,
        "net_directional_delta_bbl": delta_lots * float(contract_size_bbl),
        "marked_net_notional_usd": marked_net,
        "margin_usage_usd": margin,
        "spread_volatility_usd_per_bbl": float(spread_volatility_usd_per_bbl),
    }



def _paired_execution_mandate(
    *,
    current_position: Mapping[str, int],
    target_spread_units: int,
    risk_capacity: Mapping[str, Any],
    strategy_policy: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    current_units = int(current_position["spread_units"])
    target_units = int(target_spread_units)
    desired_pair_delta = target_units - current_units
    residual_main = int(current_position["residual_main_lots"])
    residual_next = int(current_position["residual_next_main_lots"])
    execution_config = config["paired_execution"]
    risk_config = config["risk_adapter"]

    main_turn_limit = max(0, int(risk_capacity["main_turn_liquidity_lots"]))
    next_turn_limit = max(0, int(risk_capacity["next_main_turn_liquidity_lots"]))
    remediation_main_lots = min(abs(residual_main), main_turn_limit)
    requested_remediation_main = (
        -remediation_main_lots
        if residual_main > 0
        else remediation_main_lots
        if residual_main < 0
        else 0
    )
    remediation_next_lots = min(abs(residual_next), next_turn_limit)
    requested_remediation_next = (
        -remediation_next_lots
        if residual_next > 0
        else remediation_next_lots
        if residual_next < 0
        else 0
    )
    remaining_main_turn_capacity = main_turn_limit - remediation_main_lots
    remaining_next_turn_capacity = next_turn_limit - remediation_next_lots
    pair_turn_limit = min(
        remaining_main_turn_capacity, remaining_next_turn_capacity
    )
    authorized_pair_lots = min(abs(desired_pair_delta), pair_turn_limit)
    requested_pair_delta = (
        authorized_pair_lots
        if desired_pair_delta > 0
        else -authorized_pair_lots
        if desired_pair_delta < 0
        else 0
    )
    target_gap_after_request = desired_pair_delta - requested_pair_delta
    remaining_residual_main = residual_main + requested_remediation_main
    remaining_residual_next = residual_next + requested_remediation_next
    combined_main_turnover = remediation_main_lots + abs(requested_pair_delta)
    combined_next_turnover = remediation_next_lots + abs(requested_pair_delta)

    requested_pairs = abs(requested_pair_delta)
    imbalance_fraction_limit = math.floor(
        requested_pairs
        * float(risk_config["maximum_temporary_execution_imbalance_fraction"])
    )
    temporary_imbalance_tolerance = min(
        int(risk_config["maximum_temporary_execution_imbalance_lots"]),
        imbalance_fraction_limit,
    )
    gross_turnover_multiplier = float(
        strategy_policy["execution"]["gross_turnover_multiplier"]
    )
    turnover_reference = max(abs(current_units), abs(target_units), 1)
    advisory_pair_turnover_budget = math.floor(
        turnover_reference * gross_turnover_multiplier
    )
    return {
        "schemaVersion": "asset-simulation-oil-calendar-spread-execution-mandate-v1",
        "mandate_type": str(execution_config["mandate_type"]),
        "preferred_method": str(execution_config["preferred_method"]),
        "execution_windows": int(execution_config["execution_windows_per_turn"]),
        "desired_pair_delta_units": desired_pair_delta,
        "requested_pair_delta_units": requested_pair_delta,
        "requested_pair_lots": requested_pairs,
        "requested_main_delta_lots": requested_pair_delta,
        "requested_next_main_delta_lots": -requested_pair_delta,
        "unrequested_target_gap_units": target_gap_after_request,
        "current_spread_units": current_units,
        "target_spread_units": target_units,
        "main_turn_liquidity_lots": main_turn_limit,
        "next_main_turn_liquidity_lots": next_turn_limit,
        "pair_turn_liquidity_units": pair_turn_limit,
        "main_turn_liquidity_reserved_for_remediation_lots": remediation_main_lots,
        "next_main_turn_liquidity_reserved_for_remediation_lots": remediation_next_lots,
        "main_turn_liquidity_remaining_for_pair_lots": remaining_main_turn_capacity,
        "next_main_turn_liquidity_remaining_for_pair_lots": remaining_next_turn_capacity,
        "combined_requested_main_turnover_lots": combined_main_turnover,
        "combined_requested_next_main_turnover_lots": combined_next_turnover,
        "request_within_both_leg_turn_limits": (
            combined_main_turnover <= main_turn_limit
            and combined_next_turnover <= next_turn_limit
        ),
        "advisory_pair_turnover_budget_units": advisory_pair_turnover_budget,
        "turnover_intensity": float(
            strategy_policy["execution"]["turnover_intensity"]
        ),
        "temporary_legging_allowed": bool(
            execution_config["temporary_legging_allowed"]
        ),
        "temporary_leg_imbalance_tolerance_lots": (
            temporary_imbalance_tolerance
        ),
        "imbalanceRemediation": {
            "required": residual_main != 0 or residual_next != 0,
            "current_residual_main_lots": residual_main,
            "current_residual_next_main_lots": residual_next,
            "requested_main_delta_lots": requested_remediation_main,
            "requested_next_main_delta_lots": requested_remediation_next,
            "remaining_residual_main_lots_after_request": remaining_residual_main,
            "remaining_residual_next_main_lots_after_request": remaining_residual_next,
            "fully_remediated_by_request": (
                remaining_residual_main == 0 and remaining_residual_next == 0
            ),
            "turn_capacity_priority": "remediation_before_new_pair_delta",
            "priority": list(execution_config["remediation_priority"]),
        },
        "governance": {
            "single_mandate": True,
            "leg_fills_recorded_separately": True,
            "synthetic_security_created": False,
            "unfilled_orders_persist": bool(
                execution_config["unfilled_orders_persist"]
            ),
            "execution_department_owns_scheduling": True,
            "hard_leg_turn_limits_enforced": True,
            "remediation_and_pair_share_leg_turn_limits": True,
        },
    }

def build_oil_calendar_spread_research_decision(
    market: Mapping[str, Any],
    forecast_vintage: Mapping[str, Any],
    *,
    authorized_strategy_capital_usd: float,
    positions: Mapping[str, int] | None = None,
    strategy_research_profile: Mapping[str, Any] | None = None,
    thesis_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic main-versus-next-main spread research decision.

    ``authorized_strategy_capital_usd`` is intentionally explicit.  The PM may
    deploy only a style-dependent share of this committee-owned allocation.
    """

    assets, config, contract = _validate_registered_assets()
    if not bool(market.get("ok")) or not bool(forecast_vintage.get("ok")):
        raise ValueError("oil calendar spread strategy requires successful market and forecast payloads")
    market_as_of = dict(market["asOf"])
    forecast_as_of = dict(forecast_vintage["asOf"])
    market_cutoff = tuple(int(market_as_of[key]) for key in ("year", "month", "half"))
    forecast_cutoff = tuple(int(forecast_as_of[key]) for key in ("year", "month", "half"))
    if market_cutoff != forecast_cutoff:
        raise ValueError("oil calendar spread market and forecast cutoffs must match")
    if str(market["identity"]["upstream_global_identity_hash"]) != str(
        forecast_vintage["identity"]["upstream_global_identity_hash"]
    ):
        raise ValueError("oil calendar spread market and forecast worlds must match")

    authorized_capital = _finite_number(
        authorized_strategy_capital_usd, "authorized strategy capital"
    )
    if authorized_capital <= 0.0:
        raise ValueError("authorized strategy capital must be positive")
    raw_positions = dict(positions or {})
    if any(isinstance(value, bool) or not isinstance(value, int) for value in raw_positions.values()):
        raise ValueError("oil calendar spread positions must be integer lots")

    strategy_profile, strategy_policy = resolve_oil_strategy_runtime_policy(
        strategy_research_profile
    )
    contracts = _contract_map(market)
    forecasts = _forecast_map(forecast_vintage)
    pair_identity = _validate_current_adjacent_pair(market, forecasts)
    main_contract_id = str(forecasts["main"]["contract_id"])
    next_contract_id = str(forecasts["next_main"]["contract_id"])
    if main_contract_id not in contracts or next_contract_id not in contracts:
        raise ValueError(
            "oil calendar spread forecast legs are unavailable in the current market"
        )
    main_contract = contracts[main_contract_id]
    next_contract = contracts[next_contract_id]
    main_price = _finite_number(main_contract["price_usd"], "current main price")
    next_price = _finite_number(next_contract["price_usd"], "current next-main price")

    history = _aligned_visible_spread_history(
        main_contract,
        next_contract,
        current_main_price_usd=main_price,
        current_next_price_usd=next_price,
        lookback_weeks=int(config["signal"]["historical_lookback_weeks"]),
    )
    signal_report = _spread_signal(
        history,
        forecasts,
        strategy_policy=strategy_policy,
        config=config,
    )
    capital_deployment_pct = float(
        strategy_policy["risk"]["capital_deployment_pct_of_allocated_equity"]
    )
    capacity = _risk_capacity(
        market,
        main_contract=main_contract,
        next_contract=next_contract,
        main_price_usd=main_price,
        next_price_usd=next_price,
        historical_change_volatility_usd_per_bbl=float(
            signal_report["historical_change_volatility_usd_per_bbl"]
        ),
        authorized_strategy_capital_usd=authorized_capital,
        capital_deployment_pct=capital_deployment_pct,
        config=config,
    )

    current_main_lots = int(raw_positions.get(main_contract_id, 0))
    current_next_lots = int(raw_positions.get(next_contract_id, 0))
    current_position = _extract_spread_position(current_main_lots, current_next_lots)
    risk_capacity_units = int(capacity["risk_capacity_units"])
    pre_persistence_target = int(
        round(float(signal_report["signal"]) * risk_capacity_units)
    )
    persistent_target = _apply_spread_position_persistence(
        current_spread_units=int(current_position["spread_units"]),
        proposed_target_units=pre_persistence_target,
        capacity_units=risk_capacity_units,
        position_persistence=float(strategy_policy["execution"]["position_persistence"]),
    )
    resolved_thesis_state = resolve_oil_calendar_spread_thesis_state(thesis_state)
    thesis_adjusted_target, thesis_action = _apply_thesis_policy(
        current_spread_units=int(current_position["spread_units"]),
        proposed_target_units=persistent_target,
        signal=float(signal_report["signal"]),
        thesis_state=resolved_thesis_state,
        thesis_config=config["thesis_invalidation"],
    )
    responsive_target = _responsive_target(
        current_units=int(current_position["spread_units"]),
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

    current_risk = _position_risk_metrics(
        main_lots=current_main_lots,
        next_main_lots=current_next_lots,
        main_price_usd=main_price,
        next_main_price_usd=next_price,
        contract_size_bbl=float(capacity["contract_size_bbl"]),
        initial_margin_rate=float(capacity["initial_margin_rate"]),
        spread_volatility_usd_per_bbl=float(capacity["spread_volatility_usd_per_bbl"]),
    )
    target_risk = _position_risk_metrics(
        main_lots=target_main_lots,
        next_main_lots=target_next_lots,
        main_price_usd=main_price,
        next_main_price_usd=next_price,
        contract_size_bbl=float(capacity["contract_size_bbl"]),
        initial_margin_rate=float(capacity["initial_margin_rate"]),
        spread_volatility_usd_per_bbl=float(capacity["spread_volatility_usd_per_bbl"]),
    )
    if int(target_risk["absolute_leg_imbalance_lots"]) != int(
        config["risk_adapter"]["maximum_target_leg_imbalance_lots"]
    ):
        raise ValueError("oil calendar spread target must remain exactly one-to-one by lots")

    execution_mandate = _paired_execution_mandate(
        current_position=current_position,
        target_spread_units=target_spread_units,
        risk_capacity=capacity,
        strategy_policy=strategy_policy,
        config=config,
    )
    result = {
        "schemaVersion": "asset-simulation-oil-calendar-spread-decision-v1",
        "asOf": market_as_of,
        "strategy": {
            "strategy_id": str(config["strategy_id"]),
            "display_name": str(config["display_name"]),
            "strategy_type": "relative_value_calendar_spread",
            "runtime_status": "research_candidate_not_default_competition_engine",
            "strategy_research_profile": {
                "appointment": strategy_profile["appointment"],
                "style_radar": strategy_profile["style_radar"],
                "style_tags": strategy_profile["style_tags"],
                "preference_total_score": None,
                "profile_hash": strategy_profile["profile_hash"],
            },
            "reused_pm_dimensions": [
                "continuation_reversion",
                "capital_deployment",
                "responsiveness",
                "selectivity",
                "turnover_activity",
                "holding_patience",
                "forecast_horizon",
            ],
            "intentionally_unused_pm_dimensions": ["near_month_focus"],
            "resolved_policy": strategy_policy,
        },
        "pairIdentity": pair_identity,
        "legs": {
            "main": {
                "role": "main",
                "contract_id": main_contract_id,
                "price_usd": main_price,
                "current_position_lots": current_main_lots,
                "target_position_lots": target_main_lots,
            },
            "next_main": {
                "role": "next_main",
                "contract_id": next_contract_id,
                "price_usd": next_price,
                "current_position_lots": current_next_lots,
                "target_position_lots": target_next_lots,
            },
        },
        "signal": signal_report,
        "target": {
            "current_spread_units": int(current_position["spread_units"]),
            "pre_persistence_target_spread_units": pre_persistence_target,
            "persistent_target_spread_units": persistent_target,
            "thesis_adjusted_target_spread_units": thesis_adjusted_target,
            "target_spread_units": target_spread_units,
            "target_main_lots": target_main_lots,
            "target_next_main_lots": target_next_lots,
            "lot_ratio_main_to_next": "1:-1",
        },
        "strategyRiskAdapter": {
            "schemaVersion": "asset-simulation-oil-calendar-spread-risk-adapter-v1",
            "capacity": capacity,
            "current": current_risk,
            "target": target_risk,
            "checks": {
                "target_leg_balance_ok": target_main_lots + target_next_lots == 0,
                "target_within_risk_capacity": abs(target_spread_units) <= risk_capacity_units,
                "target_margin_within_budget": float(target_risk["margin_usage_usd"])
                <= float(capacity["capital_deployment_budget_usd"]) + 1e-9,
                "expiry_roll_mismatch": bool(capacity["expiry_roll_mismatch"]),
            },
        },
        "pairedExecutionMandate": execution_mandate,
        "thesisInvalidation": {
            "schemaVersion": "asset-simulation-oil-calendar-spread-thesis-decision-v1",
            "policy": dict(config["thesis_invalidation"]),
            "stateBefore": resolved_thesis_state,
            "action": thesis_action,
        },
        "informationPolicy": {
            "visible_market_at_current_cutoff_only": True,
            "published_forecast_vintage_only": True,
            "configured_forecast_capability_score_used": False,
            "hidden_future_available": False,
            "future_market_payload_available": False,
            "market_write_back": False,
            "maturity_recomputed_by_strategy": False,
        },
        "scope": {
            "included": ["current_main", "next_main", "one_to_one_lot_ratio"],
            "excluded": list(config["scope_exclusions"]),
        },
    }
    rounded_result = _round_nested(result)
    identity = {
        "model_version": OIL_CALENDAR_SPREAD_STRATEGY_MODEL_VERSION,
        "config_id": str(config["config_id"]),
        "config_hash": assets["oil_calendar_spread_strategy_config_hash"],
        "field_contract_id": str(contract["contract_id"]),
        "field_contract_hash": assets["oil_calendar_spread_strategy_contract_hash"],
        "strategy_profile_hash": strategy_profile["profile_hash"],
        "upstream_global_identity_hash": str(
            market["identity"]["upstream_global_identity_hash"]
        ),
        "forecast_vintage_id": str(
            forecast_vintage.get("identity", {}).get("vintage_id", "")
        ),
        "write_back": False,
        "result_hash": sha256_json(rounded_result),
    }
    identity["identity_hash"] = sha256_json(identity)
    return _round_nested({"identity": identity, **rounded_result})


def _signed_pair_fill(
    requested_pair_delta_units: int,
    executed_main_delta_lots: int,
    executed_next_main_delta_lots: int,
) -> int:
    requested = int(requested_pair_delta_units)
    main = int(executed_main_delta_lots)
    next_main = int(executed_next_main_delta_lots)
    if requested > 0:
        return min(max(0, main), max(0, -next_main))
    if requested < 0:
        return -min(max(0, -main), max(0, next_main))
    return 0



def build_oil_calendar_spread_execution_report(
    mandate: Mapping[str, Any],
    *,
    executed_main_delta_lots: int,
    executed_next_main_delta_lots: int,
) -> dict[str, Any]:
    """Record independently filled legs and reject any market-limit violation."""

    requested_delta = _integer_lots(
        mandate["requested_pair_delta_units"], "requested pair delta"
    )
    requested_main = _integer_lots(
        mandate["requested_main_delta_lots"], "requested main delta"
    )
    requested_next = _integer_lots(
        mandate["requested_next_main_delta_lots"], "requested next-main delta"
    )
    executed_main = _integer_lots(executed_main_delta_lots, "executed main delta")
    executed_next = _integer_lots(
        executed_next_main_delta_lots, "executed next-main delta"
    )
    if requested_main != requested_delta or requested_next != -requested_delta:
        raise ValueError("oil calendar spread execution mandate is not a one-to-one pair")

    required_limit_fields = (
        "main_turn_liquidity_lots",
        "next_main_turn_liquidity_lots",
    )
    missing_limit_fields = [
        name for name in required_limit_fields if name not in mandate
    ]
    if missing_limit_fields:
        raise ValueError(
            "oil calendar spread execution mandate is missing required market "
            f"limits: {', '.join(missing_limit_fields)}"
        )
    main_turn_limit = _nonnegative_integer(
        mandate["main_turn_liquidity_lots"], "main turn liquidity lots"
    )
    next_turn_limit = _nonnegative_integer(
        mandate["next_main_turn_liquidity_lots"],
        "next-main turn liquidity lots",
    )
    raw_remediation = mandate.get("imbalanceRemediation", {})
    if not isinstance(raw_remediation, Mapping):
        raise TypeError("oil calendar spread imbalance remediation must be an object")
    requested_remediation_main = _integer_lots(
        raw_remediation.get("requested_main_delta_lots", 0),
        "requested remediation main delta",
    )
    requested_remediation_next = _integer_lots(
        raw_remediation.get("requested_next_main_delta_lots", 0),
        "requested remediation next-main delta",
    )
    combined_main_turnover = abs(requested_main) + abs(requested_remediation_main)
    combined_next_turnover = abs(requested_next) + abs(requested_remediation_next)
    if combined_main_turnover > main_turn_limit:
        raise ValueError(
            "main pair plus remediation request exceeds the half-turn market limit"
        )
    if combined_next_turnover > next_turn_limit:
        raise ValueError(
            "next-main pair plus remediation request exceeds the half-turn market limit"
        )
    for requested, executed, limit, name in (
        (requested_main, executed_main, main_turn_limit, "main"),
        (requested_next, executed_next, next_turn_limit, "next-main"),
    ):
        if requested == 0 and executed != 0:
            raise ValueError(f"{name} execution exists without a request")
        if requested != 0 and executed * requested < 0:
            raise ValueError(f"{name} execution has the wrong direction")
        if abs(executed) > abs(requested):
            raise ValueError(f"{name} execution exceeds the requested lots")
        if abs(executed) > limit:
            raise ValueError(f"{name} execution exceeds the half-turn market limit")

    executed_pair_units = _signed_pair_fill(
        requested_delta, executed_main, executed_next
    )
    unpaired_main = executed_main - executed_pair_units
    unpaired_next = executed_next + executed_pair_units
    signed_imbalance = executed_main + executed_next
    absolute_imbalance = abs(unpaired_main) + abs(unpaired_next)
    requested_pair_lots = abs(requested_delta)
    completion_ratio = (
        1.0
        if requested_pair_lots == 0
        else abs(executed_pair_units) / requested_pair_lots
    )
    tolerance = int(mandate.get("temporary_leg_imbalance_tolerance_lots", 0))
    if absolute_imbalance > tolerance:
        status = "temporary_imbalance_breach"
    elif absolute_imbalance > 0:
        status = "temporarily_legged"
    elif completion_ratio >= 1.0:
        status = "balanced_complete"
    else:
        status = "balanced_partial"

    if unpaired_main != 0:
        missing_leg_order = {
            "leg": "next_main",
            "delta_lots": -unpaired_main,
        }
        fallback_unwind = {"leg": "main", "delta_lots": -unpaired_main}
    elif unpaired_next != 0:
        missing_leg_order = {
            "leg": "main",
            "delta_lots": -unpaired_next,
        }
        fallback_unwind = {"leg": "next_main", "delta_lots": -unpaired_next}
    else:
        missing_leg_order = {"leg": None, "delta_lots": 0}
        fallback_unwind = {"leg": None, "delta_lots": 0}

    result = {
        "schemaVersion": "asset-simulation-oil-calendar-spread-execution-report-v1",
        "requested_pair_lots": requested_pair_lots,
        "requested_pair_delta_units": requested_delta,
        "requested_main_delta_lots": requested_main,
        "requested_next_main_delta_lots": requested_next,
        "main_turn_liquidity_lots": main_turn_limit,
        "next_main_turn_liquidity_lots": next_turn_limit,
        "requested_remediation_main_delta_lots": requested_remediation_main,
        "requested_remediation_next_main_delta_lots": requested_remediation_next,
        "combined_requested_main_turnover_lots": combined_main_turnover,
        "combined_requested_next_main_turnover_lots": combined_next_turnover,
        "executed_main_delta_lots": executed_main,
        "executed_next_main_delta_lots": executed_next,
        "executed_pair_units": executed_pair_units,
        "executed_pair_lots": abs(executed_pair_units),
        "pair_completion_ratio": completion_ratio,
        "unpaired_main_lots": unpaired_main,
        "unpaired_next_main_lots": unpaired_next,
        "leg_imbalance_lots": signed_imbalance,
        "signed_leg_imbalance_lots": signed_imbalance,
        "absolute_leg_imbalance_lots": absolute_imbalance,
        "temporary_leg_imbalance_tolerance_lots": tolerance,
        "status": status,
        "remediation": {
            "required": absolute_imbalance > 0,
            "preferred_complete_missing_leg": missing_leg_order,
            "fallback_unwind_excess_leg": fallback_unwind,
        },
        "governance": {
            "paired_mandate_preserved": True,
            "leg_fills_recorded_separately": True,
            "imbalance_reported_not_netted_away": True,
            "hard_leg_turn_limits_enforced": True,
            "remediation_reservation_validated_against_leg_turn_limits": True,
        },
    }
    rounded = _round_nested(result)
    return {**rounded, "report_hash": sha256_json(rounded)}


def attribute_oil_calendar_spread_pnl(
    *,
    starting_main_lots: int,
    starting_next_main_lots: int,
    main_start_price_usd: float,
    main_end_price_usd: float,
    next_main_start_price_usd: float,
    next_main_end_price_usd: float,
    carry_reference_main_end_price_usd: float | None = None,
    carry_reference_next_main_end_price_usd: float | None = None,
    spread_execution_cost_usd: float = 0.0,
    slippage_usd: float = 0.0,
    fees_usd: float = 0.0,
) -> dict[str, Any]:
    """Separate spread PnL, optional owner-derived carry, and residual direction.

    Carry is never accepted as a free PnL number.  It is computed only when the
    caller supplies a pair of end-of-window *carry counterfactual prices* from a
    market/carry owner.  Without those prices v1 reports carry as unavailable
    and attributes the full balanced spread PnL to non-carry curve movement.
    """

    _, config, _ = _validate_registered_assets()
    main_lots = _integer_lots(starting_main_lots, "starting main lots")
    next_lots = _integer_lots(starting_next_main_lots, "starting next-main lots")
    main_start = _finite_number(main_start_price_usd, "main start price")
    main_end = _finite_number(main_end_price_usd, "main end price")
    next_start = _finite_number(next_main_start_price_usd, "next-main start price")
    next_end = _finite_number(next_main_end_price_usd, "next-main end price")
    if min(main_start, main_end, next_start, next_end) <= 0.0:
        raise ValueError("oil calendar spread PnL prices must be positive")

    carry_refs_supplied = (
        carry_reference_main_end_price_usd is not None,
        carry_reference_next_main_end_price_usd is not None,
    )
    if carry_refs_supplied[0] != carry_refs_supplied[1]:
        raise ValueError(
            "oil calendar spread carry attribution requires both leg counterfactual prices"
        )
    carry_main_end: float | None = None
    carry_next_end: float | None = None
    if all(carry_refs_supplied):
        carry_main_end = _finite_number(
            carry_reference_main_end_price_usd, "main carry reference end price"
        )
        carry_next_end = _finite_number(
            carry_reference_next_main_end_price_usd,
            "next-main carry reference end price",
        )
        if min(carry_main_end, carry_next_end) <= 0.0:
            raise ValueError("carry reference prices must be positive")

    costs = {
        "spread_execution_cost_usd": _finite_number(
            spread_execution_cost_usd, "spread execution cost"
        ),
        "slippage_usd": _finite_number(slippage_usd, "slippage"),
        "fees_usd": _finite_number(fees_usd, "fees"),
    }
    if any(value < 0.0 for value in costs.values()):
        raise ValueError("oil calendar spread execution costs cannot be negative")

    contract_size = float(config["pnl_attribution"]["contract_size_bbl"])
    position = _extract_spread_position(main_lots, next_lots)
    spread_units = int(position["spread_units"])
    residual_main = int(position["residual_main_lots"])
    residual_next = int(position["residual_next_main_lots"])
    main_change = main_end - main_start
    next_change = next_end - next_start
    calendar_spread_pnl = (
        spread_units * (main_change - next_change) * contract_size
    )
    residual_directional_pnl = (
        residual_main * main_change + residual_next * next_change
    ) * contract_size
    gross_leg_pnl = (
        main_lots * main_change + next_lots * next_change
    ) * contract_size
    if not math.isclose(
        gross_leg_pnl,
        calendar_spread_pnl + residual_directional_pnl,
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise ValueError("oil calendar spread PnL decomposition identity failed")

    if carry_main_end is None or carry_next_end is None:
        carry = 0.0
        carry_status = "not_separately_available"
        carry_source = None
    else:
        carry = spread_units * (
            (carry_main_end - main_start) - (carry_next_end - next_start)
        ) * contract_size
        carry_status = "computed_from_counterfactual_leg_prices"
        carry_source = "caller_supplied_market_or_carry_owner_counterfactual_prices"
    forecast_curve_move_pnl = calendar_spread_pnl - carry

    total_cost = sum(costs.values())
    net_pnl = gross_leg_pnl - total_cost
    directional_share_denominator = (
        abs(calendar_spread_pnl) + abs(residual_directional_pnl)
    )
    residual_directional_share = (
        0.0
        if directional_share_denominator <= 1e-12
        else abs(residual_directional_pnl) / directional_share_denominator
    )
    maximum_directional_share = float(
        config["risk_adapter"]["maximum_residual_directional_pnl_share"]
    )
    result = {
        "schemaVersion": "asset-simulation-oil-calendar-spread-pnl-attribution-v1",
        "starting_positions": {
            "main_lots": main_lots,
            "next_main_lots": next_lots,
            **position,
        },
        "price_changes": {
            "main_change_usd_per_bbl": main_change,
            "next_main_change_usd_per_bbl": next_change,
            "spread_change_usd_per_bbl": main_change - next_change,
        },
        "carryAttribution": {
            "status": carry_status,
            "source": carry_source,
            "main_counterfactual_end_price_usd": carry_main_end,
            "next_main_counterfactual_end_price_usd": carry_next_end,
            "free_form_carry_pnl_input_allowed": False,
        },
        "calendar_spread_pnl_usd": calendar_spread_pnl,
        "forecast_curve_move_pnl_usd": forecast_curve_move_pnl,
        "convergence_carry_pnl_usd": carry,
        "residual_directional_pnl_usd": residual_directional_pnl,
        "gross_leg_pnl_before_cost_usd": gross_leg_pnl,
        **costs,
        "total_execution_cost_usd": total_cost,
        "net_pnl_usd": net_pnl,
        "residual_directional_pnl_share": residual_directional_share,
        "curve_alpha_integrity": {
            "maximum_residual_directional_pnl_share": maximum_directional_share,
            "passed": residual_directional_share
            <= maximum_directional_share + 1e-12,
        },
        "identities": {
            "calendar_spread_equals_curve_move_plus_carry": math.isclose(
                calendar_spread_pnl,
                forecast_curve_move_pnl + carry,
                rel_tol=0.0,
                abs_tol=1e-6,
            ),
            "gross_leg_equals_spread_plus_residual": True,
            "net_equals_gross_less_cost": math.isclose(
                net_pnl,
                gross_leg_pnl - total_cost,
                rel_tol=0.0,
                abs_tol=1e-6,
            ),
        },
    }
    rounded = _round_nested(result)
    return {**rounded, "attribution_hash": sha256_json(rounded)}

def _direction(value: float, threshold: float) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0



def evaluate_oil_calendar_spread_thesis_state(
    decision: Mapping[str, Any],
    *,
    realized_main_price_usd: float,
    realized_next_main_price_usd: float,
    realized_week_serial: int,
    evaluation_horizon_weeks: int = 2,
) -> dict[str, Any]:
    """Evaluate exactly one matured forecast horizon, never the blended signal.

    A half-month settlement is a two-week checkpoint, so the default evaluates
    only the 2-week component.  The caller must identify the realized market
    week, which must exactly match the frozen component target.  The 4-week
    component may influence the decision signal but cannot be scored against a
    2-week realization.
    """

    _, config, _ = _validate_registered_assets()
    if (
        isinstance(evaluation_horizon_weeks, bool)
        or not isinstance(evaluation_horizon_weeks, int)
        or evaluation_horizon_weeks <= 0
    ):
        raise ValueError("evaluation horizon weeks must be a positive integer")
    thesis_config = config["thesis_invalidation"]
    thesis = dict(decision["thesisInvalidation"])
    before = resolve_oil_calendar_spread_thesis_state(thesis.get("stateBefore"))
    signal = dict(decision["signal"])
    components = [
        dict(item) for item in signal.get("horizon_components", ())
    ]
    component = next(
        (
            item
            for item in components
            if int(item["requested_horizon_weeks"])
            == int(evaluation_horizon_weeks)
        ),
        None,
    )
    if component is None:
        raise ValueError(
            "oil calendar spread thesis has no forecast for the requested maturity"
        )
    for role in ("main", "next_main"):
        if int(component[f"{role}_selected_horizon_weeks"]) != int(
            evaluation_horizon_weeks
        ):
            raise ValueError(
                "oil calendar spread thesis component does not contain an exact "
                "forecast horizon"
            )
    main_target_week = str(component.get("main_target_week", "")).strip()
    next_target_week = str(component.get("next_main_target_week", "")).strip()
    if not main_target_week or main_target_week != next_target_week:
        raise ValueError(
            "oil calendar spread thesis legs do not share one target week"
        )
    target_week_serial = _nonnegative_integer(
        component.get("target_week_serial"), "forecast target week serial"
    )
    realized_serial = _nonnegative_integer(
        realized_week_serial, "realized week serial"
    )
    if realized_serial != target_week_serial:
        raise ValueError(
            "oil calendar spread thesis can only be evaluated at its exact "
            "forecast target week"
        )

    current_spread_usd = float(signal["current_spread_usd_per_bbl"])
    current_reference = float(signal["normalization_reference_price_usd"])
    realized_spread_usd = _dollar_spread(
        realized_main_price_usd, realized_next_main_price_usd
    )
    realized_change_usd = realized_spread_usd - current_spread_usd
    predicted_change_usd = float(
        component["forecast_spread_change_usd_per_bbl"]
    )
    component_uncertainty_usd = max(
        float(config["signal"]["minimum_normalized_scale"]) * current_reference,
        float(component["pair_uncertainty_usd_per_bbl"]),
    )
    forecast_error_z = (
        abs(realized_change_usd - predicted_change_usd)
        / component_uncertainty_usd
    )
    direction_threshold_usd = (
        float(thesis_config["minimum_direction_move_normalized"])
        * current_reference
    )
    predicted_direction = _direction(
        predicted_change_usd, direction_threshold_usd
    )
    realized_direction = _direction(
        realized_change_usd, direction_threshold_usd
    )
    direction_miss = (
        predicted_direction != 0
        and realized_direction != 0
        and predicted_direction != realized_direction
    )
    severe_error = forecast_error_z >= float(
        thesis_config["severe_forecast_error_z"]
    )
    failures = int(before["consecutive_direction_misses"])
    if direction_miss or severe_error:
        failures += 1
    else:
        failures = max(0, failures - 1)
    previous_status = str(before["status"])
    failure_limit = int(
        thesis_config["consecutive_failure_turns_to_invalidate"]
    )
    failed = direction_miss or severe_error
    if severe_error or failures >= failure_limit:
        status = "invalidated"
    elif failed or previous_status == "invalidated" or failures > 0:
        status = "watch"
    else:
        status = "active"
    recovery_turns = (
        0 if failed else int(before["recovery_turns"]) + 1
    )
    pending_horizons = sorted(
        int(item["requested_horizon_weeks"])
        for item in components
        if int(item["requested_horizon_weeks"])
        > int(evaluation_horizon_weeks)
    )
    realized_normalized = realized_spread_usd / current_reference
    evaluation = {
        "evaluated_horizon_weeks": int(evaluation_horizon_weeks),
        "evaluated_main_target_week": str(component["main_target_week"]),
        "evaluated_next_main_target_week": str(
            component["next_main_target_week"]
        ),
        "evaluated_target_week_serial": target_week_serial,
        "realized_week_serial": realized_serial,
        "unmatured_horizons_weeks": pending_horizons,
        "current_spread_usd_per_bbl": current_spread_usd,
        "forecast_spread_change_usd_per_bbl": predicted_change_usd,
        "realized_spread_usd_per_bbl": realized_spread_usd,
        "realized_spread_change_usd_per_bbl": realized_change_usd,
        "current_normalized_spread": float(
            signal["current_normalized_spread"]
        ),
        "forecast_spread_change_normalized": (
            predicted_change_usd / current_reference
        ),
        "realized_normalized_spread": realized_normalized,
        "realized_spread_change_normalized": (
            realized_change_usd / current_reference
        ),
        "forecast_error_z": forecast_error_z,
        "forecast_error_normalization_usd_per_bbl": (
            component_uncertainty_usd
        ),
        "severe_forecast_error": severe_error,
        "predicted_direction": predicted_direction,
        "realized_direction": realized_direction,
        "direction_miss": direction_miss,
        "status_before": previous_status,
        "status_after": status,
        "blended_2w_4w_signal_scored_as_one_forecast": False,
    }
    state = {
        "schemaVersion": "asset-simulation-oil-calendar-spread-thesis-state-v1",
        "status": status,
        "consecutive_direction_misses": failures,
        "recovery_turns": recovery_turns,
        "last_signal": float(signal["signal"]),
        "last_evaluation": evaluation,
    }
    result = {
        "state": state,
        "evaluation": evaluation,
        "state_hash": sha256_json(state),
        "informationPolicy": {
            "frozen_prior_forecast_only": True,
            "only_exact_matured_horizon_scored": True,
            "realized_week_matches_frozen_target_week": True,
            "newly_realized_end_prices_only": True,
            "configured_forecast_capability_score_used": False,
            "hidden_future_used": False,
        },
    }
    return _round_nested(result)
