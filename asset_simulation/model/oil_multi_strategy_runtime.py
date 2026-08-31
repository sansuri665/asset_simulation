"""Gate B dual-strategy oil runtime with one desk and one formal account."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
from statistics import pstdev
from typing import Any, Mapping, Sequence

from .engine import GlobalMacroRun
from .oil_calendar_spread_strategy import (
    build_oil_calendar_spread_research_decision,
    evaluate_oil_calendar_spread_thesis_state,
)
from .oil_execution_desk import resolve_oil_execution_runtime_policy
from .oil_futures_account import create_oil_futures_account
from .oil_futures_overlay import oil_futures_payload
from .oil_multi_strategy_authorization import (
    create_strategy_capital_authorization_state,
)
from .oil_multi_strategy_execution import (
    CALENDAR_SPREAD_STRATEGY_ID,
    DIRECTIONAL_STRATEGY_ID,
    execute_oil_multi_strategy_parent_orders,
    settle_oil_multi_strategy_allocated_turn,
)
from .oil_multi_strategy_gate_b import (
    allocate_gate_b_strategy_orders,
    build_gate_b_market_limits_from_oil_futures_payload,
)
from .oil_short_term_forecast import generate_oil_short_term_forecast
from .oil_strategy_thesis import evaluate_oil_strategy_thesis_state
from .oil_trading_strategy import (
    _cutoff_week_serial,
    _half_turn_serial,
    _turn_from_serial,
    build_oil_strategy_decision,
)
from .registry import load_json, load_registered_assets, sha256_json


OIL_MULTI_STRATEGY_RUNTIME_MODEL_VERSION = (
    "asset-simulation-oil-multi-strategy-runtime-v0.1.0"
)
OIL_MULTI_STRATEGY_RUNTIME_CONTRACT_ID = "oil_multi_strategy_runtime_v1"
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _PACKAGE_ROOT / "config" / "oil_multi_strategy_runtime_v0.1.json"
_CONTRACT_PATH = _PACKAGE_ROOT / "contracts" / "oil_multi_strategy_runtime_v1.json"
_STRATEGY_IDS = (DIRECTIONAL_STRATEGY_ID, CALENDAR_SPREAD_STRATEGY_ID)


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("multi-strategy runtime contains a non-finite value")
        return round(value, 8)
    return value


def load_oil_multi_strategy_runtime_assets() -> dict[str, Any]:
    config = load_json(_CONFIG_PATH)
    contract = load_json(_CONTRACT_PATH)
    return {
        "oil_multi_strategy_runtime_config": config,
        "oil_multi_strategy_runtime_config_hash": sha256_json(config),
        "oil_multi_strategy_runtime_contract": contract,
        "oil_multi_strategy_runtime_contract_hash": sha256_json(contract),
    }


def _assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_oil_multi_strategy_runtime_assets()
    config = assets["oil_multi_strategy_runtime_config"]
    contract = assets["oil_multi_strategy_runtime_contract"]
    if config["model_version"] != OIL_MULTI_STRATEGY_RUNTIME_MODEL_VERSION:
        raise ValueError("multi-strategy runtime config/model version mismatch")
    if contract["contract_id"] != OIL_MULTI_STRATEGY_RUNTIME_CONTRACT_ID:
        raise ValueError("multi-strategy runtime contract id mismatch")
    if tuple(config["strategy_ids"]) != _STRATEGY_IDS:
        raise ValueError("multi-strategy runtime strategy ids are out of order")
    if int(config["execution"]["shared_desk_count"]) != 1:
        raise ValueError("Gate B runtime requires exactly one shared desk")
    if bool(config["lifecycle"]["roll_scheduler_enabled"]):
        raise ValueError("Gate B runtime cannot silently enable a Roll Scheduler")
    return assets, config, contract


def _finalize_state(state: Mapping[str, Any]) -> dict[str, Any]:
    assets, config, contract = _assets()
    payload = _round_nested({key: value for key, value in state.items() if key != "identity"})
    identity = {
        "schema_version": "asset-simulation-oil-multi-strategy-runtime-state-identity-v1",
        "model_version": OIL_MULTI_STRATEGY_RUNTIME_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_multi_strategy_runtime_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_multi_strategy_runtime_contract_hash"],
        "state_hash": sha256_json(payload),
    }
    return {**payload, "identity": identity}


def _positions(value: Mapping[str, Any] | None) -> dict[str, int]:
    raw = dict(value or {})
    if any(isinstance(item, bool) or not isinstance(item, int) for item in raw.values()):
        raise ValueError("runtime strategy positions must be integer lots")
    return {str(key): int(item) for key, item in sorted(raw.items()) if int(item)}


def _aggregate_books(books: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in books.values():
        for contract_id, lots in _positions(item.get("positions")).items():
            result[contract_id] = result.get(contract_id, 0) + lots
    return {key: value for key, value in sorted(result.items()) if value}


def _pair_ids(market: Mapping[str, Any]) -> tuple[str, str]:
    curve = dict(market.get("curve", {}))
    listed = [str(item["contract_id"]) for item in curve.get("contracts", ())]
    main_id = str(curve.get("main_contract_id", ""))
    if main_id not in listed or listed.index(main_id) + 1 >= len(listed):
        raise ValueError("multi-strategy runtime cannot resolve current Main/Next pair")
    return main_id, listed[listed.index(main_id) + 1]


def _split_position_delta(current: int, delta: int) -> tuple[int, int]:
    tentative = int(current) + int(delta)
    if delta == 0:
        return 0, 0
    if current == 0:
        return 0, int(delta)
    if tentative == 0:
        return int(delta), 0
    if current * tentative > 0:
        if abs(tentative) <= abs(current):
            return int(delta), 0
        return 0, int(delta)
    return -int(current), int(tentative)


def _append_single_leg_group(
    groups: list[dict[str, Any]],
    *,
    strategy_id: str,
    group_id: str,
    contract_id: str,
    delta_lots: int,
    priority: str,
    completion_ratio: float,
) -> None:
    delta = int(delta_lots)
    if not delta:
        return
    units = abs(delta)
    if priority == "risk_increase":
        units = math.floor(units * max(0.0, min(1.0, float(completion_ratio))))
    if units <= 0:
        return
    groups.append(
        {
            "strategy_id": strategy_id,
            "group_id": group_id,
            "priority": priority,
            "requested_units": units,
            "legs": {contract_id: 1 if delta > 0 else -1},
            "atomic": False,
        }
    )


def _append_pair_group(
    groups: list[dict[str, Any]],
    *,
    group_id: str,
    main_id: str,
    next_id: str,
    delta_units: int,
    priority: str,
    completion_ratio: float,
) -> None:
    delta = int(delta_units)
    if not delta:
        return
    units = abs(delta)
    if priority == "risk_increase":
        units = math.floor(units * max(0.0, min(1.0, float(completion_ratio))))
    if units <= 0:
        return
    direction = 1 if delta > 0 else -1
    groups.append(
        {
            "strategy_id": CALENDAR_SPREAD_STRATEGY_ID,
            "group_id": group_id,
            "priority": priority,
            "requested_units": units,
            "legs": {main_id: direction, next_id: -direction},
            "atomic": True,
        }
    )


def _flatten_groups(
    strategy_id: str, positions: Mapping[str, int]
) -> list[dict[str, Any]]:
    return [
        {
            "strategy_id": strategy_id,
            "group_id": f"{strategy_id}-disabled-flatten-{contract_id}",
            "priority": "risk_reduction",
            "requested_units": abs(int(lots)),
            "legs": {contract_id: -1 if int(lots) > 0 else 1},
            "atomic": False,
        }
        for contract_id, lots in sorted(positions.items())
        if int(lots)
    ]


def build_oil_multi_strategy_child_orders(
    *,
    strategy_books: Mapping[str, Mapping[str, Any]],
    directional_decision: Mapping[str, Any] | None,
    calendar_spread_decision: Mapping[str, Any] | None,
    shared_execution_policy: Mapping[str, Any],
    account_reduce_only: bool = False,
) -> dict[str, Any]:
    """Convert two strategy decisions into completion-adjusted Gate B groups."""

    completion_ratio = float(
        shared_execution_policy["completion_reliability"][
            "normal_order_completion_ratio"
        ]
    )
    groups: list[dict[str, Any]] = []
    directional_positions = _positions(
        strategy_books[DIRECTIONAL_STRATEGY_ID].get("positions")
    )
    spread_positions = _positions(
        strategy_books[CALENDAR_SPREAD_STRATEGY_ID].get("positions")
    )

    if directional_decision is None:
        groups.extend(_flatten_groups(DIRECTIONAL_STRATEGY_ID, directional_positions))
    else:
        adjustment_speed = float(
            directional_decision["strategy"]["turnover_profile"]["adjustment_speed"]
        )
        for target in directional_decision.get("targets", ()):
            contract_id = str(target["contract_id"])
            current = int(directional_positions.get(contract_id, 0))
            desired = int(target["target_position_lots"])
            gap = desired - current
            planned = int(round(adjustment_speed * gap))
            if gap and planned == 0:
                planned = 1 if gap > 0 else -1
            if abs(planned) > abs(gap):
                planned = gap
            reduction, increase = _split_position_delta(current, planned)
            _append_single_leg_group(
                groups,
                strategy_id=DIRECTIONAL_STRATEGY_ID,
                group_id=f"directional-{contract_id}-reduce",
                contract_id=contract_id,
                delta_lots=reduction,
                priority="risk_reduction",
                completion_ratio=1.0,
            )
            _append_single_leg_group(
                groups,
                strategy_id=DIRECTIONAL_STRATEGY_ID,
                group_id=f"directional-{contract_id}-increase",
                contract_id=contract_id,
                delta_lots=increase,
                priority="risk_increase",
                completion_ratio=completion_ratio,
            )

    if calendar_spread_decision is None:
        groups.extend(_flatten_groups(CALENDAR_SPREAD_STRATEGY_ID, spread_positions))
    else:
        main_id = str(calendar_spread_decision["legs"]["main"]["contract_id"])
        next_id = str(calendar_spread_decision["legs"]["next_main"]["contract_id"])
        current_risk = calendar_spread_decision["strategyRiskAdapter"]["current"]
        residual_main = int(current_risk["residual_main_lots"])
        residual_next = int(current_risk["residual_next_main_lots"])
        _append_single_leg_group(
            groups,
            strategy_id=CALENDAR_SPREAD_STRATEGY_ID,
            group_id=f"spread-remediate-{main_id}",
            contract_id=main_id,
            delta_lots=-residual_main,
            priority="residual_remediation",
            completion_ratio=1.0,
        )
        _append_single_leg_group(
            groups,
            strategy_id=CALENDAR_SPREAD_STRATEGY_ID,
            group_id=f"spread-remediate-{next_id}",
            contract_id=next_id,
            delta_lots=-residual_next,
            priority="residual_remediation",
            completion_ratio=1.0,
        )
        current_units = int(calendar_spread_decision["target"]["current_spread_units"])
        target_units = int(calendar_spread_decision["target"]["target_spread_units"])
        reduction, increase = _split_position_delta(
            current_units, target_units - current_units
        )
        _append_pair_group(
            groups,
            group_id="spread-pair-reduce",
            main_id=main_id,
            next_id=next_id,
            delta_units=reduction,
            priority="risk_reduction",
            completion_ratio=1.0,
        )
        _append_pair_group(
            groups,
            group_id="spread-pair-increase",
            main_id=main_id,
            next_id=next_id,
            delta_units=increase,
            priority="risk_increase",
            completion_ratio=completion_ratio,
        )

    if account_reduce_only:
        groups = [
            item for item in groups if str(item["priority"]) != "risk_increase"
        ]
    result = {
        "schemaVersion": "asset-simulation-oil-multi-strategy-child-orders-v1",
        "shared_execution_completion_ratio": completion_ratio,
        "groups": sorted(
            groups,
            key=lambda item: (
                str(item["strategy_id"]), str(item["group_id"])
            ),
        ),
        "governance": {
            "completion_applied_before_allocator_to_risk_increase_only": True,
            "risk_reductions_not_completion_penalized": True,
            "spread_pair_atomic": True,
            "formal_account_reduce_only_applied": bool(account_reduce_only),
        },
    }
    rounded = _round_nested(result)
    return {"identity": {"result_hash": sha256_json(rounded)}, **rounded}


def create_oil_multi_strategy_runtime_state(
    *,
    initial_market: Mapping[str, Any],
    authorization_state: Mapping[str, Any],
    initial_company_cash_usd: float = 10_000_000.0,
) -> dict[str, Any]:
    """Open two virtual Strategy Books over one legal/economic account."""

    cash = float(initial_company_cash_usd)
    if not math.isfinite(cash) or cash <= 0.0:
        raise ValueError("initial multi-strategy company cash must be positive")
    entries = dict(authorization_state.get("authorizations", {}))
    if set(entries) != set(_STRATEGY_IDS):
        raise ValueError("runtime authorization must contain exactly two strategy ids")
    amounts = {
        strategy_id: float(entries[strategy_id]["authorized_capital_usd"])
        for strategy_id in _STRATEGY_IDS
    }
    if sum(amounts.values()) > cash + 1e-6:
        raise ValueError("opening strategy authorizations exceed company cash")
    books = {
        strategy_id: {
            "strategy_id": strategy_id,
            "authorized_capital_usd": amounts[strategy_id],
            "nav_usd": amounts[strategy_id],
            "positions": {},
            "cumulative_gross_pnl_usd": 0.0,
            "cumulative_execution_cost_usd": 0.0,
            "cumulative_margin_financing_cost_usd": 0.0,
            "cumulative_forced_liquidation_cost_usd": 0.0,
            "cumulative_fully_loaded_pnl_usd": 0.0,
            "strategy_risk_state": None,
            "thesis_state": None,
            "pending_thesis_evaluations": [],
        }
        for strategy_id in _STRATEGY_IDS
    }
    formal_account = create_oil_futures_account(
        account_id="GATE-B-DUAL-STRATEGY",
        initial_cash_usd=cash,
    )
    state = {
        "schemaVersion": "asset-simulation-oil-multi-strategy-runtime-state-v1",
        "asOf": dict(initial_market["asOf"]),
        "authorizationState": dict(authorization_state),
        "strategyBooks": books,
        "formalAccount": formal_account,
        "corporateReserveUsd": cash - sum(amounts.values()),
        "previousForecastVintage": None,
        "sharedExecutionState": {
            "rolling_external_gross_turnover_lots": [],
        },
        "completedTurns": 0,
        "lifecycle": {
            "status": "running_before_roll_boundary",
            "roll_scheduler_enabled": False,
            "long_horizon_economic_status": "lifecycle_incomplete",
            "opening_pair": list(_pair_ids(initial_market)),
            "stop_reason": None,
        },
        "previous_state_hash": None,
    }
    return _finalize_state(state)


def _effective_strategy_capital(book: Mapping[str, Any]) -> float:
    return max(
        0.0,
        min(float(book["authorized_capital_usd"]), float(book["nav_usd"])),
    )


def _compact_spread_pending(
    decision: Mapping[str, Any], *, created_turn_serial: int
) -> list[dict[str, Any]]:
    signal = dict(decision["signal"])
    shared_signal = {
        key: signal[key]
        for key in (
            "current_spread_usd_per_bbl",
            "normalization_reference_price_usd",
            "current_normalized_spread",
            "signal",
        )
    }
    main_id = str(decision["legs"]["main"]["contract_id"])
    next_id = str(decision["legs"]["next_main"]["contract_id"])
    result = []
    for component_value in signal.get("horizon_components", ()):
        component = dict(component_value)
        horizon = int(component["requested_horizon_weeks"])
        result.append(
            {
                "pending_id": f"{decision['identity']['result_hash']}:{horizon}",
                "created_turn_serial": int(created_turn_serial),
                "evaluation_horizon_weeks": horizon,
                "target_week_serial": int(component["target_week_serial"]),
                "main_contract_id": main_id,
                "next_main_contract_id": next_id,
                "decision": {
                    "thesisInvalidation": {
                        "stateBefore": deepcopy(
                            decision["thesisInvalidation"]["stateBefore"]
                        )
                    },
                    "signal": {
                        **shared_signal,
                        "horizon_components": [component],
                    },
                },
            }
        )
    return result


def _process_spread_pending(
    pending: Sequence[Mapping[str, Any]],
    *,
    current_state: Mapping[str, Any] | None,
    end_market: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    end_as_of = end_market["asOf"]
    end_serial = _cutoff_week_serial(
        int(end_as_of["year"]), int(end_as_of["month"]), int(end_as_of["half"])
    )
    contracts = {
        str(item["contract_id"]): item
        for item in end_market.get("curve", {}).get("contracts", ())
    }
    matured = [
        dict(item) for item in pending if int(item["target_week_serial"]) == end_serial
    ]
    remaining = [
        dict(item) for item in pending if int(item["target_week_serial"]) > end_serial
    ]
    stale = [
        item for item in pending if int(item["target_week_serial"]) < end_serial
    ]
    if stale:
        raise ValueError("calendar-spread pending thesis evaluation became stale")
    state = None if current_state is None else dict(current_state)
    evaluations: list[dict[str, Any]] = []
    matured.sort(
        key=lambda item: (
            int(item["created_turn_serial"]),
            int(item["evaluation_horizon_weeks"]),
            str(item["pending_id"]),
        )
    )
    for item in matured:
        main_id = str(item["main_contract_id"])
        next_id = str(item["next_main_contract_id"])
        if main_id not in contracts or next_id not in contracts:
            raise ValueError("matured calendar-spread thesis legs are unavailable")
        frozen = deepcopy(item["decision"])
        frozen["thesisInvalidation"]["stateBefore"] = state
        evaluated = evaluate_oil_calendar_spread_thesis_state(
            frozen,
            realized_main_price_usd=float(contracts[main_id]["price_usd"]),
            realized_next_main_price_usd=float(contracts[next_id]["price_usd"]),
            realized_week_serial=int(item["target_week_serial"]),
            evaluation_horizon_weeks=int(item["evaluation_horizon_weeks"]),
        )
        state = dict(evaluated["state"])
        evaluations.append(
            {
                "pending_id": item["pending_id"],
                **dict(evaluated["evaluation"]),
            }
        )
    return state, remaining, evaluations


def _account_margin_cap(state: Mapping[str, Any]) -> float:
    assets = load_registered_assets()
    pretrade = assets["oil_futures_account_config"]["pretrade"]
    maximum_pct = min(
        float(pretrade["maximum_initial_margin_pct_of_equity"]),
        100.0 - float(pretrade["minimum_free_collateral_pct_of_equity"]),
    )
    return max(0.0, float(state["formalAccount"]["equity_usd"]) * maximum_pct / 100.0)


def advance_oil_multi_strategy_runtime_one_turn(
    run: GlobalMacroRun,
    state: Mapping[str, Any],
    *,
    directional_strategy_profile: Mapping[str, Any] | None = None,
    calendar_spread_strategy_profile: Mapping[str, Any] | None = None,
    execution_desk_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Advance both strategies through one allocator/desk/account turn."""

    current_as_of = dict(state["asOf"])
    current_market = oil_futures_payload(
        run,
        as_of_year=int(current_as_of["year"]),
        as_of_month=int(current_as_of["month"]),
        as_of_half=int(current_as_of["half"]),
    )
    current_serial = _half_turn_serial(
        int(current_as_of["year"]),
        int(current_as_of["month"]),
        int(current_as_of["half"]),
    )
    next_year, next_month, next_half = _turn_from_serial(current_serial + 1)
    end_market = oil_futures_payload(
        run,
        as_of_year=next_year,
        as_of_month=next_month,
        as_of_half=next_half,
    )
    if _pair_ids(current_market) != _pair_ids(end_market):
        raise ValueError("Gate B lifecycle boundary reached before Roll Scheduler")
    if _positions(state["formalAccount"].get("positions")) != _aggregate_books(
        state["strategyBooks"]
    ):
        raise ValueError("opening strategy books do not reconcile to Formal Account")

    forecast = generate_oil_short_term_forecast(
        run,
        as_of_year=int(current_as_of["year"]),
        as_of_month=int(current_as_of["month"]),
        as_of_half=int(current_as_of["half"]),
        previous_vintage=state.get("previousForecastVintage"),
        market=current_market,
    )
    execution_profile, execution_policy = resolve_oil_execution_runtime_policy(
        execution_desk_profile
    )
    books = state["strategyBooks"]
    directional_book = books[DIRECTIONAL_STRATEGY_ID]
    spread_book = books[CALENDAR_SPREAD_STRATEGY_ID]
    directional_capital = _effective_strategy_capital(directional_book)
    spread_capital = _effective_strategy_capital(spread_book)

    directional_decision = None
    if directional_capital > 0.0:
        directional_decision = build_oil_strategy_decision(
            current_market,
            forecast,
            positions=_positions(directional_book.get("positions")),
            equity_usd=directional_capital,
            strategy_research_profile=directional_strategy_profile,
            execution_desk_profile=execution_profile,
            strategy_risk_state=directional_book.get("strategy_risk_state"),
            thesis_state=directional_book.get("thesis_state"),
            capital_authorization_pct_of_company_equity=100.0,
            fee_state={
                "rolling_gross_turnover_lots": int(
                    sum(
                        state["sharedExecutionState"].get(
                            "rolling_external_gross_turnover_lots", []
                        )[-24:]
                    )
                )
            },
        )

    spread_decision = None
    if spread_capital > 0.0:
        spread_decision = build_oil_calendar_spread_research_decision(
            current_market,
            forecast,
            authorized_strategy_capital_usd=spread_capital,
            positions=_positions(spread_book.get("positions")),
            strategy_research_profile=calendar_spread_strategy_profile,
            thesis_state=spread_book.get("thesis_state"),
        )

    formal_locked = (
        bool(state["formalAccount"].get("ever_insolvent"))
        or int(state["formalAccount"].get("restriction_turns_remaining", 0)) > 0
        or str(state["formalAccount"].get("status"))
        in {"reduce_only", "forced_liquidation", "insolvent"}
    )
    child_orders = build_oil_multi_strategy_child_orders(
        strategy_books=books,
        directional_decision=directional_decision,
        calendar_spread_decision=spread_decision,
        shared_execution_policy=execution_policy,
        account_reduce_only=formal_locked,
    )
    market_limits = build_gate_b_market_limits_from_oil_futures_payload(
        current_market,
        maximum_initial_margin_usd=_account_margin_cap(state),
    )
    allocation = allocate_gate_b_strategy_orders(
        authorization_state=state["authorizationState"],
        current_company_equity_usd=float(state["formalAccount"]["equity_usd"]),
        strategy_book_positions={
            strategy_id: _positions(books[strategy_id].get("positions"))
            for strategy_id in _STRATEGY_IDS
        },
        current_formal_positions=_positions(state["formalAccount"].get("positions")),
        market_limits=market_limits,
        order_groups=child_orders["groups"],
    )
    if not allocation["all_hard_gates_pass"]:
        raise ValueError("Gate B allocator hard gate failed")
    benchmark_contracts = {
        str(row["contract_id"]) for row in allocation["internalCrosses"]
    }
    desk_execution = execute_oil_multi_strategy_parent_orders(
        current_market,
        end_market,
        external_parent_orders=allocation["externalParentOrders"],
        formal_positions_before=allocation["formalAccountProjection"][
            "positions_before"
        ],
        benchmark_contract_ids=sorted(benchmark_contracts),
        execution_desk_profile=execution_profile,
        trailing_gross_turnover_lots=int(
            sum(
                state["sharedExecutionState"].get(
                    "rolling_external_gross_turnover_lots", []
                )[-24:]
            )
        ),
    )
    settlement = settle_oil_multi_strategy_allocated_turn(
        start_market=current_market,
        end_market=end_market,
        allocation=allocation,
        desk_execution=desk_execution,
        strategy_books_state=books,
        formal_account_state=state["formalAccount"],
        corporate_reserve_usd=float(state["corporateReserveUsd"]),
    )
    if not settlement["all_hard_gates_pass"]:
        raise ValueError("multi-strategy book/account settlement hard gate failed")

    updated_books = deepcopy(settlement["updatedStrategyBookState"])
    directional_thesis_report = None
    if directional_decision is not None:
        directional_thesis_report = evaluate_oil_strategy_thesis_state(
            directional_decision, end_market
        )
        updated_books[DIRECTIONAL_STRATEGY_ID]["thesis_state"] = dict(
            directional_thesis_report["state"]
        )
        updated_books[DIRECTIONAL_STRATEGY_ID]["strategy_risk_state"] = dict(
            directional_decision["strategyRisk"]["state"]
        )

    pending = [
        dict(item)
        for item in spread_book.get("pending_thesis_evaluations", ())
    ]
    if spread_decision is not None:
        pending.extend(
            _compact_spread_pending(
                spread_decision, created_turn_serial=current_serial
            )
        )
    _, config, _ = _assets()
    if len(pending) > int(
        config["calendar_spread_thesis"]["maximum_pending_evaluations"]
    ):
        raise ValueError("calendar-spread pending thesis ledger exceeds its bound")
    spread_thesis_state, remaining_pending, spread_evaluations = _process_spread_pending(
        pending,
        current_state=spread_book.get("thesis_state"),
        end_market=end_market,
    )
    updated_books[CALENDAR_SPREAD_STRATEGY_ID]["thesis_state"] = spread_thesis_state
    updated_books[CALENDAR_SPREAD_STRATEGY_ID][
        "pending_thesis_evaluations"
    ] = remaining_pending

    history = list(
        state["sharedExecutionState"].get(
            "rolling_external_gross_turnover_lots", []
        )
    )
    history.append(int(desk_execution["summary"]["actual_gross_turnover_lots"]))
    new_state = {
        **{key: value for key, value in state.items() if key != "identity"},
        "asOf": dict(end_market["asOf"]),
        "strategyBooks": updated_books,
        "formalAccount": settlement["formalAccount"]["state"],
        "corporateReserveUsd": float(
            settlement["corporateReserve"]["reserve_after_usd"]
        ),
        "previousForecastVintage": forecast,
        "sharedExecutionState": {
            "rolling_external_gross_turnover_lots": history[-48:],
        },
        "completedTurns": int(state["completedTurns"]) + 1,
        "previous_state_hash": str(state["identity"]["state_hash"]),
    }
    finalized_state = _finalize_state(new_state)
    report = {
        "schemaVersion": "asset-simulation-oil-multi-strategy-turn-report-v1",
        "turnNumber": int(finalized_state["completedTurns"]),
        "fromAsOf": dict(current_market["asOf"]),
        "toAsOf": dict(end_market["asOf"]),
        "authorizationState": state["authorizationState"],
        "effectiveStrategyCapitalUsd": {
            DIRECTIONAL_STRATEGY_ID: directional_capital,
            CALENDAR_SPREAD_STRATEGY_ID: spread_capital,
        },
        "childOrders": child_orders,
        "allocator": allocation,
        "sharedTradingDesk": desk_execution,
        "settlement": settlement,
        "thesis": {
            "directional": directional_thesis_report,
            "calendar_spread_matured": spread_evaluations,
            "calendar_spread_pending_count": len(remaining_pending),
        },
        "lifecycle": {
            "pair": list(_pair_ids(current_market)),
            "roll_scheduler_enabled": False,
            "long_horizon_economic_status": "lifecycle_incomplete",
        },
        "accountingIdentities": {
            "allocator_hard_gates": bool(allocation["all_hard_gates_pass"]),
            "desk_hard_gates": all(
                desk_execution["accountingIdentities"].values()
            ),
            "settlement_hard_gates": bool(settlement["all_hard_gates_pass"]),
            "state_books_equal_formal_account": (
                _aggregate_books(finalized_state["strategyBooks"])
                == _positions(finalized_state["formalAccount"].get("positions"))
            ),
        },
    }
    report["all_hard_gates_pass"] = all(report["accountingIdentities"].values())
    rounded_report = _round_nested(report)
    return {
        "state": finalized_state,
        "report": {
            "identity": {"result_hash": sha256_json(rounded_report)},
            **rounded_report,
        },
    }


