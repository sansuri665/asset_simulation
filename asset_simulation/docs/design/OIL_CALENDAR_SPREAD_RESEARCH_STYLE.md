# 原油短期跨期价差策略：策略研究特质体系

> 状态：第二策略 v0.2.2 研究候选；8 维行为开发／验证验收通过  
> Style owner：`oil_calendar_spread_research_v1`  
> Personnel owner：`oil_strategy_research_v2`  
> Construction capability owner：`oil_strategy_research_v2`  
> Visible-history adapter：`oil_calendar_spread_market_history_adapter`

## 1. 设计原则

第二策略不直接把方向策略的八维蛛网当作自己的蛛网，但也不创建第二位 PM。

```text
同一个已任命策略研究负责人
        ↓
通用 oil strategy personnel profile
        ↓
strategy-type-specific projection
        ↓
Calendar Spread dedicated style radar
        ↓
spread signal / target expression
```

同一个人可以在方向策略里明显偏趋势，而在跨期价差里只略偏趋势甚至偏回归；资本胃口、节奏、纪律和持有习惯仍保持相关。

专属 radar 是偏好，不是能力：没有总分，没有 Alpha 分，没有“高分更强”。未来表现只能来自真实历史 track record。

## 2. 八个专属风格维度

| 维度 | 低分端 | 高分端 | 当前作用 |
|---|---|---|---|
| `curve_continuation_reversion` | 价差回归 | 价差顺势 | visible curve momentum / mean-reversion 权重 |
| `forecast_vs_visible_curve` | 当前曲线证据主导 | 双腿预测主导 | forecast spread 与 visible curve 两个已计算信号的组合权重 |
| `dislocation_selectivity` | 广泛参与 | 等待大偏离 | signal deadband / 参与阈值 |
| `capital_deployment` | 保守部署 | 积极部署 | 已授权资本中的正常策略使用比例 |
| `adjustment_tempo` | 缓慢调整 | 快速调整 | spread target 调整速度 |
| `rebalance_activity` | 低频再平衡 | 主动再平衡 | 正常换手与策略刷新强度 |
| `holding_patience` | 快速兑现 | 耐心持有 | 目标缩小时保留旧 spread exposure 的黏性 |
| `forecast_horizon` | 偏 2 周 | 偏 4 周 | 短期双腿预测中的期限权重 |

`near_month_focus` 被明确移除。Calendar Spread 的交易对象已经规定 `+1 Main / -1 Adjacent Next`，再给 PM 一条近月集中偏好会和策略本体冲突；reference adapter 中固定为中性 50。

## 3. 两个核心专属哲学轴

### Forecast vs Visible Curve

信号先独立形成：

```text
forecast_spread_signal
visible_curve_signal
```

然后：

```text
raw_signal
= w_forecast × forecast_spread_signal
+ (1 - w_forecast) × visible_curve_signal
```

注册映射：

```text
score 0   → forecast weight 45%
score 50  → forecast weight 70%
score 100 → forecast weight 90%
```

默认 50 分保留中性 70/30 约定。该轴不能改写两个 component 本身、读取未来、绕过 deadband 或把能力变成信号奖励。

### Curve Continuation / Reversion

visible curve 内部拆成：

```text
curve momentum
vs
curve mean reversion
```

`curve_continuation_reversion` 只控制两类已经可见的曲线证据如何表达，不改变 forecast component。

## 4. v0.2.2：Visible Curve 真实输入修复

经济审计发现 v0.2.1 以前存在策略输入边界错误：

```text
oil_futures_overlay 实际发布：
monthly[{year, month, weekly:[{week, OHLC, ...}]}]

旧 calendar-spread reader 预期：
weekly[{year, month, week, ...}]
```

市场公开周线因此没有被 spread reader 正确识别，历史 spread 每回合只剩 decision-cutoff 一个点，导致 momentum、mean-reversion 和 visible-curve signal 在真实运行中长期为 0。

v0.2.2 不修改市场 owner，而增加 metadata-only strategy adapter：把父 monthly.year/month 继承到 weekly child。它不改变价格、volume、OI、limits、market identity、cutoff，也不写回市场。

修复后扫描开发 Seed 0—15 与验证 Seed 100—115、各 2 年共 1536 个真实半月截点：

