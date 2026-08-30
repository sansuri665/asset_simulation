# 原油双策略 50:50 Pre-Trade Limit Stress

> 状态：development-only 机制压力测试  
> Allocator：`asset-simulation-oil-multi-strategy-pretrade-allocator-v0.1.0`  
> Allocation policy：`system_proxy_fixed_equal_split_v1`  
> 目的：在不改变市场 owner 限额的前提下，人为放大双策略请求，验证共享持仓／成交容量冲突的确定性仲裁

## 1. 为什么需要这一层

单策略时代，每个策略自己读取：

- `single_contract_position_limit_lots`；
- `turn_trade_limit_lots`；
- `all_contract_gross_position_cap_lots`。

两个策略同时运行后，只在各自 Strategy Book 内检查已经不够：

```text
Directional Book
Calendar Spread Book
        ↓
各自都合规
        ↓
Formal Account aggregate
可能超单合约 position limit / gross cap
```

同时，两策略可能在同一合约方向相反：

```text
Directional  BUY Main 100
Spread       SELL Main 80
```

真正需要送到外部市场的 Main 订单可以只有 `BUY 20`。因此：

```text
position limit
→ 看 Formal Account aggregate position

turn / market capacity
→ 看内部净额后的 external market order

Strategy Book
→ 继续保留每个策略自己的 allocated delta
```

这三个对象不能混为一谈。

## 2. 当前 50:50 只是 development proxy

当前公司正式 `Investment Decision` 仍未启用 multi-strategy allocation。

本测试暂时冻结：

```text
Directional entitlement     50%
Calendar Spread entitlement 50%
Reserve                      0%
```

它不是收益最优结论，也不是正式投委会规则。

单独运行 Directional 时仍保持原来的 100% 单策略兼容基线；50:50 只用于双策略机制联调。

## 3. 为什么压力测试不直接改生产资本

本轮按照“把容量人为拉大直到撞限额”的思路，但采用更干净的实现：

- 不修改 production company equity；
- 不修改 `oil_futures_overlay_v8` 发布的 market limits；
- 在 audit 中直接构造相当于高资本／高 conviction 下会出现的 oversized risk-approved requests；
- 默认 request size = 稀缺可用容量的 4 倍。

这样被测试的是 allocator，而不是同时修改市场生态。

## 4. 仲裁顺序

当前候选冻结：

```text
1. Mandatory strategy-risk reduction
2. Opposing strategy flow internal-netting preview
3. Ordinary risk-increasing allocation
4. Trading Desk execution（后续 owner）
```

Mandatory reduction 有优先级，但不能越过 exchange hard limit。如果连强制减险都因为 Formal Account 的硬约束无法执行，allocator 不伪造解决方案，而是返回：

```text
mandatory_reduction_blocked_by_hard_market_limit
→ future Portfolio Risk escalation required
```

## 5. Ordinary risk-increasing 的 50:50 规则

第一版采用 deterministic weighted max-min progressive fill。

两个策略都能使用共享稀缺容量时，服务进度按照 entitlement 比较：

```text
allocated units / entitlement
```

50:50 时等价于尽量一手一手交替推进。

如果总共只剩 100 手共享 Main capacity：

```text
Directional demand = 400
Spread demand      = 400

→ Directional ≈ 50
→ Spread      ≈ 50
```

整数边界最多允许 1 手 deterministic tie-break 差异。

调用顺序不是收益来源。

## 6. Spread 必须作为原子 group

Calendar Spread ordinary request 不是两张独立订单：

```text
+1 Main
-1 Adjacent Next
```

或反向，始终是一组 spread unit。

如果：

```text
Main 尚有 1000 手容量
Next 只剩 120 手容量
```

Spread 最多获得 120 units：

```text
+120 Main
-120 Next
```

不能出现：

```text
+1000 Main
-120 Next
```

当 Next 先成为瓶颈后，Spread 未使用的 50% entitlement 不被永久浪费；仍可使用 Main 的 Directional 可以继续获取剩余 capacity。

## 7. Internal netting preview

如果：

```text
Directional +600 Main
Spread      -600 Main / +600 Next
```

在两个策略 attribution 上仍然保留：

```text
Directional Book  Main +600
Spread Book       Main -600 / Next +600
```

但外部市场 footprint 可以预览为：

```text
Main external order = 0
Next external order = +600
```

因此 Main：

```text
gross strategy flow = 1200 lots
internal cross       = 600 lots
external turnover    = 0 lots
market turnover saved= 1200 lots
```

当前 allocator 只生成 **internal-netting preview**，不生成实际 internal fill，也不决定 transfer price。正式 fill allocation / transfer-price owner 属于后续 Strategy Book Settlement Ledger。

## 8. 压力场景

CI stress audit 使用真实 `oil_futures_overlay_v8` 发布限额，并对 Seeds `0,42,99,197` 的 `2030-01-H1` 构造五类 oversized request：

### A. Shared position-limit collision

把 Formal Account Main 仓位放到 position limit 附近，只留下少量 headroom；两个策略都请求该 headroom 的 4 倍以上。

验收：

- Main 最终恰好不超过 position limit；
- 50:50 下两个策略占用共享 headroom 差异不超过 1 手；
- Spread 仍严格 1:-1。

### B. Shared turn-capacity collision

账户从低仓位开始，两策略同方向大额争抢 Main 外部成交容量。

验收：

- external Main order 不越 `turn_trade_limit_lots`；
- 共享容量按 50:50 近似均分；
- 调用顺序不进入结果。

### C. Spread second-leg bottleneck

人为把 Formal Account 的 Next 仓位推近 Next position limit，使 Spread 第二腿先耗尽容量。

验收：

- Spread allocation = Next 剩余 headroom；
- Main 腿不超过相同 spread units；
- Directional 可以继续使用 Spread 释放的 Main entitlement。

### D. Opposing Main flow

Directional 买 Main，Spread 做反向 spread、卖 Main。

验收：

- Main 内部净额正确；
- external Main order 可以显著小于 strategy gross flow，甚至为 0；
- Strategy ownership 不被删除。

### E. Mandatory risk reduction priority

Directional 已有风险减仓要求，同时 Spread 在 Main 上提出反向普通订单。

验收：

- mandatory reduction 完整进入 allocation；
- opposing ordinary flow 可以内部抵消其外部 footprint；
- 所有 Formal Account hard limits 仍为真。

## 9. 当前明确没有做的事情

本 allocator 不负责：

- 真正动态 Investment Decision；
- 根据历史收益改变 50:50；
- Strategy Book mutation；
- internal cross transfer price；
- fill generation；
- Trading Desk schedule；
- cash / margin / financing；
- Portfolio VaR / drawdown；
- multi-strategy lifecycle roll。

因此这轮结果只能回答：

> **当双策略同时把市场容量挤爆时，50:50 共享限额机制是否在数学和 owner 边界上能稳定工作？**

它不能回答 50:50 是否是经济上最优的长期资本配置。

## 10. 与后续 Gate B 的关系

如果压力测试通过，Gate B 的 settlement ledger 就可以直接消费：

```text
strategy allocated deltas
+ internal netting preview
+ external market order mandate
```

然后负责把真实 Trading Desk fills 分配回 Strategy Books，并确保 Formal Account 只结算一次现金与保证金。
