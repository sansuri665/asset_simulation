from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import math
import statistics
import unittest

from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.investment_decision import (
    build_company_risk_appetite,
    build_strategy_capital_mandate,
    build_strategy_charter,
    build_strategy_position_mandate,
)
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_short_horizon_risk import (
    build_default_oil_short_horizon_risk_profile,
    build_oil_short_horizon_risk_review,
    resolve_oil_short_horizon_risk_profile,
)
from asset_simulation.model.oil_trading_strategy import simulate_oil_trading_strategy


STYLE_LEVELS = {
    "tolerant": 20.0,
    "neutral": 50.0,
    "conservative": 80.0,
}
CAPABILITY_LEVELS = {
    "low": 35.0,
    "medium": 52.0,
    "high": 70.0,
}
PROFILE_SAMPLES = 6
ALLOCATIONS = (10.0, 35.0, 60.0, 100.0)
SEEDS = (0, 1, 2, 3)


def _gross(targets: dict[str, int]) -> int:
    return sum(abs(int(value)) for value in targets.values())


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else float(numerator) / float(denominator)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    mix = position - low
    return ordered[low] * (1.0 - mix) + ordered[high] * mix


def _summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "median": 0.0, "mean": 0.0, "p90": 0.0, "max": 0.0}
    return {
        "min": min(values),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "p90": _percentile(values, 0.90),
        "max": max(values),
    }


def _controlled_profile(
    *,
    family: str,
    label: str,
    sample_index: int,
    style_level: float,
    capability_level: float,
) -> dict:
    base = build_default_oil_short_horizon_risk_profile()
    candidate = deepcopy(base)
    candidate.pop("profile_hash")
    candidate["appointment"] = {
        **candidate["appointment"],
        "personnel_id": f"broad_shadow_{family}_{label}_{sample_index}",
        "display_name": f"Broad {family} {label} {sample_index}",
        "source": "broad_shadow_audit",
        "candidate_index": sample_index,
        "generation_seed": 810_000 + sample_index,
    }
    candidate["style_radar"] = {
        key: float(style_level) for key in candidate["style_radar"]
    }
    candidate["capability_radar"] = {
        key: float(capability_level) for key in candidate["capability_radar"]
    }
    return resolve_oil_short_horizon_risk_profile(candidate)


def _profile_families() -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    # Style is measured at high capability to minimize estimation noise and isolate
    # policy philosophy. Capability is measured at neutral style.
    styles = {
        label: [
            _controlled_profile(
                family="style",
                label=label,
                sample_index=index,
                style_level=level,
                capability_level=70.0,
            )
            for index in range(PROFILE_SAMPLES)
        ]
        for label, level in STYLE_LEVELS.items()
    }
    capabilities = {
        label: [
            _controlled_profile(
                family="capability",
                label=label,
                sample_index=index,
                style_level=50.0,
                capability_level=level,
            )
            for index in range(PROFILE_SAMPLES)
        ]
        for label, level in CAPABILITY_LEVELS.items()
    }
    return styles, capabilities


