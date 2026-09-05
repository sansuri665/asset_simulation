# 海湾→东亚单航线供需定价引擎 v0.2.1

此文对应当前 main。旧版 v0.2.0 的普通Seed价格振幅受日历适配器影响，不能沿用为经济校准结论。

## 责任边界

`price_single_route_turn` 输入本回合日货量、天数、即时可用VLCC、两端计划库存偏离、上期实际TCE和CPI，输出指示性TCE与供需诊断。

定价核心不移动船、不制造供给、不结算收入、不计算燃油/OPEX/折旧/债务，也不决定船东的停航策略。
测试适配器与序列回放函数只是供给和账本的验证工具，不是已完成的真船市场。

## 时间契约

装货0回合；重载2回合；东亚卸货1回合；压载返回2回合，共5回合。
在回合t开始时发船，卸货于t+2回合结束完成，货物在t+3回合开始可用；船在t+5回合开始重新可调度。
配置测试仅验证此契约的整数值，并不替代将来的逐船时空守恒测试。

## 定价关系

所有货量先转换为本回合百万桶。`I=(G-E)/2` 是计划偏离压力代理。
库存修复量为 I 的25%，最多增减本回合结构货量的20%。
修复后的需求换算成1.971百万桶的VLCC等效货盘D，与外部船位S比较：

`R=(D+4)/(S+4)`，相对于9.3 mb/d、10.1388888889天、50条prompt船的参考状态归一化。

`raw_signal=3.0*log(relative_R)+0.03*clipped_inventory_gap_days`

`settled_signal=0.25*log(previous_real_TCE/35000)+0.75*raw_signal`

`real_TCE=clip(35000*exp(settled_signal),1000,122500)`

`nominal_TCE=real_TCE*CPI/100`

本次不修改上述经济参数。上下限是原型护栏，不是普适经济上限或运营成本线。

## 无交易状态

- `no_demand`：没有当期需求，保留上一项指示TCE但 `price_observation_available=false`。
- `no_supply`：有需求但无船，仍可报告稀缺状态，不能据此确认收入。
- `indicative_quote`：供需均非零，输出仍只是市场指示价格；不意味着已经签约。

所有核心报价均有 `is_transaction_price=false`。
验证执行层另行报告整船匹配数；只有匹配数大于0才填写 `executed_fixture_tce_2025_usd_per_day`，否则为null。

## 计划库存不是绝对库存

回放器记录实际装船相对于正常装船计划的偏差。三回合后，装船偏差转移到目的地；所以其E是G的滞后镜像。
这能表达欠运/超运的延迟影响，但不能直接预测炼厂断油日或岸罐容量。
`cumulative_unfilled_fixture_observations_vlcc` 不能解释为期末未运走的独立货盘数。

## 日历修复与验证

供给验证工具改为滞后每日货量，并按当前窗口天数及等比例缓冲计算船数。
恒定每日货量的自包含新旧日历公式对照和多Seed实跑见 `REVIEW_FIX_VALIDATION.json`。
完整修复说明见 `MAIN_REVIEW_FIXES.md`。已加入输入及结果指纹，实际/名义TCE口径保持分离。
