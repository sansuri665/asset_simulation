from __future__ import annotations

from copy import deepcopy
import json
import math
import statistics
import unittest

from asset_simulation.model.investment_decision import (
    build_company_risk_appetite,
    build_strategy_capital_mandate,
    build_strategy_charter,
    build_strategy_position_mandate,
)
from asset_simulation.model.oil_short_horizon_risk import (
    build_default_oil_short_horizon_risk_profile,
    build_oil_short_horizon_risk_review,
    resolve_oil_short_horizon_risk_profile,
)
from asset_simulation.tests.test_oil_short_horizon_risk import _market


COMPANY_EQUITY_USD = 100_000_000.0
CONTRACT_ID = "OIL-3005"


def _mandate(*, pct: float, target_lots: int) -> tuple[dict, float]:
    charter = build_strategy_charter(
        asset="oil",
        horizon="short_horizon",
        strategy_type="directional",
        strategy_id=f"quant-audit-directional-{pct:g}-{target_lots}",
    )
    capital = build_strategy_capital_mandate(
        charter,
        company_equity_usd=COMPANY_EQUITY_USD,
        authorized_pct_of_company_equity=pct,
    )
    position = build_strategy_position_mandate(
        charter,
        capital,
        {CONTRACT_ID: target_lots},
    )
    return position, float(capital["authorized_capital_usd"])


def _capability_profile(level: float, sample_index: int) -> dict:
    base = build_default_oil_short_horizon_risk_profile()
    candidate = deepcopy(base)
    candidate.pop("profile_hash")
    candidate["appointment"] = {
        **candidate["appointment"],
        "personnel_id": f"quant_capability_{level:g}_{sample_index}",
        "display_name": f"Capability {level:g} / {sample_index}",
        "source": "quantitative_audit",
        "candidate_index": sample_index,
        "generation_seed": 900_000 + sample_index,
    }
    candidate["style_radar"] = {key: 50.0 for key in candidate["style_radar"]}
    candidate["capability_radar"] = {
        key: float(level) for key in candidate["capability_radar"]
    }
    return resolve_oil_short_horizon_risk_profile(candidate)


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * q
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return ordered[low]
    mix = index - low
    return ordered[low] * (1.0 - mix) + ordered[high] * mix


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "median": statistics.median(values),
        "p90": _percentile(values, 0.90),
        "max": max(values),
    }


