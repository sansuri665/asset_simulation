"""Shared Trading Desk execution and reconciled Gate B book/account settlement."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .oil_execution_desk import resolve_oil_execution_runtime_policy
from .oil_futures_account import settle_oil_futures_account_turn
from .oil_strategy_research import resolve_oil_strategy_runtime_policy
from .oil_trading_strategy import (
    _aggregate_execution_price,
    _half_turn_serial,
    _resolve_fee_profile,
    _weekly_execution_ledger,
)
from .registry import load_registered_assets, sha256_json


DIRECTIONAL_STRATEGY_ID = "directional_oil"
CALENDAR_SPREAD_STRATEGY_ID = "calendar_spread"


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("multi-strategy execution contains a non-finite value")
        return round(value, 8)
    return value


def _contract_map(market: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["contract_id"]): dict(item)
        for item in market.get("curve", {}).get("contracts", ())
    }


def _position_map(value: Mapping[str, Any] | None) -> dict[str, int]:
    raw = dict(value or {})
    if any(isinstance(item, bool) or not isinstance(item, int) for item in raw.values()):
        raise ValueError("multi-strategy positions must be integer lots")
    return {str(key): int(item) for key, item in sorted(raw.items()) if int(item)}


def _role_map(market: Mapping[str, Any]) -> dict[str, str]:
    contracts = [dict(item) for item in market.get("curve", {}).get("contracts", ())]
    ids = [str(item["contract_id"]) for item in contracts]
    main_id = str(market.get("curve", {}).get("main_contract_id", ""))
    result = {contract_id: "legacy_exit" for contract_id in ids}
    if main_id in ids:
        index = ids.index(main_id)
        result[main_id] = "main"
        if index + 1 < len(ids):
            result[ids[index + 1]] = "next_main"
    return result


def _initial_margin_usd(positions: Mapping[str, int], market: Mapping[str, Any]) -> float:
    contracts = _contract_map(market)
    specification = market["contractSpecification"]
    contract_size = float(specification["contract_size_bbl"])
    rate = float(specification["initial_margin_rate_pct"]) / 100.0
    return sum(
        abs(int(lots))
        * float(contracts[contract_id]["price_usd"])
        * contract_size
        * rate
        for contract_id, lots in positions.items()
        if contract_id in contracts
    )


def _allocate_amount_exact(
    amount: float, weights: Mapping[str, float]
) -> dict[str, float]:
    keys = sorted(map(str, weights))
    total = sum(max(0.0, float(weights[key])) for key in keys)
    if not keys:
        return {}
    if abs(float(amount)) <= 1e-12:
        return {key: 0.0 for key in keys}
    if total <= 0.0:
        raw = {key: float(amount) / len(keys) for key in keys}
    else:
        raw = {
            key: float(amount) * max(0.0, float(weights[key])) / total
            for key in keys
        }
    result: dict[str, float] = {}
    running = 0.0
    for key in keys[:-1]:
        value = raw[key]
        result[key] = value
        running += value
    result[keys[-1]] = float(amount) - running
    return result


def _allocate_integer_exact(
    amount: int, capacities: Mapping[str, int]
) -> dict[str, int]:
    quantity = abs(int(amount))
    sign = 1 if int(amount) >= 0 else -1
    keys = [key for key in sorted(capacities) if int(capacities[key]) > 0]
    if quantity == 0:
        return {key: 0 for key in sorted(capacities)}
    if not keys or sum(int(capacities[key]) for key in keys) < quantity:
        raise ValueError("strategy books cannot absorb the formal forced reduction")
    total = sum(int(capacities[key]) for key in keys)
    raw = {key: quantity * int(capacities[key]) / total for key in keys}
    allocated = {key: min(int(capacities[key]), math.floor(raw[key])) for key in keys}
    remainder = quantity - sum(allocated.values())
    order = sorted(
        keys,
        key=lambda key: (raw[key] - allocated[key], key),
        reverse=True,
    )
    while remainder > 0:
        progressed = False
        for key in order:
            if allocated[key] < int(capacities[key]):
                allocated[key] += 1
                remainder -= 1
                progressed = True
                if remainder == 0:
                    break
        if not progressed:
            raise ValueError("forced-reduction integer allocation stalled")
    return {
        key: sign * int(allocated.get(key, 0))
        for key in sorted(capacities)
    }


def execute_oil_multi_strategy_parent_orders(
    start_market: Mapping[str, Any],
    end_market: Mapping[str, Any],
    *,
    external_parent_orders: Mapping[str, Any],
    formal_positions_before: Mapping[str, Any],
    benchmark_contract_ids: Sequence[str] = (),
    execution_desk_profile: Mapping[str, Any] | None = None,
    trailing_gross_turnover_lots: int = 0,
) -> dict[str, Any]:
    """Execute one net parent order per contract through one shared desk.

    Gate B applies the desk completion choice before the allocator for ordinary
    risk-increasing child intents. Parent orders that survive allocation are
    therefore executable instructions and settle in full inside published hard
    limits. Costs and prices are produced once at parent-order level.
    """

    start_as_of = dict(start_market["asOf"])
    end_as_of = dict(end_market["asOf"])
    if _half_turn_serial(
        int(end_as_of["year"]), int(end_as_of["month"]), int(end_as_of["half"])
    ) != _half_turn_serial(
        int(start_as_of["year"]),
        int(start_as_of["month"]),
        int(start_as_of["half"]),
    ) + 1:
        raise ValueError("shared desk requires adjacent half-month markets")
    if start_market["identity"]["upstream_global_identity_hash"] != end_market[
        "identity"
    ]["upstream_global_identity_hash"]:
        raise ValueError("shared desk markets must belong to one world")

    orders = {
        str(key): int(value)
        for key, value in sorted(dict(external_parent_orders).items())
        if int(value)
    }
    positions = _position_map(formal_positions_before)
    end_contracts = _contract_map(end_market)
    role_by_contract = _role_map(start_market)
    contract_ids = sorted(set(orders) | {str(value) for value in benchmark_contract_ids})
    if any(contract_id not in end_contracts for contract_id in contract_ids):
        raise ValueError("shared desk contract is unavailable at settlement")

    execution_profile, execution_policy = resolve_oil_execution_runtime_policy(
        execution_desk_profile
    )
    assets = load_registered_assets()
    strategy_config = assets["oil_trading_strategy_config"]
    friction_config = strategy_config["execution_friction"]
    _, neutral_strategy_policy = resolve_oil_strategy_runtime_policy(None)
    turnover_profile = dict(neutral_strategy_policy["execution"])
    fee_state = {
        "rolling_gross_turnover_lots": max(0, int(trailing_gross_turnover_lots))
    }
    fee_profile = _resolve_fee_profile(
        fee_state, friction_config, execution_policy=execution_policy
    )
    neutral_fee_profile = _resolve_fee_profile(fee_state, friction_config)
    specification = end_market["contractSpecification"]
    contract_size = float(specification["contract_size_bbl"])
    tick_size = float(specification["minimum_price_fluctuation_usd_per_bbl"])

    executions: dict[str, dict[str, Any]] = {}
    transfer_prices: dict[str, float] = {}
    total_cost = 0.0
    total_gross_pnl = 0.0
    total_traded_notional = 0.0
    total_turnover = 0
    for contract_id in contract_ids:
        end_contract = end_contracts[contract_id]
        neutral_price, realized_weeks = _aggregate_execution_price(
            end_contract,
            start_year=int(start_as_of["year"]),
            start_month=int(start_as_of["month"]),
            start_half=int(start_as_of["half"]),
            end_year=int(end_as_of["year"]),
            end_month=int(end_as_of["month"]),
            end_half=int(end_as_of["half"]),
        )
        transfer_prices[contract_id] = float(neutral_price)
        delta = int(orders.get(contract_id, 0))
        if not delta:
            continue
        limits = end_contract["participantLimits"]
        ledger = _weekly_execution_ledger(
            realized_weeks,
            net_delta_lots=delta,
            gross_turnover_budget_lots=abs(delta),
            starting_position_lots=int(positions.get(contract_id, 0)),
            position_limit_lots=int(limits["single_contract_position_limit_lots"]),
            weekly_setups=[],
            contract_size_bbl=contract_size,
            role=role_by_contract.get(contract_id, "legacy_exit"),
            tick_size_usd=tick_size,
            turnover_profile=turnover_profile,
            fee_profile=fee_profile,
            neutral_fee_profile=neutral_fee_profile,
            friction_config=friction_config,
            settlement_price_usd=float(end_contract["price_usd"]),
            execution_policy=execution_policy,
        )
        if int(ledger["net_delta_lots"]) != delta:
            raise ValueError("shared desk failed to execute the allocated parent order")
        executions[contract_id] = {
            "contract_id": contract_id,
            "role": role_by_contract.get(contract_id, "legacy_exit"),
            "parent_order_lots": delta,
            "actual_fill_lots": delta,
            "neutral_transfer_price_usd": float(neutral_price),
            "actual_execution_price_usd": float(ledger["net_execution_price_usd"]),
            "settlement_price_usd": float(end_contract["price_usd"]),
            "execution_cost_usd": float(ledger["execution_cost_usd"]),
            "gross_execution_pnl_before_cost_usd": float(
                ledger["gross_execution_pnl_before_cost_usd"]
            ),
            "net_execution_pnl_after_cost_usd": float(
                ledger["net_execution_pnl_after_cost_usd"]
            ),
            "traded_notional_usd": float(ledger["traded_notional_usd"]),
            "weeklyExecution": ledger,
        }
        total_cost += float(ledger["execution_cost_usd"])
        total_gross_pnl += float(ledger["gross_execution_pnl_before_cost_usd"])
        total_traded_notional += float(ledger["traded_notional_usd"])
        total_turnover += int(ledger["gross_turnover_lots"])

    result = {
        "schemaVersion": "asset-simulation-oil-multi-strategy-shared-desk-v1",
        "fromAsOf": start_as_of,
        "toAsOf": end_as_of,
        "desk": {
            "shared_desk_count": 1,
            "profile": execution_profile,
            "resolved_policy": execution_policy,
            "parent_orders_fully_filled_after_allocator": True,
            "round_trip_turnover_enabled": False,
        },
        "externalParentOrders": orders,
        "transferPrices": transfer_prices,
        "contractExecutions": executions,
        "summary": {
            "external_parent_turnover_lots": sum(abs(value) for value in orders.values()),
            "actual_gross_turnover_lots": total_turnover,
            "execution_cost_usd": total_cost,
            "gross_execution_pnl_before_cost_usd": total_gross_pnl,
            "traded_notional_usd": total_traded_notional,
        },
        "accountingIdentities": {
            "one_parent_order_per_contract": len(executions) == len(orders),
            "all_parent_orders_fully_filled": all(
                int(item["actual_fill_lots"]) == int(item["parent_order_lots"])
                for item in executions.values()
            ),
            "one_shared_desk": True,
        },
    }
    rounded = _round_nested(result)
    return {"identity": {"result_hash": sha256_json(rounded)}, **rounded}


def _allocate_external_costs(
    allocation: Mapping[str, Any], desk_execution: Mapping[str, Any]
) -> dict[str, dict[str, float]]:
    strategy_ids = sorted(allocation["strategyBooks"])
    result = {strategy_id: {} for strategy_id in strategy_ids}
    for contract_id, execution in desk_execution["contractExecutions"].items():
        parent = int(execution["actual_fill_lots"])
        child = {
            strategy_id: int(
                allocation["strategyBooks"][strategy_id]["external_fill_lots"].get(
                    contract_id, 0
                )
            )
            for strategy_id in strategy_ids
        }
        if sum(child.values()) != parent:
            raise ValueError("strategy external fills do not sum to the desk parent fill")
        weights = {key: abs(value) for key, value in child.items() if value}
        allocated = _allocate_amount_exact(float(execution["execution_cost_usd"]), weights)
        for strategy_id in strategy_ids:
            result[strategy_id][contract_id] = float(allocated.get(strategy_id, 0.0))
    return result


def _forced_position_allocations(
    books_before_forced: Mapping[str, Mapping[str, int]],
    formal_before_forced: Mapping[str, int],
    formal_after_forced: Mapping[str, int],
    *,
    insolvent: bool,
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    strategy_ids = sorted(books_before_forced)
    books_after = {
        strategy_id: dict(books_before_forced[strategy_id])
        for strategy_id in strategy_ids
    }
    forced = {strategy_id: {} for strategy_id in strategy_ids}
    for contract_id in sorted(
        set(formal_before_forced) | set(formal_after_forced)
    ):
        delta = int(formal_after_forced.get(contract_id, 0)) - int(
            formal_before_forced.get(contract_id, 0)
        )
        if delta == 0:
            continue
        if delta < 0:
            capacities = {
                strategy_id: max(0, int(books_after[strategy_id].get(contract_id, 0)))
                for strategy_id in strategy_ids
            }
        else:
            capacities = {
                strategy_id: max(0, -int(books_after[strategy_id].get(contract_id, 0)))
                for strategy_id in strategy_ids
            }
        allocated = _allocate_integer_exact(delta, capacities)
        for strategy_id, lots in allocated.items():
            if not lots:
                continue
            forced[strategy_id][contract_id] = int(lots)
            ending = int(books_after[strategy_id].get(contract_id, 0)) + int(lots)
            if ending:
                books_after[strategy_id][contract_id] = ending
            else:
                books_after[strategy_id].pop(contract_id, None)
    if insolvent:
        for strategy_id in strategy_ids:
            for contract_id, lots in list(books_after[strategy_id].items()):
                forced[strategy_id][contract_id] = forced[strategy_id].get(
                    contract_id, 0
                ) - int(lots)
            books_after[strategy_id] = {}
    return books_after, forced


def settle_oil_multi_strategy_allocated_turn(
    *,
    start_market: Mapping[str, Any],
    end_market: Mapping[str, Any],
    allocation: Mapping[str, Any],
    desk_execution: Mapping[str, Any],
    strategy_books_state: Mapping[str, Mapping[str, Any]],
    formal_account_state: Mapping[str, Any],
    corporate_reserve_usd: float,
) -> dict[str, Any]:
    """Allocate fills/PnL to books, then settle cash and margin exactly once."""

    strategy_ids = sorted(allocation["strategyBooks"])
    if set(strategy_ids) != set(strategy_books_state):
        raise ValueError("strategy state and Gate B allocation ids differ")
    formal_before = _position_map(formal_account_state.get("positions"))
    if formal_before != _position_map(
        allocation["formalAccountProjection"]["positions_before"]
    ):
        raise ValueError("formal account does not match the Gate B allocation opening")
    if dict(desk_execution["externalParentOrders"]) != dict(
        allocation["externalParentOrders"]
    ):
        raise ValueError("shared desk parent orders differ from Gate B allocation")

    start_contracts = _contract_map(start_market)
    end_contracts = _contract_map(end_market)
    specification = end_market["contractSpecification"]
    contract_size = float(specification["contract_size_bbl"])
    external_costs = _allocate_external_costs(allocation, desk_execution)
    transfer_prices = {
        str(key): float(value)
        for key, value in desk_execution["transferPrices"].items()
    }

    books_pre_forced: dict[str, dict[str, int]] = {}
    strategy_reports: dict[str, dict[str, Any]] = {}
    gross_sum = 0.0
    execution_cost_sum = 0.0
    trading_net_sum = 0.0
    for strategy_id in strategy_ids:
        state = dict(strategy_books_state[strategy_id])
        allocation_book = allocation["strategyBooks"][strategy_id]
        positions_before = _position_map(state.get("positions"))
        if positions_before != _position_map(allocation_book["positions_before"]):
            raise ValueError("strategy book opening positions do not match allocation")
        positions_after = _position_map(allocation_book["positions_after"])
        books_pre_forced[strategy_id] = positions_after
        internal_fills = _position_map(allocation_book["internal_fill_lots"])
        external_fills = _position_map(allocation_book["external_fill_lots"])
        contract_rows: dict[str, dict[str, Any]] = {}
        starting_pnl = 0.0
        internal_pnl = 0.0
        external_pnl = 0.0
        execution_cost = 0.0
        for contract_id in sorted(
            set(positions_before) | set(internal_fills) | set(external_fills)
        ):
            if contract_id not in start_contracts or contract_id not in end_contracts:
                raise ValueError("strategy book cannot mark an unavailable contract")
            start_price = float(start_contracts[contract_id]["price_usd"])
            end_price = float(end_contracts[contract_id]["price_usd"])
            transfer_price = float(
                transfer_prices.get(contract_id, start_price)
            )
            external_execution = desk_execution["contractExecutions"].get(contract_id)
            external_price = (
                None
                if external_execution is None
                else float(external_execution["actual_execution_price_usd"])
            )
            start_lots = int(positions_before.get(contract_id, 0))
            internal_lots = int(internal_fills.get(contract_id, 0))
            external_lots = int(external_fills.get(contract_id, 0))
            start_component = start_lots * (end_price - start_price) * contract_size
            internal_component = (
                internal_lots * (end_price - transfer_price) * contract_size
            )
            external_component = (
                0.0
                if external_lots == 0
                else external_lots
                * (end_price - float(external_price))
                * contract_size
            )
            cost = float(external_costs[strategy_id].get(contract_id, 0.0))
            contract_rows[contract_id] = {
                "contract_id": contract_id,
                "starting_position_lots": start_lots,
                "internal_fill_lots": internal_lots,
                "external_fill_lots": external_lots,
                "ending_position_before_forced_liquidation_lots": int(
                    positions_after.get(contract_id, 0)
                ),
                "start_price_usd": start_price,
                "transfer_price_usd": transfer_price,
                "external_execution_price_usd": external_price,
                "end_price_usd": end_price,
                "starting_position_pnl_usd": start_component,
                "internal_transfer_pnl_usd": internal_component,
                "external_fill_pnl_before_cost_usd": external_component,
                "gross_pnl_before_cost_usd": (
                    start_component + internal_component + external_component
                ),
                "allocated_external_execution_cost_usd": cost,
                "net_trading_pnl_usd": (
                    start_component + internal_component + external_component - cost
                ),
            }
            starting_pnl += start_component
            internal_pnl += internal_component
            external_pnl += external_component
            execution_cost += cost
        gross = starting_pnl + internal_pnl + external_pnl
        net = gross - execution_cost
        strategy_reports[strategy_id] = {
            "strategy_id": strategy_id,
            "authorized_capital_usd": float(state["authorized_capital_usd"]),
            "nav_before_usd": float(state["nav_usd"]),
            "positions_before": positions_before,
            "positions_before_forced_liquidation": positions_after,
            "contracts": contract_rows,
            "starting_position_pnl_usd": starting_pnl,
            "internal_transfer_pnl_usd": internal_pnl,
            "external_fill_pnl_before_cost_usd": external_pnl,
            "gross_pnl_before_cost_usd": gross,
            "external_execution_cost_usd": execution_cost,
            "net_trading_pnl_usd": net,
        }
        gross_sum += gross
        execution_cost_sum += execution_cost
        trading_net_sum += net

    formal_pre_forced = _position_map(
        allocation["formalAccountProjection"]["positions_after"]
    )
    if formal_pre_forced != {
        key: value
        for key, value in sorted(
            {
                contract_id: sum(
                    int(books_pre_forced[strategy_id].get(contract_id, 0))
                    for strategy_id in strategy_ids
                )
                for contract_id in set().union(
                    *(set(item) for item in books_pre_forced.values())
                )
            }.items()
        )
        if value
    }:
        raise ValueError("strategy books do not reconcile before formal settlement")

    statement = {
        "decision_id": str(allocation["identity"]["result_hash"]),
        "contracts": [
            {
                "contract_id": contract_id,
                "starting_position_lots": int(formal_before.get(contract_id, 0)),
            }
            for contract_id in sorted(set(formal_before) | set(formal_pre_forced))
        ],
        "accountAfter": {
            "positions": formal_pre_forced,
            "turn_pnl_usd": trading_net_sum,
            "gross_pnl_before_cost_usd": gross_sum,
            "execution_cost_usd": execution_cost_sum,
        },
    }
    account_settlement = settle_oil_futures_account_turn(
        formal_account_state, start_market, end_market, statement
    )
    account_ledger = account_settlement["ledger"]
    formal_after = _position_map(account_settlement["accountAfter"]["positions"])
    books_after_forced, forced_fills = _forced_position_allocations(
        books_pre_forced,
        formal_pre_forced,
        formal_after,
        insolvent=bool(account_settlement["state"].get("ever_insolvent")),
    )

    average_margin = {
        strategy_id: 0.5
        * (
            _initial_margin_usd(
                strategy_reports[strategy_id]["positions_before"], start_market
            )
            + _initial_margin_usd(books_pre_forced[strategy_id], end_market)
        )
        for strategy_id in strategy_ids
    }
    financing_alloc = _allocate_amount_exact(
        float(account_ledger["margin_financing_cost_usd"]), average_margin
    )
    forced_cost_alloc = {strategy_id: 0.0 for strategy_id in strategy_ids}
    liquidation_rows = dict(account_ledger.get("forced_liquidations", {}))
    for contract_id, row in liquidation_rows.items():
        weights = {
            strategy_id: abs(int(forced_fills[strategy_id].get(contract_id, 0)))
            for strategy_id in strategy_ids
            if int(forced_fills[strategy_id].get(contract_id, 0))
        }
        allocated = _allocate_amount_exact(float(row["total_cost_usd"]), weights)
        for strategy_id, value in allocated.items():
            forced_cost_alloc[strategy_id] += float(value)

    updated_books: dict[str, dict[str, Any]] = {}
    fully_loaded_sum = 0.0
    for strategy_id in strategy_ids:
        prior = dict(strategy_books_state[strategy_id])
        report = strategy_reports[strategy_id]
        financing = float(financing_alloc.get(strategy_id, 0.0))
        forced_cost = float(forced_cost_alloc.get(strategy_id, 0.0))
        fully_loaded = float(report["net_trading_pnl_usd"]) - financing - forced_cost
        nav_after = float(prior["nav_usd"]) + fully_loaded
        report.update(
            {
                "forced_liquidation_fill_lots": forced_fills[strategy_id],
                "positions_after": books_after_forced[strategy_id],
                "margin_financing_cost_usd": financing,
                "forced_liquidation_cost_usd": forced_cost,
                "fully_loaded_pnl_usd": fully_loaded,
                "nav_after_usd": nav_after,
            }
        )
        updated_books[strategy_id] = {
            **prior,
            "positions": books_after_forced[strategy_id],
            "nav_usd": nav_after,
            "cumulative_gross_pnl_usd": float(
                prior.get("cumulative_gross_pnl_usd", 0.0)
            )
            + float(report["gross_pnl_before_cost_usd"]),
            "cumulative_execution_cost_usd": float(
                prior.get("cumulative_execution_cost_usd", 0.0)
            )
            + float(report["external_execution_cost_usd"]),
            "cumulative_margin_financing_cost_usd": float(
                prior.get("cumulative_margin_financing_cost_usd", 0.0)
            )
            + financing,
            "cumulative_forced_liquidation_cost_usd": float(
                prior.get("cumulative_forced_liquidation_cost_usd", 0.0)
            )
            + forced_cost,
            "cumulative_fully_loaded_pnl_usd": float(
                prior.get("cumulative_fully_loaded_pnl_usd", 0.0)
            )
            + fully_loaded,
        }
        fully_loaded_sum += fully_loaded

    reserve_before = float(corporate_reserve_usd)
    reserve_after = reserve_before + float(account_ledger["idle_cash_interest_usd"])
    formal_equity_after = float(account_settlement["accountAfter"]["equity_usd"])
    tolerance = 0.05
    aggregate_books_after = {
        contract_id: sum(
            int(updated_books[strategy_id]["positions"].get(contract_id, 0))
            for strategy_id in strategy_ids
        )
        for contract_id in set().union(
            *(set(updated_books[strategy_id]["positions"]) for strategy_id in strategy_ids)
        )
    }
    aggregate_books_after = {
        key: value for key, value in sorted(aggregate_books_after.items()) if value
    }
    identities = {
        "strategy_external_costs_equal_shared_desk_cost": math.isclose(
            execution_cost_sum,
            float(desk_execution["summary"]["execution_cost_usd"]),
            abs_tol=tolerance,
        ),
        "strategy_internal_transfer_pnl_is_zero_sum": math.isclose(
            sum(
                float(strategy_reports[strategy_id]["internal_transfer_pnl_usd"])
                for strategy_id in strategy_ids
            ),
            0.0,
            abs_tol=tolerance,
        ),
        "strategy_net_trading_pnl_equals_formal_variation_margin": math.isclose(
            trading_net_sum,
            float(account_ledger["variation_margin_usd"]),
            abs_tol=tolerance,
        ),
        "strategy_books_equal_formal_positions_after_settlement": (
            aggregate_books_after == formal_after
        ),
        "strategy_fully_loaded_plus_reserve_change_equals_formal_net_pnl": math.isclose(
            fully_loaded_sum + (reserve_after - reserve_before),
            float(account_ledger["account_net_pnl_usd"]),
            abs_tol=tolerance,
        ),
        "strategy_nav_plus_reserve_equals_formal_equity": math.isclose(
            sum(float(updated_books[key]["nav_usd"]) for key in strategy_ids)
            + reserve_after,
            formal_equity_after,
            abs_tol=tolerance,
        ),
        "formal_cash_identity_closes": abs(
            float(account_ledger["cash_identity_error_usd"])
        )
        <= tolerance,
        "cash_settled_once": True,
        "aggregate_margin_charged_once": True,
    }
    result = {
        "schemaVersion": "asset-simulation-oil-multi-strategy-settlement-v1",
        "strategyBooks": strategy_reports,
        "updatedStrategyBookState": updated_books,
        "corporateReserve": {
            "reserve_before_usd": reserve_before,
            "idle_cash_interest_usd": float(account_ledger["idle_cash_interest_usd"]),
            "reserve_after_usd": reserve_after,
        },
        "formalAccount": account_settlement,
        "accountingIdentities": identities,
        "all_hard_gates_pass": all(identities.values()),
    }
    rounded = _round_nested(result)
    return {"identity": {"result_hash": sha256_json(rounded)}, **rounded}
