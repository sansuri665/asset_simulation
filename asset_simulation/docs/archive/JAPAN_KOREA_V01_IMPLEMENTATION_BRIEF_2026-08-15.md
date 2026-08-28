# 日韩区域 v0.1 实装指引任务书

> 历史归档，不是当前实现权威
> 原状态：活动 Goal，已于 2026-08-15 完成并迁入当前代码
> 当前事实以 `docs/regions/JAPAN_KOREA.md`、`docs/current/` 与代码／JSON 契约为准
> 目标执行者：Grok / Cursor CLI  
> 最终审查者：Codex  
> 当时基线：全球 v0.7、北美 v0.6、中国大陆 v0.4、西欧／北欧 v0.1、G17 v0.3、World v0.4、Viewer v4.3  
> 目标区域 ID：japan_korea  
> 界面名称：日韩  
> 代表计价货币：JPY（日元）

## 0. 任务结论

在当前 asset_simulation 中新增正式区域“日韩”，沿用已经冻结的轻量架构，但不能复制旧 capital_market_lab 的大型区域模型。

这一版必须一次完成：

1. 可独立运行、可复现、具有普通周期的日韩区域内核；
2. 国际层的需求、贸易、资本、汇率、储备需求五端口；
3. 全球实际／名义 GDP、贸易、资本和 RoW 残差对账；
4. API、缓存、日韩界面、国际界面；
5. 定向测试、700 Seed 三段审计、正式文档与任务归档。

本任务是一个整体 Goal。不要在每个小步骤后重复运行全量测试；模型与接入完成后统一跑定向测试，最终只运行一次 700 Seed 审计。

---

## 1. 区域边界与代表性

### 1.1 边界

区域边界服从 map_research 的 14 区域定义，不修改地图。

资本市场和公开宏观权重主要由日本与韩国构成；城市和机场范围至少覆盖 Tokyo、Osaka/Kansai、Seoul、Busan、Fukuoka、Sapporo、Nagoya、Okinawa/Naha、Jeju。

本 Goal 不建立城市、机场或航空需求模型。

### 1.2 代表货币

区域使用 JPY 作为统一展示和账户换算货币。这只是聚合模型的代表计价单位：

- 不表示现实政治或货币联盟；
- 不表示韩国法律货币被替换；
- 韩国式外币融资、出口与信用敏感性仍要保留；
- 日本式低中性利率、国内储蓄、央行持债和安全资产特征仍要保留。

内部经济直觉采用约 70% 日本、30% 韩国作为初始锚点。该比例不是界面指标。

### 1.3 必须具有的辨识度

- 潜在增长偏低，但制造业和出口周期明显；
- 通胀中枢接近 2%，长期锚较强，允许低通胀年份；
- 政策利率中枢低，允许轻微负利率，但普通基座不启用 YCC；
- 政府债务率很高，但主权风险不能机械爆炸；
- 国内资金、央行持债和期限吸收缓和债务供给压力；
- 韩国式美元融资敏感性不能消失；
- 日元有一定避险与资金回流属性，但日韩综合货币不能复制美元；
- 能源进口依赖高；
- 股票盈利对全球制造业、半导体、汇率和能源成本敏感。

不能把日韩做成缩小版北美、换币版西欧／北欧或旧模型的裁剪版。

---

## 2. 开工前必须完整阅读

不要只读摘要，也不要凭文件名推断代码。

### 2.1 入场、架构与合同

- asset_simulation/CLAUDE.md
- asset_simulation/docs/README.md
- asset_simulation/docs/INDEX.md
- asset_simulation/docs/MODEL_CONTEXT_GUIDE.md
- asset_simulation/docs/current/RUNTIME_ARCHITECTURE.md
- asset_simulation/docs/current/CONTRACTS_AND_UNITS.md
- asset_simulation/docs/current/G17_LITE.md
- asset_simulation/docs/current/MODEL_QUALITY_AUDIT.md
- asset_simulation/docs/current/VIEWER_PROJECTION.md
- asset_simulation/docs/decisions/ADR-001-ANNUAL-SEQUENCING.md
- asset_simulation/docs/decisions/ADR-002-NOMINAL-REAL-PRICES.md
- asset_simulation/docs/decisions/ADR-003-REGIONAL-ONE-WAY-AND-G17.md
- asset_simulation/docs/decisions/ADR-004-SINGLE-COUPLED-WORLD.md

