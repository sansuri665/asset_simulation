# Asset Simulation 文档索引

> 基线：全球宏观 v0.8.1，原油海运 v0.6.8，UI/API v0.7
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
