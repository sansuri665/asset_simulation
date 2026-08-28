# 原油预测研究部与双合约短期预测

> 状态：预测引擎、连续 vintage、评分、竞技后台与预测研究人员架构已实现；玩家招聘展示、自荐文本和历史履历卡尚未实现
>
> 权威范围：预测研究人员的隐藏专业能力／研究风格、当前主力／下一主力周线预测、滚动修订与事后评分
>
> 代码基线：`asset-simulation-oil-short-term-forecast-v0.2.0`
> 最近核对：2026-08-28

## 1. 部门边界

预测研究部是四大投资部门中的信息生产 owner。它回答“市场可能怎么走”，不决定目标仓位、资本授权、风险上限或成交方式。

```text
可见历史与当前期货曲线
→ 预测研究人员的专业能力 + 研究风格
→ 主力／下一主力联合周线预测
→ 半月 vintage 继承与修订
→ 目标周实现后评分
→ 策略部门读取预测
```

市场 owner 仍是 `oil_futures_overlay_v8`。预测层可以读取已经生成但尚未公开的隐藏路径来制造有能力差异的合成研究人员，但普通预测输出不含未来真实周 K，也不能写回价格、成交量、持仓量、宏观或玩家仓位。

预测研究与策略研究是不同部门。研究员偏趋势而 PM 偏回归、研究员谨慎而 PM 激进等组合全部允许；系统不计算跨部门“适配总分”，后续效果只通过真实工作流接口自然产生。

## 2. 三层人员架构

预测研究人员不再由“先抽一个总分，再让六维在附近抖动”定义。v0.2 使用三层结构：

```text
latent professional traits
        ↓
六维隐藏专业能力 + 五维隐藏研究风格
        ↓
实际 forecast behavior
```

玩家未来招聘时不会直接看到这些底层数值。面向玩家的正式招聘设计仍是“候选人的自荐 + 过去表现”，由玩家自己推断人物特征。当前 API 暴露 profile 仅用于开发、测试和竞技后台，不代表最终招聘 UI 契约。

### 2.1 六维专业能力

六项能力全部是 skill，高通常意味着该类预测误差更小：

| 字段 | 含义 |
|---|---|
| `direction` | 方向与中短趋势判断 |
| `path` | 未来收盘路径 |
| `turning_points` | 高低点和拐点时机 |
| `range` | 周振幅与高低区间 |
| `term_structure` | 主力／下一主力相对关系 |
| `revision` | 新信息到来后的纠错能力 |

`capability_total_score` 仍保留为内部加权摘要和现有合成 Demo 的宽泛生成约束，但不是未来玩家可见的招聘评级。

### 2.2 能力相关性由 latent traits 产生

能力不直接写死两两相关系数。候选人先生成六个深层专业特质：

- `directional_reasoning`
- `path_modeling`
- `turning_point_sense`
- `volatility_modeling`
- `curve_specialization`
- `adaptive_learning`

多个能力共享部分 latent loading，因此方向与路径、拐点与修正等会自然正相关；期限结构又有独立的 curve specialization。每个能力另有 idiosyncratic component，所以“反应快但不擅长路径”“方向一般但曲线极强”等偏科组合仍可出现。

现有 score-range API 只约束加权总能力落在指定宽泛区间，单维离目标中枢最大可偏离 30 点，不再使用旧版 ±14 点的紧密聚团。

### 2.3 五维研究风格

研究风格没有总分，也没有“高就是好”：

| 字段 | 0端 | 100端 |
|---|---|---|
| `trend_reversion_bias` | 更偏均值回归 | 更偏趋势外推 |
| `fundamental_market_bias` | 更偏基本面锚定 | 更偏价格／市场行为 |
| `confirmation_lead_bias` | 等待确认 | 提前押拐点 |
| `confidence_style` | 谨慎、区间更宽 | 高确信、区间更窄 |
| `revision_style` | 观点黏性高 | 快速重新定价 |

