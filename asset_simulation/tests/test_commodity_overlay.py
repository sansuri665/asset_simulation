from __future__ import annotations

import math
import unittest
from statistics import median

from asset_simulation.model.commodity_overlay import (
    COMMODITY_MODEL_VERSION,
    CONTRACT_ORDER,
    KIND_ORDER,
    run_commodity_overlay,
)
from asset_simulation.model.engine import run_global_macro
from asset_simulation.model import oil_commodity


class CommodityOverlayTests(unittest.TestCase):
    def test_brent_is_global_pass_through_and_satellites_are_deterministic(self) -> None:
        first = run_global_macro(42, 12)
        overlay = run_commodity_overlay(first)
        repeat = run_commodity_overlay(run_global_macro(42, 12))
        other = run_commodity_overlay(run_global_macro(7, 12))
        longer = run_commodity_overlay(run_global_macro(42, 60))

        self.assertEqual(COMMODITY_MODEL_VERSION, overlay.identity["model_version"])
        self.assertEqual(list(KIND_ORDER), overlay.identity["kind_ids"])
        self.assertEqual(list(CONTRACT_ORDER), overlay.identity["contract_ids"])
        self.assertFalse(overlay.identity["write_back"])
        self.assertEqual("oil_commodity", overlay.identity["brent_owner"])
        self.assertEqual(overlay.identity["result_hash"], repeat.identity["result_hash"])
        self.assertEqual(overlay.contracts["brent"], longer.contracts["brent"][:13])
        self.assertNotEqual(overlay.contracts["copper"][3]["nominal_price_usd"], other.contracts["copper"][3]["nominal_price_usd"])

        for row, global_row in zip(overlay.contracts["brent"], first.rows, strict=True):
            self.assertAlmostEqual(row["nominal_price_usd"], global_row["brent_oil_price_usd"], places=6)
            self.assertAlmostEqual(row["real_price_index"], global_row["global_real_oil_price_index"], places=6)

        for index, row in enumerate(overlay.contracts["brent"]):
            previous = overlay.contracts["brent"][index - 1] if index else row
            open_nominal = previous["nominal_price_usd"]
            close_nominal = row["nominal_price_usd"]
            body_high = max(open_nominal, close_nominal)
            body_low = min(open_nominal, close_nominal)
            self.assertGreaterEqual(row["nominal_high_usd"], body_high - 1e-9)
            self.assertLessEqual(row["nominal_low_usd"], body_low + 1e-9)
            self.assertGreaterEqual(row["real_high_index"], max(previous["real_price_index"], row["real_price_index"]) - 1e-9)
            self.assertLessEqual(row["real_low_index"], min(previous["real_price_index"], row["real_price_index"]) + 1e-9)
            self.assertGreater(row["nominal_high_usd"], row["nominal_low_usd"])
        self.assertNotIn("nominal_high_usd", overlay.contracts["copper"][3])
        self.assertNotIn("monthly", overlay.contracts["copper"][3])
        self.assertNotEqual(
            overlay.contracts["brent"][4]["nominal_high_usd"],
            other.contracts["brent"][4]["nominal_high_usd"],
        )

        for index, row in enumerate(overlay.contracts["brent"]):
            previous = overlay.contracts["brent"][index - 1] if index else row
            months = row["monthly"]
            self.assertEqual(12, len(months))
            self.assertAlmostEqual(months[0]["open"], previous["nominal_price_usd"], places=6)
            self.assertAlmostEqual(months[-1]["close"], row["nominal_price_usd"], places=6)
            self.assertAlmostEqual(max(item["high"] for item in months), row["nominal_high_usd"], places=6)
            self.assertAlmostEqual(min(item["low"] for item in months), row["nominal_low_usd"], places=6)
            self.assertAlmostEqual(months[0]["real_open"], previous["real_price_index"], places=6)
            self.assertAlmostEqual(months[-1]["real_close"], row["real_price_index"], places=6)
            for month, item in enumerate(months, start=1):
                self.assertEqual(month, item["month"])
                self.assertLessEqual(item["low"], min(item["open"], item["close"]) + 1e-9)
                self.assertGreaterEqual(item["high"], max(item["open"], item["close"]) - 1e-9)
                weeks = item["weekly"]
                self.assertEqual(4, len(weeks))
                self.assertAlmostEqual(weeks[0]["open"], item["open"], places=6)
                self.assertAlmostEqual(weeks[-1]["close"], item["close"], places=6)
                self.assertAlmostEqual(max(week["high"] for week in weeks), item["high"], places=6)
                self.assertAlmostEqual(min(week["low"] for week in weeks), item["low"], places=6)
                self.assertAlmostEqual(weeks[0]["real_open"], item["real_open"], places=6)
                self.assertAlmostEqual(weeks[-1]["real_close"], item["real_close"], places=6)
                for week_number, week in enumerate(weeks, start=1):
                    self.assertEqual(week_number, week["week"])
                    self.assertLessEqual(week["low"], min(week["open"], week["close"]) + 1e-9)
                    self.assertGreaterEqual(week["high"], max(week["open"], week["close"]) - 1e-9)
                for left, right in zip(weeks, weeks[1:]):
                    self.assertAlmostEqual(left["close"], right["open"], places=6)
                month_leftover_up = item["high"] - max(item["open"], item["close"])
                month_leftover_dn = min(item["open"], item["close"]) - item["low"]
                week_max_oc = max(max(week["open"], week["close"]) for week in weeks)
                week_min_oc = min(min(week["open"], week["close"]) for week in weeks)
                if month_leftover_up > 0.01 * max(item["open"], item["close"]):
                    self.assertLess(week_max_oc, item["high"] - 1e-9)
                if month_leftover_dn > 0.01 * min(item["open"], item["close"]):
                    self.assertGreater(week_min_oc, item["low"] + 1e-9)
            for left, right in zip(months, months[1:]):
                self.assertAlmostEqual(left["close"], right["open"], places=6)
            leftover_up = row["nominal_high_usd"] - max(previous["nominal_price_usd"], row["nominal_price_usd"])
            leftover_dn = min(previous["nominal_price_usd"], row["nominal_price_usd"]) - row["nominal_low_usd"]
            max_oc = max(max(item["open"], item["close"]) for item in months)
            min_oc = min(min(item["open"], item["close"]) for item in months)
            if leftover_up > 0.01 * max(previous["nominal_price_usd"], row["nominal_price_usd"]):
                self.assertLess(max_oc, row["nominal_high_usd"] - 1e-9)
                hi = max(months, key=lambda item: item["high"])
                self.assertGreater(hi["high"] - max(hi["open"], hi["close"]), 1e-9)
            if leftover_dn > 0.01 * min(previous["nominal_price_usd"], row["nominal_price_usd"]):
                self.assertGreater(min_oc, row["nominal_low_usd"] + 1e-9)
                lo = min(months, key=lambda item: item["low"])
                self.assertGreater(min(lo["open"], lo["close"]) - lo["low"], 1e-9)
        self.assertEqual(overlay.contracts["brent"][5]["monthly"], longer.contracts["brent"][5]["monthly"])
        month_rets = [
            abs(100.0 * (item["close"] / item["open"] - 1.0))
            for row in longer.contracts["brent"][1:]
            for item in row["monthly"]
            if item["open"]
        ]
        self.assertGreater(median(month_rets), 4.8)
        self.assertLess(median(month_rets), 5.8)
        self.assertLess(sum(1 for value in month_rets if value < 2.0) / len(month_rets), 0.42)
        self.assertLess(max(month_rets), 28.0)
        quiet_uppers = []
        quiet_lowers = []
        for row in longer.contracts["brent"][1:]:
            for item in row["monthly"]:
                if not item["open"]:
                    continue
                body_log = abs(100.0 * math.log(item["close"] / item["open"]))
                if body_log >= 1.8:
                    continue
                body_high = max(item["open"], item["close"])
                body_low = min(item["open"], item["close"])
                if item["high"] >= row["nominal_high_usd"] - 1e-6:
                    continue
                if item["low"] <= row["nominal_low_usd"] + 1e-6:
                    continue
                quiet_uppers.append((item["high"] / body_high - 1.0) * 100.0)
                quiet_lowers.append((1.0 - item["low"] / body_low) * 100.0)
        self.assertGreater(len(quiet_uppers), 20)
        self.assertGreater(median(quiet_uppers), 1.8)
        self.assertGreater(median(quiet_lowers), 1.8)
        week_rets = [
            abs(100.0 * (week["close"] / week["open"] - 1.0))
            for row in longer.contracts["brent"][1:]
            for item in row["monthly"]
            for week in item["weekly"]
            if week["open"]
        ]
        self.assertGreater(median(week_rets), 2.4)
        self.assertLess(median(week_rets), 3.1)
        self.assertLess(sum(1 for value in week_rets if value < 1.0) / len(week_rets), 0.40)
        self.assertLess(max(week_rets), 22.0)
        year_ranges = [
            100.0
            * (row["nominal_high_usd"] - row["nominal_low_usd"])
            / ((row["nominal_high_usd"] + row["nominal_low_usd"]) / 2.0)
            for row in longer.contracts["brent"][1:]
            if row["nominal_high_usd"] + row["nominal_low_usd"]
        ]
        self.assertGreater(median(year_ranges), 36.0)
        self.assertLess(median(year_ranges), 42.0)

        quiet = oil_commodity.annual_price_envelope(
            seed=42, year_index=3, open_real=100.0, close_real=100.0,
            inventory_tightness_index=0.0, oil_demand_index=100.0, oil_supply_index=100.0,
            dollar_yoy_change_pct=0.0, real_bounds=(35.0, 280.0),
        )
        tight = oil_commodity.annual_price_envelope(
            seed=42, year_index=3, open_real=100.0, close_real=100.0,
            inventory_tightness_index=12.0, oil_demand_index=100.0, oil_supply_index=100.0,
            dollar_yoy_change_pct=0.0, real_bounds=(35.0, 280.0),
        )
        volatile = oil_commodity.annual_price_envelope(
            seed=42, year_index=3, open_real=100.0, close_real=100.0,
            inventory_tightness_index=0.0, oil_demand_index=100.0, oil_supply_index=100.0,
            dollar_yoy_change_pct=0.0, real_bounds=(35.0, 280.0),
            volatility_regime_index=1.25,
        )
        up_year = oil_commodity.annual_price_envelope(
            seed=42, year_index=3, open_real=100.0, close_real=118.0,
            inventory_tightness_index=10.0, oil_demand_index=104.0, oil_supply_index=100.0,
            dollar_yoy_change_pct=0.0, real_bounds=(35.0, 280.0),
        )
        down_year = oil_commodity.annual_price_envelope(
            seed=42, year_index=3, open_real=100.0, close_real=85.0,
            inventory_tightness_index=-10.0, oil_demand_index=100.0, oil_supply_index=104.0,
            dollar_yoy_change_pct=0.0, real_bounds=(35.0, 280.0),
        )
        self.assertGreater(tight["real_high_index"], quiet["real_high_index"])
        self.assertLess(tight["real_low_index"], quiet["real_low_index"])
        self.assertGreater(volatile["real_high_index"], quiet["real_high_index"])
        self.assertLess(volatile["real_low_index"], quiet["real_low_index"])
        up_upper = 100.0 * (up_year["real_high_index"] / 118.0 - 1.0)
        up_lower = 100.0 * (100.0 / up_year["real_low_index"] - 1.0)
        down_upper = 100.0 * (down_year["real_high_index"] / 100.0 - 1.0)
        down_lower = 100.0 * (85.0 / down_year["real_low_index"] - 1.0)
        self.assertLess(up_upper, up_lower)
        self.assertLess(down_lower, down_upper)
        self.assertLess(up_upper, 8.0)
        self.assertLess(down_lower, 8.0)

        months = oil_commodity.expand_annual_to_months(
            seed=42, year_index=3, open_px=100.0, close_px=118.0, high_px=130.0, low_px=90.0,
        )
        self.assertEqual(12, len(months))
        self.assertAlmostEqual(months[0]["open"], 100.0, places=6)
        self.assertAlmostEqual(months[-1]["close"], 118.0, places=6)
        self.assertAlmostEqual(max(item["high"] for item in months), 130.0, places=6)
        self.assertAlmostEqual(min(item["low"] for item in months), 90.0, places=6)
        self.assertLess(max(max(item["open"], item["close"]) for item in months), 130.0 - 1e-9)
        self.assertGreater(min(min(item["open"], item["close"]) for item in months), 90.0 + 1e-9)
        hi = max(months, key=lambda item: item["high"])
        lo = min(months, key=lambda item: item["low"])
        self.assertGreater(hi["high"] - max(hi["open"], hi["close"]), 1.0)
        self.assertGreater(min(lo["open"], lo["close"]) - lo["low"], 1.0)
        quiet_year = oil_commodity.expand_annual_to_months(
            seed=42, year_index=8, open_px=100.0, close_px=101.5, high_px=118.0, low_px=86.0,
        )
        quiet_months = [
            item
            for item in quiet_year
            if item["open"]
            and abs(100.0 * math.log(item["close"] / item["open"])) < 1.8
            and item["high"] < 118.0 - 1e-6
            and item["low"] > 86.0 + 1e-6
        ]
        self.assertTrue(quiet_months)
        for item in quiet_months:
            body_high = max(item["open"], item["close"])
            body_low = min(item["open"], item["close"])
            self.assertGreater((item["high"] / body_high - 1.0) * 100.0, 1.6)
            self.assertGreater((1.0 - item["low"] / body_low) * 100.0, 1.6)
        former_plateau = oil_commodity.expand_annual_to_months(
            seed=42,
            year_index=8,
            open_px=77.62213095,
            close_px=73.92499704,
            high_px=91.97784721,
            low_px=67.47813805,
        )
        plateau_run = 1
        longest_plateau = 1
        for left, right in zip(former_plateau, former_plateau[1:]):
            plateau_run = plateau_run + 1 if left["close"] == right["close"] else 1
            longest_plateau = max(longest_plateau, plateau_run)
        self.assertEqual(1, longest_plateau)
        weeks = oil_commodity.expand_month_to_weeks(
            seed=42, year_index=3, month=4, open_px=100.0, close_px=104.0, high_px=110.0, low_px=96.0,
        )
        self.assertEqual(4, len(weeks))
        self.assertAlmostEqual(weeks[0]["open"], 100.0, places=6)
        self.assertAlmostEqual(weeks[-1]["close"], 104.0, places=6)
        self.assertAlmostEqual(max(item["high"] for item in weeks), 110.0, places=6)
        self.assertAlmostEqual(min(item["low"] for item in weeks), 96.0, places=6)
        self.assertLess(max(max(item["open"], item["close"]) for item in weeks), 110.0 - 1e-9)
        self.assertGreater(min(min(item["open"], item["close"]) for item in weeks), 96.0 + 1e-9)
        former_weekly_plateau = oil_commodity.expand_month_to_weeks(
            seed=42,
            year_index=0,
            month=7,
            open_px=77.07581303,
            close_px=78.5823346,
            high_px=78.92078425,
            low_px=74.6814232,
        )
        self.assertTrue(
            all(
                left["close"] != right["close"]
                for left, right in zip(former_weekly_plateau, former_weekly_plateau[1:])
            )
        )

        opening_wti = overlay.contracts["wti"][0]
        opening_brent = overlay.contracts["brent"][0]
        self.assertAlmostEqual(
            opening_wti["nominal_price_usd"] / opening_brent["nominal_price_usd"] - 1.0,
            -0.035,
            places=6,
        )
        self.assertAlmostEqual(
            overlay.contracts["wheat"][4]["nominal_price_usd"],
            overlay.contracts["wheat"][4]["real_price_index"]
            / 100.0
            * 6.0
            * first.rows[4]["cpi_price_level_index_2025_100"]
            / 100.0,
            places=4,
        )
        energy = overlay.kinds["energy"][0]
        self.assertAlmostEqual(
            energy["real_index"],
            0.50 * overlay.contracts["brent"][0]["real_price_index"]
            + 0.20 * overlay.contracts["wti"][0]["real_price_index"]
            + 0.15 * overlay.contracts["henry_hub"][0]["real_price_index"]
            + 0.15 * overlay.contracts["ttf"][0]["real_price_index"],
            places=6,
        )
