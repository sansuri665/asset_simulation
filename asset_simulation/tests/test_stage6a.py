from __future__ import annotations
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world
from asset_simulation.model.global_shipping_contract import (
    CLASSES, CONFIG_PATH, apportion_barrels, ballast_plan, laden_plan,
    load_catalog, route_class_reference, sea_turns,
)
from asset_simulation.model.global_shipping_projection import (
    ProjectionError, project_pair_turn, replay_pair_months,
    summarize_world_projection, whole_barrels,
)
from asset_simulation.model.registry import sha256_json


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(CONFIG_PATH.read_text())
        self.cat = load_catalog()

    def test_exact_25_pairs_14_display_routes_and_11_residuals(self):
        lanes = self.cat['lanes']
        self.assertEqual(25, len(lanes))
        self.assertEqual(11, sum(r['is_residual_pair'] for r in lanes.values()))
        self.assertEqual(14, len({r['display_route_id'] for r in lanes.values() if not r['is_residual_pair']}))
        self.assertAlmostEqual(39.8, sum(r['reference_cargo_mbd'] for r in lanes.values()))
        self.assertEqual(16, sum(r['geography_ready'] for r in lanes.values()))

    def test_ballast_matrix_has_25_reverse_and_25_export_choices(self):
        self.assertEqual(50, len(self.cat['ballast_legs']))
        self.assertEqual(32, sum(v['geography_ready'] for v in self.cat['ballast_legs'].values()))
        self.assertEqual(5, sum(x['from'] == x['to'] for x in self.cat['ballast_legs'].values()))

    def test_all_share_rows_conserve_and_zero_share_not_ineligible(self):
        for lane in self.cat['lanes'].values():
            shares = lane['class_share_bps']
            self.assertEqual(10000, sum(shares.values()))
            for amount in (0, 1, 2, 3, 123, 100000003, 100000000000):
                split = apportion_barrels(amount, shares)
                self.assertEqual(amount, sum(split.values()))
                for c in CLASSES:
                    if shares[c] == 0:
                        self.assertEqual(0, split[c])
        self.assertEqual('aframax', laden_plan(self.cat, 'gulf::east_asia', 'aframax', 0).vessel_class)

    def test_parcel_size_is_not_deadweight(self):
        self.assertEqual(1971000, route_class_reference(self.cat, 'gulf::east_asia', 'vlcc')['cargo_bbl'])
        self.assertEqual(949000, route_class_reference(self.cat, 'west_africa::europe', 'suezmax')['cargo_bbl'])
        self.assertEqual(511000, route_class_reference(self.cat, 'us_gulf::europe', 'aframax')['cargo_bbl'])
        for pid in self.cat['lanes']:
            for c in CLASSES:
                row = route_class_reference(self.cat, pid, c)
                self.assertLess(row['cargo_tonnes'], self.cat['vessel_classes'][c]['reference_dwt_tonnes'])

    def test_shares_are_explicit_design_priors_not_empirical_data(self):
        for row in self.cat['lanes'].values():
            self.assertEqual('design_prior_not_observed', row['share_status'])
            self.assertEqual('low', row['share_confidence'])
        self.assertFalse(self.cat['scope']['global_fleet_simulator'])

    def test_changed_catalog_changes_identity_without_global_state(self):
        before = self.cat['catalog_hash']
        self.raw['class_share_matrix_bps']['gulf']['east_asia'] = [8500, 1500, 0]
        other = load_catalog(raw=self.raw)
        self.assertNotEqual(before, other['catalog_hash'])
        self.assertEqual(before, load_catalog()['catalog_hash'])

    def test_invalid_shares_and_unknown_pairs_fail(self):
        for values in ([9000, 900, 0], [9000, 1000, True], [-1, 10001, 0], [9000, 1000]):
            with self.subTest(values=values):
                raw = deepcopy(self.raw)
                raw['class_share_matrix_bps']['gulf']['east_asia'] = values
                with self.assertRaises(ValueError):
                    load_catalog(raw=raw)
        self.raw['parcel_overrides_tonnes']['unknown::unknown'] = {'vlcc': 270000}
        with self.assertRaises(ValueError):
            load_catalog(raw=self.raw)

    def test_invalid_clock_and_nonfinite_speeds_fail(self):
        for bad in (0, -1, float('nan'), float('inf'), True):
            raw = deepcopy(self.raw)
            raw['vessel_classes']['vlcc']['laden_speed_knots'] = bad
            with self.assertRaises(ValueError):
                load_catalog(raw=raw)
        for key in ('operating_turn_days', 'discharge_turns'):
            raw = deepcopy(self.raw); raw['clock'][key] = True
            with self.assertRaises(ValueError):
                load_catalog(raw=raw)

    def test_bad_upstream_hash_or_excess_payload_fail(self):
        self.raw['upstream_network_hash'] = '0' * 64
        with self.assertRaises(ValueError):
            load_catalog(raw=self.raw)
        raw = json.loads(CONFIG_PATH.read_text())
        raw['parcel_overrides_tonnes']['gulf::east_asia'] = {'vlcc': 300000}
        with self.assertRaises(ValueError):
            load_catalog(raw=raw)

    def test_path_masks_validated_and_not_silently_ignored(self):
        with self.assertRaises(ValueError):
            laden_plan(self.cat, 'gulf::europe', 'vlcc', 0, path_id='suez_proxy')
        self.raw['path_overrides']['gulf::europe']['default_path_by_class']['vlcc'] = 'suez_proxy'
        with self.assertRaises(ValueError):
            load_catalog(raw=self.raw)

    def test_aggregate_geography_cannot_be_used_as_a_free_port(self):
        for pid in ('other_export_regions::east_asia', 'gulf::rest_of_world'):
            with self.assertRaises(ValueError):
                laden_plan(self.cat, pid, 'vlcc', 0)
        for origin, destination in [('east_asia', 'other_export_regions'), ('rest_of_world', 'gulf'), ('gulf', 'other_export_regions')]:
            with self.assertRaises(ValueError):
                ballast_plan(self.cat, origin, destination, 'vlcc', 0)
        self.assertFalse(route_class_reference(self.cat, 'other_export_regions::east_asia', 'vlcc')['geography_ready'])

    def test_cross_export_priors_cannot_disappear_or_duplicate(self):
        raw = deepcopy(self.raw)
        raw['export_cross_distance_priors_nm'].pop('gulf::us_gulf')
        with self.assertRaises(ValueError):
            load_catalog(raw=raw)
        raw = deepcopy(self.raw)
        raw['export_cross_distance_priors_nm']['us_gulf::gulf'] = 8500
        with self.assertRaises(ValueError):
            load_catalog(raw=raw)


class MovementPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = load_catalog()

    def test_zero_two_one_two_preserves_stage5a(self):
        plan = laden_plan(self.cat, 'gulf::east_asia', 'vlcc', 7)
        self.assertEqual(['NOT_STARTED', 'LADEN', 'LADEN', 'DISCHARGING', 'OPEN_AT_DESTINATION'],
                         [plan.state_at(t) for t in range(6, 11)])
        back = ballast_plan(self.cat, 'east_asia', 'gulf', 'vlcc', plan.ready_turn)
        self.assertEqual(12, back.ready_turn)
        self.assertEqual(0, back.cargo_bbl)
        self.assertEqual(0, back.discharge_turns)

    def test_discharge_opens_at_destination_not_automatic_home(self):
        plan = laden_plan(self.cat, 'gulf::east_asia', 'vlcc', 0)
        self.assertEqual('east_asia', plan.destination)
        to_waf = ballast_plan(self.cat, 'east_asia', 'west_africa', 'vlcc', plan.ready_turn)
        self.assertEqual('west_africa', to_waf.destination)
        self.assertNotEqual(to_waf.ready_turn, ballast_plan(self.cat, 'east_asia', 'gulf', 'vlcc', 3).ready_turn)

    def test_laden_and_ballast_paths_need_not_be_symmetric(self):
        plan = laden_plan(self.cat, 'gulf::europe', 'vlcc', 0)
        back = ballast_plan(self.cat, 'europe', 'gulf', 'vlcc', plan.ready_turn)
        self.assertEqual(11500, plan.distance_nm)
        self.assertEqual(6100, back.distance_nm)
        self.assertEqual((4, 2), (plan.sea_turns, back.sea_turns))

    def test_different_cargo_not_automatic_different_speed(self):
        raw = json.loads(CONFIG_PATH.read_text())
        raw['vessel_classes']['suezmax']['laden_speed_knots'] = 10
        cat = load_catalog(raw=raw)
        fast = laden_plan(cat, 'brazil_guyana::east_asia', 'vlcc', 0)
        slow = laden_plan(cat, 'brazil_guyana::east_asia', 'suezmax', 0)
        self.assertGreater(slow.sea_turns, fast.sea_turns)

    def test_effective_distance_is_replacement_not_double_multiplier(self):
        plan = laden_plan(self.cat, 'gulf::europe', 'vlcc', 0, effective_distance_nm=12000)
        self.assertEqual(12000, plan.distance_nm)
        self.assertEqual(12000 / (13 * 24), plan.reference_sea_days)

    def test_departure_plan_is_immutable_and_does_not_read_later_settings(self):
        cat = load_catalog()
        plan = laden_plan(cat, 'gulf::east_asia', 'vlcc', 0)
        old = plan.to_dict()
        cat['vessel_classes']['vlcc']['laden_speed_knots'] = 1
        self.assertEqual(old, plan.to_dict())
        with self.assertRaises(FrozenInstanceError):
            plan.ready_turn = 20

    def test_rounding_policy_is_explicit_with_no_negative_zero_sea_leg(self):
        self.assertEqual(0, sea_turns(0, 13))
        self.assertEqual(1, sea_turns(1, 13))
        self.assertEqual(2, sea_turns(13 * 24 * 15, 13))
        ref = route_class_reference(self.cat, 'gulf::south_asia', 'vlcc')
        self.assertGreater(ref['laden_rounding_error_days'], 4)
        self.assertEqual(3, ref['roundtrip_reference_turns'])
        with self.assertRaises(ValueError):
            sea_turns(-1, 13)

    def test_unknown_transfer_or_class_is_an_error_not_a_teleport(self):
        for o, d, c in [('east_asia', 'unknown', 'vlcc'), ('europe', 'east_asia', 'vlcc'), ('gulf', 'us_gulf', 'lr2')]:
            with self.assertRaises(ValueError):
                ballast_plan(self.cat, o, d, c, 0)
        stay = ballast_plan(self.cat, 'gulf', 'gulf', 'vlcc', 8)
        self.assertEqual(8, stay.ready_turn)
        self.assertEqual(0, stay.sea_turns)


class DemandProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = load_catalog()
        cls.macro = run_global_macro(seed=42, years=5)
        cls.world = run_oil_shipping_world(cls.macro)

    def test_all_25_pairs_reproduce_14_plus_pool(self):
        restored = list(replay_pair_months(self.world, self.cat))
        self.assertEqual(72, len(restored))
        for month in restored:
            self.assertEqual(25, len(month['pairs_mbd']))
            self.assertLess(month['maximum_group_cargo_error_mbd'], 1e-7)
            self.assertAlmostEqual(sum(month['pairs_mbd'].values()), month['upstream_month_cargo_mbd'], places=6)

    def test_projection_preserves_barrels_and_keeps_residual_geography(self):
        result = summarize_world_projection(self.world, self.cat)
        self.assertEqual(0, result['class_split_residual_bbl'])
        self.assertTrue(result['source_unchanged'])
        self.assertGreater(result['residual_11_pair_plan_bbl'], 0)
        self.assertLessEqual(result['maximum_pair_month_rounding_error_bbl'], 1.50001)
        self.assertEqual(2160, result['operating_day_count'])
        for c in CLASSES:
            row = result['class_totals'][c]
            self.assertGreater(row['unresolved_geography_bbl'], 0)
            self.assertEqual(row['plan_bbl'], row['geography_ready_bbl'] + row['unresolved_geography_bbl'])
        self.assertFalse(result['price_present'])
        self.assertFalse(result['global_fleet_size_inferred'])

    def test_projecting_does_not_mutate_source_or_config(self):
        before = sha256_json({'turns': self.world.turns, 'catalog': self.cat})
        summarize_world_projection(self.world, self.cat)
        self.assertEqual(before, sha256_json({'turns': self.world.turns, 'catalog': self.cat}))

    def test_whole_parcels_plus_remainder_and_zero_demand(self):
        for q in (0, 0.0000001, 9.3, 100):
            p = project_pair_turn(self.cat, 'gulf::east_asia', q)
            self.assertEqual(0, p['class_split_residual_bbl'])
            self.assertEqual(whole_barrels(q, 10), sum(v['allocated_plan_bbl'] for v in p['classes']))
            for r in p['classes']:
                self.assertEqual(r['allocated_plan_bbl'], r['whole_parcels_in_this_slice'] * r['reference_parcel_bbl'] + r['remainder_bbl_in_this_slice'])

    def test_changed_class_shares_change_partition_not_total_demand(self):
        raw = json.loads(CONFIG_PATH.read_text())
        raw['class_share_matrix_bps']['gulf']['east_asia'] = [7500, 2500, 0]
        a = project_pair_turn(self.cat, 'gulf::east_asia', 9.3)
        b = project_pair_turn(load_catalog(raw=raw), 'gulf::east_asia', 9.3)
        self.assertEqual(a['scheduled_plan_bbl'], b['scheduled_plan_bbl'])
        self.assertNotEqual(a['classes'][0]['allocated_plan_bbl'], b['classes'][0]['allocated_plan_bbl'])

    def test_prefix_replay_cannot_read_future_months(self):
        # Any later tampering must not change the first yielded month.
        altered = deepcopy(self.world)
        altered.turns[-1]['routes'][0]['cargo_mbd'] += 1
        self.assertEqual(next(replay_pair_months(self.world, self.cat)), next(replay_pair_months(altered, self.cat)))
        with self.assertRaises(ProjectionError):
            list(replay_pair_months(altered, self.cat))

    def test_hidden_scenario_wrong_source_and_missing_routes_are_rejected(self):
        bad = deepcopy(self.world); bad.identity['scenario_hash'] = 'not-normal'
        with self.assertRaises(ProjectionError):
            next(replay_pair_months(bad, self.cat))
        bad = deepcopy(self.world); bad.identity['config_hash'] = 'different'
        with self.assertRaises(ProjectionError):
            next(replay_pair_months(bad, self.cat))
        bad = deepcopy(self.world); bad.turns[0]['routes'].pop()
        with self.assertRaises(ProjectionError):
            next(replay_pair_months(bad, self.cat))

    def test_constant_daily_flow_is_independent_of_month_label(self):
        first = project_pair_turn(self.cat, 'gulf::east_asia', 9.3)
        for _ in (28, 29, 30, 31):
            self.assertEqual(first, project_pair_turn(self.cat, 'gulf::east_asia', 9.3))
        self.assertEqual(279000000, first['scheduled_plan_bbl'] * 3)
        self.assertNotEqual(279000000, whole_barrels(9.3, 31))


if __name__ == '__main__':
    unittest.main()
