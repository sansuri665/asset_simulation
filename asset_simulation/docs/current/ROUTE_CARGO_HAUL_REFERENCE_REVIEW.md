# 主要航线参考货量与航程复核

> 分支：`route-cargo-haul-reference-review`  
> 状态：参考审阅，不改变当前运行时配置  
> 参考年：2024  
> 货量单位：百万桶/日（mb/d）  
> 航程单位：海里（nm）

## 结论

本轮结论比较简单：

1. 当前 14 条主要航线的**参考货量不需要重做**；
2. 39.8 mb/d 的 5×5 参考矩阵继续作为结构中心；
3. 航程只是区域平均参考值，不追求港到港精确；
4. 14 条主航线中建议调整 8 条航程，其中真正明显需要改的主要是中东→南亚、西非→欧洲、其他出口区→南亚和其他出口区→欧洲；
5. 按完整 25 格参考货量加权，当前基准平均航程约 `5468.59 nm`，采用本轮建议后约 `5390.95 nm`，只下降约 `1.42%`，不会改变现有海运吨海里底盘的数量级。

因此本轮是参数清理，而不是航线系统重构。

## 参考货量复核

当前货量矩阵与 Energy Institute《Statistical Review of World Energy 2025》的 2024 年 crude inter-area movements 相当吻合。

几个直接可核的例子：

- 美国 → 欧洲：`95.8 Mt/yr`，按 7.3 桶/吨约为 `1.92 mb/d`，模型为 `1.9 mb/d`；
- 中东 → 欧洲：伊拉克、科威特、沙特、阿联酋和其他中东合计约 `79.4 Mt/yr`，约 `1.59 mb/d`，模型为 `1.6 mb/d`；
- 中东 → 印度：合计约 `112.2 Mt/yr`，约 `2.24 mb/d`，模型南亚为 `2.4 mb/d`，留有印度以外南亚需求；
- 西非 → 欧洲：`55.7 Mt/yr`，约 `1.11 mb/d`，模型为 `1.1 mb/d`；
- 西非 → 印度：`13.2 Mt/yr`，约 `0.26 mb/d`，模型南亚为 `0.3 mb/d`；
- 俄罗斯 → 印度：`87.5 Mt/yr`，约 `1.75 mb/d`，模型其他出口区→南亚为 `1.7 mb/d`；
- 南美 → 中国：`52.6 Mt/yr`，约 `1.05 mb/d`，模型巴西／圭亚那→东亚为 `1.1 mb/d`，数量级合适；
- 美国 2024 年原油出口总量约 `4.1 mb/d`，其中欧洲约 `1.93 mb/d`，与模型美湾出口和美湾→欧洲锚一致。

中东→东亚的 `9.3 mb/d` 继续保留。EIA 对 2024 年霍尔木兹流量的统计显示约 84% 的原油和凝析油流向亚洲，中国、印度、日本和韩国是主要目的地；模型把印度单列南亚后，东亚约 9 mb/d 的结构中心合理。

主要参考：

- Energy Institute Statistical Review of World Energy 2025: https://www.energyinst.org/statistical-review/resources-and-data-downloads
- EIA, U.S. crude oil exports reached a new record in 2024: https://www.eia.gov/todayinenergy/detail.php?id=64964
- EIA, Strait of Hormuz destination markets: https://www.eia.gov/todayinenergy/detail.php?id=65504

## 14 条主要航线审阅结果

| 航线 | 参考货量 mb/d | 当前航程 nm | 建议航程 nm | 处理 |
|---|---:|---:|---:|---|
| 中东出口区 → 东亚 | 9.3 | 6200 | 6200 | 保留 |
| 中东出口区 → 南亚 | 2.4 | 2600 | **1800** | 下调 |
| 中东出口区 → 欧洲 | 1.6 | 6100 | 6100 | 保留 |
| 美国湾岸 → 欧洲 | 1.9 | 4700 | **5000** | 小幅上调 |
| 美国湾岸 → 东亚 | 0.9 | 14000 | **14500** | 小幅上调 |
| 巴西／圭亚那 → 东亚 | 1.1 | 11000 | 11000 | 保留 |
| 巴西／圭亚那 → 欧洲 | 0.8 | 5000 | 5000 | 保留 |
| 巴西／圭亚那 → 北美进口区 | 0.4 | 3500 | 3500 | 保留 |
| 西非 → 东亚 | 1.2 | 10500 | **10200** | 小幅下调 |
| 西非 → 欧洲 | 1.1 | 3500 | **4200** | 上调 |
| 西非 → 南亚 | 0.3 | 7500 | **6800** | 下调 |
| 其他出口区 → 东亚 | 2.2 | 6500 | 6500 | 保留，低置信度 |
| 其他出口区 → 南亚 | 1.7 | 4500 | **6000** | 上调 |
| 其他出口区 → 欧洲 | 3.8 | 3500 | **2200** | 下调 |

