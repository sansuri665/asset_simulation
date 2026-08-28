# 西欧／北欧 v0.1 实装指引任务书

> 历史归档，不是当前实现权威
> 原状态：活动 Goal，已于 2026-08-15 完成并迁入当前代码
> 当前事实以 `docs/regions/WEST_NORTH_EUROPE.md`、`docs/current/` 与代码／JSON 契约为准
> 执行对象：Cursor CLI / Grok，允许在本任务边界内独立完成代码、契约、国际层、API、Viewer、测试与审计
> 目标区域：`west_north_europe`
> 用户显示：`西欧／北欧`
> 代表计价币：`EUR`
> 当时基线：全球 v0.7、北美 v0.6、中国大陆 v0.4、G17 v0.2、World v0.3、Viewer v4.2
> 制定日期：2026-08-15

## 1. 任务结论

把 `map_research` 14 区域口径中的 `west_north_europe` 建成 Asset Simulation 的第三个正式区域，并将国际层从：

```text
北美 + 中国大陆 + 剩余世界
```

升级为：

```text
北美 + 中国大陆 + 西欧／北欧 + 剩余世界
```

这是一个完整 Goal，不只写内核。最终必须同时完成：

1. 西欧／北欧区域年度模型；
2. 本币实际／名义账户；
3. 五类 G17 滞后端口消费；
4. 四参与者贸易、资本、货币与储备清算；
5. World/RoW 共同币种对账；
6. API、单一缓存和 Viewer Scope；
7. 单元测试、方向测试、700 Seed 分段审计与文档收口。

不得只做一个孤立区域，也不得用零 G17 夹具冒充产品接入。

## 2. 区域边界

区域 ID 必须沿用地图口径：

```text
west_north_europe
```

范围以 `map_research` 的区域归属为准，包括英国、爱尔兰、法国、德国、荷比卢、瑞士、奥地利、捷克和北欧等西欧／北欧经济体；南欧、巴尔干和地中海东南欧洲继续属于：

```text
south_east_europe_mediterranean
```

不得把本区域改名为 `euro_area`，也不得擅自移动地图中的国家或城市。

### 2.1 欧元口径的真实含义

`EUR` 是区域代表计价币，作用类似东南亚未来使用新加坡元、南亚使用印度卢比：它统一区域金额和 Viewer 显示，不表示英国、瑞士、北欧等成员在现实法律上加入欧元区。

政策率采用“ECB 权重占主导的欧洲代表性政策立场”。非欧元经济体的制度差异压缩进银行信贷条件、主权碎片化和本地创新，不另建英镑、瑞郎、克朗子模型。

Viewer 必须显示“西欧／北欧”和“欧元资金”，不得显示“欧元区”或声称所有成员共用 ECB 政策。

## 3. 执行前必须建立的上下文

先完整读取以下当前文件，再修改代码：

```text
asset_simulation/CLAUDE.md
asset_simulation/docs/MODEL_CONTEXT_GUIDE.md
asset_simulation/docs/INDEX.md
asset_simulation/docs/current/RUNTIME_ARCHITECTURE.md
asset_simulation/docs/current/CONTRACTS_AND_UNITS.md
asset_simulation/docs/current/G17_LITE.md
asset_simulation/docs/current/VIEWER_PROJECTION.md
asset_simulation/docs/decisions/ADR-001-ANNUAL-SEQUENCING.md
asset_simulation/docs/decisions/ADR-002-NOMINAL-REAL-PRICES.md
asset_simulation/docs/decisions/ADR-003-REGIONAL-ONE-WAY-AND-G17.md
asset_simulation/docs/decisions/ADR-004-SINGLE-COUPLED-WORLD.md
asset_simulation/docs/regions/NORTH_AMERICA.md
asset_simulation/docs/regions/CHINA_MAINLAND.md
asset_simulation/contracts/regional_macro_extension_v3.json
asset_simulation/contracts/g17_lite_v1.json
asset_simulation/model/north_america.py
asset_simulation/model/china_mainland.py
asset_simulation/model/china_components/*.py
asset_simulation/model/g17_lite.py
asset_simulation/model/world.py
asset_simulation/model/registry.py
asset_simulation/server.py
asset_simulation/viewer/index.html
asset_simulation/viewer/static/css/style.css
asset_simulation/viewer/static/js/app.js
asset_simulation/audit_volatility.py
asset_simulation/tests/*.py
```

