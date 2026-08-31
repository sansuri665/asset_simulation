"""Deterministic integer allocation core for Gate B strategy order groups."""

from __future__ import annotations

from itertools import combinations
from typing import Any, Mapping, Sequence

from .oil_multi_strategy_gate_b_common import _finite_nonnegative, _integer


def _normalize_book_positions(
    value: Mapping[str, Mapping[str, Any]], strategy_ids: set[str]
) -> dict[str, dict[str, int]]:
    raw = {str(key): dict(item) for key, item in value.items()}
    if set(raw) != strategy_ids:
        raise ValueError("strategy-book positions must exactly match authorized strategies")
    result: dict[str, dict[str, int]] = {}
    for strategy_id, positions in sorted(raw.items()):
        result[strategy_id] = {
            str(contract_id): _integer(lots, f"{strategy_id} {contract_id} lots")
            for contract_id, lots in sorted(positions.items())
            if int(lots) != 0
        }
    return result


def _normalize_formal_positions(value: Mapping[str, Any]) -> dict[str, int]:
    return {
        str(contract_id): _integer(lots, f"formal {contract_id} lots")
        for contract_id, lots in sorted(dict(value).items())
        if int(lots) != 0
    }


def _normalize_market_limits(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(value)
    raw_contracts = dict(raw.get("contracts", {}))
    if not raw_contracts:
        raise ValueError("Gate B market limits require named contracts")
    contracts: dict[str, dict[str, Any]] = {}
    for contract_id, raw_limit in sorted(raw_contracts.items()):
        item = dict(raw_limit)
        position_limit = _integer(
            item["single_contract_position_limit_lots"],
            f"{contract_id} position limit",
            nonnegative=True,
        )
        turn_limit = _integer(
            item["turn_trade_limit_lots"],
            f"{contract_id} turn trade limit",
            nonnegative=True,
        )
        normalized: dict[str, Any] = {
            "single_contract_position_limit_lots": position_limit,
            "turn_trade_limit_lots": turn_limit,
            "new_trades_allowed": bool(item.get("new_trades_allowed", True)),
        }
        if "initial_margin_usd_per_lot" in item:
            normalized["initial_margin_usd_per_lot"] = _finite_nonnegative(
                item["initial_margin_usd_per_lot"],
                f"{contract_id} initial margin per lot",
            )
        contracts[str(contract_id)] = normalized
    result: dict[str, Any] = {"contracts": contracts}
    if "all_contract_gross_position_cap_lots" in raw:
        result["all_contract_gross_position_cap_lots"] = _integer(
            raw["all_contract_gross_position_cap_lots"],
            "all-contract gross position cap",
            nonnegative=True,
        )
    if "maximum_initial_margin_usd" in raw:
        result["maximum_initial_margin_usd"] = _finite_nonnegative(
            raw["maximum_initial_margin_usd"], "maximum initial margin"
        )
        missing_margin = [
            contract_id
            for contract_id, item in contracts.items()
            if "initial_margin_usd_per_lot" not in item
        ]
        if missing_margin:
            raise ValueError(
                "maximum initial margin requires per-lot margin for every contract"
            )
    return result


def _normalize_order_groups(
    order_groups: Sequence[Mapping[str, Any]],
    *,
    strategy_ids: set[str],
    authorized_amounts: Mapping[str, float],
    market_contract_ids: set[str],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    priorities = tuple(config["allocation"]["priority_order"])
    maximum_total = int(config["allocation"]["maximum_total_requested_units"])
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    total_requested = 0
    for raw_group in order_groups:
        raw = dict(raw_group)
        strategy_id = str(raw.get("strategy_id", "")).strip()
        group_id = str(raw.get("group_id", "")).strip()
        if strategy_id not in strategy_ids:
            raise ValueError(f"unknown strategy order group: {strategy_id}")
        key = (strategy_id, group_id)
        if not group_id or key in seen:
            raise ValueError("strategy order group ids must be unique within strategy")
        seen.add(key)
        priority = str(raw.get("priority", "risk_increase"))
        if priority not in priorities:
            raise ValueError(f"unsupported Gate B order priority: {priority}")
        requested_units = _integer(
            raw.get("requested_units"),
            f"{strategy_id}/{group_id} requested units",
            nonnegative=True,
        )
        total_requested += requested_units
        raw_legs = dict(raw.get("legs", {}))
        if not raw_legs:
            raise ValueError("Gate B order groups require at least one named leg")
        legs: dict[str, int] = {}
        for contract_id, raw_ratio in sorted(raw_legs.items()):
            resolved_contract = str(contract_id)
            if resolved_contract not in market_contract_ids:
                raise ValueError(
                    f"Gate B order leg is absent from market limits: {resolved_contract}"
                )
            ratio = _integer(
                raw_ratio, f"{strategy_id}/{group_id}/{resolved_contract} leg ratio"
            )
            if ratio == 0:
                raise ValueError("Gate B order leg ratios cannot be zero")
            legs[resolved_contract] = ratio
        risk_increase = priority == "risk_increase"
        authorized_amount = float(authorized_amounts[strategy_id])
        eligible = not risk_increase or authorized_amount > 0.0
        normalized.append(
            {
                "strategy_id": strategy_id,
                "group_id": group_id,
                "priority": priority,
                "requested_units": requested_units,
                "legs": legs,
                "atomic": bool(raw.get("atomic", len(legs) > 1)),
                "authorized_capital_usd": authorized_amount,
                "eligible": eligible,
                "blocked_reason": (
                    None
                    if eligible
                    else "zero_amount_authorization_blocks_risk_increase"
                ),
            }
        )
    if total_requested > maximum_total:
        raise ValueError("Gate B requested units exceed the configured safety bound")
    normalized.sort(key=lambda item: (item["strategy_id"], item["group_id"]))
    return normalized


def _aggregate_book_positions(
    books: Mapping[str, Mapping[str, int]]
) -> dict[str, int]:
    result: dict[str, int] = {}
    for positions in books.values():
        for contract_id, lots in positions.items():
            result[contract_id] = result.get(contract_id, 0) + int(lots)
    return {key: value for key, value in sorted(result.items()) if value != 0}


def _net_deltas(
    groups: Sequence[Mapping[str, Any]], allocations: Mapping[tuple[str, str], int]
) -> dict[str, int]:
    result: dict[str, int] = {}
    for group in groups:
        units = int(allocations[(str(group["strategy_id"]), str(group["group_id"]))])
        if units == 0:
            continue
        for contract_id, ratio in group["legs"].items():
            result[str(contract_id)] = result.get(str(contract_id), 0) + units * int(ratio)
    return {key: value for key, value in sorted(result.items()) if value != 0}


def _allocation_feasible(
    groups: Sequence[Mapping[str, Any]],
    allocations: Mapping[tuple[str, str], int],
    *,
    formal_positions: Mapping[str, int],
    market_limits: Mapping[str, Any],
) -> bool:
    net = _net_deltas(groups, allocations)
    contracts = market_limits["contracts"]
    ending: dict[str, int] = {}
    for contract_id in sorted(set(contracts) | set(formal_positions) | set(net)):
        if contract_id not in contracts:
            return False
        current = int(formal_positions.get(contract_id, 0))
        delta = int(net.get(contract_id, 0))
        item = contracts[contract_id]
        if abs(delta) > int(item["turn_trade_limit_lots"]):
            return False
        end = current + delta
        limit = int(item["single_contract_position_limit_lots"])
        if abs(current) <= limit:
            if abs(end) > limit:
                return False
        elif abs(end) > abs(current):
            return False
        if not bool(item["new_trades_allowed"]):
            if abs(end) > abs(current):
                return False
            if current != 0 and end != 0 and current * end < 0:
                return False
            if current == 0 and end != 0:
                return False
        ending[contract_id] = end
    gross_cap = market_limits.get("all_contract_gross_position_cap_lots")
    if gross_cap is not None:
        current_gross = sum(abs(int(value)) for value in formal_positions.values())
        ending_gross = sum(abs(int(value)) for value in ending.values())
        cap = int(gross_cap)
        if current_gross <= cap:
            if ending_gross > cap:
                return False
        elif ending_gross > current_gross:
            return False
    margin_cap = market_limits.get("maximum_initial_margin_usd")
    if margin_cap is not None:
        ending_margin = sum(
            abs(int(lots))
            * float(contracts[contract_id]["initial_margin_usd_per_lot"])
            for contract_id, lots in ending.items()
        )
        current_margin = sum(
            abs(int(lots))
            * float(contracts[contract_id]["initial_margin_usd_per_lot"])
            for contract_id, lots in formal_positions.items()
        )
        cap = float(margin_cap)
        if current_margin <= cap + 1e-6:
            if ending_margin > cap + 1e-6:
                return False
        elif ending_margin > current_margin + 1e-6:
            return False
    return True


def _increment_subset(
    allocations: Mapping[tuple[str, str], int],
    subset: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], int]:
    candidate = dict(allocations)
    for group in subset:
        key = (str(group["strategy_id"]), str(group["group_id"]))
        candidate[key] = int(candidate[key]) + 1
    return candidate


def _allocate_priority_until_stalled(
    groups: Sequence[Mapping[str, Any]],
    allocations: dict[tuple[str, str], int],
    *,
    priority: str,
    formal_positions: Mapping[str, int],
    market_limits: Mapping[str, Any],
    maximum_subset_groups: int,
) -> bool:
    progress = False
    while True:
        active = [
            group
            for group in groups
            if bool(group["eligible"])
            and str(group["priority"]) == priority
            and int(allocations[(str(group["strategy_id"]), str(group["group_id"]))])
            < int(group["requested_units"])
        ]
        if not active:
            return progress
        active.sort(
            key=lambda group: (
                int(
                    allocations[
                        (str(group["strategy_id"]), str(group["group_id"]))
                    ]
                ),
                str(group["strategy_id"]),
                str(group["group_id"]),
            )
        )
        if len(active) > maximum_subset_groups:
            raise ValueError(
                "too many same-priority Gate B groups for deterministic subset search"
            )
        selected: tuple[Mapping[str, Any], ...] | None = None
        for size in range(len(active), 0, -1):
            for subset in combinations(active, size):
                candidate = _increment_subset(allocations, subset)
                if _allocation_feasible(
                    groups,
                    candidate,
                    formal_positions=formal_positions,
                    market_limits=market_limits,
                ):
                    selected = subset
                    break
            if selected is not None:
                break
        if selected is None:
            return progress
        for group in selected:
            key = (str(group["strategy_id"]), str(group["group_id"]))
            allocations[key] += 1
        progress = True


def _build_internal_crosses(
    groups: Sequence[Mapping[str, Any]],
    allocations: Mapping[tuple[str, str], int],
) -> tuple[
    list[dict[str, Any]],
    dict[tuple[str, str], dict[str, int]],
    dict[tuple[str, str], dict[str, int]],
]:
    total_fills: dict[tuple[str, str], dict[str, int]] = {}
    internal_fills: dict[tuple[str, str], dict[str, int]] = {}
    for group in groups:
        key = (str(group["strategy_id"]), str(group["group_id"]))
        units = int(allocations[key])
        total_fills[key] = {
            str(contract_id): units * int(ratio)
            for contract_id, ratio in group["legs"].items()
        }
        internal_fills[key] = {str(contract_id): 0 for contract_id in group["legs"]}

    crosses: list[dict[str, Any]] = []
    contracts = sorted(
        {
            contract_id
            for fills in total_fills.values()
            for contract_id in fills
        }
    )
    for contract_id in contracts:
        buys = [
            [key, int(fills.get(contract_id, 0))]
            for key, fills in sorted(total_fills.items())
            if int(fills.get(contract_id, 0)) > 0
        ]
        sells = [
            [key, -int(fills.get(contract_id, 0))]
            for key, fills in sorted(total_fills.items())
            if int(fills.get(contract_id, 0)) < 0
        ]
        while True:
            match: tuple[int, int] | None = None
            for buy_index, buy in enumerate(buys):
                if buy[1] <= 0:
                    continue
                for sell_index, sell in enumerate(sells):
                    if sell[1] <= 0:
                        continue
                    if buy[0][0] != sell[0][0]:
                        match = (buy_index, sell_index)
                        break
                if match is not None:
                    break
            if match is None:
                break
            buy_index, sell_index = match
            buy_key, buy_remaining = buys[buy_index]
            sell_key, sell_remaining = sells[sell_index]
            lots = min(int(buy_remaining), int(sell_remaining))
            internal_fills[buy_key][contract_id] += lots
            internal_fills[sell_key][contract_id] -= lots
            buys[buy_index][1] -= lots
            sells[sell_index][1] -= lots
            crosses.append(
                {
                    "contract_id": contract_id,
                    "buyer_strategy_id": buy_key[0],
                    "buyer_group_id": buy_key[1],
                    "seller_strategy_id": sell_key[0],
                    "seller_group_id": sell_key[1],
                    "lots": lots,
                }
            )
    external_fills = {
        key: {
            contract_id: int(total_fills[key].get(contract_id, 0))
            - int(internal_fills[key].get(contract_id, 0))
            for contract_id in total_fills[key]
        }
        for key in total_fills
    }
    return crosses, internal_fills, external_fills