### 2.2 当前运行 owner

- asset_simulation/model/engine.py
- asset_simulation/model/world.py
- asset_simulation/model/g17_lite.py
- asset_simulation/model/contracts.py
- asset_simulation/model/registry.py
- asset_simulation/model/random_stream.py
- asset_simulation/model/north_america.py
- asset_simulation/model/china_mainland.py
- asset_simulation/model/west_north_europe.py
- asset_simulation/model/china_components/
- asset_simulation/model/west_north_europe_components/

### 2.3 配置与契约

- asset_simulation/config/global_macro_v0.7.json
- asset_simulation/config/g17_lite_v0.3.json
- asset_simulation/config/north_america_v0.6.json
- asset_simulation/config/china_mainland_v0.4.json
- asset_simulation/config/west_north_europe_v0.1.json
- asset_simulation/contracts/global_macro_minimum_v3.json
- asset_simulation/contracts/regional_macro_extension_v3.json
- asset_simulation/contracts/g17_lite_v1.json
- asset_simulation/contracts/china_mainland_support_v2.json
- asset_simulation/contracts/west_north_europe_support_v1.json

### 2.4 服务、界面、测试和审计

- asset_simulation/server.py
- asset_simulation/viewer/index.html
- asset_simulation/viewer/static/js/app.js
- asset_simulation/viewer/static/css/viewer.css
- asset_simulation/tests/test_global_macro.py
- asset_simulation/tests/test_g17_lite.py
- asset_simulation/tests/test_north_america.py
- asset_simulation/tests/test_china_mainland.py
- asset_simulation/tests/test_west_north_europe.py
- asset_simulation/tests/test_service_viewer.py
- asset_simulation/tests/test_volatility_audit.py
- asset_simulation/audit_volatility.py

### 2.5 地图

- map_research/index.html
- map_research/airports.js
- map_research/transport_cities.js
- map_research/City_Airport_Market_List_v0.1.md

执行前先输出不超过 20 行的“实现确认摘要”，说明你理解了 owner、年度顺序、五端口、RoW 和 Viewer 投影。

---

## 3. 旧模型只作参考

可以阅读以下旧文档：

- capital_market_lab/docs/JAPAN_KOREA_GDP_MODEL.md
- capital_market_lab/docs/JAPAN_KOREA_INFLATION_MODEL.md
- capital_market_lab/docs/JAPAN_KOREA_GDP_DEFLATOR_NOMINAL_ACCOUNTS_MODEL.md
- capital_market_lab/docs/JAPAN_KOREA_MONETARY_POLICY_MODEL.md
- capital_market_lab/docs/JAPAN_KOREA_PUBLIC_FINANCE_CENTRAL_BANK_MODEL.md
- capital_market_lab/docs/JAPAN_KOREA_PUBLIC_YIELD_CURVE_MODEL.md
- capital_market_lab/docs/JAPAN_KOREA_CURRENCY_FUNDING_MODEL.md
- capital_market_lab/docs/JAPAN_KOREA_CREDIT_TRANSMISSION_MODEL.md
- capital_market_lab/docs/JAPAN_KOREA_OIL_ENERGY_MODEL.md
- capital_market_lab/docs/JAPAN_KOREA_ASSET_WEALTH_MODEL.md
- capital_market_lab/docs/JAPAN_KOREA_FINAL_REVIEW.md

只能提取经济直觉、命名经验和宽校准范围。

禁止：

- 从 capital_market_lab 导入运行时代码；
- 复制旧十轮联合求解器；
- 恢复数百字段区域状态；
- 把特殊事件、YCC、危机 QE 或历史岔路带入普通基座；
- 绕过 regional_macro_extension_v3；
- 把旧校准当成未来预测。