## 航程依据

### 中东 → 东亚

Baltic TD3C 的标准路线是 Ras Tanura → Ningbo。公开航程工具给出的距离约 `5840 nm`；Ras Tanura → 日本／韩国在 `6200–6600 nm` 左右。因此模型以 `6200 nm` 作为整个东亚盆地平均值是合理的。

参考：

- Baltic TD3C: https://www.balticexchange.com/en/data-services/Circulars/market-announcements-/category-a/2017/circular-7-17-new-tce-for-route-td3c.html
- Poseidon route reference: https://poseidonbrokerage.com/sea-distance-calculator

### 中东 → 南亚

Clarksons 的 Ras Tanura → Jamnagar 代表航线约 `1184 nm`。印度进口炼厂并非全部在古吉拉特，因此模型平均值应该高于 1184 nm，但 `2600 nm` 对以印度西海岸为主的中东进口明显偏高。本轮建议 `1800 nm`。

### 美国湾岸 → 欧洲

Houston/Corpus Christi → Rotterdam 公开路线大约 `5000 nm`，因此从 `4700` 调到 `5000 nm`。

### 美国湾岸 → 东亚

这是典型超长航线。Galveston/US Gulf → Ningbo 的 VLCC 航线处在 mid-14k nm 量级，因此把 `14000` 微调为 `14500 nm`，但不值得继续追求个位数精度。

### 西非 → 欧洲

Bonny/Lagos → Rotterdam 的典型航程在 `4.3–4.5k nm`，Luanda → 南欧略短。当前 `3500 nm` 偏低，建议用 `4200 nm` 作为西非大篮子均值。

### 西非 → 南亚

Clarksons 的 Bonny → Jamnagar 参考约 `7075 nm`。考虑安哥拉等更南来源后，采用 `6800 nm` 作为盆地平均值。

### 其他出口区

这是最不适合追求精确距离的区域，因为同时包含俄罗斯远东、波罗的海、北海、黑海／里海和北非等来源。

- → 东亚：保持 `6500 nm`，明确标记为低置信度聚合值；
- → 南亚：俄罗斯 Baltic → India 的现实航程约 `6800–7000 nm`，而黑海／里海更短，因此 `6000 nm` 比 `4500 nm` 更合适；
- → 欧洲：Other CIS、北非、北海和残余俄罗斯对欧洲大多属于短中程运输，因此 `3500 nm` 偏长，本轮建议 `2200 nm`。

俄罗斯 Baltic → India 参考：

- ArcNautical Ust-Luga → Mundra: https://arcnautical.com/routes/ust-luga-mundra/

## 对未来 15 日船舶层的意义

这些距离仍然只负责：

```text
货量 × 航程 → 吨海里
```

未来真船层不要机械把 `nm / speed` 四舍五入成航行回合。应另设：

- `laden_voyage_days`
- `port_and_waiting_days`
- `normal_15d_ticks`
- `rerouted_15d_ticks`

例如中东→东亚约 20 个纯航海日，商业占用可自然落在约 2 个 15 日回合；美国湾岸→东亚约 46 个纯航海日，则应进入 3–4 个回合级别。

## 是否应立即写回主配置

本分支暂时只提交审阅结果，不直接改 `oil_shipping_demand_v0.6.json`。原因是：

1. 货量锚本轮没有发现需要修改的系统性问题；
2. 航程修正对参考加权平均航程只影响约 1.42%；
3. 在真船层开始前，这些参数主要用于吨海里参考，而不是船舶占用时间；
4. 先保留一份明确的审阅表，更方便下一步决定是否整体写回主配置。
