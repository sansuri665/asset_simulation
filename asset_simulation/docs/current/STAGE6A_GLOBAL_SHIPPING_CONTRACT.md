# Stage6A：全球原油轮物理契约与参数目录

分支 `stage6a-global-shipping-contracts`；从 Stage5A v0.2 `b997920847fbf5f9b7510cffc5487ee8f1c6a1bc` 继承。主干基线仍为 `a2a7281`，不修改 main 或原Stage5A分支。

## 1. 交付边界

这轮是全球扩展的**基础目录、转换函数和测试**，不是已经完成全球市场。

```
原有25格原油贸易需求（公开显示为14条+其他池）
 → 有来源说明的船型/货盘/航路目录
 → 每10运营日的按船型货量分解
 → 单程载货/卸货和另行选择的空船转场计划
 → 回合、桶数、载量、参考船日及未解决地理的审计
```

不创建900艘船、不自动给航线分船、不交易、不重新生成全球TCE、不修改成本、债务或上游需求。旧Stage5A的船舶与有界压力模型完全保留。

主要文件：

- `config/global_shipping_physical_v0.1.json`：手工维护的最小参数、来源与假设；继承并锁定原路线网络哈希，不重复抄写25格货量和距离。
- `model/global_shipping_contract.py`：展开目录、严格校验、载货与压载的冻结航程计划。
- `model/global_shipping_projection.py`：只读重放现有航线owner，恢复25格货量，分解船型工作量。
- `contracts/global_shipping_physical_v1.json`：单位、责任边界及下一阶段接口约束。
- `audit_stage6a.py`、`tests/test_stage6a.py`：可复跑审计及专项测试。

## 2. 统一什么，不统一什么

统一10运营日/回合、0回合装货、1回合卸货、桶/吨/海里/节的定义；不统一不同船型的货盘或每条航线的航程。

每个来源月的日货量用于3个10日回合，保持Stage5A的360日运营年投影。原世界仍用自然月历。输出自然月参考量、运营计划量和差额；不补月底/年底突发货盘。

参考船型如下。它们是标准服务包，不是每一艘现实船的满载极限。

| 类别 | 参考载重吨DWT | 默认货盘吨数 | 默认货盘桶数 | 重载/压载规划速度 |
|---|---:|---:|---:|---:|
| VLCC | 300,000 | 270,000 | 1,971,000 | 13 / 12.5节 |
| Suezmax | 160,000 | 135,000 | 985,500 | 13 / 12.5节 |
| Aframax | 105,000 | 80,000 | 584,000 | 13 / 12.5节 |

DWT不是货物吨数；全项目仍以7.3桶/吨近似，不声称适用于每种原油密度。航速字段归船型所有，但这版都采用Baltic 2015年历史基准13/12.5节，**不假装实测到三类船必然有不同平均航速**。以后可以独立替换。

按公开基准调整部分货盘：西非→东亚VLCC 260,000吨；西非/巴西圭亚那→欧洲Suezmax 130,000吨；海湾→欧洲Suezmax 140,000吨；美湾→欧洲Aframax 70,000吨。基准路线只是量级和服务类型的参照，不等于整个盆地的市场份额。

## 3. 航线、航路、船型适配是三个不同对象

`pair_id` 是贸易起终点；`path_id` 是实际采用的航路；船型适配掩码决定一个标准货盘能否在该模型航路上执行。船型份额0不等于物理禁行，份额也不是船数占比。

原有海里参数作为会计参考距离保留，不随新目录静默改写。例如：

| OD / 船型 | 重载海里 | 重载回合 | 卸货回合 | 返回原产地参考回合 | 合计 |
|---|---:|---:|---:|---:|---:|
| 海湾→东亚 / VLCC | 6,200 | 2 | 1 | 2 | 5 |
| 海湾→南亚 / VLCC | 1,800 | 1 | 1 | 1 | 3 |
| 西非→东亚 / VLCC | 10,200 | 3 | 1 | 3 | 7 |
| 巴西圭亚那→东亚 / VLCC | 11,000 | 4 | 1 | 4 | 9 |
| 美湾→东亚 / VLCC | 14,500 | 5 | 1 | 5 | 11 |
| 海湾→欧洲 / Suezmax，苏伊士代理路径 | 6,100 | 2 | 1 | 2 | 5 |
| 海湾→欧洲 / VLCC，好望角代理路径 | 11,500 | 4 | 1 | 2 | 7 |

