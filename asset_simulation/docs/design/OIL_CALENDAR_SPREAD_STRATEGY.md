# 短线原油跨期价差策略 PR Candidate

> 状态：**研究候选实现，不是当前竞技默认运行事实**
> 基线：`sansuri665/asset_simulation@db90bd7a5f1abe7bccc5cc945cc58d4e55a87f87`
> 候选模型：`asset-simulation-oil-calendar-spread-strategy-v0.1.2`
> 目标 owner：策略研究形成 spread units；策略专属风险适配器裁剪；交易部执行一份配对 mandate 的两条真实合约腿；正式账户继续保存命名合约仓位。

## 1. 核心对象

策略只交易市场 owner 指定的：

```text
Current Main
vs
immediately adjacent Next Main
```

定义：

```text
Dollar Spread = P_main - P_next_main
```

正 spread unit：

```text
+1 Main lot
-1 Next Main lot
```

负 spread unit 反向。账户中始终保存两条真实 `OIL-YYMM` 命名合约腿，不创建 synthetic spread security。

## 2. 本轮 reviewer feedback 的收敛修复

本候选 v0.1.1 专门修复以下六项：

1. **预测成熟时序**：2周和4周可以共同形成当期 signal，但 thesis evaluation 每次只评分一个**恰好成熟**的 horizon。半月默认只评2周组件；4周组件不会在2周截点评分。
2. **到期时间 owner**：策略不再自行依据年月和 H1/H2 重算剩余月份，而是直接使用 `oil_futures_overlay` 发布的 `months_to_expiry`；若 owner 字段缺失或与 `half_turns_to_expiry / 2` 不一致，直接拒绝。
3. **真实相邻两腿**：forecast 的 `main` 必须等于 `market.curve.main_contract_id`，`next_main` 必须是 curve 列表中紧邻 main 的下一张合约；第三月合约不能通过改 role 冒充 next-main。
4. **两腿半月成交硬限额**：paired mandate 在发出前先取两腿 `turn_trade_limit_lots` 的较小值裁剪本回合 pair request；execution report 再次逐腿拒绝任何越过市场限额的 fill。
5. **信号与 PnL 单位一致**：alpha 的方向由**美元价差变化**决定。归一化只使用决策截点的同一个 current reference price 做尺度缩放，因此不可能因为两腿共同涨跌而翻转美元价差方向。
6. **signed imbalance + 真 carry 边界**：`leg_imbalance_lots` 改为有符号；另加 `absolute_leg_imbalance_lots`。PnL 不再接受任意 `convergence_carry_pnl_usd`；只有提供两腿 carry counterfactual end prices 时才计算 carry，否则明确标记“not separately available”。

v0.1.2 继续收紧三个接口边界：

1. 2周／4周 forecast bar 必须精确存在，两条腿的 `target_week` 与 `week_serial` 必须一致；thesis evaluation 必须提交与冻结目标一致的 realized week serial。
2. 已有残腿优先占用本回合逐腿流动性额度，只有剩余额度可以用于新 pair delta；未消除的残腿继续显式报告。
3. execution report 不再用请求量补造缺失的市场限额；两条腿的 published limit 都是强制字段，并同时约束 pair request 与 remediation reservation。

## 3. 信息时序

决策只读取：

```text
visible current oil-futures payload
+ published main / next-main forecast vintage
+ appointed PM profile
+ current named-contract positions
+ committee-authorized strategy capital
```

它没有 `GlobalMacroRun`、未来 market payload 或隐藏真实 K 线输入。

```text
Decision cutoff
    ↓
2w + 4w forecast components form current signal
    ↓
paired target / risk / execution mandate
    ↓
next half-turn realizes 2 weeks
    ↓
thesis evaluates ONLY 2w component
```

4周 forecast 仍可影响当前 target，但不能用2周 realized price 提前判错。

## 4. Signal：交易美元价差变化，归一化仅作尺度

### 4.1 当前参考尺度

