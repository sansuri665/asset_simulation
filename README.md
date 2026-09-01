# Asset Simulation

一个按 Seed 确定性重放的全球宏观、物理原油平衡与原油航运需求模拟器。

项目已经从原油期货和机构交易 Demo 收缩为油运周期底座。当前完成到阶段3：全球原油物理池、区域物理平衡、航线网络和吨海里需求；Viewer 已把这条需求链做成可逐月回看的界面。尚未实现船队、运价、造船订单或船东账户。

```text
年度全球宏观与原油价格锚
        ├→ 原油月度价格路径（只读展示）
        ↓
月度原油需求、产能、产量和库存
        ↓
十个区域的产量／原油当量需求／库存／管道平衡
        ↓
区域海运盈余与缺口
        ↓
九条主要航线 + 其他航线池
        ↓
各航线原油吨数 × 各自有效航程
        ↓
吨海里需求
```

## 当前能力

- 2025年开始、每个月一个回合；
- 以百万桶/日和百万桶结算的物理原油平衡；
- 每回合严格满足库存数量守恒；
- 十个贸易盆地的生产、原油当量需求、库存、管道与海运净平衡；
- 九条显式主要航线、一个其他航线池及区域进出口；
- 严格守恒的海运货量、加权平均航程和吨海里；
- 年度原油价格锚及连续的月度 OHLC 路径；
- 仅供测试的供应、区域平衡和贸易改道注入端口，不进入正常游戏或 UI；
- 同 Seed 确定性、长短窗口前缀一致；
- API 截点只发布当前及以前的月度数据。

## 启动

需要 Python 3.11 或更高版本，运行时只使用标准库。

```powershell
py -3 -m asset_simulation.server
```

也可以直接运行 `start_ui.bat` 或 `start_ui.ps1`。默认界面地址：<http://127.0.0.1:8783/>。

- `/`：全球宏观和原油价格，单图切换展示
- `/physical`：月度原油供需、库存与产能
- `/shipping`：海运总览、区域物理平衡与主要航线
- `/api`：服务与端点说明
- `/api/health`
- `/api/global?seed=42&years=60`
- `/api/oil-price?seed=42&years=60`
- `/api/oil-shipping?seed=42&years=60&year=2030&month=1`

## 验收

```powershell
py -3 -m unittest discover -s asset_simulation/tests -v
py -3 -m asset_simulation.audit_oil_shipping_demand --seeds 0,1,2,3,4,5,6,7 --years 60
```

模型边界和字段单位见 [`asset_simulation/docs/current/OIL_SHIPPING_DEMAND.md`](asset_simulation/docs/current/OIL_SHIPPING_DEMAND.md)。
