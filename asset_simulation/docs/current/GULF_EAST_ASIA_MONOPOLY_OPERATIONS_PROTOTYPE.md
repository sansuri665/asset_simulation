# 海湾→东亚单一船东运营原型

> 分支：`gulf-east-asia-monopoly-operations-prototype`  
> 模型：`asset-simulation-gulf-east-asia-monopoly-operations-v0.1.0`  
> 状态：实验；不进入 `main`

## 一、范围

这一版退回到一条航线，只模拟：

```text
Seed生成的海湾→东亚结构货量
        ↓
一家船东拥有全部固定VLCC
        ↓
船东选择本回合实际派出多少船
        ↓
未派出的船停在海湾港口/锚地
        ↓
派出的船执行五回合完整循环
        ↓
毛运费－航次成本－全部船舶OPEX
        ↓
单一船东运营现金结果
```

不模拟：

- 折旧；
- 船价与资产重估；
- 债务、本金和利息；
- 公司税与资本结构；
- 新船订单、交付；
- 拆船、买卖船；
- 期租与多家公司竞争。

因此这不是公司金融模型，而是固定资产规模下的运营状态实验。

## 二、固定船队和状态

船东固定拥有250艘编号VLCC：

```text
VLCC-0001 ... VLCC-0250
```

参考贸易在9.3 mb/d附近需要约239艘持续运力。模型开局把191艘放入四个已有航次队列，并在海湾留下59艘prompt船；正常首回合派出约48艘后，大致留下11艘缓冲。

每一艘派出的船自动完成五个shipping turns：

| 航次年龄 | 状态 |
|---:|---|
| 0 | 装货半回合 + 前半段重载航行 |
| 1 | 重载航行 |
| 2 | 后半段重载航行 + 卸货半回合 |
| 3 | 第一段压载返程 |
| 4 | 第二段压载返程 |
| 5 | 回到海湾，重新成为可调度船 |

进入航次后不能中途撤回。没有派出的船留在海湾，继续支付常规OPEX和待港燃油。

每回合严格检查：

```text
active voyage ships + Gulf idle ships = 250
```

不允许同一编号重复、消失或凭空生成。

## 三、货物与虚拟仓库

货量直接读取现有`oil_shipping_world`中该Seed真实生成的：

```text
gulf_east_asia.cargo_mbd
```

每月拆成3个约10日的shipping turns。

虚拟仓库继续使用相对正常管线的偏离：

```text
Gulf deviation
in-transit deviation
East Asia deviation
```

并严格满足：

```text
Gulf + transit + East Asia = 0
```

实际装船少于结构流量时，海湾相对积压；若此前少装的货抵达东亚，则东亚相对见底。库存只是偏离值，不是区域绝对库存。

## 四、船东收入

每一票标准货物为：

```text
270,000 tonnes
≈ 1.971 million barrels
```

市场先形成一项实际TCE。模型再按照完整航次成本反推出毛运费：

```text
Gross Freight
= [TCE × Cycle Days
   + Bunker
   + Port
   + Other Voyage Cost]
  / (1 - Commission Rate)
```

因此：

```text
Gross Freight
- Commission
- Bunker
- Port
- Other Voyage Cost
= Net Voyage Revenue
= TCE × Cycle Days
```

毛运费是船东营业收入；TCE仍然是扣除航次直接成本后的船舶日收益，不是最终利润。

## 五、航次成本

当前采用一组可配置的VLCC代表值：

| 项目 | 原型值 |
|---|---:|
| 重载燃耗 | 57.3 t/day |
| 压载燃耗 | 39.5 t/day |
| 装货港燃油 | 20 t |
| 卸货港燃油 | 110 t |
| 等待燃油 | 10 t |
| 海湾装货港费用 | 125,000美元（2025实际） |
| 东亚卸货港费用 | 175,000美元（2025实际） |
| 其他航次费用 | 25,000美元（2025实际） |
| 总佣金 | 3.75% |

燃油价格不直接等同Brent。原型先建立一个独立marine-bunker proxy：

```text
real bunker price
= 550 USD/t
× [25% + 75% × real oil price index / 100]
```

并限制在250–1,200美元/吨的2025购买力范围，再由CPI转成当期名义价格。以后可由独立船燃价格模块替换。

## 六、不开船也要承担的成本

全部250艘船每天都承担：

```text
Vessel OPEX = 9,500 USD/day，2025购买力
```

这一项代表船员、保险、日常维修、备件、物料、润滑油和管理等船舶存在成本。

船在海湾闲置时还承担：

```text
Idle fuel = 4 tonnes/day
```

所以停船不是零成本。折旧、债务和利息明确不在本模型内。

## 七、垄断运力控制

这一版不模拟owner/charterer逐票竞价，也不使用自由市场撮合。

每个回合，模型对可派出的不同船数逐一计算一条反向需求曲线：

```text
ln(TCE / baseline TCE)
= prompt shortage effect
+ virtual inventory pressure
+ structural demand level effect
```

船东选择使下式最大的派船数：

```text
contracted net voyage revenue
- Gulf idle bunker
- inventory/service shadow cost
```

其中库存影子成本只用于船东决策，表示长期客户、储罐约束、炼厂连续性和被干预风险；它不是财务报表中的实际费用。

默认自动策略最多主动扣留15艘prompt船。但外部实验控制可以：

```text
dispatch_override_vlcc = 0 ... available
additional_withholding_vlcc = 0 ... policy dispatch
```

因此可测试短期完全停航、额外压船或正常运营。

## 八、运营账

每个航次的毛运费和佣金在五个回合中平均确认；燃油和港口费用按对应航次阶段确认。每回合运营结果为：

```text
Accrued Gross Freight Revenue
- Accrued Commission
- Accrued Sailing/Port Bunker
- Accrued Port and Other Voyage Cost
- Gulf Idle Bunker
- OPEX on all 250 vessels
= Operating Cash Result
```

这里是便于比较的运营应计口径，不模拟真实发票和付款日期。

## 九、当前局限

1. 所有派出的船都自动压载返回海湾；尚不能在东亚卸货后长期停留。
2. 航速固定，不做slow steaming或抢速。
3. 港口拥堵、天气、故障和坞修尚未进入。
4. 库存影子成本是行为控制参数，不是已校准的现实合同罚金。
5. TCE仍有1,000–122,500美元/日的2025实际价格护栏。
6. 当前只有一家船东和一条航线，不代表现实市场结构。

## 十、验证

GitHub Actions会执行：

- 单元测试；
- Seed 0、1、5、7、42各20年；
- seed42基准；
- 连续三个shipping turns完全停航的对照。

验证结果将在代码通过后写回本节。
