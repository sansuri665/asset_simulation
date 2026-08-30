# 原油短期策略类型架构

> 状态：设计与研究候选，不是当前默认竞技运行事实  
> 分支基线：`feature/oil-short-calendar-spread-v02`  
> 当前第二策略候选：`asset-simulation-oil-calendar-spread-strategy-v0.2.2`  
> 目标：把“原油 → 短期 → 策略类型”冻结为可继续扩展的策略组织方式，同时保持第一方向策略行为不变。

## 1. 顶层分类

策略最高层 identity：

```text
品种 / Commodity
→ 时间尺度 / Time Scale
→ 策略类型 / Strategy Type
```

当前原油短期形成两个真实样板：

```text
Crude Oil
└─ Short Horizon
   ├─ Directional
   │  └─ forecast-location + continuation overlay
   └─ Relative Value
      └─ current-main / adjacent-next calendar spread
```

`continuation / reversion`、换手、耐心、选择性和资金部署是 PM 的投资表达，不作为顶层 Strategy Type。策略类型优先按风险与 PnL 对象区分：方向策略赚绝对价格方向；跨期策略赚两张真实合约的美元价差变化。

## 2. 第一策略：成熟 reference implementation

`oil_trading_strategy_v8` / v1.6.0 继续作为短期方向型 reference implementation。它已经具备：

- 发布预测 → PM 理想目标 → 构造误差 → thesis → strategy mandate → 交易执行 → 正式账户；
- 方向／合约选择／移仓／净调仓／周内往返归因；
- 现金、保证金、追保、强平、利息与融资；
- 多 Seed、不同预测能力、不同 PM 风格和行情 regime 的经济校准。

本分支不修改第一策略行为基线；每轮 CI 都继续运行方向策略 unit suite 与 economic smoke。

## 3. 第二策略演进

### v0.1.2 — 金融对象与硬边界

历史 reference engine 已经解决：

- spread 定义 `P_main - P_adjacent_next`；
- +1 unit = +1 Main / -1 Adjacent Next；
- 不创建 synthetic security；
- 相邻两腿身份校验；
- 双腿容量、残腿、到期时间 owner；
- thesis 成熟时序；
- spread / residual-direction / carry PnL 恒等式。

它仍保留为 invariant reference，不静默重写。

### v0.2.1 — 多策略 owner 与专属 PM style

增加：

- 显式 taxonomy；
- `oil_strategy_book_v1`；
- dedicated owner `oil_calendar_spread_research_v1`；
- 8 维 Calendar Spread PM 风格；
- 通用 construction capability 的 spread-specific 解释。

### v0.2.2 — 恢复真实 visible spread history

经济审计发现 v0.1.2/v0.2.1 的 visible-curve 历史读取存在 schema mismatch：市场 owner 把 `year/month` 放在父 monthly node，而旧 spread reader 要求 weekly child 自带坐标，因此真实周线被丢弃。

v0.2.2 增加 strategy-local metadata-only adapter：

```text
monthly.year/month
→ inherit into weekly children
```

不改变价格、成交量、OI、limits、market identity、cutoff，也不写回市场。

修复后开发 Seed 0—15 与验证 Seed 100—115、各两年共 1536 个真实半月截点全部拥有 12 周对齐 spread history；forecast、visible curve、momentum、mean reversion 四类 signal component 全部 1536/1536 非零。

## 4. Strategy Book

每个策略拥有自己的真实命名合约仓位 book：

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

Calendar Spread 只能看自己的 `+50 / -50`，不能把账户汇总后的 `+150 / -50` 解释为“50组 spread + 100手残腿”并错误整改方向策略仓位。

Strategy Book 不是第二账户：

- 不拥有现金；
- 不拥有保证金；
- 不产生利息或融资；
- 不独立强平；
- 只负责策略仓位所有权与未来 PnL attribution。

Formal Account 仍是现金与法定持仓真相。

## 5. 第二策略专属 PM style

同一个已任命策略研究负责人，通过 strategy-type-specific deterministic projection 得到 Calendar Spread 专属 8 维蛛网：

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

`near_month_focus` 不进入第二策略专属 radar，因为 +1/-1 pair identity 已经拥有腿关系。

### 行为验收

v0.2.2 使用 broad same-state 审计：中性 PM 路径冻结当期 market、forecast 和 research-book state，然后在同一状态同时比较某一轴 10 分与 90 分。

开发与验证各 16 Seed × 2 年 = 768 个真实截点；pair identity 改变时 research book 清零。三个条件轴只在自然识别事件上比较，并要求每个分区至少 10 个事件。

结果：

```text
Development: 8 / 8 PASS
Validation:  8 / 8 PASS
Overall:     PASS
```

其中自然严格冲突事件：

- forecast vs visible curve：开发 154，验证 138；
- momentum vs mean reversion：开发 406，验证 401；
- same-direction shrink（holding patience）：开发 40，验证 30。

收益和 markout 不作为 style gate。

## 6. Construction capability

继续复用同一位 PM 的三项通用工艺能力：

| 通用能力 | Calendar Spread 解释 |
|---|---|
| `exposure_construction` | spread exposure construction |
| `transition_planning` | pair transition planning |
| `contract_lifecycle_planning` | curve lifecycle planning |

前两项进入：

```text
ideal spread target
→ bounded symmetric construction error
→ submitted spread target
```

不能制造腿偏好、不能从零创造方向、不能跨零反向，并始终保持 `target_main + target_next = 0`。

`contract_lifecycle_planning` 当前只记录确定性有界 error，不改目标。正式 multi-turn spread roll scheduler owner 尚未存在前，不能让策略自己偷偷决定换月。

## 7. 正式竞技接入前的剩余 Gate

### Gate A — Pair Execution Adapter

下一阶段首要任务。

一份 pair mandate 必须成为交易部可执行的两条真实腿，并明确：

- 同一 pair 的腿间关联；
- 两个周执行窗口如何安排；
- trader ability / style 只影响 completion 与可避免成本；
- 临时 legging 与 remediation；
- 强制减险优先级；
- 两腿 fill/cost 返回 Strategy Book attribution；
- 禁止创建 synthetic spread fill。

### Gate B — Strategy Book Settlement Ledger

正式账户按真实命名合约净持仓结算，同时把 fills/PnL 回分到 strategy books：

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

第二策略真正上线后，`portfolioRisk = dormant_single_strategy` 必须结束。Investment Decision 至少拥有：

- 原油总资本授权；
- Directional 与 Calendar Spread 各自额度；
- 组合 gross / margin / liquidity；
- 组合回撤与集中度；
- 策略停用／只减仓。

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
9. 第一方向策略单独运行结果保持基线不变。

只有这些门禁通过，才建议接入 `OilInvestmentCompetitionSession`。

## 8. 当前分支边界

已实现：

- Strategy Book；
- v0.2.2 taxonomy / config / contract；
- dedicated 8-axis PM style；
- construction capability 接入；
- visible-history metadata adapter；
- 自然事件 identification scan；
- broad same-state 开发／验证 8/8 行为验收；
- v0.1.2、v0.2.1、v0.2.2 并行回归测试；
- 第一方向策略回归保护。

故意未实现：

- 四机构竞技接入；
- Pair Execution trader runtime；
- formal-account strategy subledger；
- portfolio risk 激活；
- 多策略资本分配 UI；
- internal order netting；
- 带真实成交成本的 Calendar Spread 长样本收益校准。

现在第二策略已经从“研究接口正确”进入“策略研究行为可辨识”；下一道真正的工程门槛是 **Pair Execution Adapter**。
