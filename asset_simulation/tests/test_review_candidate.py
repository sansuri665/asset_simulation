from __future__ import annotations

import calendar
from copy import deepcopy
from dataclasses import replace
import json
import math
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world
from asset_simulation.model.oil_price_projection import run_oil_price_projection
from asset_simulation.model.oil_shipping_routes import _balance_matrix
from asset_simulation.model.single_route_pricing import (
    build_lagged_prompt_supply_path, load_single_route_pricing_config,
    price_single_route_turn, shipping_turn_days, simulate_gulf_east_asia_price_path,
)
from asset_simulation.model.decision_view import build_decision_snapshot
from asset_simulation.server import AssetSimulationHandler, clear_cache


def constant_months(start=2025, end=2030, rate=9.3):
    return tuple({'year': y, 'month': m, 'days': calendar.monthrange(y, m)[1], 'cargo_mbd': rate}
                 for y in range(start, end + 1) for m in range(1, 13))


class CalendarPricingRepairTests(unittest.TestCase):
    def test_lagged_daily_rate_is_scaled_to_current_days(self):
        cfg = load_single_route_pricing_config()
        months = list(constant_months(end=2025))
        months[1]['cargo_mbd'] = 10.0
        turns = [(m['cargo_mbd'], d) for m in months for d in shipping_turn_days(m['days'])]
        supply = build_lagged_prompt_supply_path(months, lag_turns=2)
        buffer = cfg['reference_prompt_supply_vlcc'] - cfg['reference_route_cargo_mbd'] * cfg['reference_turn_days'] / cfg['vlcc_cargo_mmbbl']
        for i, (_, days) in enumerate(turns):
            daily = turns[i - 2][0] if i >= 2 else cfg['reference_route_cargo_mbd']
            expected = math.floor(daily * days / cfg['vlcc_cargo_mmbbl'] + buffer * days / cfg['reference_turn_days'] + 0.5)
            self.assertEqual(expected, supply[i])

    def test_constant_daily_demand_has_no_large_calendar_price_cycle(self):
        months = constant_months(end=2045)
        path = simulate_gulf_east_asia_price_path(months, seed=42,
            cpi_by_year={y: 100 for y in range(2025, 2046)},
            prompt_supply_by_turn=build_lagged_prompt_supply_path(months))
        settled = path['turns'][36:]
        prices = [r['real_tce_2025_usd_per_day'] for r in settled]
        self.assertLess(max(prices) - min(prices), 1500)
        self.assertLess(max(abs(r['inventory_gap_days']) for r in settled), 0.20)
        self.assertEqual(0, path['summary']['maximum_price_guard_hit_turns'])

    def test_prefix_supply_path_is_unchanged_by_later_months(self):
        months = constant_months()
        short = build_lagged_prompt_supply_path(months[:13])
        long = build_lagged_prompt_supply_path(months)
        self.assertEqual(short, long[:len(short)])

    def test_days_are_conserved_including_leap_february(self):
        self.assertEqual((9, 9, 10), shipping_turn_days(28))
        self.assertEqual((9, 10, 10), shipping_turn_days(29))
        for n in range(28, 32):
            self.assertEqual(n, sum(shipping_turn_days(n)))
        for invalid in (28.5, True, 27, 32):
            with self.assertRaises(ValueError):
                shipping_turn_days(invalid)

    def test_price_elasticity_and_price_guards_not_recalibrated(self):
        cfg = load_single_route_pricing_config()
        self.assertEqual(3.0, cfg['pricing']['supply_demand_log_sensitivity'])
        self.assertEqual(0.03, cfg['pricing']['inventory_urgency_log_sensitivity_per_day'])
        self.assertEqual(0.25, cfg['pricing']['price_persistence'])
        self.assertEqual(122500, cfg['pricing']['maximum_real_tce_2025_usd_per_day'])
        quote = price_single_route_turn(structural_cargo_mbd=9.3, turn_days=10, prompt_supply_vlcc=50)
        self.assertAlmostEqual(34012, quote['real_tce_2025_usd_per_day'], delta=2)

    def test_nonfinite_and_boolean_public_inputs_rejected(self):
        kwargs = {'structural_cargo_mbd': 9.3, 'turn_days': 10, 'prompt_supply_vlcc': 50}
        for field in ('structural_cargo_mbd', 'prompt_supply_vlcc', 'origin_inventory_deviation_mmbbl',
                      'destination_inventory_deviation_mmbbl', 'previous_real_tce_2025_usd_per_day',
                      'cpi_price_level_index_2025_100'):
            for bad in (math.nan, math.inf, -math.inf, True):
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    price_single_route_turn(**{**kwargs, field: bad})
        with self.assertRaises(ValueError):
            price_single_route_turn(**{**kwargs, 'turn_days': 10.5})

    def test_nonfinite_or_invalid_config_rejected(self):
        for name, bad in (('liquidity_smoothing_vlcc', 0), ('supply_demand_log_sensitivity', -1),
                          ('baseline_real_tce_2025_usd_per_day', math.nan)):
            cfg = deepcopy(load_single_route_pricing_config())
            cfg['pricing'][name] = bad
            with self.assertRaises(ValueError):
                price_single_route_turn(structural_cargo_mbd=9.3, turn_days=10, prompt_supply_vlcc=50, config=cfg)

    def test_zero_demand_carries_indication_but_has_no_execution(self):
        months = constant_months(end=2025, rate=0)
        result = simulate_gulf_east_asia_price_path(months, seed=42, cpi_by_year={2025: 100},
                                                   prompt_supply_by_turn=[50] * 36)
        for row in result['turns']:
            self.assertEqual('no_demand', row['market_status'])
            self.assertFalse(row['price_observation_available'])
            self.assertFalse(row['is_transaction_price'])
            self.assertEqual(0, row['loaded_fixture_vlcc'])
            self.assertIsNone(row['executed_fixture_tce_2025_usd_per_day'])
        self.assertEqual(0, result['summary']['matched_fixture_count'])

    def test_zero_supply_quote_is_not_a_transaction(self):
        months = constant_months(end=2025)
        result = simulate_gulf_east_asia_price_path(months, seed=42, cpi_by_year={2025: 100},
                                                   prompt_supply_by_turn=[0] * 36)
        self.assertTrue(all(r['market_status'] == 'no_supply' for r in result['turns']))
        self.assertTrue(all(r['executed_fixture_tce_2025_usd_per_day'] is None for r in result['turns']))
        self.assertEqual(0, result['summary']['matched_fixture_count'])
        self.assertEqual(0, result['summary']['maximum_abs_inventory_conservation_residual_mmbbl'])

    def test_inventory_is_a_lagged_transport_plan_deviation(self):
        months = constant_months(end=2025)
        supply = build_lagged_prompt_supply_path(months, temporary_supply_delta_by_turn={5: -6})
        path = simulate_gulf_east_asia_price_path(months, seed=42, cpi_by_year={2025: 100}, prompt_supply_by_turn=supply)
        rows = path['turns']
        for t in range(3, len(rows)):
            self.assertAlmostEqual(rows[t]['closing_destination_inventory_deviation_mmbbl'],
                                   -rows[t - 3]['closing_origin_inventory_deviation_mmbbl'], places=7)
        self.assertEqual(path['summary']['total_unfilled_fixture_vlcc'],
                         path['summary']['cumulative_unfilled_fixture_observations_vlcc'])

    def test_input_and_result_fingerprints_change_with_supply(self):
        months = constant_months(end=2025)
        a = simulate_gulf_east_asia_price_path(months, seed=42, cpi_by_year={2025: 100}, prompt_supply_by_turn=[50] * 36)
        b = simulate_gulf_east_asia_price_path(months, seed=42, cpi_by_year={2025: 100}, prompt_supply_by_turn=[51] * 36)
        self.assertEqual(a['identity']['demand_input_hash'], b['identity']['demand_input_hash'])
        self.assertNotEqual(a['identity']['prompt_supply_path_hash'], b['identity']['prompt_supply_path_hash'])
        self.assertNotEqual(a['identity']['result_hash'], b['identity']['result_hash'])
        self.assertEqual(365, a['summary']['calendar_day_count'])
        self.assertEqual(1, a['summary']['covered_calendar_year_count'])

    def test_empty_path_and_invalid_supply_schedule_rejected(self):
        with self.assertRaises(ValueError):
            simulate_gulf_east_asia_price_path([], seed=42, cpi_by_year={}, prompt_supply_by_turn=[])
        for kwargs in ({'lag_turns': 1.5}, {'supply_multiplier': math.nan},
                       {'temporary_supply_delta_by_turn': {999: -6}}):
            with self.assertRaises(ValueError):
                build_lagged_prompt_supply_path(constant_months(end=2025), **kwargs)


