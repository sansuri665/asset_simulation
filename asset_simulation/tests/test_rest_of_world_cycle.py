from __future__ import annotations

import statistics
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_shipping_regions import _advance_rest_of_world_cycle
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world
from asset_simulation.model.registry import load_registered_assets


def _own_refinery_overlay(regions: dict[str, dict[str, object]]) -> float:
    return (
        float(regions["us_gulf"]["refinery_cycle_adjustment_mbd"])
        + float(regions["rest_of_world"]["refinery_cycle_adjustment_mbd"])
    )


class RestOfWorldCycleTests(unittest.TestCase):
    def test_refinery_cycle_is_persistent_bounded_and_nontrivial(self) -> None:
        cycle = load_registered_assets()["oil_shipping_demand_config"][
            "regional_oil"
        ]["rest_of_world_cycle"]
        state = {
            "refinery_operational_deviation_mbd": 0.0,
            "refinery_deviation_mbd": 0.0,
        }
        adjustments: list[float] = []
        targets: list[float] = []
        for turn_index in range(10 * 12):
            state, target = _advance_rest_of_world_cycle(
                state,
                seed=42,
                turn_index=turn_index,
                month=turn_index % 12 + 1,
                cycle=cycle,
            )
            adjustments.append(float(state["refinery_deviation_mbd"]))
            targets.append(float(target))

        monthly_changes = [
            current - previous
            for previous, current in zip(adjustments, adjustments[1:])
        ]
        self.assertGreater(statistics.stdev(monthly_changes), 0.020)
        self.assertLess(statistics.stdev(monthly_changes), 0.13)
        self.assertGreater(max(adjustments) - min(adjustments), 0.18)
        self.assertGreaterEqual(min(targets), -0.42 - 1e-8)
        self.assertLessEqual(max(targets), 0.24 + 1e-8)
        self.assertLessEqual(max(map(abs, monthly_changes)), 0.13 + 1e-8)

    def test_rest_of_world_imports_have_own_mixed_latitude_cycle(self) -> None:
        world = run_oil_shipping_world(run_global_macro(0, 6))
        turns = world.turns[:72]
        imports: list[float] = []
        own_overlays: list[float] = []
        own_overlays_by_month: dict[int, list[float]] = {
            month: [] for month in range(1, 13)
        }
        own_overlays_by_year: dict[int, list[float]] = {}
        overlay: list[float] = []
        for turn in turns:
            regions = {
                str(region["region_id"]): region
                for region in turn["regional_balances"]
            }
            rest_of_world = regions["rest_of_world"]
            us_gulf = regions["us_gulf"]
            own_overlay = _own_refinery_overlay(regions)
            self.assertLess(float(rest_of_world["net_seaborne_balance_mbd"]), 0.0)
            self.assertAlmostEqual(
                float(rest_of_world["production_policy_adjustment_mbd"]),
                0.0,
                places=8,
            )
            self.assertAlmostEqual(
                float(rest_of_world["production_cycle_adjustment_mbd"]),
                0.0,
                places=8,
            )
            self.assertLessEqual(abs(own_overlay), 0.42 + 1e-8)
            self.assertLessEqual(
                abs(
                    float(rest_of_world["refinery_cycle_target_mbd"])
                    + float(us_gulf["refinery_cycle_target_mbd"])
                ),
                0.42 + 1e-8,
            )
            self.assertAlmostEqual(
                float(turn["regional_crude_runs_residual_mbd"]),
                0.0,
                places=8,
            )
            self.assertAlmostEqual(
                float(turn["regional_refinery_conservation_residual_mbd"]),
                0.0,
                places=6,
            )
            imports.append(-float(rest_of_world["net_seaborne_balance_mbd"]))
            own_overlays.append(own_overlay)
            own_overlays_by_month[int(turn["month"])].append(own_overlay)
            own_overlays_by_year.setdefault(int(turn["year"]), []).append(
                own_overlay
            )
            overlay.append(
                -float(regions["gulf"]["production_policy_adjustment_mbd"])
                - float(regions["us_gulf"]["production_cycle_adjustment_mbd"])
                - float(
                    regions["brazil_guyana"]["production_cycle_adjustment_mbd"]
                )
                - float(regions["west_africa"]["production_cycle_adjustment_mbd"])
            )

        monthly_changes = [
            current - previous
            for previous, current in zip(imports, imports[1:])
        ]
        self.assertGreater(statistics.stdev(monthly_changes), 0.060)
        self.assertLess(statistics.stdev(monthly_changes), 0.18)
        self.assertGreater(max(imports) - min(imports), 0.55)
        self.assertGreater(max(own_overlays) - min(own_overlays), 0.18)
        self.assertLess(abs(statistics.correlation(imports, overlay)), 0.85)
        us_gulf_adjustments = [
            float(
                {
                    str(region["region_id"]): region
                    for region in turn["regional_balances"]
                }["us_gulf"]["refinery_cycle_adjustment_mbd"]
            )
            for turn in turns
        ]
        self.assertLess(
            abs(statistics.correlation(own_overlays, us_gulf_adjustments)),
            0.85,
        )

        maintenance_run_rate = statistics.fmean(
            statistics.fmean(own_overlays_by_month[month])
            for month in (2, 3, 7, 8)
        )
        high_run_rate = statistics.fmean(
            statistics.fmean(own_overlays_by_month[month])
            for month in (5, 11, 12)
        )
        self.assertLess(maintenance_run_rate, high_run_rate - 0.025)

        years = sorted(own_overlays_by_year)
        adjacent_profile_correlations = [
            statistics.correlation(
                own_overlays_by_year[previous],
                own_overlays_by_year[current],
            )
            for previous, current in zip(years, years[1:])
        ]
        self.assertLess(statistics.median(adjacent_profile_correlations), 0.90)


if __name__ == "__main__":
    unittest.main()
