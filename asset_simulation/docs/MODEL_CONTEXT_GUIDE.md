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


### 2026-09-05 候选增补（优先于上述旧进度描述）
新增独立 `single_route_pricing` v0.2.1；它只读供需和计划库存偏离并输出TCE，不属于已实现的船队。
定价不接入现有Viewer，也不反写原油需求。研究接口保留；新的 `/api/decision` 只发布月末可见字段。
详细修复范围、兼容性与验证见 `docs/current/MAIN_REVIEW_FIXES.md`。
