"""Reproducible trial controls and explicit external-input replays, not an optimizer."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import statistics
import sys
import unittest
from .model.freight_board import (BallastOrder, BoardSession, ImportRequirement, make_board_spec,
                                 quote_trial, recompute_trial_price)
from .model.mixed_cargo_market import build_mixed_inputs
from .model.registry import sha256_json

V=1_971_000
ZERO={'gulf':0,'west_africa':0}


def static_controls():
    spec=make_board_spec()
    session=BoardSession(spec,fleet_counts={'vlcc':12,'suezmax':8,'aframax':4},initialization='cold')
    snap=session.open_turn(source_release_bbl=ZERO)
    offered={o:tuple(s.ship_id for s in snap.opened_market.ships if s.location==o) for o in spec.origins}
    before=sha256_json(asdict(snap));cargo=[]
    for qg,qw in ((8*V,3*V),(9*V,2*V),(10*V,V)):
        r=quote_trial(snap,offered,{'gulf':qg,'west_africa':qw}).report()
        cargo.append({'cargo_bbl':{'gulf':qg,'west_africa':qw},
                      'tce':{o:r['routes'][o]['route_benchmark_real_tce'] for o in spec.origins},
                      'net_per_bbl':{o:r['routes'][o]['net_service_value_real_usd_per_bbl'] for o in spec.origins},'valid':r['valid']})
    ids=tuple(s.ship_id for s in snap.opened_market.ships if s.location=='gulf' and s.class_id=='vlcc')
    capacities=[]
    for n in (2,3,4,5):
        r=quote_trial(snap,{'gulf':ids[:n],'west_africa':()},{'gulf':5_000_000,'west_africa':0}).report()['routes']['gulf']
        capacities.append({'trial_ships':n,'benchmark_tce':r['route_benchmark_real_tce'],
                           'full':r['allocation']['full_ship_count'],'marginal_load_factor':r['allocation']['marginal_load_factor'],
                           'unserved_bbl':r['allocation']['unserved_trial_cargo_bbl'],'unused':r['allocation']['unused_ship_count']})
    example=quote_trial(snap,{'gulf':ids[:4],'west_africa':()},{'gulf':5_000_000,'west_africa':0}).report()
    for i in range(1000):quote_trial(snap,offered,{'gulf':(5+i%5)*V,'west_africa':V})
    return {'cargo_transfer_same_total':cargo,'more_hulls_same_cargo':capacities,'example':example,
            'snapshot_unchanged_after_1000_trials':before==sha256_json(asdict(snap))}


def external_reference_plan(snapshot, release):
    """Audit CALLER: all local hulls, bounded stock catchup, fixed home return.

    No price is read and no cheapest route/best class is chosen. The caller is
    deliberately outside the board. Its outcome is not a market equilibrium.
    """
    offered={o:tuple(s.ship_id for s in snapshot.opened_market.ships if s.location==o) for o in snapshot.spec.origins}
    q={}
    for o,band in snapshot.spec.source_bands:
        shore=sum(b.remaining_bbl for b in snapshot.opened_market.batches if b.origin==o)
        catchup=min(max(0,shore-band.normal_bbl)//4,release[o]//5)
        q[o]=min(shore+release[o],release[o]+catchup)
    ballasts=tuple(BallastOrder(s.ship_id,s.home_origin) for s in snapshot.opened_market.ships
                  if s.location==snapshot.spec.market.destination and s.movement is None)
    return offered,q,ballasts


def replay(seed=42,years=5,fleet_counts=None,*,keep_turns=False,extra_trials=True,max_turns=None):
    spec=make_board_spec();fleet=fleet_counts or {'vlcc':280,'suezmax':160,'aframax':40}
    inputs,source=build_mixed_inputs(spec.market.physical,seed=seed,years=years)
    if max_turns is not None: inputs=inputs[:max_turns]
    source_hash=sha256_json(inputs);session=BoardSession(spec,fleet_counts=fleet)
    rows=[];monotone=True;immutable=True;error=0.;stop=None;trial_count=0
    for t,inp in enumerate(inputs):
        release=inp['scheduled_by_origin_bbl']
        requirements=tuple(ImportRequirement(f'upstream:{o}:{t}',t+dict(spec.market.due_lags)[o],q)
                           for o,q in release.items() if q)
        snap=session.open_turn(source_release_bbl=release,new_requirements=requirements,cpi=inp['cpi'])
        ships,q,ballasts=external_reference_plan(snap,release)
        before=session.state.identity
        first=quote_trial(snap,ships,q,ballast_orders=ballasts);trial_count+=1
        report=first.report()
        if not report['valid']:
            stop={'turn':t,'violations':report['inventory']['hard_bound_violations'],
                  'behavior':'stopped_without_clipping_or_advancing_or_deleting_demand'}
            break
        if extra_trials:
            ready_g=sum(b.remaining_bbl for b in snap.opened_market.batches if b.origin=='gulf')+release['gulf']
            delta=min(q['west_africa']//100,max(0,ready_g-q['gulf']))
            alternative=quote_trial(snap,ships,{'gulf':q['gulf']+delta,'west_africa':q['west_africa']-delta},
                                    ballast_orders=ballasts).report();trial_count+=1
            monotone &= alternative['routes']['gulf']['route_benchmark_real_tce'] >= report['routes']['gulf']['route_benchmark_real_tce']
            monotone &= alternative['routes']['west_africa']['route_benchmark_real_tce'] <= report['routes']['west_africa']['route_benchmark_real_tce']
            last=quote_trial(snap,ships,q,ballast_orders=ballasts);trial_count+=1
            immutable &= last.identity==first.identity and before==session.state.identity
        for r in report['routes'].values():
            error=max(error,abs(recompute_trial_price(r['explanation'])-r['route_benchmark_real_tce']))
        record=session.commit(first)
        compact={'turn':t,'label':inp['label'],'execution_id':record['execution_id'],
                 'inventory_deviation_bbl':report['inventory']['destination']['closing_current_turn']['deviation_bbl'],
                 'mass_residual_bbl':report['inventory']['physical_mass_balance_residual_bbl'],
                 'routes':{o:{'price':r['route_benchmark_real_tce'],
                              'per_bbl':r['net_service_value_real_usd_per_bbl'],
                              'ordered':r['trial_cargo_bbl'],'loaded':r['allocation']['loaded_bbl'],
                              'full':r['allocation']['full_ship_count'],
                              'marginal':r['allocation']['marginal_ship_id'],
                              'unused':r['allocation']['unused_ship_count'],
                              'source_stock':report['inventory']['sources'][o]['closing']['stock_bbl'],
                              'numeric_guard':r['explanation']['numeric_guard_hit']}
                           for o,r in report['routes'].items()}}
        rows.append(compact)
    observed=rows[min(36,len(rows)):]
    summary={}
    for o in spec.origins:
        rr=[r['routes'][o] for r in observed]
        prices=[r['price'] for r in rr]
        summary[o]={'median_reference_tce':statistics.median(prices) if prices else None,
                    'min_reference_tce':min(prices) if prices else None,'max_reference_tce':max(prices) if prices else None,
                    'mean_loaded_bbl':statistics.mean([r['loaded'] for r in rr]) if rr else None,
                    'no_loading_turns':sum(r['loaded']==0 for r in rr),
                    'numeric_guard_turns':sum(r['numeric_guard'] for r in rr),
                    'max_source_stock_bbl':max((r['source_stock'] for r in rr),default=0)}
    out={'seed':seed,'annual_transitions':years,'input_turns':len(inputs),'committed_turns':len(rows),
         'trial_evaluations':trial_count,'observed_turns':len(observed),'fleet_counts':fleet,'source':source,
         'scope':'explicit_fixed_home_caller_not_endogenous_dynamic_equilibrium',
         'source_unchanged':source_hash==sha256_json(inputs),'trial_invariance':immutable,
         'order_transfer_direction':bool(monotone),'max_quote_reconstruction_error':error,
         'mass_conserved':all(r['mass_residual_bbl']==0 for r in rows),
         'failure':stop,'summary':summary,'execution_path_hash':sha256_json(rows),
         'final_state_hash':session.state.identity}
    if keep_turns:out['turns']=rows
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seeds',default='0,1,42');p.add_argument('--years',type=int,default=5)
    p.add_argument('--output',type=Path,default=Path('stage6c-preview-audit.json'))
    p.add_argument('--turns-output',type=Path)
    a=p.parse_args()
    controls=static_controls();results=[]
    for seed in map(int,a.seeds.split(',')):
        r=replay(seed,a.years,keep_turns=seed==42 and a.turns_output is not None)
        if 'turns' in r:
            a.turns_output.parent.mkdir(parents=True,exist_ok=True)
            a.turns_output.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            r.pop('turns')
        results.append(r)
        print(f"Seed {seed}: {r['committed_turns']}/{r['input_turns']} committed; failure={r['failure']}",file=sys.stderr,flush=True)
    # One actual trajectory is replayed WITHOUT any intermediate inquiries.
    check=replay(42,5,extra_trials=True,max_turns=72);plain=replay(42,5,extra_trials=False,max_turns=72)
    suite=unittest.defaultTestLoader.loadTestsFromName('asset_simulation.tests.test_freight_board')
    tested=unittest.TestResult();suite.run(tested)
    gates={'contract_tests':tested.wasSuccessful(),
           '1000_trials_pure':controls['snapshot_unchanged_after_1000_trials'],
           'same_final_plan_same_physical_history':check['execution_path_hash']==plain['execution_path_hash'],
           'source_unchanged':all(r['source_unchanged'] for r in results),
           'trial_order_independence':all(r['trial_invariance'] for r in results),
           'cargo_transfer_negative_feedback':all(r['order_transfer_direction'] for r in results),
           'prices_reconstruct':all(r['max_quote_reconstruction_error']==0 for r in results),
           'physical_mass_conserved':all(r['mass_conserved'] for r in results),
           'declared_reference_replays_complete':all(r['failure'] is None for r in results)}
    out={'model':'stage6c-preview-audit-v1','parent':'c89f735ed06154e2309288ed5b941c5457e34909',
         'controls':controls,'seeded_replays':results,'contract_test_count':tested.testsRun,
         'contract_test_failures':[(str(t),trace) for t,trace in (*tested.failures,*tested.errors)],
         'gates':gates,'all_gates_pass':all(gates.values()),
         'warnings':['No automatic buyer or owner optimizer; external trial input remains necessary.',
                     'Inventory is actual stock minus an explicit reference, not clipped price pressure.',
                     'Prices use new class-normalized post-trial quantities; not comparable to v3 calibration.',
                     'Source releases here cover only the two selected trades; this is not global surplus integration.',
                     'Known future inventories assume no as-yet-unsubmitted future decisions or requirements.',
                     'One-session replay protection is not distributed database concurrency control.']}
    out['audit_hash']=sha256_json(out)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'gates':gates,'all_gates_pass':out['all_gates_pass']}))
    if not out['all_gates_pass']:raise SystemExit(1)


if __name__=='__main__':main()
