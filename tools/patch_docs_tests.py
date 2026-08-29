from __future__ import annotations

from pathlib import Path
import textwrap

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

def write(rel: str, content: str) -> None:
    p = path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).lstrip('\n'), encoding='utf-8')

# Directional strategy docs: capital allocation is committee-owned; PM deployment is a risk preference.
rep(
    'asset_simulation/docs/current/OIL_TRADING_STRATEGY.md',
    '策略研究 `asset-simulation-oil-strategy-research-v0.2.1`、策略风控 `asset-simulation-oil-strategy-risk-v0.1.0`、公司风控 `asset-simulation-corporate-risk-control-v0.2.0`、交易部 `asset-simulation-oil-execution-desk-v0.2.0`、交易策略 `asset-simulation-oil-trading-strategy-v1.3.0`',
    '策略研究 `asset-simulation-oil-strategy-research-v0.2.2`、策略风控 `asset-simulation-oil-strategy-risk-v0.1.0`、公司风控 `asset-simulation-corporate-risk-control-v0.2.0`、交易部 `asset-simulation-oil-execution-desk-v0.2.0`、交易策略 `asset-simulation-oil-trading-strategy-v1.3.1`',
)
rep(
    'asset_simulation/docs/current/OIL_TRADING_STRATEGY.md',
    '→ 投委会资金授权 × 负责人资金部署倾向\n→ 命名合约理想净持仓\n',
    '→ 风控读取负责人资金表达倾向并提出建议，投委会独立授权策略资本\n→ 策略在委员会已授权资本与市场容量内形成命名合约理想净持仓\n',
)
rep(
    'asset_simulation/docs/current/OIL_TRADING_STRATEGY.md',
    '| 资金部署 `capital_deployment` | 在已获配策略资本内愿意部署多少 | 25%—75% |',
    '| 资金部署 `capital_deployment` | 风险表达偏好；进入策略风控压力与投委会建议，不直接乘授权资本 | 0—100偏好分 |',
)
old_block = '''决策委员会的资金授权已经是独立运行字段。当前单策略 Demo 由系统代理授权当期公司权益的100%，玩家还不能操作；风控同时发布建议资金比例，但该建议不会自动取代委员会决定。资金部署只作用于 `allocated_strategy_capital_usd`，市场限仓不乘人员偏好：

```text
资金部署预算 = 已获配策略资本 × 资金部署比例
资金容量 = 资金部署预算 × 合约权重 ÷ 单手初始保证金
市场硬容量 = min(动态单合约限仓, 组合总限仓 × 合约权重)
理想容量 = min(资金容量, 市场硬容量)
最终目标 = 理想目标 → 观点失效状态 → 单策略风控 → 公司总风控
```

每个合约明确发布 `binding_capacity` 为资金部署、市场限仓或停止新增。15%初始保证金率目前只是把获配资本换算成手数的第一版尺度，不是正式 VaR；12%维持保证金率、市场限仓和费用不是策略负责人的偏好，公司风控则由另行任命的 CRO 六维风险偏好提出。未来品种负责人和投委会发布真实额度后，无需再把整家公司权益交给单策略代理。
'''
new_block = '''决策委员会的资金授权是唯一资本 allocation owner。当前单策略 Demo 仍由系统代理决定授权比例，玩家还不能操作；风控发布建议资金比例，但该建议不会自动取代委员会决定。`capital_deployment` 只表达负责人愿意承担多大风险、进入策略风险压力与投委会建议，不再在策略内部把已授权资本再次乘一个人员百分比：

```text
委员会授权资本 = company equity × authorized capital pct
委员会资本容量 = 委员会授权资本 × 合约权重 ÷ 单手初始保证金
市场硬容量 = min(动态单合约限仓, 组合总限仓 × 合约权重)
理想容量 = min(委员会资本容量, 市场硬容量)
最终目标 = 理想目标 → 观点失效状态 → 单策略风控 → 公司总风控
```

每个合约明确发布 `binding_capacity` 为委员会授权资本、市场限仓或停止新增。15%初始保证金率目前只是把投委会额度换算成手数的第一版尺度，不是正式 VaR；12%维持保证金率、市场限仓和费用不是策略负责人的偏好。`capital_deployment` 的风险含义由策略风控读取，资本额度由投委会决定，避免 PM 在委员会授权后又成为第二个隐含资本分配者。
'''
rep('asset_simulation/docs/current/OIL_TRADING_STRATEGY.md', old_block, new_block)

# Forecast docs: explain the cooler hidden-truth transfer.
rep(
    'asset_simulation/docs/current/OIL_SHORT_TERM_FORECAST.md',
    '> 代码基线：`asset-simulation-oil-short-term-forecast-v0.2.0`',
    '> 代码基线：`asset-simulation-oil-short-term-forecast-v0.2.1`',
)
marker = '永久方向偏置和期限结构偏置仍保持中性基准。研究风格不改变六维能力，不直接放大仓位，也不越过 PM、风控和交易部。\n'
insert = marker + '''\n专业能力对隐藏未来形状的合成混合使用二次传导 `truth_mix = (skill/100)^2`。该混合仅用于制造可区分的合成研究质量，不是玩家可见信息，也不进入策略仓位公式。相比旧 smoothstep，普通和良好研究员得到的隐藏路径形状显著降低：15分约2.25%、50分25%、70分49%、100分100%，从源头压低“研究能力分 → 可交易 alpha”的过强斜率。\n'''
rep('asset_simulation/docs/current/OIL_SHORT_TERM_FORECAST.md', marker, insert)

