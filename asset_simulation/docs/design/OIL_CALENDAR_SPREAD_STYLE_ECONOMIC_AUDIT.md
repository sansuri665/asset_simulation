# 原油短期跨期价差：专属 PM 风格经济审计

> 状态：v0.2.2 研究候选行为验收已通过  
> Strategy style owner：`oil_calendar_spread_research_v1`  
> Natural identification：`audit_oil_calendar_spread_style_identification.py`  
> Current hard gate：`audit_oil_calendar_spread_style_same_state_acceptance.py`

## 1. 审计目标

本审计不回答“哪个 PM 风格赚钱最多”，而回答：

> 第二策略八个专属风格维度改变时，真实可见市场路径上的决策行为是否按定义发生变化？

收益、下一回合 markout 和最终排名都不能作为通过门禁。只有行为语义先成立，后续 Pair Execution 完成后才有资格做正式收益校准。

## 2. 审计首先发现的真实策略缺陷

首轮 v0.2.1 审计发现：真实市场路径中 visible-curve signal 实际从未工作。

根因不是 PM 映射，而是市场与 spread reader 的坐标边界不一致：

```text
oil_futures_overlay：
monthly[{year, month, weekly:[{week, OHLC, ...}]}]

旧 spread reader：
要求 weekly 子节点自己携带 year/month 或 week_serial
```

市场已经公开的周线因此被 reader 丢弃，每个回合只剩追加的 decision-cutoff 一个点，导致：

- momentum = 0；
- mean reversion = 0；
- visible curve = 0；
- `curve_continuation_reversion` 无法产生真实行为差异；
- `forecast_vs_visible_curve` 退化为预测信号的缩放旋钮。

v0.2.2 新增 strategy-local metadata adapter，只把父 monthly.year/month 继承给 weekly child；不改价格、成交量、OI、limits、market identity 或 cutoff，也不写回市场。

## 3. 修复后的自然事件覆盖

冻结开发 Seed 0—15、验证 Seed 100—115，每个 Seed 覆盖 2 年，共：

```text
Development: 16 × 48 = 768 半月截点
Validation:  16 × 48 = 768 半月截点
Total:                     1536 截点
```

主力／次主力 pair identity 改变时 research book 清零，因为正式 spread lifecycle scheduler 尚未存在。两个分区各发生 80 次 pair reset。

修复后：

| 指标 | Development | Validation |
|---|---:|---:|
| 12 周历史可用 | 768 / 768 | 768 / 768 |
| forecast signal 非零 | 768 / 768 | 768 / 768 |
| visible-curve signal 非零 | 768 / 768 | 768 / 768 |
| momentum signal 非零 | 768 / 768 | 768 / 768 |
| mean-reversion signal 非零 | 768 / 768 | 768 / 768 |
| forecast 与 visible curve 任意强度反向 | 369 | 368 |
| momentum 与 reversion 任意强度反向 | 646 | 654 |
| 同方向缩仓事件 | 40 | 30 |

严格要求冲突双方绝对值均至少 `0.15` 后，仍有：

| 条件事件 | Development | Validation |
|---|---:|---:|
| forecast vs visible curve | 154 | 138 |
| momentum vs mean reversion | 406 | 401 |

这证明两个核心专属轴拥有足够自然识别事件，不需要人为构造冲突，也不需要下调门槛。

## 4. 最终验收方法：Broad Same-State

最终验收不让低分 PM 与高分 PM 各自跑不同历史，而是：

```text
中性 50 分 PM
→ 产生唯一 research-book 状态路径
→ 冻结 market + forecast + current spread state
→ 同时计算某一轴 10 分与 90 分
→ 比较同一个状态下的行为响应
```

因此市场、forecast vintage、当前 spread 仓位和其余七轴完全相同。construction error、正式 execution、正式 account PnL 均不进入验收。

只有 `capital_deployment` 会重新计算 spread capacity，因为它本来就应该改变已授权资本的实际部署预算；其它七轴直接使用同一组 neutral signal primitives。

旧的 `audit_oil_calendar_spread_style_economics.py` 和 `audit_oil_calendar_spread_style_economic_acceptance.py` 保留为开发诊断与回归辅助，但不再是最终 hard gate；当前 CI 的 hard gate 是 broad same-state acceptance。

## 5. 八轴开发／验证结果

10 分与 90 分比较如下。表内都是行为指标，不是收益。

| Axis | Development 10→90 | Validation 10→90 | 结果 |
|---|---:|---:|---|
| `curve_continuation_reversion` | momentum alignment `-0.738 → +0.517`（406事件） | `-0.688 → +0.481`（401事件） | PASS |
| `forecast_vs_visible_curve` | forecast alignment `0.000 → 0.869`（154事件） | `0.000 → 0.833`（138事件） | PASS |
| `dislocation_selectivity` | active rate `98.05% → 93.23%` | `97.01% → 92.19%` | PASS |
| `capital_deployment` | mean capacity `129.3 → 302.3` units | `124.1 → 290.2` units | PASS |
| `adjustment_tempo` | gap completion `31.32% → 68.54%` | `31.24% → 68.37%` | PASS |
| `rebalance_activity` | advisory turnover `61.3 → 465.4` units | `59.4 → 451.1` units | PASS |
| `holding_patience` | retention `0.05% → 58.84%`（40事件） | `0.14% → 59.04%`（30事件） | PASS |
| `forecast_horizon` | 4w weight `23.6% → 36.4%` | `23.6% → 36.4%` | PASS |

三个条件轴最低要求 10 个自然事件；开发与验证均高于门槛。

```text
Development hard gate = PASS 8 / 8
Validation  hard gate = PASS 8 / 8
Overall                 = PASS
```

## 6. 明确不使用的门禁

以下只允许作为未来观察项：

- idealized next-half-turn markout；
- 某一风格累计收益；
- 某一风格 Sharpe；
- 某一风格胜率；
- 候选 PM 长期排名。

第二策略尚无正式 Pair Execution、Strategy Book settlement ledger 和 portfolio risk，此时用收益反推人员风格会混淆研究目标与真实成交结果。

## 7. 当前结论

现在可以正式宣称：

> **Calendar Spread 的八个专属 PM 风格已经从静态参数设计进入真实市场路径上的经济可辨识行为，并在开发／验证分离样本上全部通过。**

仍不能宣称：

- 第二策略收益校准成熟；
- 某一风格长期更优；
- 已有正式执行 PnL；
- 已有多策略组合收益；
- 可以立即接入四机构竞技。

下一阶段首要任务仍是 Pair Execution Adapter，其后才是 Strategy Book settlement ledger、Portfolio Risk / Investment Decision 与正式收益校准。
