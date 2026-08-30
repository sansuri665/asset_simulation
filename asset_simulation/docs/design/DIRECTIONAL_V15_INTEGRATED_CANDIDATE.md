# Directional oil v1.5 integrated candidate

> Status: review candidate based directly on `main`; not merged.

## Scope

This candidate keeps the existing PM style-first architecture and corrects the
capital/risk relationship without importing the web candidate's checkpoint
history or changing the forecast research engine.

## Parallel sizing architecture

The three governance layers have different outputs:

```text
Investment Decision Committee
    authorizes a capital ceiling
        ├─> PM builds strategy intent inside the ceiling
        └─> Risk computes independent admissible limits

approved strategy target = clip(PM intent, strategy-risk limits)
company-approved target  = clip(approved strategy target, company limits)
```

`capital_deployment` therefore remains a real PM behavior. It controls normal
use of already-authorized capital, but never changes the authorization itself.
The risk department does not multiply the PM deployment fraction by another
personnel fraction. It compares the resulting target with independently
computed volatility, margin, gross, concentration, liquidity, roll and
drawdown limits.

The directional strategy's annualized-volatility budget anchors are calibrated
from `35 / 100 / 160` to `30 / 75 / 120` percent of authorized capital. This is
an independent strategy-risk ceiling, not a multiplier applied to PM intent;
the risk algorithm and company CRO policy remain unchanged.

Changing only `capital_deployment` must:

- change the PM strategy-intent envelope and normally change raw target lots;
- leave the risk review policy unchanged;
- leave committee authorization unchanged;
- leave market and company hard limits unchanged;
- never allow risk to enlarge or reverse PM intent.

Risk may continue to review structural strategy characteristics such as
responsiveness, selectivity, turnover, holding patience, orientation
extremeness, near-month focus and forecast horizon. Whether future investment
committees should use risk recommendations when assigning capital remains a
separate governance question.

## Thesis calibration

The candidate keeps the published-forecast-only thesis hardening:

- material confidence-band breach: `1.25z`;
- severe breach: `2.0z`;
- direction miss requires published forecast conviction of at least `0.35z`;
- the registered conviction field is required and validated;
- no hidden ability score or future market truth enters thesis evaluation.

## Explicitly deferred

The forecast truth-transfer function remains unchanged. A future forecast-layer
audit must first compare capability score with directional error, path error,
range coverage and calibration before changing hidden-truth transmission in
order to influence downstream strategy returns.