# Calendar-spread docs: same PM score, no shared capital haircut.
rep(
    'asset_simulation/docs/design/OIL_CALENDAR_SPREAD_STRATEGY.md',
    '- `capital_deployment`\n',
    '- `capital_deployment`（仅作为风险表达偏好；不直接乘委员会授权资本）\n',
)
cap_marker = '''risk capacity 取以下最小值：

```text
conservative pair margin capacity
market leg position capacity
all-contract gross cap / 2
stressed visible dollar-spread volatility capacity
```
'''
cap_insert = cap_marker + '''\n其中 pair margin 与 spread-volatility 风险预算直接以 `authorized_strategy_capital_usd` 为上限资本参考。共享 PM 的 `capital_deployment` 只留给风险审查／未来投委会使用，跨期策略不会再将委员会额度乘一个通用 PM 百分比，避免方向策略校准误伤相对价值策略。\n'''
rep('asset_simulation/docs/design/OIL_CALENDAR_SPREAD_STRATEGY.md', cap_marker, cap_insert)

# Existing PM tests.
rep(
    'asset_simulation/tests/test_oil_strategy_research.py',
    '''        self.assertEqual(\n            50.0,\n            policy["risk"]["capital_deployment_pct_of_allocated_equity"],\n        )\n''',
    '''        self.assertEqual(50.0, policy["risk"]["capital_deployment_score"])\n        self.assertEqual(\n            "risk_preference_input_only",\n            policy["risk"]["capital_deployment_semantics"],\n        )\n        self.assertNotIn(\n            "capital_deployment_pct_of_allocated_equity", policy["risk"]\n        )\n''',
)
rep(
    'asset_simulation/tests/test_oil_strategy_research.py',
    '''        self.assertLess(\n            conservative_policy["risk"][\n                "capital_deployment_pct_of_allocated_equity"\n            ],\n            aggressive_policy["risk"][\n                "capital_deployment_pct_of_allocated_equity"\n            ],\n        )\n''',
    '''        self.assertEqual(0.0, conservative_policy["risk"]["capital_deployment_score"])\n        self.assertEqual(100.0, aggressive_policy["risk"]["capital_deployment_score"])\n        self.assertNotIn(\n            "capital_deployment_pct_of_allocated_equity", conservative_policy["risk"]\n        )\n        self.assertNotIn(\n            "capital_deployment_pct_of_allocated_equity", aggressive_policy["risk"]\n        )\n''',
)

# Directional runtime tests.
rep(
    'asset_simulation/tests/test_oil_trading_strategy.py',
    '''        self.assertEqual(\n            50.0,\n            first["riskBudget"][\n                "capital_deployment_pct_of_allocated_equity"\n            ],\n        )\n''',
    '''        self.assertEqual(\n            first["riskBudget"]["allocated_strategy_capital_usd"],\n            first["riskBudget"]["capital_capacity_budget_usd"],\n        )\n        self.assertEqual(\n            "investment_decision_committee",\n            first["riskBudget"]["capital_capacity_owner"],\n        )\n        self.assertFalse(\n            first["informationPolicy"][\n                "pm_capital_deployment_directly_scales_authorized_capital"\n            ]\n        )\n        self.assertNotIn(\n            "capital_deployment_pct_of_allocated_equity", first["riskBudget"]\n        )\n''',
)
rep(
    'asset_simulation/tests/test_oil_trading_strategy.py',
    '''                        "market_position_limit",\n                        "capital_deployment_budget",\n                        "new_trades_closed",\n''',
    '''                        "market_position_limit",\n                        "committee_authorized_capital",\n                        "new_trades_closed",\n''',
)

# Cross-strategy regression: PM deployment score cannot shrink calendar-spread authorized capital.
p = path('asset_simulation/tests/test_oil_calendar_spread_strategy.py')
text = p.read_text(encoding='utf-8')
needle = '    def test_pm_continuation_reversion_style_changes_visible_curve_component(self) -> None:\n'
if text.count(needle) != 1:
    raise SystemExit('calendar spread insertion point not found exactly once')
