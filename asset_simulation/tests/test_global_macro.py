from __future__ import annotations

import math
import unittest

from asset_simulation.model.contracts import build_minimum_snapshot
from asset_simulation.model.engine import MODEL_VERSION, run_global_macro, run_global_macro_with_impulses
from asset_simulation.model.impulses import IMPULSE_FIELDS
from asset_simulation.model import funding_credit, inflation_nominal, oil_commodity, rates, real_economy
from asset_simulation.model.registry import load_registered_assets


class GlobalMacroContractTests(unittest.TestCase):
    def test_registered_run_and_minimum_contract(self) -> None:
        assets = load_registered_assets()
        run = run_global_macro(seed=42, years=60)
        self.assertEqual(MODEL_VERSION, run.identity["model_version"])
        self.assertEqual(61, len(run.rows))
        self.assertEqual(61, len(run.snapshots))
        self.assertEqual(61, len(run.next_year_inputs))
        self.assertEqual(42, len(run.snapshots[0]))
        self.assertEqual(0, len(run.diagnostics))
        self.assertEqual(assets["field_contract"]["contract_id"], run.identity["field_contract_id"])
        self.assertEqual(run.snapshots[20], build_minimum_snapshot(run.rows[20], assets["field_contract"]))

        for index, (row, snapshot) in enumerate(zip(run.rows, run.snapshots, strict=True)):
            self.assertEqual(index, row["year_index"])
            self.assertEqual(2025 + index, row["year"])
            self.assertEqual(42, row["seed"])
            lagged = run.next_year_inputs[index]
            self.assertEqual("asset-simulation-next-year-inputs-v2", lagged["schema_version"])
            self.assertEqual(index, lagged["source_year_index"])
            self.assertEqual(index + 1, lagged["target_year_index"])
            self.assertEqual(42, lagged["seed"])
            self.assertNotIn("oil_yoy_change_pct", lagged)
            self.assertNotIn("commodity_yoy_change_pct", lagged)
            self.assertIn("real_oil_yoy_change_pct", lagged)
            self.assertIn("real_commodity_yoy_change_pct", lagged)
            for key, value in snapshot.items():
                if index == 0 and key in {
                    "realized_growth_pct", "potential_growth_pct",
                    "global_corporate_earnings_reference_growth_pct",
                    "global_equity_capitalization_reference_growth_pct",
                    "global_equity_total_return_reference_growth_pct",
                    "global_equity_real_total_return_reference_growth_pct",
                    "global_sovereign_bond_wealth_reference_growth_pct",
                    "global_sovereign_bond_real_wealth_reference_growth_pct",
                }:
                    self.assertIsNone(value)
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.assertTrue(math.isfinite(float(value)), key)

    def test_determinism_prefix_and_seed_separation(self) -> None:
        short = run_global_macro(seed=2026, years=12)
        long = run_global_macro(seed=2026, years=60)
        repeat = run_global_macro(seed=2026, years=12)
        other = run_global_macro(seed=2027, years=12)
        self.assertEqual(short.rows, long.rows[:13])
        self.assertEqual(short.next_year_inputs, long.next_year_inputs[:13])
        self.assertEqual(short.rows, repeat.rows)
        self.assertEqual(short.identity["result_hash"], repeat.identity["result_hash"])
        self.assertNotEqual(short.rows, other.rows)
        self.assertNotEqual(short.identity["result_hash"], other.identity["result_hash"])

    def test_accounting_timing_and_single_fci(self) -> None:
        run = run_global_macro(seed=9, years=90, diagnostics_level="full")
        self.assertEqual(90, len(run.diagnostics))
        for previous, row, diagnostics in zip(run.rows[:-1], run.rows[1:], run.diagnostics, strict=True):
            self.assertAlmostEqual(
                row["global_nominal_gdp_trillion_usd"],
                row["global_gdp_trillion_usd"] * row["gdp_deflator_price_level_index_2025_100"] / 100.0,
                places=6,
            )
            self.assertAlmostEqual(
                row["global_10y_yield_pct"] - row["global_2y_yield_pct"],
                row["term_spread_10y_2y_pct"],
                places=7,
            )
            self.assertAlmostEqual(
                row["real_policy_rate_pct"] - row["neutral_real_policy_rate_pct"],
                row["real_policy_gap_pct"],
                places=7,
            )
            expected_brent = (
                69.14
                * row["global_real_oil_price_index"] / 100.0
                * row["cpi_price_level_index_2025_100"] / 100.0
            )
            self.assertAlmostEqual(row["brent_oil_price_usd"], expected_brent, places=5)
            self.assertAlmostEqual(
                row["broad_commodity_index"],
                row["global_real_broad_commodity_index"]
                * row["cpi_price_level_index_2025_100"]
                / 100.0,
                places=7,
            )
            expected_dividend_yield = (
                100.0
                * 0.4
                * row["global_corporate_earnings_reference_index"]
                / (18.0 * previous["global_equity_capitalization_reference_index"])
            )
            self.assertAlmostEqual(
                row["global_equity_dividend_yield_pct"],
                expected_dividend_yield,
                places=7,
            )
            self.assertAlmostEqual(
                row["global_equity_total_return_reference_growth_pct"],
                row["global_equity_capitalization_reference_growth_pct"]
                + row["global_equity_dividend_yield_pct"],
                places=7,
            )
            self.assertAlmostEqual(
                row["global_equity_total_return_reference_index"],
                previous["global_equity_total_return_reference_index"]
                * (1.0 + row["global_equity_total_return_reference_growth_pct"] / 100.0),
                places=5,
            )
            self.assertAlmostEqual(
                row["global_equity_real_total_return_reference_index"],
                row["global_equity_total_return_reference_index"]
                * 100.0
                / row["cpi_price_level_index_2025_100"],
                places=7,
            )
            self.assertAlmostEqual(
                row["global_sovereign_bond_real_wealth_reference_index"],
                row["global_sovereign_bond_wealth_reference_index"]
                * 100.0
                / row["cpi_price_level_index_2025_100"],
                places=7,
            )
            self.assertAlmostEqual(
                row["global_corporate_earnings_reference_index"]
                / previous["global_corporate_earnings_reference_index"],
                (
                    row["global_nominal_gdp_trillion_usd"]
                    / previous["global_nominal_gdp_trillion_usd"]
                )
                * (
                    row["global_corporate_profit_share_index"]
                    / previous["global_corporate_profit_share_index"]
                ),
                places=7,
            )
            self.assertAlmostEqual(
                row["global_equity_real_total_return_reference_growth_pct"],
                (
                    (1.0 + row["global_equity_total_return_reference_growth_pct"] / 100.0)
                    / (1.0 + row["headline_inflation_pct"] / 100.0)
                    - 1.0
                )
                * 100.0,
                places=7,
            )
            self.assertAlmostEqual(
                row["global_sovereign_bond_real_wealth_reference_growth_pct"],
                (
                    (1.0 + row["global_sovereign_bond_wealth_reference_growth_pct"] / 100.0)
                    / (1.0 + row["headline_inflation_pct"] / 100.0)
                    - 1.0
                )
                * 100.0,
                places=7,
            )
            self.assertNotIn("financial_stress_index", row)
            self.assertIn("global_financial_conditions_index", row)
            self.assertEqual(previous["year"] + 1, row["year"])
            self.assertAlmostEqual(
                diagnostics["real_economy"]["gdp_level_identity_residual"], 0.0, places=10
            )
            self.assertAlmostEqual(
                diagnostics["rates"]["yield_10y_identity_residual"], 0.0, places=10
            )

    def test_ordinary_distribution_shape(self) -> None:
        values = {key: [] for key in ("growth", "inflation", "policy", "hy", "fci", "equity", "equity_total", "real_oil")}
        for seed in range(40):
            run = run_global_macro(seed=seed, years=90)
            for row in run.rows[1:]:
                values["growth"].append(row["realized_growth_pct"])
                values["inflation"].append(row["headline_inflation_pct"])
                values["policy"].append(row["global_policy_rate_pct"])
                values["hy"].append(row["global_high_yield_spread_bps"])
                values["fci"].append(row["global_financial_conditions_index"])
                values["equity"].append(row["global_equity_capitalization_reference_growth_pct"])
                values["equity_total"].append(row["global_equity_total_return_reference_growth_pct"])
                values["real_oil"].append(row["real_oil_yoy_change_pct"])
        mean = lambda name: sum(values[name]) / len(values[name])
        std = lambda name: math.sqrt(
            sum((value - mean(name)) ** 2 for value in values[name]) / len(values[name])
        )
        self.assertGreater(mean("growth"), 1.8)
        self.assertLess(mean("growth"), 2.8)
        self.assertGreater(mean("inflation"), 1.6)
        self.assertLess(mean("inflation"), 2.8)
        self.assertGreater(mean("policy"), 2.2)
        self.assertLess(mean("policy"), 4.2)
        self.assertGreater(min(values["growth"]), -1.6)
        self.assertTrue(any(value < 0.0 for value in values["growth"]))
        self.assertTrue(any(value < 0.0 for value in values["equity"]))
        self.assertTrue(any(value < 0.0 for value in values["equity_total"]))
        self.assertGreater(max(values["inflation"]), 4.0)
        self.assertLess(max(values["inflation"]), 6.5)
        self.assertGreater(min(values["inflation"]), -1.0)
        self.assertGreater(min(values["equity"]), -12.0)
        self.assertLess(max(values["equity"]), 16.0)
        self.assertTrue(all(220.0 <= value <= 1100.0 for value in values["hy"]))
        self.assertTrue(all(-4.0 <= value <= 4.0 for value in values["fci"]))
        self.assertGreater(std("real_oil"), 7.0)
        self.assertLess(std("real_oil"), 16.0)
        oil_tail_share = sum(abs(value) > 20.0 for value in values["real_oil"]) / len(values["real_oil"])
        self.assertGreater(oil_tail_share, 0.08)
        self.assertLess(oil_tail_share, 0.20)

        oil_run = run_global_macro(seed=42, years=60)
        volatility_regimes = [row["global_oil_volatility_regime_index"] for row in oil_run.rows]
        return_momentum = [row["global_oil_return_momentum_pct"] for row in oil_run.rows]
        self.assertTrue(all(0.75 <= value <= 1.40 for value in volatility_regimes))
        self.assertTrue(all(-24.0 <= value <= 28.0 for value in return_momentum))
        self.assertGreater(max(volatility_regimes) - min(volatility_regimes), 0.15)
        self.assertGreater(max(return_momentum) - min(return_momentum), 12.0)
        self.assertTrue(
            all(
                left["global_real_oil_price_index"] != right["global_real_oil_price_index"]
                for left, right in zip(oil_run.rows, oil_run.rows[1:])
            )
        )

    def test_ordinary_cycle_phases_curve_and_zero_event_ports(self) -> None:
        phases: set[str] = set()
        inverted_years = 0
        recession_seeds = 0
        for seed in range(40):
            run = run_global_macro(seed=seed, years=90, diagnostics_level="full")
            phases.update(row["ordinary_cycle_phase"] for row in run.rows)
            inverted_years += sum(row["term_spread_10y_2y_pct"] < 0.0 for row in run.rows[1:])
            recession_seeds += any(row["realized_growth_pct"] < 0.0 for row in run.rows[1:])
            for diagnostics in run.diagnostics:
                impulse = diagnostics["exogenous_impulses"]
                self.assertTrue(all(impulse[field] == 0.0 for field in IMPULSE_FIELDS))
        self.assertTrue({"recovery", "expansion", "late_cycle", "contraction"}.issubset(phases))
        self.assertGreater(inverted_years, 0)
        self.assertGreater(recession_seeds, 0)

    def test_exogenous_impulses_are_explicit_typed_and_directional(self) -> None:
        baseline = run_global_macro(seed=77, years=8, diagnostics_level="full")
        repeat = run_global_macro_with_impulses(
            77,
            8,
            {},
            diagnostics_level="full",
        )
        self.assertEqual(baseline.rows, repeat.rows)
        self.assertEqual(baseline.next_year_inputs, repeat.next_year_inputs)

        shocked = run_global_macro_with_impulses(
            77,
            8,
            {
                3: {
                    "demand_growth_impulse_pp": -1.5,
                    "inflation_impulse_pp": 0.8,
                    "dollar_funding_impulse_index": 4.0,
                    "credit_spread_impulse_bps": 75.0,
                    "oil_supply_growth_impulse_pp": -2.0,
                }
            },
            diagnostics_level="full",
        )
        self.assertLess(shocked.rows[3]["realized_growth_pct"], baseline.rows[3]["realized_growth_pct"])
        self.assertGreater(shocked.rows[3]["headline_inflation_pct"], baseline.rows[3]["headline_inflation_pct"])
        self.assertGreater(shocked.rows[3]["dollar_funding_stress_index"], baseline.rows[3]["dollar_funding_stress_index"])
        self.assertGreater(shocked.rows[3]["global_high_yield_spread_bps"], baseline.rows[3]["global_high_yield_spread_bps"])
        self.assertLess(shocked.rows[3]["global_oil_supply_index"], baseline.rows[3]["global_oil_supply_index"])
        self.assertEqual(3, shocked.diagnostics[2]["exogenous_impulses"]["target_year_index"])

        with self.assertRaises(KeyError):
            run_global_macro_with_impulses(77, 8, {3: {"unknown_impulse": 1.0}})
        with self.assertRaises(ValueError):
            run_global_macro_with_impulses(77, 8, {9: {"demand_growth_impulse_pp": -1.0}})

    def test_directional_transmission(self) -> None:
        config = load_registered_assets()["config"]

        real_state = real_economy.initial_state(config)
        normal_previous = {
            "real_policy_rate_pct": 1.0,
            "neutral_real_policy_rate_pct": 1.0,
            "global_high_yield_spread_bps": 420.0,
            "global_funding_liquidity_index": 55.0,
            "energy_cost_pressure_index": 50.0,
        }
        tight_previous = {
            **normal_previous,
            "real_policy_rate_pct": 2.5,
            "global_high_yield_spread_bps": 720.0,
            "global_funding_liquidity_index": 40.0,
            "energy_cost_pressure_index": 70.0,
        }
        _, normal_growth = real_economy.step(
            real_state, normal_previous, seed=77, year_index=1, config=config
        )
        _, tight_growth = real_economy.step(
            real_state, tight_previous, seed=77, year_index=1, config=config
        )
        self.assertLess(tight_growth["realized_growth_pct"], normal_growth["realized_growth_pct"])

        inflation_state = inflation_nominal.initial_state(config)
        base_prices = {
            "dollar_yoy_change_pct": 0.0,
            "real_commodity_yoy_change_pct": 0.0,
            "real_oil_yoy_change_pct": 0.0,
        }
        high_prices = {
            **base_prices,
            "real_commodity_yoy_change_pct": 12.0,
            "real_oil_yoy_change_pct": 25.0,
        }
        real = {"real_gdp_trillion_usd": 110.0, "output_gap_pct": 0.0}
        base_inflation, _ = inflation_nominal.step(
            inflation_state, real, base_prices, seed=77, year_index=1, config=config
        )
        high_inflation, _ = inflation_nominal.step(
            inflation_state, real, high_prices, seed=77, year_index=1, config=config
        )
        self.assertGreater(high_inflation["headline_inflation_pct"], base_inflation["headline_inflation_pct"])

        rate_state = rates.initial_state(config)
        rate_real = {"output_gap_pct": 0.0}
        calm_inflation = {"headline_inflation_pct": 2.2, "core_inflation_pct": 2.2, "inflation_expectation_pct": 2.2}
        hot_inflation = {"headline_inflation_pct": 4.0, "core_inflation_pct": 3.8, "inflation_expectation_pct": 3.0}
        lagged = {"global_financial_conditions_index": 0.0}
        _, calm_rates = rates.step(
            rate_state, rate_real, calm_inflation, lagged, seed=77, year_index=1, config=config
        )
        hot_rate_state, hot_rates = rates.step(
            rate_state, rate_real, hot_inflation, lagged, seed=77, year_index=1, config=config
        )
        self.assertGreater(hot_rates["global_policy_rate_pct"], calm_rates["global_policy_rate_pct"])
        self.assertGreater(hot_rates["global_10y_yield_pct"], calm_rates["global_10y_yield_pct"])

        funding_state = funding_credit.initial_state(config)
        funding_real = {"realized_growth_pct": 2.3, "potential_growth_pct": 2.3, "output_gap_pct": 0.0}
        calm_rate_input = {
            "real_policy_rate_pct": 1.0,
            "neutral_real_policy_rate_pct": 1.0,
            "global_real_10y_yield_pct": 1.0,
        }
        tight_rate_input = {**calm_rate_input, "real_policy_rate_pct": 3.0, "global_real_10y_yield_pct": 2.5}
        calm_funding, _ = funding_credit.step(
            funding_state, funding_real, calm_inflation, calm_rate_input,
            seed=77, year_index=1, config=config
        )
        tight_funding, _ = funding_credit.step(
            funding_state, funding_real, calm_inflation, tight_rate_input,
            seed=77, year_index=1, config=config
        )
        self.assertGreater(tight_funding["dollar_funding_stress_index"], calm_funding["dollar_funding_stress_index"])
        self.assertLess(tight_funding["funding_liquidity_index"], calm_funding["funding_liquidity_index"])
        self.assertGreater(tight_funding["financial_conditions_index"], calm_funding["financial_conditions_index"])

        oil_state = oil_commodity.initial_state(config)
        oil_real = {"realized_growth_pct": 2.3, "potential_growth_pct": 2.3, "output_gap_pct": 0.0}
        oil_funding = {"dollar_yoy_change_pct": 0.0}
        stable_cpi = {
            "cpi_price_level_index": 100.0,
            "previous_cpi_price_level_index": 100.0,
        }
        higher_cpi = {
            "cpi_price_level_index": 110.0,
            "previous_cpi_price_level_index": 100.0,
        }
        stable_oil, stable_diag = oil_commodity.step(
            oil_state, oil_real, stable_cpi, oil_funding, seed=77, year_index=1, config=config
        )
        inflated_oil, inflated_diag = oil_commodity.step(
            oil_state, oil_real, higher_cpi, oil_funding, seed=77, year_index=1, config=config
        )
        self.assertAlmostEqual(
            stable_oil["energy_cost_pressure_index"],
            inflated_oil["energy_cost_pressure_index"],
        )
        self.assertAlmostEqual(stable_oil["real_oil_price_index"], inflated_oil["real_oil_price_index"])
        self.assertGreater(inflated_diag["brent_oil_price_usd"], stable_diag["brent_oil_price_usd"])


if __name__ == "__main__":
    unittest.main()
