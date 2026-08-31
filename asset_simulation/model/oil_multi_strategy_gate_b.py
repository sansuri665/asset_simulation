"""Gate B amount authorization and shared-capacity allocation primitives.

This module is deliberately independent of alpha generation and execution-price
simulation. Strategy engines submit already authorized integer-unit order
groups; this layer preserves atomic leg ratios, nets opposing strategy-book
fills internally, applies one set of formal-account market constraints, and
returns reconciled child fills plus one external parent order per named
contract.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .oil_multi_strategy_allocation_core import (
    _aggregate_book_positions,
    _allocation_feasible,
    _allocate_priority_until_stalled,
    _build_internal_crosses,
    _net_deltas,
    _normalize_book_positions,
    _normalize_formal_positions,
    _normalize_market_limits,
    _normalize_order_groups,
)
from .oil_multi_strategy_authorization import (
    amend_strategy_capital_authorization_state,
    create_strategy_capital_authorization_state,
    evaluate_strategy_capital_authorization_status,
)
from .oil_multi_strategy_gate_b_common import (
    OIL_MULTI_STRATEGY_GATE_B_MODEL_VERSION,
    _assets,
    _round_nested,
    build_gate_b_market_limits_from_oil_futures_payload,
    load_oil_multi_strategy_gate_b_assets,
)
from .registry import sha256_json


def allocate_gate_b_strategy_orders(
    *,
    authorization_state: Mapping[str, Any],
    current_company_equity_usd: float,
    strategy_book_positions: Mapping[str, Mapping[str, Any]],
    current_formal_positions: Mapping[str, Any],
    market_limits: Mapping[str, Any],
    order_groups: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Allocate reconciled integer child fills under one formal account.

    Allocation is equal-unit water filling within each priority tier. A whole
    set of one-unit group increments is considered together, so opposing
    strategies can unlock internal-cross capacity without order-input bias.
    """

    assets, config, contract = _assets()
    authorization_status = evaluate_strategy_capital_authorization_status(
        authorization_state,
        current_company_equity_usd=current_company_equity_usd,
    )
    authorized_entries = dict(authorization_state["authorizations"])
    strategy_ids = set(map(str, authorized_entries))
    authorized_amounts = {
        strategy_id: float(authorized_entries[strategy_id]["authorized_capital_usd"])
        for strategy_id in strategy_ids
    }
    limits = _normalize_market_limits(market_limits)
    books_before = _normalize_book_positions(strategy_book_positions, strategy_ids)
    formal_before = _normalize_formal_positions(current_formal_positions)
    aggregate_before = _aggregate_book_positions(books_before)
    if aggregate_before != formal_before:
        raise ValueError("opening strategy books do not reconcile to the formal account")
    groups = _normalize_order_groups(
        order_groups,
        strategy_ids=strategy_ids,
        authorized_amounts=authorized_amounts,
        market_contract_ids=set(limits["contracts"]),
        config=config,
    )
    allocations = {
        (str(group["strategy_id"]), str(group["group_id"])): 0 for group in groups
    }
    priorities = tuple(config["allocation"]["priority_order"])
    maximum_subset_groups = int(
        config["allocation"]["maximum_groups_per_priority_subset_search"]
    )
    while True:
        cycle_progress = False
        for priority in priorities:
            cycle_progress = (
                _allocate_priority_until_stalled(
                    groups,
                    allocations,
                    priority=priority,
                    formal_positions=formal_before,
                    market_limits=limits,
                    maximum_subset_groups=maximum_subset_groups,
                )
                or cycle_progress
            )
        if not cycle_progress:
            break

    crosses, internal_fills, external_fills = _build_internal_crosses(
        groups, allocations
    )
    parent_orders = _net_deltas(groups, allocations)
    formal_after = {
        contract_id: int(formal_before.get(contract_id, 0))
        + int(parent_orders.get(contract_id, 0))
        for contract_id in sorted(
            set(limits["contracts"]) | set(formal_before) | set(parent_orders)
        )
    }
    formal_after = {key: value for key, value in formal_after.items() if value != 0}

    groups_output: list[dict[str, Any]] = []
    books_after = {
        strategy_id: dict(positions)
        for strategy_id, positions in books_before.items()
    }
    strategy_requested: dict[str, dict[str, int]] = {
        strategy_id: {} for strategy_id in strategy_ids
    }
    strategy_allocated: dict[str, dict[str, int]] = {
        strategy_id: {} for strategy_id in strategy_ids
    }
    strategy_internal: dict[str, dict[str, int]] = {
        strategy_id: {} for strategy_id in strategy_ids
    }
    strategy_external: dict[str, dict[str, int]] = {
        strategy_id: {} for strategy_id in strategy_ids
    }
    for group in groups:
        strategy_id = str(group["strategy_id"])
        group_id = str(group["group_id"])
        key = (strategy_id, group_id)
        requested_units = int(group["requested_units"])
        allocated_units = int(allocations[key])
        requested_legs = {
            contract_id: requested_units * int(ratio)
            for contract_id, ratio in group["legs"].items()
        }
        allocated_legs = {
            contract_id: allocated_units * int(ratio)
            for contract_id, ratio in group["legs"].items()
        }
        for contract_id in group["legs"]:
            for target, source in (
                (strategy_requested[strategy_id], requested_legs),
                (strategy_allocated[strategy_id], allocated_legs),
                (strategy_internal[strategy_id], internal_fills[key]),
                (strategy_external[strategy_id], external_fills[key]),
            ):
                target[contract_id] = target.get(contract_id, 0) + int(
                    source.get(contract_id, 0)
                )
            ending = int(books_after[strategy_id].get(contract_id, 0)) + int(
                allocated_legs[contract_id]
            )
            if ending:
                books_after[strategy_id][contract_id] = ending
            else:
                books_after[strategy_id].pop(contract_id, None)
        groups_output.append(
            {
                **group,
                "allocated_units": allocated_units,
                "unfilled_units": requested_units - allocated_units,
                "fill_ratio": (
                    1.0 if requested_units == 0 else allocated_units / requested_units
                ),
                "requested_leg_lots": requested_legs,
                "allocated_leg_lots": allocated_legs,
                "internal_fill_lots": internal_fills[key],
                "external_fill_lots": external_fills[key],
                "atomic_ratio_preserved": all(
                    int(allocated_legs[contract_id])
                    == allocated_units * int(ratio)
                    for contract_id, ratio in group["legs"].items()
                ),
                "unfilled_reason": (
                    None
                    if allocated_units == requested_units
                    else group["blocked_reason"]
                    or "shared_market_account_capacity"
                ),
            }
        )

    aggregate_after = _aggregate_book_positions(books_after)
    contract_diagnostics: dict[str, dict[str, Any]] = {}
    for contract_id, item in limits["contracts"].items():
        external = int(parent_orders.get(contract_id, 0))
        before = int(formal_before.get(contract_id, 0))
        after = int(formal_after.get(contract_id, 0))
        contract_diagnostics[contract_id] = {
            "formal_position_before_lots": before,
            "external_parent_order_lots": external,
            "formal_position_after_lots": after,
            "turn_trade_limit_lots": int(item["turn_trade_limit_lots"]),
            "turn_trade_limit_utilization": (
                0.0
                if int(item["turn_trade_limit_lots"]) == 0
                else abs(external) / int(item["turn_trade_limit_lots"])
            ),
            "single_contract_position_limit_lots": int(
                item["single_contract_position_limit_lots"]
            ),
            "new_trades_allowed": bool(item["new_trades_allowed"]),
        }
    child_gross = sum(
        abs(int(lots))
        for positions in strategy_allocated.values()
        for lots in positions.values()
    )
    internal_cross_lots = sum(int(row["lots"]) for row in crosses)
    internalized_lot_sides = 2 * internal_cross_lots
    external_turnover = sum(abs(int(value)) for value in parent_orders.values())
    internal_net_by_contract = {
        contract_id: sum(
            int(positions.get(contract_id, 0))
            for positions in strategy_internal.values()
        )
        for contract_id in limits["contracts"]
    }
    external_sum_by_contract = {
        contract_id: sum(
            int(positions.get(contract_id, 0))
            for positions in strategy_external.values()
        )
        for contract_id in limits["contracts"]
    }
    identities = {
        "opening_books_equal_formal_account": aggregate_before == formal_before,
        "ending_books_equal_formal_account": aggregate_after == formal_after,
        "internal_cross_zero_sum_by_contract": all(
            value == 0 for value in internal_net_by_contract.values()
        ),
        "external_parent_equals_strategy_external_fills": all(
            int(external_sum_by_contract[contract_id])
            == int(parent_orders.get(contract_id, 0))
            for contract_id in limits["contracts"]
        ),
        "formal_position_transition_closes": all(
            int(formal_after.get(contract_id, 0))
            == int(formal_before.get(contract_id, 0))
            + int(parent_orders.get(contract_id, 0))
            for contract_id in limits["contracts"]
        ),
        "atomic_group_ratios_preserved": all(
            bool(group["atomic_ratio_preserved"]) for group in groups_output
        ),
        "child_turnover_equals_internalized_plus_external": (
            child_gross == internalized_lot_sides + external_turnover
        ),
        "market_and_account_constraints_pass": _allocation_feasible(
            groups,
            allocations,
            formal_positions=formal_before,
            market_limits=limits,
        ),
        "authorization_amounts_not_rescaled": all(
            math.isclose(
                float(
                    authorization_status["authorizations"][strategy_id][
                        "effective_authorized_capital_usd"
                    ]
                ),
                float(authorized_amounts[strategy_id]),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            for strategy_id in strategy_ids
        ),
    }
    result = {
        "schemaVersion": "asset-simulation-oil-multi-strategy-gate-b-allocation-v1",
        "authorization": authorization_status,
        "allocationMethod": {
            "method": config["allocation"]["method"],
            "priority_order": list(priorities),
            "internal_cross_before_external_parent_order": True,
            "atomic_group_ratios_preserved": True,
            "input_order_independent": True,
        },
        "groups": groups_output,
        "internalCrosses": crosses,
        "externalParentOrders": parent_orders,
        "strategyBooks": {
            strategy_id: {
                "authorized_capital_usd": authorized_amounts[strategy_id],
                "positions_before": books_before[strategy_id],
                "requested_child_lots": strategy_requested[strategy_id],
                "allocated_child_lots": strategy_allocated[strategy_id],
                "internal_fill_lots": strategy_internal[strategy_id],
                "external_fill_lots": strategy_external[strategy_id],
                "positions_after": books_after[strategy_id],
            }
            for strategy_id in sorted(strategy_ids)
        },
        "formalAccountProjection": {
            "positions_before": formal_before,
            "external_parent_orders": parent_orders,
            "positions_after": formal_after,
            "gross_position_after_lots": sum(
                abs(value) for value in formal_after.values()
            ),
        },
        "marketCapacity": contract_diagnostics,
        "turnoverDiagnostics": {
            "requested_child_lot_sides": sum(
                abs(int(lots))
                for positions in strategy_requested.values()
                for lots in positions.values()
            ),
            "allocated_child_lot_sides": child_gross,
            "internal_cross_lots": internal_cross_lots,
            "internalized_child_lot_sides": internalized_lot_sides,
            "external_parent_turnover_lots": external_turnover,
            "external_turnover_saved_lot_sides": internalized_lot_sides,
        },
        "accountingIdentities": identities,
        "all_hard_gates_pass": all(identities.values()),
    }
    rounded = _round_nested(result)
    identity = {
        "schema_version": "asset-simulation-oil-multi-strategy-gate-b-allocation-identity-v1",
        "model_version": OIL_MULTI_STRATEGY_GATE_B_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["oil_multi_strategy_gate_b_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["oil_multi_strategy_gate_b_contract_hash"],
        "authorization_state_hash": authorization_state["identity"]["state_hash"],
        "result_hash": sha256_json(rounded),
    }
    return {"identity": identity, **rounded}