允许继承的关键直觉：

- 70% 日本、30% 韩国的复合结构；
- 2% 中期通胀锚与 25bp 政策网格；
- 高债务不等于高违约风险；
- 国内资金与央行持债压低期限溢价；
- 韩国式美元融资敏感性不能消失；
- 全球 Brent 是唯一能源锚；
- 股票回报由名义盈利、估值和分红复利闭合。

---

## 4. 不可破坏的架构原则

### 4.1 普通世界

不实现疫情、金融危机、战争、债务危机、YCC 防线、危机 QE、史诗级事件、国运层或称号级世界。

Seed 只决定普通周期的相位、持续时间、振幅和区域相对表现，不捆绑固定事件。

### 4.2 单向年度顺序

禁止同年循环求解。建议：

    global[t] + lagged_g17[t-1] + japan_korea[t-1]
      -> real_prices[t]
      -> policy_credit[t]
      -> public_market_energy[t]
      -> japan_korea[t]
      -> g17_clear[t]

后段可以读取本年已闭合的前段结果；不能回写并重算。policy_credit 使用上一年已经闭合的公共账户和曲线状态，public_market_energy 再用本年政策与信用结果闭合本年财政、曲线、能源、资产和 post-market FCI。

### 4.3 名义／实际

- 实际 GDP：2025 固定价；
- 名义 GDP：实际 GDP × GDP 平减指数；
- CPI：居民消费价格；
- GDP 平减指数：整体国内增加值价格，不得逐年等同 CPI；
- 股票、债券、商品和能源在界面优先显示名义价格或名义指数；
- 实际财富只作派生量；
- 债务是名义存量，债务率以名义 GDP 为分母。

### 4.4 唯一所有权

- 全球实际 GDP、全球 CPI、全球政策、全球无风险曲线、全球 Brent 和全球风险周期由全球内核拥有；
- 日韩消费全球 Brent，只生成进口基差和区域传导；
- 国际层拥有相对汇率、贸易、资本和储备需求清算；
- 区域不得生成与国际层无关的长期汇率随机游走。

### 4.5 不用截断掩盖问题

禁止：

- 把 RoW 名义 GDP 截成正数；
- 把贸易、资本和储备需求截在极窄区间；
- 用过强均值回归压平曲线；
- 把利率或回报钉死在目标值；
- 隐藏 NaN、Inf、常数序列和长期边界命中。

---

## 5. 文件与版本

建议新增：

- asset_simulation/model/japan_korea.py
- asset_simulation/model/japan_korea_components/__init__.py
- asset_simulation/model/japan_korea_components/real_prices.py
- asset_simulation/model/japan_korea_components/policy_credit.py
- asset_simulation/model/japan_korea_components/public_market_energy.py
- asset_simulation/config/japan_korea_v0.1.json
- asset_simulation/contracts/japan_korea_support_v1.json
- asset_simulation/tests/test_japan_korea.py
- asset_simulation/docs/regions/JAPAN_KOREA.md

版本建议：

- 日韩 v0.1；
- G17 v0.3 -> v0.4；
- World v0.4 -> v0.5；
- Viewer / API v4.3 -> v4.4；
- regional_macro_extension_v3 语义不变时不升版。

旧配置保留归档，但不能继续注册为默认版本。

随机流统一使用 japan_korea.*：

    japan_korea.real.cycle
    japan_korea.inflation.local
    japan_korea.policy.discretion
    japan_korea.credit.funding
    japan_korea.energy.basis
    japan_korea.asset.valuation

同 Seed、同版本必须确定；延长 years 不能改写已有年份。

---

## 6. 2025 工程锚

这些是平行世界工程起点，不是实时预测，也不是逐项硬边界。

