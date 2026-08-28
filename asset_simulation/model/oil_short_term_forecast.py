"""Synthetic short-horizon oil research for the current two main contracts.

The market owner remains :mod:`oil_futures_overlay`.  This module is a
separate information layer: it uses the already generated hidden path only to
manufacture fallible institution forecasts, never to settle a price.  A
forecast vintage is immutable, covers weekly OHLC after one half-month cutoff,
and can be scored only after target weeks have become observable.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from .engine import GlobalMacroRun
from .math_utils import clamp
from .oil_forecast_research_profile import (
    FORECAST_RESEARCH_STYLE_DIMENSIONS,
    generate_forecast_research_profile,
    research_behavior,
    validate_research_style,
)
from .oil_futures_overlay import oil_futures_payload
from .oil_futures_world import get_oil_futures_world
from .random_stream import normal
from .registry import load_registered_assets, sha256_json


OIL_SHORT_TERM_FORECAST_MODEL_VERSION = (
    "asset-simulation-oil-short-term-forecast-v0.2.0"
)
RADAR_DIMENSIONS = (
    "direction",
    "path",
    "turning_points",
    "range",
    "term_structure",
    "revision",
)
RESEARCH_STYLE_DIMENSIONS = FORECAST_RESEARCH_STYLE_DIMENSIONS
ROLE_ORDER = ("main", "next_main")


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("oil short-term forecast contains a non-finite value")
        return round(value, 8)
    return value


def _month_serial(year: int, month: int) -> int:
    return int(year) * 12 + int(month) - 1


def _date_from_month_serial(serial: int) -> tuple[int, int]:
    return int(serial) // 12, int(serial) % 12 + 1


def _half_turn_serial(year: int, month: int, half: int) -> int:
    return _month_serial(year, month) * 2 + int(half) - 1


def _week_serial(year: int, month: int, week: int) -> int:
    return _month_serial(year, month) * 4 + int(week) - 1


def _cutoff_week_serial(year: int, month: int, half: int) -> int:
    return _week_serial(year, month, 2 if int(half) == 1 else 4)


def _week_label(year: int, month: int, week: int) -> str:
    return f"{int(year):04d}-{int(month):02d}-W{int(week)}"


def _validate_registered_assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_short_term_forecast_config"]
    contract = assets["oil_short_term_forecast_contract"]
    if config["model_version"] != OIL_SHORT_TERM_FORECAST_MODEL_VERSION:
        raise ValueError("registered oil short-term forecast config version mismatch")
    if contract["contract_id"] != "oil_short_term_forecast_v2":
        raise ValueError("registered oil short-term forecast contract id mismatch")
    if tuple(config["capability_dimensions"]) != RADAR_DIMENSIONS:
        raise ValueError("oil short-term capability dimensions are out of contract order")
    if tuple(config["research_style_dimensions"]) != RESEARCH_STYLE_DIMENSIONS:
        raise ValueError("oil forecast research style dimensions are out of contract order")
    weights = config["scoring"]["dimension_weights"]
    if set(weights) != set(RADAR_DIMENSIONS) or not math.isclose(
        sum(float(value) for value in weights.values()), 100.0
    ):
        raise ValueError("oil short-term score weights must cover the radar and sum to 100")
    return assets, config, contract


def build_institution_profile(
    *,
    institution_id: str | None = None,
    display_name: str | None = None,
    capability_radar: Mapping[str, float] | None = None,
    research_style: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Resolve one hidden forecast-research profile.

    Capability dimensions are skills. Research-style dimensions are preferences
    with no total score and no universally superior endpoint.
    """

    _, config, _ = _validate_registered_assets()
    default = config["default_institution"]
    radar_source = (
        default["capability_radar"]
        if capability_radar is None
        else capability_radar
    )
    radar = {
        key: float(value)
        for key, value in dict(radar_source).items()
    }
    unknown_capabilities = set(radar) - set(RADAR_DIMENSIONS)
    if unknown_capabilities:
        raise KeyError(
            f"unknown oil forecast capability dimensions: {sorted(unknown_capabilities)}"
        )
    for key in RADAR_DIMENSIONS:
        if (
            key not in radar
            or not math.isfinite(radar[key])
            or not 0.0 <= radar[key] <= 100.0
        ):
            raise ValueError(
                f"oil forecast capability {key} must be between 0 and 100"
            )
    style = validate_research_style(
        research_style,
        default_style=default.get("research_style"),
    )
    weights = config["scoring"]["dimension_weights"]
    total_score = (
        sum(float(weights[key]) * radar[key] for key in RADAR_DIMENSIONS)
        / 100.0
    )

    profile = {
        "institution_id": str(institution_id or default["institution_id"]),
        "display_name": str(display_name or default["display_name"]),
        "capability_radar": radar,
        "capability_total_score": total_score,
        "research_style": style,
    }
    if not profile["institution_id"]:
        raise ValueError("oil forecast institution_id must not be empty")
    profile["profile_hash"] = sha256_json(profile)
    return _round_nested(profile)