```text
current_reference
= (P_main_now + P_next_now) / 2
```

当前美元价差：

```text
S0 = P_main_now - P_next_now
```

对每个 forecast horizon：

```text
Sh = P_main_forecast_h - P_next_forecast_h

ΔS_h = Sh - S0
```

报告中的 dimensionless change 为：

```text
normalized_ΔS_h = ΔS_h / current_reference
```

关键点是**分母固定为决策截点的 current reference**。因此：

```text
sign(normalized_ΔS_h) == sign(ΔS_h)
```

例如：

```text
70 / 69 → 80 / 79
S0 = +1
Sh = +1
ΔS = 0
forecast signal = 0
```

不会再因为未来两腿价格整体变高而错误生成 spread signal。

### 4.2 Forecast component

PM 的 `forecast_horizon` 继续从现有 2/4/8 周权重中取 2周和4周并重新归一。最终：

```text
forecast_spread_change_usd
= Σ w_h × ΔS_h
```

forecast uncertainty 由两腿置信区间转为当期 reference 下的美元尺度，用于 signal strength normalization，不改变方向。

### 4.3 Visible curve component

历史对齐后也先计算真实美元价差：

```text
S_t = P_main_t - P_next_t
```

同一 decision 中所有历史 dimensionless level 都除以当前 reference，而不是各周各自的价格水平。因此：

- momentum 方向与美元 spread momentum 一致；
- mean-reversion z-score 实际在美元 spread level 上计算；
- common oil-price level drift 不会凭空制造相对价值变化。

### 4.4 PM 风格复用

继续复用：

- `continuation_reversion`
- `capital_deployment`
- `responsiveness`
- `selectivity`
- `turnover_activity`
- `holding_patience`
- `forecast_horizon`

严格 1:-1 首版仍不使用 `near_month_focus`，避免它破坏腿平衡。

## 5. Pair identity：只允许 current main + adjacent next

策略不相信 forecast 自己写的 role 足以证明标的正确，而是交叉验证市场 owner：

```text
market.curve.main_contract_id
        ↓
find its index in market.curve.contracts
        ↓
next listed contract = required next_main
```

因此下列情况直接拒绝：

- forecast `main` 不是当前 main；
- forecast `next_main` 是第三月或更远月；
- current main 在 curve 中找不到；
- current main 后面没有下一张合约。

这与预测 owner 当前按 main index + 1 选择 next-main 的方式一致。

## 6. Sizing 与 Strategy-specific Risk Adapter

目标仍为：

```text
target_main_lots      = target_spread_units
target_next_main_lots = -target_spread_units
```

risk capacity 取以下最小值：

```text
conservative pair margin capacity
market leg position capacity
all-contract gross cap / 2
stressed visible dollar-spread volatility capacity
```

到期/roll 检查不再自行重算：

```text
main_months_to_expiry = market main contract["months_to_expiry"]
next_months_to_expiry = market next contract["months_to_expiry"]
```

若同时存在 `half_turns_to_expiry`，要求：

```text
months_to_expiry == half_turns_to_expiry / 2
```

因此 2030-04-H2 的 `OIL-3005` 若市场 owner 发布 `1.0 month`，策略就使用 `1.0`，不会再额外减 0.5。

## 7. Paired Execution：目标缺口与本回合可执行 request 分开

策略可能希望：

```text
desired_pair_delta_units = +68
```

但若：

```text
main turn limit = 10
next turn limit = 8
```

若当前没有残腿，本回合 paired mandate 只能发：

```text
requested_pair_delta_units = +8
requested_main_delta_lots = +8
requested_next_main_delta_lots = -8
unrequested_target_gap_units = +60
```

execution report 又做第二层硬校验：

```text
abs(executed_main_delta) <= main_turn_limit
abs(executed_next_delta) <= next_turn_limit
```

因此“请求68组、两腿限额10/8、仍报告68组 balanced_complete”不再可能。

若存在残腿，则按每条腿分别执行：

