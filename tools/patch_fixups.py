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

# One historical PM test assumed every roster must produce different gross exposure in one market.
# Under the new ownership model, capital_deployment can change risk review without directly changing
# current target lots, so verify distinct runtime policies instead of forcing a position difference.
rep(
    'asset_simulation/tests/test_oil_trading_strategy.py',
    '''        self.assertGreater(\n            len({item["riskBudget"]["target_gross_lots"] for item in decisions}),\n            1,\n        )\n''',
    '''        runtime_policy_fingerprints = {\n            (\n                float(item["strategy"]["resolved_policy"]["signal"]["continuation_weight"]),\n                float(item["strategy"]["resolved_policy"]["execution"]["adjustment_speed"]),\n                float(item["strategy"]["resolved_policy"]["execution"]["gross_turnover_multiplier"]),\n                float(item["strategy"]["resolved_policy"]["risk"]["capital_deployment_score"]),\n            )\n            for item in decisions\n        }\n        self.assertGreater(len(runtime_policy_fingerprints), 1)\n''',
)

# Corporate CRO is allowed to be the final company-wide envelope even when its neutral policy
# is slightly tighter than a strategy-specific risk proposal.  The invariant is non-expansion,
# and a stricter CRO must not approve more risk than the neutral CRO.
rep(
    'asset_simulation/tests/test_corporate_risk_control.py',
    '''    def test_neutral_preserves_current_targets_and_strict_policy_only_clips(self) -> None:\n        neutral = build_oil_strategy_decision(self.market, self.forecast)\n        strict = build_oil_strategy_decision(\n            self.market, self.forecast, corporate_risk_profile=_profile(0.0, "strict")\n        )\n        self.assertTrue(\n            all(\n                item["strategy_target_position_lots"] == item["target_position_lots"]\n                for item in neutral["targets"]\n            )\n        )\n        self.assertGreater(\n            strict["corporateRisk"]["approval_summary"]["clipped_gross_lots"], 0\n        )\n        for strict_item, neutral_item in zip(strict["targets"], neutral["targets"], strict=True):\n            self.assertEqual(\n                strict_item["strategy_target_position_lots"],\n                neutral_item["strategy_target_position_lots"],\n            )\n            self.assertLessEqual(\n                abs(strict_item["target_position_lots"]),\n                abs(strict_item["strategy_target_position_lots"]),\n            )\n            self.assertGreaterEqual(\n                strict_item["target_position_lots"]\n                * strict_item["strategy_target_position_lots"],\n                0,\n            )\n''',
    '''    def test_neutral_and_strict_company_risk_are_nonexpansive(self) -> None:\n        neutral = build_oil_strategy_decision(self.market, self.forecast)\n        strict = build_oil_strategy_decision(\n            self.market, self.forecast, corporate_risk_profile=_profile(0.0, "strict")\n        )\n        self.assertGreaterEqual(\n            neutral["corporateRisk"]["approval_summary"]["clipped_gross_lots"], 0\n        )\n        self.assertGreater(\n            strict["corporateRisk"]["approval_summary"]["clipped_gross_lots"], 0\n        )\n        for item in neutral["targets"]:\n            self.assertLessEqual(\n                abs(item["target_position_lots"]),\n                abs(item["strategy_target_position_lots"]),\n            )\n            self.assertGreaterEqual(\n                item["target_position_lots"] * item["strategy_target_position_lots"],\n                0,\n            )\n        neutral_by_contract = {item["contract_id"]: item for item in neutral["targets"]}\n        for strict_item in strict["targets"]:\n            neutral_item = neutral_by_contract[strict_item["contract_id"]]\n            self.assertLessEqual(\n                abs(strict_item["target_position_lots"]),\n                abs(neutral_item["target_position_lots"]),\n            )\n            self.assertGreaterEqual(\n                strict_item["target_position_lots"] * neutral_item["target_position_lots"],\n                0,\n            )\n''',
)
