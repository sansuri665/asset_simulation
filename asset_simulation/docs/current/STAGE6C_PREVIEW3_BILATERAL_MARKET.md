# Main–Stage6C Preview3：双边货盘形成与正式报盘

- 分支：`main-6Cpreview3`
- 父提交：Stage6C Preview2 `45cdb1162767ec0e43517e54f7828f3f6b1f4a7f`
- 共同 main 节点：`a2a7281424d066a11eea3eea23d9442aa329b9a0`

本分支不替代或移动 main，也不删除任何历史实验分支。它在 Preview2 的双来源、单目的地 Freight Board 上增加三件事：main 原油航线的只读输入桥、卖方可售意愿，以及询价与正式报盘的回合锁定。

## 1. Main 联动边界

`build_main_linked_preview3_inputs()` 读取 main 已有的 crude-only 区域平衡和航线网络。它沿用 Stage6B 的确定性投影，把每个日历月转换为三个10运营日回合，并为 Gulf→East Asia、West Africa→East Asia 生成：

- `natural_cargo_plan_bbl`：main 航线货量形成的自然货盘先验；
- `source_release_bbl`：只允许这两条子市场航线使用的真实来源释放；
- `ImportRequirement`：与自然航线到期日独立对应的进口义务；
- 上一已完成年度 CPI 信息。

Preview3 不能反写 main 的需求、生产、炼厂加工、区域库存、航线货量或吨海里。它仍是隔离的两来源—东亚市场，不宣称已经执行全球25格 OD。

## 2. 卖方不再是无限被动货源

每个来源的可售量由两部分构成：

```text
Seller offer = stable term offer + inventory-responsive spot offer
```

默认自然货盘的70%作为稳定 term 底座，剩余30%形成现货基准。正常现货报价保留25%报价余量，来源库存通过连续 `tanh` 信号改变现货量；变化再经过0.70的上一回合惯性。

卖方只决定现有原油中多少愿意进入本轮货盘，不决定生产量，也不计算原油售价、OSP、炼厂利润或出口商利润。最终报价始终受以下硬上限约束：

```text
physical source oil
export limit
term + spot willingness
```

任何合同标签都不能创造原油。普通买方延期与来源切换不能穿透 term 底座；只有为减少明确硬库存违规而采取的动作可以在报告中显式越过软商业底座。

## 3. 买方库存焦虑

目的地库存继续只改变进口时点和来源，不删除 `ImportRequirement`。普通库存状态下，总货盘相对自然计划最多变化±10%；硬违规或绝对库存信号达到0.75时使用±20%的紧急范围。

Preview2 将所有 `invalid` 视为可能需要补货，目的地已经超过上限时也会继续加货。Preview3 改为结构化违规：

```text
node / origin / turn / direction / excess_bbl
```

无效方案只有在候选动作严格减少违规数量或违规桶数时才能被接受。因此低库存允许加货，高库存只会减货；当前回合已经发生的缺口也不能由未来到港伪装修复。

## 4. 防止机械振荡

货盘步长采用双尺度：至少250,000桶，同时不低于自然货盘的0.5%；大市场不再用过小绝对步长跑满64次。接近转折点时依次缩小到不低于50,000桶。

- 同一次货盘形成中，库存动作只能保持一个方向；
- 候选动作不能返回已经访问的货盘状态；
- 来源切换进入门槛为10%，反向退出带为5%；
- 一步切换若立即制造显著反向价差，会用更小步长重试；
- 没有严格改善时停止，不把 `cycle_detected` 当作正常收敛。

## 5. 询价与正式报盘

`BilateralSession` 有三个阶段：

```text
ready → indicative → firm → commit/cancel → ready(next turn)
```

`indicative` 阶段可以基于同一个冻结快照反复修改船舶和货盘意向，不移动任何实物。`lock_firm()` 只接受本 session 本回合真实生成过的 indication；锁定后：

- 船舶 ID 和线路不能替换；
- 最终货盘相对最后 indication 每个来源只允许自然货盘±5%的修订；
- 超过卖方报价的修订被拒绝；
- 其它 indication 不能替代已经锁定的方案。

正式撤单使用零动作结算并消耗当前物理回合。已锁定的船不能在同一回合改投其它线路；来源释放和进口义务也不会因撤单消失。如果零动作会直接突破硬库存边界，撤单本身不可物理执行。

`BilateralSession.checkpoint()` 只允许在已结算状态生成存档，同时保存父 Freight Board 状态、Preview3 参数身份和上一回合卖方现货报价记忆。恢复后相同输入必须产生完全相同的后续货盘；打开或已锁定但尚未结算的回合不能存档。

## 6. 默认参数

| 参数 | 默认值 | 作用 |
|---|---:|---|
| `step_bbl` | 250,000 | 小市场基础货盘步长 |
| `relative_step_fraction` | 0.005 | 大市场步长约占自然货量0.5% |
| `minimum_step_bbl` | 50,000 | 转折点最小重试步长 |
| `buyer_inventory_shadow_sensitivity` | 1.0 | 买方库存焦虑斜率 |
| `normal_inventory_swing_fraction` | 0.10 | 普通总货盘增减上限 |
| `emergency_inventory_swing_fraction` | 0.20 | 紧急总货盘增减上限 |
| `inventory_reversal_deadband` | 0.03 | 防止库存动作在零点附近反向跳动 |
| `maximum_source_reallocation_fraction` | 0.30 | 普通来源替代预算 |
| `emergency_source_reallocation_fraction` | 0.60 | 紧急来源替代预算 |
| `seller_term_share` | 0.70 | 卖方稳定货量底座 |
| `seller_spot_offer_headroom_fraction` | 0.25 | 正常现货报价余量 |
| `seller_spot_inventory_response_fraction` | 0.30 | 来源库存对现货量影响 |
| `seller_offer_persistence` | 0.70 | 卖方报价惯性 |
| `firm_cargo_revision_fraction` | 0.05 | 正式确认前最后修订容差 |
| `board_inventory_log_premium_limit` | 0.06 | 剩余直接库存运价项上限 |

`max_iterations` 和运价 numeric guard 是计算安全参数，不应作为玩家难度滑杆。物理船容、航程、到期日、质量守恒和硬库存边界也不能由玩法参数绕过。

## 7. 验证

```powershell
py -3 -m unittest asset_simulation.tests.test_preview3_bilateral_market -v
py -3 -m asset_simulation.audit_stage6c_preview3 --seeds 0,1,42 --years 5
py -3 tools/demo_stage6c_preview3.py --seed 42 --turns 12
```

审计要求 main 输入只读、每个回合可提交、桶数严格守恒、卖方报价不超过实物、同回合没有反向来源切换、正式报盘锁定可验证，并且普通库存调整不能机械撞击±10%上限。

长期审计使用一个显式、可替换的外部空船参考策略：目的地空船按照各来源卖方报价、来源库存压力和已知本地／在途空船的覆盖率分配。它不读取候选运价，不改变当期装载能力，也不属于 Freight Board 或货盘形成层的隐藏船舶优化器；玩家或未来船东模块可以完整替换它。

## 8. 尚未包含

- 全球25 OD 的统一执行与多目的地船舶竞争；
- 原油品质、炼厂适配和不同油种价差；
- OSP、原油现货价格、航次成本、租船合同现金结算；
- 多玩家身份、信用、撤单罚金或声誉；
- 港口泊位和装船队列；
- 当前 main Viewer/API 的正式替换。

因此 Preview3 是可连续运行的双边数量与运力意向实验，不是完整原油交易所，也不是新的正式 main。