def resolve_oil_short_term_institution_profile(
    institution_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Canonicalize a supplied/default hidden research profile."""

    if institution_profile is None:
        return build_institution_profile()
    supplied = dict(institution_profile)
    return build_institution_profile(
        institution_id=str(supplied.get("institution_id", "")) or None,
        display_name=str(supplied.get("display_name", "")) or None,
        capability_radar=supplied.get("capability_radar"),
        research_style=supplied.get("research_style"),
    )


def generate_institution_profile_for_score_range(
    *,
    seed: int,
    score_min: float,
    score_max: float,
) -> dict[str, Any]:
    """Generate one stable specialized forecast-research profile.

    The score range remains only as a broad compatibility constraint for the
    current synthetic-demo API. Specialization and research style are generated
    by independent latent professional traits.
    """

    _, config, _ = _validate_registered_assets()
    generated = generate_forecast_research_profile(
        seed=int(seed),
        score_min=float(score_min),
        score_max=float(score_max),
        capability_dimensions=RADAR_DIMENSIONS,
        weights=config["scoring"]["dimension_weights"],
        profile_generation=config["profile_generation"],
        style_generation=config["research_style_generation"],
    )
    lower = float(score_min)
    upper = float(score_max)
    profile = build_institution_profile(
        institution_id=(
            f"generated_research_{int(seed)}_{int(round(lower * 10))}_"
            f"{int(round(upper * 10))}"
        ),
        display_name=f"合成研究机构 {abs(int(seed)) % 1_000_000:06d}",
        capability_radar=generated["capability_radar"],
        research_style=generated["research_style"],
    )
    total_score = float(profile["capability_total_score"])
    if total_score < lower - 0.01 or total_score > upper + 0.01:
        raise ValueError(
            "generated oil forecast profile fell outside the requested range"
        )
    return profile


def _flatten_contract_weeks(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
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
                    "target_week": _week_label(year, month_number, week_number),
                    "open": float(week["open"]),
                    "high": float(week["high"]),
                    "low": float(week["low"]),
                    "close": float(week["close"]),
                }
            )
    return weeks


def _contract_history_through(
    global_run: GlobalMacroRun,
    *,
    contract_id: str,
    expiry_year: int,
    expiry_month: int,
    cutoff_year: int,
    cutoff_month: int,
    cutoff_half: int,
) -> list[dict[str, Any]]:
    """Query indexed named-contract history without rebuilding a market payload."""

    max_year = max(int(row["year"]) for row in global_run.rows)
    requested = _half_turn_serial(cutoff_year, cutoff_month, cutoff_half)
    expiry = _half_turn_serial(expiry_year, expiry_month, 2)
    run_end = _half_turn_serial(max_year, 12, 2)
    target = min(requested, expiry, run_end)
    target_month_serial, half_index = divmod(target, 2)
    target_year, target_month = _date_from_month_serial(target_month_serial)
    monthly = get_oil_futures_world(global_run).contract_monthly_history(
        contract_id=str(contract_id),
        as_of_year=target_year,
        as_of_month=target_month,
        as_of_half=half_index + 1,
    )
    if not monthly:
        raise ValueError(f"oil forecast target contract is unavailable: {contract_id}")
    return _flatten_contract_weeks({"monthly": monthly})


def _future_truth(
    global_run: GlobalMacroRun,
    *,
    contract: Mapping[str, Any],
    as_of_year: int,
    as_of_month: int,
    as_of_half: int,
) -> list[dict[str, Any]]:
    history = _contract_history_through(
        global_run,
        contract_id=str(contract["contract_id"]),
        expiry_year=int(contract["expiry_year"]),
        expiry_month=int(contract["expiry_month"]),
        cutoff_year=int(contract["expiry_year"]),
        cutoff_month=int(contract["expiry_month"]),
        cutoff_half=2,
    )
    cutoff = _cutoff_week_serial(as_of_year, as_of_month, as_of_half)
    return [item for item in history if int(item["week_serial"]) > cutoff]


def _selected_contracts(market: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    contracts = [dict(item) for item in market["curve"]["contracts"]]
    main_id = str(market["curve"]["main_contract_id"])
    main_index = next(
        index for index, item in enumerate(contracts) if item["contract_id"] == main_id
    )
    if main_index + 1 >= len(contracts):
        raise ValueError("oil short-term forecast requires a next-main contract")
    return [("main", contracts[main_index]), ("next_main", contracts[main_index + 1])]


def _recent_weekly_log_trend(contract: Mapping[str, Any]) -> float:
    closes = [float(item["close"]) for item in _flatten_contract_weeks(contract)]
    closes = closes[-7:]
    if len(closes) < 2:
        return 0.0
    returns = [math.log(current / previous) for previous, current in zip(closes, closes[1:])]
    return sum(returns) / len(returns)


def _recent_weekly_range_log(contract: Mapping[str, Any]) -> float:
    weeks = _flatten_contract_weeks(contract)[-8:]
    ranges = [
        math.log(float(item["high"]) / float(item["low"]))
        for item in weeks
        if float(item["high"]) > 0.0 and float(item["low"]) > 0.0
    ]
    return 0.02 if not ranges else sum(ranges) / len(ranges)


def _skill_truth_mix(score: float) -> float:
    """Gate hidden-path shape behind capability instead of using it as the base.

    Smoothstep keeps the endpoints exact while making very weak institutions
    depend overwhelmingly on visible history.  A score of 15 retains about 6%
    of hidden shape, 70 retains about 78%, and 100 retains all of it.
    """

    skill = clamp(float(score) / 100.0, 0.0, 1.0)
    return skill * skill * (3.0 - 2.0 * skill)


def _previous_prediction_map(
    previous_vintage: Mapping[str, Any] | None,
) -> dict[tuple[str, int], Mapping[str, Any]]:
    if previous_vintage is None:
        return {}
    return {
        (str(forecast["contract_id"]), int(bar["week_serial"])): bar
        for forecast in previous_vintage.get("forecasts", ())
        for bar in forecast.get("weekly", ())
    }


def _realized_surprise(
    global_run: GlobalMacroRun,
    *,
    previous_vintage: Mapping[str, Any] | None,
    as_of_year: int,
    as_of_month: int,
    as_of_half: int,
) -> float:
    if previous_vintage is None:
        return 0.0
    evaluation_cutoff = _cutoff_week_serial(as_of_year, as_of_month, as_of_half)
    errors: list[float] = []
    for forecast in previous_vintage.get("forecasts", ()):
        history = _contract_history_through(
            global_run,
            contract_id=str(forecast["contract_id"]),
            expiry_year=int(forecast["expiry_year"]),
            expiry_month=int(forecast["expiry_month"]),
            cutoff_year=as_of_year,
            cutoff_month=as_of_month,
            cutoff_half=as_of_half,
        )
        actual = {int(item["week_serial"]): item for item in history}
        for bar in forecast.get("weekly", ()):
            address = int(bar["week_serial"])
            if address <= evaluation_cutoff and address in actual:
                errors.append(abs(math.log(float(actual[address]["close"]) / float(bar["close"]))))
    return 0.0 if not errors else sum(errors) / len(errors)


def _revision_alpha(
    profile: Mapping[str, Any], surprise: float, behavior: Mapping[str, Any]
) -> float:
    skill = float(profile["capability_radar"]["revision"]) / 100.0
    base = (0.16 + 0.68 * skill) * float(behavior["revision_speed"])
    base *= 1.0 - 0.34 * float(behavior["thesis_persistence"])
    return clamp(base + min(0.20, surprise / 0.10 * 0.20), 0.08, 0.94)


def _fresh_close_path(
    *,
    seed: int,
    profile: Mapping[str, Any],
    role: str,
    contract: Mapping[str, Any],
    truth: list[Mapping[str, Any]],
    recent_weekly_trend: float,
    error_config: Mapping[str, Any],
) -> list[float]:
    if not truth:
        return []
    radar = profile["capability_radar"]
    behavior = research_behavior(profile, error_config)
    institution_id = str(profile["institution_id"])
    path_gap = 1.0 - float(radar["path"]) / 100.0
    direction_gap = 1.0 - float(radar["direction"]) / 100.0
    timing_gap = 1.0 - float(radar["turning_points"]) / 100.0
    curve_gap = 1.0 - float(radar["term_structure"]) / 100.0
    expiry_address = _month_serial(
        int(contract["expiry_year"]), int(contract["expiry_month"])
    )
    shift_draw = normal(
        seed,
        f"oil_short_forecast.{institution_id}.{contract['contract_id']}.turn_shift",
        expiry_address,
    )
    shift = int(
        round(
            timing_gap
            * float(error_config["timing_shift_max_weeks"])
            * shift_draw
            + float(behavior["timing_lead_weeks"])
        )
    )
    shift = int(
        clamp(
            shift,
            -int(error_config["timing_shift_max_weeks"]),
            int(error_config["timing_shift_max_weeks"]),
        )
    )
    style_timing_mix = (
        0.30
        * abs(float(behavior["timing_lead_weeks"]))
        / max(1.0, float(error_config["timing_shift_max_weeks"]))
    )
    timing_mix = clamp(
        timing_gap * float(error_config["timing_truth_mix_max"])
        + style_timing_mix,
        0.0,
        0.95,
    )
    direction_draw = normal(
        seed,
        f"oil_short_forecast.{institution_id}.direction",
        expiry_address,
    )
    recent_annualized = clamp(recent_weekly_trend * 52.0, -0.35, 0.35)
    max_horizon_scale = float(error_config["max_horizon_scale"])
    role_sign = -0.5 if role == "main" else 0.5
    closes: list[float] = []
    anchor_log = math.log(float(contract["price_usd"]))
    path_truth_mix = _skill_truth_mix(float(radar["path"]))
    for index, actual in enumerate(truth):
        horizon = index + 1
        shifted_index = int(clamp(index + shift, 0, len(truth) - 1))
        actual_log = math.log(float(actual["close"]))
        shifted_log = math.log(float(truth[shifted_index]["close"]))
        hidden_shape_log = actual_log + timing_mix * (shifted_log - actual_log)
        base_log = anchor_log + path_truth_mix * (hidden_shape_log - anchor_log)
        mean_reversion = float(behavior["mean_reversion"])
        base_log = anchor_log + (base_log - anchor_log) * (1.0 - 0.42 * mean_reversion)
        horizon_years = horizon / 52.0
        base_log += (
            float(behavior["directional_bias_annual_pct"])
            / 100.0
            * horizon_years
        )
        base_log += (
            float(behavior["trend_extrapolation"])
            * recent_annualized
            * horizon_years
            * 0.55
        )
        horizon_scale = min(max_horizon_scale, math.sqrt(horizon / 2.0))
        address = int(actual["week_serial"])
        common_draw = (
            0.72
            * normal(
                seed,
                f"oil_short_forecast.{institution_id}.common_month",
                address // 4,
            )
            + 0.28
            * normal(
                seed,
                f"oil_short_forecast.{institution_id}.common_week",
                address,
            )
        )
        contract_draw = normal(
            seed,
            f"oil_short_forecast.{institution_id}.{contract['contract_id']}.path",
            address,
        )
        curve_draw = normal(
            seed,
            f"oil_short_forecast.{institution_id}.pair_curve",
            address,
        )
        base_log += (
            float(error_config["path_error_log_scale"])
            * path_gap
            * horizon_scale
            * common_draw
        )
        base_log += (
            float(error_config["direction_error_annual_log_scale"])
            * direction_gap
            * direction_draw
            * horizon_years
        )
        base_log += (
            float(error_config["contract_error_log_scale"])
            * path_gap
            * horizon_scale
            * contract_draw
        )
        base_log += (
            role_sign
            * float(error_config["term_structure_error_log_scale"])
            * curve_gap
            * horizon_scale
            * curve_draw
        )
        base_log += (
            role_sign
            * 2.0
            * float(behavior["curve_bias_annual_pct"])
            / 100.0
            * horizon_years
        )
        closes.append(math.exp(base_log))
    return closes


def _forecast_contract(
    *,
    global_run: GlobalMacroRun,
    profile: Mapping[str, Any],
    role: str,
    contract: Mapping[str, Any],
    truth: list[Mapping[str, Any]],
    previous_map: Mapping[tuple[str, int], Mapping[str, Any]],
    revision_alpha: float,
    error_config: Mapping[str, Any],
) -> tuple[dict[str, Any], list[float]]:
    recent_trend = _recent_weekly_log_trend(contract)
    fresh_closes = _fresh_close_path(
        seed=global_run.seed,
        profile=profile,
        role=role,
        contract=contract,
        truth=truth,
        recent_weekly_trend=recent_trend,
        error_config=error_config,
    )
    closes: list[float] = []
    revisions: list[float] = []
    contract_id = str(contract["contract_id"])
    for actual, fresh in zip(truth, fresh_closes, strict=True):
        previous = previous_map.get((contract_id, int(actual["week_serial"])))
        if previous is None:
            closes.append(float(fresh))
            revisions.append(0.0)
            continue
        old = float(previous["close"])
        blended = math.exp(
            (1.0 - revision_alpha) * math.log(old)
            + revision_alpha * math.log(float(fresh))
        )
        closes.append(blended)
        revisions.append(100.0 * (blended / old - 1.0))

    radar = profile["capability_radar"]
    behavior = research_behavior(profile, error_config)
    range_gap = 1.0 - float(radar["range"]) / 100.0
    range_truth_mix = _skill_truth_mix(float(radar["range"]))
    visible_range_log = _recent_weekly_range_log(contract)
    institution_id = str(profile["institution_id"])
    weekly: list[dict[str, Any]] = []
    previous_close = float(contract["price_usd"])
    for index, (actual, predicted_close, revision_pct) in enumerate(
        zip(truth, closes, revisions, strict=True)
    ):
        predicted_open = previous_close
        body_high = max(predicted_open, predicted_close)
        body_low = min(predicted_open, predicted_close)
        body_log = abs(math.log(predicted_close / predicted_open))
        actual_range_log = math.log(float(actual["high"]) / float(actual["low"]))
        reference_range_log = (
            visible_range_log
            + range_truth_mix * (actual_range_log - visible_range_log)
        )
        address = int(actual["week_serial"])
        range_draw = normal(
            global_run.seed,
            f"oil_short_forecast.{institution_id}.{contract_id}.range",
            address,
        )
        predicted_range_log = reference_range_log * float(
            behavior["volatility_multiplier"]
        ) * math.exp(
            float(error_config["range_error_log_scale"])
            * range_gap
            * range_draw
        )
        predicted_range_log = clamp(
            max(body_log, predicted_range_log),
            float(error_config["minimum_weekly_range_log"]),
            float(error_config["maximum_weekly_range_log"]),
        )
        extra_log = max(0.0, predicted_range_log - body_log)
        actual_body_high = max(float(actual["open"]), float(actual["close"]))
        actual_body_low = min(float(actual["open"]), float(actual["close"]))
        actual_upper = max(0.0, math.log(float(actual["high"]) / actual_body_high))
        actual_lower = max(0.0, math.log(actual_body_low / float(actual["low"])))
        actual_extra = actual_upper + actual_lower
        actual_split = 0.5 if actual_extra <= 1e-12 else actual_upper / actual_extra
        split_draw = normal(
            global_run.seed,
            f"oil_short_forecast.{institution_id}.{contract_id}.range_split",
            address,
        )
        split = clamp(
            0.5
            + (actual_split - 0.5) * range_truth_mix
            + 0.14 * range_gap * split_draw,
            0.08,
            0.92,
        )
        predicted_high = body_high * math.exp(extra_log * split)
        predicted_low = body_low * math.exp(-extra_log * (1.0 - split))
        horizon_scale = min(
            float(error_config["max_horizon_scale"]), math.sqrt((index + 1) / 2.0)
        )
        confidence_pct = max(
            0.6,
            float(error_config["confidence_base_pct"])
            + float(error_config["confidence_horizon_scale_pct"])
            * horizon_scale
            * (0.45 + range_gap)
            + float(behavior["confidence_bias_pct"]),
        )
        target_month_serial = _month_serial(int(actual["year"]), int(actual["month"]))
        expiry_month_serial = _month_serial(
            int(contract["expiry_year"]), int(contract["expiry_month"])
        )
        weekly.append(
            {
                "target_week": str(actual["target_week"]),
                "week_serial": address,
                "year": int(actual["year"]),
                "month": int(actual["month"]),
                "week": int(actual["week"]),
                "horizon_weeks": index + 1,
                "open": predicted_open,
                "high": max(predicted_high, body_high),
                "low": min(predicted_low, body_low),
                "close": predicted_close,
                "confidence_low": predicted_close
                * math.exp(-confidence_pct / 100.0),
                "confidence_high": predicted_close
                * math.exp(confidence_pct / 100.0),
                "revision_pct": revision_pct,
                "contract_phase": "settlement"
                if target_month_serial >= expiry_month_serial
                else "tradable",
            }
        )
        previous_close = predicted_close

    forecast_through = None if not weekly else weekly[-1]["target_week"]
    packed = {
        "role": role,
        "contract_id": contract_id,
        "code": str(contract["code"]),
        "name": str(contract["name"]),
        "anchor_price_usd": float(contract["price_usd"]),
        "expiry_year": int(contract["expiry_year"]),
        "expiry_month": int(contract["expiry_month"]),
        "expiry_label": str(contract["expiry_label"]),
        "forecast_through": forecast_through,
        "remaining_week_count": len(weekly),
        "weekly": weekly,
    }
    return packed, revisions


def generate_oil_short_term_forecast(
    global_run: GlobalMacroRun,
    *,
    as_of_year: int,
    as_of_month: int,
    as_of_half: int,
    institution_profile: Mapping[str, Any] | None = None,
    previous_vintage: Mapping[str, Any] | None = None,
    market: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish one immutable half-month vintage for the two main contracts."""

    assets, config, contract = _validate_registered_assets()
    profile = resolve_oil_short_term_institution_profile(institution_profile)
    if previous_vintage is not None:
        previous_identity = previous_vintage.get("identity", {})
        if int(previous_identity.get("seed", -1)) != global_run.seed:
            raise ValueError("previous oil forecast vintage has a different seed")
        if str(previous_identity.get("institution_id", "")) != str(
            profile["institution_id"]
        ):
            raise ValueError("previous oil forecast vintage has a different institution")
        previous_as_of = previous_vintage.get("asOf", {})
        if _half_turn_serial(
            int(previous_as_of.get("year", 0)),
            int(previous_as_of.get("month", 0)),
            int(previous_as_of.get("half", 0)),
        ) >= _half_turn_serial(as_of_year, as_of_month, as_of_half):
            raise ValueError("previous oil forecast vintage must precede the new vintage")
    if market is None:
        resolved_market = oil_futures_payload(
            global_run,
            as_of_year=as_of_year,
            as_of_month=as_of_month,
            as_of_half=as_of_half,
        )
    else:
        resolved_market = market
        market_as_of = resolved_market.get("asOf", {})
        if (
            int(market_as_of.get("year", -1)),
            int(market_as_of.get("month", -1)),
            int(market_as_of.get("half", -1)),
        ) != (int(as_of_year), int(as_of_month), int(as_of_half)):
            raise ValueError("supplied oil forecast market cutoff does not match")
        if resolved_market.get("identity", {}).get(
            "upstream_global_identity_hash"
        ) != global_run.identity["identity_hash"]:
            raise ValueError("supplied oil forecast market belongs to another world")
    market = resolved_market
    selected = _selected_contracts(market)
    previous_map = _previous_prediction_map(previous_vintage)
    surprise = _realized_surprise(
        global_run,
        previous_vintage=previous_vintage,
        as_of_year=as_of_year,
        as_of_month=as_of_month,
        as_of_half=as_of_half,
    )
    revision_alpha = _revision_alpha(
        profile,
        surprise,
        research_behavior(profile, config["error_model"]),
    )
    forecasts: list[dict[str, Any]] = []
    all_revisions: list[float] = []
    for role, target in selected:
        hidden_path = _future_truth(
            global_run,
            contract=target,
            as_of_year=as_of_year,
            as_of_month=as_of_month,
            as_of_half=as_of_half,
        )
        packed, revisions = _forecast_contract(
            global_run=global_run,
            profile=profile,
            role=role,
            contract=target,
            truth=hidden_path,
            previous_map=previous_map,
            revision_alpha=revision_alpha,
            error_config=config["error_model"],
        )
        forecasts.append(packed)
        all_revisions.extend(value for value in revisions if value != 0.0)

    vintage_id = (
        f"{profile['institution_id']}:{int(as_of_year):04d}-"
        f"{int(as_of_month):02d}-H{int(as_of_half)}"
    )
    previous_vintage_id = None
    if previous_vintage is not None:
        previous_vintage_id = previous_vintage.get("identity", {}).get("vintage_id")
    revision_reason = "initial_forecast"
    if previous_vintage is not None:
        revision_reason = "surprise_update" if surprise >= 0.025 else "routine_update"
        previous_roles = {
            str(item["contract_id"]): str(item["role"])
            for item in previous_vintage.get("forecasts", ())
        }
        if any(
            item["contract_id"] in previous_roles
            and previous_roles[item["contract_id"]] != item["role"]
            for item in forecasts
        ):
            revision_reason = "main_role_transition"

    result = {
        "schemaVersion": "asset-simulation-oil-short-term-forecast-response-v1",
        "asOf": {
            "year": int(as_of_year),
            "month": int(as_of_month),
            "half": int(as_of_half),
            "label": f"{int(as_of_year):04d}-{int(as_of_month):02d}-H{int(as_of_half)}",
        },
        "institution": profile,
        "coverage": {
            "target_roles": list(ROLE_ORDER),
            "frequency": "weekly_ohlc",
            "revision_frequency": "each_half_month_turn",
            "horizon_rule": "remaining_lifecycle_through_final_settlement_or_world_end",
        },
        "revision": {
            "previous_vintage_id": previous_vintage_id,
            "reason": revision_reason,
            "realized_surprise_abs_log": surprise,
            "fresh_view_weight": revision_alpha,
            "revised_target_count": len(all_revisions),
            "mean_absolute_revision_pct": 0.0
            if not all_revisions
            else sum(abs(value) for value in all_revisions) / len(all_revisions),
        },
        "forecasts": forecasts,
        "scoreDefinition": {
            "dimension_weights": dict(config["scoring"]["dimension_weights"]),
            "role_weights": dict(config["scoring"]["role_weights"]),
            "policy": "realized_weeks_only_with_immutable_vintages",
        },
    }
    identity = {
        "schema_version": "asset-simulation-oil-short-term-forecast-identity-v1",
        "model_version": OIL_SHORT_TERM_FORECAST_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_short_term_forecast_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_short_term_forecast_contract_hash"],
        "upstream_global_identity_hash": global_run.identity["identity_hash"],
        "upstream_oil_futures_model_version": market["identity"]["model_version"],
        "seed": global_run.seed,
        "institution_id": profile["institution_id"],
        "institution_profile_hash": profile["profile_hash"],
        "vintage_id": vintage_id,
        "previous_vintage_id": previous_vintage_id,
        "information_cutoff": "current_half_month_market_for_player_projection",
        "synthetic_generation_basis": (
            "visible_history_projection_with_skill_gated_hidden_path_and_research_style"
        ),
        "future_market_bars_in_output": False,
        "write_back": False,
        "result_hash": sha256_json(result),
    }
    return _round_nested({"ok": True, "identity": identity, **result})


def _direction(value: float, threshold: float) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def _weighted_available(
    values: Mapping[str, float | None], weights: Mapping[str, float]
) -> float | None:
    available = [key for key, value in values.items() if value is not None]
    if not available:
        return None
    denominator = sum(float(weights[key]) for key in available)
    return sum(float(weights[key]) * float(values[key]) for key in available) / denominator


def _role_realized_metrics(
    forecast: Mapping[str, Any],
    actual_lookup: Mapping[int, Mapping[str, Any]],
    scoring: Mapping[str, Any],
) -> tuple[dict[str, float | None], int]:
    bars = [
        bar
        for bar in forecast.get("weekly", ())
        if int(bar["week_serial"]) in actual_lookup
    ]
    if not bars:
        return {
            "direction": None,
            "path": None,
            "turning_points": None,
            "range": None,
        }, 0
    path_errors = [
        math.log(float(bar["close"]) / float(actual_lookup[int(bar["week_serial"])]["close"]))
        for bar in bars
    ]
    path_rmse = math.sqrt(sum(value * value for value in path_errors) / len(path_errors))
    path_score = 100.0 * math.exp(
        -path_rmse / float(scoring["path_error_scale_log"])
    )

    direction_scores: list[float] = []
    previous_predicted = float(forecast["anchor_price_usd"])
    previous_actual = float(forecast["anchor_price_usd"])
    flat_threshold = float(scoring["direction_flat_threshold_log"])
    for bar in bars:
        actual = actual_lookup[int(bar["week_serial"])]
        predicted_change = math.log(float(bar["close"]) / previous_predicted)
        actual_change = math.log(float(actual["close"]) / previous_actual)
        predicted_direction = _direction(predicted_change, flat_threshold)
        actual_direction = _direction(actual_change, flat_threshold)
        if predicted_direction == actual_direction:
            direction_scores.append(100.0)
        elif predicted_direction == 0 or actual_direction == 0:
            direction_scores.append(45.0)
        else:
            direction_scores.append(0.0)
        previous_predicted = float(bar["close"])
        previous_actual = float(actual["close"])
    direction_score = sum(direction_scores) / len(direction_scores)

    range_errors: list[float] = []
    for bar in bars:
        actual = actual_lookup[int(bar["week_serial"])]
        predicted_range = max(1e-9, math.log(float(bar["high"]) / float(bar["low"])))
        actual_range = max(1e-9, math.log(float(actual["high"]) / float(actual["low"])))
        range_errors.append(abs(predicted_range / actual_range - 1.0))
    range_score = 100.0 * math.exp(
        -(sum(range_errors) / len(range_errors))
        / float(scoring["range_error_scale_ratio"])
    )

    turning_score: float | None = None
    if len(bars) >= 4:
        predicted_high = max(range(len(bars)), key=lambda i: float(bars[i]["high"]))
        actual_high = max(
            range(len(bars)),
            key=lambda i: float(actual_lookup[int(bars[i]["week_serial"])]["high"]),
        )
        predicted_low = min(range(len(bars)), key=lambda i: float(bars[i]["low"]))
        actual_low = min(
            range(len(bars)),
            key=lambda i: float(actual_lookup[int(bars[i]["week_serial"])]["low"]),
        )
        error_scale = float(scoring["turning_point_error_scale_weeks"])
        turning_score = 50.0 * (
            math.exp(-abs(predicted_high - actual_high) / error_scale)
            + math.exp(-abs(predicted_low - actual_low) / error_scale)
        )
    return {
        "direction": direction_score,
        "path": path_score,
        "turning_points": turning_score,
        "range": range_score,
    }, len(bars)


def score_oil_short_term_forecast(
    vintage: Mapping[str, Any],
    global_run: GlobalMacroRun,
    *,
    evaluation_year: int,
    evaluation_month: int,
    evaluation_half: int,
    previous_vintage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score one frozen vintage using only target weeks realized by a cutoff."""

    _, config, _ = _validate_registered_assets()
    if int(vintage["identity"]["seed"]) != global_run.seed:
        raise ValueError("oil forecast vintage and global run seed mismatch")
    issued = _half_turn_serial(
        int(vintage["asOf"]["year"]),
        int(vintage["asOf"]["month"]),
        int(vintage["asOf"]["half"]),
    )
    evaluated = _half_turn_serial(
        evaluation_year, evaluation_month, evaluation_half
    )
    if evaluated < issued:
        raise ValueError("oil forecast cannot be scored before its publication cutoff")
    evaluation_cutoff = _cutoff_week_serial(
        evaluation_year, evaluation_month, evaluation_half
    )
    scoring = config["scoring"]
    actual_by_contract: dict[str, dict[int, Mapping[str, Any]]] = {}
    per_role: dict[str, dict[str, float | None]] = {}
    realized_counts: dict[str, int] = {}
    for forecast in vintage.get("forecasts", ()):
        history = _contract_history_through(
            global_run,
            contract_id=str(forecast["contract_id"]),
            expiry_year=int(forecast["expiry_year"]),
            expiry_month=int(forecast["expiry_month"]),
            cutoff_year=evaluation_year,
            cutoff_month=evaluation_month,
            cutoff_half=evaluation_half,
        )
        actual_lookup = {
            int(item["week_serial"]): item
            for item in history
            if int(item["week_serial"]) <= evaluation_cutoff
        }
        actual_by_contract[str(forecast["contract_id"])] = actual_lookup
        metrics, count = _role_realized_metrics(forecast, actual_lookup, scoring)
        per_role[str(forecast["role"])] = metrics
        realized_counts[str(forecast["role"])] = count

    role_weights = scoring["role_weights"]
    dimension_scores: dict[str, float | None] = {}
    for dimension in ("direction", "path", "turning_points", "range"):
        dimension_scores[dimension] = _weighted_available(
            {
                role: per_role.get(role, {}).get(dimension)
                for role in ROLE_ORDER
            },
            role_weights,
        )

    forecasts_by_role = {
        str(item["role"]): item for item in vintage.get("forecasts", ())
    }
    main = forecasts_by_role.get("main")
    next_main = forecasts_by_role.get("next_main")
    spread_errors: list[float] = []
    if main is not None and next_main is not None:
        main_pred = {int(item["week_serial"]): item for item in main.get("weekly", ())}
        next_pred = {
            int(item["week_serial"]): item for item in next_main.get("weekly", ())
        }
        main_actual = actual_by_contract.get(str(main["contract_id"]), {})
        next_actual = actual_by_contract.get(str(next_main["contract_id"]), {})
        common = set(main_pred) & set(next_pred) & set(main_actual) & set(next_actual)
        for address in sorted(common):
            predicted_spread = math.log(
                float(next_pred[address]["close"]) / float(main_pred[address]["close"])
            )
            actual_spread = math.log(
                float(next_actual[address]["close"]) / float(main_actual[address]["close"])
            )
            spread_errors.append(predicted_spread - actual_spread)
    if spread_errors:
        spread_rmse = math.sqrt(
            sum(value * value for value in spread_errors) / len(spread_errors)
        )
        dimension_scores["term_structure"] = 100.0 * math.exp(
            -spread_rmse / float(scoring["term_structure_error_scale_log"])
        )
    else:
        dimension_scores["term_structure"] = None

    revision_scores: list[float] = []
    if previous_vintage is not None:
        old_predictions = _previous_prediction_map(previous_vintage)
        floor = float(scoring["revision_error_floor_log"])
        for forecast in vintage.get("forecasts", ()):
            contract_id = str(forecast["contract_id"])
            actual_lookup = actual_by_contract.get(contract_id, {})
            for bar in forecast.get("weekly", ()):
                address = int(bar["week_serial"])
                old = old_predictions.get((contract_id, address))
                actual = actual_lookup.get(address)
                if old is None or actual is None:
                    continue
                old_log = math.log(float(old["close"]))
                new_log = math.log(float(bar["close"]))
                actual_log = math.log(float(actual["close"]))
                ideal_revision = actual_log - old_log
                actual_revision = new_log - old_log
                scale = abs(ideal_revision) + floor
                accuracy = clamp(
                    1.0 - abs(actual_revision - ideal_revision) / scale, 0.0, 1.0
                )
                excess = max(0.0, abs(actual_revision) - 1.5 * scale)
                discipline = clamp(1.0 - excess / (3.0 * floor), 0.0, 1.0)
                revision_scores.append(100.0 * (0.78 * accuracy + 0.22 * discipline))
    dimension_scores["revision"] = (
        None
        if not revision_scores
        else sum(revision_scores) / len(revision_scores)
    )
    overall = _weighted_available(
        dimension_scores, scoring["dimension_weights"]
    )
    total_realized = sum(realized_counts.values())
    total_targets = sum(
        len(item.get("weekly", ())) for item in vintage.get("forecasts", ())
    )
    status = "pending"
    if total_realized:
        status = "complete" if total_realized >= total_targets else "partial"
    result = {
        "schemaVersion": "asset-simulation-oil-short-term-scorecard-v1",
        "vintage_id": str(vintage["identity"]["vintage_id"]),
        "institution_id": str(vintage["institution"]["institution_id"]),
        "evaluationAsOf": {
            "year": int(evaluation_year),
            "month": int(evaluation_month),
            "half": int(evaluation_half),
            "label": f"{int(evaluation_year):04d}-{int(evaluation_month):02d}-H{int(evaluation_half)}",
        },
        "status": status,
        "realized_week_observations": total_realized,
        "target_week_observations": total_targets,
        "realized_by_role": realized_counts,
        "dimension_scores": dimension_scores,
        "per_role_scores": per_role,
        "overall_score": overall,
        "weights": dict(scoring["dimension_weights"]),
        "role_weights": dict(scoring["role_weights"]),
        "revision_pair_count": len(revision_scores),
        "future_values_released": False,
    }
    result["scorecard_hash"] = sha256_json(result)
    return _round_nested(result)


def aggregate_oil_short_term_scorecards(
    scorecards: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate realized scorecards into the institution's measured radar."""

    cards = [card for card in scorecards if card.get("overall_score") is not None]
    institutions = {str(card["institution_id"]) for card in cards}
    if len(institutions) > 1:
        raise ValueError("oil short-term track record cannot mix institutions")
    dimension_scores: dict[str, float | None] = {}
    for dimension in RADAR_DIMENSIONS:
        values = [
            (
                float(card["dimension_scores"][dimension]),
                max(1, int(card["realized_week_observations"])),
            )
            for card in cards
            if card.get("dimension_scores", {}).get(dimension) is not None
        ]
        dimension_scores[dimension] = (
            None
            if not values
            else sum(value * weight for value, weight in values)
            / sum(weight for _, weight in values)
        )
    overall_values = [
        (
            float(card["overall_score"]),
            max(1, int(card["realized_week_observations"])),
        )
        for card in cards
    ]
    result = {
        "schemaVersion": "asset-simulation-oil-short-term-track-record-v1",
        "institution_id": None if not institutions else next(iter(institutions)),
        "vintage_count": len(cards),
        "realized_week_observations": sum(weight for _, weight in overall_values),
        "measured_radar": dimension_scores,
        "overall_score": None
        if not overall_values
        else sum(value * weight for value, weight in overall_values)
        / sum(weight for _, weight in overall_values),
    }
    result["track_record_hash"] = sha256_json(result)
    return _round_nested(result)
