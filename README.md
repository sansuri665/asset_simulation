# Asset Simulation

一个按 Seed 确定性重放的全球宏观、原油期货与机构投资决策模拟器。

当前版本从 2025—2029 年观察期开始，在 2030 年进入游戏运行阶段，以半个月为一个回合推进至 2085 年。玩家机构与三家 AI 共享同一条不可变市场路径，分别运行预测、策略研究、策略／公司风控、交易执行和正式期货账户，并生成逐回合权益排名与报告。

## 当前能力

- 全球年度宏观、商品与原油现货路径；
- 原油主连及 `01 / 05 / 09` 月份合约的年、月、周 K 线；
- 四家机构的研究、策略、风控和交易团队随机任命；
- 现金、保证金、盯市、追保、强制减仓、利息与融资账本；
- 手续费、价差、滑点、容量衰减和交易执行差异；
- 同 Seed 可复算的竞技排名与历史回合报告。

这是开发中的单品种 Demo。玩家自主投委会、多策略组合、持久存档、委托级撮合和正式游戏 UI 尚未完成。

## 启动

需要 Python 3.11 或更高版本；运行时只使用 Python 标准库。

```powershell
git clone https://github.com/sansuri665/asset_simulation.git
cd asset_simulation
py -3 -m asset_simulation.server
```

也可以在 Windows 中运行 `start_ui.bat`。默认地址：

- 主界面：<http://127.0.0.1:8783/?seed=42&years=60>
- 游戏界面：<http://127.0.0.1:8783/game?seed=42>

## 验收

```powershell
py -3 -m unittest discover -s asset_simulation/tests -v
node --check asset_simulation/viewer/static/js/app.js
node --check asset_simulation/viewer/static/js/game.js
```

当前基线为 89 项 Python 测试通过。Node.js 只用于前端 JavaScript 语法检查，不是运行服务的依赖。

## 项目结构

```text
asset_simulation/
├─ asset_simulation/
│  ├─ model/       # 宏观、原油、预测、策略、风控、执行和账户模型
│  ├─ config/      # 注册配置
│  ├─ contracts/   # JSON 字段契约
│  ├─ viewer/      # 本地 Web 界面
│  ├─ tests/       # 确定性、契约、审计和服务测试
│  └─ docs/        # 当前事实、设计路线与历史归档
├─ pyproject.toml
└─ start_ui.bat
```

详细现状与模型边界见 [`asset_simulation/docs/README.md`](asset_simulation/docs/README.md) 和 [`asset_simulation/docs/MODEL_CONTEXT_GUIDE.md`](asset_simulation/docs/MODEL_CONTEXT_GUIDE.md)。

