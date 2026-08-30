# Investment Decision / Risk v2 设计候选

> 状态：分支候选；不改变 Directional Oil 已冻结的 signal / thesis / execution 核心。
> 基线：`sansuri665/asset_simulation@701a4b59ec01229ecaebc37f2e58ce97ade3fbf2`
> 目标：把资本配置、公司风险胃口、PM 投资意图、委员会仓位授权和专业风险审阅拆成明确 owner，并为 `Oil / Short Horizon` 风控组建立可复用的人员与单策略风控接口。

## 1. 治理原则

### Investment Decision Committee

委员会是治理层，不是第六个运营部门。它拥有：

1. Strategy Charter：批准公司是否经营某个 `asset / horizon / strategy_type`；
2. Capital Mandate：为已批准策略分配公司资本；
3. Company Risk Appetite：批准公司级风险胃口；
4. Position Mandate：把 PM 提案转换为公司愿意持有的期望仓位。

委员会不得产生 alpha：

- PM 为 0 时，委员会不得凭空开仓；
- PM 为正时，委员会不得批准负仓，反之亦然；
- 委员会只能保留或缩小 PM 提议的绝对仓位；
- 市场硬规则不属于委员会自由裁量。

### PM / Strategy Group

PM 负责使用当前可见市场、已发布预测、策略状态和委员会分配资本形成 `proposed target`。委员会资本授权是 PM 的输入；风险部门不再输出建议资本比例。

### Oil / Short-Horizon Risk Group

风控按 `asset=oil / horizon=short_horizon` 建组，而不是绑定某个 strategy id。同一风控组可覆盖 Directional 和 Calendar Spread。

单策略风险审阅必须读取真实仓位对象：

- 当前持仓；
- 委员会 expected target；
- allocated strategy capital；
- company equity；
- 当前市场与公开限额；
- 策略类型；
- company risk appetite；
- 风控人员 profile。

它不再根据 PM 的 `responsiveness / selectivity / holding_patience` 等风格，在仓位形成前猜测一个风险政策。

### Corporate aggregate risk

公司汇总风险仍位于所有单策略风险之后。v2 第一阶段先建立 owner 和接口；真正多策略 aggregate portfolio risk 在第二策略进入正式 runtime 后实现。

## 2. 资本与风险的顺序

```text
Investment Decision
  Strategy Charter
  + Capital Mandate
  + Company Risk Appetite
            ↓
PM strategy engine
            ↓
PM proposed target
            ↓
Investment Decision Position Mandate
            ↓
committee expected target
            ↓
Oil / Short-Horizon Risk Group
            ↓
strategy-risk approved target
            ↓
Corporate aggregate risk
            ↓
Execution Desk
```

资本额度是风控输入，不是风控输出。

## 3. 为什么 1% book 和 50% book 自然不同

风控同时计算：

```text
strategy-relative risk
company-materiality risk
```

例如同样 30% stressed loss：

- 1% 公司资本 book -> 0.3% company-equity loss；
- 50% 公司资本 book -> 15% company-equity loss。

因此无需硬编码 `allocation > X => stricter`。单策略风险容量由策略自身约束和公司 materiality 约束的交集自然决定。

## 4. Company Risk Appetite 与 CRO 人格分离

Company Risk Appetite 是公司政策，由 Investment Decision 批准；换 CRO 不等于换公司风险胃口。

第一版公司风险胃口继续覆盖：

- stress loss；
- margin / capital utilization；
- concentration；
- liquidity / liquidation horizon；
- roll / expiry；
- drawdown / loss containment。

CRO / Risk Officer profile 只描述如何识别和挑战风险。

## 5. 风控人员模型：强风格、轻能力

### Style / review philosophy

首版五维：

- `tail_risk_focus`：普通波动 vs 尾部压力；
- `intervention_earliness`：接近公司上限才干预 vs 提前留缓冲；
- `liquidity_priority`：容忍慢退出 vs 强调快速退出；
- `concentration_aversion`：接受集中 conviction vs 强调分散；
- `model_skepticism`：更信模型中心估计 vs 更依赖保守情景。

风格分布应宽，且没有“越高越好”。

### Lightweight capability

首版三维：

- `risk_measurement`；
- `stress_analysis`；
- `monitoring_discipline`。

候选能力分布应明显窄于预测研究和交易执行人员。能力不直接决定更严或更松，也不能修改市场硬事实；它只允许在 soft-risk estimate 上产生小幅、确定性、无未来数据的估计误差。

低能力有时高估、有时低估；高能力只是更稳定。

## 6. Hard facts 与 soft estimates

### Hard facts

不得受人员能力影响：

- current position；
- committee expected position；
- contract price；
- exchange / market position limit；
- turn trade limit；
- current margin formula；
- trading status；
- months / half-turns to expiry。

### Soft estimates

允许轻量能力误差，但只能使用当前可见信息：

- visible annualized volatility；
- stressed loss proxy；
- liquidation horizon estimate；
- tail multiplier / model uncertainty buffer。

任何 risk function 都不得接收 `GlobalMacroRun`、future market payload 或隐藏 future truth。

## 7. 单策略风险输出

Risk review 输出：

- observed hard facts；
- estimated soft risks；
- strategy-relative materiality；
- company-equity materiality；
- binding rules；
- risk-approved target；
- review rationale；
- risk personnel identity / scope；
- company risk appetite identity。

明确禁止：

- `recommended_capital_authorization_pct_of_company_equity`；
- 风控创建新方向；
- 风控扩大委员会 expected target；
- 风控跨零反向；
- 风控读取 PM 隐藏能力或未来数据。

## 8. 组织范围

第一阶段显式建立：

```text
Corporate Risk Department
  └─ Oil Risk Division
      └─ Short-Horizon Risk Group
          ├─ Directional Oil coverage
          └─ Calendar Spread coverage
```

`strategy_type` 是 coverage 对象，不是人员身份。未来扩中线/长线或其他品种时，通过 scope 扩展，而不是不断增加万能人员蛛网维度。

## 9. 第一阶段验收

必须满足：

1. PM=0 -> committee position mandate=0；
2. committee 不得改变 PM 方向或扩大绝对仓位；
3. risk 不得扩大或反转 committee expected target；
4. risk review 缺少实际 position mandate 时拒绝；
5. risk output 不存在资本推荐字段；
6. 同样 strategy-relative risk 下，1% 与 50% allocation 的 company materiality 显著不同；
7. 不同 risk style 对软限制产生有意义差异；
8. 相同 style 下的 capability 差异小于 style 差异，且不会修改 hard facts；
9. future inputs 不存在于公开 API；
10. Directional Oil 现有 signal / thesis / execution 回归全部保持通过。

## 10. 分阶段接线

本分支先实现治理对象、风控人员和实际仓位风险审阅，再把 Directional Oil 外围治理切换到 v2。切换前新接口可作为 candidate / diagnostic 路径存在，避免在没有回归证据时同时改变第一策略经济语义。Calendar Spread 随后复用同一个 `Oil / Short-Horizon Risk Group`，不再另造临时风控世界。
