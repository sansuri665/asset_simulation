# 原油短期跨期价差：真实双腿执行

> 当前执行候选：`asset-simulation-oil-calendar-spread-pair-execution-v0.1.1`  
> 当前 Strategy decision：`asset-simulation-oil-calendar-spread-strategy-v0.2.3`  
> Trading Desk owner：`oil_execution_desk_v2`  
> 状态：Gate A 连续真实路径审计已通过；仍不负责 Strategy Book / Formal Account settlement

## 1. 目标

第二策略在策略研究与策略级风控之后生成：

```text
pairedExecutionMandate
  main delta
  next-main delta
  residual remediation
  per-leg hard turn limit
```

本层把它变成两张真实命名期货合约成交：

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

## 2. Gate A 过程中发现的两个真实缺陷

### 2.1 Pair Execution v0.1.0：窗口内 look-ahead

第一版 v0.1.0 已经能够生成真实双腿成交、使用真实周 OHLC / volume 计算成本、接入 `oil_execution_desk_v2`，并保持逐窗口 1:-1。

但静态复查发现它先看到执行期后来实现的 Week 1 / Week 2 volume，再据此决定两周之间怎么分单。这属于窗口内 look-ahead：Week 1 下单时不能知道 Week 2 最终成交量。

因此 v0.1.0 保留为原型，不作为当前执行基线。

v0.1.1 改成：**H0 只用决策时已可见的两腿历史流动性冻结两周 schedule；未来周成交量只能在该周实现后影响 impact / cost，不能回写 schedule。**

### 2.2 Strategy v0.2.2：零构造误差仍抢占 thesis 反转职责

把真实 fill preview 连续传给下一回合 Strategy Book 后，又暴露一个只在非零持仓路径才出现的问题。

旧 construction helper 在：

```text
current spread > 0
ideal target < 0
construction error = 0
```

时仍会先把 submitted target 截成 0。这意味着 score-100、零误差 PM 的 construction 不再是恒等映射，而且 construction 抢走了 thesis 已经拥有的“反转先减向 0、不允许一步跨零”的职责。

因此当前策略升为 v0.2.3：

```text
zero construction error
→ submitted target == ideal target
→ construction 不处理 thesis reversal
→ thesis 将 reversal objective 裁为 0
→ adjustment tempo 决定本回合向 0 走多快
```

例如当前 +100 spread、理想目标已经翻为负数时，thesis 可以把目标裁成 0，而 responsiveness 使本回合实际 executable target 先降到 +50；这仍是“退出后才能反向”，而不是要求一个半月回合内瞬间平光。