| 项目 | 建议初始值或区间 |
|---|---:|
| 实际 GDP，2025 固定美元 | 6.0–6.6 万亿美元 |
| 代表汇率 | 145–155 JPY / USD |
| 名义 GDP | 880–990 万亿 JPY |
| 实际增长 | 1.0%–1.5% |
| 潜在增长 | 1.2%–1.5% |
| CPI 同比 | 1.7%–2.3% |
| GDP 平减指数同比 | 1.5%–2.2% |
| 政策利率 | 0.25%–0.75% |
| 2Y 收益率 | 0.6%–1.2% |
| 10Y 收益率 | 1.2%–2.2% |
| 政府债务 / GDP | 175%–195% |
| 央行总资产 / GDP | 75%–95% |
| 贸易余额 / GDP | +1.0%–+3.0% |
| 股票 PE | 13–17 倍 |

初值必须使实际／名义账户、公共账户、国际账户和资产复利自然闭合。

---

## 7. 三段式内核

### 7.1 real_prices.py

职责：

- 潜在增长、普通周期、实际增长、实际 GDP、产出缺口；
- CPI、核心通胀、预期、工资压力；
- GDP 平减指数和名义 GDP；
- 消费国际需求、贸易、汇率和全球能源输入。

最小逻辑：

    potential_growth
      = demographic_productivity_trend
      + small_persistent_seed_component

    cycle_gap
      = common_global_cycle
      + export_manufacturing_cycle
      + lagged_credit_effect
      + local_cycle_innovation

    real_growth
      = potential_growth
      + cycle_gap
      + trade_impulse
      + lagged_fci_drag

    core_inflation
      = expectation_anchor
      + wage_pressure
      + output_gap_pass_through
      + fx_import_pass_through

    headline_inflation
      = core_inflation
      + imported_energy_pass_through
      + small_food_goods_noise

要求：

- 全球周期是主周期，区域创新是次级；
- 半导体只作出口与盈利敏感度，不建产业模拟器；
- 同一 Seed 的日韩增长与全球显著相关，但不完全复制；
- 日元升值压低进口通胀和能源成本，也压低部分出口名义盈利；
- CPI 与平减指数接近但不能逐年相同。

### 7.2 policy_credit.py

职责：

- 25bp 网格政策；
- 中性实际利率和政策缺口；
- JPY 资金条件和美元融资压力；
- 银行放贷、家庭住房信用和企业信用；
- HY 利差、信用可得性和违约风险；
- 读取上一年已闭合的债务、央行数量和收益率曲线；
- 不在本阶段重算本年公共账户或曲线。

政策：

    raw_policy
      = neutral_real_rate
      + expected_inflation
      + inflation_gap_response
      + output_gap_response
      + lagged_financial_stress_response
      + inertia

    policy_rate = round_to_25bp(raw_policy)

约束：

- 政策边界可为 -0.25% 到 5.00%；
- 普通年度调整原则上不超过 75bp；
- 跨 Seed 可出现轻微负利率，但不能强迫每个 Seed 出现；
- 不能因高债务强迫央行永远零利率。

资金与信用必须同时体现：

- 日本式国内储蓄、回流和避险支撑；
- 韩国式美元融资、出口和全球风险敏感性。

风险规避时允许 JPY 有效汇率相对坚挺，同时美元融资压力上升。这两者必须由不同状态表达，不能把“货币强”误写成“资金毫无压力”。

### 7.3 public_market_energy.py

职责：

- 基本余额、利息负担、总赤字；
- 债务递推、净发行、期限结构；
- 央行资产、准备金、政府债持有；
- 普通期 QE/QT 数量；
- 2Y、10Y、期限溢价和主权风险；
- 进口石油、天然气、电力与零售能源；
- 盈利、PE、ERP；
- 股票、主权债、IG、HY、60/40 名义总回报；
- 本年 post-market FCI 和下一年接口。

债务必须递推：

    debt_t
      = debt_t-1
      + primary_deficit
      + interest_cost
      + stock_flow_adjustment

    debt_ratio_t = debt_t / nominal_gdp_t

高债务通过利息、净发行、久期供给、央行持债、期限溢价和财政空间产生影响，但不能机械映射为违约风险。主权风险还要读取国内资金、本币融资、平均期限、安全资产需求及名义增长和利息成本差。

