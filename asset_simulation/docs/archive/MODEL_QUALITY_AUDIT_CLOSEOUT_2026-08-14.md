# 模型质量审计收口实施报告

> 状态：历史归档，不是当前实现权威  
> 执行者：Cursor / Grok  
> 日期：2026-08-14  
> 范围：扩展 Goal C 审计覆盖、阈值分层、三段 700 Seed 复算、事实文档收口  
> 当前事实去向：`docs/current/MODEL_QUALITY_AUDIT.md`

## 0. 结论

- 审计总状态 **`warning`**，`failure_count = 0`，漂移 **`pass`**。
- 真过程发现是产出缺口、全球 IG／HY 下沿、全球潜在增速轨道的经常命中；中国 `terminal_potential_growth_pct` 未消费。这些都没有改公式。
- Reserved：北美 MBS 15% 地板、跨 Seed 路径同一、QE／QT 全零。字段保留，不加随机项。
- 未改契约 JSON、模型公式、API、Viewer 布局、油价或权益校准。
- 日常 unittest 与 `node --check` 见第 4 节。

## 1. 实际读取

Glob／Grep 命中不记为完整读取。本收口在评审会话之后继续，下列以本实施轮次的 Read 为准。

### 完整读取

| 路径 | 用途 |
|---|---|
| `audit_volatility.py` | 扩展 Goal C 分段、阈值、Reserved／结构诊断 |
| `tests/test_volatility_audit.py` | 合成 warning／fail 分层；确认 700 Seed 不进 unittest |
| `docs/INDEX.md` | 加入正式审计文档 |
| `docs/components/GLOBAL_ORDINARY_CYCLE.md` | 修正与区域／G17 冲突的陈旧产品限制段 |

### 部分读取

| 路径 | 范围 |
|---|---|
| `docs/MODEL_CONTEXT_GUIDE.md` | 前 80 行：入场与验收 |
| `docs/current/RUNTIME_ARCHITECTURE.md` | 产品边界、时序、质量门禁段 |
| `docs/current/CONTRACTS_AND_UNITS.md` | 已注册契约与单位 |
| `docs/current/G17_LITE.md` | 校准与验收段 |
| `docs/archive/MACRO_VOLATILITY_GOAL_C_GROK_REPORT_2026-08-14.md` | 前 80 行：报告体例 |
| `model/contracts.py` | `validate_region_extension`：不读 `g17_ports_status` |
| `contracts/regional_macro_extension_v2.json` | 末段 `g17_ports_status`／`future_owner` |
| `model/china_components/real_prices.py` | 潜在增速公式，确认不读 terminal 键 |
| `model/north_america.py` | MBS `clamp(0.985 * previous, 3.0, 8.0)`、`qe_qt = 0.0` |
| `tests/test_service_viewer.py` | INDEX 必存在路径含 `current/MODEL_QUALITY_AUDIT.md` |
| `config/china_mainland_v0.3.json` | Grep：`terminal_potential_growth_pct`、bounds |
| `config/north_america_v0.6.json`、`config/global_macro_v0.6.json` | Grep：gap／IG／HY／潜在增速／政策 bounds |

### 失败读取

无。

### 本轮未完整重读（以代码／Grep 核对，不冒充全文）

`model/engine.py`、`model/world.py`、`model/g17_lite.py`、`model/registry.py`、`model/china_mainland.py`、四个契约全文、Viewer `app.js`。版本与单一 World 入口沿用已冻结基线：全球 v0.6、北美 v0.6、中国 v0.3、G17／World v0.2。

## 2. 实际修改

未触碰仓库中与本任务无关的用户改动。未改 `model/` 公式、`config/` 数值、`contracts/*.json`、`server.py`、Viewer 卡片。

