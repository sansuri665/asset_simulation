# Stage6C-preview2：动态货盘形成层

本分支 `stage6c-preview2-dynamic-cargo-formation` 直接继承 `stage6c-preview-dynamic-freight-board` 的 `d788a7c72aa7256dc8fc51d70f614f45308ecd81`。父 Preview 不修改。本版只增加货盘形成层，不重写 Freight Board、船舶物理执行、库存台账或上游原油世界。

## 1. 目标

Stage6C-preview 已经回答：给定一份试算货盘和一份外部船舶意向，当前与未来运力如何形成透明报价，最后如何一次性 commit 到真实物理世界。

Preview2 补上此前仍是外部输入的一层：**当前进口方到底愿意下多少运输订单，以及这些订单在可用来源之间如何小步调整。**

完整试算关系成为：

```text
上游/上一轮自然 OD 计划
+ 本回合真实来源可出口油
+ 冻结的目的地 opening inventory
+ 已知进口义务与在途到货
+ 外部当前船舶填报
        ↓
Cargo Formation：小步调整货盘
        ↓
原 Stage6C-preview quote_trial()
        ↓
新的路线运价、未来库存、边际装载
        ↓
继续调整货盘；外部也可修改船舶填报后重新形成货盘
        ↓
选定一个 Final Trial
        ↓
原 BoardSession.commit() 一次性真实执行
```

所有货盘形成调用仍基于同一个冻结 BoardSnapshot。反复形成方案不会移动船、修改货批、改变 opening inventory 或推进价格记忆。

## 2. Natural cargo plan 是先验，不是最终硬配额

`natural_cargo_plan` 来自上游贸易结构、旧 OD 矩阵或上一轮延续计划。它回答：**没有本轮市场反馈时，贸易本来大致会怎么走。**

它不再是最终刚性运输计划。

如果某来源实际可出口量不足，该来源计划首先被真实物理供给裁到可装水平；缺口不被删除，而是由库存状态与其它来源真实余量决定，是暂时由库存承受，还是转向其它来源补回。

因此：

```text
Natural OD ≠ Final OD
```

Preview2 不设置“西非最多替代海湾30%”之类默认来源替代上限。来源调整采用固定小步、价差阈值和有限迭代；只要另一来源确有真实油，最终能否继续移动由运价反馈、库存状态与物理供给决定。自然计划仍然是每轮动态形成的起点，而不是不可突破的份额。

## 3. 出口端不设 exporter agent

出口端只提供物理边界：

```text
PhysicalAvailable_o
= min(源端现有未装油 + 本回合真实释放油, 当前出口限额)
```

系统不模拟海湾国家、西非卖家或贸易商的人格、报价策略、原油品质谈判。某来源没有真实油就不能承接新货盘；其它来源的油也不能瞬移过去。

## 4. 进口端：需求不删除，库存只改变运输时点

进口需求继续由独立 `ImportRequirement` 拥有。Cargo Formation 不能创建、删除或改写真实需求。

目的地 opening inventory 是上一轮已经 commit 的现实，加上本回合已经真实到港的货，再减去已到期需求。它在本轮所有试算中冻结。

新 trial cargo 只会改变未来预计到港路径：

```text
OpeningInventory_t = committed reality
TrialCargo_t        = future inventory expectation
```

因此今天下单不能修复今天已经发生的库存缺口。

所谓 `inventory_draw_vs_natural_bbl` 只是相对于自然计划少下了一部分本轮运输订单，让现有库存承担这段时间差；需求仍保留，未来库存下降会提高补货压力。它不是 demand destruction。

## 5. 库存焦虑：透明的影子运输价值

货盘形成层不引入原油现货价格、炼厂利润或 voyage cost。为了判断“现在付较高运费补货，还是让库存再顶一段时间”，本版只构造一个相对于当前运费水平的库存影子运输价值：

```text
anchor = 自然货盘权重下的当前路线每桶运输服务价值
inventory_signal = opening 及规划窗口内最紧张的库存软压力
shadow = anchor * exp(-sensitivity * inventory_signal)
```

- 库存高：`inventory_signal > 0`，shadow 下降，更愿意延期贵的货盘；
- 库存低：`inventory_signal < 0`，shadow 上升，更愿意支付较高运费补货；
- 接近硬边界：原 Preview 的库存可行性仍是硬约束，不能靠影子价格绕过。

该 shadow 不是原油价格，也不是“库存的美元会计价值”；它只是一个透明、可重算的运输时点比较标尺。

## 6. 三类自动货盘动作

默认每次只移动 250,000 桶，最多 64 次迭代。

### A. Forced shortfall replacement

某来源真实油不足使当前订单总量低于 natural total，且目的地库存已经低于正常水平时：

> 优先从仍有真实余量的最便宜来源小步补回 natural total。

这里不要求替代来源运费低于 shadow。原因是低库存状态下，先恢复原本计划中的总进口量，再讨论额外预防性补库，比继续因为某条替代航线昂贵而吃掉已经偏低的库存更符合本版定义。

### B. Inventory replenishment / deferral

达到 natural total 后，如果库存仍偏低，可以继续比较替代来源运费与 shadow，决定是否额外预补库存。

库存偏高时，如果某条当前货盘的运输价值高于 shadow，可以减少一小步订单，只要未来库存路径仍满足硬边界。

### C. Source switch

如果两条来源之间存在足够大的相对运费差，且另一来源还有真实油，可以保持订单总量不变地从贵来源向便宜来源小步移动。

默认 `maximum_source_reallocation_fraction=1.0`，因此这个安全字段在正常单轮中不形成来源替代硬上限；它只保留为可显式收紧的实验/场景控制。默认状态下，来源切换是否停止取决于价差反馈、真实来源余量、库存约束、250,000桶步长和最大迭代次数，而不是预先规定的OD替代比例。

## 7. 船舶侧仍然完全外部

`form_cargo_plan()` 接收 `trial_ship_plan`，但只读它。

它不会：

- 新增或删除船；
- 调整船序；
- 把船自动转场；
- 根据高运价寻找套利；
- 计算利润最大化。

外部船东/玩家若改变船舶填报，可在**同一个 BoardSnapshot** 上再次调用 `form_cargo_plan()`。新的运力结构先改变 Freight Board 报价，货盘层再据此重新形成订单。

因此动态关系是：

```text
CargoPlan ↔ FreightQuote ↔ ExternalShipPlan
```

但所有物理状态仍只在最终 `commit()` 时推进一次。

## 8. 为什么不做全局最优化

本版刻意不求：

```text
argmin 全世界运输成本
```

也不求一个隐含的唯一均衡解。

每一步动作、移动桶数、修改前后的路线报价、库存 signal、shadow、硬边界状态都会写进 iteration ledger。这样玩家和后续 Agent 可以看清系统为什么调整，而不是只拿到一个黑箱“最优 OD 矩阵”。

## 9. Preview2 仍未包含

- 全球 25 OD 正式执行；
- 多进口目的地；
- 原油 grades / refinery compatibility；
- 原油买卖价格；
- term contracts；
- 船东利润与成本；
- 自动船东策略；
- exporter agent；
- 全局数学均衡器。

本版只验证双来源、单目的地情况下：**自然货盘能否在真实来源供给、目的地库存和运力报价反馈之间透明地重新形成。**

## 10. 核心原则

```text
真实油决定“哪里能卖多少”。
真实进口义务决定“最终欠多少油”。
库存决定“运输时间上能拖多久”。
运费决定“边际上更愿意从哪里补”。
外部船东决定“船愿意去哪里”。
只有 Final Commit 改变真实世界。
```