还要读取地图事实：

```text
map_research/app.js
map_research/airports.js
map_research/transport_cities.js
```

只需使用 `west_north_europe` 的归属信息，不修改地图文件。

### 3.1 旧模型只能怎样使用

以下旧文档可用于提取经济直觉：

```text
capital_market_lab/docs/WEST_NORTH_EUROPE_REAL_PRICE_NOMINAL_MODEL.md
capital_market_lab/docs/WEST_NORTH_EUROPE_MONETARY_POLICY_MODEL.md
capital_market_lab/docs/WEST_NORTH_EUROPE_PUBLIC_FINANCE_CENTRAL_BANK_MODEL.md
capital_market_lab/docs/WEST_NORTH_EUROPE_PUBLIC_YIELD_CREDIT_MODEL.md
capital_market_lab/docs/WEST_NORTH_EUROPE_CURRENCY_FUNDING_MODEL.md
capital_market_lab/docs/WEST_NORTH_EUROPE_OIL_GAS_ENERGY_MODEL.md
capital_market_lab/docs/WEST_NORTH_EUROPE_ASSET_WEALTH_MODEL.md
capital_market_lab/docs/WEST_NORTH_EUROPE_FINAL_REVIEW.md
```

它们不是当前运行时。禁止：

- 导入 `capital_market_lab`；
- 复制旧版 14 区域 reconciliation runner；
- 搬运数百个旧字段和多次全路径求解；
- 恢复旧 Viewer 的解释卡、版本徽章或超长指标墙；
- 用旧测试数量或旧哈希冒充当前验收。

可保留的经济直觉只有：低潜在增速、2% 价格锚、银行融资占主导、主权碎片化、能源进口敏感、欧元具有第二层级储备货币作用。

## 4. 不可破坏的架构红线

1. 全球 `t` 先完整结账，西欧／北欧 `t` 读取全球 `t` 与 G17 `t-1`。
2. 区域不得反写同年全球，G17 不得触发区域同年重跑。
3. 五类国际输入必须逐项有消费者；独立区域 runner 才允许显式零端口。
4. 普通 Seed 不绑定危机、战争、疫情、英国脱欧、欧债危机、国运或称号世界。
5. 特殊事件只走未来显式端口；本任务不创建事件表。
6. 汇率由 G17 清算。区域内部不得再生成一条独立 EUR/USD 随机路径。
7. 本地账户使用 EUR；共同美元换算只存在于 World、RoW 和 G17。
8. 名义 GDP、CPI、平减指数、名义能源和资产总回报必须遵守现有口径，不重复计入通胀。
9. `rest_of_world` 是精确残差，不是靠 clamp 修出来的余额。
10. 不得为了降低 warning 而普遍压低波动；先判断 warning 是普通周期 rail、过程边界还是实际错误。

## 5. 现实参考与设计取舍

这些现实事实只用于确定机制方向，不要求模型逐年预测现实：

- ECB 的中期 HICP 目标为对称的 2%，因此长期通胀预期应围绕 2% 缓慢变化。
- Eurostat 报告的 2025 欧元区政府赤字约 2.9% GDP、债务约 87.8% GDP，可作为合成欧洲公共账户的数量级检查；本区域比欧元区更宽，不能直接照抄该总量。
- ECB 银行贷款调查把贷款标准与贷款需求作为货币和经济评估的重要信息，因此欧洲信用层应比北美更突出银行信贷传导。
- ECB 的 TPI 说明统一政策面对主权融资条件碎片化，因此区域需要一个温和的“核心基准 + 碎片化溢价”结构，但不需要成员国逐国债券市场。
- 欧洲能源进口依赖较高，油气进口价格应比北美更明显地进入贸易条件与通胀；但全球现阶段没有完整天然气市场，所以天然气只能是可替换的区域代理。
- 欧元是第二层级国际储备货币，储备权重应显著高于人民币、显著低于美元。

官方参考：

