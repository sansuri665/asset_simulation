"""Preview3 bilateral cargo, seller offers, main linkage and firm locks."""
from __future__ import annotations

from dataclasses import asdict
import unittest

from asset_simulation.model.freight_board import (
    BilateralSession,
    BoardSession,
    ImportRequirement,
    build_main_linked_preview3_inputs,
    form_bilateral_cargo_plan,
    form_seller_offers,
    make_bilateral_formation_spec,
    make_preview3_board_spec,
)
from asset_simulation.model.registry import sha256_json


V = 1_971_000
ZERO = {"gulf": 0, "west_africa": 0}


def offered(snapshot):
    return {
        origin: tuple(
            ship.ship_id for ship in snapshot.opened_market.ships
            if ship.location == origin
        )
        for origin in snapshot.spec.origins
    }


class SellerOfferTests(unittest.TestCase):
    def setUp(self):
        self.spec = make_preview3_board_spec()

    def _offers(self, source_stock):
        session = BoardSession(
            self.spec,
            fleet_counts={"vlcc": 12},
            initialization="cold",
            source_stock_bbl=source_stock,
        )
        snapshot = session.open_turn(source_release_bbl=ZERO)
        return form_seller_offers(snapshot, {"gulf": 2 * V, "west_africa": 2 * V})

    def test_high_source_inventory_offers_more_spot_than_low_inventory(self):
        bands = dict(self.spec.source_bands)
        high = {
            origin: band.normal_bbl + band.soft_high_bbl // 2
            for origin, band in self.spec.source_bands
        }
        low = {
            origin: band.normal_bbl + band.soft_low_bbl // 2
            for origin, band in self.spec.source_bands
        }
        hi, lo = self._offers(high), self._offers(low)
        for origin in self.spec.origins:
            self.assertGreater(hi[origin]["spot_offer_bbl"], lo[origin]["spot_offer_bbl"])
            self.assertGreater(hi[origin]["final_offer_bbl"], lo[origin]["final_offer_bbl"])

    def test_offer_never_exceeds_physical_oil_or_export_limit(self):
        session = BoardSession(
            self.spec,
            fleet_counts={"vlcc": 12},
            initialization="cold",
            source_stock_bbl=ZERO,
        )
        snapshot = session.open_turn(
            source_release_bbl={"gulf": 2 * V, "west_africa": 2 * V},
            export_limits_bbl={"gulf": V, "west_africa": 2 * V},
        )
        rows = form_seller_offers(snapshot, {"gulf": 5 * V, "west_africa": 5 * V})
        self.assertEqual(rows["gulf"]["final_offer_bbl"], V)
        self.assertGreater(rows["gulf"]["unsatisfied_term_bbl"], 0)
        for row in rows.values():
            self.assertLessEqual(row["final_offer_bbl"], row["physical_available_bbl"])

    def test_previous_offer_adds_declared_seller_inertia(self):
        bands = dict(self.spec.source_bands)
        high = {
            origin: band.normal_bbl + band.soft_high_bbl // 2
            for origin, band in self.spec.source_bands
        }
        session = BoardSession(
            self.spec, fleet_counts={"vlcc": 12}, initialization="cold", source_stock_bbl=high
        )
        snapshot = session.open_turn(source_release_bbl=ZERO)
        natural = {"gulf": 2 * V, "west_africa": 2 * V}
        immediate = form_seller_offers(snapshot, natural)
        previous = {origin: 0 for origin in self.spec.origins}
        inertial = form_seller_offers(snapshot, natural, previous_spot_offer_bbl=previous)
        for origin in self.spec.origins:
            self.assertLess(inertial[origin]["spot_offer_bbl"], immediate[origin]["spot_offer_bbl"])


