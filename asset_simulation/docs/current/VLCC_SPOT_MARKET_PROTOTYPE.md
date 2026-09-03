# 海湾→东亚 VLCC 现货市场原型

> 实验分支：`seed42-vlcc-spot-market-prototype`  
> 状态：单航线实验，不进入当前 main 的公共运行时  
> 上游需求：直接读取现有 `oil_shipping_world` 的 `gulf_east_asia.cargo_mbd`  
> 航运时间：每月 3 个 shipping turns，约 10 天/回合

## 目的

这个原型只验证一件事：在现有真实 Seed 产生的海湾→东亚结构货量之下，如果 VLCC 总供给暂时视为灵活、船舶可以在航线之间自由转场，那么“局部船位 + 虚拟库存偏离”能否形成可信的短周期现货运价。

它不会修改上游原油贸易需求，也没有额外添加 backlog demand。需求仍完全来自当前区域物理世界和航线 RAS/IPF。

当前也没有：

- 期租；
- 新船订单、交付或拆船；
- 船东公司和资产负债表；
- 真实其他 13 条航线与本航线争夺同一组编号船舶；
- 港口逐船装卸事件；
- 制裁／影子船队；
- 真实绝对区域库存。

因此本原型是短周期 spot-market 验证，不是完整 VLCC 大周期模型。

## 基准尺度

当前采用：

| 参数 | 值 |
|---|---:|
| 海湾→东亚参考货量 | 9.3 mb/d |
| VLCC 标准货盘 | 1.971 百万桶 |
| shipping turns / 月 | 3 |
| 平均 shipping turn | 10.1389 天 |
| 完整航线循环 | 5 turns |
| 参考航线 VLCC 等效运力 | 239 艘 |
| 虚拟货物抵达东亚时滞 | 3 turns |
| 外部转场时滞 | 2 turns |

239 艘不是“专属船队”，而是 9.3 mb/d 在约 5 个 10 日回合循环下所占用的 VLCC 等效运力。实验中的航线船池可以从一个外部自由市场借入或返还 VLCC 等效运力。

## 虚拟库存

三项状态全部是“相对正常运输管线的偏离”，不是绝对库存：

```text
G = Gulf inventory deviation
W = in-transit deviation
E = East Asia inventory deviation
```

全部允许为正或负，正常状态都是 0。

如果本回合实际装船相对结构需求的偏差为：

```text
D = actual loaded cargo - structural cargo
```

则装船后：

```text
G -= D
W += D
```

经过 3 个 shipping turns，到货偏差进入东亚：

```text
W -= D
E += D
```

因此始终保持：

```text
G + W + E = 0
```

本轮自动化测试逐回合验证该恒等式。

用于市场压力的单一库存错配指标为：

```text
inventory_gap = (G - E) / 2
```

这样同一批“海湾多、东亚少”的原油不会被重复计算两次。

## 船位与自由转场

每回合先按当前航线船池估算可以形成多少 VLCC fixtures：

```text
available fixtures
≈ route fleet / 5
× turn days / 10.1389
```

货量被离散成整数 VLCC 货盘。

如果：

- prompt fixture 不足；
- 海湾库存偏高；
- 东亚库存偏低；

则市场发出正向 reposition request。船在 2 个回合后进入这条航线。

反之，如果 prompt ships 过剩且库存偏松，reposition request 为负，VLCC 等效运力离开本航线返回外部市场。

因此当前供给是双向、灵活的，不允许出现“只会摇船进来，不会把闲船摇走”的机械积累。

当前单回合净转场上限为 7 艘，外部池相对参考航线船池最多允许 ±120 艘。这个外部池只是 reduced-form 验证工具；正式多航线版本应改为全球编号船舶严格零和转场。

## 运价

市场紧张程度先产生 2025 年购买力口径的实际 TCE：

```text
real TCE = 35,000 USD/day × tightness multiplier
```

`tightness multiplier` 同时读取：

