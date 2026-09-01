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