class DirectionAndConvergenceTests(unittest.TestCase):
    def setUp(self):
        self.spec = make_preview3_board_spec()

    def test_destination_upper_violation_reduces_cargo_never_adds(self):
        stock = self.spec.destination_band.normal_bbl + self.spec.destination_band.hard_high_bbl
        session = BoardSession(
            self.spec,
            fleet_counts={"vlcc": 12},
            initialization="cold",
            destination_stock_bbl=stock,
        )
        snapshot = session.open_turn(source_release_bbl=ZERO)
        result = form_bilateral_cargo_plan(
            snapshot, offered(snapshot), {"gulf": V, "west_africa": V}
        ).report()
        self.assertLess(result["final_total_bbl"], result["natural_total_bbl"])
        actions = [row["action"] for row in result["iterations"] if row["action"]]
        self.assertTrue(actions)
        self.assertTrue(all(action["kind"] == "hard_bound_repair_remove" for action in actions))
        self.assertTrue(all(v["direction"] == "above_upper" for v in result["final_hard_violations"]))

    def test_urgent_current_shortage_is_not_faked_by_future_orders(self):
        session = BoardSession(
            self.spec, fleet_counts={"vlcc": 12}, initialization="cold", destination_stock_bbl=1
        )
        snapshot = session.open_turn(
            source_release_bbl=ZERO,
            new_requirements=(ImportRequirement("urgent", 0, 2),),
        )
        result = form_bilateral_cargo_plan(
            snapshot,
            {"gulf": (), "west_africa": ()},
            {"gulf": V, "west_africa": V},
        ).report()
        self.assertFalse(result["final_trial_valid"])
        self.assertTrue(result["needs_ship_response"])
        self.assertTrue(any(v["turn"] == 0 for v in result["final_hard_violations"]))

    def test_source_switch_has_no_reverse_pair_and_respects_budget(self):
        session = BoardSession(
            self.spec,
            fleet_counts={"vlcc": 24, "suezmax": 12},
            initialization="cold",
        )
        snapshot = session.open_turn(source_release_bbl=ZERO)
        natural = {"gulf": 5 * V, "west_africa": 2 * V}
        result = form_bilateral_cargo_plan(snapshot, offered(snapshot), natural).report()
        switches = [
            row["action"] for row in result["iterations"]
            if row["action"] and row["action"]["kind"] == "source_switch"
        ]
        directions = {(row["from"], row["to"]) for row in switches}
        self.assertLessEqual(len(directions), 1)
        inventory_kinds = {
            row["action"]["kind"] for row in result["iterations"]
            if row["action"] and row["action"]["kind"].startswith("buyer_inventory_")
        }
        self.assertFalse({"buyer_inventory_replenishment", "buyer_inventory_deferral"} <= inventory_kinds)
        self.assertNotEqual(result["stop_reason"], "cycle_detected")
        self.assertLessEqual(
            result["source_reallocated_bbl"],
            round(result["natural_total_bbl"] * make_bilateral_formation_spec().maximum_source_reallocation_fraction),
        )

    def test_default_buyer_quantity_stays_inside_normal_ten_percent_band(self):
        for offset in (1, -1):
            band = self.spec.destination_band
            stock = band.normal_bbl + (
                band.soft_high_bbl // 2 if offset > 0 else band.soft_low_bbl // 2
            )
            session = BoardSession(
                self.spec,
                fleet_counts={"vlcc": 24, "suezmax": 12},
                initialization="cold",
                destination_stock_bbl=stock,
            )
            snapshot = session.open_turn(source_release_bbl=ZERO)
            report = form_bilateral_cargo_plan(
                snapshot, offered(snapshot), {"gulf": 5 * V, "west_africa": 2 * V}
            ).report()
            deviation = abs(report["final_total_bbl"] - report["natural_total_bbl"])
            self.assertLessEqual(deviation, round(report["natural_total_bbl"] * 0.10))


