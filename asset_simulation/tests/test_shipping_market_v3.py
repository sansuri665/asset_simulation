"""Behavior and counterexample tests for the transparent Stage6B-v3 kernel."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
import unittest

from asset_simulation.model.global_shipping_contract import CLASSES, load_catalog
from asset_simulation.model.mixed_cargo_market import build_mixed_inputs
from asset_simulation.model.shipping_v3 import (
    BallastOrder, BatchOrder, Decision, LoadOrder, build_availability,
    dump_state, initial_market, load_config, load_state, make_market_spec,
    prepare_turn, run_market, settle_turn, step_market, time_only_opportunities,
)
from asset_simulation.model.shipping_v3.engine import validate_state
from asset_simulation.model.shipping_v3.policies import demo_decision, hold_all
from asset_simulation.model.shipping_v3.pricing import quote_routes, recompute_price, urgency_signal
from asset_simulation.model.shipping_v3.types import OriginSignal


def constant(n=24):
    return [{'scheduled_by_origin_bbl': {'gulf':93000000,'west_africa':12000000}, 'cpi':100.0} for _ in range(n)]


def pure(spec, *, multiplier=1., now_factor=1.06, future_factor=1., pressure=0.):
    cfg=spec.config();buckets={};q={};signals={}
    for lane in spec.physical.lanes:
        volume=round(lane.reference_daily_bbl*10*multiplier);q[lane.origin]=volume
        buckets[lane.origin]=[{'horizon_turns':i,'capacity_bbl':round(volume*(now_factor if i==0 else future_factor))}
                               for i in range(len(cfg['availability']['arrival_weights']))]
        signals[lane.origin]=OriginSignal(lane.origin,pressure_days=pressure)
    args=dict(scheduled_by_origin_bbl=q,availability={'routes':buckets,'current_turn':0},
              signals=signals,destination_pressure=pressure)
    return quote_routes(spec,**args),args


class V3Contracts(unittest.TestCase):
    def test_defaults_fixed_clock(self):
        spec=make_market_spec()
        self.assertEqual(dict(spec.physical.capacities),dict(vlcc=1971000,suezmax=985500,aframax=584000))
        self.assertEqual(dict(spec.due_lags),{'gulf':3,'west_africa':4})

    def test_bad_horizon_and_weights(self):
        for weights in ([],[0.5],[1,0.2,0.5],[1,1,1,1,1],[1,float('nan')],[1,True]):
            cfg=load_config();cfg['availability']['arrival_weights']=weights
            with self.assertRaises(ValueError):make_market_spec(config=cfg)

    def test_bad_parameters(self):
        for group,key,value in [('demo_policy','minimum_tail_load_fraction',0),('pressure','half_life_turns',0),
                                 ('pricing','price_persistence',1),('pricing','shared_signal_weight',2),
                                 ('pricing','numeric_maximum_real_tce',100),('pricing','liquidity_fraction',float('inf'))]:
            cfg=load_config();cfg[group][key]=value
            with self.assertRaises(ValueError):make_market_spec(config=cfg)

    def test_benchmark_parcels_and_shares_do_not_enter_v3(self):
        path=Path(__file__).parents[1]/'config/global_shipping_physical_v0.1.json'
        raw=json.loads(path.read_text())
        a=make_market_spec()
        for cells in raw['class_share_matrix_bps'].values():
            for k in cells:cells[k]=[0,0,10000]
        raw['parcel_overrides_tonnes']['west_africa::east_asia']['vlcc']=200000
        b=make_market_spec(catalog=load_catalog(raw=raw))
        self.assertEqual(a,b)

    def test_unknown_route_due_date_requires_explicit_configuration(self):
        with self.assertRaises(ValueError):make_market_spec(destination='europe')

    def test_zero_fleet_is_valid_but_never_executes(self):
        s=make_market_spec();state=initial_market(s,dict(vlcc=0,suezmax=0,aframax=0))
        for _ in range(5):state,row=step_market(state,s,scheduled_by_origin_bbl={'gulf':1000,'west_africa':2000})
        self.assertEqual(row['routes']['gulf']['origin_unshipped_bbl'],5000)
        self.assertEqual(row['routes']['gulf']['loaded_bbl'],0)

    def test_all_three_classes_without_quotas(self):
        spec=make_market_spec(origins=('gulf',))
        for c in CLASSES:
            cap=dict(spec.physical.capacities)[c]
            s=initial_market(spec,{c:1},initialization='cold')
            s,r=step_market(s,spec,scheduled_by_origin_bbl={'gulf':cap})
            self.assertEqual(r['routes']['gulf']['loaded_bbl'],cap)
            self.assertEqual(s.ships[0].class_id,c)

    def test_slow_ship_does_not_move_cargo_deadline(self):
        path=Path(__file__).parents[1]/'config/global_shipping_physical_v0.1.json'
        raw=json.loads(path.read_text());raw['vessel_classes']['suezmax']['laden_speed_knots']=8.
        spec=make_market_spec(origins=('gulf',),catalog=load_catalog(raw=raw))
        s=initial_market(spec,{'suezmax':1},initialization='cold')
        s,_=step_market(s,spec,scheduled_by_origin_bbl={'gulf':985500})
        self.assertEqual(s.batches[0].due_turn,3)
        self.assertEqual(s.ships[0].movement.ready_turn,4)
        for _ in range(4):s,r=step_market(s,spec,scheduled_by_origin_bbl={'gulf':0},decision_factory=hold_all,include_events=True)
        self.assertEqual(r['events']['deliveries'][0]['batch_slices'][0]['lateness_turns'],1)


class V3Execution(unittest.TestCase):
    def setUp(self):
        self.spec=make_market_spec(origins=('gulf',))
        self.state=initial_market(self.spec,{'vlcc':2,'suezmax':2},initialization='cold')

    def snap(self,amount=3000000):
        return prepare_turn(self.state,self.spec,scheduled_by_origin_bbl={'gulf':amount})

    def test_partial_load_actual_barrels_and_full_hull_time(self):
        snap=self.snap();s,r=settle_turn(snap,self.spec,Decision(snap.snapshot_id,(LoadOrder(1,700000),)),include_events=True)
        self.assertEqual(s.ships[0].capacity_bbl,1971000)
        self.assertEqual(s.ships[0].movement.cargo_bbl,700000)
        self.assertEqual(s.ships[0].movement.ready_turn,3)
        self.assertEqual(s.batches[0].remaining_bbl,2300000)
        self.assertEqual(r['routes']['gulf']['partial_load_ships'],1)
        d=r['departures'][0]
        full=r['routes']['gulf']['class_quotes']['vlcc']['full_load_indicative_tce_real']
        self.assertAlmostEqual(d['actual_load_reference_tce_real']/full,700000/1971000)

    def test_delivery_uses_frozen_manifest_not_current_quote(self):
        snap=self.snap();s,_=settle_turn(snap,self.spec,Decision(snap.snapshot_id,(LoadOrder(1,800000),)))
        booked=s.ships[0].booked_net_service_value_per_bbl
        for _ in range(3):
            s,r=step_market(s,self.spec,scheduled_by_origin_bbl={'gulf':15000000},decision_factory=hold_all,include_events=True)
        d=r['events']['deliveries'][0]
        self.assertEqual(d['cargo_bbl'],800000)
        self.assertEqual(d['frozen_net_service_value_per_bbl'],booked)
        self.assertEqual(s.batches[0].delivered_bbl,800000)

    def test_fifo_earliest_due_and_batch_splitting(self):
        snap=prepare_turn(self.state,self.spec,new_batches=[BatchOrder('late','gulf',600000,8),BatchOrder('early','gulf',400000,3),BatchOrder('next','gulf',500000,4)])
        s,_=settle_turn(snap,self.spec,Decision(snap.snapshot_id,(LoadOrder(1,700000),)))
        self.assertEqual([(x.batch_id,x.cargo_bbl) for x in s.ships[0].manifest],[('early',400000),('next',300000)])
        self.assertEqual({b.batch_id:b.remaining_bbl for b in s.batches},dict(late=600000,early=0,next=200000))

    def test_fifo_ties_preserve_creation_order(self):
        s,_=step_market(self.state,self.spec,new_batches=[BatchOrder('z_old','gulf',300000,8)],decision_factory=hold_all)
        snap=prepare_turn(s,self.spec,new_batches=[BatchOrder('a_new','gulf',300000,8)])
        s,_=settle_turn(snap,self.spec,Decision(snap.snapshot_id,(LoadOrder(1,400000),)))
        self.assertEqual(s.ships[0].manifest[0].batch_id,'z_old')

    def test_one_big_or_two_small_same_cargo_different_hulls(self):
        snap=self.snap(1971000)
        a,ra=settle_turn(snap,self.spec,Decision(snap.snapshot_id,(LoadOrder(1,1971000),)))
        b,rb=settle_turn(snap,self.spec,Decision(snap.snapshot_id,(LoadOrder(3,985500),LoadOrder(4,985500))))
        self.assertEqual(ra['routes']['gulf']['route_benchmark_real_tce'],rb['routes']['gulf']['route_benchmark_real_tce'])
        self.assertEqual(ra['routes']['gulf']['loaded_bbl'],rb['routes']['gulf']['loaded_bbl'])
        self.assertNotEqual(a.ships,b.ships)

    def test_invalid_loads_atomic(self):
        snap=self.snap(1000000);before=self.state
        bad=[(LoadOrder(0,100),),(LoadOrder(1,0),),(LoadOrder(1,1971001),),(LoadOrder(1,1000001),),
             (LoadOrder(1,500000),LoadOrder(1,500000)),(LoadOrder(1,True),)]
        for loads in bad:
            with self.assertRaises(ValueError):settle_turn(snap,self.spec,Decision(snap.snapshot_id,loads))
        self.assertEqual(before,self.state)

    def test_duplicate_load_and_ballast_rejected(self):
        snap=self.snap()
        d=Decision(snap.snapshot_id,(LoadOrder(1,700000),),(BallastOrder(1,'gulf'),))
        with self.assertRaises(ValueError):settle_turn(snap,self.spec,d)

    def test_in_transit_not_retargetable_or_reloadable(self):
        snap=self.snap();s,_=settle_turn(snap,self.spec,Decision(snap.snapshot_id,(LoadOrder(1,700000),)))
        again=prepare_turn(s,self.spec,scheduled_by_origin_bbl={'gulf':0})
        with self.assertRaises(ValueError):settle_turn(again,self.spec,Decision(again.snapshot_id,ballasts=(BallastOrder(1,'gulf'),)))
        with self.assertRaises(ValueError):settle_turn(again,self.spec,Decision(again.snapshot_id,(LoadOrder(1,100),)))

    def test_stale_or_tampered_snapshot(self):
        snap=self.snap()
        with self.assertRaises(ValueError):settle_turn(snap,self.spec,Decision('wrong'))
        tampered=replace(snap,quote_json=snap.quote_json+' ')
        with self.assertRaises(ValueError):settle_turn(tampered,self.spec,Decision(snap.snapshot_id))

    def test_no_double_open(self):
        snap=self.snap()
        with self.assertRaises(ValueError):prepare_turn(snap.opened_state,self.spec,scheduled_by_origin_bbl={'gulf':0})

    def test_batch_ids_and_due_dates_validated(self):
        for order in [BatchOrder('bootstrap:x','gulf',1,3),BatchOrder('a','gulf',0,3),BatchOrder('a','bad',1,3),BatchOrder('a','gulf',1,-1)]:
            with self.assertRaises(ValueError):prepare_turn(self.state,self.spec,new_batches=[order])
        with self.assertRaises(ValueError):prepare_turn(self.state,self.spec,new_batches=[BatchOrder('a','gulf',1,3)]*2)
        with self.assertRaises(ValueError):prepare_turn(self.state,self.spec,new_batches=[],scheduled_by_origin_bbl={'gulf':0})

    def test_oldest_age_and_no_loss_when_pressure_saturates(self):
        s=self.state
        for _ in range(15):s,r=step_market(s,self.spec,scheduled_by_origin_bbl={'gulf':1000000},decision_factory=hold_all)
        self.assertEqual(r['routes']['gulf']['origin_unshipped_bbl'],15000000)
        self.assertEqual(r['routes']['gulf']['oldest_unshipped_age_turns'],14)
        self.assertTrue(all(abs(x.pressure_days)<=5 for x in s.signals))
        self.assertEqual(sum(b.total_bbl for b in s.batches),15000000)

    def test_zero_new_cargo_can_ship_old_batch(self):
        s,_=step_market(self.state,self.spec,scheduled_by_origin_bbl={'gulf':700000},decision_factory=hold_all)
        snap=prepare_turn(s,self.spec,scheduled_by_origin_bbl={'gulf':0})
        s,r=settle_turn(snap,self.spec,Decision(snap.snapshot_id,(LoadOrder(1,700000),)))
        self.assertEqual(r['routes']['gulf']['market_status'],'no_new_demand')
        self.assertEqual(r['routes']['gulf']['loaded_bbl'],700000)

    def test_manual_zero_action_does_not_withdraw_offer(self):
        snap=self.snap();_,r=settle_turn(snap,self.spec,Decision(snap.snapshot_id))
        self.assertGreater(r['routes']['gulf']['current_prompt_capacity_bbl'],0)
        self.assertEqual(r['routes']['gulf']['loaded_bbl'],0)

    def test_demo_threshold_is_not_a_physical_limit(self):
        snap=self.snap(100000)
        d=demo_decision(snap,self.spec);self.assertEqual(d.loads,())
        s,r=settle_turn(snap,self.spec,Decision(snap.snapshot_id,(LoadOrder(1,100000),)))
        self.assertEqual(r['routes']['gulf']['loaded_bbl'],100000)

    def test_demo_only_one_partial_hull_per_origin(self):
        snap=self.snap(5300000);d=demo_decision(snap,self.spec)
        partial=[x for x in d.loads if x.cargo_bbl<self.state.ships[x.ship_id-1].capacity_bbl]
        self.assertLessEqual(len(partial),1)
        self.assertTrue(all(x.cargo_bbl/self.state.ships[x.ship_id-1].capacity_bbl>=.7 for x in partial))

    def test_checkpoint_resume_identical(self):
        spec=make_market_spec();s=initial_market(spec,{'vlcc':50,'suezmax':60})
        for row in constant(8):s,_=step_market(s,spec,**row)
        restored=load_state(dump_state(s,spec),spec)
        for row in constant(6):
            s,a=step_market(s,spec,**row,include_events=True)
            restored,b=step_market(restored,spec,**row,include_events=True)
            self.assertEqual(a,b)
        self.assertEqual(s,restored)

    def test_checkpoint_tamper_or_different_spec(self):
        cp=dump_state(self.state,self.spec);cp['state']['turn']=123
        with self.assertRaises(ValueError):load_state(cp,self.spec)
        cfg=load_config();cfg['pricing']['supply_demand_log_sensitivity']=2.
        other=make_market_spec(origins=('gulf',),config=cfg)
        with self.assertRaises(ValueError):load_state(dump_state(self.state,self.spec),other)

    def test_manifest_or_hull_capacity_tampering_detected(self):
        snap=self.snap();s,_=settle_turn(snap,self.spec,Decision(snap.snapshot_id,(LoadOrder(1,700000),)))
        for ship in [replace(s.ships[0],capacity_bbl=2000000),replace(s.ships[0],manifest=())]:
            bad=replace(s,ships=(ship,*s.ships[1:]))
            with self.assertRaises(ValueError):validate_state(bad,self.spec)


class V3AvailabilityAndPrice(unittest.TestCase):
    def setUp(self):self.spec=make_market_spec()

    def destination_empty_snapshot(self):
        spec=make_market_spec(origins=('gulf',))
        state=initial_market(spec,{'vlcc':1},initialization='cold')
        for i in range(3):state,_=step_market(state,spec,scheduled_by_origin_bbl={'gulf':1971000 if i==0 else 0})
        # At t=3 unloading completes, but no return order is decided yet.
        return spec,prepare_turn(state,spec,scheduled_by_origin_bbl={'gulf':0})

    def test_uncommitted_empty_not_guessed(self):
        spec,snap=self.destination_empty_snapshot();a=snap.quotes()['availability']
        self.assertEqual(a['uncommitted_destination_ship_ids'],[1])
        self.assertEqual([b['capacity_bbl'] for b in a['routes']['gulf']],[0,0,0])

    def test_ballast_order_only_enters_next_quote(self):
        spec,snap=self.destination_empty_snapshot()
        s,_=settle_turn(snap,spec,Decision(snap.snapshot_id,ballasts=(BallastOrder(1,'gulf'),)))
        self.assertEqual([b['capacity_bbl'] for b in snap.quotes()['availability']['routes']['gulf']],[0,0,0])
        new=prepare_turn(s,spec,scheduled_by_origin_bbl={'gulf':0})
        self.assertEqual([b['capacity_bbl'] for b in new.quotes()['availability']['routes']['gulf']],[0,1971000,0])
        with self.assertRaises(ValueError):settle_turn(new,spec,Decision(new.snapshot_id,(LoadOrder(1,100),)))

    def test_loaded_ships_do_not_imply_home_return(self):
        spec=make_market_spec(origins=('gulf',));s=initial_market(spec,{'vlcc':1},initialization='cold')
        s,_=step_market(s,spec,scheduled_by_origin_bbl={'gulf':1971000})
        a=build_availability(s,spec)
        self.assertEqual(a['loaded_without_committed_next_origin_ship_ids'],[1])
        self.assertEqual(sum(b['capacity_bbl'] for b in a['routes']['gulf']),0)

    def test_one_hull_once_not_cumulative(self):
        spec,snap=self.destination_empty_snapshot()
        s,_=settle_turn(snap,spec,Decision(snap.snapshot_id,ballasts=(BallastOrder(1,'gulf'),)))
        a=build_availability(s,spec)
        ids=[x['ship_id'] for bs in a['routes'].values() for b in bs for x in b['ships']]
        self.assertEqual(ids,[1])
        with self.assertRaises(ValueError):build_availability(replace(s,ships=(*s.ships,s.ships[0])),spec)

    def test_normal_schedule_anchor_independent_of_horizon_weights(self):
        for weights in ([1.],[1.,.5],[1.,.5,.15],[1.,.8,.4,.1]):
            cfg=load_config();cfg['availability']['arrival_weights']=weights
            spec=make_market_spec(config=cfg);q,_=pure(spec)
            self.assertTrue(all(abs(x['route_benchmark_real_tce']-35000)<.001 for x in q['routes'].values()))

    def test_future_supply_changes_quote_but_not_current_capacity(self):
        q,args=pure(self.spec,now_factor=.7,future_factor=0)
        b=deepcopy(args);b['availability']['routes']['west_africa'][1]['capacity_bbl']=12000000
        after=quote_routes(self.spec,**b)
        self.assertLess(after['routes']['west_africa']['route_benchmark_real_tce'],q['routes']['west_africa']['route_benchmark_real_tce'])
        self.assertEqual(after['routes']['west_africa']['current_prompt_capacity_bbl'],q['routes']['west_africa']['current_prompt_capacity_bbl'])

    def test_arrival_later_has_less_price_relief(self):
        _,args=pure(self.spec,now_factor=.7,future_factor=0)
        one=deepcopy(args);two=deepcopy(args)
        one['availability']['routes']['west_africa'][1]['capacity_bbl']=12000000
        two['availability']['routes']['west_africa'][2]['capacity_bbl']=12000000
        a=quote_routes(self.spec,**one);b=quote_routes(self.spec,**two)
        self.assertLess(a['routes']['west_africa']['route_benchmark_real_tce'],b['routes']['west_africa']['route_benchmark_real_tce'])

    def test_capacity_substitution_in_all_horizons(self):
        spec=make_market_spec(origins=('gulf',))
        large=initial_market(spec,{'vlcc':1},initialization='cold')
        small=initial_market(spec,{'suezmax':2},initialization='cold')
        # Different real hull registries but identical offered capacity.
        sa=prepare_turn(large,spec,scheduled_by_origin_bbl={'gulf':1971000})
        sb=prepare_turn(small,spec,scheduled_by_origin_bbl={'gulf':1971000})
        self.assertEqual(sa.quotes()['routes']['gulf']['route_benchmark_real_tce'],sb.quotes()['routes']['gulf']['route_benchmark_real_tce'])
        large,_=settle_turn(sa,spec,Decision(sa.snapshot_id,(LoadOrder(1,1971000),)))
        small,_=settle_turn(sb,spec,Decision(sb.snapshot_id,(LoadOrder(1,985500),LoadOrder(2,985500))))
        for _ in range(2):
            large,_=step_market(large,spec,scheduled_by_origin_bbl={'gulf':0},decision_factory=hold_all)
            small,_=step_market(small,spec,scheduled_by_origin_bbl={'gulf':0},decision_factory=hold_all)
        sa=prepare_turn(large,spec,scheduled_by_origin_bbl={'gulf':0})
        sb=prepare_turn(small,spec,scheduled_by_origin_bbl={'gulf':0})
        large,_=settle_turn(sa,spec,Decision(sa.snapshot_id,ballasts=(BallastOrder(1,'gulf'),)))
        small,_=settle_turn(sb,spec,Decision(sb.snapshot_id,ballasts=(BallastOrder(1,'gulf'),BallastOrder(2,'gulf'))))
        sa=prepare_turn(large,spec,scheduled_by_origin_bbl={'gulf':1971000})
        sb=prepare_turn(small,spec,scheduled_by_origin_bbl={'gulf':1971000})
        qa,qb=sa.quotes()['routes']['gulf'],sb.quotes()['routes']['gulf']
        self.assertEqual(qa['explanation']['exact_arrival_capacity_bbl'],[0,1971000,0])
        self.assertEqual(qa['route_benchmark_real_tce'],qb['route_benchmark_real_tce'])

    def test_v3_can_exceed_legacy_122500_bound(self):
        spec=make_market_spec(origins=('gulf',));q,_=pure(spec,now_factor=.25,future_factor=.25)
        self.assertGreater(q['routes']['gulf']['route_benchmark_real_tce'],150000)
        self.assertFalse(q['routes']['gulf']['explanation']['numeric_guard_hit'])

    def test_debug_guard_is_flagged_and_trace_reconstructs(self):
        spec=make_market_spec(origins=('gulf',));q,_=pure(spec,now_factor=0,future_factor=0,pressure=5)
        r=q['routes']['gulf'];self.assertTrue(r['explanation']['numeric_guard_hit'])
        self.assertAlmostEqual(r['route_benchmark_real_tce'],1000000,places=3)
        self.assertEqual(recompute_price(r['explanation']),r['route_benchmark_real_tce'])

    def test_urgency_bounded_separately_from_market(self):
        cfg=load_config();a,_=urgency_signal(5,cfg)
        self.assertLess(math.exp(a),1.7)
        calm,_=pure(self.spec,pressure=0);urgent,_=pure(self.spec,pressure=5)
        for o in self.spec.origins:
            self.assertLess(urgent['routes'][o]['route_benchmark_real_tce']/calm['routes'][o]['route_benchmark_real_tce'],math.exp(a)+1e-6)

    def test_local_bound_and_center_both_hold(self):
        _,args=pure(self.spec);args['availability']['routes']['west_africa'][0]['capacity_bbl']=0
        result=quote_routes(self.spec,**args)
        self.assertLess(abs(result['weighted_local_residual']),1e-10)
        self.assertTrue(all(abs(x['explanation']['bounded_centered_local_residual'])<=.6+1e-10 for x in result['routes'].values()))

    def test_bad_supply_numbers_and_duplicate_ship_evidence(self):
        _,args=pure(self.spec)
        bad=deepcopy(args);bad['availability']['routes']['gulf'][0]['capacity_bbl']=True
        with self.assertRaises(ValueError):quote_routes(self.spec,**bad)
        bad=deepcopy(args)
        for o in self.spec.origins:
            b=bad['availability']['routes'][o][0];b['ships']=[dict(ship_id=1,capacity_bbl=b['capacity_bbl'])]
        with self.assertRaises(ValueError):quote_routes(self.spec,**bad)

    def test_nominal_cpi_conversion(self):
        q,args=pure(self.spec);n=quote_routes(self.spec,**args,cpi=200)
        for o in self.spec.origins:self.assertAlmostEqual(n['routes'][o]['route_benchmark_nominal_tce'],2*q['routes'][o]['route_benchmark_real_tce'],places=3)

    def test_partial_opportunity_no_duplicate_ballast(self):
        spec,snap=self.destination_empty_snapshot()
        full=time_only_opportunities(snap,spec,1)[0]
        half=time_only_opportunities(snap,spec,1,cargo_bbl=985500)[0]
        self.assertEqual(full['total_time_to_common_terminal_days'],50)
        self.assertFalse(full['additional_return_leg_charged'])
        self.assertAlmostEqual(half['time_adjusted_service_value_real_usd_per_day']*2,full['time_adjusted_service_value_real_usd_per_day'])

    def test_price_parameters_do_not_choose_ships(self):
        base=make_market_spec();cfg=load_config();cfg['pricing']['supply_demand_log_sensitivity']=1.2
        cfg['availability']['arrival_weights']=[1.]
        changed=make_market_spec(config=cfg)
        a=run_market(base,constant(12),fleet_counts={'vlcc':50,'suezmax':60},warmup_turns=0,include_events=True)
        b=run_market(changed,constant(12),fleet_counts={'vlcc':50,'suezmax':60},warmup_turns=0,include_events=True)
        for x,y in zip(a['turns'],b['turns']):
            self.assertEqual(x['ballast_orders'],y['ballast_orders'])
            self.assertEqual([(z['ship_id'],z['cargo_bbl']) for z in x['departures']],[(z['ship_id'],z['cargo_bbl']) for z in y['departures']])
        self.assertNotEqual(a['summary']['routes']['gulf']['benchmark_median'],b['summary']['routes']['gulf']['benchmark_median'])

    def test_future_input_prefix_cannot_affect_history(self):
        spec=make_market_spec();a=constant(10);b=constant(15)
        b[-1]['scheduled_by_origin_bbl']['west_africa']=99999999
        short=run_market(spec,a,fleet_counts={'vlcc':15,'suezmax':20},warmup_turns=0)
        long=run_market(spec,b,fleet_counts={'vlcc':15,'suezmax':20},warmup_turns=0)
        self.assertEqual(short['turns'],long['turns'][:10])

    def test_quote_recomputable_from_explanation(self):
        for now in (.1,.5,1.06,2.,5.):
            q,_=pure(self.spec,now_factor=now,pressure=3.)
            for r in q['routes'].values():self.assertEqual(recompute_price(r['explanation']),r['route_benchmark_real_tce'])

    def test_real_seed_inputs_match_v2_source_full_cargo(self):
        spec=make_market_spec();a,ident=build_mixed_inputs(spec.physical,seed=42,years=5)
        self.assertFalse(ident['class_partition_applied'])
        self.assertEqual(len(a),216)
        out=run_market(spec,a[:10],fleet_counts={'vlcc':25,'suezmax':20},warmup_turns=0)
        self.assertTrue(out['summary']['conservation_exact'])
        self.assertEqual(sum(r['routes']['gulf']['scheduled_cargo_bbl'] for r in out['turns']),sum(r['scheduled_by_origin_bbl']['gulf'] for r in a[:10]))


if __name__=='__main__':unittest.main()
