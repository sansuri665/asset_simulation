"""Multi-turn audit for main-linked Stage6C Preview3 bilateral formation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import unittest

from .model.freight_board import (
    BallastOrder,
    BilateralSession,
    build_main_linked_preview3_inputs,
    form_seller_offers,
    make_preview3_board_spec,
)
from .model.registry import sha256_json


def _offered(snapshot):
    return {
        origin: tuple(
            ship.ship_id for ship in snapshot.opened_market.ships
            if ship.location == origin
        )
        for origin in snapshot.spec.origins
    }


def _ballasts(snapshot, seller_offers):
    """Replaceable audit caller: balance future capacity against seller offers.

    This is deliberately outside the market. It reads no candidate price and
    cannot move a ship this turn; it only assigns currently idle destination
    hulls to a real ballast leg for future turns.
    """
    capacity = {origin: 0 for origin in snapshot.spec.origins}
    for ship in snapshot.opened_market.ships:
        if ship.movement is None and ship.location in capacity:
            capacity[ship.location] += ship.capacity_bbl
        elif ship.movement and ship.movement.kind == "ballast" and ship.movement.destination in capacity:
            capacity[ship.movement.destination] += ship.capacity_bbl
    desired = {
        origin: max(1, round(
            row["final_offer_bbl"] * (1 + 0.5 * max(0.0, row["source_inventory_signal"]))
        ))
        for origin, row in seller_offers.items()
    }
    orders = []
    idle = sorted(
        (
            ship for ship in snapshot.opened_market.ships
            if ship.location == snapshot.spec.market.destination and ship.movement is None
        ),
        key=lambda ship: ship.ship_id,
    )
    for ship in idle:
        compatible = [
            origin for origin in snapshot.spec.origins
            if any(service.class_id == ship.class_id for service in snapshot.spec.market.physical.lane(origin).services)
        ]
        target = min(
            compatible,
            key=lambda origin: (capacity[origin] / desired[origin], snapshot.spec.origins.index(origin)),
        )
        orders.append(BallastOrder(ship.ship_id, target))
        capacity[target] += ship.capacity_bbl
    return tuple(orders)


def replay(seed: int, years: int, *, max_turns: int | None = None, keep_turns: bool = False) -> dict:
    spec = make_preview3_board_spec()
    inputs, main_link = build_main_linked_preview3_inputs(spec, seed=seed, years=years)
    if max_turns is not None:
        inputs = inputs[:max_turns]
    session = BilateralSession(
        spec,
        fleet_counts={"vlcc": 280, "suezmax": 160, "aframax": 40},
    )
    rows = []
    failure = None
    for item in inputs:
        snapshot = session.open_turn(
            source_release_bbl=item["source_release_bbl"],
            new_requirements=item["new_requirements"],
            cpi=item["cpi"],
        )
        seller_offers = form_seller_offers(
            snapshot,
            item["natural_cargo_plan_bbl"],
            previous_spot_offer_bbl=session.previous_spot_offer_bbl,
            formation_spec=session.formation_spec,
        )
        indication = session.indicate(
            _offered(snapshot), item["natural_cargo_plan_bbl"],
            ballast_orders=_ballasts(snapshot, seller_offers),
        )
        report = indication.report()
        if not report["final_trial_valid"]:
            failure = {
                "turn": item["turn"],
                "label": item["label"],
                "kind": "no_feasible_firm_plan",
                "violations": report["final_hard_violations"],
                "natural_cargo_plan_bbl": report["natural_cargo_plan_bbl"],
                "seller_offers": report["seller_offers"],
                "final_cargo_plan_bbl": report["final_cargo_plan_bbl"],
                "actions": [row["action"] for row in report["iterations"] if row["action"]],
            }
            break
        firm = session.lock_firm(indication)
        record = session.commit_firm()
        actions = [entry["action"] for entry in report["iterations"] if entry["action"]]
        switches = [action for action in actions if action["kind"] == "source_switch"]
        natural_total = report["natural_total_bbl"]
        final_total = report["final_total_bbl"]
        inventory_deviation_fraction = (
            abs(final_total - natural_total) / natural_total if natural_total else 0.0
        )
        rows.append({
            "turn": item["turn"],
            "label": item["label"],
            "snapshot_id": snapshot.snapshot_id,
            "indication_id": indication.identity,
            "commitment_id": firm.identity,
            "execution_id": record["execution_id"],
            "natural_total_bbl": natural_total,
            "final_total_bbl": final_total,
            "inventory_deviation_fraction": inventory_deviation_fraction,
            "source_reallocated_bbl": report["source_reallocated_bbl"],
            "seller_offer_bbl": report["seller_offer_bbl"],
            "seller_offers": report["seller_offers"],
            "final_plan_bbl": report["final_cargo_plan_bbl"],
            "unserved_bbl": report["unserved_trial_cargo_bbl"],
            "mass_residual_bbl": indication.final_trial.report()["inventory"]["physical_mass_balance_residual_bbl"],
            "destination_opening_stock_bbl": indication.final_trial.report()["inventory"]["destination"]["opening"]["stock_bbl"],
            "source_closing_stock_bbl": {
                origin: indication.final_trial.report()["inventory"]["sources"][origin]["closing"]["stock_bbl"]
                for origin in spec.origins
            },
            "switch_directions": [(action["from"], action["to"]) for action in switches],
            "iteration_count": len(report["iterations"]),
            "action_kinds": [action["kind"] for action in actions],
            "stop_reason": report["stop_reason"],
            "locked_ship_count": len(firm.locked_ship_ids),
            "firm_lock_recorded": record["firm_plan_cannot_retarget_within_turn"],
        })

    deviations = [row["inventory_deviation_fraction"] for row in rows]
    saturation = sum(abs(value - 0.10) < 1e-9 or abs(value - 0.20) < 1e-9 for value in deviations)
    reverse_cycles = 0
    opposite_inventory = 0
    for row in rows:
        directions = set(tuple(pair) for pair in row["switch_directions"])
        reverse_cycles += any((b, a) in directions for a, b in directions)
        kinds = set(row["action_kinds"])
        opposite_inventory += (
            "buyer_inventory_replenishment" in kinds and "buyer_inventory_deferral" in kinds
        )
    summary = {
        "seed": seed,
        "years": years,
        "input_turns": len(inputs),
        "committed_turns": len(rows),
        "failure": failure,
        "main_link": main_link,
        "mass_conserved": all(row["mass_residual_bbl"] == 0 for row in rows),
        "seller_offers_physical": all(
            seller["final_offer_bbl"] <= seller["physical_available_bbl"]
            for row in rows for seller in row["seller_offers"].values()
        ),
        "same_turn_reverse_switch_count": reverse_cycles,
        "same_turn_opposite_inventory_action_count": opposite_inventory,
        "max_iteration_stop_count": sum(row["stop_reason"] == "max_iterations" for row in rows),
        "mean_iteration_count": statistics.mean(row["iteration_count"] for row in rows) if rows else None,
        "maximum_iteration_count": max((row["iteration_count"] for row in rows), default=None),
        "all_firm_locks_recorded": all(row["firm_lock_recorded"] for row in rows),
        "total_unserved_bbl": sum(sum(row["unserved_bbl"].values()) for row in rows),
        "mean_abs_buyer_quantity_deviation_fraction": statistics.mean(deviations) if deviations else None,
        "maximum_abs_buyer_quantity_deviation_fraction": max(deviations, default=None),
        "inventory_cap_saturation_turns": saturation,
        "inventory_cap_saturation_fraction": saturation / len(rows) if rows else None,
        "execution_path_hash": sha256_json(rows),
        "final_state_hash": session.state.identity,
    }
    if keep_turns:
        summary["turns"] = rows
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", default="0,1,42")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("stage6c-preview3-audit.json"))
    parser.add_argument("--turns-output", type=Path)
    args = parser.parse_args()
    results = []
    for seed in map(int, args.seeds.split(",")):
        result = replay(seed, args.years, keep_turns=seed == 42 and args.turns_output is not None)
        if "turns" in result:
            args.turns_output.parent.mkdir(parents=True, exist_ok=True)
            args.turns_output.write_text(
                json.dumps(result["turns"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            result.pop("turns")
        results.append(result)
        print(
            f"Seed {seed}: {result['committed_turns']}/{result['input_turns']} committed; "
            f"failure={result['failure']}",
            file=sys.stderr,
            flush=True,
        )
    deterministic_a = replay(42, args.years, max_turns=72)
    deterministic_b = replay(42, args.years, max_turns=72)
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "asset_simulation.tests.test_preview3_bilateral_market"
    )
    tested = unittest.TestResult()
    suite.run(tested)
    gates = {
        "preview3_contract_tests": tested.wasSuccessful(),
        "all_main_linked_turns_commit": all(r["failure"] is None for r in results),
        "main_source_is_read_only": all(r["main_link"]["main_source_unchanged"] for r in results),
        "physical_mass_conserved": all(r["mass_conserved"] for r in results),
        "seller_offer_never_exceeds_physical_oil": all(r["seller_offers_physical"] for r in results),
        "no_same_turn_reverse_source_switch": all(r["same_turn_reverse_switch_count"] == 0 for r in results),
        "no_same_turn_opposite_inventory_action": all(
            r["same_turn_opposite_inventory_action_count"] == 0 for r in results
        ),
        "no_max_iteration_stop": all(r["max_iteration_stop_count"] == 0 for r in results),
        "firm_route_locks_recorded": all(r["all_firm_locks_recorded"] for r in results),
        "ordinary_inventory_cap_not_mechanical": all(
            r["inventory_cap_saturation_fraction"] is not None
            and r["inventory_cap_saturation_fraction"] < 0.25 for r in results
        ),
        "deterministic_72_turn_prefix": deterministic_a["execution_path_hash"] == deterministic_b["execution_path_hash"],
    }
    output = {
        "model": "main-stage6c-preview3-bilateral-market-v0.1.0",
        "branch": "main-6Cpreview3",
        "results": results,
        "contract_test_count": tested.testsRun,
        "contract_test_failures": [
            (str(test), trace) for test, trace in (*tested.failures, *tested.errors)
        ],
        "gates": gates,
        "all_gates_pass": all(gates.values()),
        "scope": [
            "Main's crude route world is a read-only natural cargo and physical release input.",
            "Preview3 remains the Gulf/West Africa to East Asia isolated market, not global 25-OD execution.",
            "Seller behavior is quantity willingness only; no crude price, cost, grade or exporter profit model.",
            "Indicative plans are free; one firm ship/cargo plan is locked until commit or turn-consuming cancellation.",
        ],
    }
    output["audit_hash"] = sha256_json(output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"gates": gates, "all_gates_pass": output["all_gates_pass"]}))
    if not output["all_gates_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