class OilShortHorizonRiskBroadShadowAuditTests(unittest.TestCase):
    def test_three_year_shadow_style_dominates_lightweight_capability(self) -> None:
        appetite = build_company_risk_appetite()
        style_profiles, capability_profiles = _profile_families()
        report: dict[str, object] = {
            "scope": {
                "seeds": list(SEEDS),
                "years": 3,
                "allocations_pct": list(ALLOCATIONS),
                "profile_samples_per_level": PROFILE_SAMPLES,
                "style_levels": STYLE_LEVELS,
                "capability_levels": CAPABILITY_LEVELS,
                "style_sweep_capability": 70.0,
                "capability_sweep_style": 50.0,
                "binding_runtime_unchanged": True,
            },
            "allocations": {},
        }

        global_style_ranges: list[float] = []
        global_capability_ranges: list[float] = []
        global_active_style_ranges: list[float] = []
        global_active_capability_ranges: list[float] = []
        tolerant_ge_conservative = 0
        tolerant_lt_conservative = 0
        high_cap_gt_low = 0
        high_cap_lt_low = 0
        high_cap_eq_low = 0

        for allocation_pct in ALLOCATIONS:
            style_ratios: dict[str, list[float]] = {key: [] for key in STYLE_LEVELS}
            capability_ratios: dict[str, list[float]] = {
                key: [] for key in CAPABILITY_LEVELS
            }
            style_bindings: dict[str, Counter[str]] = {
                key: Counter() for key in STYLE_LEVELS
            }
            capability_bindings: dict[str, Counter[str]] = {
                key: Counter() for key in CAPABILITY_LEVELS
            }
            legacy_ratios: list[float] = []
            style_ranges: list[float] = []
            capability_ranges: list[float] = []
            active_turns = 0
            total_turns = 0

            for seed in SEEDS:
                global_run = run_global_macro(seed, 10)
                simulation = simulate_oil_trading_strategy(
                    global_run,
                    start_year=2030,
                    start_month=1,
                    start_half=1,
                    end_year=2033,
                    end_month=1,
                    end_half=1,
                    capital_authorization_pct_of_company_equity=allocation_pct,
                )
                charter = build_strategy_charter(
                    asset="oil",
                    horizon="short_horizon",
                    strategy_type="directional",
                    strategy_id=str(simulation["strategy"]["strategy_id"]),
                )
                for turn in simulation["turns"]:
                    total_turns += 1
                    decision = turn["decision"]
                    pm_targets = {
                        str(item["contract_id"]): int(
                            item["strategy_intent_target_position_lots"]
                        )
                        for item in decision["targets"]
                    }
                    pm_gross = _gross(pm_targets)
                    if pm_gross == 0:
                        continue
                    active_turns += 1
                    legacy_strategy_gross = sum(
                        abs(int(item["strategy_risk_approved_target_position_lots"]))
                        for item in decision["targets"]
                    )
                    legacy_ratios.append(_ratio(legacy_strategy_gross, pm_gross))
                    as_of = decision["asOf"]
                    market = oil_futures_payload(
                        global_run,
                        as_of_year=int(as_of["year"]),
                        as_of_month=int(as_of["month"]),
                        as_of_half=int(as_of["half"]),
                    )
                    equity = float(decision["accountBefore"]["equity_usd"])
                    capital = build_strategy_capital_mandate(
                        charter,
                        company_equity_usd=equity,
                        authorized_pct_of_company_equity=allocation_pct,
                    )
                    mandate = build_strategy_position_mandate(
                        charter,
                        capital,
                        pm_targets,
                    )

                    baseline_hard_facts = None
                    turn_style_means: dict[str, float] = {}
                    for label, profiles in style_profiles.items():
                        sample_ratios: list[float] = []
                        for profile in profiles:
                            review = build_oil_short_horizon_risk_review(
                                market,
                                mandate,
                                company_equity_usd=equity,
                                allocated_strategy_capital_usd=float(
                                    capital["authorized_capital_usd"]
                                ),
                                current_positions=decision["accountBefore"]["positions"],
                                company_risk_appetite=appetite,
                                risk_profile=profile,
                            )
                            self.assertEqual(
                                2.0,
                                float(review["riskHorizon"]["review_horizon_weeks"]),
                            )
                            if baseline_hard_facts is None:
                                baseline_hard_facts = review["hardFacts"]
                            self.assertEqual(baseline_hard_facts, review["hardFacts"])
                            approved_gross = _gross(
                                {
                                    str(key): int(value)
                                    for key, value in review[
                                        "riskApprovedTargets"
                                    ].items()
                                }
                            )
                            sample_ratios.append(_ratio(approved_gross, pm_gross))
                            style_bindings[label].update(
                                review["portfolioBindingRules"]
                            )
                        turn_style_means[label] = statistics.fmean(sample_ratios)
                        style_ratios[label].append(turn_style_means[label])

                    turn_capability_means: dict[str, float] = {}
                    for label, profiles in capability_profiles.items():
                        sample_ratios = []
                        for profile in profiles:
                            review = build_oil_short_horizon_risk_review(
                                market,
                                mandate,
                                company_equity_usd=equity,
                                allocated_strategy_capital_usd=float(
                                    capital["authorized_capital_usd"]
                                ),
                                current_positions=decision["accountBefore"]["positions"],
                                company_risk_appetite=appetite,
                                risk_profile=profile,
                            )
                            self.assertEqual(baseline_hard_facts, review["hardFacts"])
                            approved_gross = _gross(
                                {
                                    str(key): int(value)
                                    for key, value in review[
                                        "riskApprovedTargets"
                                    ].items()
                                }
                            )
                            sample_ratios.append(_ratio(approved_gross, pm_gross))
                            capability_bindings[label].update(
                                review["portfolioBindingRules"]
                            )
                        turn_capability_means[label] = statistics.fmean(sample_ratios)
                        capability_ratios[label].append(turn_capability_means[label])

                    style_range = max(turn_style_means.values()) - min(
                        turn_style_means.values()
                    )
                    capability_range = max(turn_capability_means.values()) - min(
                        turn_capability_means.values()
                    )
                    style_ranges.append(style_range)
                    capability_ranges.append(capability_range)
                    global_style_ranges.append(style_range)
                    global_capability_ranges.append(capability_range)
                    if style_range > 1e-12:
                        global_active_style_ranges.append(style_range)
                    if capability_range > 1e-12:
                        global_active_capability_ranges.append(capability_range)

                    if (
                        turn_style_means["tolerant"]
                        >= turn_style_means["conservative"] - 1e-12
                    ):
                        tolerant_ge_conservative += 1
                    else:
                        tolerant_lt_conservative += 1
                    high = turn_capability_means["high"]
                    low = turn_capability_means["low"]
                    if high > low + 1e-12:
                        high_cap_gt_low += 1
                    elif high < low - 1e-12:
                        high_cap_lt_low += 1
                    else:
                        high_cap_eq_low += 1

            report["allocations"][str(int(allocation_pct))] = {
                "turns": total_turns,
                "active_turns": active_turns,
                "legacy_strategy_approval_ratio": _summary(legacy_ratios),
                "style_approval_ratio": {
                    label: _summary(values) for label, values in style_ratios.items()
                },
                "capability_approval_ratio": {
                    label: _summary(values)
                    for label, values in capability_ratios.items()
                },
                "style_effect_range": _summary(style_ranges),
                "capability_effect_range": _summary(capability_ranges),
                "style_binding_counts": {
                    label: dict(sorted(counter.items()))
                    for label, counter in style_bindings.items()
                },
                "capability_binding_counts": {
                    label: dict(sorted(counter.items()))
                    for label, counter in capability_bindings.items()
                },
            }

        style_summary = _summary(global_style_ranges)
        capability_summary = _summary(global_capability_ranges)
        active_style_summary = _summary(global_active_style_ranges)
        active_capability_summary = _summary(global_active_capability_ranges)
        report["cross_allocation"] = {
            "style_effect_range": style_summary,
            "capability_effect_range": capability_summary,
            "active_style_effect_range": active_style_summary,
            "active_capability_effect_range": active_capability_summary,
            "tolerant_ge_conservative_turns": tolerant_ge_conservative,
            "tolerant_lt_conservative_turns": tolerant_lt_conservative,
            "high_capability_more_permissive_than_low_turns": high_cap_gt_low,
            "high_capability_more_restrictive_than_low_turns": high_cap_lt_low,
            "high_capability_equal_low_turns": high_cap_eq_low,
        }

        # Risk philosophy must remain the dominant personnel difference, while
        # higher professional capability must not collapse into a permissiveness
        # score. The 5x margin leaves substantial room around the observed ~10x
        # separation without pinning exact calibration values.
        self.assertEqual(0, tolerant_lt_conservative)
        self.assertGreater(tolerant_ge_conservative, 0)
        self.assertGreater(high_cap_gt_low, 0)
        self.assertGreater(high_cap_lt_low, 0)
        self.assertGreater(high_cap_eq_low, 0)
        self.assertGreater(len(global_active_style_ranges), 0)
        self.assertGreater(len(global_active_capability_ranges), 0)
        self.assertGreater(
            float(active_style_summary["median"]),
            5.0 * float(active_capability_summary["median"]),
        )
        self.assertGreater(
            float(style_summary["mean"]),
            5.0 * float(capability_summary["mean"]),
        )

        print("RISK_V2_BROAD_SHADOW_AUDIT=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