def _performance(equities: Sequence[float], turns_per_year: int) -> dict[str, Any]:
    values = [float(value) for value in equities]
    if len(values) < 2:
        return {
            "completed_turns": 0,
            "cumulative_return_pct": 0.0,
            "provisional_annualized_return_pct": 0.0,
            "annualized_volatility_pct": 0.0,
            "maximum_drawdown_pct": 0.0,
        }
    returns = [after / before - 1.0 for before, after in zip(values, values[1:])]
    completed = len(returns)
    annualized = (
        (values[-1] / values[0]) ** (float(turns_per_year) / completed) - 1.0
        if values[-1] > 0.0 and values[0] > 0.0
        else -1.0
    )
    volatility = (
        0.0
        if len(returns) < 2
        else pstdev(returns) * math.sqrt(float(turns_per_year))
    )
    peak = values[0]
    maximum_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        maximum_drawdown = min(maximum_drawdown, value / peak - 1.0)
    return {
        "completed_turns": completed,
        "starting_equity_usd": values[0],
        "ending_equity_usd": values[-1],
        "cumulative_return_pct": 100.0 * (values[-1] / values[0] - 1.0),
        "provisional_annualized_return_pct": 100.0 * annualized,
        "annualized_volatility_pct": 100.0 * volatility,
        "maximum_drawdown_pct": 100.0 * maximum_drawdown,
        "annualization_warning": "short_pre_roll_window_not_a_long_horizon_economic_result",
    }


