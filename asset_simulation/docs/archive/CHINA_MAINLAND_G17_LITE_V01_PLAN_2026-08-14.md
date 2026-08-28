# 中国大陆区域与 G17-lite v0.1 精细实施路线

> 状态：历史归档，不是当前实现权威；Goal 已于 2026-08-14 完成  
> Goal：一次完成中国大陆区域 v0.1，并首次跑通北美—中国大陆—剩余世界之间的 G17-lite 跨区域闭环  
> 当前基线：全球 v0.5、北美 v0.3、区域契约 v2、宏观 UI v3  
> 目标版本：北美 v0.4、中国大陆 v0.1、G17-lite v0.1、World v0.1、宏观 UI v4  
> 最近核对：2026-08-14  
> 完成后的去向：将稳定事实分别写入 `current/`、`regions/` 和 ADR，本文件移入 `archive/`

## 1. Goal 的真正目标

这次工作不是“再复制一个北美页面”，也不是立刻构造完整国际经济系统。目标是建立第一个能长期扩展的多区域运行闭环：

1. 中国大陆拥有独立、可解释、人民币计价的区域宏观内核。
2. 北美和中国大陆都读取同一个已经清算的全球年份。
3. G17-lite 在两个区域运行结束后，清算贸易、资本、汇率和储备需求，并把结果作为下一年的区域输入。
4. 未建模经济体统一进入“剩余世界”会计残差，全球总量不会因为只实现两个区域而失真。
5. 全球、北美、中国大陆和跨区域页面来自同一个世界运行实例，不允许各接口各算一遍形成多个互不一致的世界。

因此，本 Goal 完成后应该看到的不是剧烈货币战争，而是一个平稳世界中的基本国际联动：区域增速差异会改变外需，利差和风险会影响资本流，贸易与资本压力会逐年传到汇率，美元与人民币开始拥有对手盘，北美也第一次收到非零的跨区域输入。

## 2. 明确不做什么

以下内容不进入 v0.1：

- 不加入疫情、战争、金融危机等特殊事件。
- 不加入“东方日升”“欧洲领袖”等称号级世界规则。
- 不把普通的区域发展快慢硬编码成国运脚本；它只能由相同结构下的增长、通胀、政策和资本条件逐步形成。
- 不建立逐国贸易矩阵、逐行业投入产出表或高频外汇市场。
- 不让 G17-lite 反向改写同年的全球总量，也不做同年隐藏迭代。
- 不把南亚、欧洲、日韩、东南亚等尚未接入的新区域伪装成独立模型；它们暂时都属于剩余世界。
- 不在游戏界面展示跨 Seed 排名、百分位或“这个 Seed 更强”的解释。

这些边界很重要：v0.1 需要的是可靠的骨架和守恒关系，而不是用大量独立随机数模拟国际新闻。

## 3. 当前代码事实与需要改变的边界

### 3.1 已经具备

- 全球 v0.5 能产生普通周期、通胀、政策、曲线、信用、能源和资产等统一年度状态。
- 北美 v0.3 已实现区域契约 v2 的十类公共因子，并具备五个 G17 输入端口。
- 北美年度时序已经是“区域年份 t 读取全球 t 与 G17 的 t-1 输入”。
- Viewer 已有全球／北美 Scope、年份悬停联动和实际／名义口径。

### 3.2 目前仍是假接口

北美当前发布的五个跨区域端口全部为零：

| 端口 | 当前状态 | G17-lite 后的含义 |
|---|---|---|
| `external_demand_impulse_pp` | 零 | 贸易伙伴景气变化对下一年实际增长的增量影响 |
| `bilateral_fx_change_pct` | 零 | 本币相对共同外部篮子的年度升值率，正值为升值 |
| `trade_balance_impulse_pct_gdp` | 零 | 贸易变化对下一年需求的增量，不是贸易余额水平 |
| `net_capital_flow_impulse_pct_gdp` | 零 | 私人净资本流入对下一年金融条件的影响 |
| `reserve_currency_demand_impulse_index` | 零 | 境外储备需求对该区域货币与融资条件的支持 |

