"""Compact global macro orchestrator for the asset simulation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from . import asset_reference, funding_credit, inflation_nominal, oil_commodity, rates, real_economy
from .contracts import build_minimum_snapshot
from .impulses import ExogenousImpulseBundle, build_impulse_bundle, zero_impulse_bundle
from .math_utils import round_record
from .random_stream import RANDOM_STREAM_VERSION
from .registry import load_registered_assets, sha256_json


MODEL_VERSION = "asset-simulation-global-macro-v0.8.1"
STACK_VERSION = "asset-simulation-global-stack-v8.1"


@dataclass(frozen=True)
class GlobalMacroRun:
    seed: int
    start_year: int
    years: int
    diagnostics_level: str
    rows: tuple[dict[str, Any], ...]
    snapshots: tuple[dict[str, Any], ...]
    next_year_inputs: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, Any], ...]
    identity: dict[str, Any]
    summary: dict[str, Any]


def _next_year_inputs(row: Mapping[str, Any]) -> dict[str, Any]:
    """Publish the only lagged macro inputs consumed by the next transition."""

    return round_record(
        {
            "schema_version": "asset-simulation-next-year-inputs-v2",
            "seed": int(row["seed"]),
            "source_year_index": int(row["year_index"]),
            "source_year": int(row["year"]),
            "target_year_index": int(row["year_index"]) + 1,
            "target_year": int(row["year"]) + 1,
            "rates_owner": "rates",
            "funding_credit_owner": "funding_credit",
            "oil_commodity_owner": "oil_commodity",
            "real_policy_rate_pct": row["real_policy_rate_pct"],
            "neutral_real_policy_rate_pct": row["neutral_real_policy_rate_pct"],
            "global_high_yield_spread_bps": row["global_high_yield_spread_bps"],
            "global_funding_liquidity_index": row["global_funding_liquidity_index"],
            "energy_cost_pressure_index": row["energy_cost_pressure_index"],
            "global_financial_conditions_index": row["global_financial_conditions_index"],
            "dollar_yoy_change_pct": row["dollar_yoy_change_pct"],
            "real_commodity_yoy_change_pct": row["real_commodity_yoy_change_pct"],
            "real_oil_yoy_change_pct": row["real_oil_yoy_change_pct"],
        }
    )


def _initial_row(config: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, float]]]:
    real = real_economy.initial_state(config)
    inflation = inflation_nominal.initial_state(config)
    rate_state = rates.initial_state(config)
    funding = funding_credit.initial_state(config)
    oil = oil_commodity.initial_state(config)
    asset = asset_reference.initial_state(config)
    expected_2y = rate_state["expected_short_rate_2y_pct"]
    expected_10y = rate_state["expected_short_rate_10y_pct"]
    term_premium = rate_state["term_premium_10y_pct"]
    yield_2y = expected_2y + 0.22 * term_premium
    yield_10y = expected_10y + term_premium
    real_policy = rate_state["policy_rate_pct"] - inflation["inflation_expectation_pct"]
    real_10y = yield_10y - inflation["inflation_expectation_pct"]
    nominal_gdp = real["real_gdp_trillion_usd"] * inflation["gdp_deflator_price_level_index"] / 100.0
    row = {
        "global_gdp_trillion_usd": real["real_gdp_trillion_usd"],
        "realized_growth_pct": None,
        "potential_growth_pct": None,
        "output_gap_pct": real["output_gap_pct"],
        "ordinary_cycle_index": real["ordinary_cycle_index"],
        "ordinary_cycle_momentum_index": real["ordinary_cycle_momentum_index"],
        "ordinary_cycle_phase": real_economy.cycle_phase(
            real["ordinary_cycle_index"], real["ordinary_cycle_momentum_index"]
        ),
        "headline_inflation_pct": inflation["headline_inflation_pct"],
        "core_inflation_pct": inflation["core_inflation_pct"],
        "inflation_expectation_pct": inflation["inflation_expectation_pct"],
        "inflation_supply_pressure_index": inflation["inflation_supply_pressure_index"],
        "global_policy_rate_pct": rate_state["policy_rate_pct"],
        "real_policy_rate_pct": real_policy,
        "neutral_real_policy_rate_pct": rate_state["neutral_real_policy_rate_pct"],
        "global_2y_yield_pct": yield_2y,
        "global_10y_yield_pct": yield_10y,
        "term_premium_10y_pct": term_premium,
        "global_real_10y_yield_pct": real_10y,
        "global_dollar_index": funding["dollar_index"],
        "dollar_yoy_change_pct": 0.0,
        "dollar_funding_stress_index": funding["dollar_funding_stress_index"],
        "global_funding_liquidity_index": funding["funding_liquidity_index"],
        "global_investment_grade_spread_bps": funding["ig_spread_bps"],
        "global_high_yield_spread_bps": funding["hy_spread_bps"],
        "credit_availability_index": funding["credit_availability_index"],
        "default_risk_index": funding["default_risk_index"],
        "brent_oil_price_usd": float(config["anchors"]["brent_oil_price_usd"]),
        "oil_yoy_change_pct": 0.0,
        "real_oil_yoy_change_pct": 0.0,
        "commodity_yoy_change_pct": 0.0,
        "real_commodity_yoy_change_pct": 0.0,
        "energy_cost_pressure_index": oil["energy_cost_pressure_index"],
        "global_financial_conditions_index": funding["financial_conditions_index"],
        "risk_appetite_index": 50.0,
        "global_corporate_earnings_reference_growth_pct": None,
        "global_equity_valuation_center_pe": asset["equity_valuation_pe"],
        "global_equity_risk_premium_center_pct": asset["equity_risk_premium_pct"],
        "global_equity_capitalization_reference_growth_pct": None,
        "global_equity_dividend_yield_pct": (
            100.0
            * float(config["anchors"]["equity_payout_ratio"])
            / asset["equity_valuation_pe"]
        ),
        "global_equity_total_return_reference_growth_pct": None,
        "global_equity_real_total_return_reference_growth_pct": None,
        "global_sovereign_bond_wealth_reference_growth_pct": None,
        "global_sovereign_bond_real_wealth_reference_growth_pct": None,
        "cpi_price_level_index_2025_100": inflation["cpi_price_level_index"],
        "gdp_deflator_price_level_index_2025_100": inflation["gdp_deflator_price_level_index"],
        "global_nominal_gdp_trillion_usd": nominal_gdp,
        "term_spread_10y_2y_pct": yield_10y - yield_2y,
        "real_policy_gap_pct": real_policy - rate_state["neutral_real_policy_rate_pct"],
        "global_oil_demand_index": oil["oil_demand_index"],
        "global_oil_supply_index": oil["oil_supply_index"],
        "global_oil_inventory_tightness_index": oil["inventory_tightness_index"],
        "global_oil_return_momentum_pct": oil["return_momentum_pct"],
        "global_oil_volatility_regime_index": oil["volatility_regime_index"],
        "global_real_oil_price_index": oil["real_oil_price_index"],
        "global_real_broad_commodity_index": oil["real_commodity_price_index"],
        "broad_commodity_index": (
            oil["real_commodity_price_index"]
            * inflation["cpi_price_level_index"]
            / 100.0
        ),
        "global_corporate_earnings_reference_index": asset["earnings_index"],
        "global_corporate_profit_share_index": asset["corporate_profit_share_index"],
        "global_equity_capitalization_reference_index": asset["equity_capitalization_index"],
        "global_equity_real_capitalization_reference_index": (
            asset["equity_capitalization_index"]
            * 100.0
            / inflation["cpi_price_level_index"]
        ),
        "global_equity_total_return_reference_index": asset["equity_total_return_index"],
        "global_equity_real_total_return_reference_index": (
            asset["equity_total_return_index"]
            * 100.0
            / inflation["cpi_price_level_index"]
        ),
        "global_sovereign_bond_wealth_reference_index": asset["sovereign_bond_wealth_index"],
        "global_sovereign_bond_real_wealth_reference_index": (
            asset["sovereign_bond_wealth_index"]
            * 100.0
            / inflation["cpi_price_level_index"]
        ),
    }
    return row, {
        "real": real,
        "inflation": inflation,
        "rates": rate_state,
        "funding": funding,
        "oil": oil,
        "asset": asset,
    }


def _transition(
    states: dict[str, dict[str, float]],
    previous: Mapping[str, Any],
    lagged_inputs: Mapping[str, Any],
    *,
    seed: int,
    year_index: int,
    config: Mapping[str, Any],
    impulses: ExogenousImpulseBundle,
) -> tuple[dict[str, Any], dict[str, dict[str, float]]]:
    impulse_values = impulses.values()
    real, real_diag = real_economy.step(
        states["real"], lagged_inputs, seed=seed, year_index=year_index, config=config,
        impulses=impulse_values,
    )
    inflation, inflation_diag = inflation_nominal.step(
        states["inflation"], real, lagged_inputs, seed=seed, year_index=year_index, config=config,
        impulses=impulse_values,
    )
    rate_state, rate_diag = rates.step(
        states["rates"], real, inflation, lagged_inputs, seed=seed, year_index=year_index, config=config
    )
    rate_public = {
        **rate_state,
        **rate_diag,
        "neutral_real_policy_rate_pct": rate_state["neutral_real_policy_rate_pct"],
    }
    real_public = {
        **real,
        "realized_growth_pct": real_diag["realized_growth_pct"],
    }
    funding, funding_diag = funding_credit.step(
        states["funding"], real_public, inflation, rate_public,
        seed=seed, year_index=year_index, config=config, impulses=impulse_values,
    )
    funding_public = {
        **funding,
        **funding_diag,
        "global_dollar_index": funding["dollar_index"],
        "global_funding_liquidity_index": funding["funding_liquidity_index"],
        "global_investment_grade_spread_bps": funding["ig_spread_bps"],
        "global_high_yield_spread_bps": funding["hy_spread_bps"],
        "global_financial_conditions_index": funding["financial_conditions_index"],
    }
    inflation_for_oil = {
        **inflation,
        "previous_cpi_price_level_index": states["inflation"]["cpi_price_level_index"],
    }
    oil, oil_diag = oil_commodity.step(
        states["oil"], real_public, inflation_for_oil, funding_public,
        seed=seed, year_index=year_index, config=config, impulses=impulse_values,
    )
    macro_for_asset = {
        **real_public,
        **inflation,
        **rate_public,
        **funding_public,
        **oil,
        **oil_diag,
        "energy_cost_pressure_index": oil["energy_cost_pressure_index"],
        "previous_global_10y_yield_pct": float(previous["global_10y_yield_pct"]),
        "global_nominal_gdp_trillion_usd": inflation_diag["global_nominal_gdp_trillion_usd"],
        "previous_global_nominal_gdp_trillion_usd": float(
            previous["global_nominal_gdp_trillion_usd"]
        ),
    }
    asset, asset_diag = asset_reference.step(
        states["asset"], macro_for_asset, seed=seed, year_index=year_index, config=config
    )
    row = {
        "global_gdp_trillion_usd": real["real_gdp_trillion_usd"],
        "realized_growth_pct": real_diag["realized_growth_pct"],
        "potential_growth_pct": real["potential_growth_pct"],
        "output_gap_pct": real["output_gap_pct"],
        "ordinary_cycle_index": real["ordinary_cycle_index"],
        "ordinary_cycle_momentum_index": real["ordinary_cycle_momentum_index"],
        "ordinary_cycle_phase": real_diag["ordinary_cycle_phase"],
        "headline_inflation_pct": inflation["headline_inflation_pct"],
        "core_inflation_pct": inflation["core_inflation_pct"],
        "inflation_expectation_pct": inflation["inflation_expectation_pct"],
        "inflation_supply_pressure_index": inflation["inflation_supply_pressure_index"],
        "global_policy_rate_pct": rate_state["policy_rate_pct"],
        "real_policy_rate_pct": rate_diag["real_policy_rate_pct"],
        "neutral_real_policy_rate_pct": rate_state["neutral_real_policy_rate_pct"],
        "global_2y_yield_pct": rate_diag["global_2y_yield_pct"],
        "global_10y_yield_pct": rate_diag["global_10y_yield_pct"],
        "term_premium_10y_pct": rate_state["term_premium_10y_pct"],
        "global_real_10y_yield_pct": rate_diag["global_real_10y_yield_pct"],
        "global_dollar_index": funding["dollar_index"],
        "dollar_yoy_change_pct": funding_diag["dollar_yoy_change_pct"],
        "dollar_funding_stress_index": funding["dollar_funding_stress_index"],
        "global_funding_liquidity_index": funding["funding_liquidity_index"],
        "global_investment_grade_spread_bps": funding["ig_spread_bps"],
        "global_high_yield_spread_bps": funding["hy_spread_bps"],
        "credit_availability_index": funding["credit_availability_index"],
        "default_risk_index": funding["default_risk_index"],
        "brent_oil_price_usd": oil_diag["brent_oil_price_usd"],
        "oil_yoy_change_pct": oil_diag["oil_yoy_change_pct"],
        "real_oil_yoy_change_pct": oil_diag["real_oil_yoy_change_pct"],
        "commodity_yoy_change_pct": oil_diag["commodity_yoy_change_pct"],
        "real_commodity_yoy_change_pct": oil_diag["real_commodity_yoy_change_pct"],
        "energy_cost_pressure_index": oil["energy_cost_pressure_index"],
        "global_financial_conditions_index": funding["financial_conditions_index"],
        "risk_appetite_index": asset_diag["risk_appetite_index"],
        "global_corporate_earnings_reference_growth_pct": asset_diag["earnings_growth_pct"],
        "global_equity_valuation_center_pe": asset["equity_valuation_pe"],
        "global_equity_risk_premium_center_pct": asset["equity_risk_premium_pct"],
        "global_equity_capitalization_reference_growth_pct": asset_diag["capitalization_growth_pct"],
        "global_equity_dividend_yield_pct": asset_diag["equity_dividend_yield_pct"],
        "global_equity_total_return_reference_growth_pct": asset_diag["equity_total_return_growth_pct"],
        "global_equity_real_total_return_reference_growth_pct": (
            (1.0 + asset_diag["equity_total_return_growth_pct"] / 100.0)
            / (1.0 + inflation["headline_inflation_pct"] / 100.0)
            - 1.0
        ) * 100.0,
        "global_sovereign_bond_wealth_reference_growth_pct": asset_diag["bond_wealth_growth_pct"],
        "global_sovereign_bond_real_wealth_reference_growth_pct": (
            (1.0 + asset_diag["bond_wealth_growth_pct"] / 100.0)
            / (1.0 + inflation["headline_inflation_pct"] / 100.0)
            - 1.0
        ) * 100.0,
        "cpi_price_level_index_2025_100": inflation["cpi_price_level_index"],
        "gdp_deflator_price_level_index_2025_100": inflation["gdp_deflator_price_level_index"],
        "global_nominal_gdp_trillion_usd": inflation_diag["global_nominal_gdp_trillion_usd"],
        "term_spread_10y_2y_pct": rate_diag["term_spread_10y_2y_pct"],
        "real_policy_gap_pct": rate_diag["real_policy_rate_pct"] - rate_state["neutral_real_policy_rate_pct"],
        "global_oil_demand_index": oil["oil_demand_index"],
        "global_oil_supply_index": oil["oil_supply_index"],
        "global_oil_inventory_tightness_index": oil["inventory_tightness_index"],
        "global_oil_return_momentum_pct": oil["return_momentum_pct"],
        "global_oil_volatility_regime_index": oil["volatility_regime_index"],
        "global_real_oil_price_index": oil["real_oil_price_index"],
        "global_real_broad_commodity_index": oil["real_commodity_price_index"],
        "broad_commodity_index": oil_diag["broad_commodity_index"],
        "global_corporate_earnings_reference_index": asset["earnings_index"],
        "global_corporate_profit_share_index": asset["corporate_profit_share_index"],
        "global_equity_capitalization_reference_index": asset["equity_capitalization_index"],
        "global_equity_real_capitalization_reference_index": (
            asset["equity_capitalization_index"]
            * 100.0
            / inflation["cpi_price_level_index"]
        ),
        "global_equity_total_return_reference_index": asset["equity_total_return_index"],
        "global_equity_real_total_return_reference_index": (
            asset["equity_total_return_index"]
            * 100.0
            / inflation["cpi_price_level_index"]
        ),
        "global_sovereign_bond_wealth_reference_index": asset["sovereign_bond_wealth_index"],
        "global_sovereign_bond_real_wealth_reference_index": (
            asset["sovereign_bond_wealth_index"]
            * 100.0
            / inflation["cpi_price_level_index"]
        ),
    }
    diagnostics = {
        "real_economy": real_diag,
        "inflation_nominal": inflation_diag,
        "rates": rate_diag,
        "funding_credit": funding_diag,
        "oil_commodity": oil_diag,
        "asset_reference": asset_diag,
        "exogenous_impulses": impulses.as_record(),
    }
    return row, {
        "real": real,
        "inflation": inflation,
        "rates": rate_state,
        "funding": funding,
        "oil": oil,
        "asset": asset,
        "diagnostics": diagnostics,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    transitions = rows[1:]
    return {
        "row_count": len(rows),
        "average_real_growth_pct": round(
            sum(float(row["realized_growth_pct"]) for row in transitions) / len(transitions), 4
        ),
        "average_headline_inflation_pct": round(
            sum(float(row["headline_inflation_pct"]) for row in transitions) / len(transitions), 4
        ),
        "average_policy_rate_pct": round(
            sum(float(row["global_policy_rate_pct"]) for row in transitions) / len(transitions), 4
        ),
        "minimum_real_growth_pct": round(min(float(row["realized_growth_pct"]) for row in transitions), 4),
        "maximum_real_growth_pct": round(max(float(row["realized_growth_pct"]) for row in transitions), 4),
        "minimum_headline_inflation_pct": round(min(float(row["headline_inflation_pct"]) for row in transitions), 4),
        "maximum_headline_inflation_pct": round(max(float(row["headline_inflation_pct"]) for row in transitions), 4),
        "recession_years": sum(float(row["realized_growth_pct"]) < 0.0 for row in transitions),
        "low_growth_years": sum(float(row["realized_growth_pct"]) < 1.0 for row in transitions),
        "inverted_curve_years": sum(float(row["term_spread_10y_2y_pct"]) < 0.0 for row in transitions),
        "high_yield_tight_years": sum(float(row["global_high_yield_spread_bps"]) > 700.0 for row in transitions),
        "minimum_brent_oil_price_usd": round(min(float(row["brent_oil_price_usd"]) for row in rows), 4),
        "maximum_brent_oil_price_usd": round(max(float(row["brent_oil_price_usd"]) for row in rows), 4),
    }


def run_global_macro(
    seed: int = 42,
    years: int = 60,
    *,
    diagnostics_level: str = "minimal",
) -> GlobalMacroRun:
    """Run the ordinary deterministic world; named events are deliberately absent."""

    return _run_global_macro(
        seed=seed,
        years=years,
        diagnostics_level=diagnostics_level,
        exogenous_impulses=None,
    )


def run_global_macro_with_impulses(
    seed: int,
    years: int,
    exogenous_impulses: Mapping[int, Mapping[str, Any] | ExogenousImpulseBundle],
    *,
    diagnostics_level: str = "minimal",
) -> GlobalMacroRun:
    """Explicit development hook for future event layers; not used by the service."""

    return _run_global_macro(
        seed=seed,
        years=years,
        diagnostics_level=diagnostics_level,
        exogenous_impulses=exogenous_impulses,
    )


def _run_global_macro(
    *,
    seed: int,
    years: int,
    diagnostics_level: str,
    exogenous_impulses: Mapping[int, Mapping[str, Any] | ExogenousImpulseBundle] | None,
) -> GlobalMacroRun:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    assets = load_registered_assets()
    config = assets["config"]
    if config["model_version"] != MODEL_VERSION:
        raise ValueError("registered config model version mismatch")
    if isinstance(years, bool) or not isinstance(years, int):
        raise ValueError("years must be an integer")
    if not int(config["minimum_years"]) <= years <= int(config["maximum_years"]):
        raise ValueError(
            f"years must be between {config['minimum_years']} and {config['maximum_years']}"
        )
    if diagnostics_level not in {"minimal", "full"}:
        raise ValueError("diagnostics_level must be minimal or full")

    start_year = int(config["start_year"])
    initial, states = _initial_row(config)
    rows: list[dict[str, Any]] = [
        round_record({"seed": seed, "year_index": 0, "year": start_year, **initial})
    ]
    next_year_inputs: list[dict[str, Any]] = [_next_year_inputs(rows[0])]
    diagnostics: list[dict[str, Any]] = []
    impulse_schedule = {} if exogenous_impulses is None else dict(exogenous_impulses)
    invalid_years = set(impulse_schedule) - set(range(1, years + 1))
    if invalid_years:
        raise ValueError(f"exogenous impulse target year_index is out of range: {sorted(invalid_years)}")
    for year_index in range(1, years + 1):
        lagged_inputs = next_year_inputs[-1]
        if (
            int(lagged_inputs["seed"]) != seed
            or int(lagged_inputs["source_year_index"]) != year_index - 1
            or int(lagged_inputs["target_year_index"]) != year_index
        ):
            raise ValueError("next-year input timing or seed identity mismatch")
        impulses = (
            zero_impulse_bundle(
                seed=seed,
                target_year_index=year_index,
                target_year=start_year + year_index,
            )
            if year_index not in impulse_schedule
            else build_impulse_bundle(
                seed=seed,
                target_year_index=year_index,
                target_year=start_year + year_index,
                values=impulse_schedule[year_index],
            )
        )
        row, result = _transition(
            states, rows[-1], lagged_inputs,
            seed=seed, year_index=year_index, config=config, impulses=impulses,
        )
        states = {key: result[key] for key in ("real", "inflation", "rates", "funding", "oil", "asset")}
        rows.append(
            round_record(
                {"seed": seed, "year_index": year_index, "year": start_year + year_index, **row}
            )
        )
        next_year_inputs.append(_next_year_inputs(rows[-1]))
        if diagnostics_level == "full":
            diagnostics.append(
                {
                    "seed": seed,
                    "year_index": year_index,
                    "year": start_year + year_index,
                    **result["diagnostics"],
                }
            )

    snapshots = [build_minimum_snapshot(row, assets["field_contract"]) for row in rows]
    result_hash = sha256_json(rows)
    next_year_inputs_hash = sha256_json(next_year_inputs)
    identity = {
        "schema_version": "asset-simulation-run-identity-v1",
        "model_version": MODEL_VERSION,
        "stack_version": STACK_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["config_hash"],
        "field_contract_id": assets["field_contract"]["contract_id"],
        "field_contract_hash": assets["field_contract_hash"],
        "random_stream_version": RANDOM_STREAM_VERSION,
        "exogenous_impulse_schema_version": "asset-simulation-exogenous-impulses-v1",
        "exogenous_impulses_hash": sha256_json(
            {
                str(index): build_impulse_bundle(
                    seed=seed,
                    target_year_index=index,
                    target_year=start_year + index,
                    values=value,
                ).as_record()
                for index, value in sorted(impulse_schedule.items())
            }
        ),
        "seed": seed,
        "start_year": start_year,
        "years": years,
        "diagnostics_level": diagnostics_level,
        "result_hash": result_hash,
        "next_year_inputs_hash": next_year_inputs_hash,
    }
    identity["identity_hash"] = sha256_json(identity)
    return GlobalMacroRun(
        seed=seed,
        start_year=start_year,
        years=years,
        diagnostics_level=diagnostics_level,
        rows=tuple(rows),
        snapshots=tuple(snapshots),
        next_year_inputs=tuple(next_year_inputs),
        diagnostics=tuple(diagnostics),
        identity=identity,
        summary=_summary(rows),
    )
