# 全球固定 VLCC 船池与多航线现货市场原型

> 实验分支：`global-vlcc-spot-market-prototype`  
> 上游基线：`seed42-vlcc-spot-market-prototype`  
> 状态：实验，不进入 `main`

## 目标

这一版把原来的“单条海湾→东亚航线从抽象外部池借船”升级为一个全球零和市场：

```text
现有 Seed 的月度原油航线货量
        ↓
每月 3 个约 10 日的 shipping turns
        ↓
6 个 VLCC 市场桶同时产生货盘
        ↓
同一组固定编号 VLCC 竞争这些货盘
        ↓
虚拟库存、船位转场与各航线 Spot TCE
```

任何一条航线多得到一艘 VLCC，都必须来自：

- 全球 idle pool；
- 另一条 VLCC 航线；
- 已经在转场途中、随后抵达的具体编号船。

模型不会因价格上涨而凭空生成船。

## 固定船队

当前采用：

| 项目 | 数量 |
|---|---:|
| 全球 VLCC 总数 | 900 |
| 结构性不可用／维修等 | 70 |
| 可交易市场船数 | 830 |
| 初始 idle ships | 30 |
| 初始分配给 6 个市场桶 | 800 |

全部船舶在模型内拥有固定编号 `VLCC-0001` 至 `VLCC-0900`。公开记录只发布数量，但内部每回合检查：

```text
各航线船舶
+ idle ships
+ repositioning ships
+ unavailable ships
= 900
```

并检查同一编号不能同时出现在两个位置。

## 六个市场桶

| 市场桶 | 上游实际需求 | VLCC份额 | 标准循环 | 初始船数 |
|---|---|---:|---:|---:|
| 中东→东亚 | `gulf_east_asia` | 100% | 5 turns | 239 |
| 西非→东亚 | `west_africa_east_asia` | 100% | 8 | 49 |
| 巴西／圭亚那→东亚 | `brazil_guyana_east_asia` | 100% | 8 | 45 |
| 美国湾岸→东亚 | `us_gulf_east_asia` | 100% | 11 | 51 |
| 中东→欧洲 VLCC 份额 | `gulf_europe` | 35% | 6 | 17 |
| 其他 VLCC 市场 | 其余航线吨海里指数 | 聚合 | 8 | 399 |

前五条直接读取现有 `oil_shipping_world` 的实际 Seed 货量。最后一个桶不是固定常数，而是把其余航线的：

```text
cargo_mbd × effective_haul_nm
```

汇总为剩余 VLCC 吨海里指数，再围绕 399 艘参考占用量动态变化。

## 虚拟库存与执行

每个市场桶均维护：

```text
origin deviation
in-transit deviation
destination deviation
```

它们都是相对于正常运输管线的偏离，可以为正或负，并严格满足：

```text
origin + in-transit + destination = 0
```

货量离散成 1.971 百万桶的 VLCC cargo。船位不足会造成未成交 fixture、出口端积压和进口端短缺；库存偏离又会提高该航线对船的边际出价。

## 全球船位竞争

每回合先执行现有船池能够完成的货盘，再计算各航线下一阶段希望拥有的船数。市场按以下顺序清算：

1. 高出价航线先吸收 idle ships；
2. 仍有缺口时，从低出价且船位过剩的航线调船；
3. 航线间转场需要 2 turns；
4. idle→航线和航线→idle 需要 1 turn；
5. 单回合全球最多转场 18 艘，单航线最多净变化 7 艘。

转场中的船暂时不能承运，因此大规模重配本身会收紧全市场 prompt supply。

## 运价口径

每条航线的供需状态先生成 2025 年购买力口径的实际 TCE：

```text
real TCE
= 35,000 USD/day
× f(prompt shortage, inventory gap, global fleet tightness)
```

随后使用对应 Seed 的 CPI 名义化：

```text
nominal TCE
= real TCE × CPI / 100
```

实际 TCE 暂时保留 0.45×–3.5× 的实验护栏。该护栏是数值安全边界，不是最终经济学意义上的绝对运价上限。

## 当前边界

这一版已经使用固定编号船舶并做全球零和分配，但仍是“商业循环船池”而不是逐船航行状态机。尚未逐艘记录：

- 装货半回合；
- 重载航行阶段；
- 卸货半回合；
- 压载返程；
- 港口等待和维修。

因此它验证的是**全市场有限运力、航线竞争和运价联动**。下一版再把每条编号船从 route pool 展开为具体 `loading / laden / discharge / ballast / idle` 状态。