北美目前只实质使用了外需端口；实施 G17-lite 时必须把另外四个端口接入明确的 owner，不能只把非零数字显示在 API 中。

### 3.3 当前缓存问题

服务端分别缓存全球运行和北美运行，Viewer 再并行请求两个接口。单区域时可以工作；进入 G17 后，这会允许同一 Seed 出现不同来源的区域快照。目标架构必须改成一个 `WorldRun` 和一个世界缓存，所有 Scope 都只是同一运行结果的投影。

## 4. 冻结的年度时序

每个年份只按以下顺序运行一次：

```text
全球状态 t 已清算
        │
        ├── 北美 t：读取全球 t + G17→北美 t-1
        │
        └── 中国大陆 t：读取全球 t + G17→中国 t-1
                    │
                    ▼
      G17-lite t：读取北美 t、中国 t、剩余世界 t
                    │
                    ▼
       发布 G17→各区域 t+1 的五类输入
```

硬规则：

- 区域 t 不得读取 G17 t。
- G17 t 不得要求区域重跑 t。
- 全球 t 不读取本 Goal 新增的区域反馈。
- 初始年份的 G17 输入是显式零输入，而不是缺失值或临时默认值。
- 所有随机项继续使用地址化随机流；新增区域不能改变现有全球随机序列。

这样做会带来一年传导滞后，但时序清楚、可复现，也给未来特殊事件留下唯一入口。

## 5. 中国大陆区域内核

### 5.1 公共输出不另起体系

中国大陆继续实现 `regional_macro_extension_v2` 的十类公共因子，使资产、银行、保险和证券等下游能够使用同一组最小宏观接口：

1. 实际增长与产出缺口。
2. 总体、核心与预期通胀。
3. 政策与实际政策条件。
4. 短端和长端贴现率。
5. 财政、债务与期限供给。
6. 央行数量工具与流动性。
7. 货币与融资。
8. 信贷可得性与违约压力。
9. 能源／贸易条件敞口。
10. 市场后的金融条件与风险偏好。

公共字段相同，不表示制度相同。中国大陆的支持字段和计算 owner 必须体现银行信贷、数量工具、地方公共账户、管理浮动汇率和能源进口依赖，不照抄北美的国债—市场融资结构。

### 5.2 建议代码拆分

```text
model/china_mainland.py
    对外 dataclass、初始化、年度 step、完整运行包装器

model/china_components/real_prices.py
    潜在增长、实际增长、产出缺口、CPI、核心 CPI、工资、GDP 平减指数、实际／名义 GDP

model/china_components/policy_credit.py
    代表性政策利率、实际政策条件、准备金率、政策流动性、社会融资／信用脉冲、信贷压力映射

model/china_components/public_market_energy.py
    中央／地方／广义公共账户、收益率曲线、人民币融资、能源进口与贸易条件、盈利和资产指数
```

`china_mainland.py` 只负责编排和契约输出，不继续成长为另一个巨型公式文件。组件可以使用共享数学工具，但不得直接读取 Viewer 或服务层状态。

### 5.3 实际经济与价格

中国大陆实际侧仍遵循全球普通周期，但保留区域差异：

- 潜在增速从较高水平缓慢下行；初始中枢先放在约 4.0%–4.5%，全部参数进入配置文件。
- 区域周期由全球周期、区域持久项、上一年金融条件和上一年 G17 外需共同形成。
- 普通世界的实际增速主要落在约 2%–5%，允许少量温和负增长，但不靠频繁危机制造辨识度。
- CPI 由核心黏性、需求缺口、工资、能源进口和汇率传导组成。
- GDP 平减指数独立于 CPI，但共享通胀锚；名义 GDP 严格由实际 GDP 与平减指数复利得到。
- 工资增速和政策拖累必须实际进入通胀或增长公式，不能只作为展示字段。

