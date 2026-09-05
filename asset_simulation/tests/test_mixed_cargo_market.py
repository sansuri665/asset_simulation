"""Quota-free engine contracts, replacement invariance and physical regressions."""
from copy import deepcopy
from dataclasses import asdict, replace
import itertools
import unittest

from asset_simulation.model.global_shipping_contract import CLASSES, load_catalog
from asset_simulation.model.mixed_cargo_contract import load_mixed_config, make_mixed_spec
from asset_simulation.model.mixed_cargo_market import (
    build_mixed_inputs, choose_full_load_mix, initial_mixed_market,
    run_mixed_market, step_mixed_market, validate_mixed_state,
)
from asset_simulation.model.mixed_cargo_pricing import quote_mixed_routes
from asset_simulation.model.registry import sha256_json


def constant(n=80, **volumes):
    q={'gulf':93000000,'west_africa':12000000};q.update(volumes)
    return [{'scheduled_by_origin_bbl':dict(q),'cpi':100.} for _ in range(n)]


def fingerprint(cat):
    cat['catalog_hash']=sha256_json({k:v for k,v in cat.items() if k!='catalog_hash'})
    return cat


class MixedContractsTests(unittest.TestCase):
    def test_fixed_capacity_is_owned_by_ship_class(self):
        spec=make_mixed_spec(origins=('gulf','west_africa','brazil_guyana','us_gulf'))
        expected={'vlcc':1971000,'suezmax':985500,'aframax':584000}
        for lane in spec.lanes:
            self.assertEqual({s.class_id:s.capacity_bbl for s in lane.services},expected)
            for service in lane.services:
                self.assertEqual(service.outbound.cargo_bbl,expected[service.class_id])

    def test_reference_mix_cannot_affect_new_spec_or_simulation(self):
        cat=load_catalog();base=make_mixed_spec(catalog=cat)
        for lane in cat['lanes'].values():
            lane['class_share_bps']={'vlcc':0,'suezmax':0,'aframax':10000}
            lane['share_status']='test_prior'
        mutated=make_mixed_spec(catalog=fingerprint(cat))
        self.assertEqual(base,mutated)
        a=run_mixed_market(base,constant(),fleet_counts={'vlcc':300})
        b=run_mixed_market(mutated,constant(),fleet_counts={'vlcc':300})
        self.assertEqual(a,b)

    def test_benchmark_parcel_overrides_are_not_actual_loads(self):
        cat=load_catalog();a=make_mixed_spec(catalog=cat)
        for lane in cat['lanes'].values():
            lane['cargo_tonnes_by_class']={'vlcc':230000,'suezmax':110000,'aframax':50000}
        b=make_mixed_spec(catalog=fingerprint(cat))
        self.assertEqual(a,b)

    def test_compatibility_is_preserved_even_when_share_is_zero(self):
        cat=load_catalog();a=make_mixed_spec(catalog=cat)
        self.assertIn('aframax',[s.class_id for s in a.lanes[0].services])
        cat['lanes']['gulf::east_asia']['paths']['reference']['allowed_laden_classes']=['suezmax','aframax']
        b=make_mixed_spec(catalog=fingerprint(cat))
        self.assertNotIn('vlcc',[s.class_id for s in b.lane('gulf').services])
        self.assertEqual(b.lane('west_africa').service('vlcc').capacity_bbl,1971000)

    def test_unknown_geography_is_not_free_capacity(self):
        for origins in (['other_export_regions'],['gulf','gulf'],'gulf'):
            with self.assertRaises(ValueError):make_mixed_spec(origins=origins)

    def test_invalid_config_rejected(self):
        for group,key,value in [('pricing','shared_signal_weight',2),('pressure','half_life_turns',0),
                                ('pricing','local_signal_limit',0),('routing','forecast_persistence',1),
                                ('pricing','baseline_real_tce_2025_usd_per_day',float('nan'))]:
            cfg=load_mixed_config();cfg[group][key]=value
            with self.assertRaises(ValueError):make_mixed_spec(config=cfg)


