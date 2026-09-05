# Asset Simulation

一个按 Seed 确定性重放的全球宏观、总液体物理平衡、原油物理平衡与原油航运需求模拟器。

项目已经从原油期货和机构交易 Demo 收缩为油运周期底座。当前完成到阶段4：宏观总液体池与 crude-only 物理层分离，区域原油平衡、航线网络和吨海里需求均由后者驱动；Viewer 已把这条需求链做成可逐月回看的界面。尚未实现港口装船队列、船队、运价、造船订单或船东账户。

```text
年度全球宏观、总液体周期与原油价格锚
        ├→ 原油月度价格路径（只读展示）
        ↓
月度总液体需求、产能、产量和库存
        ↓ 宏观增长与价格背景
独立的原油产量、炼厂原油加工量、产能和原油库存
        ↓
十个区域的原油产量／炼厂原油加工／原油库存／原油管道平衡
        ↓
区域海运盈余与缺口
        ↓
十四条主要航线 + 其他航线池
        ↓
各航线原油吨数 × 各自有效航程
        ↓
吨海里需求
```

## 当前能力

- 2025年开始、每个月一个回合；
- 相互独立的总液体物理池和 crude-only 物理池；
- 两个物理池每回合各自严格满足库存数量守恒；
- 十个贸易盆地的原油生产、炼厂原油加工、原油库存、原油管道与海运净平衡；
- 十四条显式主要航线、一个其他航线池及区域进出口；
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
- `/physical`：月度总液体供需、库存与产能
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

## Main 审查修复（2026-09-05）

本轮已纳入 main，新增独立单航线定价库，但没有船队或公司层。
需求参数、区域角色、14条主要航线和参考距离保持不变。

- `/api/global` 与 `/api/oil-price` 是全路径研究接口，不能直接提供给玩家/AI。
- `/api/decision?seed=42&years=60&year=2030&month=1` 是月末已知信息快照：
  只提供允许的已完成宏观和当前运输字段；由年度锚插值的油价月线只发布初始年或已完成年度，不带未知年末锚或全路径哈希。
- `years=20` 表示初始年后20次年度转移；月度世界实际覆盖2025–2045年，共21个日历年。
- 定价验证的供给适配器滞后每日货量，并按当前回合日数转换；不代表真实船池。
- 报价、运输匹配、成本结算分离。零供给可以有指示价格，但没有执行价格或收入。

详情见 `asset_simulation/docs/current/MAIN_REVIEW_FIXES.md`。