年度恒等式：

```text
real_gdp_t = real_gdp_(t-1) × (1 + real_growth_t)
gdp_deflator_t = gdp_deflator_(t-1) × (1 + deflator_inflation_t)
nominal_gdp_t = real_gdp_t × gdp_deflator_t / 100
```

本地存储和 Viewer 主口径：

- `real_gdp_trillion_2025_cny`
- `nominal_gdp_trillion_cny`
- `gdp_deflator_index`，2025=100
- `cpi_index`，2025=100

G17 对账时再投影为共同美元口径，不能把美元换算值反向冒充中国大陆本地 GDP。

### 5.4 货币与信用

中国大陆的 `policy_rate_pct` 是代表性价格工具，不采用北美的 25bp 硬网格。数量工具进入支持字段：

- `rrr_pct`
- `policy_liquidity_index`
- `credit_impulse_index`
- `social_financing_growth_pct`
- `property_credit_stress_index`

区域公共契约中的 `credit_availability_index` 表示全社会信用可得性；`hy_spread_bps` 在中国大陆是“市场化高收益信用压力的可比映射”，不是整个信用体系的唯一 owner。实施时必须在支持契约和文档中明确这一点，避免下游误把它等同于北美 HY 市场。

主要方向关系：

- 更紧的实际政策、资本外流和房地产信用压力降低信贷可得性。
- 政策流动性、温和信用脉冲和资本流入改善信贷条件。
- 信用过度扩张可以短期支持增长，但通过滞后债务服务与信用压力形成回吐，不提供永久免费增长。

### 5.5 公共金融与曲线

支持字段至少区分：

- `central_government_debt_pct_gdp`
- `local_government_debt_pct_gdp`
- `broad_public_debt_pct_gdp`
- `government_net_issuance_pct_gdp`
- `government_weighted_average_maturity_years`

区域公共契约中的 `gross_public_debt_pct_gdp` 固定映射到文档定义的“广义公共债务”口径，不能在不同年份切换中央债务和广义债务。

曲线继续遵守“政策预期＋期限溢价”结构：

```text
short_rate = expected_policy_path + short_market_premium
long_rate  = expected_policy_path + inflation_compensation + term_premium + sovereign/funding_adjustment
```

收益率不需要复制北美水平；但同样必须对增长、通胀、政策、供给和风险具有方向可检验的反应。

### 5.6 央行数量工具

- `central_bank_assets_pct_gdp`、`reserve_or_bank_liquidity_pct_gdp` 继续是公共字段。
- 准备金率和政策流动性作为中国大陆支持字段。
- 普通世界中 `qe_qt_flow_pct_gdp` 可以多数年份接近零；只有明确定义的数量型资产购买才进入该字段，普通流动性投放不全部叫 QE。
- 不能为了让图表“有线条”而制造无经济意义的 QE/QT 流量。

### 5.7 能源、盈利与资产

- 中国大陆读取全球名义 Brent，同时用全球 CPI／自身 CPI 构造实际能源压力。
- 作为能源净进口区域，实际油价上涨通常恶化贸易条件并抬升总体 CPI；传导系数保持温和、滞后。
- 区域资产继续遵守现有名义主显示、实际可审计的约定。
- 权益不是短期价格预测，而是由名义盈利、估值中枢、贴现率和风险共同形成的复利／再定价指数。
- 主权债券总回报使用票息、久期和收益率变化；通胀稀释通过实际审计口径体现，不重复扣除两次。

## 6. 人民币与 G17-lite 货币约定

### 6.1 固定符号

- `regional_currency_index`：2025=100；上升表示本币相对共同外部货币篮子升值。
- `bilateral_fx_change_pct`：正值表示目标年份本币升值。
- `external_demand_impulse_pp`：正值提高下一年实际增长／周期。
- `trade_balance_impulse_pct_gdp`：正值改善下一年净出口需求。
- `net_capital_flow_impulse_pct_gdp`：正值表示私人净资本流入，通常缓解本地融资压力。
- `reserve_currency_demand_impulse_index`：正值表示外部储备需求增强。

