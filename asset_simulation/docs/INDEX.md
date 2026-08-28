# Asset Simulation 文档索引

> 状态：当前权威索引  
> 适用基线：全球 v0.8.1、服务／Viewer v5.41  
> 最近核对：2026-08-25  
> 路径基准：仓库根下的 `asset_simulation/` Python 包

本文只负责文档路由。Python、JavaScript、注册配置和 JSON 契约是运行事实的最终来源。

## 权威层级

| 优先级 | 位置 | 语义 |
|---:|---|---|
| 1 | 代码与注册契约 | 当前实际行为 |
| 2 | `docs/current/` | 已运行事实、已验证边界和已知限制 |
| 3 | `docs/decisions/` | 已接受或已被取代的架构决策 |
| 4 | `docs/components/` | 已运行组件的机制说明 |
| 5 | `docs/design/` | 尚未完成的路线；不得冒充运行事实 |
| 6 | `docs/archive/` | 历史任务、旧模型和退役实现 |

`CLAUDE.md` 是自动入口，`MODEL_CONTEXT_GUIDE.md` 是阅读纪律，`README.md` 是产品概览。

## 当前运行事实

| 文档 | 权威范围 | 主要 owner |
|---|---|---|
| [`current/PROJECT_STATUS.md`](current/PROJECT_STATUS.md) | 可分享的完整现状、完成度、短板和路线 | 当前 owner 汇总 |
| [`current/RUNTIME_ARCHITECTURE.md`](current/RUNTIME_ARCHITECTURE.md) | 年度／半月时序、缓存、状态和回写边界 | `model/engine.py`、`server.py` |
| [`current/CONTRACTS_AND_UNITS.md`](current/CONTRACTS_AND_UNITS.md) | 注册契约、单位、符号与可执行边界 | `contracts/`、`model/registry.py` |
| [`current/VIEWER_PROJECTION.md`](current/VIEWER_PROJECTION.md) | 主 Viewer、游戏页、URL、交互和 UI 技术债 | `viewer/`、`server.py` |
| [`current/COMMODITY_LAYER.md`](current/COMMODITY_LAYER.md) | 商品 overlay、原油现货、01／05／09期货和流动性 | `model/commodity_overlay.py`、`model/oil_futures_overlay.py` |
| [`current/OIL_SHORT_TERM_FORECAST.md`](current/OIL_SHORT_TERM_FORECAST.md) | 双合约预测、能力、修订和评分 | `model/oil_short_term_forecast.py` |
| [`current/OIL_TRADING_STRATEGY.md`](current/OIL_TRADING_STRATEGY.md) | 策略研究、目标仓位、换手、成本与结算 | `model/oil_strategy_research.py`、`model/oil_trading_strategy.py` |
| [`current/CORPORATE_RISK_CONTROL.md`](current/CORPORATE_RISK_CONTROL.md) | 公司 CRO、审批、回撤状态与报告 | `model/corporate_risk_control.py` |
| [`current/OIL_INVESTMENT_COMPETITION.md`](current/OIL_INVESTMENT_COMPETITION.md) | 玩家与3家 AI 的随机任命、账户、排名和回合报告 | `model/oil_investment_competition.py` |
| [`current/INSTITUTION_ORGANIZATION.md`](current/INSTITUTION_ORGANIZATION.md) | 1000万美元自营资本、Investment Decision 治理层和五部门组织壳 | `model/institution_organization.py`、`config/institution_organization_v0.1.json` |
| [`current/OIL_FORMAL_ACCOUNT_AND_CALIBRATION.md`](current/OIL_FORMAL_ACCOUNT_AND_CALIBRATION.md) | 现金／保证金、追保强平、收益分布、现实锚点和校准门禁 | `model/oil_futures_account.py`、`audit_oil_formal_account_calibration.py` |
| [`current/MODEL_QUALITY_AUDIT.md`](current/MODEL_QUALITY_AUDIT.md) | 全球、期限结构、策略交叉和风控审计证据 | `audit_*.py` |
| [`components/GLOBAL_ORDINARY_CYCLE.md`](components/GLOBAL_ORDINARY_CYCLE.md) | 无命名事件的全球普通周期 | `model/real_economy.py`、`model/impulses.py` |