普通基座：

- 不创建危机 QE 状态机；
- 不创建 YCC 状态机；
- qe_qt_flow_pct_gdp = 0；
- 央行资产与准备金仍有慢变化，不能是一把尺子；
- 准备金管理购买必须与危机 QE 分开。

曲线：

    yield_2y
      = expected_policy_path
      + short_term_premium

    yield_10y
      = expected_policy_path
      + inflation_uncertainty
      + duration_supply_pressure
      - domestic_funding_support
      - central_bank_duration_absorption
      - safe_asset_demand

能源：

    import_oil_real
      = global_brent_real
      * regional_import_basis
      * fx_translation

    import_oil_nominal
      = import_oil_real
      * regional_price_level

- 不重复叠加 CPI；
- 全球 Brent 必须传入；
- 日元升值降低本币能源压力；
- 能源影响 headline CPI、贸易条件和盈利；
- 天然气、电力只作轻量指数。

资产：

    nominal_earnings_growth
      = real_growth
      + gdp_deflator_growth
      + operating_leverage
      + export_fx_effect
      - energy_margin_drag
      - funding_drag

    log_pe_change
      = earnings_outlook
      - real_yield_pressure
      - erp_pressure
      - funding_pressure

    equity_total_return
      = price_return
      + dividend_yield

- 股票不能脱离盈利和估值出现无解释的单年极端崩跌；
- 股票长期名义复利原则上不应系统性低于名义 GDP；
- 债券总回报由票息与久期价格效应构成；
- 高政府债务不能直接让主权债总回报失真；
- 界面资产主口径使用名义指数。

---

## 8. 日韩支持合同

新增 japan_korea_support_v1.json，控制在 6–8 个字段：

| 字段 | 含义 | 直接消费者 |
|---|---|---|
| wage_pressure_pct | 工资与服务通胀压力 | 核心 CPI、政策 |
| household_housing_credit_pressure_index | 家庭住房信用压力 | 银行放贷、FCI |
| bank_lending_conditions_index | 银行放贷条件 | GDP、信用利差 |
| central_bank_govt_bond_holdings_pct_gdp | 央行政府债持有 | 期限溢价、准备金 |
| safe_asset_demand_index | 本币安全资产需求 | 10Y、主权风险、汇率 |
| semiconductor_export_cycle_index | 半导体和制造业出口周期 | GDP、盈利、贸易 |
| imported_energy_price_index_2025_100 | 本币进口能源价格 | CPI、贸易条件、盈利 |

每个字段必须：

- 在合同中声明；
- 在状态中稳定输出；
- 至少一个直接消费者；
- 有范围或方向测试；
- 在区域文档写明 owner 与消费者。

没有真实消费者就删除，不保留摆设。

---

## 9. 国际层接入

### 9.1 正式参与者

G17 v0.4：

    north_america
    china_mainland
    west_north_europe
    japan_korea
    rest_of_world

建议 G17 trade_weight：

| 区域 | 权重 |
|---|---:|
| 北美 | 0.27 |
| 中国大陆 | 0.18 |
| 西欧／北欧 | 0.21 |
| 日韩 | 0.11 |
| RoW | 0.23 |

合计严格为 1。若根据现有世界初值调整，必须记录理由。

### 9.2 日韩国际参数

| 参数 | 建议值或区间 |
|---|---:|
| base_trade_balance_pct_gdp | +1.0% 至 +3.0% |
| capital_mobility | 0.80–0.92 |
| fx_adjustment_speed | 0.68–0.82 |
| reserve_currency_weight | 0.20–0.32 |
| energy_import_exposure | 0.70–0.85 |
| risk_off_capital_beta | 0.00–0.20 |
| risk_off_fx_beta | 0.15–0.35 |

JPY 的储备与避险属性高于普通非储备货币、低于美元。复合区域不能在所有风险期机械升值或机械贬值。

### 9.3 五端口必须真实消费

1. external_demand_impulse_pp  
   消费者：出口制造业周期、实际 GDP、盈利。