- prompt fixtures shortage / surplus；
- 虚拟库存错配天数。

当前实际 TCE 设有 0.45×–3.5× 的实验护栏。这个护栏不是对未来超级周期的经济判断，只是防止单航线原型在供给被人为锁死时数值爆炸。

然后再按当前 Seed 的 CPI 名义化：

```text
nominal TCE
= real 2025-dollar TCE
× CPI price-level index / 100
```

因此长期模拟必须同时看 real TCE 与 nominal TCE。后期出现 15–20 万美元/日的名义数字，不代表其真实市场紧张程度等同于 2025 年的 15–20 万美元行情。

当前所谓 TCE 仍是直接用 TCE 单位表达的运价状态变量；尚未加入燃油、港口费等，因此还不是由 voyage revenue 减 voyage costs 逐项计算出的完整 Baltic-style TCE。

## 自动化验证

实验分支使用 Python 3.11 GitHub Actions 实际执行：

- 6 个原型单元测试；
- Seed `0,1,5,7,42` 各 20 年真实上游需求；
- seed42 灵活供给 vs 固定供给对照；
- seed42 60 年长窗口。

最新验证全部通过。

### 5 Seed × 20 年

实际航线货量峰谷振幅：

```text
20.94% – 42.45%
```

虚拟库存：

```text
跨 Seed 的 p95 |inventory gap| 平均 = 0.816 天正常流量
跨 Seed 最大 |inventory gap| = 2.214 天
```

实际 2025 美元 TCE：

```text
跨 Seed 中位数平均 ≈ 33,016 USD/day
最低 Seed 的 p05 ≈ 26,771 USD/day
最高 Seed 的 p95 ≈ 44,038 USD/day
所有 Seed 绝对最高 ≈ 57,389 USD/day
```

说明在“全球船位仍很灵活”的假设下，现有需求变化主要形成普通到中度紧张行情，并不会自动产生真实 10–20 万美元/日的超级周期。

## seed42：20 年实际需求

结构货量：

```text
mean = 9.4435 mb/d
min  = 8.1885 mb/d
max  = 10.9037 mb/d
peak-to-trough = 33.16%
```

航线 VLCC 等效船池：

```text
min  = 217
mean = 247.36
max  = 295
```

虚拟库存：

```text
p95 |inventory gap| = 0.810 天
max |inventory gap| = 1.429 天
```

实际 2025 美元 TCE：

```text
p05    = 27,221 USD/day
median = 32,868 USD/day
p95    = 42,818 USD/day
max    = 50,744 USD/day
```

20 年末 CPI 约为 `152.43`，因此名义 TCE 已明显高于实际 TCE：

```text
nominal median = 40,525 USD/day
nominal p95    = 53,196 USD/day
nominal max    = 69,088 USD/day
```

同回合结构货量与实际 TCE 的相关系数只有约 `0.20`。价格不是简单由“cargo 高就贵”机械生成，而是通过船位和库存状态传导。

## seed42：固定供给对照

将航线 VLCC 等效船池永久锁定为 239 艘，并禁止从其他市场摇船时，同一个 seed42 20 年需求路径出现：

```text
max |inventory gap| ≈ 207.9 天
p95 |inventory gap| ≈ 202.3 天
unfilled fixtures = 7,821
```

实际 TCE 很快长期撞上实验护栏：

```text
median = 122,500 USD/day
p95    = 122,500 USD/day
max    = 122,500 USD/day
```

这个结果不是“239 艘固定船队的真实均衡运价”，而是说明：如果需求长期变化、但本航线永远无法从全市场增减船位，则虚拟库存必然无限积累，单航线市场无法自行清算。

对应的灵活供给版本只有：

```text
max |inventory gap| = 1.429 天
real TCE median = 32,868 USD/day
real TCE max = 50,744 USD/day
```

因此短周期 spot 市场的首要调节器应是船位重分配。

## seed42：60 年长窗口

结构货量：

