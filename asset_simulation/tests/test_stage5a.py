from __future__ import annotations

import calendar
from copy import deepcopy
from dataclasses import replace
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world
from asset_simulation.model.registry import sha256_json
from asset_simulation.model.single_route_fleet import FleetState, advance_fleet, dispatch_fleet, initial_fleet
from asset_simulation.model.single_route_market import (
    initial_market, monthly_turn_inputs, seeded_route_inputs, simulate_fixed_route,
    step_route_market,
)
from asset_simulation.model.bounded_route_pricing import load_bounded_pricing_config, price_bounded_route_turn


def flat_turns(n=150, barrels=98550000):
    return tuple({"scheduled_cargo_bbl": barrels, "turn_days": 10, "cpi": 100.0} for _ in range(n))


class FleetTests(unittest.TestCase):
    def test_exact_zero_two_one_two_timing(self):
        state = initial_fleet(1, reference_departures=0, initialization="all_prompt")
        names = ["laden_1", "laden_2", "ea_discharge", "ballast_1", "ballast_2", "gulf_prompt"]
        for t, name in enumerate(names):
            opened, events = advance_fleet(state)
            self.assertEqual((1,) if t == 3 else (), events["delivered_ship_ids"])
            self.assertEqual((1,) if t == 5 else (), events["returned_ship_ids"])
            state, _ = dispatch_fleet(opened, 1 if t == 0 else 0)
            self.assertEqual((1,), getattr(state, name))
            self.assertEqual(1, sum(state.counts().values()))

    def test_no_double_advance_or_double_dispatch(self):
        state = initial_fleet(2, reference_departures=0)
        with self.assertRaises(ValueError):
            dispatch_fleet(state, 1)
        opened, _ = advance_fleet(state)
        with self.assertRaises(ValueError):
            advance_fleet(opened)
        with self.assertRaises(ValueError):
            dispatch_fleet(opened, 3)
        closed, _ = dispatch_fleet(opened, 1)
        with self.assertRaises(ValueError):
            dispatch_fleet(closed, 1)

    def test_idle_ships_remain_prompt_supply(self):
        state = initial_fleet(10, reference_departures=0)
        opened, _ = advance_fleet(state)
        closed, _ = dispatch_fleet(opened, 3)
        next_open, _ = advance_fleet(closed)
        self.assertEqual(7, len(next_open.gulf_prompt))
        self.assertEqual(3, len(next_open.laden_2))

    def test_invalid_and_duplicated_ids_fail(self):
        with self.assertRaises(ValueError):
            FleetState(2, gulf_prompt=(1, 1)).validate()
        with self.assertRaises(ValueError):
            FleetState(2, gulf_prompt=(1, 3)).validate()
        for bad in (-1, True, 2.5):
            with self.assertRaises(ValueError):
                initial_fleet(bad, reference_departures=2)

    def test_phased_initialization_uses_fixed_reference(self):
        fleet = initial_fleet(245, reference_departures=47.839)
        groups = [len(getattr(fleet, name)) for name in ("laden_1", "laden_2", "ea_discharge", "ballast_1", "ballast_2")]
        self.assertLessEqual(max(groups) - min(groups), 1)
        self.assertEqual(239, sum(groups))
        self.assertEqual(6, len(fleet.gulf_prompt))