```text
published leg turn limit
- remediation reservation (first priority)
= remaining capacity for new pair delta
```

`combined_requested_*_turnover_lots` 使用 pair 与 remediation 的绝对委托量之和；任何一条腿超过 published limit，mandate 或 execution report 都会拒绝。若本回合容量不足，`remaining_residual_*_lots_after_request` 保留未完成部分，不能偷偷假定一次清零。

成交仍逐腿记录。失衡定义：

```text
leg_imbalance_lots
= executed_main_delta_lots
  + executed_next_main_delta_lots
```

这是**有符号值**。另发布：

```text
absolute_leg_imbalance_lots
```

用于风险阈值和 remediation。

## 8. Thesis：只评分恰好成熟的 horizon

`evaluate_oil_calendar_spread_thesis_state(..., realized_week_serial=..., evaluation_horizon_weeks=2)` 默认半月只选择：

```text
requested_horizon_weeks == 2
selected_horizon_weeks == 2
realized_week_serial == frozen target_week_serial
```

并用该组件自己的：

- forecast dollar spread change；
- target week；
- pair uncertainty；

与2周实现价比较。

4周组件被列为：

```text
unmatured_horizons_weeks = [4]
```

而不是混进同一次 forecast error。

当前 research-stage helper 不维护一个跨多回合的 delayed thesis ledger。调用方只有在真实市场推进到冻结的4周 `target_week_serial` 后，才能调用4周评价；传入2周实际周序号会被拒绝。未来正式多策略 runtime 仍应建立按 vintage + target week 到期的 pending evaluation ledger，自动管理何时触发，而不是依靠界面或策略自行挑选期限。

## 9. PnL：美元 spread 与 residual direction

平衡部分：

```text
calendar_spread_pnl
= spread_units
  × [(P_main_end - P_main_start)
     - (P_next_end - P_next_start)]
  × 1000
```

残余腿：

```text
residual_directional_pnl
= residual_main × ΔP_main × 1000
  + residual_next × ΔP_next × 1000
```

严格满足：

```text
gross_leg_pnl
= calendar_spread_pnl
  + residual_directional_pnl
```

### Carry

v0.1.1 删除自由输入：

```text
convergence_carry_pnl_usd = arbitrary caller number
```

改为可选的两腿 carry counterfactual prices：

```text
carry_reference_main_end_price_usd
carry_reference_next_main_end_price_usd
```

若两者都有，则：

```text
carry_pnl
= spread_units
  × [(P_main_carry_end - P_main_start)
     - (P_next_carry_end - P_next_start)]
  × 1000
```

然后：

```text
forecast_curve_move_pnl
= calendar_spread_pnl - carry_pnl
```

若没有可靠 counterfactual，系统明确报告：

```text
carryAttribution.status = "not_separately_available"
convergence_carry_pnl_usd = 0
```

而不是通过人为传入一个 carry PnL 再倒算剩余项。

## 10. 当前边界

本候选仍故意**不接入**：

- `OilInvestmentCompetitionSession`；
- 当前默认 `oil_trading_strategy.py`；
- 多策略 Investment Decision 资本分配；
- 正式 account-level strategy book；
- 组合保证金；
- Viewer 第二策略入口。

因此本 PR 只把第二策略的 owner、单位、硬约束和研究接口做正确，不改变当前竞技收益或默认 replay。

## 11. 合入前最低验收

候选自己的回归至少覆盖：

1. 2周正确、4周未到期时不会因4周误差提前 invalidated；
2. 2030-04-H2 使用市场 owner 的1.0月期限；
3. 第三月合约冒充 next-main 被拒绝；
4. paired fills 不能超过任一腿半月容量；
5. common +10 美元、美元价差不变时 forecast spread signal 为0；
6. signed imbalance 正负方向均正确；
7. carry 只能从 counterfactual leg prices 计算；
8. randomized PnL 恒等式；
9. randomized signal direction consistency；
10. 完整仓库 unit suite。