最后一行的11,500海里是**低置信度设计代理值，不是新测得航程**。满载VLCC在本模型苏伊士路径中被保守排除，不等于现实所有VLCC都无法过苏伊士；SUMED管道、部分装载和多船转运不在本轮。空船回程另用6,100海里的苏伊士代理距离。美湾→东亚不启用巴拿马捷径。港口吃水、码头、装港拼货/STS的资格尚未细化，`geography_ready`不代表航海许可。

原先手写的西非/巴西8回合不沿用为必须命中的答案；新回合数由同一规则生成。

## 4. 参考天数与游戏天数分开

```
参考海上天数 = distance_nm / (24 * speed_knots)
海上回合 = max(1, floor(参考天数/10 + 0.5))
```

正距离最少1回合，只有显式同一区域停留才为0。采用最近回合、半值向上取整，不将每一段都向上取整。这样保留海湾—东亚2+1+2。海上取整不是保守ETA，可能早于连续参考到达；误差作为字段和表格公开。本目录最大重载误差约4.74日、压载误差5日；1,800海里的短航线误差比例很大。这是明确的游戏化，而不是物理精确性。

以后引用动态 `effective_haul_nm` 必须由航路owner确定它属于哪条船型路径；`effective_distance_nm` **替换**选定路径距离，绝不把已含改道的距离再乘一次改道系数。已出发计划冻结距离、速度推导天数和就绪回合，后来的航路参数不会追溯改动在途船。

## 5. 卸完货以后船在目的地，不必返回原航线

```
OPEN_AT_ORIGIN → LADEN → DISCHARGING → OPEN_AT_DESTINATION
OPEN_AT_ANY_KNOWN_NODE → BALLAST(target) → OPEN_AT_TARGET
```

`laden_plan`只生成运输至卸货完成的计划；`ballast_plan`需要调用者明确选择目标区域。没有成本、报价或自动最优路线选择。

例如t回合发船海湾→东亚，t+3开始交货/空船就绪；若此时选择压载回海湾，t+5就绪；若选择去西非，则按东亚→西非的独立空船距离处理。压载没有货物，不再消耗1回合“卸货”。

已定义25格进口区→出口区空船参考，以及25格出口区→出口区选择（含5格原地停留）。跨出口区的6组对称距离是低置信度规划先验，未作AIS校准。入口未知、地理未明确的转场会报错，而不是自动使用0天。

## 6. 其他池不是一个港口

公开 `other_routes` 汇总了11格贸易联系。Stage6A只读重放同一个seed的现有航线owner，恢复这11格；逐月校验恢复后的25格重新聚合等于原来的14条+其他池。它不是用新偏好矩阵再次编造一套需求。

宏观owner发布的浮点数据有8位小数，重放航程允许不超过相对1e-7的舍入尘埃；货量聚合核对使用1e-7 mb/d绝对容忍度。所有分船型后的货量用整数桶，分解残差必须严格为0。含隐藏场景或错误来源配置的世界会被拒绝，不能偷偷漏掉其冲击。

`other_export_regions`和`rest_of_world`本身仍是集合地区。25格中有9格涉及它们；静态参考货量共17.5 mb/d，即39.8 mb/d的约43.97%。这些货量仍计入分船型工作量和保留额，但**不能直接生成真实航行计划**，需要下一阶段细分子盆地。

其余16格也只是具名盆地的代表性服务节点，仍不是具体港口。32条空船选择拥有具名盆地两端，其余保留为待细分引用。不能将同一只位于“其他地区”的船任意瞬移到世界各处。

