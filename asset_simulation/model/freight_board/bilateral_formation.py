"""Preview3 two-sided cargo formation over one frozen freight-board snapshot.

The buyer owns import timing and source choice. Sellers own a bounded offer
made from physical oil: a stable term component plus an inventory-responsive
spot component. Neither side creates oil, changes upstream demand, or chooses
ships. Every candidate is a pure quote against the same BoardSnapshot.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

from ..global_shipping_contract import integer
from ..multi_origin_pricing import finite_signed
from ..registry import sha256_json
from ..shipping_v3.types import BallastOrder
from .board import quote_trial
from .inventory import stock_report
from .types import BoardSnapshot, BoardSpec, TrialResult, canonical, make_board_spec


VERSION = "main-stage6c-preview3-bilateral-market-v0.1.0"
CONFIG = Path(__file__).resolve().parents[2] / "config/stage6c_preview3_v0.1.json"


@dataclass(frozen=True)
class BilateralFormationSpec:
    step_bbl: int
    minimum_step_bbl: int
    max_iterations: int
    planning_horizon_turns: int
    relative_step_fraction: float
    buyer_inventory_shadow_sensitivity: float
    normal_inventory_swing_fraction: float
    emergency_inventory_swing_fraction: float
    emergency_inventory_signal: float
    inventory_reversal_deadband: float
    source_switch_entry_fraction: float
    source_switch_exit_fraction: float
    maximum_source_reallocation_fraction: float
    emergency_source_reallocation_fraction: float
    seller_term_share: float
    seller_spot_offer_headroom_fraction: float
    seller_spot_inventory_response_fraction: float
    seller_offer_persistence: float
    firm_cargo_revision_fraction: float
    board_inventory_log_premium_limit: float
    config_json: str

    @property
    def identity(self) -> str:
        return sha256_json(asdict(self))

    def config(self) -> dict:
        return json.loads(self.config_json)


@dataclass(frozen=True)
class BilateralFormationResult:
    snapshot_id: str
    natural_cargo_plan: tuple[tuple[str, int], ...]
    seller_spot_offer: tuple[tuple[str, int], ...]
    final_cargo_plan: tuple[tuple[str, int], ...]
    final_trial: TrialResult
    report_json: str

    def report(self) -> dict:
        return json.loads(self.report_json)

    @property
    def identity(self) -> str:
        return sha256_json({
            "snapshot": self.snapshot_id,
            "natural": self.natural_cargo_plan,
            "seller_spot": self.seller_spot_offer,
            "final": self.final_cargo_plan,
            "trial": self.final_trial.identity,
            "report": self.report(),
        })


def make_bilateral_formation_spec(config: Mapping | None = None) -> BilateralFormationSpec:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if config is None else json.loads(canonical(config))
    if cfg.get("model_version") != VERSION:
        raise ValueError("wrong Preview3 bilateral formation version")
    for key in ("step_bbl", "minimum_step_bbl", "max_iterations", "planning_horizon_turns"):
        integer(cfg[key], key)
    if not 0 < cfg["minimum_step_bbl"] <= cfg["step_bbl"]:
        raise ValueError("Preview3 steps must be positive and ordered")
    if not 1 <= cfg["max_iterations"] <= 1000 or cfg["planning_horizon_turns"] <= 0:
        raise ValueError("invalid Preview3 iteration or horizon control")
    names = (
        "relative_step_fraction", "buyer_inventory_shadow_sensitivity", "normal_inventory_swing_fraction",
        "emergency_inventory_swing_fraction", "emergency_inventory_signal",
        "inventory_reversal_deadband",
        "source_switch_entry_fraction", "source_switch_exit_fraction",
        "maximum_source_reallocation_fraction", "emergency_source_reallocation_fraction",
        "seller_term_share", "seller_spot_offer_headroom_fraction",
        "seller_spot_inventory_response_fraction",
        "seller_offer_persistence", "firm_cargo_revision_fraction",
        "board_inventory_log_premium_limit",
    )
    values = {name: finite_signed(cfg[name], name) for name in names}
    if not 0 < values["relative_step_fraction"] <= 0.05:
        raise ValueError("relative step fraction must be in (0,0.05]")
    if not 0 <= values["buyer_inventory_shadow_sensitivity"] <= 5:
        raise ValueError("buyer inventory sensitivity must be in [0,5]")
    fractions = names[2:]
    if any(not 0 <= values[name] <= 1 for name in fractions):
        raise ValueError("Preview3 fractions and signals must be in [0,1]")
    if values["normal_inventory_swing_fraction"] > values["emergency_inventory_swing_fraction"]:
        raise ValueError("emergency inventory swing must cover the normal swing")
    if values["source_switch_exit_fraction"] >= values["source_switch_entry_fraction"]:
        raise ValueError("source-switch exit threshold must be below entry threshold")
    if values["maximum_source_reallocation_fraction"] > values["emergency_source_reallocation_fraction"]:
        raise ValueError("emergency source budget must cover the normal source budget")
    return BilateralFormationSpec(
        step_bbl=cfg["step_bbl"],
        minimum_step_bbl=cfg["minimum_step_bbl"],
        max_iterations=cfg["max_iterations"],
        planning_horizon_turns=cfg["planning_horizon_turns"],
        config_json=canonical(cfg),
        **values,
    )


def make_preview3_board_spec(*, formation_spec: BilateralFormationSpec | None = None) -> BoardSpec:
    """Build the inherited board with Preview3's reduced direct stock premium.

    Inventory primarily changes buyer and seller quantities in Preview3. The
    smaller remaining direct quote term represents residual fixture urgency and
    avoids counting the same stock anxiety at full strength twice.
    """
    formation = make_bilateral_formation_spec() if formation_spec is None else formation_spec
    parent_path = Path(__file__).resolve().parents[2] / "config/stage6c_preview_v0.1.json"
    board_config = json.loads(parent_path.read_text(encoding="utf-8"))
    board_config["inventory_log_premium_limit"] = formation.board_inventory_log_premium_limit
    return make_board_spec(config=board_config)


def _ordered_nonnegative(snapshot: BoardSnapshot, values: Mapping[str, int], name: str) -> dict[str, int]:
    if set(values) != set(snapshot.spec.origins):
        raise ValueError(f"{name} must identify every Preview3 origin")
    out = {}
    for origin in snapshot.spec.origins:
        integer(values[origin], f"{name} barrels")
        out[origin] = values[origin]
    return out


def _physical_available(snapshot: BoardSnapshot) -> dict[str, int]:
    releases = dict(snapshot.releases_bbl)
    limits = dict(snapshot.export_limits_bbl)
    return {
        origin: min(
            sum(batch.remaining_bbl for batch in snapshot.opened_market.batches if batch.origin == origin)
            + releases[origin],
            limits[origin],
        )
        for origin in snapshot.spec.origins
    }


def form_seller_offers(
    snapshot: BoardSnapshot,
    natural_cargo_plan: Mapping[str, int],
    *,
    previous_spot_offer_bbl: Mapping[str, int] | None = None,
    formation_spec: BilateralFormationSpec | None = None,
) -> dict[str, dict]:
    """Return deterministic term/spot offers without changing physical supply."""
    formation = make_bilateral_formation_spec() if formation_spec is None else formation_spec
    natural = _ordered_nonnegative(snapshot, natural_cargo_plan, "natural cargo plan")
    previous = None if previous_spot_offer_bbl is None else _ordered_nonnegative(
        snapshot, previous_spot_offer_bbl, "previous seller spot offer"
    )
    physical = _physical_available(snapshot)
    bands = dict(snapshot.spec.source_bands)
    rows = {}
    for origin in snapshot.spec.origins:
        opening = sum(
            batch.remaining_bbl for batch in snapshot.opened_market.batches if batch.origin == origin
        )
        signal = stock_report(opening, bands[origin])["soft_pressure"]
        term = round(natural[origin] * formation.seller_term_share)
        base_spot = natural[origin] - term
        normal_spot = round(base_spot * (1 + formation.seller_spot_offer_headroom_fraction))
        target_spot = max(0, round(normal_spot * (
            1 + formation.seller_spot_inventory_response_fraction * signal
        )))
        prior = normal_spot if previous is None else previous[origin]
        spot = max(0, round(
            formation.seller_offer_persistence * prior
            + (1 - formation.seller_offer_persistence) * target_spot
        ))
        offered = min(physical[origin], term + spot)
        # If physical oil is short, term cargo consumes the available amount
        # first. No contractual label can create unavailable barrels.
        effective_term = min(term, offered)
        effective_spot = offered - effective_term
        rows[origin] = {
            "natural_cargo_bbl": natural[origin],
            "opening_source_stock_bbl": opening,
            "source_inventory_signal": signal,
            "term_offer_bbl": effective_term,
            "base_spot_offer_bbl": base_spot,
            "normal_spot_offer_with_headroom_bbl": normal_spot,
            "previous_spot_offer_bbl": prior,
            "target_spot_offer_bbl": target_spot,
            "spot_offer_bbl": effective_spot,
            "physical_available_bbl": physical[origin],
            "final_offer_bbl": offered,
            "unsatisfied_term_bbl": max(0, term - effective_term),
        }
    return rows


def _inventory_signal(report: dict, horizon: int) -> float:
    destination = report["inventory"]["destination"]
    turn = next(iter(report["routes"].values()))["turn"]
    values = [destination["opening"]["soft_pressure"]]
    values.extend(
        row["soft_pressure"] for row in destination["future_path"]
        if row["turn"] <= turn + horizon
    )
    return min(values)


def _route_prices(report: dict, origins: Sequence[str]) -> dict[str, float]:
    return {
        origin: finite_signed(
            report["routes"][origin]["net_service_value_real_usd_per_bbl"],
            "route service value",
        )
        for origin in origins
    }


def _anchor_price(prices: Mapping[str, float], natural: Mapping[str, int]) -> float:
    total = sum(natural.values())
    if total:
        value = sum(prices[origin] * natural[origin] for origin in natural) / total
    else:
        value = sum(prices.values()) / max(1, len(prices))
    return max(value, 1e-12)


def structured_hard_violations(report: dict) -> tuple[dict, ...]:
    """Expose node, direction and magnitude; string-only violations are unsafe."""
    rows = []
    for origin, item in report["inventory"]["sources"].items():
        closing = item["closing"]
        lower = closing["normal_bbl"] + closing["bounds"]["hard_low_bbl"]
        upper = closing["normal_bbl"] + closing["bounds"]["hard_high_bbl"]
        if closing["stock_bbl"] < lower:
            rows.append({"node": "source", "origin": origin, "turn": None,
                         "direction": "below_lower", "excess_bbl": lower - closing["stock_bbl"]})
        elif closing["stock_bbl"] > upper:
            rows.append({"node": "source", "origin": origin, "turn": None,
                         "direction": "above_upper", "excess_bbl": closing["stock_bbl"] - upper})
    for item in report["inventory"]["destination"]["future_path"]:
        lower = item["normal_bbl"] + item["bounds"]["hard_low_bbl"]
        upper = item["normal_bbl"] + item["bounds"]["hard_high_bbl"]
        if item["stock_bbl"] < lower:
            rows.append({"node": "destination", "origin": None, "turn": item["turn"],
                         "direction": "below_lower", "excess_bbl": lower - item["stock_bbl"]})
        elif item["stock_bbl"] > upper:
            rows.append({"node": "destination", "origin": None, "turn": item["turn"],
                         "direction": "above_upper", "excess_bbl": item["stock_bbl"] - upper})
    return tuple(rows)


def _hard_score(report: dict) -> tuple[int, int]:
    violations = structured_hard_violations(report)
    return len(violations), sum(row["excess_bbl"] for row in violations)


def _trial(
    snapshot: BoardSnapshot,
    ship_plan: Mapping[str, Sequence[int]],
    cargo: Mapping[str, int],
    ballast_orders: Sequence[BallastOrder],
) -> TrialResult:
    return quote_trial(snapshot, ship_plan, cargo, ballast_orders=tuple(ballast_orders))


def _step_sizes(formation: BilateralFormationSpec, natural_total: int):
    relative = round(natural_total * formation.relative_step_fraction)
    quantum = formation.minimum_step_bbl
    relative = max(quantum, round(relative / quantum) * quantum)
    step = max(formation.step_bbl, relative)
    yielded = set()
    while step >= formation.minimum_step_bbl:
        if step not in yielded:
            yielded.add(step)
            yield step
        if step == formation.minimum_step_bbl:
            break
        step = max(formation.minimum_step_bbl, step // 2)


def form_bilateral_cargo_plan(
    snapshot: BoardSnapshot,
    trial_ship_plan: Mapping[str, Sequence[int]],
    natural_cargo_plan: Mapping[str, int],
    *,
    previous_spot_offer_bbl: Mapping[str, int] | None = None,
    ballast_orders: Sequence[BallastOrder] = (),
    formation_spec: BilateralFormationSpec | None = None,
) -> BilateralFormationResult:
    """Form a bounded buyer plan against explicit seller offers and one snapshot."""
    formation = make_bilateral_formation_spec() if formation_spec is None else formation_spec
    natural = _ordered_nonnegative(snapshot, natural_cargo_plan, "natural cargo plan")
    if set(trial_ship_plan) != set(snapshot.spec.origins):
        raise ValueError("ship plan must identify every Preview3 origin")
    sellers = form_seller_offers(
        snapshot, natural, previous_spot_offer_bbl=previous_spot_offer_bbl,
        formation_spec=formation,
    )
    offers = {origin: sellers[origin]["final_offer_bbl"] for origin in snapshot.spec.origins}
    term_floors = {origin: sellers[origin]["term_offer_bbl"] for origin in snapshot.spec.origins}
    origins = snapshot.spec.origins
    natural_total = sum(natural.values())
    plan = {origin: min(natural[origin], offers[origin]) for origin in origins}
    seen = {tuple(plan[origin] for origin in origins)}
    source_reallocated = 0
    inventory_direction = None
    ledger = []
    stop_reason = "max_iterations"

    for iteration in range(formation.max_iterations):
        current = _trial(snapshot, trial_ship_plan, plan, ballast_orders)
        report = current.report()
        hard_before = _hard_score(report)
        prices = _route_prices(report, origins)
        anchor = _anchor_price(prices, natural)
        signal = _inventory_signal(report, formation.planning_horizon_turns)
        shadow = anchor * math.exp(-formation.buyer_inventory_shadow_sensitivity * signal)
        emergency = hard_before[0] > 0 or abs(signal) >= formation.emergency_inventory_signal
        inventory_budget = (
            formation.emergency_inventory_swing_fraction if emergency
            else formation.normal_inventory_swing_fraction
        )
        source_budget_fraction = (
            formation.emergency_source_reallocation_fraction if emergency
            else formation.maximum_source_reallocation_fraction
        )
        lower_total = max(0, natural_total - round(natural_total * inventory_budget))
        upper_total = natural_total + round(natural_total * inventory_budget)
        source_budget = round(natural_total * source_budget_fraction)
        total = sum(plan.values())
        candidate = None
        action = None

        def consider(test: dict[str, int], proposed: dict, *, require_valid: bool = False):
            nonlocal candidate, action
            key = tuple(test[origin] for origin in origins)
            if key in seen:
                return False
            trial = _trial(snapshot, trial_ship_plan, test, ballast_orders)
            score = _hard_score(trial.report())
            kind = proposed["kind"]
            next_signal = _inventory_signal(trial.report(), formation.planning_horizon_turns)
            if kind == "buyer_inventory_replenishment" and next_signal > formation.inventory_reversal_deadband:
                return False
            if kind == "buyer_inventory_deferral" and next_signal < -formation.inventory_reversal_deadband:
                return False
            if hard_before[0]:
                acceptable = score < hard_before
            else:
                acceptable = score == (0, 0) and (not require_valid or trial.report()["valid"])
            if acceptable:
                candidate, action = test, proposed
                return True
            return False

        # Invalid states are repaired strictly by reduced violation count/volume.
        # Unlike Preview2, "invalid" never means "add cargo" without direction.
        if hard_before[0]:
            options = []
            for step in _step_sizes(formation, natural_total):
                for origin in origins:
                    if plan[origin] < offers[origin] and total < upper_total:
                        amount = min(step, offers[origin] - plan[origin], upper_total - total)
                        if amount:
                            test = dict(plan); test[origin] += amount
                            trial = _trial(snapshot, trial_ship_plan, test, ballast_orders)
                            options.append((_hard_score(trial.report()), 0, origins.index(origin), test, trial,
                                            {"kind": "hard_bound_repair_add", "origin": origin, "bbl": amount}))
                    if plan[origin] > 0 and total > lower_total:
                        # A hard inventory violation may override the soft term
                        # floor; the exception stays explicit in the action log.
                        amount = min(step, plan[origin], total - lower_total)
                        if amount:
                            test = dict(plan); test[origin] -= amount
                            trial = _trial(snapshot, trial_ship_plan, test, ballast_orders)
                            options.append((_hard_score(trial.report()), 1, origins.index(origin), test, trial,
                                            {"kind": "hard_bound_repair_remove", "origin": origin, "bbl": amount}))
            improving = [row for row in options if row[0] < hard_before
                         and tuple(row[3][origin] for origin in origins) not in seen]
            if improving:
                _, _, _, candidate, _, action = min(improving, key=lambda row: row[:3])

        # Valid low stocks may add only if the buyer's shadow value covers freight.
        elif signal < 0 and inventory_direction != "remove" and total < upper_total:
            receivers = sorted(
                (origin for origin in origins if plan[origin] < offers[origin]),
                key=lambda origin: (prices[origin], origins.index(origin)),
            )
            for origin in receivers:
                if prices[origin] > shadow:
                    continue
                for step in _step_sizes(formation, natural_total):
                    amount = min(step, offers[origin] - plan[origin], upper_total - total)
                    if amount:
                        test = dict(plan); test[origin] += amount
                        if consider(test, {"kind": "buyer_inventory_replenishment", "origin": origin,
                                           "bbl": amount}, require_valid=True):
                            break
                if candidate is not None:
                    break

        # Valid high stocks defer the most expensive cargo without breaking bounds.
        elif signal > 0 and inventory_direction != "add" and total > lower_total:
            donors = sorted(
                (origin for origin in origins if plan[origin] > term_floors[origin]),
                key=lambda origin: (-prices[origin], origins.index(origin)),
            )
            for origin in donors:
                if prices[origin] <= shadow:
                    continue
                for step in _step_sizes(formation, natural_total):
                    amount = min(step, plan[origin] - term_floors[origin], total - lower_total)
                    if amount:
                        test = dict(plan); test[origin] -= amount
                        if consider(test, {"kind": "buyer_inventory_deferral", "origin": origin,
                                           "bbl": amount}, require_valid=True):
                            break
                if candidate is not None:
                    break

        # Source switching preserves total imports and cannot exceed the seller
        # offer, the source budget, or the feasibility of the current plan.
        if candidate is None and source_reallocated < source_budget:
            donor = max(origins, key=lambda origin: (prices[origin], -origins.index(origin)))
            receiver = min(origins, key=lambda origin: (prices[origin], origins.index(origin)))
            gap = prices[donor] - prices[receiver]
            if (donor != receiver and plan[donor] > term_floors[donor] and plan[receiver] < offers[receiver]
                    and gap / anchor >= formation.source_switch_entry_fraction):
                for step in _step_sizes(formation, natural_total):
                    amount = min(step, plan[donor] - term_floors[donor], offers[receiver] - plan[receiver],
                                 source_budget - source_reallocated)
                    if not amount:
                        continue
                    test = dict(plan); test[donor] -= amount; test[receiver] += amount
                    key = tuple(test[origin] for origin in origins)
                    if key in seen:
                        continue
                    trial = _trial(snapshot, trial_ship_plan, test, ballast_orders)
                    trial_report = trial.report()
                    trial_prices = _route_prices(trial_report, origins)
                    reverse_gap = trial_prices[receiver] - trial_prices[donor]
                    trial_anchor = _anchor_price(trial_prices, natural)
                    # A step that immediately creates a material reverse signal
                    # is too large; retry with a smaller transparent increment.
                    if reverse_gap / trial_anchor >= formation.source_switch_exit_fraction \
                            and amount > formation.minimum_step_bbl:
                        continue
                    if _hard_score(trial_report) <= hard_before:
                        candidate = test
                        action = {"kind": "source_switch", "from": donor, "to": receiver,
                                  "bbl": amount}
                        break

        ledger.append({
            "iteration": iteration,
            "cargo_plan_bbl": {origin: plan[origin] for origin in origins},
            "seller_offer_bbl": offers,
            "route_service_value_real_usd_per_bbl": prices,
            "inventory_signal": signal,
            "inventory_shadow_value_real_usd_per_bbl": shadow,
            "emergency_controls_active": emergency,
            "hard_violations": structured_hard_violations(report),
            "action": action,
        })
        if candidate is None:
            stop_reason = "no_strictly_improving_transparent_step"
            break
        if action and action["kind"] == "source_switch":
            source_reallocated += action["bbl"]
        elif action and action["kind"] == "buyer_inventory_replenishment":
            inventory_direction = "add"
        elif action and action["kind"] == "buyer_inventory_deferral":
            inventory_direction = "remove"
        plan = candidate
        seen.add(tuple(plan[origin] for origin in origins))

    final_trial = _trial(snapshot, trial_ship_plan, plan, ballast_orders)
    final_report = final_trial.report()
    final_total = sum(plan.values())
    result = {
        "model_version": VERSION,
        "snapshot_id": snapshot.snapshot_id,
        "formation_spec_hash": formation.identity,
        "natural_cargo_plan_bbl": natural,
        "natural_total_bbl": natural_total,
        "seller_offers": sellers,
        "seller_offer_bbl": offers,
        "final_cargo_plan_bbl": plan,
        "final_total_bbl": final_total,
        "inventory_draw_vs_natural_bbl": max(0, natural_total - final_total),
        "inventory_build_vs_natural_bbl": max(0, final_total - natural_total),
        "source_reallocated_bbl": source_reallocated,
        "final_hard_violations": structured_hard_violations(final_report),
        "final_trial_valid": final_report["valid"],
        "unserved_trial_cargo_bbl": {
            origin: final_report["routes"][origin]["allocation"]["unserved_trial_cargo_bbl"]
            for origin in origins
        },
        "needs_ship_response": (not final_report["valid"]) or any(
            final_report["routes"][origin]["allocation"]["unserved_trial_cargo_bbl"]
            for origin in origins
        ),
        "stop_reason": stop_reason,
        "iterations": ledger,
        "contracts": {
            "main_world_is_read_only": True,
            "seller_offer_cannot_exceed_physical_oil_or_export_limit": True,
            "term_label_cannot_create_oil": True,
            "buyer_inventory_changes_timing_not_import_requirements": True,
            "invalidity_is_repaired_only_by_strictly_lower_violation_severity": True,
            "no_hidden_ship_optimizer": True,
            "no_crude_price_or_cost_model": True,
        },
    }
    return BilateralFormationResult(
        snapshot.snapshot_id,
        tuple((origin, natural[origin]) for origin in origins),
        tuple((origin, sellers[origin]["spot_offer_bbl"]) for origin in origins),
        tuple((origin, plan[origin]) for origin in origins),
        final_trial,
        canonical(result),
    )
