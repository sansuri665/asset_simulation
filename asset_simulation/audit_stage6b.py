"""Reproducible two-/four-origin sweeps and causal physical-spillover controls."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import statistics
import sys
import unittest

from .model.multi_origin_market import (
    build_seeded_inputs, load_network_config, make_network_spec, run_network,
)
from .model.registry import sha256_json


def _constant(n=180, whole=False):
    cargo={'gulf':42*1971000,'west_africa':5*1898000} if whole else {'gulf':83700000,'west_africa':9600000}
    return [{'scheduled_by_origin_bbl':dict(cargo),'cpi':100.0} for _ in range(n)]


def _compact(run):
    return {'summary':run['summary'],'result_hash':run['result_hash'],'input_hash':run['input_hash']}


def _shock_comparison(spec, base, shock, fleet=265, start=80, end=130):
    a=run_network(spec,base,fleet_size=fleet,include_events=True)
    b=run_network(spec,shock,fleet_size=fleet,include_events=True)
    per={}
    for origin in spec.origins:
        av=[r['routes'][origin]['real_tce_2025_usd_per_day'] for r in a['turns'][start:end]]
        bv=[r['routes'][origin]['real_tce_2025_usd_per_day'] for r in b['turns'][start:end]]
        transfer=[y['committed_after_routing'][origin]-x['committed_after_routing'][origin] for x,y in zip(a['turns'][start:end],b['turns'][start:end])]
        per[origin]={'control_peak_real_tce':max(av),'shock_peak_real_tce':max(bv),
                     'maximum_quote_increase_same_turn':max(y-x for x,y in zip(av,bv)),
                     'maximum_extra_assigned_ships':max(transfer),
                     'minimum_extra_assigned_ships':min(transfer),
                     'maximum_shock_unshipped_bbl':max(r['routes'][origin]['origin_unshipped_bbl'] for r in b['turns'][start:end])}
    return {'origins':per,'prefix_identical':a['turns'][:start]==b['turns'][:start],
            'gulf_new_plan_unchanged':all(x['routes']['gulf']['scheduled_bbl']==y['routes']['gulf']['scheduled_bbl'] for x,y in zip(a['turns'],b['turns'])),
            'fleet_conserved':a['summary']['conservation_exact'] and b['summary']['conservation_exact'],
            'control':_compact(a),'shock':_compact(b)}


def run_audit(seeds=(0,1,5,7,42), years=20, fleet_sizes=(245,255,265,280), *, turns_output=None):
    spec=make_network_spec(); spec_before=spec.identity
    controls={}; whole=run_network(spec,_constant(216,True),fleet_size=250)
    controls['constant_whole_lots']=_compact(whole)
    fractional=run_network(spec,_constant(216),fleet_size=265)
    controls['constant_daily_fractional_lots']=_compact(fractional)
    controls['fractional_quote_cv']={o:statistics.pstdev([r['routes'][o]['real_tce_2025_usd_per_day'] for r in fractional['turns'][36:]]) / statistics.mean([r['routes'][o]['real_tce_2025_usd_per_day'] for r in fractional['turns'][36:]]) for o in spec.origins}
    base=_constant(180); shock=deepcopy(base)
    for i in range(80,92):shock[i]['scheduled_by_origin_bbl']['west_africa']=int(shock[i]['scheduled_by_origin_bbl']['west_africa']*1.5)
    controls['west_africa_plus_50pct_12_turns']=_shock_comparison(spec,base,shock)
    # Remove ALL direct urgency and price-directed routing: any remaining Gulf
    # effect must be transmitted by physical ship positions, not a shared price factor.
    cfg=load_network_config()
    cfg['pricing']['quote_recovery_fraction']=0
    cfg['pricing']['inventory_urgency_log_sensitivity_per_day']=0
    cfg['routing']['bounded_price_tiebreak_weight']=0
    physical_spec=make_network_spec(config=cfg)
    controls['physical_only_spillover']=_shock_comparison(physical_spec,base,shock)
    cfg['routing']['mode']='home_return'
    home_spec=make_network_spec(config=cfg)
    controls['segmented_physical_control']=_shock_comparison(home_spec,base,shock)
    results=[]; retained=None
    for seed in seeds:
        inputs, source=build_seeded_inputs(spec,seed=seed,years=years)
        item={'seed':seed,'source':source,'fleets':{}}
        for size in fleet_sizes:
            run=run_network(spec,inputs,fleet_size=size,include_events=False)
            item['fleets'][str(size)]=_compact(run)
            if seed==42 and size==265:retained=run
        print(f'Stage6B seed {seed}: {len(fleet_sizes)} fleet scenarios passed state checks',file=sys.stderr,flush=True)
        results.append(item)
    cfg=load_network_config();cfg['routing']['mode']='home_return'
    hs=make_network_spec(config=cfg)
    inputs, _=build_seeded_inputs(spec,seed=42,years=years)
    controls['seed42_segmented_265']=_compact(run_network(hs,inputs,fleet_size=265))
    if retained is None:retained=run_network(spec,inputs,fleet_size=265)
    controls['seed42_pooled_265']=_compact(retained)
    if turns_output:
        Path(turns_output).parent.mkdir(parents=True,exist_ok=True)
        Path(turns_output).write_text(json.dumps(retained,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    fs=make_network_spec(origins=('gulf','west_africa','brazil_guyana','us_gulf'))
    four_inputs,four_source=build_seeded_inputs(fs,seed=42,years=years)
    four={'source':four_source,'fleets':{}}
    for size in (340,360,380):
        four['fleets'][str(size)]=_compact(run_network(fs,four_inputs,fleet_size=size))
    print('Stage6B four-origin Seed42 extension completed',file=sys.stderr,flush=True)
    long_inputs,long_source=build_seeded_inputs(spec,seed=42,years=60)
    long_run=run_network(spec,long_inputs,fleet_size=265)
    long={'source':long_source,**_compact(long_run)}
    # These are executed, not hardcoded audit gates.
    from .tests.test_stage6b import MultiOriginPricingTests, NetworkPhysicalTests
    def test_ok(case):
        result=unittest.TestResult();case.run(result);return result.wasSuccessful()
    formula_ok=test_ok(MultiOriginPricingTests('test_generalized_formula_reduces_to_stage5a_when_inputs_match'))
    events_ok=test_ok(NetworkPhysicalTests('test_every_loaded_ship_delivers_once_and_is_locked'))
    physical=controls['physical_only_spillover']; segmented=controls['segmented_physical_control']
    checks={
        'all_fixed_fleets_and_integer_barrels_conserved':all(v['summary']['conservation_exact'] for r in results for v in r['fleets'].values()),
        'shared_destination_is_not_copied_as_multiple_cargo_pools':all(r['plan_conservation_residual_bbl']==0 for r in retained['turns']),
        'single_formula_preserved_by_executable_reduction_control':formula_ok,
        'whole_lot_constant_work_has_no_autonomous_quote_cycle':all(max(r['routes'][o]['real_tce_2025_usd_per_day'] for r in whole['turns'][60:])-min(r['routes'][o]['real_tce_2025_usd_per_day'] for r in whole['turns'][60:])<.05 for o in spec.origins),
        'local_shock_has_identical_pre_shock_history':controls['west_africa_plus_50pct_12_turns']['prefix_identical'],
        'competitor_shock_does_not_change_gulf_new_demand':physical['gulf_new_plan_unchanged'],
        'shared_fleet_transmits_a_physical_cross_origin_effect':physical['origins']['west_africa']['maximum_extra_assigned_ships']>=5 and physical['origins']['gulf']['maximum_quote_increase_same_turn']>1000,
        'segmented_control_cannot_transmit_that_physical_effect':abs(segmented['origins']['gulf']['maximum_quote_increase_same_turn'])<.01 and segmented['origins']['west_africa']['maximum_extra_assigned_ships']==0,
        'four_origin_extension_uses_the_same_conserved_engine':all(v['summary']['conservation_exact'] for v in four['fleets'].values()),
        'long_window_remains_conservative':long_run['summary']['conservation_exact'],
        'source_worlds_and_network_parameters_unchanged':all(r['source']['source_unchanged'] for r in results) and spec.identity==spec_before,
        'no_instant_ballast_or_double_dispatch':events_ok,
    }
    output={'model_version':'stage6b-audit-v1','base_stage6a_commit':'c8c8f0ade48ebfb8cf2ff32407a1c1693a44d31b',
            'scope':'two-origin VLCC submarket, four-origin extension; no full global price claim',
            'clock':'36 ten-operating-day turns per label year','warmup_turns':36,
            'reference_lanes':[{'origin':l.origin,'parcel_bbl':l.parcel_bbl,'reference_daily_bbl':l.reference_daily_bbl,
                                'delivery_turns':l.outbound.ready_turn,'return_turns':l.return_leg.ready_turn,
                                'reference_cycling_vessels':l.reference_daily_bbl*10/l.parcel_bbl*l.cycle_turns} for l in spec.lanes],
            'controls':controls,'seeded_sweep':results,'four_origin_extension':four,'seed42_60_years':long,
            'gates':checks,'all_gates_pass':all(checks.values()),
            'warnings':{
                'thin_market_integer_lot_sensitivity':{o:cv for o,cv in controls['fractional_quote_cv'].items() if cv>.2},
                'price_realism_or_service_adequacy_not_asserted_by_conservation_gates':True,
                'unchanged_absolute_soft_price_bound':122500.0,
            },
            'limitations':['Catalogue ship-class shares are priors, not observed shares.',
                           'WAF is a small whole-lot market: one prompt vessel has a large price effect.',
                           'Fixed catalogue paths, no dynamic reroute or port congestion.',
                           'Router is a known heuristic; it is not calibrated owner profit maximization.',
                           '122500 real-dollar soft ceiling is unchanged, so no claim of uncapped global price discovery.',
                           'Integer ledger conservation does not guarantee adequate service or empirical rate realism.']}
    output['audit_hash']=sha256_json(output)
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seeds',default='0,1,5,7,42');p.add_argument('--years',type=int,default=20)
    p.add_argument('--fleet-sizes',default='245,255,265,280')
    p.add_argument('--output',type=Path,default=Path('stage6b-audit.json'))
    p.add_argument('--turns-output',type=Path)
    args=p.parse_args()
    out=run_audit(tuple(map(int,args.seeds.split(','))),args.years,tuple(map(int,args.fleet_sizes.split(','))),turns_output=args.turns_output)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'all_gates_pass':out['all_gates_pass'],'gates':out['gates']},ensure_ascii=False))
    if not out['all_gates_pass']:raise SystemExit(1)


if __name__=='__main__':main()
