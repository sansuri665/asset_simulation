# Main 改进候选：2026-09-05 审查修复

## 来源与边界

- 分支：`main-review-fixes-candidate`，直接以 main `75722a1db5867eec9a2f0a1fa96fa30b1df88cb4` 为起点。
- 只移植独立定价实验 `1f077c6` 的核心、配置、测试与审计工具；不合并全球船池、垄断经营或成本实验。
- 上游全球宏观、总液体、原油、区域角色、区域周期参数、14条主要航线和参考航程不重新校准。
- main 由用户另行决定是否合并；本候选不包含自动合并操作。

## 修复清单

### 1. 消除供给验证适配器的日历错配

旧实现将两回合前的**窗口货量**直接变成当前船位。9、10、11天窗口因此错配，恒定每日货量也会产生虚假的船货周期。

新实现为：

`test_supply[t] = round(lagged_daily_cargo[t-2] * current_turn_days / vessel_capacity + reference_buffer * current_turn_days / reference_turn_days)`

实际船队以后必须发布自己的 prompt 船数，不能使用这个测试公式自动造船或补船。
价格公式的敏感度3.0、库存项0.03、惯性0.25、基准35,000及上下护栏均未为维持旧振幅而调整。

### 2. 原始区域边际必须相容

IPF 开始前检查原始进出口总量，只允许绝对值不超过 `1e-7 mb/d` 的舍入尘埃进行归一化。
结束后检查每个区域的**原始**出口或进口目标，而不是仅检查同一矩阵内部的汇总。
真实的供需差额现在会报错，不会被自动缩平。正常输入使用原来的浮点运算顺序。

### 3. 报价不是成交，更不是收入

核心输出 `market_status`、`price_observation_available`、`is_transaction_price`。
零需求时保留上一项指示价格，不生成新的市场价格观测；零供给时可以有高指示价格，但无法运输。
测试执行层仅当确有整船匹配时填写 `executed_fixture_tce_2025_usd_per_day`，否则为 null。
不存在燃油、港口费、OPEX、利润、折旧、利息或公司现金流结算。

### 4. 库存的准确语义

G、W、E 是相对于滞后正常运输计划的偏离，不是绝对岸罐或炼厂库存。
在当前封闭计划偏差测试中有 `E[t] = -G[t-3]`；这是预期性质，不代表独立模拟了炼厂消耗。
`(G-E)/2` 是一个选定的压力代理，不是普适的物理欠运量公式。

`cumulative_unfilled_fixture_observations_vlcc` 是逐回合未匹配观察数之和，不是不同货盘的数量，也不是期末积压。
旧名称 `total_unfilled_fixture_vlcc` 仅保留作兼容别名。期末 G/E 偏离另行报告。

### 5. 研究与决策信息分离

`/api/global`、`/api/oil-price` 继续供研究Viewer观察全路径，并明确标为研究范围。
新增 `/api/decision?seed=42&years=60&year=2030&month=1`，其时点是**所选月份结束后**：

- 宏观只发布已完成年度；初始年度条件在起点可用。
- 原油价格只发布已完成月线，不含当年年末油价锚。
- 运输只发布当前月允许的字段、航线和区域基本值。
- 不含隐藏长期需求情景、全路径结果哈希、未来年份范围或未来年度输入。
- 快照哈希只依赖可见字段；延长模拟终点不改变同一截点的快照。

这是信息边界适配器，不是正式游戏时钟或存档系统。月内航运决策应使用最近一个已经完成的月度快照，不能提前使用本月 high/low/close。
原有 `/api/oil-shipping` 仍是研究截点接口；它的完整运行元数据不应用作决策输入。

### 6. 接口和界面小修

- 统一拒绝定价入口的 NaN、无穷、布尔数值及非整数回合天数。
- 每月仍分三个回合，真实日数严格守恒；并未改成360天一年。
- 修复三个Viewer中缺省URL参数误读为0，以及输入Seed 0被替换为42的问题。
- 健康接口航线数量直接读取注册配置；README不再写旧的9条航线。
- 定价审计保留配置、需求、供给路径、CPI和结果哈希。
- 明确 `years=20` 为初始年后的20次年度转移，月度世界覆盖21个日历年、756个航运回合。

## 验证

候选CI运行完整Python测试（3.11、3.13）、真实执行的JavaScript参数测试、8 Seed × 60年上游审计，以及5 Seed × 20次年度转移的定价审计。
`tools/run_review_checks.py` 另外直接调用旧提交与新代码，复现恒定日需求反例并比较真实Seed旧/新路径。
同一脚本以Git工作树运行原main，逐Seed核对宏观、油价投影、海运世界的结果哈希，要求完全相同。

命令：

```sh
python -m unittest discover -s asset_simulation/tests -v
node tools/test_viewer_parameters.cjs
python -m asset_simulation.audit_oil_shipping_demand --seeds 0,1,2,3,4,5,6,7 --years 60
python -m asset_simulation.audit_single_route_pricing --seeds 0,1,5,7,42 --years 20
python tools/run_review_checks.py --output review_validation.json
```

实际运行数值将保存在同目录 `REVIEW_FIX_VALIDATION.json`。历史定价实验的振幅结论不再作为已通过的经济校准。
这些是软件与行为回归检查，不是使用真实运费数据拟合市场弹性。

## 有意未做

不增加逐船状态机、区域绝对库存、成本会计、债务、期租、造船或拆船，不改变五回合时间原则；不重做区域进出口角色和宏观双向反馈。