class CapacityQuoteTests(unittest.TestCase):
    def setUp(self):
        self.spec=make_mixed_spec()
        self.args={'scheduled_by_origin_bbl':{'gulf':93000000,'west_africa':12000000},
                   'prompt_by_origin_class':{'gulf':dict(vlcc=40,suezmax=20,aframax=0),
                                             'west_africa':dict(vlcc=5,suezmax=2,aframax=0)},
                   'origin_pressures':{'gulf':0.,'west_africa':0.},'destination_pressure':0.,
                   'previous_signals':{'gulf':0.,'west_africa':0.},'cpi':100.}

    def quote(self, **changes):
        return quote_mixed_routes(self.spec,**{**self.args,**changes})

    def test_one_vlcc_equals_two_suezmax_in_current_capacity(self):
        a=self.quote();new=deepcopy(self.args['prompt_by_origin_class'])
        new['west_africa']={'vlcc':4,'suezmax':4,'aframax':0};b=self.quote(prompt_by_origin_class=new)
        for o in self.spec.origins:
            for field in ('route_benchmark_real_tce','relative_capacity_tightness','net_service_value_real_usd_per_bbl'):
                self.assertEqual(a['routes'][o][field],b['routes'][o][field])

    def test_three_classes_do_not_triple_the_demand(self):
        r=self.quote()
        self.assertEqual(r['scheduled_cargo_counted_once_bbl'],105000000)
        self.assertEqual(r['aggregate_compatible_prompt_capacity_bbl'],56*1971000)

    def test_all_suezmax_and_all_vlcc_equivalent_quotes(self):
        p=self.args['prompt_by_origin_class'];a=self.quote()
        n={o:{'vlcc':0,'suezmax':v['vlcc']*2+v['suezmax'],'aframax':0} for o,v in p.items()}
        b=self.quote(prompt_by_origin_class=n)
        for o in p:self.assertEqual(a['routes'][o]['route_benchmark_real_tce'],b['routes'][o]['route_benchmark_real_tce'])

    def test_class_tce_is_a_declared_net_value_conversion(self):
        r=self.quote()['routes']['west_africa'];values=[]
        for v in r['class_quotes'].values():
            values.append(v['indicative_tce_real_2025_usd_per_day']*v['reference_cycle_days']/v['capacity_bbl'])
        self.assertLess(max(values)-min(values),1e-7)
        self.assertAlmostEqual(r['class_quotes']['vlcc']['indicative_tce_real_2025_usd_per_day'],
                               2*r['class_quotes']['suezmax']['indicative_tce_real_2025_usd_per_day'],delta=.001)

    def test_missing_class_has_indication_not_executable_capacity(self):
        r=self.quote()['routes']['gulf']
        self.assertFalse(r['class_quotes']['aframax']['executable_now'])
        self.assertFalse(r['is_transaction_price'])

    def test_no_supply_no_demand_are_not_cash_revenue(self):
        n={o:dict(vlcc=0,suezmax=0,aframax=0) for o in self.spec.origins}
        r=self.quote(prompt_by_origin_class=n)
        for q in r['routes'].values():self.assertEqual(q['market_status'],'no_supply')
        r=self.quote(scheduled_by_origin_bbl={o:0 for o in self.spec.origins})
        for q in r['routes'].values():self.assertFalse(q['price_observation_available'])

    def test_extra_small_ship_relaxes_local_capacity_price(self):
        a=self.quote();n=deepcopy(self.args['prompt_by_origin_class']);n['west_africa']['suezmax']+=1
        b=self.quote(prompt_by_origin_class=n)
        self.assertLess(b['routes']['west_africa']['route_benchmark_real_tce'],a['routes']['west_africa']['route_benchmark_real_tce'])

    def test_inventory_pressure_bounded_and_directional(self):
        a=self.quote();b=self.quote(destination_pressure=5.)
        for o in self.spec.origins:
            self.assertGreater(b['routes'][o]['route_benchmark_real_tce'],a['routes'][o]['route_benchmark_real_tce'])
            self.assertLessEqual(b['routes'][o]['priced_pressure_days'],5)
        with self.assertRaises(ValueError):self.quote(destination_pressure=6.)

    def test_shared_signal_is_not_an_extra_multiplier(self):
        r=self.quote();self.assertAlmostEqual(r['weighted_local_residual'],0.,places=12)
        cfg=load_mixed_config();cfg['pricing']['shared_signal_weight']=0
        s=make_mixed_spec(config=cfg);r=quote_mixed_routes(s,**self.args)
        for q in r['routes'].values():
            self.assertAlmostEqual(q['settled_signal'],.75*q['raw_local_supply_signal'])

    def test_cpi_changes_only_nominal_quotes(self):
        a=self.quote();b=self.quote(cpi=250.)
        for o in self.spec.origins:
            self.assertEqual(a['routes'][o]['route_benchmark_real_tce'],b['routes'][o]['route_benchmark_real_tce'])
            self.assertAlmostEqual(b['routes'][o]['route_benchmark_nominal_tce'],2.5*a['routes'][o]['route_benchmark_real_tce'],delta=.001)

    def test_invalid_count_and_cpi_rejected(self):
        for value in (-1,True,1.5):
            p=deepcopy(self.args['prompt_by_origin_class']);p['gulf']['vlcc']=value
            with self.assertRaises(ValueError):self.quote(prompt_by_origin_class=p)
        with self.assertRaises(ValueError):self.quote(cpi=float('nan'))