2. trade_balance_impulse_pct_gdp  
   消费者：净出口、贸易条件、名义账户。

3. net_capital_flow_impulse_pct_gdp  
   消费者：美元资金压力、银行放贷、信用利差。

4. bilateral_fx_change_pct  
   消费者：进口通胀、能源、出口盈利、名义换算。

5. reserve_currency_demand_impulse_index  
   消费者：安全资产需求、资金稳定性、期限溢价。

禁止只输出 JSON 而不进入公式。

### 9.4 汇率

区域逐年状态和 G17 账户至少保留当前公共命名：

- regional_currency_index，2025=100；
- bilateral_fx_change_pct；
- reserve_currency_demand_index；
- private_net_capital_inflow_pct_gdp；
- funding_stress_index。

World / API 可另外投影 jpy_per_usd 和 dollar_funding_pressure 供界面使用，但不要改写公共合同字段的语义。

建议换算：

    jpy_per_usd
      = base_jpy_per_usd
      * north_america_currency_value
      / japan_korea_currency_value

方向：

- jpy_per_usd 上升 = 日元对美元贬值；
- currency_value 上升 = 代表货币有效升值。

### 9.5 RoW 对账

增加日韩后重新闭合：

- RoW 实际 GDP；
- RoW 名义 GDP；
- 全球贸易余额；
- 全球私人资本流；
- 储备需求残差。

恒等式：

    sum(region_real_gdp_fixed_usd) == global_real_gdp_fixed_usd
    sum(region_nominal_gdp_current_usd) == global_nominal_gdp_current_usd
    sum(region_trade_balance_usd) == 0
    sum(region_private_capital_flow_usd) == 0

不得 clamp RoW。

第四个正式区域加入后必须重新审查现有 fx_mean_reversion=0.12。不要为了 RoW 为正单独调高它；先检查初始 GDP、名义换算、FX 权重和平减指数。

---

## 10. World、API 与缓存

### 10.1 World

- CoupledWorldRun 注册 japan_korea；
- 年度顺序保持 global -> regions -> G17 clear；
- 日韩读取滞后一期 G17 外部端口；
- G17 当年清算读取当年已闭合区域；
- 实际 GDP 用 2025 固定汇率换算；
- 名义 GDP 用当年 JPY/USD 换算。

### 10.2 API

新增或等价支持：

- /api/japan-korea；
- /api/world 中包含 japan_korea；
- /api/config 或当前元数据返回日韩版本与币种。

### 10.3 缓存

所有 scope 必须复用同一 CoupledWorldRun。禁止为日韩 scope 单独重跑 world。

沿用当前缓存键 seed、years、diagnostics_level、WORLD_MODEL_VERSION；将 World 正式升到 v0.5 即完成缓存失效。不要另建日韩私有缓存。相同 seed、years 的不同 scope 中，全球及其他区域数据必须逐值一致。

---

## 11. Viewer v4.4

### 11.1 日韩 scope

新增 scope=japan_korea，显示“日韩”，金额使用 ¥。必要时只在模块标题或单位处注明“代表性 JPY”，不要每张卡重复解释。

顶部八卡：

1. 实际 GDP；
2. 名义 GDP；
3. CPI / 平减指数；
4. 政策利率 / 10Y；
5. JPY 有效价值 / 美元资金压力；
6. 信用 / 综合金融条件；
7. 进口能源价格；
8. 股票名义总回报指数。

卡片不显示版本标签、模型说明或长段解释。

至少支持：

- 概览；
- 实际 GDP；
- 名义 GDP；
- 增长；
- 通胀；
- 政策与收益率；
- JPY 与资金；
- 信用；
- 公共账户；
- 能源；
- 资产。

折线图必须继承现有鼠标移动选年功能，不能出现 hover 年份不同步。

### 11.2 国际 scope

界面只称“国际”，不暴露 G17 Lite 名称。

顶层继续控制在 6 张卡，建议改成可扩展结构：