人民币采用管理浮动，而不是固定汇率：

- `fx_adjustment_speed` 低于自由浮动货币。
- `capital_mobility` 低于北美。
- 贸易、资本、政策和风险压力可以部分被储备／管理项吸收，但不能永远把汇率钉死。

### 6.2 不硬编码 USD/CNY 单边符号

每个区域都维护相对共同篮子的货币指数。美元兑人民币的相对变化由两者之差派生：

```text
relative_usd_cny_change ≈ north_america_currency_change - china_currency_change
```

共同篮子满足加权去均值约束：

```text
Σ trade_weight_i × log_currency_change_i = 0
```

参与者为北美、中国大陆和剩余世界。这样以后加入欧洲、日韩等区域时，只需缩小剩余世界权重，不必推翻 USD/CNY 的旧公式。

### 6.3 本地与共同币种

中国大陆初始 GDP 锚、初始 CNY/USD 和贸易权重全部进入配置。初步锚可以采用约 140 万亿元人民币、约 7.2 CNY/USD，仅作为平行世界的初始参数，不写死在公式中。

G17 需要同时保存：

- 本地实际／名义 GDP。
- 共同 2025 美元实际 GDP。
- 当年共同美元名义 GDP。
- 使用的货币指数和换算率。

任何跨区域加总只能使用共同币种口径。

## 7. G17-lite 的清算机制

### 7.1 内核 owner

新增 `model/g17_lite.py`，建议公开：

- `G17LiteRun`
- `G17ClearingRow`
- `RegionExternalInputs`
- `initial_g17_state(...)`
- `clear_g17_year(...)`
- `validate_g17_run(...)`

G17 自己保存账户水平；给区域的五类字段只是下一年脉冲。二者不允许混为一列。

### 7.2 每年内部顺序

1. **外需**：使用贸易伙伴相对全球的增长偏离计算，避免把区域已经读取的全球周期重复加一遍。
2. **贸易余额**：由滞后实际汇率竞争力、伙伴需求、区域进口倾向和能源净进口敞口形成。
3. **私人资本流**：由实际利差、相对增长、风险／FCI、资本开放度和均值回复形成。
4. **货币压力**：由相对政策、相对增长、相对通胀、贸易、资本、储备需求和小幅地址化随机项形成。
5. **共同篮子清算**：对北美、中国大陆和剩余世界的货币变化做贸易权重去均值。
6. **储备与国际收支闭合**：计算官方储备变动或储备货币需求的对手项。
7. **端口发布**：把清算结果转换为各区域 t+1 的五类输入。

G17 的随机项只承担不可观测扰动，不承担主要逻辑。建议地址：

- `g17_fx.<region_id>`
- `g17_trade.<region_id>`
- `g17_capital.<region_id>`

### 7.3 国际收支恒等式

统一规定私人净流入为正：

```text
trade_balance
+ private_net_capital_inflow
- official_reserve_accumulation
= 0
```

区域先用共同美元金额闭合，再换算为该区域 GDP 百分比。北美作为储备货币发行者时，境外美元储备需求是对手项，不把它伪装成北美外汇储备积累。

每个 G17 年度行至少保存：

- `trade_balance_common_usd`
- `trade_balance_pct_gdp`
- `private_net_capital_inflow_common_usd`
- `private_net_capital_inflow_pct_gdp`
- `official_reserve_accumulation_common_usd`
- `official_reserve_accumulation_pct_gdp`
- `regional_currency_index`
- `bilateral_fx_change_pct`
- `reserve_currency_demand_index`
- `bop_reconciliation_residual_common_usd`

### 7.4 五类端口如何接回区域

