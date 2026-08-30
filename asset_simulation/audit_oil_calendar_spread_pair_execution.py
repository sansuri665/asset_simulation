"""Real-path Gate-A audit for calendar-spread pair execution v0.1.1.

The audit propagates only Strategy Book position previews from realized fills.
It does not create a Formal Account, settle cash/margin, or claim strategy PnL.
Pair identity changes reset the research execution book because a dedicated
calendar-spread lifecycle/roll scheduler is still a later owner.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .audit_oil_formal_account_calibration import _build_visible_path, _round_nested
from .model.oil_calendar_spread_pair_execution_v11 import (
    OIL_CALENDAR_SPREAD_PAIR_EXECUTION_V11_MODEL_VERSION,
    execute_oil_calendar_spread_pair_turn_v11,
)
from .model.oil_calendar_spread_strategy_v2 import (
    OIL_CALENDAR_SPREAD_STRATEGY_V2_ID,
)
from .model.oil_calendar_spread_strategy_v22 import (
    build_oil_calendar_spread_strategy_v22_decision,
)
from .model.oil_strategy_book import build_oil_strategy_book
from .model.registry import sha256_json


AUDIT_VERSION = "asset-simulation-oil-calendar-spread-pair-execution-audit-v0.1.0"
DEFAULT_SEEDS = (0, 42, 99, 197)


def _pair_ids(market: Mapping[str, Any]) -> tuple[str, str]:
    curve = dict(market["curve"])
    contracts = [dict(item) for item in curve["contracts"]]
    ids = [str(item["contract_id"]) for item in contracts]
    main_id = str(curve["main_contract_id"])
    if main_id not in ids or ids.index(main_id) + 1 >= len(ids):
        raise ValueError("pair execution audit market lacks an adjacent pair")
    return main_id, ids[ids.index(main_id) + 1]


def _book(seed: int, positions: Mapping[str, int]) -> dict[str, Any]:
    return build_oil_strategy_book(
        institution_id=f"PAIR-EXEC-AUDIT-{int(seed)}",
        strategy_id=OIL_CALENDAR_SPREAD_STRATEGY_V2_ID,
        positions=positions,
    )


def _cost_identity(report: Mapping[str, Any]) -> bool:
    costs = dict(report["costs"])
    return math.isclose(
        float(costs["execution_cost_usd"]),
        float(costs["spread_cost_usd"])
        + float(costs["slippage_cost_usd"])
        + float(costs["net_fee_usd"]),
        rel_tol=0.0,
        abs_tol=1e-5,
    )


def _run_seed(seed: int, horizon_years: int) -> dict[str, Any]:
    positions: dict[str, int] = {}
    previous_pair: tuple[str, str] | None = None
    pair_reset_count = 0
    turn_count = 0
    active_request_turns = 0
    executed_pair_turns = 0
    requested_pair_lots = 0
    executed_pair_lots = 0
    gross_leg_turnover_lots = 0
    total_execution_cost_usd = 0.0
    total_spread_cost_usd = 0.0
    total_slippage_cost_usd = 0.0
    total_net_fee_usd = 0.0
    total_execution_value_added_usd = 0.0
    invariant_violations: list[str] = []
    sample_trade: dict[str, Any] | None = None

    for turn_index, item in enumerate(_build_visible_path(int(seed), int(horizon_years))):
        start_market = item["start_market"]
        end_market = item["end_market"]
        pair = _pair_ids(start_market)
        if previous_pair is not None and pair != previous_pair:
            # No spread lifecycle scheduler yet: do not teleport the old pair into
            # the new contract identities in a Gate-A execution audit.
            positions = {}
            pair_reset_count += 1
        book = _book(seed, positions)
        decision = build_oil_calendar_spread_strategy_v22_decision(
            start_market,
            item["forecast"],
            authorized_strategy_capital_usd=10_000_000.0,
            strategy_book=book,
        )
        report = execute_oil_calendar_spread_pair_turn_v11(
            start_market,
            end_market,
            decision,
            strategy_book=book,
        )
        turn_count += 1
        request = abs(int(report["mandate"]["requested_pair_delta_units"]))
        executed = abs(int(report["completion"]["executed_pair_units"]))
        requested_pair_lots += request
        executed_pair_lots += executed
        active_request_turns += int(request > 0)
        executed_pair_turns += int(executed > 0)
        gross_leg_turnover_lots += int(report["legs"]["main"]["gross_turnover_lots"])
        gross_leg_turnover_lots += int(report["legs"]["next_main"]["gross_turnover_lots"])
        total_execution_cost_usd += float(report["costs"]["execution_cost_usd"])
        total_spread_cost_usd += float(report["costs"]["spread_cost_usd"])
        total_slippage_cost_usd += float(report["costs"]["slippage_cost_usd"])
        total_net_fee_usd += float(report["costs"]["net_fee_usd"])
        total_execution_value_added_usd += float(
            report["costs"]["execution_value_added_usd"]
        )

        if bool(report["pairExecution"]["synthetic_spread_fill_created"]):
            invariant_violations.append(f"turn {turn_index}: synthetic spread fill")
        if bool(report["informationPolicy"]["future_window_volume_used_for_schedule"]):
            invariant_violations.append(f"turn {turn_index}: future volume scheduled order")
        if not _cost_identity(report):
            invariant_violations.append(f"turn {turn_index}: cost identity")
        if executed > request:
            invariant_violations.append(f"turn {turn_index}: execution expanded request")
        if int(report["legs"]["main"]["gross_turnover_lots"]) > int(
            report["mandate"]["main_turn_liquidity_lots"]
        ):
            invariant_violations.append(f"turn {turn_index}: main hard limit")
        if int(report["legs"]["next_main"]["gross_turnover_lots"]) > int(
            report["mandate"]["next_main_turn_liquidity_lots"]
        ):
            invariant_violations.append(f"turn {turn_index}: next-main hard limit")
        for window in report["weeklyWindows"]:
            if int(window["main_pair_delta_lots"]) != -int(
                window["next_main_pair_delta_lots"]
            ):
                invariant_violations.append(
                    f"turn {turn_index}: weekly pair imbalance"
                )

        if sample_trade is None and executed > 0:
            sample_trade = {
                "seed": int(seed),
                "start": str(report["startAsOf"]["label"]),
                "end": str(report["endAsOf"]["label"]),
                "main_contract_id": str(report["legs"]["main"]["contract_id"]),
                "next_main_contract_id": str(
                    report["legs"]["next_main"]["contract_id"]
                ),
                "requested_pair_delta_units": int(
                    report["mandate"]["requested_pair_delta_units"]
                ),
                "executed_pair_units": int(
                    report["completion"]["executed_pair_units"]
                ),
                "pair_completion_ratio": float(
                    report["completion"]["pair_completion_ratio"]
                ),
                "frozen_pair_execution_weights": list(
                    report["schedule"]["pair_execution_weights"]
                ),
                "weekly_pair_units": [
                    int(window["pair_units"])
                    for window in report["weeklyWindows"]
                ],
                "neutral_pair_execution_spread_usd_per_bbl": report[
                    "pairExecution"
                ]["neutral_pair_execution_spread_usd_per_bbl"],
                "all_in_pair_execution_spread_usd_per_bbl": report[
                    "pairExecution"
                ]["all_in_pair_execution_spread_usd_per_bbl"],
                "execution_cost_usd": float(
                    report["costs"]["execution_cost_usd"]
                ),
                "spread_cost_usd": float(report["costs"]["spread_cost_usd"]),
                "slippage_cost_usd": float(
                    report["costs"]["slippage_cost_usd"]
                ),
                "net_fee_usd": float(report["costs"]["net_fee_usd"]),
                "execution_value_added_usd": float(
                    report["costs"]["execution_value_added_usd"]
                ),
                "execution_director": str(
                    report["executionDesk"]["profile"]["appointment"][
                        "display_name"
                    ]
                ),
            }

        positions = {
            str(key): int(value)
            for key, value in report["strategyBookSettlementPreview"][
                "ending_positions_preview"
            ].items()
        }
        previous_pair = pair

    completion = (
        1.0
        if requested_pair_lots == 0
        else executed_pair_lots / requested_pair_lots
    )
    return {
        "seed": int(seed),
        "turn_count": turn_count,
        "pair_identity_reset_count": pair_reset_count,
        "active_request_turns": active_request_turns,
        "executed_pair_turns": executed_pair_turns,
        "requested_pair_lots": requested_pair_lots,
        "executed_pair_lots": executed_pair_lots,
        "weighted_pair_completion_ratio": completion,
        "gross_leg_turnover_lots": gross_leg_turnover_lots,
        "execution_cost_usd": total_execution_cost_usd,
        "spread_cost_usd": total_spread_cost_usd,
        "slippage_cost_usd": total_slippage_cost_usd,
        "net_fee_usd": total_net_fee_usd,
        "execution_value_added_usd": total_execution_value_added_usd,
        "invariant_violation_count": len(invariant_violations),
        "invariant_violations": invariant_violations,
        "sample_trade": sample_trade,
    }


def build_oil_calendar_spread_pair_execution_audit(
    *,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    horizon_years: int = 1,
) -> dict[str, Any]:
    if horizon_years <= 0:
        raise ValueError("pair execution audit horizon must be positive")
    if not seeds:
        raise ValueError("pair execution audit requires at least one seed")
    rows = [_run_seed(int(seed), int(horizon_years)) for seed in seeds]
    requested = sum(int(row["requested_pair_lots"]) for row in rows)
    executed = sum(int(row["executed_pair_lots"]) for row in rows)
    violations = sum(int(row["invariant_violation_count"]) for row in rows)
    aggregate = {
        "seed_count": len(rows),
        "turn_count": sum(int(row["turn_count"]) for row in rows),
        "pair_identity_reset_count": sum(
            int(row["pair_identity_reset_count"]) for row in rows
        ),
        "active_request_turns": sum(int(row["active_request_turns"]) for row in rows),
        "executed_pair_turns": sum(int(row["executed_pair_turns"]) for row in rows),
        "requested_pair_lots": requested,
        "executed_pair_lots": executed,
        "weighted_pair_completion_ratio": (
            1.0 if requested == 0 else executed / requested
        ),
        "gross_leg_turnover_lots": sum(
            int(row["gross_leg_turnover_lots"]) for row in rows
        ),
        "execution_cost_usd": sum(float(row["execution_cost_usd"]) for row in rows),
        "spread_cost_usd": sum(float(row["spread_cost_usd"]) for row in rows),
        "slippage_cost_usd": sum(float(row["slippage_cost_usd"]) for row in rows),
        "net_fee_usd": sum(float(row["net_fee_usd"]) for row in rows),
        "execution_value_added_usd": sum(
            float(row["execution_value_added_usd"]) for row in rows
        ),
        "invariant_violation_count": violations,
    }
    result = {
        "audit_version": AUDIT_VERSION,
        "pair_execution_model_version": (
            OIL_CALENDAR_SPREAD_PAIR_EXECUTION_V11_MODEL_VERSION
        ),
        "horizon_years": int(horizon_years),
        "seeds": list(map(int, seeds)),
        "method": {
            "realized_weekly_execution": True,
            "schedule_frozen_at_decision_cutoff": True,
            "strategy_book_preview_propagated": True,
            "formal_account_settlement": False,
            "cash_margin_financing": False,
            "pair_identity_reset_without_roll_scheduler": True,
            "strategy_pnl_claimed": False,
        },
        "rows": rows,
        "aggregate": aggregate,
        "hard_gate_pass": violations == 0 and aggregate["executed_pair_turns"] > 0,
    }
    result["result_hash"] = sha256_json(result)
    return _round_nested(result)


def _parse_seed_list(value: str) -> tuple[int, ...]:
    items = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not items:
        raise argparse.ArgumentTypeError("seed list must not be empty")
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=_parse_seed_list, default=DEFAULT_SEEDS)
    parser.add_argument("--years", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_oil_calendar_spread_pair_execution_audit(
        seeds=args.seeds,
        horizon_years=args.years,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is None:
        print(text)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(args.output)
    if not bool(report["hard_gate_pass"]):
        raise SystemExit("calendar-spread pair execution audit failed")


if __name__ == "__main__":
    main()