class FirmnessTests(unittest.TestCase):
    def setUp(self):
        self.spec = make_preview3_board_spec()

    def _opened(self):
        session = BilateralSession(
            self.spec, fleet_counts={"vlcc": 12}, initialization="cold"
        )
        snapshot = session.open_turn(source_release_bbl=ZERO)
        indication = session.indicate(offered(snapshot), {"gulf": V, "west_africa": V})
        return session, snapshot, indication

    def test_indications_are_free_until_firm_plan_locks(self):
        session, snapshot, first = self._opened()
        second = session.indicate(
            {"gulf": offered(snapshot)["gulf"][:1], "west_africa": offered(snapshot)["west_africa"]},
            {"gulf": V, "west_africa": V},
        )
        self.assertNotEqual(first.identity, second.identity)
        firm = session.lock_firm(first)
        self.assertEqual(session.phase, "firm")
        with self.assertRaises(ValueError):
            session.indicate(offered(snapshot), {"gulf": V, "west_africa": V})
        record = session.commit_firm()
        self.assertEqual(record["preview3_commitment_id"], firm.identity)
        self.assertTrue(record["firm_plan_cannot_retarget_within_turn"])
        self.assertEqual(session.phase, "ready")

    def test_final_cargo_revision_is_limited_to_five_percent(self):
        session, _, indication = self._opened()
        cargo = dict(indication.final_cargo_plan)
        too_large = dict(cargo); too_large["gulf"] = max(0, cargo["gulf"] - 100_000)
        with self.assertRaises(ValueError):
            session.lock_firm(indication, cargo_revision_bbl=too_large)
        allowed = dict(cargo); allowed["gulf"] = max(0, cargo["gulf"] - 50_000)
        firm = session.lock_firm(indication, cargo_revision_bbl=allowed)
        self.assertEqual(dict(firm.cargo_revision_bbl)["gulf"], -50_000)

    def test_cancel_consumes_turn_and_does_not_restore_retargeting(self):
        session, _, indication = self._opened()
        before = session.state.market.turn
        firm = session.lock_firm(indication)
        record = session.cancel_firm()
        self.assertEqual(record["cancelled_commitment_id"], firm.identity)
        self.assertTrue(record["cancellation_consumed_turn"])
        self.assertFalse(record["same_turn_retargeting_allowed"])
        self.assertEqual(session.state.market.turn, before + 1)

    def test_checkpoint_preserves_seller_memory_and_exact_continuation(self):
        session, _, indication = self._opened()
        session.lock_firm(indication)
        session.commit_firm()
        checkpoint = session.checkpoint()
        restored = BilateralSession.restore(checkpoint, self.spec)
        self.assertEqual(restored.state, session.state)
        self.assertEqual(restored.previous_spot_offer_bbl, session.previous_spot_offer_bbl)
        results = []
        for owner in (session, restored):
            snapshot = owner.open_turn(source_release_bbl=ZERO)
            results.append(owner.indicate(offered(snapshot), {"gulf": V, "west_africa": V}))
        self.assertEqual(results[0], results[1])

    def test_checkpoint_rejects_open_turn_and_tampering(self):
        session, _, indication = self._opened()
        with self.assertRaises(ValueError):
            session.checkpoint()
        session.lock_firm(indication)
        session.commit_firm()
        checkpoint = session.checkpoint()
        checkpoint["payload"]["previous_spot_offer_bbl"]["gulf"] += 1
        with self.assertRaises(ValueError):
            BilateralSession.restore(checkpoint, self.spec)


class MainBridgeTests(unittest.TestCase):
    def test_main_link_is_read_only_exact_and_on_the_ten_day_clock(self):
        spec = make_preview3_board_spec()
        records, source = build_main_linked_preview3_inputs(spec, seed=42, years=5)
        self.assertEqual(len(records), 216)
        self.assertTrue(source["main_source_unchanged"])
        self.assertFalse(source["write_back_to_main"])
        replayed, replay_source = build_main_linked_preview3_inputs(spec, seed=42, years=5)
        self.assertEqual(records, replayed)
        self.assertEqual(source["records_hash"], replay_source["records_hash"])
        for turn, row in enumerate(records):
            self.assertEqual(row["turn"], turn)
            self.assertEqual(row["natural_cargo_plan_bbl"], row["source_release_bbl"])
            self.assertEqual(
                sum(r.volume_bbl for r in row["new_requirements"]),
                sum(row["natural_cargo_plan_bbl"].values()),
            )
        before = sha256_json([asdict(r) for row in records for r in row["new_requirements"]])
        self.assertEqual(before, sha256_json([asdict(r) for row in records for r in row["new_requirements"]]))


if __name__ == "__main__":
    unittest.main()
