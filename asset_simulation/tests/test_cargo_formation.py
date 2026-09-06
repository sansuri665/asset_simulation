"""Stage6C-preview2 cargo formation: cargo side closes without owning ships."""
from __future__ import annotations
from dataclasses import asdict
import unittest

from asset_simulation.model.freight_board import BoardSession, ImportRequirement, make_board_spec
from asset_simulation.model.freight_board.cargo_formation import form_cargo_plan, make_cargo_formation_spec
from asset_simulation.model.registry import sha256_json

V = 1_971_000
ZERO = {'gulf': 0, 'west_africa': 0}


def offered(snapshot):
    return {o: tuple(s.ship_id for s in snapshot.opened_market.ships if s.location == o)
            for o in snapshot.spec.origins}


class CargoFormationContracts(unittest.TestCase):
    def setUp(self):
        self.spec = make_board_spec()

    def test_high_inventory_defers_more_than_low_inventory(self):
        band = self.spec.destination_band
        high = band.normal_bbl + band.soft_high_bbl // 2
        low = band.normal_bbl + band.soft_low_bbl // 2
        natural = {'gulf': 4*V, 'west_africa': 2*V}
        results = []
        for stock in (high, low):
            s = BoardSession(self.spec, fleet_counts={'vlcc': 18, 'suezmax': 8}, initialization='cold',
                             destination_stock_bbl=stock)
            snap = s.open_turn(source_release_bbl=ZERO)
            results.append(form_cargo_plan(snap, offered(snap), natural))
        hi, lo = (r.report() for r in results)
        self.assertGreater(hi['inventory_draw_vs_natural_bbl'], lo['inventory_draw_vs_natural_bbl'])
        self.assertLess(hi['final_total_bbl'], lo['final_total_bbl'])

    def test_low_inventory_replaces_forced_gulf_shortage_from_waf(self):
        band = self.spec.destination_band
        low = band.normal_bbl + band.soft_low_bbl // 2
        s = BoardSession(self.spec, fleet_counts={'vlcc': 24, 'suezmax': 12}, initialization='cold',
                         source_stock_bbl=ZERO, destination_stock_bbl=low)
        snap = s.open_turn(source_release_bbl={'gulf': 2*V, 'west_africa': 8*V})
        natural = {'gulf': 5*V, 'west_africa': V}
        r = form_cargo_plan(snap, offered(snap), natural).report()
        self.assertEqual(r['final_cargo_plan_bbl']['gulf'], 2*V)
        self.assertGreater(r['final_cargo_plan_bbl']['west_africa'], V)
        self.assertLess(r['inventory_draw_vs_natural_bbl'], 3*V)
        self.assertEqual(r['physical_available_bbl']['gulf'], 2*V)

    def test_high_inventory_can_absorb_forced_source_shortage(self):
        band = self.spec.destination_band
        high = band.normal_bbl + band.soft_high_bbl // 2
        s = BoardSession(self.spec, fleet_counts={'vlcc': 24}, initialization='cold',
                         source_stock_bbl=ZERO, destination_stock_bbl=high)
        snap = s.open_turn(source_release_bbl={'gulf': 2*V, 'west_africa': 8*V})
        natural = {'gulf': 5*V, 'west_africa': V}
        r = form_cargo_plan(snap, offered(snap), natural).report()
        self.assertGreater(r['inventory_draw_vs_natural_bbl'], 0)
        self.assertLess(r['final_total_bbl'], sum(natural.values()))

    def test_ship_intentions_change_cargo_response_without_ship_optimization(self):
        s = BoardSession(self.spec, fleet_counts={'vlcc': 24, 'suezmax': 12}, initialization='cold')
        snap = s.open_turn(source_release_bbl=ZERO)
        all_ships = offered(snap)
        natural = {'gulf': 2*V, 'west_africa': V}
        balanced = all_ships
        waf_tight = {'gulf': all_ships['gulf'], 'west_africa': all_ships['west_africa'][:1]}
        a = form_cargo_plan(snap, balanced, natural)
        b = form_cargo_plan(snap, waf_tight, natural)
        pa, pb = a.report()['final_cargo_plan_bbl'], b.report()['final_cargo_plan_bbl']
        self.assertNotEqual(pa, pb)
        self.assertGreater(pb['gulf'], pa['gulf'])
        self.assertLess(pb['west_africa'], pa['west_africa'])
        for result, ships in ((a, balanced), (b, waf_tight)):
            routes = {r.origin: r.ordered_ship_ids for r in result.final_trial.plan.routes}
            self.assertEqual(routes, {o: tuple(ships[o]) for o in self.spec.origins})
            self.assertTrue(result.report()['contracts']['no_hidden_ship_optimizer'])

    def test_pure_replay_does_not_change_snapshot_or_price_memory(self):
        s = BoardSession(self.spec, fleet_counts={'vlcc': 18}, initialization='cold')
        snap = s.open_turn(source_release_bbl=ZERO)
        ships = offered(snap)
        natural = {'gulf': 4*V, 'west_africa': 2*V}
        before = sha256_json(asdict(snap))
        first = form_cargo_plan(snap, ships, natural)
        for _ in range(100):
            self.assertEqual(first, form_cargo_plan(snap, ships, natural))
        self.assertEqual(before, sha256_json(asdict(snap)))
        self.assertEqual(s.state.market.turn, 0)

    def test_urgent_current_shortage_cannot_be_repaired_by_future_orders(self):
        s = BoardSession(self.spec, fleet_counts={'vlcc': 12}, initialization='cold',
                         destination_stock_bbl=1)
        snap = s.open_turn(source_release_bbl=ZERO,
                           new_requirements=(ImportRequirement('urgent', 0, 2),))
        empty = {'gulf': (), 'west_africa': ()}
        formed = form_cargo_plan(snap, empty, {'gulf': V, 'west_africa': V})
        r = formed.report()
        self.assertFalse(r['final_trial_valid'])
        self.assertTrue(r['needs_ship_response'])
        self.assertIn('destination:turn:0', formed.final_trial.report()['inventory']['hard_bound_violations'])

    def test_source_limit_never_bypassed(self):
        s = BoardSession(self.spec, fleet_counts={'vlcc': 20}, initialization='cold',
                         source_stock_bbl=ZERO)
        snap = s.open_turn(source_release_bbl={'gulf': 10*V, 'west_africa': 10*V},
                           export_limits_bbl={'gulf': V, 'west_africa': 10*V})
        r = form_cargo_plan(snap, offered(snap), {'gulf': 6*V, 'west_africa': 2*V}).report()
        self.assertLessEqual(r['final_cargo_plan_bbl']['gulf'], V)
        self.assertEqual(r['physical_available_bbl']['gulf'], V)

    def test_import_requirements_survive_formation_and_commit(self):
        requirement = ImportRequirement('ea-demand', 5, V)
        s = BoardSession(self.spec, fleet_counts={'vlcc': 18}, initialization='cold')
        snap = s.open_turn(source_release_bbl=ZERO, new_requirements=(requirement,))
        formed = form_cargo_plan(snap, offered(snap), {'gulf': V, 'west_africa': V})
        self.assertIn(requirement, snap.requirements)
        if formed.final_trial.report()['valid']:
            s.commit(formed.final_trial)
            self.assertIn(requirement, s.state.requirements)

    def test_ledger_is_small_step_and_stable(self):
        s = BoardSession(self.spec, fleet_counts={'vlcc': 18}, initialization='cold')
        snap = s.open_turn(source_release_bbl=ZERO)
        formation = make_cargo_formation_spec()
        r = form_cargo_plan(snap, offered(snap), {'gulf': 5*V, 'west_africa': 3*V},
                            formation_spec=formation).report()
        self.assertLessEqual(len(r['iterations']), formation.max_iterations)
        for row in r['iterations']:
            action = row['action']
            if action is not None:
                self.assertLessEqual(action['bbl'], formation.step_bbl)
        self.assertIn(r['stop_reason'], {'no_improving_transparent_step', 'cycle_detected', 'max_iterations'})

    def test_natural_plan_is_prior_not_hard_share(self):
        s = BoardSession(self.spec, fleet_counts={'vlcc': 24}, initialization='cold')
        snap = s.open_turn(source_release_bbl=ZERO)
        ships = offered(snap)
        natural = {'gulf': 2*V, 'west_africa': 4*V}
        scarce = {'gulf': ships['gulf'], 'west_africa': ships['west_africa'][:1]}
        r = form_cargo_plan(snap, scarce, natural).report()
        self.assertNotEqual(r['final_cargo_plan_bbl'], natural)
        self.assertTrue(r['contracts']['natural_OD_plan_is_a_prior_not_a_hard_share'])

    def test_same_snapshot_can_be_reformed_after_external_ship_change(self):
        s = BoardSession(self.spec, fleet_counts={'vlcc': 24}, initialization='cold')
        snap = s.open_turn(source_release_bbl=ZERO)
        ships = offered(snap)
        natural = {'gulf': 3*V, 'west_africa': 3*V}
        a = form_cargo_plan(snap, ships, natural)
        b = form_cargo_plan(snap, {'gulf': ships['gulf'][:1], 'west_africa': ships['west_africa']}, natural)
        self.assertEqual(a.snapshot_id, b.snapshot_id)
        self.assertEqual(s.state.market.turn, 0)
        self.assertNotEqual(a.report()['final_route_service_value_real_usd_per_bbl'],
                            b.report()['final_route_service_value_real_usd_per_bbl'])


if __name__ == '__main__':
    unittest.main()
