# 原油短期跨期价差策略：策略研究特质体系

> 状态：第二策略 v0.2.2 研究候选设计  
> Style owner：`oil_calendar_spread_research_v1`  
> Personnel owner：`oil_strategy_research_v2`  
> Construction capability owner：`oil_strategy_research_v2`  
> Visible-history adapter：`oil_calendar_spread_market_history_adapter`

## 1. 设计原则

第二策略不再直接把方向策略的八维蛛网当作自己的蛛网，但也不创建第二位 PM。

正式关系是：

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

因此同一个人可以：

- 在方向策略里明显偏趋势；
- 在跨期价差里只略偏趋势甚至偏回归；
- 仍然保留相似的资本胃口、节奏、纪律和持有习惯。

这表达“同一个人的投资性格跨策略相关，但不会机械复制”。

专属 radar 仍然是**偏好，不是能力**：没有总分，没有 Alpha 分，没有“高分更强”。未来表现只能来自真实历史 track record。

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

### 为什么没有 `near_month_focus`

Calendar Spread v1 的交易对象就是：

```text
+1 Main
-1 Adjacent Next
```

把“近月集中”继续作为 PM 轴会天然鼓励破坏 1:-1 腿平衡，因此该轴在第二策略中被移除。reference adapter 固定为中性 50，仅用于兼容已有 PM 参数映射。

## 3. 第二策略真正新增的哲学轴

### 3.1 Forecast vs Visible Curve

这是第二策略最重要的新轴。

当前信号先独立计算：

```text
forecast_spread_signal
visible_curve_signal
```

其中 visible curve 本身仍包含：

```text
curve continuation
vs
curve mean reversion
```

然后由 PM 的 `forecast_vs_visible_curve` 决定组合：

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

默认 50 分严格保留 70/30 的中性组合约定。

这一维不允许：

- 改写 forecast component 本身；
- 改写 visible curve component 本身；
- 读取未来真实价格；
- 绕过 deadband；
- 把能力分变成信号奖励。

它只是回答：**当研究预测与当前期限结构证据意见不完全一致时，这位 PM 更相信谁。**

### 3.2 v0.2.2：Visible Curve 从“公式存在”变为“真实输入可用”

受控经济审计发现 v0.2.1 之前存在一个策略输入边界错误：

```text
oil_futures_overlay 实际发布：
monthly[{year, month, weekly:[{week, OHLC, ...}]}]

旧 calendar-spread reader 预期：
weekly[{year, month, week, ...}]
```

因此市场已经公开的周线没有被 spread reader 正确识别，真实运行中 historical spread history 每回合只剩手工追加的 decision-cutoff 一个点；momentum、mean-reversion 与 visible-curve signal 实际长期为 0。

v0.2.2 不修改市场 owner，而在策略边界增加 metadata-only adapter：

```text
父 monthly.year / monthly.month
        ↓ inherit only
子 weekly.year / weekly.month
```

它：

- 不改变任何价格；
- 不改变 volume / open interest / limits；
- 不改变 market identity 或 cutoff；
- 不写回市场；
- 只让策略正确读取本来已经可见的命名合约历史。

修复后对开发 Seed 0—15 与验证 Seed 100—115、各 2 年共 1536 个真实半月截点扫描：

- 1536 / 1536 个截点均恢复 12 周对齐 spread history；
- forecast、visible-curve、momentum、mean-reversion 四个 signal component 均 1536 / 1536 非零；
- forecast 与 visible curve 自然反向：开发 369 次、验证 368 次；
- momentum 与 mean-reversion 自然反向：开发 646 次、验证 654 次；
- 在双方绝对值均至少 0.15 的严格识别门槛下，forecast-vs-curve 仍有开发 154 次、验证 138 次；momentum-vs-reversion 有开发 406 次、验证 401 次。

因此从 v0.2.2 起，`forecast_vs_visible_curve` 与 `curve_continuation_reversion` 才能被视为真实经济选择，而不是装饰参数。v0.2.1 以前的收益或人员风格结果不得用于这两个轴的经济校准基线。

## 4. 同一个人如何得到不同策略画像

专属 radar 不是随机重抽，也不是照抄方向 radar。

每一维使用：

```text
50
+ 通用风格偏离 × 注册 loadings
+ strategy-specific deterministic idiosyncrasy
```

其中：

- 通用风格偏离保证跨策略相关性；
- strategy-specific idiosyncrasy 允许同一人在两个策略里不完全一致；
- idiosyncrasy 只由人员 profile hash 和维度地址决定；
- 不使用市场未来、预测真值或历史 PnL；
- 最终分数强制落在 10—90。

默认兼容负责人是唯一特例：八维全部严格 50，用于提供稳定的中性行为锚点。

## 5. 风格与能力严格分离

第二策略专属 style owner 不新增能力分。

Construction capability 仍使用同一位人员已有三项：

| 通用能力 | 第二策略解释 |
|---|---|
| `exposure_construction` | spread exposure construction |
| `transition_planning` | pair transition planning |
| `contract_lifecycle_planning` | curve lifecycle planning |

运行顺序：

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

因此：

- 风格决定“想怎么做”；
- 能力决定“能否稳定把自己的想法构造成方案”；
- 风险决定“公司允许做到哪里”；
- 交易部决定“如何成交以及付出多少可避免成本”。

四者不得互相代替。

## 6. 经济可辨识验收

最终验收使用 broad same-state 方法，而不是让低分与高分各自跑出不同历史：

```text
中性 50 分 PM 路径
→ 冻结当期 market + forecast + research-book state
→ 同时计算某一轴 10 分与 90 分
→ 比较同状态决策差异
```

主力／次主力 pair identity 一旦改变，research-book spread units 清零；正式 spread lifecycle scheduler 尚未建立前，不允许把旧 pair 仓位抽象搬运给新 pair。

conditional axes：

- `forecast_vs_visible_curve`：只在两类证据显著反向时比较；
- `curve_continuation_reversion`：只在 momentum / reversion 显著反向时比较；
- `holding_patience`：只在同方向缩仓时比较。

其余五轴在全部真实截点比较。开发与验证分区均必须通过，且收益/markout 不参与门禁。

## 7. 当前边界

当前 v0.2.2 已经具备：

- 专属 8 维 PM 风格；
- 同一人员的 strategy-type-specific 投影；
- Strategy Book 持仓隔离；
- 三项 construction capability 的 spread-specific 解释；
- 真实 12 周 visible spread history；
- 开发／验证分离的自然事件扫描与 same-state 行为验收框架。

仍未完成：

- Pair Execution trader adapter；
- Strategy Book settlement ledger；
- portfolio capital allocation；
- 多策略组合风险；
- 带真实成交成本的长样本收益校准；
- 正式竞技接入。

因此即使八维行为门禁全部通过，也只能宣称**策略研究风格已经经济可辨识**，不能宣称第二策略收益校准成熟或已经具备正式竞技资格。
