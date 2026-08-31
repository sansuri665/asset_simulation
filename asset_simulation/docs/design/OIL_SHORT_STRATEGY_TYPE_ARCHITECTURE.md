# 原油短期策略类型架构

> 状态：设计与研究候选，不是当前默认竞技运行事实  
> 分支：`feature/oil-short-calendar-spread-v02`  
> 当前第二策略：`asset-simulation-oil-calendar-spread-strategy-v0.2.3`  
> 当前真实执行：`asset-simulation-oil-calendar-spread-pair-execution-v0.1.1`  
> PM style hard gate：开发／验证 8/8 PASS  
> Pair Execution Gate A：连续真实路径 PASS  
> 下一 Gate：**Strategy Book Settlement Ledger**

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

`continuation / reversion`、换手、耐心、选择性和资金部署属于 PM 投资表达，不作为顶层 Strategy Type。策略类型按风险与 PnL 对象区分：方向策略赚绝对价格方向；跨期策略赚两张真实合约的美元价差变化。

## 2. 第一策略：成熟 reference implementation

`oil_trading_strategy_v8` / v1.6.0 继续作为短期方向型 reference implementation，具备预测、PM、construction、thesis、strategy mandate、交易执行、正式账户和多 Seed 经济校准。

本分支不改第一策略行为路径；每轮 CI 继续运行 Directional unit suite 与 economic smoke。

## 3. 第二策略版本演进

### v0.1.2 — 金融对象与硬边界

冻结 spread 对象、真实 +1/-1 两腿、相邻合约身份、容量、残腿、到期 owner、thesis 成熟时序与 PnL 恒等式。保留为历史 invariant reference。

### v0.2.1 — 多策略 owner 与专属 PM

增加 taxonomy、`oil_strategy_book_v1`、`oil_calendar_spread_research_v1`、八维 Calendar Spread PM 风格，以及通用 construction capability 的 spread-specific 解释。

### v0.2.2 — 恢复真实 visible spread history

旧 reader 没有从父 `monthly.year/month` 继承到 `weekly` 子节点，真实周线因此被丢弃，visible curve / momentum / mean reversion 在真实 payload 上退化为 0。

v0.2.2 增加 strategy-local metadata-only adapter；修复后开发 Seed 0—15 与验证 Seed 100—115、各两年共 1536 个截点全部拥有 12 周对齐 spread history，四类 signal component 全部非零。

### v0.2.3 — Construction / Thesis owner 边界修复

真实成交形成非零 Strategy Book 后发现：旧 construction helper 即使误差为 0，也会在理想目标反向时先把 submitted target 改成 0，重复执行 thesis 的 reversal discipline。

v0.2.3 冻结：

```text
zero construction error
→ submitted target == ideal target

thesis
→ 独占 reversal exit discipline

responsiveness
→ 决定本回合向 0 退出多快
```

因此 score-100 construction 在非零持仓与 reversal 状态都重新成为真正的恒等映射；低能力的非零工艺误差仍不能自行跨零制造反向方向。

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

Calendar Spread 只能读取自己的 book，不能把账户汇总仓位误认成自己的 residual。

Strategy Book 不拥有现金、保证金、利息、融资或独立强平；它只负责策略仓位 owner 与未来 attribution。Formal Account 仍是现金与法定净持仓真相。

## 5. 第二策略专属 PM style

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

同一位 PM 的 Directional 与 Calendar Spread 画像相关但不机械相同；默认兼容负责人八维严格 50。不存在偏好总分、Alpha 分或 PM 质量分。

Broad Same-State 验收：

```text
Development: 8 / 8 PASS
Validation:  8 / 8 PASS
Overall:     PASS
```

严格自然事件数：forecast vs visible curve 154 / 138；momentum vs mean reversion 406 / 401；same-direction shrink 40 / 30。收益和 markout 不进入 style gate。

## 6. Construction capability

复用同一位 PM 的三项工艺能力：

| 通用能力 | Calendar Spread 解释 |
|---|---|
| `exposure_construction` | spread exposure construction |
| `transition_planning` | pair transition planning |
| `contract_lifecycle_planning` | curve lifecycle planning |

前两项只产生确定性有界 proposal error，不创造 Alpha。`contract_lifecycle_planning` 暂时 diagnostic-only，等待正式 multi-turn spread lifecycle / roll scheduler。

v0.2.3 的额外不变量是：**当 construction error = 0 时，construction 必须完全透明，不得替 thesis 做任何 reversal 决策。**

## 7. Gate A — Real Pair Execution：已实现

当前执行候选 `oil_calendar_spread_pair_execution-v0.1.1` 把 strategy-level risk-approved pair mandate 转为两条真实命名合约成交。

