"""A--J acceptance and adversarial cases for the dynamic freight board."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
import unittest
from asset_simulation.model.freight_board import (
    BallastOrder, BoardSession, ImportRequirement, InventoryBand,
    make_board_spec, open_board, quote_trial, recompute_trial_price,
)
from asset_simulation.model.freight_board.inventory import stock_report
from asset_simulation.model.registry import sha256_json
from asset_simulation.model.shipping_v3 import make_market_spec
from asset_simulation.model.global_shipping_contract import load_catalog
V=1_971_000
ZERO={'gulf':0,'west_africa':0}


def setup(fleet=None, **options):
    spec=make_board_spec()
    return spec, BoardSession(spec, fleet_counts=fleet or {'vlcc':12,'suezmax':8,'aframax':4}, initialization='cold', **options)


def offered(snap):
    return {o: [s.ship_id for s in snap.opened_market.ships if s.location==o] for o in snap.spec.origins}


def empty():
    return {'gulf':[],'west_africa':[]}


class BoardContracts(unittest.TestCase):
    def test_no_class_quotas_in_spec(self):
        cat=deepcopy(load_catalog())
        for lane in cat['lanes'].values():
            lane['class_share_bps']={'vlcc':0,'suezmax':0,'aframax':10000}
        cat['catalog_hash']=sha256_json({k:v for k,v in cat.items() if k!='catalog_hash'})
        self.assertEqual(make_board_spec().identity, make_board_spec(market=make_market_spec(catalog=cat)).identity)

    def test_snapshot_alteration_detected(self):
        _, s=setup(); snap=s.open_turn(source_release_bbl=ZERO)
        bad=replace(snap,opened_market=replace(snap.opened_market,destination_pressure=1))
        with self.assertRaises(ValueError):quote_trial(bad,empty(),ZERO)

    def test_missing_negative_and_nonfinite_inputs(self):
        _, s=setup()
        with self.assertRaises(ValueError):s.open_turn(source_release_bbl={'gulf':0})
        with self.assertRaises(ValueError):s.open_turn(source_release_bbl={'gulf':True,'west_africa':0})
        snap=s.open_turn(source_release_bbl=ZERO)
        for q in (-1,True,float('nan')):
            with self.assertRaises(ValueError):quote_trial(snap,empty(),{'gulf':q,'west_africa':0})

    def test_duplicate_wrong_origin_unknown_ship(self):
        _,s=setup();snap=s.open_turn(source_release_bbl=ZERO);sid=offered(snap)['gulf'][0]
        for p in ({'gulf':[sid,sid],'west_africa':[]},{'gulf':[],'west_africa':[sid]},{'gulf':[9999],'west_africa':[]}):
            with self.assertRaises(ValueError):quote_trial(snap,p,ZERO)

    def test_load_and_ballast_same_hull_rejected(self):
        _,s=setup();snap=s.open_turn(source_release_bbl=ZERO);sid=offered(snap)['gulf'][0]
        with self.assertRaises(ValueError):
            quote_trial(snap,{'gulf':[sid],'west_africa':[]},ZERO,ballast_orders=(BallastOrder(sid,'west_africa'),))

    def test_requirement_cannot_duplicate_or_rewrite_past(self):
        _,s=setup();r=ImportRequirement('r',3,10)
        with self.assertRaises(ValueError):s.open_turn(source_release_bbl=ZERO,new_requirements=(r,r))
        x=s.open_turn(source_release_bbl=ZERO,new_requirements=(r,));s.commit(quote_trial(x,empty(),ZERO))
        with self.assertRaises(ValueError):s.open_turn(source_release_bbl=ZERO,new_requirements=(r,))
        with self.assertRaises(ValueError):s.open_turn(source_release_bbl=ZERO,new_requirements=(ImportRequirement('p',0,1),))

    def test_no_source_teleport_or_export_cap_bypass(self):
        _,s=setup(source_stock_bbl=ZERO)
        x=s.open_turn(source_release_bbl={'gulf':2*V,'west_africa':1},export_limits_bbl={'gulf':V,'west_africa':1})
        for q in ({'gulf':V+1,'west_africa':0},{'gulf':0,'west_africa':2}):
            with self.assertRaises(ValueError):quote_trial(x,offered(x),q)

    def test_inventory_band_validation(self):
        for b in (InventoryBand(0,-1,-1,1,2),InventoryBand(100,-101,-50,50,100)):
            with self.assertRaises(ValueError):make_board_spec(destination_band=b)

    def test_preview_does_not_silently_expand_to_globe(self):
        with self.assertRaises(ValueError):make_board_spec(market=make_market_spec(origins=('gulf',)))


class TrialAlgebra(unittest.TestCase):
    def setUp(self):
        self.spec,self.session=setup();self.snap=self.session.open_turn(source_release_bbl=ZERO)
        self.ships=offered(self.snap)

    def test_exact_full_marginal_unused(self):
        g=[s.ship_id for s in self.snap.opened_market.ships if s.location=='gulf' and s.class_id=='vlcc'][:4]
        r=quote_trial(self.snap,{'gulf':g,'west_africa':[]},{'gulf':5_000_000,'west_africa':0}).report()['routes']['gulf']['allocation']
        self.assertEqual([x['assigned_cargo_bbl'] for x in r['ships']],[V,V,1_058_000,0])
        self.assertEqual((r['full_ship_count'],r['marginal_ship_id'],r['unused_ship_count']),(2,g[2],1))
        self.assertAlmostEqual(r['marginal_load_factor'],1_058_000/V)

    def test_order_is_not_reoptimized(self):
        ss=self.snap.opened_market.ships
        v=next(s.ship_id for s in ss if s.location=='gulf' and s.class_id=='vlcc')
        a=next(s.ship_id for s in ss if s.location=='gulf' and s.class_id=='aframax')
        q={'gulf':V,'west_africa':0}
        x=quote_trial(self.snap,{'gulf':[v,a],'west_africa':[]},q).report()['routes']['gulf']
        y=quote_trial(self.snap,{'gulf':[a,v],'west_africa':[]},q).report()['routes']['gulf']
        self.assertEqual(x['route_benchmark_real_tce'],y['route_benchmark_real_tce'])
        self.assertIsNone(x['allocation']['marginal_ship_id']);self.assertEqual(y['allocation']['marginal_ship_id'],v)

    def test_more_ships_reduces_price_reserve_counted_once(self):
        values=[]
        for n in range(1,len(self.ships['gulf'])+1):
            r=quote_trial(self.snap,{'gulf':self.ships['gulf'][:n],'west_africa':self.ships['west_africa']},
                          {'gulf':5*V,'west_africa':V}).report()['routes']['gulf']
            values.append(r['route_benchmark_real_tce'])
            self.assertEqual(r['current_prompt_capacity_bbl']+r['reserve_capacity_bbl'],
                             sum(s.capacity_bbl for s in self.snap.opened_market.ships if s.location=='gulf'))
        self.assertTrue(all(a>b for a,b in zip(values,values[1:])))

    def test_more_cargo_increases_price(self):
        values=[quote_trial(self.snap,self.ships,{'gulf':q,'west_africa':V}).report()['routes']['gulf']['route_benchmark_real_tce']
                for q in (0,V,2*V,5*V,10*V)]
        self.assertTrue(all(a<b for a,b in zip(values,values[1:])))

    def test_order_transfer_two_price_directions(self):
        a=quote_trial(self.snap,self.ships,{'gulf':8*V,'west_africa':3*V}).report()
        b=quote_trial(self.snap,self.ships,{'gulf':9*V,'west_africa':2*V}).report()
        self.assertLess(a['routes']['gulf']['route_benchmark_real_tce'],b['routes']['gulf']['route_benchmark_real_tce'])
        self.assertGreater(a['routes']['west_africa']['route_benchmark_real_tce'],b['routes']['west_africa']['route_benchmark_real_tce'])
        self.assertEqual(a['actual_required_imports_counted_once_bbl'],b['actual_required_imports_counted_once_bbl'])

    def test_equal_capacity_class_substitution(self):
        ss=self.snap.opened_market.ships
        v=next(s.ship_id for s in ss if s.class_id=='vlcc' and s.location=='gulf')
        small=[s.ship_id for s in ss if s.class_id=='suezmax' and s.location=='gulf'][:2]
        a=quote_trial(self.snap,{'gulf':[v],'west_africa':[]},{'gulf':V,'west_africa':0}).report()['routes']['gulf']
        b=quote_trial(self.snap,{'gulf':small,'west_africa':[]},{'gulf':V,'west_africa':0}).report()['routes']['gulf']
        self.assertEqual(a['route_benchmark_real_tce'],b['route_benchmark_real_tce'])
        self.assertEqual(a['allocation']['loaded_bbl'],b['allocation']['loaded_bbl'])

    def test_partial_value_linear_not_double_discount(self):
        g=self.ships['gulf'][0]
        r=quote_trial(self.snap,{'gulf':[g],'west_africa':[]},{'gulf':700_000,'west_africa':0}).report()['routes']['gulf']
        x=r['allocation']['ships'][0]
        self.assertAlmostEqual(x['actual_load_reference_tce'],x['full_load_reference_tce']*x['load_factor'])
        self.assertAlmostEqual(x['service_value_real_usd'],700_000*r['net_service_value_real_usd_per_bbl'])

    def test_unselected_reserve_is_priced_without_loading(self):
        r=quote_trial(self.snap,empty(),{'gulf':V,'west_africa':0}).report()['routes']['gulf']
        self.assertGreater(r['reserve_capacity_bbl'],0);self.assertGreater(r['normalized_capacity_bbl'],0)
        self.assertEqual(r['allocation']['loaded_bbl'],0)

    def test_quote_reconstruction(self):
        for r in quote_trial(self.snap,self.ships,{'gulf':7*V,'west_africa':V}).report()['routes'].values():
            self.assertEqual(recompute_trial_price(r['explanation']),r['route_benchmark_real_tce'])

    def test_thousand_trials_no_memory_or_snapshot_change(self):
        before=sha256_json(asdict(self.snap))
        reference=quote_trial(self.snap,self.ships,{'gulf':5*V,'west_africa':V})
        for i in range(1000):
            quote_trial(self.snap,{'gulf':self.ships['gulf'][:1+i%len(self.ships['gulf'])],'west_africa':self.ships['west_africa']},
                        {'gulf':(i%10+1)*V,'west_africa':V})
        self.assertEqual(before,sha256_json(asdict(self.snap)))
        self.assertEqual(reference,quote_trial(self.snap,self.ships,{'gulf':5*V,'west_africa':V}))

    def test_reordering_only_changes_allocation(self):
        a=quote_trial(self.snap,self.ships,{'gulf':V,'west_africa':V}).report()
        b=quote_trial(self.snap,{o:list(reversed(ids)) for o,ids in self.ships.items()},{'gulf':V,'west_africa':V}).report()
        for o in self.spec.origins:
            self.assertEqual(a['routes'][o]['route_benchmark_real_tce'],b['routes'][o]['route_benchmark_real_tce'])

    def test_nominal_values_scale_once(self):
        r=quote_trial(self.snap,self.ships,{'gulf':V,'west_africa':V}).report()['routes']['gulf']
        snap=open_board(self.session.state,self.spec,source_release_bbl=ZERO,cpi=200)
        r2=quote_trial(snap,self.ships,{'gulf':V,'west_africa':V}).report()['routes']['gulf']
        self.assertEqual(r['route_benchmark_real_tce'],r2['route_benchmark_real_tce'])
        self.assertAlmostEqual(r2['route_benchmark_nominal_tce'],2*r['route_benchmark_real_tce'],places=3)


class InventoryAndTiming(unittest.TestCase):
    def test_order_is_not_current_arrival(self):
        _,s=setup();x=s.open_turn(source_release_bbl=ZERO);p=offered(x)
        a=quote_trial(x,p,{'gulf':V,'west_africa':0}).report()['inventory']['destination']
        b=quote_trial(x,p,{'gulf':2*V,'west_africa':0}).report()['inventory']['destination']
        self.assertEqual(a['closing_current_turn'],b['closing_current_turn'])
        self.assertEqual(b['future_path'][3]['stock_bbl']-a['future_path'][3]['stock_bbl'],V)

    def test_hard_upper_checks_arrival_and_invalid_commit_atomic(self):
        spec=make_board_spec(destination_band=InventoryBand(10_000_000,-10_000_000,-2_000_000,2_000_000,3_000_000))
        s=BoardSession(spec,fleet_counts={'vlcc':8},initialization='cold');x=s.open_turn(source_release_bbl=ZERO)
        r=quote_trial(x,offered(x),{'gulf':2*V,'west_africa':0});before=s.state.identity
        self.assertFalse(r.report()['valid']);self.assertIn('destination:turn:3',r.report()['inventory']['hard_bound_violations'])
        with self.assertRaises(ValueError):s.commit(r)
        self.assertEqual(before,s.state.identity)

    def test_empty_stock_not_repaired_by_not_arrived_oil(self):
        _,s=setup(destination_stock_bbl=1)
        x=s.open_turn(source_release_bbl=ZERO,new_requirements=(ImportRequirement('urgent',0,2),))
        r=quote_trial(x,offered(x),{'gulf':V,'west_africa':0}).report()
        self.assertFalse(r['valid']);self.assertIn('destination:turn:0',r['inventory']['hard_bound_violations'])

    def test_known_due_requires_timely_arrival(self):
        _,s=setup(destination_stock_bbl=100)
        x=s.open_turn(source_release_bbl=ZERO,new_requirements=(ImportRequirement('r',3,V),))
        self.assertFalse(quote_trial(x,empty(),ZERO).report()['valid'])
        self.assertTrue(quote_trial(x,offered(x),{'gulf':V,'west_africa':0}).report()['valid'])

    def test_soft_pressure_continuous(self):
        b=InventoryBand(100000,-100000,-20000,20000,100000)
        vals=[stock_report(n,b)['soft_pressure'] for n in (119999,120000,120001)]
        self.assertTrue(vals[0]<vals[1]<vals[2]);self.assertLess(vals[2]-vals[0],.0001)
        self.assertEqual(stock_report(100000,b)['soft_pressure'],0)

    def test_source_overflow_not_clipped(self):
        _,s=setup();x=s.open_turn(source_release_bbl={'gulf':2_000_000_000,'west_africa':0})
        r=quote_trial(x,empty(),ZERO).report()
        self.assertFalse(r['valid']);self.assertGreater(r['inventory']['sources']['gulf']['closing']['stock_bbl'],2_000_000_000)
        self.assertEqual(r['inventory']['physical_mass_balance_residual_bbl'],0)

    def test_inventory_plan_is_assertion_not_free_barrels(self):
        _,s=setup();x=s.open_turn(source_release_bbl=ZERO)
        with self.assertRaises(ValueError):quote_trial(x,empty(),ZERO,trial_inventory_plan={0:0})

    def test_future_commitment_not_loadable_early(self):
        spec,s=setup({'vlcc':6});st=s.state
        st=replace(st,market=replace(st.market,ships=(replace(st.market.ships[0],location='east_asia'),*st.market.ships[1:])))
        s=BoardSession(spec,st);x=s.open_turn(source_release_bbl=ZERO)
        t=quote_trial(x,empty(),ZERO,ballast_orders=(BallastOrder(1,'west_africa'),))
        e=next(i for i in t.report()['routes']['west_africa']['availability_evidence'] if i['ship_id']==1)
        self.assertEqual((e['basis'],e['horizon_turns']),('proposed',3))
        s.commit(t);x=s.open_turn(source_release_bbl=ZERO)
        e=next(i for i in quote_trial(x,empty(),ZERO).report()['routes']['west_africa']['availability_evidence'] if i['ship_id']==1)
        self.assertEqual((e['basis'],e['horizon_turns']),('committed',2))
        with self.assertRaises(ValueError):quote_trial(x,{'gulf':[],'west_africa':[1]},{'gulf':0,'west_africa':V})

    def test_uncommitted_destination_hulls_not_guessed(self):
        spec,s=setup({'vlcc':6});st=s.state
        st=replace(st,market=replace(st.market,ships=tuple(replace(x,location='east_asia') for x in st.market.ships)))
        x=open_board(st,spec,source_release_bbl=ZERO)
        self.assertTrue(all(not r['availability_evidence'] for r in quote_trial(x,empty(),ZERO).report()['routes'].values()))

    def test_ballast_target_changes_future_not_current_capacity(self):
        spec,s=setup({'vlcc':6});st=s.state
        st=replace(st,market=replace(st.market,ships=tuple(replace(x,location='east_asia') for x in st.market.ships)))
        x=open_board(st,spec,source_release_bbl=ZERO);q={'gulf':V,'west_africa':V}
        a=quote_trial(x,empty(),q,ballast_orders=(BallastOrder(1,'gulf'),)).report()
        b=quote_trial(x,empty(),q,ballast_orders=(BallastOrder(1,'west_africa'),)).report()
        for o in spec.origins:self.assertEqual(a['routes'][o]['current_prompt_capacity_bbl'],0)
        self.assertLess(a['routes']['gulf']['route_benchmark_real_tce'],b['routes']['gulf']['route_benchmark_real_tce'])
        self.assertGreater(a['routes']['west_africa']['route_benchmark_real_tce'],b['routes']['west_africa']['route_benchmark_real_tce'])


class Settlement(unittest.TestCase):
    def setUp(self):
        self.spec,self.s=setup({'vlcc':6});self.x=self.s.open_turn(source_release_bbl=ZERO)
        self.t=quote_trial(self.x,{'gulf':offered(self.x)['gulf'][:3],'west_africa':[]},{'gulf':5_000_000,'west_africa':0})

    def test_final_quote_and_exact_manifest(self):
        r=self.s.commit(self.t);ds=r['physical_execution']['departures']
        self.assertEqual([d['cargo_bbl'] for d in ds],[V,V,1_058_000])
        p=self.t.report()['routes']['gulf']['net_service_value_real_usd_per_bbl']
        self.assertTrue(all(d['booked_net_service_reference_value_real']==p*d['cargo_bbl'] for d in ds))
        self.assertTrue(all(d['ready_turn']==3 for d in ds));self.assertEqual(self.s.state.market.turn,1)

    def test_commit_once(self):
        self.s.commit(self.t);before=self.s.state.identity
        with self.assertRaises(ValueError):self.s.commit(self.t)
        self.assertEqual(before,self.s.state.identity)

    def test_concurrent_commit_once(self):
        def f(_):
            try:self.s.commit(self.t);return 1
            except ValueError:return 0
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(f,range(2))),1)
        self.assertEqual(self.s.state.market.turn,1)

    def test_altered_report_and_valid_retry(self):
        before=self.s.state.identity
        with self.assertRaises(ValueError):self.s.commit(replace(self.t,report_json='{}'))
        self.assertEqual(before,self.s.state.identity);self.s.commit(self.t)

    def test_restore_continue_identical(self):
        self.s.commit(self.t);other=BoardSession.restore(self.s.checkpoint(),self.spec)
        for s in (self.s,other):
            for _ in range(5):
                x=s.open_turn(source_release_bbl=ZERO);s.commit(quote_trial(x,empty(),ZERO))
        self.assertEqual(self.s.state,other.state)
        self.assertEqual(sum(b.delivered_bbl for b in other.state.market.batches),5_000_000)

    def test_open_twice_and_unsettled_checkpoint_rejected(self):
        with self.assertRaises(ValueError):self.s.open_turn(source_release_bbl=ZERO)
        with self.assertRaises(ValueError):self.s.checkpoint()

    def test_unused_hulls_remain_reserves_next_turn(self):
        self.s.commit(self.t);x=self.s.open_turn(source_release_bbl=ZERO)
        r=quote_trial(x,empty(),ZERO).report()['routes']['gulf']
        self.assertGreater(r['reserve_capacity_bbl'],0)

    def test_zero_orders_do_not_erase_import_needs(self):
        self.s.commit(self.t);r=ImportRequirement('later',8,100)
        x=self.s.open_turn(source_release_bbl=ZERO,new_requirements=(r,));self.s.commit(quote_trial(x,empty(),ZERO))
        self.assertIn(r,self.s.state.requirements)


class AdditionalPhysicalControls(unittest.TestCase):
    def test_different_ship_times_have_different_normalizers(self):
        cat=deepcopy(load_catalog())
        cat['vessel_classes']['suezmax']['ballast_speed_knots']=6.0
        cat['catalog_hash']=sha256_json({k:v for k,v in cat.items() if k!='catalog_hash'})
        spec=make_board_spec(market=make_market_spec(catalog=cat))
        session=BoardSession(spec,fleet_counts={'vlcc':12,'suezmax':12},initialization='cold')
        x=session.open_turn(source_release_bbl=ZERO)
        r=quote_trial(x,offered(x),{'gulf':5*V,'west_africa':V}).report()['routes']['gulf']
        by=r['explanation']['per_class_capacity_and_normalization']
        self.assertNotEqual(by['vlcc']['return_turns'],by['suezmax']['return_turns'])
        self.assertNotEqual(by['vlcc']['normalizer'],by['suezmax']['normalizer'])
        self.assertEqual(recompute_trial_price(r['explanation']),r['route_benchmark_real_tce'])

    def test_higher_destination_stock_reduces_urgency(self):
        spec=make_board_spec();normal=spec.destination_band.normal_bbl
        prices=[]
        for stock in (normal-50_000_000,normal,normal+50_000_000):
            session=BoardSession(spec,fleet_counts={'vlcc':12},initialization='cold',destination_stock_bbl=stock)
            x=session.open_turn(source_release_bbl=ZERO)
            prices.append(quote_trial(x,offered(x),{'gulf':5*V,'west_africa':V}).report()['routes']['gulf']['route_benchmark_real_tce'])
        self.assertTrue(prices[0]>prices[1]>prices[2])

    def test_far_future_requirement_is_rejected_not_unbounded_allocation(self):
        _,s=setup()
        with self.assertRaises(ValueError):
            s.open_turn(source_release_bbl=ZERO,new_requirements=(ImportRequirement('unbounded',10**12,1),))

    def test_checkpoint_tampering_and_replay_after_restore(self):
        spec,s=setup();x=s.open_turn(source_release_bbl=ZERO);trial=quote_trial(x,empty(),ZERO);s.commit(trial)
        restored=BoardSession.restore(s.checkpoint(),spec)
        with self.assertRaises(ValueError):restored.commit(trial)
        cp=s.checkpoint();cp['payload']['initial_destination_bbl']+=1
        with self.assertRaises(ValueError):BoardSession.restore(cp,spec)

    def test_commit_mass_matches_trial_for_future_delivery(self):
        spec,s=setup();x=s.open_turn(source_release_bbl={'gulf':7_000_000,'west_africa':2_000_000})
        trial=quote_trial(x,offered(x),{'gulf':5_000_000,'west_africa':1_000_000});r=trial.report()
        committed=s.commit(trial)
        self.assertEqual(committed['physical_execution']['barrel_conservation_residual'],0)
        for o in spec.origins:
            self.assertEqual(sum(b.remaining_bbl for b in s.state.market.batches if b.origin==o),
                             r['inventory']['sources'][o]['closing']['stock_bbl'])


if __name__=='__main__':unittest.main()