class PhysicalMarketTests(unittest.TestCase):
    def test_exact_barrel_and_plan_conservation(self):
        result = simulate_fixed_route(flat_turns(), fleet_size=235)
        for row in result["turns"]:
            for field in ("fleet_conservation_residual", "barrel_conservation_residual", "plan_conservation_residual"):
                self.assertEqual(0, row[field])
            self.assertGreaterEqual(row["origin_unshipped_bbl"], 0)
            self.assertGreaterEqual(row["actual_in_transit_bbl"], 0)
            self.assertEqual(row["loaded_cargo_bbl"], row["dispatched_vlcc"] * 1971000)

    def test_no_loads_or_deliveries_without_ships(self):
        result = simulate_fixed_route(flat_turns(12), fleet_size=0, warmup_turns=0)
        self.assertEqual(12 * 98550000, result["final_state"]["origin_bbl"])
        for row in result["turns"]:
            self.assertEqual(0, row["loaded_cargo_bbl"])
            self.assertEqual(0, row["delivered_cargo_bbl"])
            self.assertIsNone(row["executed_benchmark_real_tce"])
            self.assertFalse(row["is_transaction_price"])

    def test_whole_cargo_remainder_is_not_lost(self):
        result = simulate_fixed_route(flat_turns(40, 1000000), fleet_size=10,
                                      initialization="all_prompt", warmup_turns=0)
        total = sum(r["loaded_cargo_bbl"] for r in result["turns"])
        self.assertEqual(40000000, total + result["final_state"]["origin_bbl"])
        self.assertLess(result["final_state"]["origin_bbl"], 1971000)

    def test_demand_stop_drains_old_cargo_and_releases_fleet(self):
        inputs = flat_turns(20, 98550000) + flat_turns(20, 0)
        result = simulate_fixed_route(inputs, fleet_size=252, warmup_turns=0)
        last = result["turns"][-1]
        self.assertEqual(252, last["idle_prompt_after_dispatch"])
        self.assertEqual(0, last["actual_in_transit_bbl"])
        self.assertFalse(last["price_observation_available"])
        self.assertIsNone(last["executed_benchmark_real_tce"])

    def test_each_identified_voyage_delivers_once_and_returns_at_five(self):
        rows = simulate_fixed_route(flat_turns(), fleet_size=252, include_events=True)["turns"]
        departures = {}
        delivered = set()
        for t, row in enumerate(rows):
            for ship in row["delivered_ship_ids"]:
                if ship in departures:
                    self.assertEqual(3, t - departures[ship])
                    key = (ship, departures[ship])
                    self.assertNotIn(key, delivered)
                    delivered.add(key)
            for ship in row["returned_ship_ids"]:
                if ship in departures:
                    self.assertEqual(5, t - departures[ship])
                    self.assertIn((ship, departures[ship]), delivered)
            for ship in row["departed_ship_ids"]:
                if ship in departures:
                    self.assertGreaterEqual(t - departures[ship], 5)
                departures[ship] = t

    def test_flat_equal_window_is_not_an_autonomous_price_cycle(self):
        for n in (252, 260, 280):
            result = simulate_fixed_route(flat_turns(200), fleet_size=n)
            self.assertLess(result["summary"]["real_tce_max"] - result["summary"]["real_tce_min"], 0.02)
        tight = simulate_fixed_route(flat_turns(200), fleet_size=250)["summary"]
        loose = simulate_fixed_route(flat_turns(200), fleet_size=280)["summary"]
        self.assertGreater(tight["real_tce_median"], loose["real_tce_median"])

    def test_structural_shortage_has_analytical_capacity_limit(self):
        rows = simulate_fixed_route(flat_turns(200), fleet_size=200)["turns"]
        tail = rows[50:150]
        self.assertEqual(200 * 20, sum(r["dispatched_vlcc"] for r in tail))
        expected = 100 * 98550000 - 200 * 20 * 1971000
        self.assertEqual(expected, rows[149]["origin_unshipped_bbl"] - rows[49]["origin_unshipped_bbl"])

    def test_step_calls_pure_bounded_price_component(self):
        state = initial_market(252)
        cfg = load_bounded_pricing_config()
        before = sha256_json(cfg)
        opened, events = advance_fleet(state.fleet)
        destination = state.destination_deviation_bbl + len(events["delivered_ship_ids"]) * state.cargo_bbl - state.reference_arrivals_bbl[0]
        quote = price_bounded_route_turn(structural_cargo_mbd=9.3, turn_days=10,
            prompt_supply_vlcc=len(opened.gulf_prompt),
            pricing_pressure_days=0.0,
            origin_inventory_deviation_mmbbl=state.origin_bbl/1e6,
            destination_inventory_deviation_mmbbl=destination/1e6,
            previous_real_tce_2025_usd_per_day=state.previous_real_tce)
        _, row = step_route_market(state, scheduled_cargo_bbl=93000000, turn_days=10)
        self.assertEqual(quote["real_tce_2025_usd_per_day"], row["real_tce_2025_usd_per_day"])
        self.assertEqual(before, sha256_json(cfg))

    def test_price_level_is_not_secretly_a_dispatch_strategy(self):
        cfg = deepcopy(load_bounded_pricing_config())
        cfg["pricing"]["baseline_real_tce_2025_usd_per_day"] = 40000.0
        a = simulate_fixed_route(flat_turns(), fleet_size=245)
        b = simulate_fixed_route(flat_turns(), fleet_size=245, pricing_config=cfg)
        self.assertEqual([r["dispatched_vlcc"] for r in a["turns"]], [r["dispatched_vlcc"] for r in b["turns"]])
        self.assertNotEqual(a["summary"]["mean_real_tce"], b["summary"]["mean_real_tce"])

    def test_continuation_is_identical_to_one_pass(self):
        inputs = flat_turns(100)
        a = simulate_fixed_route(inputs, fleet_size=252, warmup_turns=0)
        state = initial_market(252)
        rows = []
        for item in inputs[:37]:
            state, row = step_route_market(state, **{k:v for k,v in item.items() if k != "cpi"}, cpi=item["cpi"])
            rows.append(row)
        resumed_state = deepcopy(state)
        for item in inputs[37:]:
            resumed_state, row = step_route_market(resumed_state, **item)
            rows.append(row)
        self.assertEqual(a["turns"], tuple(rows))

    def test_invalid_inputs_and_tampered_ledger_fail(self):
        state = initial_market(245)
        for bad in (True, -1, 2.5):
            with self.assertRaises(ValueError):
                step_route_market(state, scheduled_cargo_bbl=bad, turn_days=10)
        with self.assertRaises(ValueError):
            step_route_market(replace(state, actual_in_transit_bbl=0), scheduled_cargo_bbl=1000, turn_days=10)
        with self.assertRaises(ValueError):
            step_route_market(state, scheduled_cargo_bbl=93000000, turn_days=10, cpi=float('nan'))


class SeedAndCalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.macro = run_global_macro(42, 5)
        cls.shipping = run_oil_shipping_world(cls.macro)

    def test_monthly_daily_rate_is_projected_to_fixed_operating_clock(self):
        months = [{'year':2028,'month':m,'days':calendar.monthrange(2028,m)[1],'cargo_mbd':9.33333333} for m in range(1,13)]
        inputs = monthly_turn_inputs(months, cpi_by_information_year={2025:100.0,2027:110.0}, initial_year=2025)
        self.assertEqual(360, sum(t['turn_days'] for t in inputs))
        self.assertEqual({10}, {t['turn_days'] for t in inputs})
        for i, month in enumerate(months):
            self.assertEqual(round(month['cargo_mbd'] * 30 * 1e6), sum(t['scheduled_cargo_bbl'] for t in inputs[3*i:3*i+3]))

    def test_same_seed_demand_and_no_upstream_mutation(self):
        before = sha256_json([self.macro.rows, self.shipping.turns])
        inputs = seeded_route_inputs(self.macro,self.shipping)
        first = self.shipping.turns[0]['routes'][0]
        self.assertEqual('gulf_east_asia',first['route_id'])
        self.assertEqual(first['cargo_mbd'], inputs[0]['source_cargo_mbd'])
        simulate_fixed_route(inputs, fleet_size=260)
        self.assertEqual(before, sha256_json([self.macro.rows, self.shipping.turns]))

    def test_cpi_reads_only_last_completed_year(self):
        inputs = seeded_route_inputs(self.macro,self.shipping)
        for row in inputs:
            self.assertEqual(max(2025,row['year']-1),row['cpi_information_year'])
        for row in inputs[36:72]:
            self.assertEqual(100.0,row['cpi'])

    def test_future_months_do_not_change_past(self):
        inputs = seeded_route_inputs(self.macro,self.shipping)
        short = simulate_fixed_route(inputs[:72],fleet_size=260)
        long = simulate_fixed_route(inputs,fleet_size=260)
        self.assertEqual(short['turns'],long['turns'][:72])
        self.assertNotEqual(short['identity']['input_hash'],long['identity']['input_hash'])

    def test_constant_daily_flow_has_only_small_integer_fixture_variation(self):
        months = [{'year':y,'month':m,'days':calendar.monthrange(y,m)[1],'cargo_mbd':9.3} for y in range(2025,2028) for m in range(1,13)]
        inputs=monthly_turn_inputs(months,cpi_by_information_year={y:100.0 for y in range(2025,2028)},initial_year=2025)
        result=simulate_fixed_route(inputs,fleet_size=245)
        self.assertLess(result['summary']['end_origin_backlog_days_at_mean_flow'],1.0)
        self.assertLess(result['summary']['real_tce_cv'],0.02)
        self.assertEqual('fixed_fleet_counterfactual_not_a_market_equilibrium_or_supercycle',result['summary']['interpretation'])

    def test_mixed_macro_world_rejected(self):
        other = run_global_macro(7,5)
        with self.assertRaises(ValueError):
            seeded_route_inputs(other,self.shipping)


if __name__ == '__main__':
    unittest.main()
