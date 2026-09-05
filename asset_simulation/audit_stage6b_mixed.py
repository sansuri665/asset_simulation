"""Actual seeded full-route cargo, capacity-matched fleets and quota-free controls."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from .model.global_shipping_contract import CLASSES
from .model.mixed_cargo_contract import load_mixed_config, make_mixed_spec
from .model.mixed_cargo_market import build_mixed_inputs, run_mixed_market
from .model.mixed_cargo_pricing import quote_mixed_routes
from .model.registry import sha256_json

FLEETS = {
    'vlcc_only_300': {'vlcc':300,'suezmax':0,'aframax':0},
    'mixed_240v_120s': {'vlcc':240,'suezmax':120,'aframax':0},
    'mixed_210v_180s': {'vlcc':210,'suezmax':180,'aframax':0},
    'three_225v_120s_50a': {'vlcc':225,'suezmax':120,'aframax':50},
}


def compact(r):
    return {'summary':r['summary'],'input_hash':r['input_hash'],'result_hash':r['result_hash']}


def constant(n=216):
    return [{'scheduled_by_origin_bbl':{'gulf':93000000,'west_africa':12000000},'cpi':100.} for _ in range(n)]


def run_audit(*,seeds=(0,1,5,7,42),years=20,turns_output=None,long_years=60):
    spec=make_mixed_spec();before=spec.identity
    flat={name:compact(run_mixed_market(spec,constant(),fleet_counts=fleet)) for name,fleet in FLEETS.items()}
    cfg=load_mixed_config();cfg['pricing']['shared_signal_weight']=0
    independent=make_mixed_spec(config=cfg)
    flat_independent=run_mixed_market(independent,constant(),fleet_counts=FLEETS['mixed_240v_120s'])
    retained_flat=run_mixed_market(spec,constant(),fleet_counts=FLEETS['mixed_240v_120s'])
    # Same physical record generator, different quote presentation only.
    same_execution=all(x['routes'][o]['loaded_by_class_bbl']==y['routes'][o]['loaded_by_class_bbl']
                       and x['routes'][o]['origin_unshipped_bbl']==y['routes'][o]['origin_unshipped_bbl']
                       for x,y in zip(flat_independent['turns'],retained_flat['turns']) for o in spec.origins)
    base=constant(180);shock=deepcopy(base)
    for i in range(80,92):shock[i]['scheduled_by_origin_bbl']['west_africa']*=2
    shock_base=run_mixed_market(spec,base,fleet_counts=FLEETS['mixed_240v_120s'])
    shock_run=run_mixed_market(spec,shock,fleet_counts=FLEETS['mixed_240v_120s'])
    spill={o:{'control_peak':max(r['routes'][o]['route_benchmark_real_tce'] for r in shock_base['turns'][80:135]),
              'shock_peak':max(r['routes'][o]['route_benchmark_real_tce'] for r in shock_run['turns'][80:135]),
              'max_absolute_committed_capacity_change_bbl_per_turn':max(abs(x['committed_service_bbl_per_turn'].get(o,0)-y['committed_service_bbl_per_turn'].get(o,0))
                    for x,y in zip(shock_base['turns'][80:135],shock_run['turns'][80:135]))} for o in spec.origins}
    results=[];retained=None
    for seed in seeds:
        inputs,source=build_mixed_inputs(spec,seed=seed,years=years)
        item={'seed':seed,'source':source,'fleets':{}}
        for name,fleet in FLEETS.items():
            run=run_mixed_market(spec,inputs,fleet_counts=fleet)
            item['fleets'][name]=compact(run)
            if seed==42 and name=='mixed_240v_120s':retained=run
        results.append(item)
        print(f'Mixed Stage6B seed {seed}: 4 full-cargo fleet mixes complete',file=sys.stderr,flush=True)
    if retained is None:
        inputs,_=build_mixed_inputs(spec,seed=42,years=years)
        retained=run_mixed_market(spec,inputs,fleet_counts=FLEETS['mixed_240v_120s'])
    if turns_output:
        Path(turns_output).parent.mkdir(parents=True,exist_ok=True)
        Path(turns_output).write_text(json.dumps(retained,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    four_spec=make_mixed_spec(origins=('gulf','west_africa','brazil_guyana','us_gulf'))
    four_inputs,four_source=build_mixed_inputs(four_spec,seed=42,years=years)
    four=run_mixed_market(four_spec,four_inputs,fleet_counts={'vlcc':350,'suezmax':180,'aframax':20})
    long_inputs,long_source=build_mixed_inputs(spec,seed=42,years=long_years)
    long=run_mixed_market(spec,long_inputs,fleet_counts=FLEETS['mixed_240v_120s'])
    print('Mixed Stage6B four-origin and long-window runs complete',file=sys.stderr,flush=True)
    # Pure quote equality for capacity-substitutable offers, not equal profits.
    args={'scheduled_by_origin_bbl':{'gulf':93000000,'west_africa':12000000},
          'prompt_by_origin_class':{'gulf':dict(vlcc=50,suezmax=0,aframax=0),'west_africa':dict(vlcc=6,suezmax=0,aframax=0)},
          'origin_pressures':dict(gulf=0.,west_africa=0.),'destination_pressure':0.,'previous_signals':dict(gulf=0.,west_africa=0.)}
    qa=quote_mixed_routes(spec,**args);args['prompt_by_origin_class']['west_africa']=dict(vlcc=4,suezmax=4,aframax=0)
    qb=quote_mixed_routes(spec,**args)
    # Execute regression gates, don't merely print previously asserted results.
    import unittest
    from .tests.test_mixed_cargo_market import MixedContractsTests,MixedExecutionTests
    def ok(case):
        res=unittest.TestResult();case.run(res);return res.wasSuccessful()
    gates={
        'all_seeded_fleets_and_barrels_conserved':all(r['summary']['conservation_exact'] for x in results for r in x['fleets'].values()),
        'full_cargo_not_partitioned_by_class':all(not x['source']['class_partition_applied'] for x in results),
        'upstream_world_unchanged':all(x['source']['source_unchanged'] for x in results),
        'class_prior_invariance':ok(MixedContractsTests('test_reference_mix_cannot_affect_new_spec_or_simulation')),
        'route_parcel_does_not_change_physical_capacity':ok(MixedContractsTests('test_benchmark_parcel_overrides_are_not_actual_loads')),
        'equivalent_offered_capacity_has_identical_quote':qa['routes']['west_africa']['route_benchmark_real_tce']==qb['routes']['west_africa']['route_benchmark_real_tce'],
        'arbitrary_external_mix_is_independent_of_same_turn_quote':ok(MixedExecutionTests('test_manual_mix_changes_execution_not_same_turn_quotes')),
        'all_three_single_class_fleets_can_serve_same_cargo':ok(MixedExecutionTests('test_single_class_any_mix_no_reference_share_required')),
        'packing_maximum_checked_against_bruteforce':ok(MixedExecutionTests('test_packer_agrees_with_brute_force_small_fleets')),
        'price_sharing_does_not_modify_dispatch':same_execution,
        'fixed_class_on_every_ship_and_no_double_dispatch':ok(MixedExecutionTests('test_all_three_classes_exact_conservation_and_unique_dispatch')),
        'pre_shock_prefix_unchanged':shock_base['turns'][:80]==shock_run['turns'][:80],
        'real_capacity_reallocation_occurs':spill['gulf']['max_absolute_committed_capacity_change_bbl_per_turn']>0,
        'four_origins_use_same_conserved_engine':four['summary']['conservation_exact'],
        'long_window_conserved':long['summary']['conservation_exact'],
        'specification_unchanged':before==spec.identity,
    }
    out={'model_version':'stage6b-mixed-audit-v0.2','base_commit':'1d9f824ad6c099e7671446698f2fc5053054f38c',
         'scope':'full selected-route cargo; no costs and no empirical share/rate fit',
         'capacity_bbl':dict(spec.capacities),
         'scenarios':{n:{'counts':f,'total_cargo_capacity_bbl':sum(dict(spec.capacities)[c]*f[c] for c in CLASSES)} for n,f in FLEETS.items()},
         'constant_controls':flat,'constant_independent_quote_control':compact(flat_independent),
         'short_shock':spill,'seeded_sweep':results,'four_origin':{'source':four_source,**compact(four)},
         'seed42_long':{'source':long_source,**compact(long)},
         'capacity_substitution_control':{'6_vlcc':qa['routes']['west_africa'],'4_vlcc_4_suezmax':qb['routes']['west_africa']},
         'gates':gates,'all_gates_pass':all(gates.values()),
         'warnings':['Class TCE is a capacity/time convention from common net service value, not independent price discovery.',
                     'Shared signal and bounded local residual are explicit uncalibrated quote-design parameters.',
                     'Full-load packing is a maximum-volume demo, not an economic owner choice.',
                     'More hulls with the same capacity can affect timing/packing: live trajectories need not be identical.',
                     'The inherited legacy Stage6B uses only VLCC shares, so its rates are not a like-for-like comparison.',
                     'Physical conservation is not proof of adequate service or real market equilibrium.']}
    out['audit_hash']=sha256_json(out)
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seeds',default='0,1,5,7,42');p.add_argument('--years',type=int,default=20)
    p.add_argument('--long-years',type=int,default=60);p.add_argument('--output',type=Path,default=Path('stage6b-mixed-audit.json'))
    p.add_argument('--turns-output',type=Path)
    a=p.parse_args();out=run_audit(seeds=tuple(map(int,a.seeds.split(','))),years=a.years,turns_output=a.turns_output,long_years=a.long_years)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'gates':out['gates'],'all_gates_pass':out['all_gates_pass']}))
    if not out['all_gates_pass']:raise SystemExit(1)


if __name__=='__main__':main()
