# Asset Simulation 新模型入场指引

> 状态：当前入场流程  
> 权威范围：阅读路由、证据纪律和上下文就绪判定  
> 实现基线：全球 v0.8.1、宏观 UI v5.41  
> 最近核对：2026-08-25

唯一模型根目录是仓库根下的 `asset_simulation/` Python 包。代码与 JSON 契约优先于文档；归档和未来设计不能冒充当前事实。

## 1. 首次进入的最低证据集

按顺序完整读取：

1. `CLAUDE.md`
2. `docs/INDEX.md`
3. `docs/current/RUNTIME_ARCHITECTURE.md`
4. `docs/current/CONTRACTS_AND_UNITS.md`
5. `model/engine.py`
6. `model/funding_credit.py`
7. `model/impulses.py`
8. `contracts/global_macro_minimum_v3.json`
9. `model/registry.py`

Viewer 任务再完整读取 `server.py`、相关 HTML/CSS、`viewer/static/js/app.js` 和 `viewer/static/js/game.js` 到 EOF。工具截断时必须续读；`limit: 100` 只能报告为“前 100 行”。

## 2. 按任务追加 owner

| 任务 | 文档 | Owner |
|---|---|---|
| GDP / 普通周期 | `components/GLOBAL_ORDINARY_CYCLE.md` | `model/real_economy.py` |
| 通胀 / 名义 GDP | Contracts | `model/inflation_nominal.py` |
| 政策 / 曲线 | Runtime | `model/rates.py` |
| 资金 / 信用 | Runtime + Contracts | `model/funding_credit.py` |
| 石油 / 商品 | Contracts + ADR-002 + `current/COMMODITY_LAYER.md` | `model/oil_commodity.py`、`model/commodity_overlay.py`、`model/oil_futures_overlay.py`、两份 overlay 配置 |
| 原油短期预测 | `current/OIL_SHORT_TERM_FORECAST.md` + Commodity Layer | `model/oil_short_term_forecast.py`、`model/oil_forecast_research_profile.py`、`config/oil_short_term_forecast_v0.2.json`、`contracts/oil_short_term_forecast_v2.json` |
| 原油策略研究 | `current/OIL_TRADING_STRATEGY.md` + Oil Short-Term Forecast + Commodity Layer | `model/oil_strategy_research.py`、`config/oil_strategy_research_v0.2.json`、`contracts/oil_strategy_research_v2.json` |
| 原油交易部 | `design/OIL_EXECUTION_DESK_ARCHITECTURE.md` + Oil Trading Strategy | `model/oil_execution_desk.py`、`config/oil_execution_desk_v0.1.json`、`contracts/oil_execution_desk_v1.json` |
| 策略风控审阅 | `current/CORPORATE_RISK_CONTROL.md` + Oil Trading Strategy | `model/oil_strategy_risk.py`、`config/oil_strategy_risk_v0.1.json`、`contracts/oil_strategy_risk_v1.json` |
| 公司级风控部 | `current/CORPORATE_RISK_CONTROL.md` + Oil Trading Strategy | `model/corporate_risk_control.py`、`config/corporate_risk_control_v0.2.json`、`contracts/corporate_risk_control_v2.json` |
| 投资决策竞技 | `current/OIL_INVESTMENT_COMPETITION.md` + Oil Trading Strategy | `model/oil_investment_competition.py`、`server.py`、游戏前端 |
| 原油基础交易策略 | `current/OIL_TRADING_STRATEGY.md` + Oil Short-Term Forecast + Commodity Layer | 策略研究、观点失效、策略／公司风控、投委会代理与交易部 owner + `model/oil_trading_strategy.py`、`model/oil_strategy_thesis.py`、`config/oil_trading_strategy_v1.1.json`、`contracts/oil_trading_strategy_v8.json` |
| 原油完整回合交易 | Oil Investment Competition + Oil Trading Strategy | 玩家与三家 AI 的随机任命、账户、排行榜和回合报告已运行；正式持久化和可操作投委会仍是未来设计 |
| 资产参考 | Contracts + ADR-002 | `model/asset_reference.py` |
| 资本市场 | `design/FUTURE_LAYERS.md` + ADR-006 | 未注册契约 `capital_market_minimum_v1` |
| Viewer | `current/VIEWER_PROJECTION.md` | `server.py`、完整前端 |

## 3. 必须复核的实现锚点

- 全球 `asset-simulation-global-macro-v0.8.1`。没有正式区域、G17 或 Coupled World。
- 全球契约 `global_macro_minimum_v3` 有 26 个 A 级字段、总计 42 项。
- 全球同年顺序为 `real_economy.step → inflation_nominal.step → rates.step → funding_credit.step → oil_commodity.step → asset_reference.step`。
- 全球美元代理不是真实 DXY，也不是汇率。产品只有一个计价单位。
- 六个宏观特殊事件端口仍默认为零。资本市场契约未注册。不进入宏观端口。
- 主 Viewer 请求 `/api/global`。`/game` 同时请求当前上／下半月的 `/api/oil-futures` 和 `/api/oil-investment-competition`；前者列出4个01／05／09合约和只读主连，后者返回四机构账户与报告。两者都不把未来公开行情交给前端，也不改变全球42项快照。没有银行行业或命名个股。
- 原油短期预测 API 仍可独立调用，但游戏行情页已撤下预测按钮。竞技会话在后台为每家机构生成当前主力／下一主力预测，并把它交给策略、风控和交易链；预测能力分仍不直接进入仓位公式。
- `oil_strategy_research` 按 Seed 生成八维无总分负责人，`corporate_risk_control` 审批目标，`oil_execution_desk` 执行同一批订单。`oil_investment_competition` 为玩家和三家 AI 各随机抽取四个岗位并逐半月结算；界面展示任命、排名和历史报告。任命仍只读、账户只在进程内重放，所有机构都不写回市场。
- 当前 UI 是功能性 Demo，不得把“入口和报告已经存在”写成“游戏界面已经完成”。现金／保证金、追保和强平已由正式账户 owner 运行；持久存档、玩家任命、投委会操作、委托、多策略和长期平衡仍未实现。

## 4. 证据纪律

- 记录每次 Read 的真实路径、完整／部分／失败状态；失败的 Read 也必须在报告中列出。
- Glob/Grep 命中、文件名推断和文档描述都不等于完整读取 owner。
- 不得为证明理解而修改代码，也不得用测试或其它工具绕过权限拒绝。
- 风险必须写成“触发条件 → 当前机制 → 可能影响”；历史问题或泛泛的“改错会出错”不算当前风险。

## 5. 上下文就绪报告

首次进入时，在不修改文件的前提下报告：

1. 完整、部分和失败读取的文件清单；
2. 全球同年前馈与一年滞后反馈；
3. GDP、CPI、能源、权益、债券口径；
4. 事件六端口及 owner；
5. 单一全球 API／缓存；
6. 当前代码风险、是否可独立修改、还需补读哪些 owner。

最低证据集未读完、用文档代替 owner、遗漏失败调用或时序／单位互相矛盾时，不得宣称“可独立实施变更”。

## 6. 修改后的最低验收

```powershell
py -3.13 -m unittest discover -s asset_simulation/tests -v
node --check asset_simulation/viewer/static/js/app.js
node --check asset_simulation/viewer/static/js/game.js
```

公式／配置还需确定性、前缀、方向性和三段 Seed 审计；Viewer 变更需检查真实页面、控制台、窄屏和 pointer 年份。