def simulate_oil_multi_strategy_runtime(
    run: GlobalMacroRun,
    *,
    strategy_authorizations_usd: Mapping[str, float],
    maximum_turns: int = 6,
    initial_company_cash_usd: float = 10_000_000.0,
    directional_strategy_profile: Mapping[str, Any] | None = None,
    calendar_spread_strategy_profile: Mapping[str, Any] | None = None,
    execution_desk_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the first mechanically valid pre-roll multi-strategy report window."""

    turns = int(maximum_turns)
    if isinstance(maximum_turns, bool) or turns < 1:
        raise ValueError("maximum_turns must be a positive integer")
    opening_market = oil_futures_payload(
        run, as_of_year=2030, as_of_month=1, as_of_half=1
    )
    authorization = create_strategy_capital_authorization_state(
        decision_id=(
            f"gate-b-{int(run.seed)}-"
            + "-".join(
                f"{key}:{float(strategy_authorizations_usd.get(key, 0.0)):g}"
                for key in _STRATEGY_IDS
            )
        ),
        effective_turn=str(opening_market["asOf"]["label"]),
        reference_company_equity_usd=float(initial_company_cash_usd),
        strategy_authorizations_usd={
            strategy_id: float(strategy_authorizations_usd.get(strategy_id, 0.0))
            for strategy_id in _STRATEGY_IDS
        },
    )
    state = create_oil_multi_strategy_runtime_state(
        initial_market=opening_market,
        authorization_state=authorization,
        initial_company_cash_usd=initial_company_cash_usd,
    )
    reports: list[dict[str, Any]] = []
    equities = [float(state["formalAccount"]["equity_usd"])]
    stop_reason = "maximum_turns_reached_before_roll_boundary"
    for _ in range(turns):
        current = dict(state["asOf"])
        serial = _half_turn_serial(
            int(current["year"]), int(current["month"]), int(current["half"])
        )
        next_year, next_month, next_half = _turn_from_serial(serial + 1)
        current_market = oil_futures_payload(
            run,
            as_of_year=int(current["year"]),
            as_of_month=int(current["month"]),
            as_of_half=int(current["half"]),
        )
        next_market = oil_futures_payload(
            run,
            as_of_year=next_year,
            as_of_month=next_month,
            as_of_half=next_half,
        )
        if _pair_ids(current_market) != _pair_ids(next_market):
            stop_reason = "main_next_pair_change_requires_roll_scheduler"
            break
        advanced = advance_oil_multi_strategy_runtime_one_turn(
            run,
            state,
            directional_strategy_profile=directional_strategy_profile,
            calendar_spread_strategy_profile=calendar_spread_strategy_profile,
            execution_desk_profile=execution_desk_profile,
        )
        state = advanced["state"]
        reports.append(advanced["report"])
        equities.append(float(state["formalAccount"]["equity_usd"]))

    lifecycle = dict(state["lifecycle"])
    lifecycle["status"] = "stopped_pre_roll_lifecycle_incomplete"
    lifecycle["stop_reason"] = stop_reason
    state = _finalize_state(
        {
            **{key: value for key, value in state.items() if key != "identity"},
            "lifecycle": lifecycle,
            "previous_state_hash": str(state["identity"]["state_hash"]),
        }
    )
    _, config, _ = _assets()
    strategy_summary = {
        strategy_id: {
            "authorized_capital_usd": float(
                state["strategyBooks"][strategy_id]["authorized_capital_usd"]
            ),
            "ending_nav_usd": float(state["strategyBooks"][strategy_id]["nav_usd"]),
            "cumulative_fully_loaded_pnl_usd": float(
                state["strategyBooks"][strategy_id][
                    "cumulative_fully_loaded_pnl_usd"
                ]
            ),
            "cumulative_execution_cost_usd": float(
                state["strategyBooks"][strategy_id][
                    "cumulative_execution_cost_usd"
                ]
            ),
            "ending_positions": _positions(
                state["strategyBooks"][strategy_id]["positions"]
            ),
        }
        for strategy_id in _STRATEGY_IDS
    }
    allocated_child = sum(
        int(report["allocator"]["turnoverDiagnostics"]["allocated_child_lot_sides"])
        for report in reports
    )
    internalized = sum(
        int(report["allocator"]["turnoverDiagnostics"]["internalized_child_lot_sides"])
        for report in reports
    )
    result = {
        "schemaVersion": "asset-simulation-oil-multi-strategy-runtime-report-v1",
        "seed": int(run.seed),
        "authorization": authorization,
        "performance": _performance(equities, int(config["turns_per_year"])),
        "strategySummary": strategy_summary,
        "turnoverAndNetting": {
            "allocated_child_lot_sides": allocated_child,
            "internalized_child_lot_sides": internalized,
            "external_parent_turnover_lots": sum(
                int(
                    report["allocator"]["turnoverDiagnostics"][
                        "external_parent_turnover_lots"
                    ]
                )
                for report in reports
            ),
            "internalization_ratio_pct": (
                0.0 if allocated_child == 0 else 100.0 * internalized / allocated_child
            ),
            "shared_external_execution_cost_usd": sum(
                float(report["sharedTradingDesk"]["summary"]["execution_cost_usd"])
                for report in reports
            ),
        },
        "lifecycle": lifecycle,
        "mechanicalStatus": {
            "multi_strategy_runtime_valid": bool(reports)
            and all(bool(report["all_hard_gates_pass"]) for report in reports),
            "accounting_reconciled": all(
                bool(report["settlement"]["all_hard_gates_pass"])
                for report in reports
            ),
            "one_shared_trading_desk": True,
            "formal_cash_and_margin_settle_once": True,
            "roll_scheduler_enabled": False,
            "long_horizon_economic_status": "lifecycle_incomplete",
        },
        "turnReports": reports,
        "finalState": state,
    }
    result["all_mechanical_hard_gates_pass"] = all(
        (
            bool(result["mechanicalStatus"]["multi_strategy_runtime_valid"]),
            bool(result["mechanicalStatus"]["accounting_reconciled"]),
            bool(result["mechanicalStatus"]["one_shared_trading_desk"]),
            bool(result["mechanicalStatus"]["formal_cash_and_margin_settle_once"]),
        )
    )
    result["all_hard_gates_pass"] = bool(
        result["all_mechanical_hard_gates_pass"]
    )
    result["long_horizon_economic_result_valid"] = False
    rounded = _round_nested(result)
    return {"identity": {"result_hash": sha256_json(rounded)}, **rounded}
