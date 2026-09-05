"""One-use exact patch; restrict publication to two checksum-verified v3 files."""
import hashlib
import os
from pathlib import Path
assert os.environ['GITHUB_REF'] == 'refs/heads/stage6b-v3-transparent-market'
assert os.environ['GITHUB_REPOSITORY'] == 'sansuri665/asset_simulation'
p = Path('asset_simulation/model/shipping_v3/pricing.py')
assert hashlib.sha256(p.read_bytes()).hexdigest() == '95b2feafdb69a5d44b89b27b7a7011ec89efff0f583098f3d476c4dece1e1ceb'
s = p.read_text()
s = s.replace("    normalizer = a['reference_prompt_multiplier'] + sum(weights[1:])\n", '')
s = s.replace("        weighted = sum(w * s for w, s in zip(weights, capacity))\n        # At steady ordinary coverage A0 = 1.06*Q and A1=A2=Q,\n        # normalized available work equals Q. Without this term, simply\n        # lengthening the horizon would artificially depress every quote.\n        normalized = weighted / normalizer", """        weighted = sum(w * s for w, s in zip(weights, capacity))
        # Orders are placed AFTER the quote. With a reference return of b
        # turns, normal already-committed arrivals at this opening can only
        # occupy horizons 1..b-1. Do not price unmade h=b orders as missing
        # supply. Longer cross-origin commitments still count when real.
        reference_service = next((x for x in lane.services if x.class_id == 'vlcc'), lane.services[0])
        return_lag = reference_service.return_leg.ready_turn
        normal_profile = [a['reference_prompt_multiplier']] + [
            1.0 if h < return_lag else 0.0 for h in range(1, len(weights))]
        normalizer = sum(w * expected for w, expected in zip(weights, normal_profile))
        normalized = weighted / normalizer""")
s = s.replace("'capacities': capacity, 'weighted': weighted, 'normalized': normalized,", "'capacities': capacity, 'weighted': weighted, 'normalized': normalized,\n                  'normalizer': normalizer, 'normal_profile': normal_profile, 'reference_return_lag': return_lag,")
s = s.replace("'weighted_scheduled_capacity_bbl': s['weighted'], 'normal_schedule_divisor': normalizer,", "'weighted_scheduled_capacity_bbl': s['weighted'], 'normal_schedule_divisor': s['normalizer'],\n            'normal_committed_arrival_profile': s['normal_profile'],\n            'reference_return_lag_turns': s['reference_return_lag'],")
assert hashlib.sha256(s.encode()).hexdigest() == 'c89b2e20261f27dda5d156f6ca1087efeeb381937cc705359d1e59f8bfcb9612'
p.write_bytes(s.encode())
p = Path('asset_simulation/tests/test_shipping_market_v3.py')
assert hashlib.sha256(p.read_bytes()).hexdigest() == '24547a9e314a87a9732e4bd8663a3e032b997f2cf40c35d8945e628740f1d8e1'
s = p.read_text()
s = s.replace("        buckets[lane.origin]=[{'horizon_turns':i,'capacity_bbl':round(volume*(now_factor if i==0 else future_factor))}\n                               for i in range(len(cfg['availability']['arrival_weights']))]", """        ref = next((x for x in lane.services if x.class_id == 'vlcc'), lane.services[0])
        buckets[lane.origin]=[{'horizon_turns':i,'capacity_bbl':round(volume*(now_factor if i==0 else future_factor if i < ref.return_leg.ready_turn else 0))}
                               for i in range(len(cfg['availability']['arrival_weights']))]""")
s = s.replace('    def test_future_supply_changes_quote_but_not_current_capacity(self):', """    def test_normal_profile_respects_opening_before_routing(self):
        q,_=pure(self.spec)
        self.assertEqual(q['routes']['gulf']['explanation']['normal_committed_arrival_profile'],[1.06,1.,0.])
        self.assertEqual(q['routes']['west_africa']['explanation']['normal_committed_arrival_profile'],[1.06,1.,1.])
        self.assertAlmostEqual(q['routes']['gulf']['explanation']['normal_schedule_divisor'],1.56)
        self.assertAlmostEqual(q['routes']['west_africa']['explanation']['normal_schedule_divisor'],1.71)

    def test_real_early_commitment_beyond_normal_profile_still_matters(self):
        spec=make_market_spec(origins=('gulf',));q,args=pure(spec)
        args['availability']['routes']['gulf'][2]['capacity_bbl']=93000000
        after=quote_routes(spec,**args)
        self.assertLess(after['routes']['gulf']['route_benchmark_real_tce'],q['routes']['gulf']['route_benchmark_real_tce'])

    def test_future_supply_changes_quote_but_not_current_capacity(self):""")
assert hashlib.sha256(s.encode()).hexdigest() == '94d50359d547b7889ff0e3abd3c7a872a0df35767026ccec1b5737cc8c91a891'
p.write_bytes(s.encode())
print('Patched opening-time normalization and two additional regression controls.')