- [ECB monetary policy strategy](https://www.ecb.europa.eu/mopo/strategy/html/index.en.html)
- [ECB euro area bank lending survey](https://www.ecb.europa.eu/stats/ecb_surveys/bank_lending_survey/html/index.en.html)
- [ECB Transmission Protection Instrument](https://www.ecb.europa.eu/press/pr/date/2022/html/ecb.pr220721~973e6e7273.en.html)
- [Eurostat 2025 euro area deficit and debt](https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-22042026-ap)
- [Eurostat Energy in Europe 2026](https://ec.europa.eu/eurostat/web/interactive-publications/energy-2026)
- [ECB international role of the euro 2026](https://www.ecb.europa.eu/press/other-publications/ire/html/ecb.ire202606.de.html)

## 6. 区域模型愿景

目标不是制造“欧洲长期衰落”的剧本，而是在普通世界中形成有辨识度、但不被结论预设的西欧／北欧资本市场基座：

- 潜在增长低于全球和北美，长期缓慢收敛；
- 普通周期与全球高度相关，同时保留制造业、服务业和本地信用周期；
- 通胀预期稳，但能源、进口成本和工资能产生中期波动；
- 统一代表性政策利率面对不同主权融资条件；
- 财政总量可持续，但碎片化会影响曲线、银行与信用；
- 银行信贷比企业债融资更重要；
- 欧元具有国际货币支持，但风险收缩时不应机械复制美元避险表现；
- 能源进口冲击同时影响实际需求、通胀、贸易条件和盈利；
- 权益与债券是宏观复利参考，不是短期价格预测。

不同 Seed 可以让本区长期相对表现稍强或稍弱，但本任务不实现“国运结构层”。长期分化只能来自普通周期、生产率、政策、能源和国际账户的累积结果，不能暗中内定欧洲崛起或衰落。

## 7. 建议文件布局

参考中国大陆的三组件编排，避免再写一个超大单文件：

```text
asset_simulation/model/west_north_europe.py
asset_simulation/model/west_north_europe_components/__init__.py
asset_simulation/model/west_north_europe_components/real_prices.py
asset_simulation/model/west_north_europe_components/policy_credit.py
asset_simulation/model/west_north_europe_components/public_market_energy.py
asset_simulation/config/west_north_europe_v0.1.json
asset_simulation/contracts/west_north_europe_support_v1.json
asset_simulation/tests/test_west_north_europe.py
asset_simulation/docs/regions/WEST_NORTH_EUROPE.md
```

`west_north_europe.py` 只负责编排、校验、发布 identity 和 summary。公式应放在三个组件中。

## 8. 年度主链

```text
global[t] + G17→WNE[t-1]
→ 潜在增长、普通周期、实际 GDP
→ 核心／总体通胀、工资、CPI、平减指数、名义 GDP
→ 代表性欧洲政策率
→ 财政、债务、净发行、期限与央行数量
→ 核心公共曲线、碎片化溢价、聚合 2Y/10Y
→ 欧元资金、银行贷款条件、IG/HY 与信用可得性
→ 进口油气、贸易条件、盈利
→ 权益／主权债复利参考、风险偏好与最终 FCI
```

区域行必须投影 `regional_macro_extension_v3` 的全部公共字段。只有确实有直接消费者或 Viewer 用途的欧洲辨识度字段才能进入支持契约。

## 9. 三个组件的最低设计

### 9.1 `real_prices.py`

建议初始锚，不是必须精确照抄的最终参数：

```text
2025 实际 GDP：约 17.5–18.5 万亿 EUR 的合成工程锚
起步潜在增长：约 1.35%–1.55%
长期潜在增长：约 1.05%–1.25%
2025 产出缺口：约 -0.2%
长期通胀预期：2.0%
```

潜在增长结构应简洁：长期锚 + 缓慢收敛 + 全球潜在增速小比例偏离 + 本地低频创新。不得恢复人口、资本深化、技术前沿九组件旧模型。

普通周期主要继承全球共同周期，本地创新较弱；实际 GDP 恒等式沿用：

```text
potential_gdp[t] = potential_gdp[t-1] × (1 + potential_growth[t] / 100)
real_gdp[t] = potential_gdp[t] × exp(output_gap[t] / 100)
realized_growth[t] = real_gdp[t] / real_gdp[t-1] - 1
```

通胀最低包含 headline、core、expectation、wage、food/energy gap、CPI 与 GDP deflator。欧洲辨识度来自工资惯性、进口价格和能源，而不是额外堆十几个 CPI 分项。

名义 GDP 必须逐年闭合：

```text
nominal_gdp_eur = real_gdp_2025_eur × gdp_deflator / 100
```

### 9.2 `policy_credit.py`

代表性政策率围绕：

```text
中性实际利率 + 通胀偏离 + 产出缺口 + 金融传导压力
```

形成平滑目标。允许采用常见 25bp 决策步长和暂停阶段，但文档必须声明这是合成欧洲政策立场，不是所有成员央行共同决定。

信用以银行渠道为主：

- `bank_lending_conditions_index` 收紧时，信用可得性下降、贷款传导变弱；
- 资本流入与欧元储备需求缓解资金压力；
- 主权碎片化提高银行融资与信用利差；
- IG/HY 仍需投影公共可比字段，但不能把 HY 当成欧洲全部信用体系。

不得创建独立于 G17 的欧元随机汇率。`regional_currency_index` 只吸收上一年 G17 汇率变化。

### 9.3 `public_market_energy.py`

公共账户采用合成总量：收入、初级支出、总体余额、债务、有效利息率、净发行、期限供给。2025 工程锚可从以下范围开始：

```text
总体余额：约 -2.5% 至 -3.2% GDP
公共债务：约 85% 至 95% GDP
央行资产：约 35% 至 50% GDP
```

曲线结构：

```text
核心 10Y = 预期短端 + 期限溢价 + 核心主权风险
高债 10Y = 核心 10Y + 碎片化溢价
公共 10Y = 核心与高债曲线的固定合成
```

成员国不逐一模拟。碎片化溢价读取聚合债务负担、利息负担、久期供给、银行压力和有限的传导支持；普通 Seed 中应缓慢变化，不能单年随机跳成欧债危机。

央行数量层允许普通资产负债表缓慢扩张或收缩。危机型 QE、TPI 大规模购买和特殊流动性工具仍留给未来事件端口。`qe_qt_flow_pct_gdp` 可以在普通年份接近零，但 `central_bank_assets_pct_gdp` 与 `reserves_pct_gdp` 不应成为完全水平线。

能源层使用全球 Brent、实际商品环境、欧元变化和本地能源创新生成：

- 名义进口能源指数；
- 实际进口能源压力；
- 天然气进口成本代理；
- 贸易条件。

天然气只是区域可替换代理，不得在文档中声称全球天然气市场已完成。

资产沿用当前名义主显示／实际审计口径。欧洲权益应体现较低估值、更高分红、银行和工业盈利对信用及能源更敏感；主权债总回报读取票息、久期与收益率变化。

## 10. 欧洲支持契约

新建 `west_north_europe_support_v1`，`common_contract_id` 指向现有 `regional_macro_extension_v3`。公共契约本身没有新增共同字段时，不升级 v3。

支持字段控制在约 6–8 项，建议：

```text
wage_pressure_pct
bank_lending_conditions_index
sovereign_fragmentation_premium_bps
core_sovereign_yield_10y_pct
high_debt_sovereign_yield_10y_pct
gas_import_cost_index_2025_100
imported_energy_price_index_2025_100
```

每项必须声明 owner、单位、时点、范围和直接消费者。仅为“看起来专业”而无人使用的字段不得加入。

## 11. G17 与 World 接入

### 11.1 版本

若按本任务完整接入，建议版本：

```text
west_north_europe model: v0.1
west_north_europe support contract: v1
G17-lite: v0.3
World: v0.4
Viewer/service: v4.3
```

`regional_macro_extension_v3` 与 `g17_lite_v1` 的字段语义未变时可以保留；参与者集合和模型身份变化仍必须升级 G17 配置、G17 model、World 和服务身份。

### 11.2 G17 参与者

把硬编码三方逻辑泛化为四方：

```text
REGION_IDS = (
  north_america,
  china_mainland,
  west_north_europe,
  rest_of_world,
)
```

建议从以下权重开始校准，而不是把它们当作冻结事实：

| 参与者 | trade weight | base trade balance | capital mobility | FX speed | reserve weight | energy exposure |
|---|---:|---:|---:|---:|---:|---:|
| 北美 | 0.27 | -2.8% | 0.95 | 0.90 | 1.00 | 0.10 |
| 中国大陆 | 0.18 | +2.2% | 0.60 | 0.62 | 0.12 | 1.00 |
| 西欧／北欧 | 0.21 | +1.8% | 0.90 | 0.82 | 0.40 | 0.65 |
| 剩余世界 | 0.34 | residual | 0.72 | 0.72 | 0.20 | -0.18 |

欧洲在国际风险收缩时不应机械复制美元：美元仍是主要避险与储备货币；欧元可以获得部分储备支持，但资本流和汇率方向还取决于相对增长、实际利率、碎片化和能源贸易条件。

### 11.3 必须消除的硬编码

现有 G17 中贸易与资本金额只对北美、中国计算，然后把 RoW 设为残差。改为：

```text
formal_regions = north_america + china_mainland + west_north_europe
rest_of_world amount = -sum(formal_region amounts)
```

伙伴均值、货币加权去均值、储备需求、summary、测试和审计必须基于动态参与者集合，不得再散落多个“两区专用”循环。

### 11.4 EUR/USD 与共同美元换算

配置中增加合成 2025 锚：

```text
base_eur_per_usd ≈ 0.90–0.95
```

区域本地行始终使用 EUR。World 可沿现有 CNY/USD 相对指数逻辑形成：

```text
eur_per_usd[t]
= base_eur_per_usd
× north_america_currency_index[t]
/ west_north_europe_currency_index[t]
```

实际 GDP 用固定 2025 换算率进入共同 2025 美元；名义 GDP 用当年换算率进入共同当年美元。不得在本地欧洲 Viewer 显示美元 GDP。

### 11.5 RoW 对账

每年必须满足：

```text
global real GDP
= NA real GDP common USD
+ China real GDP common USD
+ WNE real GDP common USD
+ RoW real GDP

global nominal GDP
= NA nominal GDP common USD
+ China nominal GDP common USD
+ WNE nominal GDP common USD
+ RoW nominal GDP
```

RoW 实际和名义 GDP 必须始终为正。若出现负值，应检查初始规模、换算或区域增长，禁止直接 clamp。

G17 继续满足贸易、资本、货币篮子与每区国际收支残差 `<1e-8`。

## 12. 五类国际端口消费者

西欧／北欧必须消费上一年 G17 的全部五项：

| G17 `t-1 → t` 端口 | 西欧／北欧消费者 |
|---|---|
| `external_demand_impulse_pp` | 普通周期与出口需求 |
| `trade_balance_impulse_pct_gdp` | 净出口需求和贸易条件 |
| `net_capital_flow_impulse_pct_gdp` | 欧元资金、银行贷款条件与信用利差 |
| `bilateral_fx_change_pct` | 欧元指数、进口通胀、能源成本与下一年竞争力 |
| `reserve_currency_demand_impulse_index` | 欧元资金支持、流动性与有限期限溢价支持 |

首年端口显式为零；默认 World 从第二个年度开始必须存在温和非零值。每行保留端口值，便于检查来源年和目标年。

## 13. API 与 Viewer

### 13.1 API

`/api/world` 增加：

```text
regions.west_north_europe
```

增加调试投影：

```text
GET /api/west-north-europe?seed=42&years=60
```

它必须来自同一个 `_WORLD_CACHE`，不能新建欧洲专用产品缓存或重复运行世界。

### 13.2 Viewer Scope

新增：

```text
scope=west_north_europe
label=西欧／北欧
currency=EUR
```

沿用全球、北美、中国的成熟布局，不新增解释性版本徽章。默认仍为 8 张核心卡：

1. 实际 GDP；
2. 名义 GDP；
3. 总体通胀；
4. 政策与 10Y；
5. 欧元资金；
6. 高收益利差 / FCI；
7. 进口能源；
8. 权益复利参考。

模块建议：总览、实际 GDP、名义 GDP、增长、通胀、利率、欧元资金、信用、能源商品、资产参考、公共账户。

### 13.3 国际 Scope

新增区域后仍避免指标墙。国际顶部维持 6 张卡，建议改为：

1. 国际风险周期；
2. USD/CNY 相对指数；
3. EUR/USD 相对指数；
4. 北美贸易／资本；
5. 中国贸易／资本；
6. 西欧／北欧贸易／资本。

美元、人民币和欧元储备需求进入“储备需求”模块及右侧年度明细，不再各占一张顶部卡。对账模块必须显示四参与者和新的 RoW 残差。

Viewer 需保留 pointer 年份联动、窄屏可用、无 `NaN`/`undefined`/`Infinity`、控制台无 error。

## 14. 随机、版本与身份

- 新随机地址使用 `west_north_europe.*` 或更具体组件前缀，不复用北美、中国或旧 Lab 地址。
- 相同 Seed 和年数必须完全复算；短窗口严格等于长窗口前缀。
- 增加欧洲不得改变全球单独运行的任何 row 或 identity。
- 北美／中国独立零端口夹具保持可运行；默认耦合路径因 G17 参与者增加而变化是预期行为。
- 配置、支持契约、模型、G17、World、服务和结果 identity 必须包含新版本与哈希。
- 旧配置文件保留；注册表只指向新默认版本。

## 15. 测试与审计

### 15.1 欧洲专项测试

至少覆盖：

- determinism 与 prefix；
- 本币实际／名义 GDP 恒等式；
- CPI 与平减指数复利；
- 公共债务递推与期限份额；
- 核心／高债／聚合曲线关系；
- 碎片化上升不会降低银行资金压力和 HY；
- 资本流入改善资金与信用；
- 欧元升值降低进口通胀；
- 实际能源冲击恶化贸易条件；
- 五类 G17 端口都有方向性消费者；
- 独立 runner 的零端口夹具仍成立。

### 15.2 World/G17 回归

至少覆盖：

- 四参与者账户集合；
- 全球实际／名义 GDP 对账；
- RoW 始终为正；
- 贸易、资本和币篮残差 `<1e-8`；
- EUR/USD 相对指数方向；
- 风险收缩优先支持美元，欧元表现由储备支持与欧洲基本面共同决定；
- World identity、API payload、缓存键和四个正式 Scope。

### 15.3 分布愿景

先用小样本调试，最终只运行一次 0–699、60 年的三段审计。建议检查区间：

```text
实际增长长期均值：1.1%–1.8%
实际增长标准差：0.75–1.15pp
普通负增长频率：0.3%–2.5%
headline 长期均值：1.8%–2.3%
headline 标准差：0.55–0.95pp
10Y 长期中枢：约 2.0%–4.5%
EUR 年度变化标准差：约 1.2%–2.1%
贸易余额长期中枢：约 +0.5%–+3.0% GDP
欧洲与全球增长相关：约 0.75–0.92
```

这些是普通世界校验带，不是硬裁剪边界。不要为了让所有 Seed 落在区间内而 clip。

审计工具需要加入欧洲：水平、变化、bounds、负增长、通胀尾部、曲线、FX、贸易、资本、五端口、身份和非有限值。保留 calibration 0–399、validation 400–499、holdout 500–699 的隔离。

### 15.4 最低命令

```powershell
cd C:\d_e\oiltanker\airport
py -3.13 -m unittest discover -s asset_simulation/tests -v
node --check asset_simulation/viewer/static/js/app.js
py -3.13 -m asset_simulation.audit_volatility --profile goal-c --output <new-report-path>
```

最后重启 8783，确认 `/api/health` 返回新版本，再检查全球、北美、中国大陆、西欧／北欧和国际五个 Scope。

## 16. 完成定义

只有同时满足以下条件，才能宣布 Goal 完成：

- 欧洲不是孤立夹具，而是默认 `CoupledWorldRun` 的正式成员；
- G17 已由三方泛化为四方，RoW 已相应缩小并保持正值；
- 五类欧洲端口真实非零、严格滞后且有方向性消费者；
- 本地 EUR 与共同美元换算口径闭合；
- 全球结果保持不变，北美和中国没有被偷偷重校准；
- API、缓存、Viewer、版本和文档同步；
- 全部测试通过，700 Seed 审计 0 个硬失败，跨段漂移通过；
- 对剩余 warning 逐项解释，不以 warning 数量下降代替经济合理性；
- 写成 `docs/regions/WEST_NORTH_EUROPE.md` 的当前事实文档；
- 将本任务书移动到 `docs/archive/`，加注“历史归档，不是当前实现权威”，并从 `docs/INDEX.md` 的活动路线移除。

## 17. 允许执行者自行判断的范围

执行者可以自行调整具体系数、bounds、初始工程锚和内部函数拆分，只要：

1. 不突破时序、单位、owner 和 G17 守恒红线；
2. 给出调整前后的分布证据；
3. 不通过压低所有随机项来消除告警；
4. 不创建任务范围外的特殊事件、国运或称号世界；
5. 不修改 `map_research` 和 `capital_market_lab`；
6. 遇到必须改变公共契约语义、14 区域边界或国际账户恒等式的情况时停止并报告，而不是自行扩大权限。

