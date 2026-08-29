# Directional Oil v1.5 calibration notes

> Status: calibration candidate on `calibration/directional-v1.5`; not merged into `main`.

## Ownership change

`capital_deployment` remains an appointed PM style / risk-expression score, but it no longer resolves to a hard percentage of already-authorized strategy capital.

Capital flow is now:

```text
PM style radar
→ strategy-risk pressure / risk recommendation
→ Investment Decision Committee authorized_strategy_capital_usd
→ strategy-specific capacity adapter
→ strategy risk
→ corporate CRO
→ account / market hard rules
```

The Investment Decision Committee is the unique capital-allocation owner. Directional oil and calendar spread both consume committee-authorized capital directly; neither applies a shared PM capital haircut.

## Directional strategy-specific risk calibration

Removing the shared PM capital haircut exposed the true directional-strategy risk envelope. Absolute risk cooling therefore belongs in the directional strategy-risk adapter, not in shared personnel policy. The registered annualized-volatility budget anchors are calibrated from `35 / 100 / 160` to `30 / 75 / 120` percent of committee-authorized capital. The `oil_strategy_risk` algorithm itself remains identical to `main`; this is a configuration-only calibration.

## Directional thesis hardening

- material confidence-band breach: `1.35z`;
- severe breach: `2.0z`;
- minimum realized / forecast direction move remains `0.004` log-return;
- a direction miss is eligible only when published forecast direction conviction is at least `0.50z`;
- `minimum_direction_forecast_z` is a required registered field and fails closed if missing.

No configured research ability score or hidden future data enters thesis evaluation.

## Forecast information calibration

The synthetic research-skill truth transfer is changed from smoothstep to:

```text
truth_mix = (skill / 100)^2
```

Representative hidden-shape mixes are 2.25% at score 15, 25% at 50, 49% at 70, and 100% at 100. The purpose is to reduce the slope from ordinary/good research capability to directly tradable hidden-path alpha while preserving clear skill differentiation and exact endpoints.

## Economic validation split

- development: Seeds 0–7;
- validation: Seeds 8–15;
- combined orientation ecology: 64 Seed × forecast-band cells.

Acceptance requires combined largest orientation winner share <=65%, at least three winning orientation scores, winners on both sides of neutral in development and validation, thesis occupancy guardrails, turnover/cost/round-trip controls, formal-account realism, and regime semantics: continuation remains competitive in trends, reversion remains competitive in ranges, and reversion is the clear turning-state winner.

The old single-sample `<=50%` orientation-winner rule and direct capital-deployment volatility/drawdown rules remain diagnostics only because they are not valid ownership invariants under committee-owned capital allocation.
