# Asset Simulation 文档索引

> 基线：全球宏观 v0.8.1，原油海运 v0.6.9，UI/API v0.7
> 最近核对：2026-09-03

代码、注册配置和 JSON 契约优先于文档。

| 文档 | 范围 | Owner |
|---|---|---|
| `current/PROJECT_STATUS.md` | 当前完成度和下一阶段 | 当前 owner 汇总 |
| `current/RUNTIME_ARCHITECTURE.md` | 年度宏观、月度油池、缓存和 API | `engine.py`、`oil_shipping_world.py`、`server.py` |
| `current/CONTRACTS_AND_UNITS.md` | 单位与公共字段 | `contracts/`、`registry.py` |
| `current/OIL_PRICE_PROJECTION.md` | 年度原油锚与月度价格路径 | `oil_commodity.py`、`oil_price_projection.py` |
| `current/OIL_SHIPPING_DEMAND.md` | 物理平衡、货量、航程与吨海里 | 三个 oil shipping owner |
| `current/MAJOR_ROUTE_VOLUME_ANCHORS.md` | 14条航线状况参考与25格货量校准矩阵 | `oil_shipping_routes.py`、航线配置 |
| `components/GLOBAL_ORDINARY_CYCLE.md` | 普通全球周期 | `real_economy.py` |

仍有效的架构决策为年度时序、名义／实际价格和单一全球世界。旧区域、商品卫星、期货、预测、策略、风控、交易及旧版 Viewer 文档已经退役。


### 2026-09-05 主线增补（优先于上述旧进度描述）
新增独立 `single_route_pricing` v0.2.1；它只读供需和计划库存偏离并输出TCE，不属于已实现的船队。
定价不接入现有Viewer，也不反写原油需求。研究接口保留；新的 `/api/decision` 只发布月末可见字段，由年度锚插值的油价月线只发布初始年或已完成年度。
详细修复范围、兼容性与验证见 `docs/current/MAIN_REVIEW_FIXES.md`。

### Stage5A 实验分支
固定编号VLCC状态机、整数桶运输执行、船队规模扫描与未校准定价见 `current/STAGE5A_PHYSICAL_MARKET.md`。不包含成本或公司模型；不修改现有服务。

### Stage5A v0.2（当前实验分支，优先于旧Stage5A描述）
固定10运营日/回合，日货量投影至360日标签年；真实欠运完整守恒，报价改用有界短记忆信号与软上下界。旧主干定价核保留不改。详情见 `current/STAGE5A_FIXED10_BOUNDED_PRESSURE.md`。不含成本、需求破坏或增减船。


### Stage6A 全球物理契约（独立分支）
新增三种原油轮、25格航线/船型先验、载货与空船计划、50项定向空船选择和只读分船型工作量。集合地理明确禁止直接航行；不实现全球船池、成本或新运价。见 `current/STAGE6A_GLOBAL_SHIPPING_CONTRACT.md`。