1. 国际风险周期；
2. USD/CNY；
3. EUR/USD；
4. JPY/USD；
5. 最大贸易顺差／逆差；
6. 最大资本流入／流出。

详细区域账户放在图表或明细，不为每个新增区域永久增加顶卡。

### 11.3 UI 检查

- 无 NaN、Infinity、undefined；
- 2025 与接管年份标线正确；
- 实际／名义 GDP 不混用；
- JPY/USD 方向和标签一致；
- 万亿 JPY 不显示成万亿美元；
- 图例、轴标题、卡片口径一致；
- 全球、北美、中国大陆、西欧／北欧界面无退化。

---

## 12. 测试

新增 test_japan_korea.py，并扩展 G17、服务、界面和审计测试。

### 12.1 区域

- 确定性与前缀不变；
- 年份连续、字段有限；
- regional_macro_extension_v3 全部 common_fields 有效；
- CPI、平减指数、实际／名义 GDP 递推一致；
- 政策严格为 25bp 网格；
- 债务递推一致，期限份额合计 100%；
- 2Y / 10Y 方向正确；
- qe_qt_flow_pct_gdp 在普通基座恒为零，且不存在 YCC 状态机；
- 央行资产和准备金不是长期常数；
- 支持合同字段都有消费者。

### 12.2 方向 counterfactual

- 全球需求更强 -> 日韩增长和出口盈利上升；
- 日元升值 -> 进口 CPI 与能源压力下降；
- 日元升值 -> 出口名义盈利边际承压；
- 美元压力更强 -> 日韩外币融资压力上升；
- 央行持债更高 -> 10Y 期限溢价下降；
- 安全资产需求更强 -> 主权风险与 10Y 压力下降；
- 久期净发行更高 -> 10Y 期限溢价上升；
- 能源价格更高 -> headline CPI 上升、贸易条件与盈利下降；
- 信用更紧 -> 下一年 GDP 和资产估值承压；
- 名义增长更高 -> 其他条件相同时债务率压力下降。

高债务要增加供给和利息压力；但若国内资金、央行持债和安全资产需求同时更强，主权风险不能机械同幅上升。

### 12.3 国际

- 五端口全部被消费；
- 实际 GDP 加总闭合；
- 名义 GDP 按当年汇率闭合；
- 贸易余额美元合计为零；
- 私人资本流美元合计为零；
- RoW 实际和名义 GDP 有限且为正；
- 风险期美元整体反应强于日韩代表货币；
- 日韩可获得一定汇率支撑，但美元融资压力仍会上升；
- 新区域不改变既有全球随机前缀；
- 国际冲击不被 clamp 吞掉。

### 12.4 API / UI 烟测

- 日韩端点返回 200；
- world 包含 japan_korea；
- scope=japan_korea 可渲染；
- 国际 scope 显示 JPY/USD；
- scope 切换复用缓存；
- 卡片和图表无 NaN；
- hover 选年同步。

---

## 13. 700 Seed 审计

定向测试通过后只运行一次：

    py -3.13 -m asset_simulation.audit_volatility --profile goal-c --years 60 --output $env:TEMP\asset_sim_japan_korea_v01_goal_c.json

将日韩加入 levels、changes、events、bounds、international、public_accounts。

建议带宽不是逐年硬目标：

| 指标 | 目标带宽 |
|---|---:|
| 60 年平均实际增长 | 0.9%–1.6% |
| 年度增长标准差 | 0.75%–1.15% |
| 负增长年份占比 | 2%–8% |
| headline CPI 均值 | 1.5%–2.3% |
| CPI 年变动标准差 | 0.28–0.80pct |
| 政策利率均值 | 0.5%–2.5% |
| 政策暂停占比 | 25%–70% |
| 10Y 收益率中枢 | 1.3%–3.6% |
| 10Y 年变动标准差 | 0.18–0.65pct |
| JPY 有效价值年变动标准差 | 1.0%–2.8% |
| 贸易余额均值 | +0.5%–+3.5% GDP |
| 与全球增长相关系数 | 0.65–0.90 |
| 终点债务率跨 Seed 均值 | 160%–220% GDP |
| 终点债务率 p95 | 不高于约 245% GDP |

