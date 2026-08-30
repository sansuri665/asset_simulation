# 原油短期策略类型架构

> 状态：设计与研究候选，不是当前默认竞技运行事实  
> 分支基线：`feature/oil-short-calendar-spread-v02`  
> 当前第二策略候选：`asset-simulation-oil-calendar-spread-strategy-v0.2.2`  
> 当前 PM style hard gate：`audit_oil_calendar_spread_style_same_state_acceptance.py`（开发／验证 8/8 PASS）  
> 下一开发 Gate：Pair Execution Adapter

## 1. 顶层分类

```text
品种 / Commodity
→ 时间尺度 / Time Scale
→ 策略类型 / Strategy Type
```

当前：

```text
Crude Oil
└─ Short Horizon
   ├─ Directional
   │  └─ forecast-location + continuation overlay
   └─ Relative Value
      └─ current-main / adjacent-next calendar spread
```

`continuation / reversion`、换手、耐心、选择性和资金部署是 PM 的投资表达，不作为顶层 Strategy Type。策略类型按风险与 PnL 对象区分：方向策略赚绝对价格方向；跨期策略赚两张真实合约的美元价差变化。

## 2. 第一策略：成熟 reference implementation

`oil_trading_strategy_v8` / v1.6.0 继续作为短期方向型 reference implementation，已经具备预测、PM、construction、thesis、strategy mandate、交易执行、正式账户和多 Seed 经济校准。

本分支不修改第一策略行为基线；每轮 CI 都继续运行方向策略 unit suite 与 economic smoke。

## 3. 第二策略演进

### v0.1.2 — 金融对象与硬边界

历史 reference engine 解决：spread 对象、+1/-1 两腿、不创建 synthetic security、相邻两腿身份、双腿容量、残腿、到期时间 owner、thesis 成熟时序和 PnL 恒等式。它保留为 invariant reference，不静默重写。

### v0.2.1 — 多策略 owner 与专属 PM style

增加显式 taxonomy、`oil_strategy_book_v1`、`oil_calendar_spread_research_v1`、8 维 Calendar Spread PM 风格，以及通用 construction capability 的 spread-specific 解释。

### v0.2.2 — 恢复真实 visible spread history

经济审计发现旧 spread reader 没有继承 `monthly.year/month` 到 `weekly` 子节点，真实周线因此被丢弃。

v0.2.2 增加 strategy-local metadata-only adapter：

```text
monthly.year/month
→ inherit into weekly children
```

不改变价格、成交量、OI、limits、market identity、cutoff，也不写回市场。

修复后开发 Seed 0—15 与验证 Seed 100—115、各两年共 1536 个真实半月截点全部拥有 12 周对齐 spread history；forecast、visible curve、momentum、mean reversion 四类 signal component 全部 1536/1536 非零。

## 4. Strategy Book

```text
Directional Book
  OIL-3005 +100

Calendar Spread Book
  OIL-3005 +50
  OIL-3009 -50

Portfolio / Formal Account Aggregate
  OIL-3005 +150
  OIL-3009 -50
```

Calendar Spread 只能看自己的 book，不能把账户汇总仓位误认成自己的残腿。

Strategy Book 不拥有现金、保证金、利息、融资或独立强平，只负责策略仓位所有权与未来 attribution。Formal Account 仍是现金与法定持仓真相。

## 5. 第二策略专属 PM style

同一位策略研究负责人通过 strategy-type-specific deterministic projection 得到：

```text
curve_continuation_reversion
forecast_vs_visible_curve
dislocation_selectivity
capital_deployment
adjustment_tempo
rebalance_activity
holding_patience
forecast_horizon
```

方向策略与跨期策略画像相关但不机械相同；默认兼容负责人八维严格为 50。无偏好总分、Alpha 分或质量分。

### Broad Same-State 行为验收

中性 PM 路径冻结每个真实截点的 market、forecast 与 research-book state，然后在同一状态同时比较某一轴 10 分与 90 分。pair identity 改变时 research book 清零。

开发与验证各 16 Seed × 2 年 = 768 截点；三个条件轴要求每个分区至少 10 个自然事件。

```text
Development: 8 / 8 PASS
Validation:  8 / 8 PASS
Overall:     PASS
```

严格自然事件数：forecast vs visible curve 154 / 138；momentum vs mean reversion 406 / 401；same-direction shrink 40 / 30。收益和 markout 不进入 style hard gate。

## 6. Construction capability

继续复用同一位 PM 的三项通用工艺能力：

| 通用能力 | Calendar Spread 解释 |
|---|---|
| `exposure_construction` | spread exposure construction |
| `transition_planning` | pair transition planning |
| `contract_lifecycle_planning` | curve lifecycle planning |

前两项进入 `ideal spread target → bounded symmetric construction error → submitted spread target`，不能制造腿偏好、从零创造方向或跨零反向，并始终保持 `target_main + target_next = 0`。

`contract_lifecycle_planning` 当前只记录确定性有界 error，不改目标。正式 multi-turn spread roll scheduler owner 尚未存在前，不能让策略自己偷偷决定换月。

## 7. 正式竞技接入前的剩余 Gate

### Gate A — Pair Execution Adapter

**当前下一阶段。**

一份 pair mandate 必须成为交易部可执行的两条真实腿，并明确：

- 同一 pair 的腿间关联；
- 两个周执行窗口；
- trader ability / style 只影响 completion 与可避免成本；
- 临时 legging 与 remediation；
- 强制减险优先级；
- 两腿 fill/cost 返回 Strategy Book attribution；
- 禁止 synthetic spread fill。

### Gate B — Strategy Book Settlement Ledger

```text
strategy requests
→ optional future internal netting
→ market orders
→ realized fills
→ allocation back to strategy books
→ formal account named-contract position
```

内部净额只能优化外部成交，不能删除策略所有权。

### Gate C — Portfolio Risk + Investment Decision

第二策略真正上线后，`portfolioRisk = dormant_single_strategy` 必须结束。Investment Decision 至少拥有原油总授权、两个策略各自额度、组合 gross/margin/liquidity、组合回撤/集中度、策略停用/只减仓。

策略 risk mandate 先裁剪自己的意图；portfolio risk 只能继续缩小，不能放大。

### Gate D — Formal Economic Calibration

Pair Execution 与 settlement 完成后再做：

1. 开发／验证分离跨 Seed 回放；
2. 正向、反向、平坦与快速结构切换；
3. forecast alpha / curve momentum / mean reversion 单因子和组合归因；
4. spread / residual-direction / carry / cost 恒等式；
5. legging / remediation 压力测试；
6. PM 风格的环境差异，而不是单一风格长期统治；
7. construction 0/50/100 只改变方案误差，不形成机械 Alpha 排名；
8. Directional + Calendar Spread 同时存在时 account/book/portfolio 三层逐回合对账；
9. 第一方向策略单独运行保持基线不变。

只有这些门禁通过，才建议接入 `OilInvestmentCompetitionSession`。

## 8. 当前分支边界

已实现：Strategy Book、v0.2.2 taxonomy/config/contract、dedicated 8-axis PM style、construction capability、visible-history metadata adapter、自然事件 identification、broad same-state 8/8 行为验收、v0.1.2/v0.2.1/v0.2.2 并行回归测试、第一方向策略回归保护。

故意未实现：四机构竞技接入、Pair Execution trader runtime、formal-account strategy subledger、portfolio risk、多策略资本分配 UI、internal order netting、带真实成交成本的 Calendar Spread 长样本收益校准。

现在第二策略已经从“研究接口正确”进入“策略研究行为可辨识”；下一道真正的工程门槛是 **Pair Execution Adapter**。