### 7.1 无 look-ahead schedule

H0 只用决策时已经可见的 Main / Next weekly liquidity 冻结未来两个执行窗口权重。未来实现的 weekly volume 只能影响该窗口已经发生后的 market impact / slippage，绝不能回写 schedule。

v0.1.0 因使用未来周成交量分配 Week 1 / Week 2 被保留为有缺陷原型，不作为当前基线。

### 7.2 Trading Desk owner

`oil_execution_desk_v2` 继续拥有 price execution、impact control、liquidity scheduling、completion reliability、roll coordination、fee efficiency、urgency、passive preference、window timing。

交易员可以少完成策略批准量、改善或恶化可避免成本，但不能扩大 mandate、改变方向、扩大 hard limit 或制造 Alpha。

### 7.3 真实两腿与成本

每个 weekly window 的 new pair 都保持：

```text
+N Main / -N Next
```

或反向。两腿各自记录 fill、OHLC4 benchmark、all-in price、spread、square-root impact 与 fee；不存在 synthetic spread fill。

Residual remediation 优先且属于 mandatory risk reduction；绕过普通 completion penalty，但仍受每腿 hard limit。

硬限额按真实 gross turnover，而不是净 delta。

Pair entry price 只使用 `new_pair` fills，不混入 remediation。

### 7.4 连续真实路径验收

冻结审计：Seeds `0,42,99,197`，每个 Seed 1 年，共 96 半月回合。

| 指标 | 结果 |
|---|---:|
| 有 pair 请求回合 | 95 |
| 实际 pair 成交回合 | 95 |
| requested spread units | 2,283 |
| executed spread units | 2,283 |
| gross real-leg turnover | 4,566 lots |
| execution cost | $105,154.53 |
| spread cost | $69,735.00 |
| slippage cost | $24,004.53 |
| net fee | $11,415.00 |
| hard invariant violations | **0** |

审计使用中性兼容 Trading Desk，所以 completion 为 100%、TCA value added 为 0；执行能力高低对 same-order completion 与成本的差异由专项测试独立验证。

Gate A 现在可以视为**连续真实周路径可执行**，但尚未完成会计 settlement。

## 8. 下一 Gate — Strategy Book Settlement Ledger

当前 execution report 已输出：

```text
real leg fills
+ per-leg execution costs
+ ending Strategy Book position preview
```

下一阶段必须把 preview 变成正式双重记账：

```text
strategy decision
→ real strategy fills
→ Strategy Book ledger update
→ strategy-level PnL attribution
        ↓
portfolio aggregation
        ↓
Formal Account named-contract positions / cash / margin
```

核心问题不是“把 dict 改掉”，而是同时保持：

- Strategy Book 的策略所有权；
- Formal Account 的真实净仓；
- 现金与保证金只结算一次；
- 两个策略未来可以在同一合约上反向而不丢失 attribution；
- internal netting 若以后加入，只优化外部订单，不能消灭策略 fill owner。

## 9. 后续 Gate

### Gate C — Portfolio Risk + Investment Decision

第二策略真正上线后，`portfolioRisk = dormant_single_strategy` 必须结束。Investment Decision 至少需要拥有原油总授权、两个策略各自额度、组合 gross/margin/liquidity、组合 drawdown / concentration、策略停用与 reduce-only。

策略 risk 先裁剪自己的意图；portfolio risk 只能继续缩小，不能放大。

### Gate D — Formal Economic Calibration

Settlement 完成后再进行带真实 fills / costs / margin 的开发—验证分离回放，包括 spread / residual-direction / carry / cost 归因、legging/remediation、不同 PM 与 Trading Desk 人员生态、多策略逐回合对账，以及 Directional 单独运行的基线保护。

只有这些门禁通过，才建议把第二策略接入 `OilInvestmentCompetitionSession`。

## 10. 当前分支边界

已实现：Strategy Book owner、v0.2.3 strategy candidate、八维 dedicated PM style、construction capability、visible-history adapter、style development/validation acceptance、真实双腿 Pair Execution v0.1.1、无 look-ahead scheduling、真实 spread/slippage/fee、continuous fill-preview replay、历史版本并行回归、Directional regression protection。

仍未实现：Strategy Book 持久 settlement ledger、Formal Account 双策略 settlement、portfolio risk、多策略资本分配 UI、internal order netting、正式 calendar-spread lifecycle / roll scheduler、带真实账户的长样本收益校准、四机构竞技接入。

当前最合理的下一工程任务已经从 Pair Execution 前移为 **Gate B — Strategy Book Settlement Ledger**。
