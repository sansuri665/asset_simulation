# 原油短期跨期价差：真实双腿执行

> 当前候选：`asset-simulation-oil-calendar-spread-pair-execution-v0.1.1`  
> Strategy decision：`asset-simulation-oil-calendar-spread-strategy-v0.2.2`  
> Trading Desk owner：`oil_execution_desk_v2`  
> 状态：Gate A 研究候选；不负责 Strategy Book / Formal Account settlement

## 1. 目标

第二策略在策略研究与策略级风控之后已经能生成：

```text
pairedExecutionMandate
  main delta
  next-main delta
  residual remediation
  per-leg hard turn limit
```

本层把它变成真正的两张命名期货合约成交：

```text
risk-approved pair mandate
        ↓
appointed Trading Desk
        ↓
pre-decision visible-liquidity schedule
        ↓
Week 1 real Main + Next fills
Week 2 real Main + Next fills
        ↓
per-leg spread / impact / fee
        ↓
fill + cost report
        ↓
Strategy Book settlement preview
```

不创建 synthetic spread security。

## 2. v0.1.0 原型暴露的问题

第一版 v0.1.0 已经能够：

- 生成两条真实腿成交；
- 使用真实周 OHLC / volume 算成本；
- 接入 `oil_execution_desk_v2`；
- 让交易员能力影响 completion 与 TCA；
- 保持新开 pair 的逐窗口 1:-1。

但静态复查发现一个时间信息错误：它先看到执行期后来实现的 Week 1 / Week 2 volume，再据此决定两周之间怎么分单。

这虽然不读取执行期之后的信息，但仍属于**窗口内 look-ahead**：Week 1 下单时不能知道 Week 2 最终成交量。

因此 v0.1.0 保留为原型，不作为当前执行基线。

## 3. v0.1.1 的信息时间线

### H0：策略决策完成

可见：

- 当前 Main / Adjacent Next；
- 当前及历史命名合约价格；
- 截止 H0 已实现的 weekly volume / OI；
- 已冻结的 Strategy Book；
- 已冻结的策略级 risk-approved pair mandate；
- 已任命 Trading Desk 人员与执行风格。

在 H0 冻结未来两个执行窗口权重：

```text
Main 最近已知周成交量
Next 最近已知周成交量
        ↓
min(Main, Next)
        ↓
common visible pair-liquidity weights
        ↓
Trading Desk liquidity-scheduling / window-timing style
        ↓
frozen Week-1 / Week-2 pair weights
```

未来实现的周成交量绝不能回写 schedule。

### H+1：执行期实现

Week 1 / Week 2 各自实现后，可以使用当周：

- OHLC；
- volume；
- 当前真实腿流动性；

计算该窗口的：

- neutral OHLC4 benchmark；
- bid/ask spread cost；
- square-root market impact；
- exchange / clearing / broker fee；
- fee rebate；
- all-in execution price。

因此：

```text
未来 weekly volume
可以影响 realized impact
不能影响 frozen scheduling weight
```

## 4. PM 与 Trading Desk 的边界

Calendar Spread PM 的 `rebalance_activity` 已经影响策略希望刷新多少仓位；它不会再次决定交易员的执行风格。

Trading Desk 独立拥有：

- price execution；
- market-impact control；
- liquidity scheduling；
- completion reliability；
- roll coordination；
- fee efficiency；
- urgency；
- passive preference；
- window timing。

因此：

```text
PM        = 要交易什么、交易多少
Strategy Risk = 最多允许多少
Trading Desk  = 允许的单子最终完成多少、在哪个窗口、付多少可避免成本
```

Trading Desk 不允许：

- 改 forecast；
- 改 PM target；
- 扩大 risk-approved order；
- 改 hard market limit；
- 创建额外 Alpha。

## 5. Completion

普通新 pair：

```text
requested pair units
× execution-desk completion reliability
∩ execution-desk normal capacity
∩ frozen strategy / market pair limit
→ executed pair units
```

执行能力可以使订单少完成，但不能多于策略批准量。

已有残腿 remediation 属于 mandatory risk reduction：

- 优先于新 pair；
- 不受普通 style completion penalty；
- 仍受每腿 hard turn limit；
- 仍产生真实执行成本。

## 6. Pair 与 legging

v0.1.1 的新 pair 在每个 weekly window 内按真实腿记录：

```text
+N Main
-N Next
```

或反向。

两条腿有各自：

- fill record；
- benchmark price；
- all-in price；
- spread cost；
- impact；
- fee。

但仓位状态在 weekly boundary 原子更新，因此本版不会虚构没有微观盘口支撑的秒级 legging 过程。

真实的 asynchronous legging / complete-missing-leg scheduler 可以以后继续扩展；当前宁可明确“不模拟”，也不使用随意随机数制造残腿风险。

## 7. Residual remediation 与 gross turnover

已有 Strategy Book 残腿先整改，再开新 pair。

硬限额验证按真实 **gross turnover**：

```text
abs(remediation lots)
+ abs(new-pair leg lots)
<= frozen leg turn limit
```

不能因为两笔交易方向相反、净 delta 很小，就把真实成交量藏掉。

目前不做 remediation 与 new-pair 的同策略内部净额优化；这一点可以与下一阶段 Strategy Book settlement / attribution 一起设计。

## 8. 成本与 TCA

当前复用方向策略已经审计过的 execution-friction primitives：

- 周内动态 spread；
- square-root slippage；
- exchange / clearing / broker fee；
- rebate tiers。

这是过渡性的 shared primitive reuse，不意味着 Calendar Spread 继承 Directional 的 Alpha、风险或 target 逻辑。

所有成本逐腿计算后求和。

Pair execution spread 只使用 `new_pair` bucket：

```text
Main new-pair average execution price
-
Next new-pair average execution price
```

Residual remediation 的成交不能污染 pair entry price。

TCA 使用：

```text
actual appointed Trading Desk cost
vs
score-50 neutral execution cost
```

并且二者使用完全相同的 realized fills；因此 execution value added 不会因为高能力交易员“多成交了几手”而虚增。

## 9. Settlement 边界

本层输出：

- real leg fills；
- per-leg costs；
- pair completion；
- ending Strategy Book position preview。

本层不执行：

- Strategy Book mutation；
- Formal Account mutation；
- cash settlement；
- margin；
- interest；
- financing；
- portfolio internal netting。

原因是下一层需要同时解决**策略归属与正式账户净仓之间的双重记账**，不能让执行模块顺手篡改账户。

## 10. Gate A 验收

最低要求：

1. 决策与执行起点严格一致；
2. 只允许相邻 half-turn settlement；
3. Strategy Book identity 冻结；
4. schedule 不读取未来执行窗口 volume；
5. 新 pair 逐窗口严格 1:-1；
6. no synthetic fill；
7. execution 不扩大 mandate；
8. mandatory remediation 不因交易员低 completion 而被省略；
9. 每腿 gross turnover 不越 hard limit；
10. execution cost = spread + slippage + net fee；
11. pair entry price 排除 remediation；
12. 输入市场、Strategy Book、Formal Account 都不被写回；
13. Directional strategy regression 保持原基线。

Gate A 通过以后，下一阶段是 **Gate B — Strategy Book Settlement Ledger**。
