# Gate B 双策略逐回合运行时

> 状态：首版机械闭环实现；Roll Scheduler 尚未加入，因此长期经济结论仍标记为 `lifecycle_incomplete`。

## 1. 本轮完成的边界

```text
Directional child orders
+ Calendar Spread child orders
        ↓
USD amount authorization
        ↓
Gate B shared allocator
        ↓
internal cross at one neutral transfer price
        ↓
one external parent order per named contract
        ↓
one shared Trading Desk
        ↓
actual fills and costs allocated back
        ↓
Directional Strategy Book + Spread Strategy Book
        ↓
one Formal Account cash / margin settlement
```

策略授权的权威单位仍然是美元。首份比较固定为：

```text
Directional-only:
    Directional      $10m
    Calendar Spread  $0

Fixed dual strategy:
    Directional      $5m
    Calendar Spread  $5m
```

比例字段只作展示，不驱动自动再平衡。

## 2. Strategy Books 与 Formal Account

两本 Strategy Book 记录：

- 授权金额；
- 虚拟命名合约仓位；
- 期初仓位 MTM；
- 内部成交 PnL；
- 外部成交后续 PnL；
- 外部分摊执行成本；
- 保证金融资成本归因；
- 强平成本归因；
- fully-loaded PnL 与 Strategy NAV。

Formal Account 是唯一法律／经济结算真相：

- 现金只结算一次；
- 变动保证金只入账一次；
- 初始／维持保证金只按聚合净仓计算一次；
- 空闲现金利息、保证金融资、追保和强平只运行一次。

每回合强制满足：

```text
Σ StrategyBookPosition = FormalAccountPosition

Σ Strategy external fills = Trading Desk parent fills

Σ Strategy execution cost = Trading Desk external cost

Σ Strategy internal transfer PnL = 0

Σ Strategy net trading PnL = Formal variation margin

Σ Strategy fully-loaded PnL
+ corporate reserve change
= Formal Account net PnL

Σ Strategy NAV + corporate reserve = Formal equity
```

## 3. one Trading Desk

共享 allocator 完成后，每张命名合约只向市场发布一张净父订单。一个执行负责人档案统一决定：

- 普通新增风险订单完成倾向；
- 两个实现周的执行调度；
- 价差、滑点、费用与 TCA；
- 实际外部成交价格和成本。

首版为保持 Strategy Book 原子约束，把普通订单的 completion choice 放在 allocator 之前：

```text
strategy desired risk increase
→ shared desk completion adjustment
→ shared capacity allocator
→ fully executable external parent order
```

风险减仓、残腿整改和强制清算不吃普通完成率折扣。

## 4. 内部净额与转移价

相反的 Strategy child fills 先内部交叉，不占市场成交上限，也不产生交易所、经纪、滑点成本。

转移价固定为同一实现窗口的：

```text
OHLC4 × realized volume weighted neutral benchmark
```

规则在决策后才用新实现周解析，不让任一策略读取未来；买方与卖方使用同一价格，内部 PnL 在公司层严格零和。

## 5. Calendar Spread

Calendar Spread 继续使用真实命名 Main/Next 两腿，不创建 synthetic security：

```text
+1 spread unit = +1 Main lot -1 Next lot
```

共享 allocator 只能批准完整 spread units，不能逐腿独立裁剪。残腿 remediation 优先于普通新增 pair。

正式 runtime 还加入 pending thesis evaluation ledger：2周和4周 forecast 分别在其冻结的 `target_week_serial` 精确成熟时评价；不会提前用2周结果打分4周观点。

## 6. 首份报告的限制

为了先冻结净仓、账簿与 Formal Account 恒等式，首版关闭策略内部 round-trip turnover。Directional-only 与双策略对照使用相同净调仓执行口径，因此比较仍然公平，但尚不是最终换手经济版本。

Roll Scheduler 未完成。运行时在 Main/Next 身份切换前 fail closed：

```text
mechanical_status = valid
accounting_status = reconciled
long_horizon_economic_status = lifecycle_incomplete
```

因此报告中的 annualized return 只叫 `provisional_annualized_return_pct`，不可当作长期校准收益。

## 7. 验收

新增测试覆盖：

1. Directional `+80 Main` 与 Spread `-60 Main/+60 Next` 的真实内部净额、父订单、执行成本和 Formal Account 对账；
2. 固定 `$5m/$5m` 授权在权益变化后仍不自动缩放；
3. 双策略逐回合结果与哈希确定性；
4. 2周／4周 pending thesis 精确成熟；
5. Main/Next 切换前停止；
6. 第一份 Directional-only vs `$5m/$5m` 审计可执行并通过全部机械门禁。

运行：

```powershell
python -m asset_simulation.audit_oil_multi_strategy_gate_b_runtime `
  --seeds 0,42,99 `
  --maximum-turns 6 `
  --output artifacts/gate-b-first-dual-strategy-report.json
```