class MixedExecutionTests(unittest.TestCase):
    def setUp(self):self.spec=make_mixed_spec()

    def test_packer_agrees_with_brute_force_small_fleets(self):
        capacities={'vlcc':1971000,'suezmax':985500,'aframax':584000}
        for counts in itertools.product(range(3),repeat=3):
            available=dict(zip(CLASSES,counts))
            for budget in (0,500000,1900000,2800000,6000000):
                got=choose_full_load_mix(budget,capacities,available)
                actual=sum(got[c]*capacities[c] for c in CLASSES)
                possible=[sum(capacities[c]*n for c,n in zip(CLASSES,mix)) for mix in itertools.product(*(range(n+1) for n in counts))]
                self.assertEqual(actual,max(x for x in possible if x<=budget))

    def test_smaller_ship_can_carry_a_remainder_vlcc_cannot(self):
        capacities={'vlcc':1971000,'suezmax':985500,'aframax':584000}
        a=choose_full_load_mix(3000000,capacities,dict(vlcc=2,suezmax=0,aframax=0))
        b=choose_full_load_mix(3000000,capacities,dict(vlcc=1,suezmax=2,aframax=0))
        self.assertEqual(a['vlcc'],1)
        self.assertGreater(sum(b[c]*capacities[c] for c in CLASSES),1971000)

    def test_manual_mix_changes_execution_not_same_turn_quotes(self):
        state=initial_mixed_market(self.spec,dict(vlcc=20,suezmax=40,aframax=0),initialization='cold')
        item={'scheduled_by_origin_bbl':{'gulf':1971000,'west_africa':0},'ballast_orders':{}}
        a,ra=step_mixed_market(state,self.spec,**item,dispatch_by_origin_class={'gulf':{'vlcc':1},'west_africa':{}})
        b,rb=step_mixed_market(state,self.spec,**item,dispatch_by_origin_class={'gulf':{'suezmax':2},'west_africa':{}})
        self.assertEqual(ra['routes']['gulf']['loaded_bbl'],rb['routes']['gulf']['loaded_bbl'])
        self.assertEqual(ra['routes']['gulf']['route_benchmark_real_tce'],rb['routes']['gulf']['route_benchmark_real_tce'])
        self.assertEqual(a.ledgers,b.ledgers)
        self.assertNotEqual(a.ships,b.ships)

    def test_manual_mix_rejects_overbooking_and_nonexistent_oil(self):
        state=initial_mixed_market(self.spec,dict(vlcc=10,suezmax=0),initialization='cold')
        item=constant(1)[0]
        for mix in ({'gulf':{'vlcc':999},'west_africa':{}},{'gulf':{'suezmax':1},'west_africa':{}},
                    {'gulf':{'unknown':1},'west_africa':{}},{'gulf':{'vlcc':True},'west_africa':{}}):
            with self.assertRaises(ValueError):step_mixed_market(state,self.spec,**item,dispatch_by_origin_class=mix)
        with self.assertRaises(ValueError):
            step_mixed_market(state,self.spec,scheduled_by_origin_bbl={'gulf':1,'west_africa':0},
                              dispatch_by_origin_class={'gulf':{'vlcc':1},'west_africa':{}})

    def test_all_three_classes_exact_conservation_and_unique_dispatch(self):
        run=run_mixed_market(self.spec,constant(100),fleet_counts=dict(vlcc=230,suezmax=120,aframax=40),include_events=True)
        expected={'vlcc':1971000,'suezmax':985500,'aframax':584000}
        self.assertTrue(run['summary']['conservation_exact'])
        for r in run['turns']:
            used=[e['ship_id'] for e in r['departures']+r['ballast_orders']]
            self.assertEqual(len(used),len(set(used)))
            for e in r['departures']:self.assertEqual(e['cargo_bbl'],expected[e['class_id']])
            for q in r['routes'].values():self.assertEqual(q['loaded_bbl'],sum(q['loaded_by_class_bbl'].values()))

    def test_same_ship_keeps_capacity_after_changing_origin(self):
        run=run_mixed_market(self.spec,constant(100),fleet_counts={'vlcc':240,'suezmax':120},include_events=True)
        cargo={};origins={}
        for r in run['turns']:
            for d in r['departures']:
                sid=d['ship_id']
                if sid in cargo:self.assertEqual(cargo[sid],d['cargo_bbl'])
                cargo[sid]=d['cargo_bbl'];origins.setdefault(sid,set()).add(d['origin'])
        self.assertTrue(any(len(v)>1 for v in origins.values()))

    def test_delivery_once_and_actual_arrival_lock(self):
        run=run_mixed_market(self.spec,constant(90),fleet_counts={'vlcc':240,'suezmax':120},include_events=True)
        planned={};delivered=set()
        for r in run['turns']:
            for e in r['departures']:
                key=e['ship_id'],e['ready_turn'];self.assertNotIn(key,planned);planned[key]=e['cargo_bbl']
            for e in r['deliveries']:
                key=e['ship_id'],e['ready_turn'];self.assertNotIn(key,delivered);delivered.add(key)
                if key in planned:self.assertEqual(planned[key],e['cargo_bbl'])
        cutoff=run['turns'][-1]['internal_movement_turn']
        self.assertTrue(all(k in delivered for k in planned if k[1]<=cutoff))

    def test_empty_destination_ships_are_not_local_prompt(self):
        s=initial_mixed_market(self.spec,{'vlcc':8,'suezmax':10},initialization='cold')
        s=replace(s,ships=tuple(replace(x,location='east_asia') for x in s.ships))
        _,r=step_mixed_market(s,self.spec,**constant(1)[0],include_events=True)
        self.assertTrue(all(q['compatible_prompt_capacity_bbl']==0 and q['loaded_bbl']==0 for q in r['routes'].values()))
        self.assertEqual(len(r['ballast_orders']),18)
        self.assertTrue(all(x['ready_turn']>r['internal_movement_turn'] for x in r['ballast_orders']))

    def test_class_swap_and_cargo_tampering_fail(self):
        s=initial_mixed_market(self.spec,{'vlcc':10,'suezmax':8})
        with self.assertRaises(ValueError):validate_mixed_state(replace(s,ships=(replace(s.ships[0],class_id='suezmax'),*s.ships[1:])),self.spec)
        l=replace(s.ledgers[0],loaded_bbl=1)
        with self.assertRaises(ValueError):validate_mixed_state(replace(s,ledgers=(l,*s.ledgers[1:])),self.spec)

    def test_zero_fleet_no_ghost_cargo(self):
        r=run_mixed_market(self.spec,constant(40),fleet_counts={})
        for row in r['turns']:
            for q in row['routes'].values():self.assertEqual(q['loaded_bbl']+q['delivered_bbl'],0)
        self.assertTrue(r['summary']['conservation_exact'])

    def test_no_new_demand_drains_old_full_loads(self):
        inp=constant(60)+constant(180,gulf=0,west_africa=0)
        r=run_mixed_market(self.spec,inp,fleet_counts={'suezmax':480})
        for q in r['turns'][-1]['routes'].values():
            self.assertLess(q['origin_unshipped_bbl'],985500)
            self.assertEqual(q['actual_in_transit_bbl'],0)
            self.assertFalse(q['price_observation_available'])

    def test_single_class_any_mix_no_reference_share_required(self):
        for fleet in ({'vlcc':300},{'suezmax':600},{'aframax':1000}):
            r=run_mixed_market(self.spec,constant(55),fleet_counts=fleet)
            for q in r['summary']['routes'].values():
                c=next(iter(fleet));self.assertEqual(q['classes'][c]['realized_cargo_share'],1.)

    def test_price_parameters_cannot_change_default_physical_execution(self):
        cfg=load_mixed_config();cfg['pricing']['shared_signal_weight']=0;cfg['pricing']['supply_demand_log_sensitivity']=.5
        alt=make_mixed_spec(config=cfg)
        a=run_mixed_market(self.spec,constant(60));b=run_mixed_market(alt,constant(60))
        for x,y in zip(a['turns'],b['turns']):
            for o in self.spec.origins:
                for key in ('loaded_bbl','delivered_bbl','origin_unshipped_bbl','dispatched_by_class'):
                    self.assertEqual(x['routes'][o][key],y['routes'][o][key])

    def test_resume_and_future_prefix_are_deterministic(self):
        s=initial_mixed_market(self.spec);original=sha256_json(asdict(s))
        a=s
        for inp in constant(40):a,_=step_mixed_market(a,self.spec,**inp)
        b=s
        for inp in constant(17):b,_=step_mixed_market(b,self.spec,**inp)
        for inp in constant(23):b,_=step_mixed_market(b,self.spec,**inp)
        self.assertEqual(a,b);self.assertEqual(original,sha256_json(asdict(s)))
        x=run_mixed_market(self.spec,constant(50));y=run_mixed_market(self.spec,constant(50)+constant(10,west_africa=50000000))
        self.assertEqual(x['turns'],y['turns'][:50])

    def test_external_ballast_is_validated_and_does_not_teleport(self):
        s=initial_mixed_market(self.spec,{'suezmax':8},initialization='cold')
        no_dispatch={o:{} for o in self.spec.origins}
        target='west_africa' if s.ships[0].location=='gulf' else 'gulf'
        a,r=step_mixed_market(s,self.spec,**constant(1)[0],dispatch_by_origin_class=no_dispatch,ballast_orders={1:target},include_events=True)
        self.assertIsNone(a.ships[0].location);self.assertGreater(a.ships[0].movement.ready_turn,r['internal_movement_turn'])
        with self.assertRaises(ValueError):step_mixed_market(a,self.spec,**constant(1)[0],dispatch_by_origin_class=no_dispatch,ballast_orders={1:target})


class MixedSeedTests(unittest.TestCase):
    def test_full_route_not_vlcc_fraction_and_no_upstream_mutation(self):
        spec=make_mixed_spec();inputs,source=build_mixed_inputs(spec,seed=42,years=5)
        self.assertFalse(source['class_partition_applied']);self.assertTrue(source['source_unchanged'])
        for row in inputs:
            for o,rate in row['source_route_cargo_mbd'].items():
                self.assertAlmostEqual(row['scheduled_by_origin_bbl'][o],rate*1e7,delta=.5001)
            self.assertEqual(row['cpi_information_year'],max(2025,row['year']-1))

    def test_actual_seed_and_four_origin_extension(self):
        spec=make_mixed_spec(origins=('gulf','west_africa','brazil_guyana','us_gulf'))
        inputs,_=build_mixed_inputs(spec,seed=42,years=5)
        run=run_mixed_market(spec,inputs,fleet_counts={'vlcc':350,'suezmax':180,'aframax':20})
        self.assertEqual(len(run['summary']['routes']),4);self.assertTrue(run['summary']['conservation_exact'])


if __name__=='__main__':unittest.main()