| 端口 | 北美 owner | 中国大陆 owner | 防止重复计算 |
|---|---|---|---|
| 外需 | 下一年周期／实际增长 | 下一年周期／实际增长 | 只用伙伴相对全球偏离 |
| 汇率变化 | 美元指数、进口通胀、融资 | 人民币指数、进口通胀、贸易条件 | 全球美元代理量不再重复叠加 |
| 贸易脉冲 | 下一年净出口需求 | 下一年净出口需求 | 账户水平先转换成变化脉冲 |
| 资本流脉冲 | 流动性、融资、信用 | 政策流动性、信用可得性 | 不直接再加一次 GDP |
| 储备需求 | 美元融资／期限溢价支持 | 人民币外部需求与融资支持 | 与官方储备账户对手项一致 |

接入后北美版本升为 v0.4。必须保留一个 `zero_g17` 测试夹具，证明关闭 G17 时北美结果精确回到 v0.3 基线；默认世界运行则使用非零 G17 输入。

## 8. 剩余世界不是第三个完整区域

北美和中国大陆不能代表全球。新增内部 `rest_of_world` 会计残差，但不创建可玩页面和完整政策模型。

年度总量：

```text
RoW real GDP
= global real GDP
- North America real GDP in common 2025 USD
- China real GDP in common 2025 USD
```

名义 GDP 使用同年共同美元口径做相同残差。要求：

- 实际和名义剩余世界 GDP 始终为正。
- 剩余世界吸收全球贸易与资本账户的剩余项。
- 不强迫北美和中国大陆加权增长机械等于全球；计算隐含 RoW 增长并做合理性诊断。
- 未来每新增一个正式区域，就从 RoW 中扣除该区域，逐步缩小残差。

如果隐含 RoW 增长长期不合理，应调区域初值、规模权重或增长锚，而不是给对账残差加截断器遮盖问题。

## 9. 单一世界运行与缓存

新增 `model/world.py`：

```text
CoupledWorldRun
├── global_run
├── regions
│   ├── north_america
│   └── china_mainland
├── rest_of_world
├── g17_run
├── identity
└── summary
```

主要入口为：

```python
run_coupled_world(seed: int, years: int, include_diagnostics: bool = False)
```

服务端改为唯一 `_WORLD_CACHE`，键至少包含 `seed / years / diagnostics / world_model_version`。全球、北美、中国大陆和 G17 的 API 都从同一个 `CoupledWorldRun` 投影，禁止在 scoped endpoint 内重新调用模型。

世界 identity 必须绑定：

- 全球结果 identity/hash。
- 北美、中国大陆、G17 与 RoW 配置版本/hash。
- 区域公共契约、中国支持契约和 G17 契约 hash。
- 随机算法版本。
- 最终世界结果 hash。

## 10. 契约、配置与注册表

计划新增：

```text
config/china_mainland_v0.1.json
config/g17_lite_v0.1.json
contracts/china_mainland_support_v1.json
contracts/g17_lite_v1.json
```

计划修改：

```text
config/north_america_v0.4.json
model/registry.py
model/contracts.py（仅在需要新增通用校验时）
```

`regional_macro_extension_v2.json` 保持公共区域契约；仅因中国大陆有额外支持字段，不升级整个公共契约。只有十类公共字段或时序本身改变时才建立 v3。

注册表在导入时加载并校验所有 JSON，identity 中记录内容 hash。公式中的重要常数进入配置，不允许散落在 Viewer 和服务层。

## 11. API 与 Viewer

### 11.1 主 API

新增：

```text
GET /api/world?seed=42&years=60
```

返回结构：

```text
schemaVersion: asset-simulation-coupled-world-response-v1
identity
global
  identity / summary / snapshots / viewerSupportRows
regions
  north_america
  china_mainland
g17Lite
  identity / summary / rows / nextInputsByRegion / reconciliation
```

现有 `/api/global`、`/api/north-america` 可以保留为调试／兼容投影，但必须读取世界缓存。新增 `/api/china-mainland` 也遵守同一规则。