class OriginalTradeMarginTests(unittest.TestCase):
    def test_inconsistent_original_totals_are_not_silently_rescaled(self):
        with self.assertRaisesRegex(ValueError, 'original export/import totals'):
            _balance_matrix({'gulf': 10}, {'ea': 12}, {'gulf::ea': 1})

    def test_only_rounding_dust_is_tolerated(self):
        flow = _balance_matrix({'gulf': 10}, {'ea': 10 + 2e-8}, {'gulf::ea': 1})
        self.assertAlmostEqual(10, flow['gulf::ea'], places=7)

    def test_each_original_region_margin_is_met(self):
        rows, columns = {'g': 7., 'b': 3.}, {'e': 6., 'w': 4.}
        flow = _balance_matrix(rows, columns, {'g::e': 2., 'g::w': 1., 'b::e': 1., 'b::w': 4.})
        for origin, target in rows.items():
            self.assertAlmostEqual(target, sum(flow[f'{origin}::{d}'] for d in columns), places=7)
        for dest, target in columns.items():
            self.assertAlmostEqual(target, sum(flow[f'{o}::{dest}'] for o in rows), places=7)

    def test_nan_margin_or_preference_rejected(self):
        with self.assertRaises(ValueError):
            _balance_matrix({'g': math.nan}, {'e': 10}, {'g::e': 1})
        with self.assertRaises(ValueError):
            _balance_matrix({'g': 10}, {'e': 10}, {'g::e': math.nan})


class DecisionBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.macro = run_global_macro(42, 5)
        cls.world = run_oil_shipping_world(cls.macro)
        cls.prices = run_oil_price_projection(cls.macro)

    def snapshot(self, year=2028, month=6):
        return build_decision_snapshot(self.macro, self.world, self.prices, as_of_year=year, as_of_month=month)

    def test_only_completed_annual_and_monthly_data_is_published(self):
        view = self.snapshot()
        self.assertEqual(2027, view['macro']['year'])
        self.assertEqual((2028, 6), (view['shipping']['year'], view['shipping']['month']))
        self.assertEqual({'year': 2027, 'month': 12}, view['oilPriceAsOf'])
        self.assertTrue(all(r['year'] <= 2027 for r in view['oilPrices']))
        text = json.dumps(view)
        for forbidden in ('annual_close_anchor', 'upstream_global_identity_hash', 'end_year', 'nextYearInputs', 'long_run_demand_regime'):
            self.assertNotIn(forbidden, text)
        december = self.snapshot(month=12)
        self.assertEqual(2028, december['macro']['year'])
        self.assertEqual({'year': 2028, 'month': 12}, december['oilPriceAsOf'])

    def test_initial_macro_row_available_at_start(self):
        initial = self.snapshot(year=2025, month=1)
        self.assertEqual(2025, initial['macro']['year'])
        self.assertEqual({'year': 2025, 'month': 1}, initial['oilPriceAsOf'])

    def test_longer_world_has_identical_visible_snapshot_and_hash(self):
        run = run_global_macro(42, 6)
        long = build_decision_snapshot(run, run_oil_shipping_world(run), run_oil_price_projection(run), as_of_year=2028, as_of_month=6)
        self.assertEqual(self.snapshot(), long)

    def test_hidden_anchors_and_future_records_cannot_leak(self):
        monthly = deepcopy(self.prices.monthly)
        for row in monthly:
            row['annual_close_anchor_usd_per_bbl'] = 987654321
            row['future_debug'] = 'not allowed'
            if (row['year'], row['month']) > (2028, 6):
                row['close_usd_per_bbl'] = 987654321
        mutated = replace(self.prices, monthly=monthly)
        view = build_decision_snapshot(self.macro, self.world, mutated, as_of_year=2028, as_of_month=6)
        self.assertEqual(self.snapshot(), view)

    def test_unsettled_year_end_anchor_cannot_change_visible_snapshot(self):
        rows = deepcopy(self.macro.rows)
        current_year = next(row for row in rows if row['year'] == 2028)
        current_year['brent_oil_price_usd'] *= 1.5
        changed_macro = replace(
            self.macro,
            rows=tuple(rows),
            identity={
                **self.macro.identity,
                'identity_hash': 'counterfactual-unsettled-2028-anchor',
            },
        )
        changed_june = build_decision_snapshot(
            changed_macro,
            run_oil_shipping_world(changed_macro),
            run_oil_price_projection(changed_macro),
            as_of_year=2028,
            as_of_month=6,
        )
        self.assertEqual(self.snapshot(), changed_june)

        changed_december = build_decision_snapshot(
            changed_macro,
            run_oil_shipping_world(changed_macro),
            run_oil_price_projection(changed_macro),
            as_of_year=2028,
            as_of_month=12,
        )
        self.assertNotEqual(self.snapshot(month=12), changed_december)

    def test_wrong_input_identity_rejected(self):
        other = run_oil_price_projection(run_global_macro(7, 5))
        with self.assertRaises(ValueError):
            build_decision_snapshot(self.macro, self.world, other, as_of_year=2028, as_of_month=6)

    def test_visible_snapshot_does_not_alias_source_records(self):
        view = self.snapshot()
        original = self.world.turns[41]['routes'][0]['cargo_mbd']
        view['shipping']['routes'][0]['cargo_mbd'] = -999
        self.assertEqual(original, self.world.turns[41]['routes'][0]['cargo_mbd'])

    def test_http_health_and_decision_endpoint(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), AssetSimulationHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            root = f'http://127.0.0.1:{server.server_port}'
            with urlopen(root + '/api/health', timeout=30) as response:
                self.assertEqual(14, json.load(response)['explicitRouteCount'])
            with urlopen(root + '/api/decision?seed=42&years=5&year=2028&month=6', timeout=30) as response:
                self.assertEqual(self.snapshot(), json.load(response))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            clear_cache()


if __name__ == '__main__':
    unittest.main()
