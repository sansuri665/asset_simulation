# 当前运行架构

> 基线：全球宏观 v0.8.1，原油海运 v0.6.9
> 最近核对：2026-09-03

## 两个时间尺度

年度全球宏观顺序保持：

```text
real_economy → inflation_nominal → rates → funding_credit
→ oil_commodity（年度价格锚）→ asset_reference
```

`oil_price_projection` 在年度原油开收盘与年度波动包络内生成12个月度 OHLC，只用于价格观察。它不参与年度求解，也不回写物理原油、航运需求或通胀。

海运需求世界只读取最近已经完整结账的宏观行，每年生成12个月度状态。总液体池负责宏观能源周期，独立 crude-only 层负责油轮可运输的物理原油，随后才运行区域平衡与航线分配：

```text
年度增长与价格背景
→ 总液体需求、产能、产量与库存守恒
→ 独立原油产能、产量、炼厂原油加工与原油库存守恒
→ 十个区域的原油产量／炼厂原油加工／原油库存／原油管道平衡
→ 区域海运盈余与缺口
→ 受行列边际约束的5×5贸易矩阵
→ 十四条航线状况参考 + 十一格其他航线池
→ 航线有效航程
→ 航线吨海里加总
```

除初始2025状态外，每年月度模型的 `macro_information_year = year - 1`。产能读取最多三个已完成年度的实际油价指数，不读取正在运行年份或未来年度行。API 只公开所选月份及以前的历史。

## 所有权

| 状态 | Owner |
|---|---|
| 年度名义／实际油价 | `oil_commodity.py` |
| 月度原油价格观察路径 | `oil_price_projection.py` |
| 月度总液体需求、产能、产量、库存 | `oil_physical_world.py` |
| 月度原油产能、产量、炼厂加工、原油库存 | `crude_physical_world.py` |
| 贸易改道环境 | `oil_shipping_demand.py` |
| 区域物理分解与海运净平衡 | `oil_shipping_regions.py` |
| 受约束航线、有效航程与吨海里 | `oil_shipping_routes.py` |
| 时间循环、年度聚合、身份和截点 | `oil_shipping_world.py` |

当前区域与航线货量是海运贸易需求，尚未经过港口装船队列；吨海里是运力需求，不是运价。没有任何模块预生成未来运价曲线。

测试可通过 `scenario_by_turn` 注入供应、区域或航程扰动；服务和 Viewer 不接受或发布场景参数，场景也不回写油价世界。

## API 与缓存

- `/api/health`
- `/api/global`
- `/api/oil-price`
- `/api/oil-shipping`

年度世界使用服务内有界缓存；油运世界使用上游 identity 和普通参数构成的确定性投影缓存。服务重启后可由 Seed 完整重建。