## 7. 船型份额是可调先验，不是拿船数反推的事实

本轮所有份额明确标为 `design_prior_not_observed`、低置信度。公开Baltic基准能支持哪些船型服务这些市场、标准货盘的量级，不能推出真实承运比例。下面是初始实验值；三类船只形成当前模型的原油运输服务范围，不涵盖现实全部Panamax、产品船或影子船市场。

| 起终点 | VLCC% | Suezmax% | Aframax% | 地理 |
|---|---:|---:|---:|---|
| gulf::east_asia | 90.0 | 10.0 | 0.0 | 代表盆地 |
| gulf::south_asia | 55.0 | 35.0 | 10.0 | 代表盆地 |
| gulf::europe | 20.0 | 70.0 | 10.0 | 代表盆地 |
| gulf::north_america_import | 50.0 | 40.0 | 10.0 | 代表盆地 |
| gulf::rest_of_world | 40.0 | 40.0 | 20.0 | 集合待细分 |
| us_gulf::east_asia | 70.0 | 25.0 | 5.0 | 代表盆地 |
| us_gulf::south_asia | 65.0 | 30.0 | 5.0 | 代表盆地 |
| us_gulf::europe | 10.0 | 50.0 | 40.0 | 代表盆地 |
| us_gulf::north_america_import | 0.0 | 20.0 | 80.0 | 代表盆地 |
| us_gulf::rest_of_world | 15.0 | 40.0 | 45.0 | 集合待细分 |
| brazil_guyana::east_asia | 80.0 | 20.0 | 0.0 | 代表盆地 |
| brazil_guyana::south_asia | 70.0 | 25.0 | 5.0 | 代表盆地 |
| brazil_guyana::europe | 15.0 | 75.0 | 10.0 | 代表盆地 |
| brazil_guyana::north_america_import | 10.0 | 60.0 | 30.0 | 代表盆地 |
| brazil_guyana::rest_of_world | 20.0 | 50.0 | 30.0 | 集合待细分 |
| west_africa::east_asia | 80.0 | 20.0 | 0.0 | 代表盆地 |
| west_africa::south_asia | 60.0 | 35.0 | 5.0 | 代表盆地 |
| west_africa::europe | 10.0 | 70.0 | 20.0 | 代表盆地 |
| west_africa::north_america_import | 25.0 | 60.0 | 15.0 | 代表盆地 |
| west_africa::rest_of_world | 20.0 | 50.0 | 30.0 | 集合待细分 |
| other_export_regions::east_asia | 35.0 | 35.0 | 30.0 | 集合待细分 |
| other_export_regions::south_asia | 30.0 | 45.0 | 25.0 | 集合待细分 |
| other_export_regions::europe | 5.0 | 40.0 | 55.0 | 集合待细分 |
| other_export_regions::north_america_import | 10.0 | 30.0 | 60.0 | 集合待细分 |
| other_export_regions::rest_of_world | 10.0 | 35.0 | 55.0 | 集合待细分 |

海湾→东亚默认分配90%给VLCC，不同于Stage5A的100% VLCC单航线假设。**两者需求范围不同，不能把旧245艘直接照搬当成新版均衡船队。** Stage5A原函数与参数没有回改。

每个回合先确定一份总桶数，再用最大余数法按基点分配，三份之和严格等于原数。参考整票数与不足一票的余桶都输出。它们是该窗口的规划分解，不是实际装船，不会因每回合重新出表而删除长期真实库存。

## 8. 参考船日不等于真实全球船数

```
参考循环占用等效船数 = 日货量 × 船型份额 / 单船货盘 × 参考循环天数
```

这个诊断隐含“卸货后空返原产地”、无额外等待等假设。实际全球市场允许三角航行、不同空船目标、等待与维修，因此不能把它当成精确船队需求，更不能为了得到900艘而回调份额。