## 当前未来设计

| 文档 | 当前有效部分 | 已运行部分 |
|---|---|---|
| [`design/FUTURE_LAYERS.md`](design/FUTURE_LAYERS.md) | 下一阶段优先级和市场／宏观边界 | 商品、原油交易竞技已越过早期设计阶段 |
| [`design/OIL_FUTURES_TURN_EXECUTION.md`](design/OIL_FUTURES_TURN_EXECUTION.md) | 投委会权限、多策略、动态组合保证金和存档扩展 | 目标仓位、容量、全成本、正式账户和多实体竞技已运行 |
| [`design/OIL_EXECUTION_DESK_ARCHITECTURE.md`](design/OIL_EXECUTION_DESK_ARCHITECTURE.md) | 独立执行指令、正式任命与长期 TCA | 六维能力、候选、成交磨损与随机任命已运行 |

`design/*_CARDS.md` 是已退役银行发行人系统的历史设计稿。它们不再有当前 owner，不得用于推断运行时；完整退役事实见 [`archive/ISSUER_ICBC_RETIRED_2026-08-20.md`](archive/ISSUER_ICBC_RETIRED_2026-08-20.md)。文件暂留原路径只为保存旧数字和链接。

## 架构决策

| ADR | 当前状态 | 核心结论 |
|---|---|---|
| [`ADR-001`](decisions/ADR-001-ANNUAL-SEQUENCING.md) | 有效 | 同年前馈，返回上游跨年 |
| [`ADR-002`](decisions/ADR-002-NOMINAL-REAL-PRICES.md) | 有效 | 名义主显示，实际口径审计 |
| [`ADR-003`](decisions/ADR-003-REGIONAL-ONE-WAY-AND-G17.md) | 已取代 | 历史区域／G17方案 |
| [`ADR-004`](decisions/ADR-004-SINGLE-COUPLED-WORLD.md) | 已取代 | 历史 Coupled World／RoW方案 |
| [`ADR-005`](decisions/ADR-005-MACRO-ENVIRONMENT-AND-CAPITAL-MARKETS.md) | 部分有效 | 资本市场另建 owner、宏观事件 opt-in 仍有效 |
| [`ADR-006`](decisions/ADR-006-SINGLE-GLOBAL-FEDERATION.md) | 有效 | 单一全球宏观、无汇率和分区；服务清单按 v5.41修订 |

## 按任务读取

| 任务 | 最低文档 | 最终 owner |
|---|---|---|
| 全球组件 | Runtime + Contracts + Ordinary Cycle | `model/engine.py` 与对应组件 |
| Viewer／API | Viewer Projection + Runtime | `server.py`、HTML、CSS、完整 JS |
| 商品／原油期货 | Commodity Layer + Contracts | 商品与期货 overlay、配置和契约 |
| 预测研究 | Oil Short-Term Forecast | 预测模型、配置和契约 |
| 策略／换手／成本 | Oil Trading Strategy | 策略研究、交易策略、配置和契约 |
| 公司风控 | Corporate Risk Control | 公司风控模型、配置和契约 |
| 四机构竞技 | Oil Investment Competition + 上述四层 | 竞技会话、服务和游戏前端 |
| 正式账户／收益尺度 | Oil Formal Account and Calibration + Contracts | 正式账户、校准器、配置和契约 |
| 长期产品路线 | Future Layers + 两份原油设计 | 只允许报告为未来设计 |

## 历史归档

`archive/` 中的区域、G17、银行行业、命名发行人、旧 Viewer 和旧质量 Goal 均不是当前实现。归档文件保留当时日期和版本，不随当前代码机械改写。
