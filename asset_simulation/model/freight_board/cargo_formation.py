"""Stage6C-preview2 cargo formation over a frozen Stage6C-preview BoardSnapshot.

The upstream/natural cargo plan is a prior, not a hard OD share. This layer may
defer some imports into destination inventory, refill inventory, or reallocate
cargo between origins in small transparent steps. It never selects ships and it
never mutates the frozen board.
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
from .types import BoardSnapshot, TrialResult, canonical

VERSION = 'stage6c-preview2-dynamic-cargo-formation-v0.1.0'
CONFIG = Path(__file__).resolve().parents[2] / 'config/stage6c_preview2_v0.1.json'


@dataclass(frozen=True)
class CargoFormationSpec:
    step_bbl: int
    max_iterations: int
    planning_horizon_turns: int
    inventory_shadow_sensitivity: float
    source_switch_threshold_fraction: float
    maximum_source_reallocation_fraction: float
    maximum_inventory_swing_fraction: float
    config_json: str

    @property
    def identity(self) -> str:
        return sha256_json(asdict(self))

    def config(self) -> dict:
        return json.loads(self.config_json)


@dataclass(frozen=True)
class CargoFormationResult:
    snapshot_id: str
    natural_cargo_plan: tuple[tuple[str, int], ...]
    final_cargo_plan: tuple[tuple[str, int], ...]
    final_trial: TrialResult
    report_json: str

    def report(self) -> dict:
        return json.loads(self.report_json)

    @property
    def identity(self) -> str:
        return sha256_json({
            'snapshot': self.snapshot_id,
            'natural': self.natural_cargo_plan,
            'final': self.final_cargo_plan,
            'trial': self.final_trial.identity,
            'report': self.report(),
        })


def make_cargo_formation_spec(config: Mapping | None = None) -> CargoFormationSpec:
    cfg = json.loads(CONFIG.read_text(encoding='utf-8')) if config is None else json.loads(canonical(config))
    if cfg.get('model_version') != VERSION:
        raise ValueError('wrong cargo formation version')
    integer(cfg['step_bbl'], 'cargo formation step')
    integer(cfg['max_iterations'], 'cargo formation iterations')
    integer(cfg['planning_horizon_turns'], 'cargo planning horizon')
    if cfg['step_bbl'] <= 0 or not 1 <= cfg['max_iterations'] <= 1000 or cfg['planning_horizon_turns'] <= 0:
        raise ValueError('invalid positive cargo formation controls')
    sens = finite_signed(cfg['inventory_shadow_sensitivity'], 'inventory shadow sensitivity')
    threshold = finite_signed(cfg['source_switch_threshold_fraction'], 'source switch threshold')
    source_budget = finite_signed(cfg['maximum_source_reallocation_fraction'], 'source reallocation')
    inventory_budget = finite_signed(cfg['maximum_inventory_swing_fraction'], 'inventory swing')
    if not 0 <= sens <= 5 or not 0 <= threshold <= 2:
        raise ValueError('invalid cargo formation sensitivity or threshold')
    if not 0 <= source_budget <= 1 or not 0 <= inventory_budget <= 1:
        raise ValueError('cargo formation fractions must be in [0,1]')
    return CargoFormationSpec(
        cfg['step_bbl'], cfg['max_iterations'], cfg['planning_horizon_turns'],
        sens, threshold, source_budget, inventory_budget, canonical(cfg)
    )


def _ordered_plan(snapshot: BoardSnapshot, plan: Mapping[str, int]) -> dict[str, int]:
    if set(plan) != set(snapshot.spec.origins):
        raise ValueError('natural cargo plan must identify every preview origin')
    out = {}
    for o in snapshot.spec.origins:
        integer(plan[o], 'natural cargo plan barrels')
        out[o] = plan[o]
    return out


def _physical_available(snapshot: BoardSnapshot) -> dict[str, int]:
    out = {}
    releases = dict(snapshot.releases_bbl)
    limits = dict(snapshot.export_limits_bbl)
    for o in snapshot.spec.origins:
        ready = sum(b.remaining_bbl for b in snapshot.opened_market.batches if b.origin == o) + releases[o]
        out[o] = min(ready, limits[o])
    return out


def _inventory_signal(report: dict, horizon: int) -> float:
    dest = report['inventory']['destination']
    values = [dest['opening']['soft_pressure']]
    for row in dest['future_path']:
        if row['turn'] <= report['routes'][next(iter(report['routes']))]['turn'] + horizon:
            values.append(row['soft_pressure'])
    return min(values)


def _prices_per_bbl(report: dict, origins: Sequence[str]) -> dict[str, float]:
    return {o: finite_signed(report['routes'][o]['net_service_value_real_usd_per_bbl'], 'route service value')
            for o in origins}


def _anchor_price(prices: Mapping[str, float], natural: Mapping[str, int]) -> float:
    total = sum(natural.values())
    if total:
        value = sum(prices[o] * natural[o] for o in natural) / total
    else:
        value = sum(prices.values()) / max(1, len(prices))
    return max(value, 1e-12)


def _feasibility_key(report: dict) -> tuple[int, int, float]:
    violations = report['inventory']['hard_bound_violations']
    dest_violations = sum(v.startswith('destination:') for v in violations)
    source_violations = len(violations) - dest_violations
    path = report['inventory']['destination']['future_path']
    minimum = min((r['deviation_bbl'] for r in path), default=0)
    return (-dest_violations, -source_violations, minimum)


def _trial(snapshot: BoardSnapshot, ship_plan: Mapping[str, Sequence[int]], cargo: Mapping[str, int],
           ballast_orders: Sequence[BallastOrder]) -> TrialResult:
    return quote_trial(snapshot, ship_plan, cargo, ballast_orders=tuple(ballast_orders))


def form_cargo_plan(
    snapshot: BoardSnapshot,
    trial_ship_plan: Mapping[str, Sequence[int]],
    natural_cargo_plan: Mapping[str, int],
    *,
    ballast_orders: Sequence[BallastOrder] = (),
    formation_spec: CargoFormationSpec | None = None,
) -> CargoFormationResult:
    """Return a pure, deterministic cargo-plan adjustment against one frozen board.

    The caller supplies the current ship intentions. This function changes only
    cargo intentions. Re-running it with a different ship plan on the same
    snapshot is how cargo and tonnage interact without hidden ship optimization.
    """
    formation = make_cargo_formation_spec() if formation_spec is None else formation_spec
    natural = _ordered_plan(snapshot, natural_cargo_plan)
    if set(trial_ship_plan) != set(snapshot.spec.origins):
        raise ValueError('ship plan must identify every preview origin')
    available = _physical_available(snapshot)
    origins = snapshot.spec.origins
    natural_total = sum(natural.values())
    lower_total = max(0, natural_total - round(natural_total * formation.maximum_inventory_swing_fraction))
    upper_total = natural_total + round(natural_total * formation.maximum_inventory_swing_fraction)
    source_switch_budget = round(natural_total * formation.maximum_source_reallocation_fraction)

    plan = {o: min(natural[o], available[o]) for o in origins}
    forced_shortfall = natural_total - sum(plan.values())
    source_reallocated = 0
    seen = set()
    ledger = []
    stop_reason = 'max_iterations'

    for iteration in range(formation.max_iterations):
        key = tuple(plan[o] for o in origins)
        if key in seen:
            stop_reason = 'cycle_detected'
            break
        seen.add(key)
        current = _trial(snapshot, trial_ship_plan, plan, ballast_orders)
        report = current.report()
        prices = _prices_per_bbl(report, origins)
        anchor = _anchor_price(prices, natural)
        signal = _inventory_signal(report, formation.planning_horizon_turns)
        shadow = anchor * math.exp(-formation.inventory_shadow_sensitivity * signal)
        total = sum(plan.values())
        spare = {o: max(0, available[o] - plan[o]) for o in origins}
        action = None
        candidate = None

        receivers = [o for o in origins if spare[o] > 0]
        if receivers and total < upper_total and (not report['valid'] or signal < 0):
            receiver = min(receivers, key=lambda o: (prices[o], origins.index(o)))
            # If stock is below normal and a source shortage pushed current
            # orders below the natural total, restore that import quantity
            # before asking whether extra inventory build is worth its freight.
            restore_natural = signal < 0 and total < natural_total
            if not report['valid'] or restore_natural or prices[receiver] <= shadow:
                add = min(formation.step_bbl, spare[receiver], upper_total - total)
                if restore_natural:
                    add = min(add, natural_total - total)
                if add:
                    candidate = dict(plan)
                    candidate[receiver] += add
                    action = {'kind': 'forced_shortfall_replacement' if restore_natural else 'inventory_replenishment',
                              'origin': receiver, 'bbl': add}

        if candidate is None and report['valid'] and signal > 0 and total > lower_total:
            donors = [o for o in origins if plan[o] > 0]
            if donors:
                donor = max(donors, key=lambda o: (prices[o], -origins.index(o)))
                if prices[donor] > shadow:
                    remove = min(formation.step_bbl, plan[donor], total - lower_total)
                    if remove:
                        test = dict(plan)
                        test[donor] -= remove
                        trial = _trial(snapshot, trial_ship_plan, test, ballast_orders)
                        if trial.report()['valid']:
                            candidate = test
                            action = {'kind': 'inventory_deferral', 'origin': donor, 'bbl': remove}

        if candidate is None and source_reallocated < source_switch_budget:
            donors = [o for o in origins if plan[o] > 0]
            receivers = [o for o in origins if spare[o] > 0]
            if donors and receivers:
                donor = max(donors, key=lambda o: (prices[o], -origins.index(o)))
                receiver = min(receivers, key=lambda o: (prices[o], origins.index(o)))
                gap = prices[donor] - prices[receiver]
                if donor != receiver and gap / anchor >= formation.source_switch_threshold_fraction:
                    move = min(formation.step_bbl, plan[donor], spare[receiver],
                               source_switch_budget - source_reallocated)
                    if move:
                        test = dict(plan)
                        test[donor] -= move
                        test[receiver] += move
                        trial = _trial(snapshot, trial_ship_plan, test, ballast_orders)
                        if _feasibility_key(trial.report()) >= _feasibility_key(report):
                            candidate = test
                            action = {'kind': 'source_switch', 'from': donor, 'to': receiver, 'bbl': move}

        ledger.append({
            'iteration': iteration,
            'cargo_plan_bbl': {o: plan[o] for o in origins},
            'route_service_value_real_usd_per_bbl': prices,
            'inventory_signal': signal,
            'inventory_shadow_value_real_usd_per_bbl': shadow,
            'trial_valid': report['valid'],
            'hard_bound_violations': report['inventory']['hard_bound_violations'],
            'action': action,
        })
        if candidate is None:
            stop_reason = 'no_improving_transparent_step'
            break
        if action and action['kind'] == 'source_switch':
            source_reallocated += action['bbl']
        plan = candidate

    final_trial = _trial(snapshot, trial_ship_plan, plan, ballast_orders)
    final_report = final_trial.report()
    final_total = sum(plan.values())
    unserved = {o: final_report['routes'][o]['allocation']['unserved_trial_cargo_bbl'] for o in origins}
    formed = {
        'model_version': VERSION,
        'snapshot_id': snapshot.snapshot_id,
        'formation_spec_hash': formation.identity,
        'natural_cargo_plan_bbl': {o: natural[o] for o in origins},
        'natural_total_bbl': natural_total,
        'physical_available_bbl': available,
        'forced_source_shortfall_bbl': forced_shortfall,
        'final_cargo_plan_bbl': {o: plan[o] for o in origins},
        'final_total_bbl': final_total,
        'inventory_draw_vs_natural_bbl': max(0, natural_total - final_total),
        'inventory_build_vs_natural_bbl': max(0, final_total - natural_total),
        'source_reallocated_bbl': source_reallocated,
        'opening_destination_inventory': final_report['inventory']['destination']['opening'],
        'final_inventory_path': final_report['inventory']['destination']['future_path'],
        'final_route_service_value_real_usd_per_bbl': _prices_per_bbl(final_report, origins),
        'final_trial_valid': final_report['valid'],
        'unserved_trial_cargo_bbl': unserved,
        'needs_ship_response': (not final_report['valid']) or any(unserved.values()),
        'stop_reason': stop_reason,
        'iterations': ledger,
        'contracts': {
            'opening_inventory_is_frozen_committed_reality': True,
            'trial_arrivals_change_future_projection_not_opening_stock': True,
            'natural_OD_plan_is_a_prior_not_a_hard_share': True,
            'import_requirements_are_not_deleted_or_rewritten': True,
            'source_oil_cannot_teleport': True,
            'ship_plan_is_read_only_external_input': True,
            'no_hidden_ship_optimizer': True,
            'no_cost_or_crude_price_model': True,
            'no_demand_destruction': True
        }
    }
    return CargoFormationResult(
        snapshot.snapshot_id,
        tuple((o, natural[o]) for o in origins),
        tuple((o, plan[o]) for o in origins),
        final_trial,
        canonical(formed),
    )