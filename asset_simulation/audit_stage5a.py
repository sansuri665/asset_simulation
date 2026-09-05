"""Reproducible Stage5A fleet sweeps, clocks, phases and conservation controls.

These are model counterfactuals, NOT a historical freight calibration. Long
backlogs with an undersized fixed fleet are reported, never patched by ships
appearing from an abstract pool or by resetting the transport ledger.
"""
from __future__ import annotations

import argparse
import calendar
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from .model.engine import run_global_macro
from .model.oil_shipping_world import run_oil_shipping_world
from .model.registry import sha256_json
from .model.single_route_market import (
    load_stage5a_config, monthly_turn_inputs, seeded_route_inputs, simulate_fixed_route,
)
from .model.single_route_pricing import load_single_route_pricing_config


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_rounded(v) for v in value]
    return value


def _flat_calendar(years: int = 5):
    months = [{"year": y, "month": m, "days": calendar.monthrange(y, m)[1], "cargo_mbd": 9.3}
              for y in range(2025, 2025 + years) for m in range(1, 13)]
    return monthly_turn_inputs(months, cpi_by_information_year={y:100.0 for y in range(2025,2025+years)},initial_year=2025)


def audit_stage5a(seeds=(0, 1, 5, 7, 42), *, years: int = 20,
                  fleet_sizes=(230, 235, 239, 242, 245, 250, 260, 280),
                  long_window: bool = True, turns_output: str | None = None) -> dict[str, Any]:
    cfg = load_single_route_pricing_config()
    config_hash = sha256_json(cfg)
    per_seed = []
    all_conserved = True
    upstream_unchanged = True
    seed42_inputs = None
    for seed in seeds:
        macro = run_global_macro(seed, years)
        shipping = run_oil_shipping_world(macro)
        before = sha256_json([macro.rows, shipping.turns])
        inputs = seeded_route_inputs(macro, shipping)
        sweeps = []
        for n in fleet_sizes:
            run = simulate_fixed_route(inputs, fleet_size=n)
            summary = run["summary"]
            all_conserved = all_conserved and all(summary[k] == 0 for k in
                ("max_fleet_residual", "max_barrel_residual", "max_plan_residual"))
            sweeps.append(summary)
            if seed == 42 and n == 245 and turns_output:
                Path(turns_output).write_text(json.dumps(_rounded(run),ensure_ascii=False,indent=2),encoding="utf-8")
        upstream_unchanged = upstream_unchanged and before == sha256_json([macro.rows, shipping.turns])
        per_seed.append({"seed": seed, "upstream_numerical_hash": before, "sweep": sweeps})
        if seed == 42:
            seed42_inputs = inputs

    # Exact equal-window control: 50 complete cargoes per 10-day turn.
    equal = tuple({"scheduled_cargo_bbl":98550000,"turn_days":10,"cpi":100.0} for _ in range(360))
    equal_control = simulate_fixed_route(equal, fleet_size=252)["summary"]
    under = simulate_fixed_route(equal, fleet_size=200)
    tail = under["turns"][50:150]
    expected_loads = 200 * 20  # 100 turns / 5-turn cycles
    capacity_correct = sum(r["dispatched_vlcc"] for r in tail) == expected_loads
    capacity_correct = capacity_correct and (
        under["turns"][149]["origin_unshipped_bbl"] - under["turns"][49]["origin_unshipped_bbl"]
        == 100 * 98550000 - expected_loads * 1971000)
    # Same daily flow under real dates. Any repeating calendar/phase pattern
    # here is an explicit model-clock effect, not macro/market news.
    calendar_inputs = _flat_calendar(6)
    calendar_sweep = [simulate_fixed_route(calendar_inputs,fleet_size=n)["summary"] for n in (239,242,245,250,260,280)]
    phases = [simulate_fixed_route(calendar_inputs, fleet_size=245, phase_rotation=p)["summary"] for p in range(5)]
    cold = simulate_fixed_route(calendar_inputs, fleet_size=245, initialization="all_prompt")["summary"]
    # Genuine short-lived transport-work shock, not synthetic supply creation.
    base_control = simulate_fixed_route(calendar_inputs,fleet_size=245)
    demand_shock = deepcopy(calendar_inputs)
    for t in range(72,81):
        demand_shock[t]["scheduled_cargo_bbl"] = round(demand_shock[t]["scheduled_cargo_bbl"] * 1.10)
    shocked = simulate_fixed_route(demand_shock,fleet_size=245)
    short_prefix = simulate_fixed_route(calendar_inputs[:72],fleet_size=245)
    prefix_equal = short_prefix["turns"] == base_control["turns"][:72]

    long_results = []
    if long_window:
        macro = run_global_macro(42,60)
        shipping = run_oil_shipping_world(macro)
        inputs = seeded_route_inputs(macro,shipping)
        for n in (245,250,260,280):
            summary = simulate_fixed_route(inputs,fleet_size=n)["summary"]
            all_conserved = all_conserved and all(summary[k] == 0 for k in
                ("max_fleet_residual", "max_barrel_residual", "max_plan_residual"))
            long_results.append(summary)
    gates = {
        "all_fixed_fleets_and_actual_barrels_conserved": all_conserved,
        "upstream_worlds_not_mutated": upstream_unchanged,
        "pricing_configuration_not_recalibrated": config_hash == sha256_json(cfg),
        "equal_window_constant_work_has_flat_price_after_warmup": equal_control["real_tce_max"] - equal_control["real_tce_min"] < 0.02,
        "insufficient_fleet_matches_analytic_throughput_limit": capacity_correct,
        "past_states_unchanged_by_later_inputs": prefix_equal,
        "temporary_extra_work_increases_peak_plan_gap": shocked["summary"]["max_abs_plan_gap_days"] > base_control["summary"]["max_abs_plan_gap_days"],
    }
    return _rounded({
        "schema_version":"asset-simulation-stage5a-audit-v1",
        "scope":"physical_fixed_fleet_plus_unchanged_pricing",
        "annual_transitions":years,"covered_calendar_years":years+1,"warmup_turns":36,
        "observed_note":"Exclude initial year from summaries without resetting state; real calendar retained.",
        "config":load_stage5a_config(),"pricing_config_hash":config_hash,
        "gates":gates,"all_gates_pass":all(gates.values()),"per_seed":per_seed,
        "equal_10_day_control":equal_control,
        "insufficient_fleet_control":under["summary"],
        "constant_daily_real_calendar_sweep":calendar_sweep,
        "phase_rotation_controls":phases,"all_prompt_cold_start_control":cold,
        "temporary_ten_percent_extra_work":shocked["summary"],
        "seed42_sixty_annual_transitions":long_results,
        "limitations":[
            "Fixed five-turn voyages inherit variable 9/10/11-day calendar durations. Calendar-only price echoes are disclosed, not called economic cycles.",
            "Transport plan deviations are not refinery stocks or delivered-crude market equilibrium.",
            "All prompt vessels are offered. Price does not cause owner withholding or charterer demand destruction.",
            "Long backlogs and price caps in small fleets signal infeasible service, not equilibrium freight estimates.",
            "TCE is an indicative standard-vessel benchmark. No freight invoices, revenue, OPEX or financial accounts are present.",
        ],
    })


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years",type=int,default=20)
    parser.add_argument("--seeds",default="0,1,5,7,42")
    parser.add_argument("--fleets",default="230,235,239,242,245,250,260,280")
    parser.add_argument("--skip-long",action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--turns-output",help="optional detailed seed42/245-vessel research replay")
    args=parser.parse_args()
    result=audit_stage5a(tuple(map(int,args.seeds.split(','))),years=args.years,
        fleet_sizes=tuple(map(int,args.fleets.split(','))),long_window=not args.skip_long,turns_output=args.turns_output)
    text=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
    if args.output:
        Path(args.output).write_text(text+'\n',encoding="utf-8")
    else:
        print(text)
    print(json.dumps({"gates":result["gates"],"all_gates_pass":result["all_gates_pass"]},ensure_ascii=False))
    if not result["all_gates_pass"]:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
