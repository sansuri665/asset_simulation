"""Executable acceptance controls for the selected multi-origin VLCC market."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict, replace
import math
import unittest

from asset_simulation.model.bounded_route_pricing import load_bounded_pricing_config, price_bounded_route_turn
from asset_simulation.model.global_shipping_contract import load_catalog, apportion_barrels
from asset_simulation.model.global_shipping_projection import whole_barrels
from asset_simulation.model.multi_origin_market import (
    build_seeded_inputs, initial_network, load_network_config, make_network_spec,
    run_network, step_network, validate_state,
)
from asset_simulation.model.multi_origin_pricing import quote_origin_route
from asset_simulation.model.registry import sha256_json


def constant_inputs(count=120, *, whole=True):
    cargo = {'gulf': 42 * 1971000, 'west_africa': 5 * 1898000} if whole else {'gulf':83700000, 'west_africa':9600000}
    return [{'scheduled_by_origin_bbl': dict(cargo), 'cpi':100.0} for _ in range(count)]


class MultiOriginPricingTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_network_config()
        self.args = dict(pair_id='gulf::east_asia', scheduled_bbl=93000000, parcel_bbl=1971000,
                         reference_daily_bbl=9300000.0, prompt_ships=50,
                         origin_pressure_days=0.0, destination_pressure_days=0.0,
                         previous_real_tce=35000.0, cpi=100.0, config=self.cfg)

    def test_generalized_formula_reduces_to_stage5a_when_inputs_match(self):
        for ships in (0,15,30,45,50,70,100):
            for pressure in (0,1,3,5):
                old = price_bounded_route_turn(structural_cargo_mbd=9.3, turn_days=10,
                        prompt_supply_vlcc=ships, pricing_pressure_days=pressure)
                new = quote_origin_route(**{**self.args, 'prompt_ships':ships,
                          'origin_pressure_days':pressure, 'destination_pressure_days':pressure})
                self.assertAlmostEqual(old['real_tce_2025_usd_per_day'], new['real_tce_2025_usd_per_day'], delta=.02)

    def test_prompt_and_pressure_directions(self):
        quote = lambda **changes: quote_origin_route(**{**self.args, **changes})['real_tce_2025_usd_per_day']
        self.assertGreater(quote(prompt_ships=40), quote())
        self.assertLess(quote(prompt_ships=60), quote())
        self.assertGreater(quote(origin_pressure_days=3), quote())
        self.assertGreater(quote(destination_pressure_days=3), quote())

    def test_reference_and_smoothing_scale_with_route_size(self):
        full = quote_origin_route(**self.args)
        half = quote_origin_route(**{**self.args, 'scheduled_bbl':46500000, 'reference_daily_bbl':4650000., 'prompt_ships':25})
        self.assertAlmostEqual(full['real_tce_2025_usd_per_day'], half['real_tce_2025_usd_per_day'])
        self.assertAlmostEqual(full['liquidity_smoothing_vlcc'],2*half['liquidity_smoothing_vlcc'])

    def test_shared_and_local_pressure_are_not_added_twice(self):
        row = quote_origin_route(**{**self.args,'origin_pressure_days':4.,'destination_pressure_days':4.})
        self.assertEqual(row['combined_pricing_pressure_days'],4.)

    def test_nominal_price_only_changes_with_cpi(self):
        a=quote_origin_route(**self.args); b=quote_origin_route(**{**self.args,'cpi':250.})
        self.assertEqual(a['real_tce_2025_usd_per_day'],b['real_tce_2025_usd_per_day'])
        self.assertAlmostEqual(b['nominal_tce_usd_per_day'],2.5*a['nominal_tce_usd_per_day'],delta=.02)

    def test_zero_supply_and_no_new_demand_are_not_transactions(self):
        a=quote_origin_route(**{**self.args,'prompt_ships':0})
        self.assertEqual(a['market_status'],'no_supply');self.assertFalse(a['is_transaction_price'])
        b=quote_origin_route(**{**self.args,'scheduled_bbl':0})
        self.assertFalse(b['price_observation_available']);self.assertEqual(b['real_tce_2025_usd_per_day'],35000)

    def test_invalid_quote_inputs_rejected(self):
        for field,value in [('scheduled_bbl',True),('parcel_bbl',0),('cpi',float('nan')),
                            ('prompt_ships',2.5),('origin_pressure_days',6),('reference_daily_bbl',0)]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                quote_origin_route(**{**self.args,field:value})


class NetworkPhysicalTests(unittest.TestCase):
    def setUp(self):
        self.spec=make_network_spec()

    def test_unique_ids_and_barrels_conserved_through_switches(self):
        state=initial_network(self.spec,265)
        for inp in constant_inputs(100,whole=False):
            state,row=step_network(state,self.spec,**inp)
            self.assertEqual(len(state.ships),265)
            for key in ('fleet_conservation_residual','cargo_conservation_residual_bbl','plan_conservation_residual_bbl'):
                self.assertEqual(row[key],0)

    def test_ship_and_initial_cargo_count_are_permanently_registered(self):
        state=initial_network(self.spec,265)
        with self.assertRaises(ValueError):validate_state(replace(state,ships=state.ships[:-1]),self.spec)
        dup=state.ships[:-1]+(state.ships[0],)
        with self.assertRaises(ValueError):validate_state(replace(state,ships=dup),self.spec)

    def test_every_loaded_ship_delivers_once_and_is_locked(self):
        run=run_network(self.spec,constant_inputs(80),fleet_size=265,include_events=True)
        scheduled={};delivered=set();ballast={}
        for r in run['turns']:
            t=r['internal_movement_turn']
            for d in r['deliveries']:
                key=(d['ship_id'],t)
                self.assertNotIn(key,delivered);delivered.add(key)
                if key in scheduled:self.assertEqual(d['cargo_bbl'],scheduled[key])
            used=[d['ship_id'] for d in r['departures']]+[d['ship_id'] for d in r['ballast_orders']]
            self.assertEqual(len(used),len(set(used)))
            for d in r['departures']:
                key=(d['ship_id'],d['ready_turn'])
                self.assertNotIn(key,scheduled);scheduled[key]=d['cargo_bbl']
                self.assertEqual(d['ready_turn']-t,3 if d['origin']=='gulf' else 4)
            for d in r['ballast_orders']:
                self.assertGreater(d['ready_turn'],t);ballast[d['ship_id'],d['ready_turn']]=d['to']
        cutoff=run['turns'][-1]['internal_movement_turn']
        self.assertTrue(all(k in delivered for k in scheduled if k[1]<=cutoff))

    def test_destination_empty_ships_are_not_both_origins_prompt(self):
        state=initial_network(self.spec,20,initialization='cold')
        state=replace(state,ships=tuple(replace(s,location=self.spec.destination) for s in state.ships))
        state,row=step_network(state,self.spec,**constant_inputs(1)[0],include_events=True)
        self.assertTrue(all(x['prompt_supply_vlcc']==0 for x in row['routes'].values()))
        self.assertTrue(all(x['executed_lots']==0 for x in row['routes'].values()))
        self.assertEqual(len(row['ballast_orders']),20)
        self.assertEqual(len({m['ship_id'] for m in row['ballast_orders']}),20)

    def test_distinct_route_parcels_are_not_vlcc_count_times_one_cargo(self):
        run=run_network(self.spec,constant_inputs(45),include_events=True)
        for r in run['turns']:
            for o,v in r['routes'].items():
                self.assertEqual(v['loaded_bbl'],v['executed_lots']*(1971000 if o=='gulf' else 1898000))

    def test_destination_is_one_aggregate_delivery_ledger(self):
        run=run_network(self.spec,constant_inputs(60))
        for r in run['turns']:
            self.assertEqual(r['shared_destination_deviation_bbl'],sum(x['destination_contribution_bbl'] for x in r['routes'].values()))
            self.assertTrue(all(x['shared_destination_pressure_days']==r['destination_pressure_open'] for x in r['routes'].values()))

    def test_no_fleet_means_no_deliveries_even_with_high_quote(self):
        run=run_network(self.spec,constant_inputs(50),fleet_size=0)
        for r in run['turns']:
            for x in r['routes'].values():
                self.assertEqual(x['loaded_bbl'],0);self.assertEqual(x['delivered_bbl'],0)
                self.assertIsNone(x['executed_benchmark_real_tce'])

    def test_demand_stop_drains_existing_whole_lots_without_new_quotes(self):
        inputs=constant_inputs(80,whole=False)+[{'scheduled_by_origin_bbl':{'gulf':0,'west_africa':0}} for _ in range(120)]
        run=run_network(self.spec,inputs,fleet_size=240)
        last=run['turns'][-1]
        for x in last['routes'].values():
            self.assertLess(x['origin_unshipped_bbl'],x['parcel_bbl'])
            self.assertEqual(x['actual_in_transit_bbl'],0)
            self.assertFalse(x['price_observation_available'])

    def test_state_is_immutable_and_can_resume_exactly(self):
        s=initial_network(self.spec);before=sha256_json(asdict(s))
        a,r=step_network(s,self.spec,**constant_inputs(1)[0])
        b,q=step_network(s,self.spec,**constant_inputs(1)[0])
        self.assertEqual(a,b);self.assertEqual(r,q);self.assertEqual(before,sha256_json(asdict(s)))
        left=s
        for item in constant_inputs(10):left,_=step_network(left,self.spec,**item)
        for item in constant_inputs(10):left,_=step_network(left,self.spec,**item)
        right=s
        for item in constant_inputs(20):right,_=step_network(right,self.spec,**item)
        self.assertEqual(left,right)

    def test_missing_or_invalid_origin_schedule_fails(self):
        s=initial_network(self.spec)
        for schedule in ({'gulf':100},{'gulf':-1,'west_africa':0},{'gulf':False,'west_africa':0}):
            with self.assertRaises(ValueError):step_network(s,self.spec,scheduled_by_origin_bbl=schedule)

    def test_tampered_pipeline_cannot_delete_owed_oil(self):
        s=initial_network(self.spec)
        l=replace(s.ledgers[0],loaded_bbl=100000000)
        with self.assertRaises(ValueError):validate_state(replace(s,ledgers=(l,*s.ledgers[1:])),self.spec)
        m=s.ships[0].movement
        bad=replace(s.ships[0],movement=replace(m,ready_turn=m.ready_turn+1))
        with self.assertRaises(ValueError):validate_state(replace(s,ships=(bad,*s.ships[1:])),self.spec)

    def test_single_origin_requires_no_special_fleet_or_price_branch(self):
        spec=make_network_spec(origins=['gulf'])
        inputs=[{'scheduled_by_origin_bbl':{'gulf':42*1971000}} for _ in range(80)]
        run=run_network(spec,inputs,fleet_size=213)
        self.assertTrue(run['summary']['conservation_exact'])
        self.assertEqual(run['summary']['switched_origin_orders'],0)

    def test_four_named_origins_use_the_same_engine(self):
        spec=make_network_spec(origins=['gulf','west_africa','brazil_guyana','us_gulf'])
        inp={'scheduled_by_origin_bbl':{l.origin:math.floor(l.reference_daily_bbl*10+.5) for l in spec.lanes}}
        run=run_network(spec,[inp]*60,fleet_size=345)
        self.assertEqual(len(run['summary']['routes']),4)
        self.assertTrue(run['summary']['conservation_exact'])

    def test_unresolved_geography_and_invalid_config_rejected(self):
        with self.assertRaises(ValueError):make_network_spec(origins=['other_export_regions'])
        with self.assertRaises(ValueError):make_network_spec(origins=['gulf','gulf'])
        cfg=load_network_config();cfg['routing']['forecast_persistence']=1.
        with self.assertRaises(ValueError):make_network_spec(config=cfg)
        cat=load_catalog();cat['clock']['operating_turn_days']=11
        with self.assertRaises(ValueError):make_network_spec(catalog=cat)


class CouplingAndCausalityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec=make_network_spec()
        cls.inputs,cls.source=build_seeded_inputs(cls.spec,seed=42,years=5)

    def test_seeded_vlcc_flow_uses_inherited_shares_once(self):
        for inp in self.inputs:
            for lane in self.spec.lanes:
                rate=inp['source_route_cargo_mbd'][lane.origin]
                expected=apportion_barrels(whole_barrels(rate,10),dict(zip(('vlcc','suezmax','aframax'),lane.share_bps)))['vlcc']
                self.assertEqual(inp['scheduled_by_origin_bbl'][lane.origin],expected)
        self.assertTrue(self.source['source_unchanged'])

    def test_future_inputs_do_not_change_the_past(self):
        a=run_network(self.spec,self.inputs[:72],fleet_size=265)
        b=run_network(self.spec,self.inputs,fleet_size=265)
        self.assertEqual(a['turns'],b['turns'][:72])

    def test_cpi_information_is_last_completed_year(self):
        for r in self.inputs:
            self.assertEqual(r['cpi_information_year'],max(2025,r['year']-1))

    def test_input_mapping_order_does_not_choose_winners(self):
        reverse=deepcopy(self.inputs)
        for r in reverse:r['scheduled_by_origin_bbl']=dict(reversed(list(r['scheduled_by_origin_bbl'].items())))
        a=run_network(self.spec,self.inputs);b=run_network(self.spec,reverse)
        self.assertEqual(a['turns'],b['turns'])

    def test_whole_lot_constant_world_does_not_invent_a_cycle(self):
        run=run_network(self.spec,constant_inputs(120),fleet_size=250)
        for o in self.spec.origins:
            prices=[r['routes'][o]['real_tce_2025_usd_per_day'] for r in run['turns'][60:]]
            self.assertLess(max(prices)-min(prices),.05)

    def test_competitor_shock_transfers_actual_ships_not_just_price(self):
        base=constant_inputs(160,whole=False);shock=deepcopy(base)
        for i in range(80,92):shock[i]['scheduled_by_origin_bbl']['west_africa']*=2
        a=run_network(self.spec,base,fleet_size=265,include_events=True)
        b=run_network(self.spec,shock,fleet_size=265,include_events=True)
        self.assertEqual(a['turns'][:80],b['turns'][:80])
        self.assertTrue(all(x['routes']['gulf']['scheduled_bbl']==y['routes']['gulf']['scheduled_bbl'] for x,y in zip(a['turns'],b['turns'])))
        window=range(80,125)
        transfers=[b['turns'][i]['committed_after_routing']['west_africa']-a['turns'][i]['committed_after_routing']['west_africa'] for i in window]
        self.assertGreater(max(transfers),5)
        for i in window:
            self.assertEqual(sum(b['turns'][i]['committed_after_routing'].values()),265)
        rise=max(b['turns'][i]['routes']['gulf']['real_tce_2025_usd_per_day']-a['turns'][i]['routes']['gulf']['real_tce_2025_usd_per_day'] for i in window)
        self.assertGreater(rise,1000)

    def test_segmented_control_cannot_transfer_ship_ownership_pools(self):
        cfg=load_network_config();cfg['routing']['mode']='home_return'
        spec=make_network_spec(config=cfg)
        run=run_network(spec,constant_inputs(100,whole=False),fleet_size=265,include_events=True)
        self.assertEqual(run['summary']['switched_origin_orders'],0)
        counts=[r['committed_after_routing'] for r in run['turns']]
        self.assertTrue(all(x==counts[0] for x in counts))

    def test_recent_pressure_stays_bounded_while_true_debt_accumulates(self):
        run=run_network(self.spec,constant_inputs(120),fleet_size=80)
        end=run['turns'][-1]
        self.assertGreater(sum(x['origin_unshipped_bbl'] for x in end['routes'].values()),1000000000)
        for r in run['turns']:
            self.assertLessEqual(abs(r['destination_pressure_open']),5)
            self.assertTrue(all(abs(x['origin_pressure_close'])<=5 for x in r['routes'].values()))


if __name__=='__main__':unittest.main()
