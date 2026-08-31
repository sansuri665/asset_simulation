# Gate B：金额授权与共享市场容量碰撞门禁

> 状态：多策略运行入口候选；尚未接入 `OilInvestmentCompetitionSession` 的默认逐回合路径  
> 模型：`asset-simulation-oil-multi-strategy-gate-b-v0.1.0`  
> 范围：金额制策略授权状态、Strategy Book／Formal Account 仓位对账、内部净额、共享成交与持仓上限、原子多腿分配  
> 不在本轮：真实 Trading Desk 成交价格、Strategy PnL、Formal Account 现金结算、Roll Scheduler、动态 Portfolio Risk

## 1. 本轮冻结的治理语义

投资决策委员会按美元金额授权：

```text
Directional authorization = $5,000,000
Calendar Spread authorization = $5,000,000
```

金额是权威字段，比例只用于显示。账户权益变化不会把授权自动改写成固定比例；委员会需要通过新的决策显式修改金额。授权变更本身不直接改动 Strategy Book 或 Formal Account 仓位，仓位只能通过后续市场执行变化。

新决策截点要求：

```text
sum(strategy_authorized_capital_usd)
<= reference_company_equity_usd
```

若之后公司权益下降，旧金额保持不变并报告：

```text
authorization_overhang_usd
```

系统不会偷偷按比例缩放。正式账户、保证金和共享 allocator 仍是实际新增风险的最终边界。

## 2. Gate B 分配对象

每个策略提交一个或多个整数单位 `OrderGroup`：

```text
Directional:
  requested_units = 80
  legs = {Main: +1}

Calendar Spread:
  requested_units = 60
  legs = {Main: -1, Next: +1}
```

多腿组的单位比例是原子的。allocator 只能决定成交多少个完整单位，不能逐腿独立裁剪后制造新的残余方向风险。

优先级固定为：

```text
mandatory_liquidation
→ residual_remediation
→ risk_reduction
→ risk_increase
```

同一优先级使用确定性的等单位 water filling：先让仍有需求的组同步获得一个单位；若完整一轮不可行，再选择当前可行的最大确定性子集。较小需求会先自然完成，剩余容量继续流向仍有需求的组。

## 3. 内部净额与外部父订单

最终已分配的 Strategy Book fills 先在策略之间按命名合约内部匹配：

```text
Directional +80 Main
Spread      -60 Main +60 Next
```

得到：

```text
internal cross:
  Directional +60 Main
  Spread      -60 Main

external parent orders:
  +20 Main
  +60 Next
```

内部成交只转移 Strategy Book ownership，不改变 Formal Account 总仓位，不消耗外部成交额度，也不应产生交易所／经纪／滑点成本。

## 4. 当前硬约束

allocator 在一个 Formal Account 上同时检查：

- 单合约半月外部净成交上限；
- 单合约期末持仓上限；
- 全期限 gross position cap；
- `new_trades_allowed`；
- 可选的聚合初始保证金上限；
- 零金额授权禁止普通新增风险，但不阻止强平、残腿修复和减险；
- Strategy Books 期初仓位之和必须等于 Formal Account 期初仓位。

## 5. 碰撞测试

当前单元测试覆盖：

1. `80 Directional + 80 Spread` 同向争抢 Main=100，分配为 50／50；
2. `+80 Main` 与 `-60 Main/+60 Next` 内部净额，外部只成交 +20 Main 与 +60 Next；
3. Main=100、Next=35 的非对称腿上限，得到 Directional 65、Spread 35，Spread 仍严格 1:-1；
4. Formal Account 已持有 Main=900、持仓上限=1000，两个策略共享剩余 100 手空间；
5. 需求 10 与 200 争抢 100，较小需求先完成，结果为 10 与 90；
6. 零金额授权阻止新增风险，但已有仓位仍可减仓；
7. 权益上涨不自动增配，权益下跌只报告授权 overhang；
8. 输入顺序和映射顺序不改变结果或 hash。

## 6. 逐回合恒等式

Gate B 输出并验证：

```text
Σ StrategyBookPosition_before == FormalPosition_before
Σ StrategyBookPosition_after  == FormalPosition_after

Σ InternalFill_by_contract == 0
Σ StrategyExternalFill == ExternalParentOrder

FormalPosition_after
= FormalPosition_before + ExternalParentOrder

allocated child lot-sides
= internalized child lot-sides + external parent turnover
```

全部恒等式通过时发布：

```text
all_hard_gates_pass = true
```

## 7. 下一接入点

本模块已经冻结“金额授权 + 多策略共享容量 + 内部净额 + Strategy Book／Formal Account 对账”的入口，但尚未替代现有 Directional 自带的成交与结算路径。后续正式接入应依次完成：

```text
Directional child orders
+ Calendar Spread child orders
→ Gate B allocator
→ one external Trading Desk
→ actual fill allocation
→ per-strategy PnL and costs
→ one Formal Account cash/margin settlement
```

Roll Scheduler 完成前，Main／Next 身份切换附近仍必须标记 `lifecycle_incomplete`。