### 11.2 Viewer 架构

把当前二选一分支重构为声明式 Scope 配置：

```text
SCOPE_DEFINITIONS
├── global
├── north_america
├── china_mainland
└── g17_lite
```

每个 Scope 声明自己的 modes、卡片、字段格式和图表，但共享：

- Seed／年份参数。
- 当前年份和悬停年份联动。
- 实际／名义口径规则。
- 数据完整性检查。
- 一个 `/api/world` 请求和一个世界 identity。

中国大陆页面沿用全球／北美成熟布局，货币显示人民币：

- 实际 GDP：2025 年不变价万亿元人民币。
- 名义 GDP：当年万亿元人民币。
- CPI／GDP 平减指数：2025=100。
- 人民币指数：2025=100，上升为升值。

G17-lite 页面只显示真正有助于理解跨区清算的内容：

- 北美、中国大陆、剩余世界的相对增长和贸易权重。
- 美元／人民币相对汇率与各自篮子指数。
- 贸易余额、私人资本流、官方储备／储备货币需求。
- 守恒残差和口径提示。

不在卡片上堆放版本解释、开发阶段说明或跨 Seed 百分位。

## 12. 分阶段实施

这是一个整体 Goal，阶段只是控制改动顺序；中间只做针对断点的快速冒烟，完整测试和多 Seed 校准集中在最后。

### W1：契约、配置与类型

- 新增中国支持契约、G17 契约和两个配置文件。
- 扩展 registry 与 identity hash。
- 定义中国、G17、RoW、World dataclass。
- 冻结符号、单位和 t/t+1 时序。

完成判定：配置可导入，契约可校验，尚不要求模型曲线完成。

### W2：中国大陆零端口独立运行

- 实现中国大陆四个内部模块和公共区域输出。
- 使用显式零 G17 输入跑通 2025–2085。
- 完成 CNY 实际／名义 GDP、价格、政策信用、公共金融、能源和资产口径。

完成判定：单 Seed 无 NaN、公共契约完整、人民币恒等式闭合、同 Seed 可复现。

### W3：World 编排与剩余世界

- 建立 `run_coupled_world`。
- 全球先运行，北美和中国大陆读取同一全球行。
- 建立实际／名义 RoW 残差与诊断。

完成判定：每年全球规模对账，RoW 始终为正，所有子运行共享世界 identity。

### W4：G17-lite 清算与双向跨年接入

- 实现贸易、资本、货币篮子和储备清算。
- 发布五类端口。
- 五类端口接入北美 v0.4 和中国大陆 v0.1 的明确 owner。
- 添加 zero-G17 基线与方向性冲击夹具。

完成判定：默认世界出现温和非零汇率／资本／贸易联动，且不存在同年重跑。

### W5：服务缓存与 API

- 用 `_WORLD_CACHE` 替换独立缓存。
- 新增 `/api/world`，把 scoped API 改为同一世界投影。
- 缓存估算、清理和身份字段以世界为单位。

完成判定：同一查询下所有 API 的年份、Seed、hash 和快照完全一致。

### W6：Viewer v4

- 建立声明式 Scope。
- 接入中国大陆和 G17-lite 页面。
- 保持悬停年份、图表与卡片口径一致。
- 清理前端手工拼入全球 Brent 等临时组合，数据由世界响应明确提供。

完成判定：四个 Scope 可切换且不重新模拟，人民币／美元／指数单位无混用。

### W7：最终验收、校准与权威文档

- 运行单元测试、JS 语法检查、API 冒烟和浏览器视觉检查。
- 冻结参数后再跑校准集、验证集和留出集。
- 写入正式当前文档、区域文档和新 ADR。
- 将本路线移入归档。

## 13. 最终验收矩阵

### 13.1 确定性与前缀

- 同 Seed、同 years、同配置 hash 的整个 `WorldRun` 字节级结果一致。
- 90 年运行的前 60 年等于独立 60 年运行。
- 新增中国或 G17 的地址化随机项不改变全球 v0.5 结果。

