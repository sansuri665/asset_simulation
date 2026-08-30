# Investment Decision and Oil Short-Horizon Risk v2

> Status: candidate governance/risk architecture on `feature/investment-decision-risk-v2`; the frozen Directional Oil runtime is not cut over yet.

## Governance boundary

```text
Investment Decision Committee
├─ strategy admission / charter
├─ capital mandate
├─ company risk appetite
└─ preserve-or-reduce position mandate

PM / Strategy Group
└─ creates alpha and proposed positions

Oil Risk Division / Short-Horizon Risk Group
└─ reviews actual committee position mandates using allocated capital as an input

Corporate aggregate risk
└─ future portfolio-level aggregation across strategy groups
```

Investment Decision never creates alpha. A committee position mandate may preserve, reduce, or reject PM intent, but may not open a position from zero intent, reverse direction, or increase absolute exposure.

Risk never recommends strategy capital authorization in the v2 path. Capital allocation and company risk appetite are governance inputs owned by Investment Decision.

## Personnel architecture

Risk personnel are scoped by `asset=oil` and `horizon=short_horizon`, not by strategy id. The same group covers both `directional` and `calendar_spread` strategies.

Risk-personnel differentiation is deliberately asymmetric:

- wide review-style dispersion: tail-risk focus, intervention earliness, liquidity priority, concentration aversion, model skepticism;
- narrow professional-capability dispersion: risk measurement, stress analysis, monitoring discipline.

Capability never changes hard facts such as current positions, exchange/market position limits, turn-trade limits, expiry, or contract trading status. It only produces bounded deterministic uncertainty in soft estimates and monitoring-derived soft limits. Higher capability narrows this uncertainty; it does not make the officer mechanically more conservative or more permissive.

## Risk horizon

The Oil / Short-Horizon group uses a **two-week review horizon**, matching the half-month decision cadence.

- Directional risk starts from visible annualized volatility and rescales it by `sqrt(2 / 52)` before applying tail-risk and model-uncertainty multipliers.
- Calendar-spread risk starts from visible weekly changes in the real `main - next_main` dollar spread and rescales the spread sigma by `sqrt(2)`.
- Tail-risk and model-uncertainty multipliers are applied exactly once. In particular, a balanced calendar spread is not penalized by applying the same multipliers both inside the stressed spread move and again to the resulting dollar loss.

An earlier shadow candidate incorrectly compared an annualized one-sigma-like exposure directly with short-horizon loss limits. It produced median approvals of roughly 21.6%, 18.7%, 11.3% and 6.6% at 10%, 35%, 60% and 100% capital authorization. That candidate was rejected as a time-horizon mismatch rather than accepted by loosening limits.

## Controlled quantitative validation

A controlled audit keeps market state, company risk appetite and risk style fixed, then varies either position materiality or professional capability.

### Position materiality

The audit uses a USD 100m company and scales position linearly with allocation so the **two-week strategy-relative stress stays fixed at 8.1859% of allocated capital**. No allocation tier or `if allocation > X` rule exists.

| Allocation | Target lots | Stress / company equity | Portfolio scale | Approved lots | Binding |
|---:|---:|---:|---:|---:|---|
| 1% | 40 | 0.0819% | 1.0000 | 40 | none |
| 5% | 200 | 0.4093% | 1.0000 | 200 | none |
| 10% | 400 | 0.8186% | 1.0000 | 400 | none |
| 25% | 1,000 | 2.0465% | 1.0000 | 1,000 | none |
| 35% | 1,400 | 2.8651% | 0.9632 | 1,348 | company materiality |
| 40% | 1,600 | 3.2744% | 0.8428 | 1,348 | company materiality |
| 50% | 2,000 | 4.0930% | 0.6742 | 1,348 | company materiality |

The same strategy-relative risk is fully acceptable while the book is small, then the company-equity constraint naturally creates an absolute risk ceiling as the strategy becomes material. The exact crossover is deliberately not a fixed allocation threshold; it can move continuously with market conditions, company appetite and the appointed risk officer's bounded monitoring estimate.

### Lightweight capability

The capability audit holds all five style dimensions at 50 and reviews the same 20%-allocated, 1,100-lot position across 64 stable personnel identities at capability levels 35, 52 and 70.

| Capability | Median abs vol-measurement error | Median abs stress-analysis error | Worst approval ratio | Median approval ratio |
|---:|---:|---:|---:|---:|
| 35 | 6.70% | 7.52% | 71.91% | 100.00% |
| 52 | 3.68% | 4.54% | 87.55% | 99.95% |
| 70 | 1.71% | 2.08% | 93.00% | 99.68% |

At capability 70 the maximum observed volatility-measurement error remains below 3% and the maximum stress-analysis error below 4%; at capability 35 the corresponding maxima are about 11.85% and 14.92%.

The important interpretation is that capability mainly narrows uncertainty around marginal risk decisions. It does not modify hard market facts and it does not create a universal return advantage. Style remains the larger source of persistent policy differences.

## Real Directional shadow replay

The production v2 candidate was then run non-binding against real Directional Oil decisions. For seeds 0-3, each one-year replay produced 96 half-month turns per capital-authorization level. The shadow consumes the PM's already-produced `strategy_intent_target_position_lots`; it does not recompute alpha and it cannot change realized trades.