低 construction capability 的**非零工艺误差**仍保留安全 guard，不能因为工艺误差自己跨零创造反向仓位。

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
future weekly volume
可以影响 realized impact
不能影响 frozen scheduling weight
```

## 4. PM、Strategy Risk 与 Trading Desk 边界

```text
PM             = 要交易什么、理想做多少
Strategy Risk  = 最多允许多少
Thesis         = 旧论点是否仍允许扩大/反转
Trading Desk   = 已批准订单最终完成多少、在哪个窗口、付多少可避免成本
```

Calendar Spread PM 的 `rebalance_activity` 已经影响策略希望刷新多少仓位；它不会再次决定交易员的 urgency / passive / timing。

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

Trading Desk 不允许改 forecast、PM target、strategy risk capacity、hard market limit，也不允许创建额外 Alpha。

## 5. Completion 与 mandatory remediation

普通新 pair：

```text
requested pair units
× execution-desk completion reliability
∩ execution-desk normal capacity
∩ frozen strategy / market pair limit
→ executed pair units
```

执行能力可以少完成，但不能多于策略批准量。

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

两条腿各自拥有 fill、benchmark、all-in price、spread cost、impact 和 fee。

仓位状态在 weekly boundary 原子更新，因此本版不会凭空模拟没有盘口数据支持的秒级 asynchronous legging。未来可以单独增加 legging scheduler；当前明确“不模拟”优于用随机数制造残腿 Alpha/风险。

## 7. Residual remediation 与 gross turnover

已有 Strategy Book 残腿先整改，再开新 pair。

硬限额按真实 **gross turnover**：

```text
abs(remediation lots)
+ abs(new-pair leg lots)
<= frozen leg turn limit
```

不能因为两笔方向相反、净 delta 很小，就把真实成交量藏掉。

目前不做 remediation 与 new-pair 的内部净额优化；这应与下一阶段 Strategy Book settlement / attribution 一起设计。

## 8. 成本与 TCA

当前复用方向策略已经审计过的 execution-friction primitives：

- 周内动态 spread；
- square-root slippage；
- exchange / clearing / broker fee；
- rebate tiers。

这是共享执行基础设施，不意味着 Calendar Spread 继承 Directional 的 Alpha、风险或 target 逻辑。

所有成本逐腿计算后求和：

```text
execution cost
= spread cost
+ slippage cost
+ net fee
```

Pair execution spread 只使用 `new_pair` bucket：

```text
Main new-pair average execution price
-
Next new-pair average execution price
```

Residual remediation 成交不会污染 pair entry price。

TCA 使用 actual appointed Trading Desk 与 score-50 neutral benchmark，而且比较**同一组 realized fills**，避免高能力交易员因为多成交而机械获得 value-added。

## 9. 连续真实路径审计

当前 Gate-A audit：

```text
Seeds: 0, 42, 99, 197
Horizon: 1 year / seed
Total: 96 half-turns
```

研究执行 book 只传播真实 fills 形成的 ending position preview；Main/Next pair identity 发生切换时清零，因为正式 lifecycle / roll scheduler 尚未实现。

汇总结果：

| 指标 | 结果 |
|---|---:|
| 半月回合 | 96 |
| 有 pair 请求的回合 | 95 |
| 实际成交 pair 回合 | 95 |
| requested spread units | 2,283 |
| executed spread units | 2,283 |
| gross real-leg turnover | 4,566 lots |
| execution cost | $105,154.53 |
| spread cost | $69,735.00 |
| slippage cost | $24,004.53 |
| net fee | $11,415.00 |
| hard invariant violations | **0** |

这份 audit 使用中性兼容 Trading Desk，所以 completion 恰为 100%，execution value added 为 0；低／高执行能力对 completion 和 TCA 的差异由专项 same-order tests 单独验证，不能从这份中性路径推断所有交易员都会满额成交。

一个实际样本（Seed 42）：

```text
2030-01-H1 → 2030-01-H2
Main      = OIL-3005
Next Main = OIL-3009
requested pair = -45
executed pair  = -45

H0 frozen weights:
53.1877% / 46.8123%

weekly pair fills:
-24 / -21

neutral pair execution spread = -1.938045 $/bbl
all-in pair execution spread  = -1.987529 $/bbl

execution cost = $2,226.78
  spread       = $1,455.00
  slippage     = $546.78
  net fee      = $225.00
```

这里的 `-45 spread units` 不是 synthetic security：实际记录的是每个 weekly window 对 Main 与 Next Main 的两条相反方向真实合约 fill。

## 10. Settlement 边界

本层输出：

- real leg fills；
- per-leg costs；
- pair completion；
- ending Strategy Book position preview。

本层仍不执行：

- Strategy Book 持久 mutation；
- Formal Account mutation；
- cash settlement；
- margin；
- interest；
- financing；
- portfolio internal netting。

原因是下一层必须同时解决**策略归属与正式账户净仓之间的双重记账**，不能让执行模块顺手篡改账户。

## 11. Gate A 验收状态

已经验证：

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
13. 真实 fill 形成非零 Strategy Book 后，v0.2.3 仍保持 zero-error construction identity 与 thesis reversal owner；
14. Directional strategy economic smoke 保持原基线。

因此 Gate A 已经从“接口设计”推进到**连续真实周路径可执行**。下一阶段是 **Gate B — Strategy Book Settlement Ledger**。