风格使用独立 latent traits 生成，不与能力总分绑定，因此高水平研究员和低水平研究员都可以是趋势派、谨慎派或快速修正派。

## 3. 风格如何进入运行时

50 分是中性点，精确复现 v0.1.3 的 baseline behavior。风格只调整已经存在的研究行为旋钮：

- 趋势／回归与市场行为偏好调整 `trend_extrapolation` 和 `mean_reversion`；
- 抢先／确认偏好调整 `timing_lead_weeks`；
- 置信风格调整预测置信区间宽度，不直接提高预测正确率；
- 修正风格调整 `revision_speed` 与 `thesis_persistence`。

永久方向偏置和期限结构偏置仍保持中性基准。研究风格不改变六维能力，不直接放大仓位，也不越过 PM、风控和交易部。

## 4. 预测目标与连续 vintage

每个半月选择当前 `main_contract_id` 及其下一主力命名合约，预测截点之后到最终结算周的剩余周 K。上半月从 W3 开始，下半月从下月 W1 开始。

P0-B 后，服务使用连续 forecast session：直接请求较晚半月时会按同一机构从游戏起点补齐连续 revision ancestry，而不是只临时重建“上一期 + 当前期”。同一命名合约从 next-main 升为 main 时仍按 contract ID 继承旧预测。

## 5. 评分

评分仍严格只读取评价截点已经实现的目标周。当前权重保持不变：

| 维度 | 权重 |
|---|---:|
| 路径 | 30% |
| 方向 | 20% |
| 拐点 | 15% |
| 振幅 | 15% |
| 期限结构 | 15% |
| 修订 | 5% |

配置能力是隐藏先验参数；`measured_radar` 是事后已经实现的预测成绩，两者不得混为一项。未来玩家招聘展示应该以历史事实与自荐文本为主，而不是展示配置能力雷达。

## 6. 确定性与信息隔离

候选人 latent traits、能力偏科和研究风格全部使用 addressed RNG；相同 Seed、同一候选生成条件必须稳定。新增人物生成不得改变市场随机流。

零能力预测仍不能保留隐藏未来形状。普通 forecast output 不发布真实未来 K；research style 只能改变研究者如何处理可见趋势、时点、置信区间和修订，不获得额外未来信息。

## 7. 测试结构

本轮同时收口测试组织：

- 新增 `tests/support.py`，跨测试模块复用只读 deterministic macro run，避免多个测试文件重复生成同一 Seed／年限世界；
- P0-B 的永久回归测试回归所属领域：期货世界 parity → `test_oil_futures_curve.py`，forecast session → `test_oil_short_term_forecast.py`，compact report → `test_oil_investment_competition.py`；
- 删除历史性的 `test_p0b_incremental_world.py` 文件，但不删除其关键回归门槛；
- `test_service_viewer.py` 不再重复跑 forecast-session 连续性，这一行为由预测领域测试负责；
- 人员相关性／偏科／风格映射用纯生成测试完成，不为了测试人物生成而启动长市场回放。

完整单元测试仍是合入门槛；测试精简的目标是减少重复 owner 和重复长世界构造，不是降低覆盖。

## 8. 尚未实现

- 玩家正式招聘页面；
- 候选人自荐文本；
- 由过去真实运行生成的履历／表现摘要；
- 人员持久 ID、职业履历和机构间流动；
- 多人研究部与资产专业经验；
- 研究员对不同策略／资产的经验迁移。

这些属于后续 personnel/recruitment layer，不应反向污染本轮预测模型 owner。

## Owner

- 人员生成：`model/oil_forecast_research_profile.py`
- 预测公式：`model/oil_short_term_forecast.py`
- 配置：`config/oil_short_term_forecast_v0.2.json`
- 契约：`contracts/oil_short_term_forecast_v2.json`
- 测试：`tests/test_oil_short_term_forecast.py`
- API：`server.py`
