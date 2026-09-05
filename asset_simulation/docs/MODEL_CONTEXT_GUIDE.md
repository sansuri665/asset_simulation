# 新模型入场指引

## 最低证据集

按顺序完整读取：

1. `CLAUDE.md`
2. `docs/INDEX.md`
3. `docs/current/RUNTIME_ARCHITECTURE.md`
4. `docs/current/CONTRACTS_AND_UNITS.md`
5. `model/engine.py`
6. `model/oil_commodity.py`
7. `model/oil_price_projection.py`
8. `model/oil_physical_world.py`
9. `model/crude_physical_world.py`
10. `model/oil_shipping_demand.py`
11. `model/oil_shipping_regions.py`
12. `model/oil_shipping_routes.py`
13. `model/oil_shipping_world.py`
14. `config/oil_shipping_demand_v0.6.json`
15. `contracts/oil_shipping_demand_v6.json`
16. `model/registry.py`

API 任务另读完整 `server.py`。全球公式任务按 `docs/components/GLOBAL_ORDINARY_CYCLE.md` 追加对应宏观 owner。

## 当前实现锚点

- 唯一宏观入口是 `run_global_macro`；
- 唯一油运入口是 `run_oil_shipping_world`；
- 2025年起每年12个月度回合；
- 全球年度油价仍由 `oil_commodity` 产生；
- 原油月线只由 `oil_price_projection` 在年度锚之间展开且不回写；
- 总液体月度物理池不读取旧供需指数；
- crude-only 物理层读取宏观增长与滞后价格背景，但独立形成原油产量、炼厂原油加工量、原油产能和原油库存；
- 全球海运货量只由 crude-only 区域物理盈余和缺口生成，不使用总液体消费代理或固定海运份额；
- 场景扰动只用于测试，不由 API、Viewer 或正常游戏暴露，也不回写油价；
- API `/api/oil-shipping` 强制截点；
- 当前没有运价、船队、船厂、公司或正式游戏 UI；只读需求链 Viewer 已存在。

## 修改后的报告纪律

必须区分：

- 年度价格信号与月度物理桶数；
- 总液体消费、炼厂原油加工量与海运原油货量；
- 货物吨数与吨海里；
- 已实现的需求侧与尚未实现的船队／运价侧。


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