静态参考分解为VLCC 17.455 mb/d、Suezmax 13.405、Aframax 8.940，合计39.8。份额在VLCC和Suezmax之间各移动10个百分点的敏感性测试，将VLCC参考占用从约378变到568艘；这是参数不确定性的展示，不是船队数量观测。

目录路径吨海里与原世界动态有效吨海里分别报告。海湾→欧洲大船绕行等新路径假设会改变前者，不得拿这种差额声称原世界守恒出错。

## 9. 下一阶段必须遵守的接口

同一条当前可用船可能兼容多条路线，但只能被一次分配消耗。不能给14个定价器各塞一遍同一海湾船池，然后把14个输出当成独立实际供给。

船型份额先作参考分区；真正允许船型替代时，必须在一个共用货物台账上清算。若只做全球VLCC子市场，非VLCC货量必须明确在范围之外，不得挤入VLCC需求，也不能把全世界全部VLCC都分配给一个未覆盖其他市场的子集。

每类船的TCE是每船日收益标尺，不能直接当作可横向替代的每桶报价。未来成本/报价转换单独处理。全球缺船与局部缺船不能重复计价；库存附加溢价有界与整体价格安全限幅也必须分开。本轮未修改有界价格曲线，旧12.25万美元实际上限仍是旧组件的限制，不宣称Stage6A已解决全球价格发现。

下一步应先选两个具名产地和一个目的地，以通用MovementPlan替代写死的返航链，验证跨航线竞争船号；再细分集合地区并扩展全球VLCC。尚不需要加入成本、公司、造船或拆船。

## 10. 验证与使用

专项测试28项，覆盖份额守恒、未知/集合转场拒绝、原始25格恢复、前缀因果、载量与DWT分离、交货与回程时点、冻结计划、航速变化、路径掩码及10日时钟。多Seed采用0、1、5、7、42，各20次年度转移，合计1,260个月、3,780个运营窗口和31,500个OD月度输入；每份货量仅分配一次。

本轮数值审计见 `STAGE6A_VALIDATION.json`。完整回归和继承的Stage5A/上游审计由新分支只读CI执行；不得将仅新增测试通过写成全套CI已通过。

```sh
python -m unittest asset_simulation.tests.test_stage6a -v
python -m unittest discover -s asset_simulation/tests -v
python -m asset_simulation.audit_stage6a --years 20 --output audit.json --tables-dir tables
```

导出表：25行份额表、75行航线×船型表、150行空船选择×船型表。所有表可由配置复建，无需旧分支或外部服务。

```python
from asset_simulation.model.global_shipping_contract import load_catalog, laden_plan, ballast_plan
cat = load_catalog()
leg = laden_plan(cat, 'gulf::east_asia', 'vlcc', depart_turn=0)
assert leg.ready_turn == 3
back = ballast_plan(cat, 'east_asia', 'gulf', 'vlcc', depart_turn=leg.ready_turn)
assert back.ready_turn == 5
```

## 来源与适用范围

配置 `sources` 中存有出处、访问日期及每条来源支持的字段。重要出处：

- Baltic Exchange, Tanker Services：当前公开船型/货盘基准目录。https://www.balticexchange.com/en/data-services/market-information0/tankers-services.html
- Baltic Exchange, Revised tanker benchmark specifications（2015）：历史13/12.5节规划速度。https://www.balticexchange.com/en/data-services/WeeklyRoundup/tanker/news/2015/revised-tanker-benchmarkspecifications.html
- Baltic Exchange, Tanker Whitepaper：300k/160k/105k DWT参考与载货工作量口径。https://emissions.balticexchange.com/en/imo-regulationss/eeoi/tanker-whitepaper.html
- EIA, Maritime chokepoints are critical to global energy security：DWT、货物及不同密度换算的区别。https://www.eia.gov/todayinenergy/detail.php?id=32292
- 已审核的原仓库航线配置：25格海里和货量继承自main `a2a7281`，不重新校准。

这些来源不支持本轮的具体船型百分比、跨出口区转场先验或完整全球船队数；对应字段明确列为设计假设。
