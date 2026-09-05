"""Reproducible v3 audits, including negative results and separated changes."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

from .model.mixed_cargo_contract import make_mixed_spec
from .model.mixed_cargo_market import build_mixed_inputs, run_mixed_market
from .model.registry import sha256_json
from .model.shipping_v3 import load_config, make_market_spec, run_market
from .tests.test_shipping_market_v3 import V3Contracts, V3Execution, V3AvailabilityAndPrice

FLEETS = {
    'vlcc_300': {'vlcc':300,'suezmax':0,'aframax':0},
    'mixed_240v_120s': {'vlcc':240,'suezmax':120,'aframax':0},
    'three_225v_120s_50a': {'vlcc':225,'suezmax':120,'aframax':50},
}


def constant(n=216):
    return [{'scheduled_by_origin_bbl':{'gulf':93000000,'west_africa':12000000},'cpi':100.} for _ in range(n)]


def compact(result):
    return {'summary':result['summary'],'input_hash':result['input_hash'],'result_hash':result['result_hash']}


def physical_signature(result):
    fields=('loaded_bbl','origin_unshipped_bbl','actual_in_transit_bbl','dispatched_by_class','loaded_by_class_bbl')
    return sha256_json([{'routes':{o:{k:r[k] for k in fields} for o,r in t['routes'].items()},
                         'committed':t['committed_service_bbl_per_turn']} for t in result['turns']])


def test_pass(case):
    out=unittest.TestResult();case.run(out)
    return out.wasSuccessful()


def run_audit(*,seeds=(0,1,5,7,42),years=20,long_years=60,turns_output=None):
    spec=make_market_spec();fleet=FLEETS['mixed_240v_120s'];identity=spec.identity
    controls={};run_kept={}
    for threshold in (1.,.7,.5):
        cfg=load_config();cfg['demo_policy']['minimum_tail_load_fraction']=threshold
        for forward in (False,True):
            cfg['availability']['arrival_weights']=[1.,.5,.15] if forward else [1.]
            label=f'tail_{threshold:g}_'+('scheduled' if forward else 'now_only')
            run=run_market(make_market_spec(config=cfg),constant(),fleet_counts=fleet)
            controls[label]=compact(run);run_kept[label]=run
    old_flat=run_mixed_market(make_mixed_spec(),constant(),fleet_counts=fleet)
    shock=constant(180)
    for row in shock[80:92]:row['scheduled_by_origin_bbl']['west_africa']*=2
    baseline=run_market(spec,constant(180),fleet_counts=fleet)
    stressed=run_market(spec,shock,fleet_counts=fleet)
    spill={o:{'control_peak':max(r['routes'][o]['route_benchmark_real_tce'] for r in baseline['turns'][80:140]),
              'shock_peak':max(r['routes'][o]['route_benchmark_real_tce'] for r in stressed['turns'][80:140]),
              'max_committed_service_change':max(abs(a['committed_service_bbl_per_turn'].get(o,0)-b['committed_service_bbl_per_turn'].get(o,0))
                 for a,b in zip(baseline['turns'][80:140],stressed['turns'][80:140]))} for o in spec.origins}
    print('V3 clock/partial/forward controls and physical shock complete',file=sys.stderr,flush=True)
    results=[];retained=None;seed42_inputs=None
    for seed in seeds:
        inputs,source=build_mixed_inputs(spec.physical,seed=seed,years=years)
        item={'seed':seed,'source':source,'fleets':{}}
        for name,counts in FLEETS.items():
            run=run_market(spec,inputs,fleet_counts=counts)
            item['fleets'][name]=compact(run)
            if seed==42 and name=='mixed_240v_120s':retained=run;seed42_inputs=inputs
        results.append(item)
        print(f'V3 seed {seed}: three fixed fleets complete',file=sys.stderr,flush=True)
    if retained is None:
        seed42_inputs,_=build_mixed_inputs(spec.physical,seed=42,years=years)
        retained=run_market(spec,seed42_inputs,fleet_counts=fleet)
    old_seed42=run_mixed_market(spec.physical,seed42_inputs,fleet_counts=fleet)
    cfg=load_config();cfg['demo_policy']['minimum_tail_load_fraction']=1.
    full_run=run_market(make_market_spec(config=cfg),seed42_inputs,fleet_counts=fleet)
    cfg['availability']['arrival_weights']=[1.]
    full_now=run_market(make_market_spec(config=cfg),seed42_inputs,fleet_counts=fleet)
    if turns_output:
        path=Path(turns_output);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(retained,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        sample=retained['turns'][len(retained['turns'])//2]
        path.with_name('v3-quote-example.json').write_text(json.dumps(sample,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    four_spec=make_market_spec(origins=('gulf','west_africa','brazil_guyana','us_gulf'))
    four_inputs,four_source=build_mixed_inputs(four_spec.physical,seed=42,years=years)
    four=run_market(four_spec,four_inputs,fleet_counts={'vlcc':350,'suezmax':180,'aframax':20})
    long_inputs,long_source=build_mixed_inputs(spec.physical,seed=42,years=long_years)
    long=run_market(spec,long_inputs,fleet_counts=fleet)
    print('V3 four-origin and long-window audits complete',file=sys.stderr,flush=True)
    gate_tests={
        'fixed_capacity_and_reference_mix_independence':V3Contracts('test_benchmark_parcels_and_shares_do_not_enter_v3'),
        'cargo_due_date_independent_of_ship_speed':V3Contracts('test_slow_ship_does_not_move_cargo_deadline'),
        'partial_load_keeps_full_hull_occupancy':V3Execution('test_partial_load_actual_barrels_and_full_hull_time'),
        'fifo_and_partial_batch_allocation':V3Execution('test_fifo_earliest_due_and_batch_splitting'),
        'manifest_delivered_at_frozen_quote':V3Execution('test_delivery_uses_frozen_manifest_not_current_quote'),
        'permanent_hull_and_manifest_validation':V3Execution('test_manifest_or_hull_capacity_tampering_detected'),
        'invalid_decisions_are_atomic':V3Execution('test_invalid_loads_atomic'),
        'checkpoint_restart_exact':V3Execution('test_checkpoint_resume_identical'),
        'uncommitted_hulls_not_guessed':V3AvailabilityAndPrice('test_uncommitted_empty_not_guessed'),
        'future_hulls_not_loadable_now':V3AvailabilityAndPrice('test_ballast_order_only_enters_next_quote'),
        'one_hull_one_availability_bucket':V3AvailabilityAndPrice('test_one_hull_once_not_cumulative'),
        'normal_horizon_schedule_preserves_anchor':V3AvailabilityAndPrice('test_normal_schedule_anchor_independent_of_horizon_weights'),
        'same_capacity_substitution_preserves_quote':V3AvailabilityAndPrice('test_capacity_substitution_in_all_horizons'),
        'price_components_do_not_choose_ships':V3AvailabilityAndPrice('test_price_parameters_do_not_choose_ships'),
        'legacy_upper_bound_removed':V3AvailabilityAndPrice('test_v3_can_exceed_legacy_122500_bound'),
        'urgency_only_premium_remains_bounded':V3AvailabilityAndPrice('test_urgency_bounded_separately_from_market'),
        'local_bound_and_zero_sum_both_hold':V3AvailabilityAndPrice('test_local_bound_and_center_both_hold'),
        'time_only_diagnostic_no_double_return':V3AvailabilityAndPrice('test_partial_opportunity_no_duplicate_ballast'),
    }
    gates={name:test_pass(case) for name,case in gate_tests.items()}
    all_runs=[r for item in results for r in item['fleets'].values()]+list(controls.values())+[compact(four),compact(long)]
    gates.update({
        'all_seeded_hulls_and_batches_conserved':all(r['summary']['conservation_exact'] for r in all_runs),
        'every_quote_reconstructs_from_trace':all(r['summary']['max_quote_reconstruction_error']==0 for r in all_runs),
        'full_cargo_source_unchanged':all(item['source']['source_unchanged'] and not item['source']['class_partition_applied'] for item in results),
        'forward_quote_not_physical_forecast':all(physical_signature(run_kept[f'tail_{t:g}_scheduled'])==physical_signature(run_kept[f'tail_{t:g}_now_only']) for t in (1.,.7,.5)),
        'pre_shock_history_identical':baseline['turns'][:80]==stressed['turns'][:80],
        'shock_causes_real_capacity_reallocation':spill['gulf']['max_committed_service_change']>0,
        'spec_unchanged':identity==spec.identity,
    })
    out={'model_version':'stage6b-v3-audit','parent_v2_commit':'6f6aaa24e44784ff3eb0538e488636cff5f7d88b',
         'scope':'same full cargo; explicit timing and tail-policy ablations; not empirical shipping-price fit',
         'constant_v2_full_load':compact(old_flat),'constant_v3_controls':controls,
         'seeded_sweep':results,
         'seed42_ablation':{'v2_full_current_logistic':compact(old_seed42),
                            'v3_full_current_exp':compact(full_now),
                            'v3_full_scheduled_exp':compact(full_run),
                            'v3_tail70_scheduled_exp':compact(retained)},
         'shock':spill,'four_origin':{'source':four_source,**compact(four)},
         'seed42_long':{'source':long_source,**compact(long)},
         'gates':gates,'all_gates_pass':all(gates.values()),
         'warnings':[
             'Ten-day game time, source calendar projection, reference capacities and distances remain design assumptions.',
             'Partial loads can reduce tail backlog but consume whole hull voyages; lower thresholds are not automatically superior.',
             'Forward arrival weights and normal-schedule normalization are not forecasts of uncommitted ships or future cargo.',
             'Removing the 122500 ceiling changes prices: separated comparisons are necessary; lower CV is not empirical validation.',
             'No-current-supply indications and numeric-guard observations are not transaction revenues.',
             'The coverage router remains a demo without price arbitrage, costs or owner optimization.',
             'Quote net value is not gross freight; actual load scales reference value but never changes permanent ship capacity.',
         ]}
    out['audit_hash']=sha256_json(out)
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seeds',default='0,1,5,7,42');p.add_argument('--years',type=int,default=20)
    p.add_argument('--long-years',type=int,default=60);p.add_argument('--output',type=Path,default=Path('stage6b-v3-audit.json'))
    p.add_argument('--turns-output',type=Path)
    a=p.parse_args();r=run_audit(seeds=tuple(map(int,a.seeds.split(','))),years=a.years,long_years=a.long_years,turns_output=a.turns_output)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'gates':r['gates'],'all_gates_pass':r['all_gates_pass']}))
    if not r['all_gates_pass']:raise SystemExit(1)


if __name__=='__main__':main()
