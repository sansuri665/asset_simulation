# 原油短期跨期价差：专属 PM 风格经济审计

> 状态：v0.2.1 研究候选验收方法  
> Strategy style owner：`oil_calendar_spread_research_v1`  
> Audit engine：`audit_oil_calendar_spread_style_economics.py`  
> Acceptance runner：`audit_oil_calendar_spread_style_economic_acceptance.py`

## 1. 审计目的

本审计不回答“哪个 PM 风格赚钱最多”，而回答一个更基础的问题：

> 当第二策略的某个专属风格维度从低分移动到高分时，真实可见市场路径上的决策行为是否按照该维度定义发生变化？

只有这一层先成立，后续才有资格讨论不同市场状态下的收益差异。

## 2. 受控识别

八个专属轴逐个审计：

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

每个轴只取：

```text
10 / 50 / 90
```

当某一轴变化时，其余七轴严格固定 50。

同时冻结：

- 同一 Seed 的全球宏观与原油期货路径；
- 同一预测机构与同一 forecast vintage；
- 默认三项 construction capability = 100，因此构造误差严格为零；
- 相同策略资本授权；
- 不读取未来市场形成决策。

因此这是 dedicated style axis 的受控语义审计，不是生成候选之间的生态相关性比较。

## 3. Research Book 状态

当前尚未接入正式 Pair Execution 与 Strategy Book settlement ledger，因此审计不能伪装成账户回测。

为了观察 `holding_patience` 和 `adjustment_tempo` 的跨回合作用，研究审计使用：

```text
本回合 target
→ 直接作为下一回合 research-book spread units
```

这等价于“研究目标状态传播”，不是成交模型。

仍保留生产策略的一条硬不变量：

```text
已有 spread 方向
→ 新信号反向
→ 本回合先向 0 退出
→ 不允许一步跨零开反向仓
```

审计不运行 thesis 的预测表现反馈，以免不同风格因为不同历史 PnL/预测命中状态产生二次路径污染。

## 4. 八个硬门禁

### 4.1 Forecast vs Visible Curve

只观察 forecast signal 与 visible-curve signal 明显反向的回合。

要求：高分相较低分，最终 raw signal 更接近 forecast component。

### 4.2 Curve Continuation / Reversion

只观察 curve momentum 与 curve mean-reversion 明显反向的回合。

要求：高分相较低分，visible-curve signal 更接近 momentum component。

### 4.3 Dislocation Selectivity

要求：高选择性不得产生更高的 active-signal frequency。

这一轴不是“高分更强”，只是更愿意等待大偏离。

### 4.4 Capital Deployment

要求：高部署意愿不得降低在相同授权资本下的 spread risk capacity。

实际 target 仍可能受 signal、流动性和风险边界约束，因此不要求每回合仓位机械放大。

### 4.5 Adjustment Tempo

要求：高分在相同 desired gap 下完成更高比例的目标调整。

### 4.6 Rebalance Activity

要求：高分拥有更高的 advisory pair turnover budget。

目前尚无正式 Pair Execution，因此只验证策略请求层，不宣称实际成交量。

### 4.7 Holding Patience

只观察已有仓位与新目标同方向、但新目标绝对值缩小的回合。

要求：高分相较低分保留更多旧 exposure。

### 4.8 Forecast Horizon

要求：高分提高 4 周 forecast component 的权重。

在 2 周与 4 周预测方向冲突时，同时记录 forecast spread change 是否向 4 周组件移动，但当前硬门禁优先使用注册期限权重本身，避免偶然价格尺度主导验收。

## 5. 开发与验证分离

当前首轮 CI 使用：

```text
Development: Seed 0, 42
Validation:  Seed 99, 197
Horizon:     2030 年 24 个半月回合
```

开发与验证必须分别通过全部八个行为门禁，才算 style semantics 通过首轮真实路径验收。

后续在 Pair Execution 完成后，应扩大到更多 Seed 与更长窗口，但不能反过来用扩样本掩盖基本方向错误。

## 6. Markout 只作观察

审计额外记录：

```text
idealized next-half-turn target markout
```

定义是假设 target 在决策参考价瞬间建立，并持有至下一半月，因此：

- 不含两腿真实 fill；
- 不含 spread / slippage / fee；
- 不含 margin constraint；
- 不含 legging；
- 不含 portfolio risk；
- 不是 Formal Account PnL。

它只用于观察“不同风格表达在这些市场路径上大致朝哪个方向产生经济差异”，**绝不能作为风格轴通过/失败门禁，也不能用于证明高分优于低分。**

## 7. 当前结论边界

只要首轮开发/验证全部行为门禁通过，可以宣称：

> 第二策略的八个专属 PM 风格已经从静态参数语义进入真实市场路径上的可辨识决策行为。

仍不能宣称：

- 第二策略收益校准成熟；
- 某一风格长期更优；
- 已有正式执行 PnL；
- 已具备多策略组合表现；
- 可以立即接入四机构竞技。

真正的收益校准必须等待 Pair Execution Adapter 与 Strategy Book settlement ledger 完成后再做。