| Capital authorization | Active turns | Legacy strategy median approval | v2 median approval | v2 min approval | v2 more restrictive vs legacy strategy | Main v2 portfolio binding |
|---:|---:|---:|---:|---:|---:|---|
| 10% | 90 | 100% | 100% | 32.26% | 40.0% | strategy stress (36 turns) |
| 35% | 92 | 100% | 99.0% | 24.58% | 50.0% | company materiality (46 turns) |
| 60% | 92 | 100% | 59.61% | 14.95% | 63.04% | company materiality (58 turns) |
| 100% | 92 | 100% | 35.67% | 8.91% | 77.17% | company materiality (71 turns; company margin once) |

This is the intended qualitative behavior:

- a 10% experimental/small book usually passes unchanged, although genuinely high-risk turns can still be clipped;
- around 35% the median book is still almost fully approved, while company materiality begins to matter in stressful turns;
- at 60% and 100% the same PM risk becomes a core-company exposure and is materially constrained by company-level loss contribution;
- v2 never becomes looser than legacy strategy risk in this replay; one 100%-allocation turn is less restrictive than the *legacy final corporate-risk* result, which is expected because the legacy corporate layer is a separate model and v2 is not yet the final multi-strategy corporate aggregator.

The real replay therefore validates the two-week horizon correction without requiring a change to the company Risk Appetite mapping.

## Broad three-year personnel shadow

A broader non-binding shadow then tests the personnel architecture itself rather than one default officer:

- seeds 0-3;
- 2030-01-H1 through 2033-01-H1;
- capital authorization at 10%, 35%, 60% and 100%;
- 1,120 active real PM turns in total;
- six stable personnel identities per controlled level;
- style sweep at capability 70: tolerant=20, neutral=50, conservative=80 on all five style axes;
- capability sweep at neutral style: low=35, medium=52, high=70 on all three capability axes.

### Style is structurally larger than capability

Across all allocations, the per-turn range in approval ratios is:

| Effect | Mean range | Median range | P90 range | Median range when effect is active |
|---|---:|---:|---:|---:|
| Risk style | **13.69 pp** | **11.06 pp** | **34.23 pp** | **19.50 pp** |
| Capability | 1.43 pp | 0.68 pp | 4.26 pp | 1.91 pp |

The mean style effect is about **9.5x** the capability effect; on turns where each effect is non-zero, the median style range is about **10.2x** the capability range. This is the intended asymmetry: risk philosophy dominates normal personnel differentiation, while capability remains economically visible around marginal decisions.

The ordering is also clean. Across all **1,120 active turns**, tolerant style approved at least as much as conservative style in **1,120 / 1,120** cases, with no reversals.

By capital authorization, mean/median approval ratios for controlled styles are:

| Capital | Tolerant mean / median | Neutral mean / median | Conservative mean / median |
|---:|---:|---:|---:|
| 10% | 88.65% / 100% | 83.70% / 100% | 74.44% / 95.83% |
| 35% | 80.82% / 100% | 76.06% / 99.52% | 66.95% / 71.10% |
| 60% | 67.06% / 71.46% | 61.92% / 59.15% | 52.72% / 41.67% |
| 100% | 53.55% / 43.05% | 48.86% / 35.62% | 41.19% / 25.03% |

The style distinction therefore remains visible both in small books and when company materiality dominates large books.

### Capability does not become a hidden permissiveness score

Across the same 1,120 active turns, high capability relative to low capability was:

- more permissive on 149 turns;
- more restrictive on 495 turns;
- equal on 476 turns.

So `capability=70` is not equivalent to “approve more risk”. Its primary value remains tighter estimation error rather than a preferred risk appetite.

There is a small residual mean-level asymmetry in this fixed six-identity sample: low-capability mean approval is roughly 0.7-0.9 percentage points above high-capability mean approval across the four capital levels. This is an order of magnitude smaller than the style effect. It is not treated as a capability ranking, but it should be rechecked with a larger or explicitly paired identity sample before binding cutover because bounded nonlinear approval and fixed profile-hash draws can create small sampling/convexity biases.

## Test retention

The following tests retain the evidence:

- `tests/test_oil_short_horizon_risk_quantitative_audit.py`: controlled materiality and capability sweeps;
- `tests/test_oil_short_horizon_risk_shadow_audit.py`: real Directional legacy-vs-v2 one-year shadow replay;
- `tests/test_oil_short_horizon_risk_broad_shadow_audit.py`: three-year style/capability shadow across four capital levels;
- `tests/test_oil_short_horizon_risk_calendar_horizon.py`: two-week calendar-spread sigma and single tail/model multiplier semantics.

## Current cutover rule

The legacy Directional Oil risk path remains binding. The broader style/capability shadow now passes, but v2 remains non-binding until counterfactual **economic** replay is run with v2 approvals actually feeding the existing execution/account path. That replay must establish that the new governance/risk path changes risk outcomes for coherent reasons rather than merely truncating the already-frozen strategy economics. A later cutover must preserve the frozen Directional Oil signal, thesis, execution and account semantics; only the governance/risk approval path is eligible to change.
