"""Acceptance runner for the controlled calendar-spread PM style audit.

The reusable audit engine intentionally omits thesis evaluation so style axes can
be isolated. Production calendar-spread semantics nevertheless require an
existing spread position to exit before the opposite direction may be opened.
This runner restores that invariant in the research-book state propagation.

The hardened v0.1.2 reference engine is used here only to obtain current signal
primitives and visible risk capacity. It therefore receives a flat reference
position: the controlled research-book position is owned entirely by this audit
layer and cannot leak back into the neutral 70/30 reference target path.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from . import audit_oil_calendar_spread_style_economics as base
from .model.oil_calendar_spread_strategy import (
    _apply_spread_position_persistence,
    _paired_execution_mandate,
    _responsive_target,
    build_oil_calendar_spread_research_decision,
)
from .model.oil_calendar_spread_strategy_v2 import _dedicated_signal_mix
from .model.registry import load_registered_assets


ACCEPTANCE_VERSION = (
    "asset-simulation-oil-calendar-spread-style-economic-acceptance-v0.1.1"
)


def _controlled_decision_with_reversal_guard(
    market: Mapping[str, Any],
    forecast: Mapping[str, Any],
    *,
    current_spread_units: int,
    dedicated_radar: Mapping[str, float],
    authorized_strategy_capital_usd: float,
) -> dict[str, Any]:
    _, reference_profile, policy = base._runtime_bundle(dedicated_radar)

    # Reference owner is deliberately flat.  It supplies pair identity, forecast
    # and visible-curve components plus capacity; it does not own audit book state.
    reference = build_oil_calendar_spread_research_decision(
        market,
        forecast,
        authorized_strategy_capital_usd=float(authorized_strategy_capital_usd),
        positions={},
        strategy_research_profile=reference_profile,
        thesis_state={"status": "active", "last_signal": 0.0},
    )
    signal = _dedicated_signal_mix(reference["signal"], policy)
    capacity = dict(reference["strategyRiskAdapter"]["capacity"])
    risk_capacity = int(capacity["risk_capacity_units"])
    current = max(-risk_capacity, min(risk_capacity, int(current_spread_units)))
    ideal = int(round(float(signal["signal"]) * risk_capacity))
    persistent = _apply_spread_position_persistence(
        current_spread_units=current,
        proposed_target_units=ideal,
        capacity_units=risk_capacity,
        position_persistence=float(policy["execution"]["position_persistence"]),
    )

    reversal_exit_applied = current != 0 and persistent * current < 0
    if reversal_exit_applied:
        persistent = 0

    target = _responsive_target(
        current_units=current,
        target_units=persistent,
        adjustment_speed=float(policy["execution"]["adjustment_speed"]),
        capacity_units=risk_capacity,
    )
    current_position = {
        "spread_units": current,
        "residual_main_lots": 0,
        "residual_next_main_lots": 0,
    }
    strategy_config = load_registered_assets()["oil_calendar_spread_strategy_v2_config"]
    mandate = _paired_execution_mandate(
        current_position=current_position,
        target_spread_units=target,
        risk_capacity=capacity,
        strategy_policy=policy,
        config=strategy_config,
    )
    result = {
        "style_radar": dict(dedicated_radar),
        "policy": policy,
        "signal": signal,
        "capacity": capacity,
        "current_spread_units": current,
        "ideal_target_spread_units": ideal,
        "persistent_target_spread_units": persistent,
        "target_spread_units": target,
        "reversal_exit_applied": reversal_exit_applied,
        "reference_engine_flat_for_primitives": True,
        "paired_execution_mandate": mandate,
        "legs": {
            "main": dict(reference["legs"]["main"]),
            "next_main": dict(reference["legs"]["next_main"]),
        },
    }
    return base._round_nested(result)


def build_oil_calendar_spread_style_economic_acceptance(**kwargs: Any) -> dict[str, Any]:
    original = base._controlled_decision
    try:
        base._controlled_decision = _controlled_decision_with_reversal_guard
        report = base.build_oil_calendar_spread_style_economic_audit(**kwargs)
    finally:
        base._controlled_decision = original
    report = dict(report)
    report["acceptance_version"] = ACCEPTANCE_VERSION
    report["method"] = {
        **dict(report["method"]),
        "reversal_policy": "exit_existing_spread_before_opposite_direction",
        "reference_engine_position_state": "flat_primitives_only",
        "controlled_book_owner": "style_economic_acceptance_runner",
        "thesis_performance_feedback_included": False,
    }
    report["result_hash"] = base.sha256_json(
        {key: value for key, value in report.items() if key != "result_hash"}
    )
    return base._round_nested(report)


def _parse_seed_list(value: str) -> tuple[int, ...]:
    items = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not items:
        raise argparse.ArgumentTypeError("seed list must not be empty")
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--development-seeds",
        type=_parse_seed_list,
        default=base.DEFAULT_DEVELOPMENT_SEEDS,
    )
    parser.add_argument(
        "--validation-seeds",
        type=_parse_seed_list,
        default=base.DEFAULT_VALIDATION_SEEDS,
    )
    parser.add_argument("--years", type=int, default=1)
    parser.add_argument("--include-rows", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_oil_calendar_spread_style_economic_acceptance(
        development_seeds=args.development_seeds,
        validation_seeds=args.validation_seeds,
        horizon_years=args.years,
        include_rows=bool(args.include_rows),
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is None:
        print(text)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(args.output)
    if not bool(report["overall_hard_gate_pass"]):
        raise SystemExit("calendar-spread PM style economic acceptance failed")


if __name__ == "__main__":
    main()