| 文件 | 原因 |
|---|---|
| `audit_volatility.py` | 产出缺口／IG／HY／潜在增速 bounds 与边沿；MBS／QE／中国终端潜在增速诊断；饱和度 10% 失败线 vs 通胀 0.5% 线；info／warning／fail 分层 |
| `tests/test_volatility_audit.py` | 合成测试：缺口 warning、HY 0.51% warning、HY 10.1% fail、MBS 15% 不 fail、未使用配置键 warning |
| `docs/components/GLOBAL_ORDINARY_CYCLE.md` | §6 不再声称区域／G17 未接通；RoW 仍是残差；指向质量审计 |
| `docs/current/RUNTIME_ARCHITECTURE.md` | 质量段指向 `MODEL_QUALITY_AUDIT.md`（本轮前已改） |
| `docs/INDEX.md` | 正式文档表加入 `current/MODEL_QUALITY_AUDIT.md`；归档表加入本报告 |
| `docs/current/MODEL_QUALITY_AUDIT.md` | 可复算命令、阈值、三段命中与解释 |
| `docs/archive/MODEL_QUALITY_AUDIT_CLOSEOUT_2026-08-14.md` | 本报告 |
| `tests/test_service_viewer.py` | INDEX 必存在路径（本轮前已加入 `MODEL_QUALITY_AUDIT.md`） |

**明确未改：** `contracts/regional_macro_extension_v2.json`（`g17_ports_status` 进入 `region_contract_hash`）；中国潜在增速公式与 `terminal_potential_growth_pct`；油价、权益、汇率、债券；Viewer 重排。

## 3. 三段审计

```powershell
cd C:\d_e\oiltanker\airport
py -3.13 -m asset_simulation.audit_volatility --profile goal-c --years 60 --output $env:TEMP\asset_simulation_goal_c_audit.json
```

原始 JSON：`C:\Users\sanuri\AppData\Local\Temp\asset_simulation_goal_c_audit.json`，约 125 KB，**不进仓库**。约 66 秒。

| 段 | Seed | 状态 | fail | 要点 |
|---|---|---|---:|---|
| 校准 | 0–399 | warning | 0 | 缺口 3.50%/2.76%/2.41%；全球 HY 下沿 2.01%；MBS 15% |
| 验证 | 400–499 | warning | 0 | 全球潜在增速 3.73%；北美政策下沿恰好 0.50%（warning 不是 fail） |
| 留出 | 500–699 | warning | 0 | 中国核心通胀下沿 0.108% 刚过解释线 |
| 漂移 | — | pass | 0 | 均值最大差 0.084pp |

合计 `warning_count = 40`（三段门禁相加，含每段重复的 MBS／未使用键），`info_count = 6`（每段 MBS 路径同一 + QE 全零）。

## 4. 测试

从 `C:\d_e\oiltanker\airport`：

```powershell
py -3.13 -m unittest discover -s asset_simulation/tests -v
node --check asset_simulation/viewer/static/js/app.js
```

结果：`Ran 32 tests in 4.183s`，`OK`。`node --check` 退出码 0。`tests/test_volatility_audit.py` 单独 4 项约 0.14 秒 OK。700 Seed 不在 unittest 内。

## 5. 不同意见

1. 评审曾把 0.5% 过程失败线套到新覆盖的缺口／IG／HY／潜在增速，700 Seed 会变成 `fail`。Codex 分层是：恒等式／非有限值／错误契约才硬失败；周期轨道不是通胀剪裁。实施改为 ≥0.1% warning、>10% 才 saturation fail。这是对 Codex 第 C 条的执行，不是放宽守恒。
2. 认同 Codex：平静世界约 8/10；权益 2%–3% 年波动是复利参考中枢；名义油价随 CPI 上涨不改公式；RoW 薄层不补建；Reserved 字段不删不加噪；Viewer 本轮不重排。
3. `g17_ports_status` 文案已与默认 World 冲突，但改 JSON 会改 `region_contract_hash`。只记待迁移，不改契约。

## 6. 未解决项

- 中国 `terminal_potential_growth_pct`：消费或在正式配置迁移中删除；本轮只记录。
- 区域契约 `g17_ports_status`／`future_owner`：等版本化契约迁移。
- 产出缺口与全球信用下沿是否在未来版本放宽或降惯性：超出本轮，禁止借审计改参。
- Viewer 默认减法与中文命名、完整市场层、新区域、特殊事件：明确不做。
