from __future__ import annotations

import calendar
from copy import deepcopy
import unittest

from asset_simulation.model.bounded_route_pricing import (
    bounded_pressure, load_bounded_pricing_config, price_bounded_route_turn,
    soft_price, inverse_soft_price, validate_bounded_config,
)
from asset_simulation.model.single_route_market import (
    initial_market, monthly_turn_inputs, simulate_fixed_route, step_route_market,
)


class BoundedPricingTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_bounded_pricing_config()

    def quote(self, **kwargs):
        values = dict(structural_cargo_mbd=9.3, turn_days=10,
                      prompt_supply_vlcc=50.0, config=self.cfg)
        values.update(kwargs)
        return price_bounded_route_turn(**values)

    def test_soft_curve_preserves_anchor_and_local_elasticity(self):
        self.assertAlmostEqual(35000.0, soft_price(0.0, self.cfg), places=7)
        h = 1e-5
        derivative = (soft_price(h, self.cfg) - soft_price(-h, self.cfg)) / (2*h)
        self.assertAlmostEqual(35000.0, derivative, delta=0.01)
        for score in (-3,-1,0,1,3):
            self.assertAlmostEqual(score, inverse_soft_price(soft_price(score,self.cfg),self.cfg), places=8)

    def test_soft_curve_is_monotonic_and_bounded(self):
        values = [soft_price(s/20,self.cfg) for s in range(-200,201)]
        self.assertTrue(all(a < b for a,b in zip(values,values[1:])))
        self.assertTrue(all(1000.0 <= p <= 122500.0 for p in values))
        self.assertAlmostEqual(1000.0,soft_price(-1000,self.cfg))
        self.assertAlmostEqual(122500.0,soft_price(1000,self.cfg))

    def test_less_supply_and_more_recent_pressure_raise_quote(self):
        self.assertLess(self.quote(prompt_supply_vlcc=56)['real_tce_2025_usd_per_day'],
                        self.quote(prompt_supply_vlcc=44)['real_tce_2025_usd_per_day'])
        values = [self.quote(pricing_pressure_days=x)['real_tce_2025_usd_per_day'] for x in (-5,-2,0,2,5)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(5.0, self.quote(pricing_pressure_days=5)['priced_inventory_pressure_days'])

    def test_historical_backlog_does_not_recreate_a_price_premium(self):
        a = self.quote(origin_inventory_deviation_mmbbl=93, destination_inventory_deviation_mmbbl=-93,
                       pricing_pressure_days=1.0)
        b = self.quote(origin_inventory_deviation_mmbbl=93000, destination_inventory_deviation_mmbbl=-93000,
                       pricing_pressure_days=1.0)
        self.assertEqual(a['real_tce_2025_usd_per_day'], b['real_tce_2025_usd_per_day'])
        self.assertNotEqual(a['inventory_gap_days'], b['inventory_gap_days'])
        self.assertFalse(b['raw_inventory_used_directly_for_quote'])

    def test_pressure_has_exact_finite_half_life_and_no_windup(self):
        pressure = bounded_pressure(0, 10000, config=self.cfg, decay=True)
        self.assertEqual(5.0, pressure)
        for _ in range(3):
            pressure = bounded_pressure(pressure, 0, config=self.cfg, decay=True)
        self.assertAlmostEqual(2.5, pressure)
        for _ in range(21):
            pressure = bounded_pressure(pressure, 0, config=self.cfg, decay=True)
        self.assertAlmostEqual(5 / 256, pressure)
        for _ in range(100):
            pressure = bounded_pressure(pressure, 10, config=self.cfg, decay=True)
        self.assertEqual(5, pressure)

    def test_raw_debt_can_stay_large_while_pure_price_memory_recedes(self):
        pressure=5.0
        previous=35000.0
        for _ in range(30):
            row=self.quote(pricing_pressure_days=pressure,
                           origin_inventory_deviation_mmbbl=930,
                           destination_inventory_deviation_mmbbl=-930,
                           previous_real_tce_2025_usd_per_day=previous)
            previous=row['real_tce_2025_usd_per_day']
            pressure=bounded_pressure(pressure,0,config=self.cfg,decay=True)
        self.assertLess(abs(previous-35000),200)
        self.assertAlmostEqual(100.0,row['inventory_gap_days'])

    def test_nominal_cash_scale_does_not_change_real_quote(self):
        a=self.quote()
        b=self.quote(cpi_price_level_index_2025_100=250)
        self.assertEqual(a['real_tce_2025_usd_per_day'],b['real_tce_2025_usd_per_day'])
        self.assertAlmostEqual(2.5*a['nominal_tce_usd_per_day'],b['nominal_tce_usd_per_day'],delta=0.02)

    def test_no_demand_no_supply_are_not_fake_transactions(self):
        no_demand=self.quote(structural_cargo_mbd=0,previous_real_tce_2025_usd_per_day=42000)
        self.assertEqual('no_demand',no_demand['market_status'])
        self.assertEqual(42000,no_demand['real_tce_2025_usd_per_day'])
        self.assertFalse(no_demand['price_observation_available'])
        self.assertFalse(no_demand['is_transaction_price'])
        empty=self.quote(prompt_supply_vlcc=0)
        self.assertEqual('no_supply',empty['market_status'])
        self.assertFalse(empty['is_transaction_price'])

    def test_invalid_clock_pressure_and_config_fail(self):
        for days in (9,11,10.0,True):
            with self.assertRaises(ValueError): self.quote(turn_days=days)
        for bad in (-6,6,float('inf'),float('nan'),True):
            with self.assertRaises(ValueError): self.quote(pricing_pressure_days=bad)
        for field in ('limit_days','half_life_turns','soft_scale_days'):
            cfg=deepcopy(self.cfg); cfg['pressure'][field]=0
            with self.assertRaises(ValueError): validate_bounded_config(cfg)


class OperatingClockAndLedgerTests(unittest.TestCase):
    def months(self,year):
        return tuple(dict(year=year,month=m,days=calendar.monthrange(year,m)[1],cargo_mbd=9.3) for m in range(1,13))

    def test_leap_year_and_february_do_not_change_operating_work(self):
        for year in (2027,2028):
            inputs=monthly_turn_inputs(self.months(year),cpi_by_information_year={year:100},initial_year=year)
            self.assertEqual({10},{r['turn_days'] for r in inputs})
            self.assertEqual({93000000},{r['scheduled_cargo_bbl'] for r in inputs})
            self.assertEqual(360,sum(r['turn_days'] for r in inputs))
            for r in inputs:
                self.assertEqual(r['operating_month_cargo_bbl']-r['source_calendar_month_cargo_bbl'],r['clock_projection_difference_bbl'])
        a=monthly_turn_inputs(self.months(2027),cpi_by_information_year={2027:100},initial_year=2027)
        b=monthly_turn_inputs(self.months(2028),cpi_by_information_year={2028:100},initial_year=2028)
        self.assertEqual([r['scheduled_cargo_bbl'] for r in a],[r['scheduled_cargo_bbl'] for r in b])

    def test_exact_50_operating_days_per_cycle(self):
        state=initial_market(1,initialization='all_prompt')
        rows=[]
        for t in range(6):
            state,row=step_route_market(state,scheduled_cargo_bbl=1971000 if t==0 else 0,
                                       turn_days=10,include_events=True)
            rows.append(row)
        self.assertEqual((1,),rows[3]['delivered_ship_ids'])
        self.assertEqual((1,),rows[5]['returned_ship_ids'])
        self.assertEqual(30,rows[3]['day_start_offset'])
        self.assertEqual(50,rows[5]['day_start_offset'])

    def test_pressure_limit_does_not_discard_or_cancel_owed_oil(self):
        inputs=tuple(dict(scheduled_cargo_bbl=93000000,turn_days=10) for _ in range(100))
        result=simulate_fixed_route(inputs,fleet_size=0,warmup_turns=0)
        final=result['final_state']
        self.assertEqual(9300000000,final['origin_bbl'])
        self.assertEqual(0,final['cumulative_loaded_bbl'])
        self.assertLessEqual(abs(final['pricing_pressure_days']),5)
        self.assertEqual(0,result['summary']['max_barrel_residual'])
        self.assertFalse(result['identity']['demand_destruction'])

    def test_pressure_parameters_cannot_change_shipments(self):
        inputs=tuple(dict(scheduled_cargo_bbl=100000000,turn_days=10) for _ in range(200))
        cfg=load_bounded_pricing_config(); cfg['pressure']['half_life_turns']=1.0
        fast=simulate_fixed_route(inputs,fleet_size=245,pricing_config=cfg)
        cfg['pressure']['half_life_turns']=12.0
        slow=simulate_fixed_route(inputs,fleet_size=245,pricing_config=cfg)
        self.assertEqual([r['loaded_cargo_bbl'] for r in fast['turns']], [r['loaded_cargo_bbl'] for r in slow['turns']])
        self.assertEqual(fast['final_state']['origin_bbl'],slow['final_state']['origin_bbl'])
        self.assertNotEqual(fast['summary']['mean_real_tce'],slow['summary']['mean_real_tce'])

    def test_zero_new_flow_still_drains_existing_owed_oil(self):
        inputs=tuple(dict(scheduled_cargo_bbl=93000000,turn_days=10) for _ in range(20))
        inputs+=tuple(dict(scheduled_cargo_bbl=0,turn_days=10) for _ in range(50))
        result=simulate_fixed_route(inputs,fleet_size=150,warmup_turns=0)
        self.assertLess(result['final_state']['origin_bbl'],1971000)
        self.assertEqual(0,result['final_state']['actual_in_transit_bbl'])
        self.assertEqual(0,result['summary']['max_barrel_residual'])


if __name__ == '__main__':
    unittest.main()