class OilShortHorizonRiskQuantitativeAuditTests(unittest.TestCase):
    def test_quantify_capability_and_position_materiality(self) -> None:
        market = _market()
        appetite = build_company_risk_appetite()

        # Moderate constant risk intensity: the target scales with allocated capital.
        # This isolates company materiality from strategy-relative risk intensity.
        position_rows: list[dict[str, object]] = []
        for pct in (1.0, 5.0, 10.0, 25.0, 35.0, 40.0, 50.0):
            target_lots = int(round(8.0 * pct))
            mandate, allocated = _mandate(pct=pct, target_lots=target_lots)
            review = build_oil_short_horizon_risk_review(
                market,
                mandate,
                company_equity_usd=COMPANY_EQUITY_USD,
                allocated_strategy_capital_usd=allocated,
                company_risk_appetite=appetite,
            )
            materiality = review["materialityBeforePortfolioScale"]
            approved = int(review["riskApprovedTargets"][CONTRACT_ID])
            position_rows.append(
                {
                    "allocation_pct": pct,
                    "target_lots": target_lots,
                    "approved_lots": approved,
                    "approval_ratio": 0.0 if target_lots == 0 else approved / target_lots,
                    "stress_pct_of_allocated_capital": float(
                        materiality["stress_loss_pct_of_allocated_strategy_capital"]
                    ),
                    "stress_pct_of_company_equity": float(
                        materiality["stress_loss_pct_of_company_equity"]
                    ),
                    "margin_pct_of_allocated_capital": float(
                        materiality["margin_pct_of_allocated_strategy_capital"]
                    ),
                    "margin_pct_of_company_equity": float(
                        materiality["margin_pct_of_company_equity"]
                    ),
                    "portfolio_scale": float(review["portfolioScale"]),
                    "binding_rules": list(review["portfolioBindingRules"]),
                }
            )

        relative_stress = [
            float(row["stress_pct_of_allocated_capital"]) for row in position_rows
        ]
        self.assertLess(max(relative_stress) - min(relative_stress), 1e-6)
        self.assertGreater(
            float(position_rows[-1]["stress_pct_of_company_equity"]),
            float(position_rows[0]["stress_pct_of_company_equity"]),
        )
        self.assertEqual([], position_rows[4]["binding_rules"])
        self.assertIn("company_materiality", position_rows[5]["binding_rules"])
        self.assertIn("company_materiality", position_rows[6]["binding_rules"])
        self.assertGreater(float(position_rows[5]["approval_ratio"]), 0.85)
        self.assertLess(float(position_rows[5]["approval_ratio"]), 0.95)
        self.assertGreater(float(position_rows[6]["approval_ratio"]), 0.65)
        self.assertLess(float(position_rows[6]["approval_ratio"]), 0.80)

        # Capability is intentionally narrow. Sample many stable personnel identities
        # while holding style, market, appetite, capital and target fixed.
        capability_levels = (35.0, 52.0, 70.0)
        capability_report: dict[str, object] = {}
        mandate, allocated = _mandate(pct=20.0, target_lots=220)
        baseline_hard_facts = None
        for level in capability_levels:
            measurement_abs_errors: list[float] = []
            stress_abs_errors: list[float] = []
            portfolio_scales: list[float] = []
            approval_ratios: list[float] = []
            resolved_strategy_stress_limits: list[float] = []
            for sample_index in range(64):
                profile = _capability_profile(level, sample_index)
                review = build_oil_short_horizon_risk_review(
                    market,
                    mandate,
                    company_equity_usd=COMPANY_EQUITY_USD,
                    allocated_strategy_capital_usd=allocated,
                    company_risk_appetite=appetite,
                    risk_profile=profile,
                )
                if baseline_hard_facts is None:
                    baseline_hard_facts = review["hardFacts"]
                self.assertEqual(baseline_hard_facts, review["hardFacts"])
                estimates = review["softRiskEstimatesBeforePortfolioScale"]
                per_contract = estimates["per_contract"][CONTRACT_ID]
                measurement_abs_errors.append(
                    abs(float(per_contract["measurement_error_fraction"]))
                )
                stress_abs_errors.append(
                    abs(float(estimates["stress_analysis_error_fraction"]))
                )
                portfolio_scales.append(float(review["portfolioScale"]))
                approved = int(review["riskApprovedTargets"][CONTRACT_ID])
                approval_ratios.append(approved / 220.0)
                resolved_strategy_stress_limits.append(
                    float(
                        review["companyRiskAppetite"]["resolved_limits"]
                        ["max_strategy_stress_loss_pct_of_allocated_capital"]
                    )
                )
            capability_report[str(int(level))] = {
                "measurement_abs_error": _summary(measurement_abs_errors),
                "stress_analysis_abs_error": _summary(stress_abs_errors),
                "portfolio_scale": _summary(portfolio_scales),
                "approval_ratio": _summary(approval_ratios),
                "resolved_strategy_stress_limit_pct": _summary(
                    resolved_strategy_stress_limits
                ),
            }

        low = capability_report["35"]
        high = capability_report["70"]
        self.assertGreater(
            low["measurement_abs_error"]["median"],
            high["measurement_abs_error"]["median"],
        )
        self.assertGreater(
            low["stress_analysis_abs_error"]["median"],
            high["stress_analysis_abs_error"]["median"],
        )
        self.assertLessEqual(high["measurement_abs_error"]["max"], 0.03 + 1e-12)
        self.assertLessEqual(high["stress_analysis_abs_error"]["max"], 0.04 + 1e-12)
        self.assertGreaterEqual(high["approval_ratio"]["min"], 0.90)
        self.assertLess(low["approval_ratio"]["min"], 0.80)

        report = {
            "position_materiality_sweep": position_rows,
            "capability_sweep": capability_report,
            "interpretation_contract": {
                "capability_changes_hard_facts": False,
                "capability_expected_to_be_lightweight": True,
                "position_sweep_keeps_strategy_relative_risk_intensity_constant": True,
            },
        }
        print("RISK_V2_QUANT_AUDIT=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