还需检查：

- 政策下界不是多年常态；
- 轻微负利率可出现但不普遍；
- 央行资产和准备金不是一把尺子；
- qe_qt_flow_pct_gdp 为零，且不存在 YCC 状态机；
- 股票没有无宏观解释的单年极端崩跌；
- 名义 GDP、股票、债券和 CPI 的长期量级可解释；
- RoW 不因加入日韩接近零；
- 贸易和资本有周期感，不是白噪声。

若必须再次运行全审计，交付说明必须解释原因。

---

## 14. 文档与收尾

完成后更新：

- asset_simulation/docs/INDEX.md
- asset_simulation/docs/current/RUNTIME_ARCHITECTURE.md
- asset_simulation/docs/current/CONTRACTS_AND_UNITS.md
- asset_simulation/docs/current/G17_LITE.md
- asset_simulation/docs/current/MODEL_QUALITY_AUDIT.md
- asset_simulation/docs/current/VIEWER_PROJECTION.md
- asset_simulation/docs/regions/JAPAN_KOREA.md

JAPAN_KOREA.md 必须写明：

- 区域边界与代表 JPY；
- 70/30 只是内部经济权重；
- 三段式年度顺序；
- 支持合同 owner 与消费者；
- 国际五端口；
- 名义／实际口径；
- 高债务、央行持债、期限溢价关系；
- 已实现与明确未实现；
- 下一步跨区域联合校准点。

完成后：

1. 将本任务书移入 docs/archive，文件名加入完成日期；
2. docs/tasks 不保留已完成任务；
3. INDEX 当前活动路线恢复“无未完成 Goal”；
4. 不遗留临时 JSON、日志、截图或备份源码；
5. 不修改 capital_market_lab 运行时。

---

## 15. 完成定义

同时满足才算完成：

- 日韩内核默认接入；
- regional_macro_extension_v3 公共字段有效；
- support_v1 字段都有消费者；
- 国际五端口全部有可测影响；
- JPY/USD 与有效汇率方向一致；
- 全球实际、名义、贸易、资本账户闭合；
- RoW 无截断且保持正值；
- API、缓存和 Viewer 接入完成；
- 国际页仍紧凑；
- 定向测试通过；
- 700 Seed 无硬失败，或少数偏差有证据解释；
- 正式文档与版本更新；
- 未把旧大型模型带回当前项目。

---

## 16. Grok 的自主范围

可以自主调整：

- 建议区间内的参数；
- 三个组件内部函数划分；
- 支持字段轻微规范化；
- 图表颜色和布局细节；
- 为数值稳定使用的弱均值回归；
- 测试组织方式。

必须停止并交给 Codex 决策：

- 改变 regional_macro_extension_v3 字段语义；
- 引入同年循环求解；
- 引入特殊事件、YCC 或危机 QE；
- 修改地图边界；
- 用截断修复 RoW；
- 删除既有区域字段；
- 改变全球 Brent、全球 CPI 或全球无风险曲线 owner。

有不同意见可以在实施记录写“建议变更”，不能静默偏离红线。

---

## 17. 推荐执行顺序

1. 完整阅读第 2 节并输出实现确认摘要。
2. 建配置、状态适配和随机流。
3. 完成 real_prices。
4. 完成 policy_credit。
5. 完成 public_market_energy。
6. 接入 G17 和 RoW。
7. 接入 World、API、缓存。
8. 接入 Viewer，国际页保持 6 卡。
9. 一次性运行定向测试。
10. 运行一次 700 Seed 审计。
11. 做必要校准并复跑受影响的定向测试。
12. 更新正式文档、归档任务书、清理临时文件。

最终交付报告只需列出：

- 新增和修改文件；
- 主因果链；
- 五端口消费者；
- 测试结果；
- 700 Seed 核心统计；
- 偏离任务书之处；
- 留给跨区域校准、特殊事件、国运层和称号级世界的内容。