```text
mean = 8.8292 mb/d
min  = 7.1891 mb/d
max  = 10.9037 mb/d
peak-to-trough = 51.67%
```

航线 VLCC 等效船池：

```text
min  = 187
mean = 231.50
max  = 295
```

库存状态仍然稳定：

```text
p95 |inventory gap| = 0.886 天
max |inventory gap| = 1.866 天
```

实际 2025 美元 TCE：

```text
min    = 20,804 USD/day
p05    = 27,230 USD/day
median = 32,988 USD/day
p95    = 42,947 USD/day
max    = 55,115 USD/day
```

但 CPI 从 `100` 上升到 `364.09`，因此名义 TCE 变为：

```text
p05    = 34,293 USD/day
median = 62,911 USD/day
p95    = 120,040 USD/day
max    = 165,886 USD/day
```

这说明长期判断“超级周期”必须看实际 TCE，而不是只看名义美元数字。

## seed42 年度轨迹抽样

| 年份 | 结构货量 mb/d | 航线 VLCC 等效船池 | 实际 TCE（2025 USD/day） | 名义 TCE（USD/day） |
|---|---:|---:|---:|---:|
| 2025 | 9.191 | 240.67 | 34,010 | 34,010 |
| 2030 | 9.420 | 247.19 | 33,316 | 37,385 |
| 2035 | 9.682 | 255.14 | 32,760 | 39,481 |
| 2040 | 10.031 | 259.47 | 35,421 | 45,577 |
| 2045 | 8.682 | 228.28 | 32,489 | 49,524 |
| 2050 | 8.196 | 215.89 | 33,881 | 59,122 |
| 2055 | 8.340 | 214.64 | 37,190 | 70,099 |
| 2060 | 8.117 | 214.31 | 32,685 | 62,771 |
| 2065 | 8.760 | 228.06 | 36,617 | 77,971 |
| 2070 | 9.460 | 250.39 | 35,390 | 88,587 |
| 2075 | 8.583 | 230.61 | 31,020 | 89,795 |
| 2080 | 8.256 | 214.81 | 33,897 | 107,065 |
| 2085 | 9.154 | 243.00 | 33,019 | 120,220 |

seed42 的需求不是恒定 9.3 mb/d：前期上升到约 10 mb/d，2040 年后明显回落，随后又有再上行。灵活供给版本的船位会跟着这个长期需求结构变化，但实际 TCE 中枢没有随年代机械抬升。

## 当前最重要的结论

本原型验证了短周期机制：

```text
Seeded structural cargo
→ virtual inventory deviation
→ prompt ship shortage / surplus
→ two-sided VLCC repositioning
→ spot freight response
```

在供给仍然灵活时，真实需求走势本身足以形成明显现货波动，但不会形成真正的超级周期。

真正的大周期需要下一层：

```text
global numbered VLCC fleet
+ all major routes competing for the same ships
+ vessel age
+ scrapping
+ orderbook
+ delivery lag
+ shipyard capacity
```

当全球总船数不再能快速追随需求时，同样的虚拟库存偏离和 prompt-ship shortage 才会把实际运价曲线整体推到高位，并可能产生真实 10–20 万美元/日的极端周期。

## 已知限制

1. 当前“外部自由船池”不是其他航线的显式船舶，因此还没有全球船位严格零和；
2. 船是 VLCC 等效容量，不是逐艘 `ship_id`；
3. 每月上游货量在三个 shipping turns 内保持相同 mb/d，尚未加入月内 cargo timing；
4. 装卸没有逐船半回合状态机，只通过 5-turn 周期和 3-turn 到货时滞近似；
5. 运价直接用实际 TCE 单位表达，没有先生成 freight quote 再扣 fuel/port costs；
6. 没有 Aframax/Suezmax 替代；
7. 没有期租，所有船都按自由 spot market 处理；
8. 没有公司金融、利率、债务和破产；这些应在船队市场稳定之后再接入。
