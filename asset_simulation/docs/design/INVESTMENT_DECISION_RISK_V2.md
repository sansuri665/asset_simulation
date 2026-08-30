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

## Quantitative validation

A controlled audit keeps market state, company risk appetite and risk style fixed, then varies either position materiality or professional capability.

### Position materiality

The audit uses a USD 100m company and keeps strategy-relative stress intensity constant at 7.8633% of allocated capital by scaling the position linearly with the committee allocation.

| Allocation | Target lots | Stress / company equity | Portfolio scale | Approved lots | Binding |
|---:|---:|---:|---:|---:|---|
| 1% | 8 | 0.0786% | 1.0000 | 8 | none |
| 5% | 40 | 0.3932% | 1.0000 | 40 | none |
| 10% | 80 | 0.7863% | 1.0000 | 80 | none |
| 25% | 200 | 1.9658% | 1.0000 | 200 | none |
| 35% | 280 | 2.7522% | 1.0000 | 280 | none |
| 40% | 320 | 3.1453% | 0.8987 | 287 | company materiality |
| 50% | 400 | 3.9317% | 0.7190 | 287 | company materiality |

This demonstrates the intended behavior without a hard-coded allocation threshold: the same strategy-relative risk is acceptable while small, but company materiality becomes binding when the same risk intensity becomes a core book. In this controlled case the crossover appears between 35% and 40% of company equity.

### Lightweight capability

The capability audit holds style at 50/50/50/50/50 and reviews the same 20%-allocated, 220-lot position across 64 stable personnel identities at capability levels 35, 52 and 70.

| Capability | Median abs vol-measurement error | Median abs stress-analysis error | Worst approval ratio | Median approval ratio |
|---:|---:|---:|---:|---:|
| 35 | 5.58% | 7.40% | 73.64% | 100.0% |
| 52 | 4.29% | 4.20% | 81.36% | 98.64% |
| 70 | 1.43% | 1.83% | 90.45% | 97.50% |

Maximum absolute errors obey the configured capability bounds: at capability 70, volatility-measurement error remains below 3% and stress-analysis error below 4%; at capability 35 the observed maxima are about 11.81% and 14.67% respectively.

The important interpretation is that capability mostly changes uncertainty around a marginal risk decision. It does not modify hard market facts and does not create a universal return advantage. Style remains the larger source of persistent policy differences.

The quantitative audit is retained in `tests/test_oil_short_horizon_risk_quantitative_audit.py` and is part of the full Python unit suite.

## Current cutover rule

The legacy Directional Oil risk path remains binding until a shadow comparison is completed. The v2 candidate is currently diagnostic/governance infrastructure only. Cutover must preserve the frozen Directional Oil signal, thesis, execution, account and economic semantics.