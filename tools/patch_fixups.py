from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def path(rel: str) -> Path:
    return ROOT / rel

def rep(rel: str, old: str, new: str) -> None:
    p = path(rel)
    text = p.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{rel}: expected exactly one replacement, found {count}: {old[:120]!r}')
    p.write_text(text.replace(old, new), encoding='utf-8')

# Calendar spread: one remaining invariant still referred to the deleted PM haircut field.
rep(
    'asset_simulation/model/oil_calendar_spread_strategy.py',
    '                <= float(capacity["capital_deployment_budget_usd"]) + 1e-9,\n',
    '                <= float(capacity["capital_capacity_budget_usd"]) + 1e-9,\n',
)

# Acceptance module lives at package root; registry lives under model.
rep(
    'asset_simulation/audit_oil_directional_economic_acceptance.py',
    'from .registry import sha256_json\n',
    'from .model.registry import sha256_json\n',
)

# Strategy-specific risk should be at least as conservative as neutral corporate risk by default.
rep(
    'asset_simulation/config/oil_strategy_risk_v0.1.json',
    '  "config_id": "asset-simulation-oil-strategy-risk-base-v0.1.0",\n  "model_version": "asset-simulation-oil-strategy-risk-v0.1.0",',
    '  "config_id": "asset-simulation-oil-strategy-risk-base-v0.1.1",\n  "model_version": "asset-simulation-oil-strategy-risk-v0.1.1",',
)
rep(
    'asset_simulation/config/oil_strategy_risk_v0.1.json',
    '      "score_50": 80.0,\n',
    '      "score_50": 75.0,\n',
)
rep(
    'asset_simulation/model/oil_strategy_risk.py',
    'OIL_STRATEGY_RISK_MODEL_VERSION = "asset-simulation-oil-strategy-risk-v0.1.0"',
    'OIL_STRATEGY_RISK_MODEL_VERSION = "asset-simulation-oil-strategy-risk-v0.1.1"',
)
rep(
    'asset_simulation/docs/current/OIL_TRADING_STRATEGY.md',
    '策略风控 `asset-simulation-oil-strategy-risk-v0.1.0`',
    '策略风控 `asset-simulation-oil-strategy-risk-v0.1.1`',
)

# One historical PM test assumed every roster must produce different gross exposure in one market.
# Under the new ownership model, capital_deployment can change risk review without directly changing
# current target lots, so verify distinct runtime policies instead of forcing a position difference.
rep(
    'asset_simulation/tests/test_oil_trading_strategy.py',
    '''        self.assertGreater(\n            len({item["riskBudget"]["target_gross_lots"] for item in decisions}),\n            1,\n        )\n''',
    '''        runtime_policy_fingerprints = {\n            (\n                float(item["strategy"]["resolved_policy"]["signal"]["continuation_weight"]),\n                float(item["strategy"]["resolved_policy"]["execution"]["adjustment_speed"]),\n                float(item["strategy"]["resolved_policy"]["execution"]["gross_turnover_multiplier"]),\n                float(item["strategy"]["resolved_policy"]["risk"]["capital_deployment_score"]),\n            )\n            for item in decisions\n        }\n        self.assertGreater(len(runtime_policy_fingerprints), 1)\n''',
)