method = '''    def test_pm_capital_deployment_score_does_not_haircut_authorized_pair_capital(self) -> None:\n        low = build_default_oil_strategy_research_profile()\n        low.pop("profile_hash")\n        low["style_radar"]["capital_deployment"] = 0.0\n        high = deepcopy(low)\n        high["style_radar"]["capital_deployment"] = 100.0\n\n        low_decision = build_oil_calendar_spread_research_decision(\n            _market(),\n            _forecast(),\n            authorized_strategy_capital_usd=10_000_000.0,\n            strategy_research_profile=low,\n        )\n        high_decision = build_oil_calendar_spread_research_decision(\n            _market(),\n            _forecast(),\n            authorized_strategy_capital_usd=10_000_000.0,\n            strategy_research_profile=high,\n        )\n        low_capacity = low_decision["strategyRiskAdapter"]["capacity"]\n        high_capacity = high_decision["strategyRiskAdapter"]["capacity"]\n        self.assertEqual(10_000_000.0, low_capacity["capital_capacity_budget_usd"])\n        self.assertEqual(\n            low_capacity["capital_capacity_budget_usd"],\n            high_capacity["capital_capacity_budget_usd"],\n        )\n        self.assertEqual(\n            low_capacity["risk_capacity_units"],\n            high_capacity["risk_capacity_units"],\n        )\n        self.assertNotIn("capital_deployment_pct_of_authorized_capital", low_capacity)\n\n'''
p.write_text(text.replace(needle, method + needle), encoding='utf-8')

write(
    'asset_simulation/tests/test_directional_calibration_governance.py',
    r'''
    from __future__ import annotations

    from copy import deepcopy
    import unittest
    from unittest.mock import patch

    from asset_simulation.model import oil_short_term_forecast, oil_trading_strategy
    from asset_simulation.model.registry import load_registered_assets


    class DirectionalCalibrationGovernanceTests(unittest.TestCase):
        def test_minimum_direction_forecast_z_is_required_fail_closed(self) -> None:
            assets = load_registered_assets()
            broken = deepcopy(assets)
            broken["oil_trading_strategy_config"] = deepcopy(
                assets["oil_trading_strategy_config"]
            )
            broken["oil_trading_strategy_config"]["thesis_invalidation"].pop(
                "minimum_direction_forecast_z"
            )
            with patch.object(oil_trading_strategy, "load_registered_assets", return_value=broken):
                with self.assertRaisesRegex(ValueError, "minimum_direction_forecast_z is required"):
                    oil_trading_strategy._validate_registered_assets()

        def test_truth_mix_is_quadratic_and_bounded(self) -> None:
            expected = {
                0.0: 0.0,
                15.0: 0.0225,
                50.0: 0.25,
                70.0: 0.49,
                100.0: 1.0,
            }
            for score, value in expected.items():
                self.assertAlmostEqual(value, oil_short_term_forecast._skill_truth_mix(score))
            self.assertEqual(0.0, oil_short_term_forecast._skill_truth_mix(-10.0))
            self.assertEqual(1.0, oil_short_term_forecast._skill_truth_mix(110.0))


    if __name__ == "__main__":
        unittest.main()
    ''',
)

write(
    'asset_simulation/tests/test_oil_strategy_thesis_calibration.py',
    r'''
    from __future__ import annotations

    from copy import deepcopy
    import unittest

    from asset_simulation.model.oil_strategy_thesis import evaluate_oil_strategy_thesis_state
    from asset_simulation.model.registry import load_registered_assets


    class OilStrategyThesisCalibrationTests(unittest.TestCase):
        @classmethod
        def setUpClass(cls) -> None:
            cls.policy = deepcopy(
                load_registered_assets()["oil_trading_strategy_config"]["thesis_invalidation"]
            )

        def _decision(self, *, center: float, uncertainty: float) -> dict[str, object]:
            anchor = 100.0
            return {
                "thesisInvalidation": {"policy": self.policy, "stateBefore": {}},
                "targets": [
                    {
                        "contract_id": "OIL-3005",
                        "role": "main",
                        "anchor_price_usd": anchor,
                        "signal": 0.5,
                        "horizon_components": [
                            {
                                "selected_horizon_weeks": 2,
                                "target_week": "2030-01-W4",
                                "forecast_close_usd": center,
                                "confidence_low_usd": center * 0.98,
                                "confidence_high_usd": center * 1.02,
                                "uncertainty_log": uncertainty,
                            }
                        ],
                    }
                ],
            }

        @staticmethod
        def _end(price: float) -> dict[str, object]:
            return {"curve": {"contracts": [{"contract_id": "OIL-3005", "price_usd": price}]}}

        def test_low_conviction_direction_disagreement_does_not_count_as_miss(self) -> None:
            outcome = evaluate_oil_strategy_thesis_state(
                self._decision(center=100.5, uncertainty=0.02),
                self._end(99.0),
            )
            evaluation = outcome["evaluations"][0]
            self.assertLess(evaluation["forecast_direction_z"], 0.35)
            self.assertFalse(evaluation["direction_miss_eligible"])
            self.assertFalse(evaluation["direction_miss"])

        def test_material_direction_disagreement_still_counts_as_miss(self) -> None:
            outcome = evaluate_oil_strategy_thesis_state(
                self._decision(center=102.0, uncertainty=0.02),
                self._end(99.0),
            )
            evaluation = outcome["evaluations"][0]
            self.assertGreaterEqual(evaluation["forecast_direction_z"], 0.35)
            self.assertTrue(evaluation["direction_miss_eligible"])
            self.assertTrue(evaluation["direction_miss"])


    if __name__ == "__main__":
        unittest.main()
    ''',
)