### 13.2 中国大陆恒等式

- 实际 GDP、平减指数与名义 GDP 每年严格闭合。
- CPI、平减指数、名义资产价格无 NaN／Infinity。
- 广义公共债务 owner 始终一致。
- 公共区域字段和十类 family 全部满足契约。

### 13.3 G17 守恒

- 每区国际收支残差绝对值 `< 1e-8` 共同美元。
- 全球贸易余额加总残差 `< 1e-8`。
- 全球私人资本流加总与储备／发行者对手项严格一致。
- 货币篮子加权 log 变化残差 `< 1e-8`。
- 全球实际／名义 GDP 与北美＋中国＋RoW 的残差 `< 1e-8`。

### 13.4 时序与方向性

- G17 t 的任何变化只能从 t+1 进入区域。
- 伙伴增长上升应改善本区下一年外需。
- 本币升值通常降低进口通胀、削弱滞后出口竞争力。
- 私人资本流入通常改善下一年融资／信贷条件。
- 实际油价上涨通常恶化中国大陆贸易条件并抬升总体 CPI。
- 北美储备货币需求增强通常支持美元融资或降低相应期限／流动性压力。

### 13.5 普通世界统计目标

参数冻结后使用与全球一致的三段 Seed：

- 校准集：0–399。
- 验证集：400–499。
- 留出集：500–699。

初始目标不是预测现实点位，而是避免明显失真：

- 中国大陆普通实际增长多数年份约 2%–5%，潜在增速缓慢变化。
- CPI 中枢稳定但有普通周期，不是一把尺子。
- 管理浮动人民币年度变化的 5%–95% 区间初步控制在约 ±3%–4%，不得由截断器制造边界堆积。
- 普通资本流动多数年份控制在约 ±3% GDP 内。
- 汇率、贸易和资本不得只靠独立随机项移动；方向测试必须通过。
- 不要求每个 Seed 权益都胜过债券，但长期名义资产与名义经济规模、贴现率和风险的关系必须可解释。

### 13.6 UI 与 API

- 四个 Scope 的当前年份完全同步。
- 卡片、折线与悬停值来自同一行。
- 中国大陆 GDP 不显示美元单位。
- G17 对账页明确区分账户水平和下一年脉冲。
- 页面无 NaN、无旧的“宏观解说／支线”区块、无跨 Seed 信息。

## 14. 失败时禁止采用的补丁

- 不用扩大随机噪声掩盖过平曲线。
- 不用大量 `clamp` 把汇率、资本流或增长压在目标区间。
- 不在 Viewer 临时换单位修复模型口径。
- 不让服务端对不同 Scope 分别模拟。
- 不用同年二次运行修复反馈时序。
- 不让 RoW 出现负 GDP 后简单截为零。
- 不为中国大陆复制北美的 25bp 网格、HY owner 或 QE 命名。

出现这些问题时，应回到 owner 公式、账户定义或初始规模校准。

## 15. 完成后的文档收口

Goal 完成后新增或更新：

- `docs/current/RUNTIME_ARCHITECTURE.md`：写入 WorldRun 与 G17 年度时序。
- `docs/current/CONTRACTS_AND_UNITS.md`：写入 CNY、共同美元、汇率符号和国际收支恒等式。
- `docs/current/VIEWER_PROJECTION.md`：写入四 Scope 和单一世界响应。
- `docs/regions/NORTH_AMERICA.md`：从 v0.3 更新为 v0.4 的非零 G17 接口。
- `docs/regions/CHINA_MAINLAND.md`：写中国大陆 v0.1 的运行事实。
- `docs/current/G17_LITE.md`：写清算机制、端口和冻结校准。
- `docs/decisions/ADR-004-SINGLE-COUPLED-WORLD.md`：记录单一世界缓存、RoW 和跨年清算决策。

本文件届时只保留为实施历史，不继续承担当前事实说明。
