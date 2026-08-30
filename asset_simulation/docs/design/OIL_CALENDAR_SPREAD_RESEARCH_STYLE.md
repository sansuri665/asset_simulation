# 原油短期跨期价差策略：策略研究特质体系

> 状态：第二策略 v0.2.1 研究候选设计  
> Style owner：`oil_calendar_spread_research_v1`  
> Personnel owner：`oil_strategy_research_v2`  
> Construction capability owner：`oil_strategy_research_v2`

## 1. 设计原则

第二策略不再直接把方向策略的八维蛛网当作自己的蛛网，但也不创建第二位 PM。

正式关系是：

```text
同一个已任命策略研究负责人
        ↓
通用 oil strategy personnel profile
        ↓
strategy-type-specific projection
        ↓
Calendar Spread dedicated style radar
        ↓
spread signal / target expression
```

因此同一个人可以：

- 在方向策略里明显偏趋势；
- 在跨期价差里只略偏趋势甚至偏回归；
- 仍然保留相似的资本胃口、节奏、纪律和持有习惯。

这表达“同一个人的投资性格跨策略相关，但不会机械复制”。

专属 radar 仍然是**偏好，不是能力**：没有总分，没有 Alpha 分，没有“高分更强”。未来表现只能来自真实历史 track record。

## 2. 八个专属风格维度

| 维度 | 低分端 | 高分端 | 当前作用 |
|---|---|---|---|
| `curve_continuation_reversion` | 价差回归 | 价差顺势 | visible curve momentum / mean-reversion 权重 |
| `forecast_vs_visible_curve` | 当前曲线证据主导 | 双腿预测主导 | forecast spread 与 visible curve 两个已计算信号的组合权重 |
| `dislocation_selectivity` | 广泛参与 | 等待大偏离 | signal deadband / 参与阈值 |
| `capital_deployment` | 保守部署 | 积极部署 | 已授权资本中的正常策略使用比例 |
| `adjustment_tempo` | 缓慢调整 | 快速调整 | spread target 调整速度 |
| `rebalance_activity` | 低频再平衡 | 主动再平衡 | 正常换手与策略刷新强度 |
| `holding_patience` | 快速兑现 | 耐心持有 | 目标缩小时保留旧 spread exposure 的黏性 |
| `forecast_horizon` | 偏 2 周 | 偏 4 周 | 短期双腿预测中的期限权重 |

### 为什么没有 `near_month_focus`

Calendar Spread v1 的交易对象就是：

```text
+1 Main
-1 Adjacent Next
```

把“近月集中”继续作为 PM 轴会天然鼓励破坏 1:-1 腿平衡，因此该轴在第二策略中被移除。reference adapter 固定为中性 50，仅用于兼容已有 PM 参数映射。

## 3. 第二策略真正新增的哲学轴

### 3.1 Forecast vs Visible Curve

这是第二策略最重要的新轴。

当前信号先独立计算：

```text
forecast_spread_signal
visible_curve_signal
```

其中 visible curve 本身仍包含：

```text
curve continuation
vs
curve mean reversion
```

然后由 PM 的 `forecast_vs_visible_curve` 决定组合：

```text
raw_signal
= w_forecast × forecast_spread_signal
+ (1 - w_forecast) × visible_curve_signal
```

注册映射：

```text
score 0   → forecast weight 45%
score 50  → forecast weight 70%
score 100 → forecast weight 90%
```

默认 50 分严格保留 v0.1.2 的 70/30 基线。

这一维不允许：

- 改写 forecast component 本身；
- 改写 visible curve component 本身；
- 读取未来真实价格；
- 绕过 deadband；
- 把能力分变成信号奖励。

它只是回答：**当研究预测与当前期限结构证据意见不完全一致时，这位 PM 更相信谁。**

## 4. 同一个人如何得到不同策略画像

专属 radar 不是随机重抽，也不是照抄方向 radar。

每一维使用：

```text
50
+ 通用风格偏离 × 注册 loadings
+ strategy-specific deterministic idiosyncrasy
```

其中：

- 通用风格偏离保证跨策略相关性；
- strategy-specific idiosyncrasy 允许同一人在两个策略里不完全一致；
- idiosyncrasy 只由人员 profile hash 和维度地址决定；
- 不使用市场未来、预测真值或历史 PnL；
- 最终分数强制落在 10—90。

默认兼容负责人是唯一特例：八维全部严格 50，用于保证旧基线可复现。

## 5. 风格与能力严格分离

第二策略专属 style owner 不新增能力分。

Construction capability 仍使用同一位人员已有三项：

| 通用能力 | 第二策略解释 |
|---|---|
| `exposure_construction` | spread exposure construction |
| `transition_planning` | pair transition planning |
| `contract_lifecycle_planning` | curve lifecycle planning |

运行顺序：

```text
Dedicated PM style
→ ideal spread target
→ construction capability bounded error
→ submitted spread target
→ persistence
→ thesis
→ strategy-specific risk
→ paired execution mandate
```

因此：

- 风格决定“想怎么做”；
- 能力决定“能否稳定把自己的想法构造成方案”；
- 风险决定“公司允许做到哪里”；
- 交易部决定“如何成交以及付出多少可避免成本”。

四者不得互相代替。

## 6. 当前边界

本版专属 style 已进入 v0.2.1 candidate decision。

仍未完成：

- 长样本 style economics calibration；
- 专属风格的开发／验证 Seed 分离；
- pair execution trader adapter；
- Strategy Book settlement ledger；
- portfolio capital allocation；
- 多策略组合风险；
- 正式竞技接入。

因此当前只能宣称：**专属风格语义、确定性投影、信息隔离和默认兼容已经实现**，不能宣称这八维的经济区分度已经成熟。
