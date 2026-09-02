from __future__ import annotations

import statistics
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_shipping_regions import _advance_east_asia_cycle
from asset_simulation.model.oil_shipping_world import run_oil_shipping_world
from asset_simulation.model.registry import load_registered_assets


class EastAsiaCycleTests(unittest.TestCase):
    def test_refinery_cycle_is_persistent_bounded_and_nontrivial(self) -> None:
        cycle = load_registered_assets()["oil_shipping_demand_config"][
            "regional_oil"
        ]["east_asia_cycle"]
        state = {
            "refinery_operational_deviation_mbd": 0.0,
            "refinery_deviation_mbd": 0.0,
        }
        adjustments: list[float] = []
        targets: list[float] = []
        for turn_index in range(10 * 12):
            state, target = _advance_east_asia_cycle(
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
        self.assertGreater(statistics.stdev(monthly_changes), 0.04)
        self.assertLess(statistics.stdev(monthly_changes), 0.22)
        self.assertGreater(max(adjustments) - min(adjustments), 0.45)
        self.assertGreaterEqual(min(targets), -0.85 - 1e-8)
        self.assertLessEqual(max(targets), 0.50 + 1e-8)
        self.assertLessEqual(max(map(abs, monthly_changes)), 0.22 + 1e-8)

    def test_east_asia_imports_have_own_spring_autumn_refinery_cycle(self) -> None:
        world = run_oil_shipping_world(run_global_macro(0, 6))
        turns = world.turns[:72]
        imports: list[float] = []
        refinery_adjustments: list[float] = []
        refinery_adjustments_by_month: dict[int, list[float]] = {
            month: [] for month in range(1, 13)
        }
        refinery_adjustments_by_year: dict[int, list[float]] = {}
        overlay: list[float] = []
        for turn in turns:
            regions = {
                str(region["region_id"]): region
                for region in turn["regional_balances"]
            }
            east_asia = regions["east_asia"]
            self.assertLess(float(east_asia["net_seaborne_balance_mbd"]), 0.0)
            self.assertAlmostEqual(
                float(east_asia["production_policy_adjustment_mbd"]),
                0.0,
                places=8,
            )
            self.assertAlmostEqual(
                float(east_asia["production_cycle_adjustment_mbd"]),
                0.0,
                places=8,
            )
            self.assertLessEqual(
                abs(
                    float(regions["us_gulf"]["refinery_cycle_adjustment_mbd"])
                    + float(
                        regions["rest_of_world"]["refinery_cycle_adjustment_mbd"]
                    )
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
            self.assertLessEqual(
                abs(float(east_asia["refinery_cycle_target_mbd"])),
                0.85 + 1e-8,
            )
            imports.append(-float(east_asia["net_seaborne_balance_mbd"]))
            refinery_adjustments.append(
                float(east_asia["refinery_cycle_adjustment_mbd"])
            )
            refinery_adjustments_by_month[int(turn["month"])].append(
                refinery_adjustments[-1]
            )
            refinery_adjustments_by_year.setdefault(int(turn["year"]), []).append(
                refinery_adjustments[-1]
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
        self.assertGreater(statistics.stdev(monthly_changes), 0.065)
        self.assertLess(statistics.stdev(monthly_changes), 0.18)
        self.assertGreater(max(imports) - min(imports), 0.40)
        self.assertGreater(
            max(refinery_adjustments) - min(refinery_adjustments),
            0.40,
        )
        self.assertLess(abs(statistics.correlation(imports, overlay)), 0.85)

        maintenance_run_rate = statistics.fmean(
            statistics.fmean(refinery_adjustments_by_month[month])
            for month in (3, 4, 5, 10, 11)
        )
        high_run_rate = statistics.fmean(
            statistics.fmean(refinery_adjustments_by_month[month])
            for month in (6, 7, 8)
        )
        self.assertLess(maintenance_run_rate, high_run_rate - 0.05)

        years = sorted(refinery_adjustments_by_year)
        adjacent_profile_correlations = [
            statistics.correlation(
                refinery_adjustments_by_year[previous],
                refinery_adjustments_by_year[current],
            )
            for previous, current in zip(years, years[1:])
        ]
        self.assertLess(statistics.median(adjacent_profile_correlations), 0.90)


if __name__ == "__main__":
    unittest.main()
