# Oil / Short-Horizon Risk v0.2 Candidate Acceptance

> Status: **candidate accepted; production cutover not yet performed**.  The frozen Directional Oil signal, thesis, execution and formal-account runtime remain unchanged on the production path.

## Decision

The v0.2 strategy-risk boundary is accepted for integration testing.

The accepted ownership model is:

```text
Investment Decision Committee
  owns strategy admission, capital authorization and company Risk Appetite
        |
        v
PM / Strategy
  owns alpha and proposed positions
        |
        v
Oil / Short-Horizon Strategy Risk v0.2
  BINDS
    - two-week stress relative to allocated strategy capital
    - initial margin relative to allocated strategy capital
    - market hard position/trade constraints
    - liquidity, concentration and roll constraints
  REPORTS BUT DOES NOT BIND
    - strategy stress as % of company equity
    - strategy margin as % of company equity
        |
        v
Execution Desk + Formal Account
        |
        v
Future Corporate Aggregate Risk
  will own binding company-wide stress, margin/funding and cross-strategy aggregation
```

A single strategy-risk group may not recreate a second absolute company-capital ceiling after Investment Decision has already authorized strategy capital.

## Why v0.1.1 was rejected for cutover

Three independent diagnostics exposed two structural problems before any production cutover.

First, the earliest candidate compared an annualized one-sigma-like exposure directly with a short-horizon loss limit.  It cut median approvals at 10/35/60/100% capital authorization to roughly 21.6/18.7/11.3/6.6%.  The fix was dimensional rather than political: the Oil / Short-Horizon group now evaluates a two-week risk window.

Second, after the horizon correction, path-dependent economic replay showed that the v0.1.1 `company_materiality` and `company_margin_materiality` caps still overrode Investment Decision.  At high capital authorization, 35/60/100% books converged toward nearly the same absolute strategy scale.

An appetite frontier demonstrated that simply increasing loss tolerance did not solve the issue.  At the most permissive loss-appetite score tested, 60% capital retained only about 55% of legacy CAGR and 49% of turnover, while 100% capital retained only about 33% of CAGR and 25% of turnover.  As stress tolerance loosened, `company_margin_materiality` simply replaced `company_materiality` as the active absolute ceiling.

A boundary counterfactual then removed the two company-level per-strategy caps independently.  Removing company stress alone did not restore capital scaling; company margin immediately became the next cap.  Removing both restored a stable strategy-relative response across capital authorization.  With the strategy stress budget at the economically validated level, CAGR retention became approximately 95.4/95.7/96.2/99.0% across 10/35/60/100% capital.

## v0.2 calibration

v0.2 preserves `strategy_stress_loss_tolerance=50` as the semantic neutral point.  It does not relabel a formerly aggressive score as neutral.

Instead, the strategy-relative two-week stress mapping is recalibrated from the v0.1.1 6/12/22% anchors to 9/18/33% at scores 0/50/100.  This is a uniform 1.5x rescaling of the prior piecewise geometry.  Personnel style and monitoring may still operate conservatively inside the committee-approved limit.

Company stress and company margin remain calculated and auditable, but are diagnostic-only in the strategy review.  Their future binding owner is `corporate_aggregate_risk`.

## Path-dependent economic acceptance

The acceptance replay uses seeds 0-3, three years, and 10/35/60/100% capital authorization.  Each v0.2-approved target is actually fed through the existing frozen Execution Desk and Formal Account, and counterfactual positions/equity/fee history are rolled into the next turn.

Mean v0.2 / frozen-legacy ratios:

| Capital authorization | CAGR retention | Drawdown ratio | Return/DD ratio | Turnover ratio | Mean max margin/equity |
|---:|---:|---:|---:|---:|---:|
| 10% | **95.43%** | 98.50% | 96.96% | **95.69%** | 3.36% |
| 35% | **95.69%** | 98.69% | 96.98% | **95.59%** | 11.41% |
| 60% | **96.16%** | 98.36% | 97.79% | **95.37%** | 18.83% |
| 100% | **99.05%** | 98.08% | **100.98%** | **98.96%** | 30.51% |

Acceptance gates require, for every capital level:

- mean CAGR retention >= 90%;
- mean turnover retention >= 90%;
- mean return/drawdown ratio >= 90% of frozen legacy;
- mean maximum drawdown <= 105% of frozen legacy;
- no `company_materiality` or `company_margin_materiality` binding in strategy risk.

Capital authorization must also remain economically distinguishable.  Mean maximum margin/equity rises monotonically from about 3.36% to 11.41%, 18.83% and 30.51%; the 100% book therefore no longer collapses onto the 60% book through a hidden absolute company cap.

## Personnel acceptance under v0.2

The three-year broad shadow was rerun with the new v0.2 review kernel over 1,120 active real PM turns.

Across all capital levels:

| Effect | Mean approval range | Median range | Active-effect median |
|---|---:|---:|---:|
| Risk style | **10.77 pp** | 0.26 pp | **31.25 pp** |
| Capability | 0.87 pp | 0.00 pp | 2.58 pp |

The mean style effect is about **12.4x** the capability effect.  Tolerant style approved at least as much as conservative style on **1,120 / 1,120** active turns.  High capability versus low capability was more permissive on 118 turns, more restrictive on 198, and equal on 804, so capability still does not become a hidden permissiveness score.

The existing broad-shadow regression requires persistent style effects to remain materially larger than lightweight capability effects.

## CI policy

Minute-scale economic research replays are not ordinary unit tests.

The CI is split into:

1. fast ordinary unit tests for semantics, contracts, accounts, services and deterministic invariants;
2. a dedicated Risk v0.2 acceptance job that runs the current three-year economic and personnel gates;
3. manually dispatched historical research replay for the rejected v0.1.1 shadow, appetite frontier and ownership-boundary experiments.

This keeps the research evidence reproducible without making every ordinary code change rerun the entire research path.

## Remaining cutover step

v0.2 is accepted as the strategy-risk candidate, not yet as the production runtime.

The remaining integration step is to wire v0.2 into the Directional runtime behind an explicit candidate/runtime switch, then demonstrate exact semantic equivalence of the frozen signal/thesis/execution/account layers and expected economic equivalence to the standalone v0.2 acceptance replay.  Only after that integration gate passes should the legacy strategy-risk/corporate-risk path be removed or the PR be marked ready for production review.