- 1536 / 1536 都有 12 周对齐 history；
- forecast、visible curve、momentum、mean reversion 四类 signal 全部 1536 / 1536 非零；
- forecast 与 curve 自然反向：开发 369、验证 368；
- momentum 与 reversion 自然反向：开发 646、验证 654；
- 严格 `|signal| >= 0.15` 条件下，forecast-vs-curve 有 154 / 138 个事件，momentum-vs-reversion 有 406 / 401 个事件。

所以从 v0.2.2 起，这两个轴才是实际存在的经济选择。v0.2.1 以前不能作为它们的人员经济校准基线。

## 5. 同一个人如何得到不同策略画像

专属 radar 不是随机重抽，也不是照抄方向 radar：

```text
50
+ 通用风格偏离 × 注册 loadings
+ strategy-specific deterministic idiosyncrasy
```

- 通用偏离保证跨策略相关；
- strategy-specific idiosyncrasy 允许同一个人在不同策略里不完全一致；
- idiosyncrasy 只由人员 profile hash 与维度地址决定；
- 不使用市场未来、预测真值或历史 PnL；
- 最终落在 10—90。

默认兼容负责人是特例：八维全部严格 50，用于稳定中性锚点。

## 6. 风格与能力严格分离

第二策略专属 style owner 不新增能力分。

| 通用能力 | 第二策略解释 |
|---|---|
| `exposure_construction` | spread exposure construction |
| `transition_planning` | pair transition planning |
| `contract_lifecycle_planning` | curve lifecycle planning |

```text
Dedicated PM style
→ ideal spread target
→ construction capability bounded error
→ submitted spread target
→ persistence
→ thesis
→ strategy-specific risk
→ paired execution mandate
```

风格决定“想怎么做”；能力决定“能否稳定把自己的想法做成方案”；风险决定“允许做到哪里”；交易部以后决定“如何成交以及付多少可避免成本”。

## 7. Broad Same-State 行为验收

最终验收不让 10 分与 90 分各自跑不同历史，而是：

```text
中性 50 分 PM 路径
→ 冻结 market + forecast + research-book state
→ 同一个截点同时计算某一轴 10 分与 90 分
→ 比较同状态响应
```

主力／次主力 pair identity 变化时 research book 清零；正式 lifecycle scheduler 建立前，不把旧 pair 仓位抽象搬给新 pair。

开发与验证各 16 Seed × 2 年 = 768 个截点。结果：

| Axis | Development 10→90 | Validation 10→90 |
|---|---:|---:|
| `curve_continuation_reversion` | alignment `-0.738 → +0.517`（406事件） | `-0.688 → +0.481`（401事件） |
| `forecast_vs_visible_curve` | alignment `0.000 → 0.869`（154事件） | `0.000 → 0.833`（138事件） |
| `dislocation_selectivity` | active `98.05% → 93.23%` | `97.01% → 92.19%` |
| `capital_deployment` | capacity `129.3 → 302.3` | `124.1 → 290.2` |
| `adjustment_tempo` | completion `31.32% → 68.54%` | `31.24% → 68.37%` |
| `rebalance_activity` | turnover budget `61.3 → 465.4` | `59.4 → 451.1` |
| `holding_patience` | retention `0.05% → 58.84%`（40事件） | `0.14% → 59.04%`（30事件） |
| `forecast_horizon` | 4w weight `23.6% → 36.4%` | `23.6% → 36.4%` |

```text
Development = 8 / 8 PASS
Validation  = 8 / 8 PASS
Overall     = PASS
```

收益和 markout 不参与门禁。

## 8. 当前边界

v0.2.2 已具备：

- 专属 8 维 PM 风格；
- 同一人员 strategy-type-specific 投影；
- Strategy Book 持仓隔离；
- 三项 construction capability 的 spread-specific 解释；
- 真实 12 周 visible spread history；
- 开发／验证分离的自然事件扫描；
- broad same-state 8/8 行为验收。

仍未完成：

- Pair Execution trader adapter；
- Strategy Book settlement ledger；
- portfolio capital allocation；
- 多策略组合风险；
- 带真实成交成本的长样本收益校准；
- 正式竞技接入。

因此现在可以宣称**第二策略的策略研究风格已经经济可辨识**；仍不能宣称第二策略收益校准成熟或已经具备正式竞技资格。
