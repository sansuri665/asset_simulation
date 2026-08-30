# 原油短期策略类型架构

> 状态：设计与研究候选，不是当前默认竞技运行事实  
> 分支基线：`feature/oil-short-calendar-spread-v02`  
> 目标：把“原油 → 短期 → 策略类型”冻结为可继续扩展的策略组织方式，同时保持第一方向策略行为不变。

## 1. 顶层分类

策略最高层 identity 按以下顺序组织：

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

`continuation / reversion`、换手、耐心、选择性和资金部署是 PM 的投资表达，不再作为顶层 Strategy Type。策略类型优先按风险与 PnL 对象区分：方向策略赚绝对价格方向；跨期策略赚两张真实合约的美元价差变化。

## 2. 第一策略：成熟 reference implementation

当前 `oil_trading_strategy_v8` / v1.6.0 继续作为短期方向型 reference implementation。它已经具备：

- 发布预测 → PM 理想目标 → 构造误差 → thesis → strategy mandate → 交易执行 → 正式账户；
- 方向／合约选择／移仓／净调仓／周内往返归因；
- 现金、保证金、追保、强平、利息与融资；
- 多 Seed、不同预测能力、不同 PM 风格和行情 regime 的经济校准。

本分支不修改第一策略行为基线。

## 3. 第二策略 v0.2：先冻结 owner，再谈竞技接入

v0.1.2 已经解决 spread 对象、相邻两腿、信息时序、双腿硬容量、残腿、到期时间、PnL 恒等式和 carry 输入边界。v0.2 增加三件基础设施。

### 3.1 显式策略 taxonomy

正式 identity：

```text
asset_class   = commodity
commodity     = crude_oil
instrument    = futures
time_scale    = short_horizon
strategy_type = relative_value_calendar_spread
```

策略 ID：

```text
oil.short.relative_value.calendar_spread.v1
```

这样后续第三策略不需要继续把 signal recipe 塞进最高层命名。

### 3.2 Strategy Book

正式多策略接入前，每个策略必须拥有自己的真实命名合约仓位 book：

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

Calendar Spread 只能看自己的 `+50 / -50`，不能把账户汇总后的 `+150 / -50` 解释为“50组 spread + 100手残腿”然后错误整改方向策略仓位。

Strategy Book 不是第二账户：

- 不拥有现金；
- 不拥有保证金；
- 不产生利息或融资；
- 不独立强平；
- 只负责策略仓位所有权与未来 PnL attribution。

Formal Account 仍是现金与法定持仓真相。

### 3.3 PM construction capability 接入

复用现有 PM 的三项构造能力，不增加 calendar-spread-specific Alpha 分：

| 通用能力 | Calendar Spread 解释 |
|---|---|
| `exposure_construction` | spread exposure construction |
| `transition_planning` | pair transition planning |
| `contract_lifecycle_planning` | curve lifecycle planning |

前两项在 v0.2 真正进入 `ideal target → submitted target`：两腿 construction error 对称合成为 spread-unit error，不能制造腿偏好，不能从零创造方向，也不能跨零反向。

`contract_lifecycle_planning` 在 v0.2 只记录确定性有界 error，不改变目标。原因是当前还没有独立 multi-turn spread roll scheduler owner；不能为了“让第三维生效”就在策略内部偷偷重算换月时间。

100 分兼容负责人三项误差均严格为零，因此相同输入必须复现 v0.1.2 目标路径。

## 4. 正式接入前必须继续完成的四层

### Gate A — Execution adapter

现在 paired mandate 与 fill validator 已正确，但还不是交易部完整 runtime。下一步应把一份 pair mandate 转成交易部可执行的两条真实订单，并明确：

- 同一 pair 的两腿执行关联；
- 两个周窗口如何分配；
- trader ability / style 如何影响可避免成本和 completion；
- 临时 legging 怎样进入 remediation；
- 强制减险不受普通执行风格完成率惩罚；
- 两腿成本和成交都必须返回 Strategy Book attribution。

不能通过创建 synthetic spread fill 绕过真实腿。

### Gate B — Strategy Book settlement ledger

正式账户继续按命名合约净持仓结算，但需要把成交和 PnL 按 strategy book 回分：

```text
strategy requested orders
→ optional portfolio/internal netting
→ market orders
→ realized fills
→ fill allocation back to strategy books
→ formal account named-contract position
```

内部净额只是执行优化，不能删除策略所有权。例如 S1 买 Main 100、S2 卖 Main 50 时，市场可以只买 50，但两个 strategy book 仍必须得到可审计的内部 fill allocation。

v0.2 当前明确不实现 portfolio internal netting。

### Gate C — Portfolio Risk + Investment Decision

出现第二策略后，`portfolioRisk = dormant_single_strategy` 不再成立。正式接入必须让 Investment Decision 至少拥有：

- 原油总资本授权；
- Directional 与 Calendar Spread 各自策略授权；
- 两策略共同占用的 gross、margin 和 liquidity 预算；
- 组合回撤与集中度；
- 策略停用／只减仓状态。

策略自己的 risk mandate 先裁剪自己的意图；portfolio risk 只能继续缩小，不能扩大任何策略目标。

### Gate D — Economic calibration

第二策略不能因为契约测试通过就直接加入竞技。最低应做：

1. 至少开发／验证分离的跨 Seed 回放；
2. 正向、反向、平坦和快速结构切换场景；
3. forecast spread alpha、visible curve momentum、mean reversion 的单因子与组合审计；
4. spread PnL、residual directional PnL、carry attribution 和成本恒等式；
5. 两腿成交容量、legging 和 remediation 压力测试；
6. PM 风格轴的环境差异，而不是单一风格长期统治；
7. construction 0/50/100 只能改变方案误差，不形成机械 Alpha 排名；
8. Directional + Calendar Spread 同时存在时，账户／book／portfolio 三层逐回合对账；
9. 第一方向策略单独运行结果逐项保持基线不变。

只有这些门禁通过，才建议接入 `OilInvestmentCompetitionSession`。

## 5. 这轮分支的边界

本分支实现：

- `oil_strategy_book_v1`；
- calendar spread v0.2 taxonomy / config / contract；
- strategy-book-aware v0.2 decision candidate；
- PM construction capability 在 spread-unit proposal 上的有界接入；
- 新旧 reference target 的 100 分兼容门禁；
- 策略 book 与账户汇总仓位隔离测试。

本分支故意不实现：

- 四机构竞技接入；
- portfolio risk 激活；
- 多策略资本分配 UI；
- 真实交易部 pair execution；
- formal-account strategy subledger；
- internal order netting；
- calendar spread 长样本收益校准。

这不是保守拖延，而是为了让第二策略第一次进入正式 runtime 时，持仓 owner、资金 owner、风险 owner 和执行 owner 已经分开。
