# Asset Simulation 模型入场规则

修改前完整阅读 `asset_simulation/docs/MODEL_CONTEXT_GUIDE.md`，再按其中路由读取 owner。

## 硬规则

1. 代码、注册配置和 JSON 契约是当前事实的最终来源。
2. 当前产品是单一全球宏观、十个原油贸易盆地区域、一个受约束航线网络和只读需求链 Viewer；没有国家级模型、期货或投资机构。
3. 全球年度顺序仍为 `real → inflation → rates → funding → oil price anchor → assets`；返回上游只经下一年输入。
4. `model/oil_commodity.py` 中的旧需求／供给／松紧状态只用于年度价格生成，不是物理桶数。
5. `model/oil_price_projection.py` 只在年度价格锚之间展开月度路径，不得回写宏观或物理油池。
6. `model/oil_physical_world.py` 只负责总液体物理池；`model/crude_physical_world.py` 独立负责供油运使用的 crude-only 物理池。两者必须分别保持库存质量守恒，不得用总液体消费替代炼厂原油加工量。
7. `oil_shipping_regions.py` 只读取 crude-only 产量、炼厂原油加工、原油库存变化和原油管道流，负责区域物理差额；`oil_shipping_routes.py` 负责受约束航线与吨海里。当前不存在港口装船执行或运价。
8. 船只买卖、船队、造船和拆船均未实现，不得从吨海里直接声称已有油运周期。
9. 普通 Seed 不绑定战争、禁运或命名危机；扰动端口只用于测试，不得暴露给正常世界、API 或 Viewer，也不得回写油价。
10. 不得向截点 API 发布未来月度数据。
11. 所有公共单位必须与 `contracts/oil_shipping_demand_v6.json` 一致。

## 最低验收

```powershell
py -3.13 -m unittest discover -s asset_simulation/tests -v
py -3.13 -m asset_simulation.audit_oil_shipping_demand --seeds 0,1,2,3,4,5,6,7 --years 60
```


### 2026-09-05 主线增补（优先于上述旧进度描述）
新增独立 `single_route_pricing` v0.2.1；它只读供需和计划库存偏离并输出TCE，不属于已实现的船队。
定价不接入现有Viewer，也不反写原油需求。研究接口保留；新的 `/api/decision` 只发布月末可见字段，由年度锚插值的油价月线只发布初始年或已完成年度。
详细修复范围、兼容性与验证见 `docs/current/MAIN_REVIEW_FIXES.md`。

### Stage5A v0.2（当前实验分支，优先于旧Stage5A描述）
固定10运营日/回合，日货量投影至360日标签年；真实欠运完整守恒，报价改用有界短记忆信号与软上下界。旧主干定价核保留不改。详情见 `asset_simulation/docs/current/STAGE5A_FIXED10_BOUNDED_PRESSURE.md`。不含成本、需求破坏或增减船。


### Stage6B 多产地—单目的地实验
固定编号VLCC在海湾、西非（可扩四产地）与同一个东亚节点间运行；即时供给只含本地开放船。共用目的地压力、独立源端台账、单次分配与冻结MovementPlan；不含成本或完整全球均衡。见 `docs/current/STAGE6B_MULTI_ORIGIN_MARKET.md`。


### Stage6B v0.2：共用货物与多船型（当前替代分支）
新入口 `model/mixed_cargo_market.py::run_seeded_mixed_market` 使用完整航线货量、固定船型载量和外部配船接口，不按船型份额切货；旧Stage6A份额与商业货盘仅留作历史参照。纯报价按合计兼容运量形成共同服务价值，不含成本。旧Stage6B文件为可复跑对照。见 `docs/current/STAGE6B_MIXED_CARGO_V02.md`。


### Stage6B-v3：透明报价与逐船货批（当前替代候选）
新入口 `model.shipping_v3` 保留完整共同货物，增加逐船实际装载、不可变批次到期日、已承诺到船曲线、分离的有限欠运溢价、可复算报价和存档。`prepare_turn → external Decision → settle_turn` 不做同回合迭代；测试策略单列，不含成本。旧v2代码保留对照。见 `docs/current/STAGE6B_V3_TRANSPARENT_MARKET.md`。
