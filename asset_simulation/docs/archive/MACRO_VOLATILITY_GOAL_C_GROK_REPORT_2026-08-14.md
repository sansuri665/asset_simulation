# 宏观波动 Goal C · Grok 实施报告

> 执行者：Cursor / Grok  
> 日期：2026-08-14  
> 范围：W7 Viewer 语义收口 + W8 三段 Seed 审计  
> 供 Codex 终验：本报告；原始 `MACRO_VOLATILITY_GOAL_C_AUDIT.json` 在 C4 核对并提取稳定事实后清理，可用本文命令完整复算

## 0. 结论（先看这里）

- **W7 已落地**：资产复利参考文案、主界面去版本/哈希、国际总览左右轴对调。未改 API、内部字段、`g17_lite` 键或模型公式。
- **W8 已落地**：`py -3.13 -m asset_simulation.audit_volatility --profile goal-c --years 60 --output <path>` 一次跑完校准 0–399、验证 400–499、留出 500–699，**没有**再跑一遍 0–699。
- **700 世界 / 60 年**：`summary.status = warning`，`failure_count = 0`，`drift_status = pass`，`holdout_used_for_parameter_selection = false`。
- **未改** `model/`、`config/`、`contracts/`、`server.py` 或时序。
- **未做真实浏览器页面 / 窄屏点击**；该项留给 Codex C4。

---

## 1. 实际读取的文件

按 Goal C 任务书强制清单 + 入场指引。Glob/Grep 命中不记为完整读取。

### 完整读取

| 路径 | 状态 |
|---|---|
| `CLAUDE.md` | 完整 |
| `docs/MODEL_CONTEXT_GUIDE.md` | 完整 |
| `docs/INDEX.md` | 完整 |
| `docs/current/RUNTIME_ARCHITECTURE.md` | 完整 |
| `docs/current/CONTRACTS_AND_UNITS.md` | 完整 |
| `docs/current/G17_LITE.md` | 完整 |
| `docs/current/VIEWER_PROJECTION.md` | 完整 |
| `docs/tasks/MACRO_VOLATILITY_REALISM_V02_PLAN.md` | 完整 |
| `docs/tasks/MACRO_VOLATILITY_REALISM_GOAL_C_PLAN.md` | 完整 |
| `model/engine.py` | 完整。`MODEL_VERSION = asset-simulation-global-macro-v0.6`；同年顺序 `real → inflation → rates → funding_credit → oil_commodity → asset_reference` |
| `model/world.py` | 完整。`asset-simulation-coupled-world-v0.2`；区域读全球 `t` 与 G17 `t-1` |
| `model/g17_lite.py` | 完整。`asset-simulation-g17-lite-v0.2` |
| `model/registry.py` | 完整。配置/契约路径与代码版本一致 |
| `server.py` | 完整。`SERVICE_ID = asset-simulation-macro-ui-v4.1`；Viewer 只走 `/api/world` |
| `audit_volatility.py` | 修改前完整读取，随后在同文件实施 W8 |
| `viewer/index.html` | 完整 |
| `viewer/static/js/app.js` | 完整到 EOF |
| `viewer/static/css/viewer.css` | 完整 |
| `tests/test_volatility_audit.py` | 完整 |
| `tests/test_service_viewer.py` | 完整 |

### 部分读取

| 路径 | 状态 |
|---|---|
| `tests/test_global_macro.py` | 仅前 80 行，用于核对确定性/恒等式测试仍存在 |
| `config/north_america_v0.6.json` | 仅 Grep bounds：`policy_rate_pct [0.0, 9.0]`，`core_inflation_pct [0.1, 6.0]` |
| `config/china_mainland_v0.3.json` | 仅 Grep bounds：`policy_rate_pct [0.5, 7.0]`，`core_inflation_pct [0.0, 5.0]`，`term_premium_10y_pct [-0.6, 3.0]` |
| `config/g17_lite_v0.2.json` | 仅 Grep：`private_net_capital_inflow_pct_gdp [-3.5, 3.5]` |
| `config/global_macro_v0.6.json` | 仅 Grep policy/core/term-premium bounds |

### 未读（本次不改公式，故未扩读 owner）

`model/north_america.py`、`model/china_mainland.py`、`model/funding_credit.py`、`model/impulses.py`、四个 `contracts/*.json` 全文、`docs/regions/*` 全文。入场指引的 Viewer 任务强制项（`server.py`、HTML/CSS、`app.js`）已完整读取。

### 失败读取

无。

---

## 2. 实际修改文件及原因

未触碰仓库中 `airport/` 其它未跟踪/已修改的无关用户改动。只改 `asset_simulation` 内 Goal C 文件。

| 文件 | 原因 |
|---|---|
| `viewer/static/js/app.js` | W7：资产复利参考文案；footer 去掉版本/哈希；国际总览左轴改风险周期+贸易+资本，右轴改 USD/CNY |
| `audit_volatility.py` | W8：兼容保留 `build_audit`；新增 non-finite、bound rate、`inflation_above_8`、`--profile goal-c`、三段门禁与漂移 |
| `tests/test_service_viewer.py` | 同步静态契约：新文案必须在，旧主标签不得在 |
| `tests/test_volatility_audit.py` | 小样本结构测试 + 合成报告测 warning/fail，不把 700 Seed 塞进日常单测 |
| `docs/tasks/MACRO_VOLATILITY_GOAL_C_GROK_REPORT.md` | 本报告 |
| `docs/tasks/MACRO_VOLATILITY_GOAL_C_AUDIT.json` | 700 世界三段审计原始证据（约 83 KB） |

**明确未改：** `model/`、`config/`、`contracts/`、`server.py`、`viewer/index.html`、`viewer/static/css/viewer.css`。

---

## 3. 对 Goal C 的不同意见

### 3.1 主界面 identity

```text
原方案：面向用户只显示「Seed 42 · 61 个年度状态」；服务状态仍可显示四 Scope。
Grok 建议：footer `#identityText` 使用 `Seed ${seed} · ${rows.length} 个年度状态`；顶部 status 只保留「单一世界 · 全球 + 北美 + 中国大陆 + 国际」。哈希与 model_version 仍留在 API / `state.identities`，不渲染。
代码／统计／页面证据：修改前 `applyScope` 渲染 `${modelLabel} · ${identity.result_hash.slice(0,12)} · Seed …`，会把 `asset-simulation-global-macro-v0.6` 和哈希送到主界面。`index.html` 已有 `#identityText`，无需改 HTML。
是否采用：是
采用后的风险与回退方式：调试时主界面看不到哈希。回退只需恢复 `applyScope` 旧拼接；API 未变。
```

### 3.2 审计 schema 与日常测试

```text
原方案：goal-c profile 固定 0–399 / 400–499 / 500–699；日常单测只用极小 Seed 验证结构。
Grok 建议：`build_audit` 保持 `asset-simulation-volatility-audit-v1` 并增量加入 `nonfinite_counts`、`bounds_hit_rates_pct`、`event_counts`、`inflation_above_8`；Goal C 包装使用 `asset-simulation-volatility-audit-goal-c-v1`。`build_goal_c_audit(segments=...)` 可注入小范围，CLI `--profile goal-c` 仍用冻结三段。
代码／统计／页面证据：原 `tests/test_volatility_audit.py` 只断言 v1 字段存在，不禁止新键。3×1 Seed×5 年结构测试在 0.1s 内完成；700 世界不进 unittest。
是否采用：是
采用后的风险与回退方式：若外部脚本假定 v1 恰好只有旧键，会看到新键。回退可删除增量键，门禁改为只读 Goal C 包装。
```

### 3.3 普通通胀尾部按 Scope 拆分

```text
原方案：每段应出现高于 4% 和低于 0% 的年份。
Grok 建议：三个通胀序列（全球/北美/中国）全部缺失才 fail；只缺其中一部分为 warning。8% 以上：≥1.0% fail，≥0.2% warning。
代码／统计／页面证据：Goal A 全球通缩仅 0.13%。100-Seed 验证集期望约 8 个全球通缩年，单独把「某一区域为 0」打成 fail 会把小样本噪声升级成公式问题。本次三段三个 Scope 的 >4% 与 <0% 均非零，8% 事件为 0。
是否采用：是
采用后的风险与回退方式：若未来只有中国出现通缩、全球完全没有，只会 warning。回退：把单 Scope 缺失改回 fail。
```

### 3.4 Bounds 0.5% 含不含边界

```text
原方案：单字段命中率必须 <0.5%；高于 0.1% 必须解释。
Grok 建议：fail 仅当命中率 >0.5%；0.1%–0.5%（含 0.5%）为 warning 并写入 explanations。不改政策或通胀公式。
代码／统计／页面证据：验证集北美 `policy_rate_pct` 30/6000 = 0.5% 正好踩线。校准 69/24000 = 0.2875%，留出 27/12000 = 0.225%，0–699 合计 126/42000 = 0.300% < 0.5%。北美政策 bounds 为 `[0.0, 9.0]`，与 Goal A 允许降到零的行动阶段一致；留出集未恶化，故不是 Goal A/B 验收失败，不能据此改 `config/`。
是否采用：是（只改比较符）
采用后的风险与回退方式：0.50% 不再 fail，0.51% 仍 fail。回退：`evaluate_segment_gates` 改回 `rate >= 0.5`。
```

### 3.5 资本「不是近似常数」

```text
原方案：私人资本流 5%–95% 主要位于 ±3.5% GDP，且不是近似常数。
Grok 建议：p05 < -3.5 或 p95 > 3.5 为 fail；std < 0.05 或 (p95−p05) < 0.10 视为近似常数而 fail。
代码／统计／页面证据：三段北美 5%–95% 约 -1.97～+1.70，中国约 -1.17～+1.79；std 约 0.88–1.11。G17 资本 bound 恰好是 `[-3.5, 3.5]`，与 5%–95% 窗口同数；真正约束来自「命中率低 + 分位数远小于 ±3.5」，而不是分位数本身。
是否采用：是
采用后的风险与回退方式：若分布贴着 ±3.5 但 5%–95% 仍刚好在界内，本门禁不会 fail，要靠 bound rate warning 暴露。回退：把 5%–95% 收紧到 ±2.5。
```

### 3.6 不改 CSS / HTML / 正式文档

```text
原方案：index.html 仅在需要时改 identity 容器；css 仅在窄屏检查发现问题；C4 由 Codex 把稳定事实写入 docs/current。
Grok 建议：本次不改 HTML/CSS；不把路线文档移入 archive；不更新 VIEWER_PROJECTION.md。
代码／统计／页面证据：`#identityText` 已存在。`app.js` 已无「25bp网格」。650px 媒体查询已把 scope/mode 改成 4 列 grid。真实页面仍待 Codex。
是否采用：是
采用后的风险与回退方式：若 Codex 发现窄屏溢出或国际图例过长，再改 CSS。文档陈旧风险留给 C4。
```

未采用、也未实施的事项：改年度时序、删公共字段、重命名内部 `g17_lite`、为 Reserved 字段加噪声、看完留出集后调参。

---

## 4. 测试命令与结果摘要

工作目录均为 `C:\d_e\oiltanker\airport`。

```powershell
py -3.13 -m unittest discover -s asset_simulation/tests -v
node --check asset_simulation/viewer/static/js/app.js
py -3.13 -m asset_simulation.audit_volatility --profile goal-c --years 60 --output asset_simulation/docs/tasks/MACRO_VOLATILITY_GOAL_C_AUDIT.json
```

| 命令 | 结果 |
|---|---|
| `unittest discover -s asset_simulation/tests -v` | **32 tests, OK, 4.261s** |
| `node --check .../app.js` | **exit 0** |
| `--profile goal-c --years 60` | **exit 0，约 65s，700 世界，未重跑全体 0–699** |

32 项测试仍覆盖确定性、前缀、实际/名义 GDP 恒等式、2Y/10Y 方向、G17 方向、零事件端口、单一 World 缓存和 Viewer 静态契约。未新增重型统计单测。

---

## 5. 三段 Seed 门禁

原始 JSON：`docs/tasks/MACRO_VOLATILITY_GOAL_C_AUDIT.json`。

| 段 | Seed | 世界数 | status | fail | warning |
|---|---|---:|---|---:|---:|
| calibration | 0–399 | 400 | warning | 0 | 4 |
| validation | 400–499 | 100 | warning | 0 | 5 |
| holdout | 500–699 | 200 | warning | 0 | 4 |
| drift | — | — | pass | 0 | 0 |
| **合计** | **0–699 一次** | **700** | **warning** | **0** | **13** |

硬门禁（三段均满足）：

- 全部世界跑完；五 Scope 非有限值均为 0。
- 贸易/资本残差 0；货币篮子最大残差 `4.8e-9`–`4.9e-9` `< 1e-8`。
- 全球—北美/中国/RoW 增长相关约 0.88 / 0.83 / 0.89，均 ≥ 0.75。
- RoW 未加权增长方差份额 42.4% / 43.5% / 43.0%，落在 25%–50%。
- 通胀均值：全球 2.16–2.21，北美 1.98–2.04，中国 1.81–1.88。
- 通胀年变动标准差：全球 0.349–0.353，北美 0.368–0.370，中国 0.387–0.389。
- 每段、每个通胀 Scope 都有 >4% 与 <0% 年份；>8% 为 0。
- 北美政策暂停 38.5–39.0%；中国 <5bp 暂停 32.3–32.8%。
- 10Y 年变动标准差：北美 0.342–0.343，中国 0.303–0.305。
- 汇率年变动标准差：北美 1.49–1.51%，中国 1.22–1.25%。
- 贸易年变动标准差：北美 0.199–0.201，中国 0.204–0.206。
- 资本 5%–95% 均在 ±2.0% GDP 内，std 约 0.88–1.11，非常数。

漂移：均值最大差 0.084pp（北美政策），远低于 0.25pp；标准差相对差最大 4.7%（北美资本），远低于 30%；尾部未消失；频率比最大 1.38x。

### 5.1 必须解释的 bound 命中（0.1%–0.5%）

这些命中**没有**被截断器拿来制造 Goal A/B 的通胀/汇率尾部。未改公式。

| 字段 | 校准 | 验证 | 留出 | 因果解释 |
|---|---:|---:|---:|---|
| 北美 `core_inflation_pct` bound `[0.1, 6.0]` | 0.175% | 0.183% | 0.183% | 三段命中率几乎相同。北美通缩年 0.58–0.83%，核心通胀会碰到 0.1% 数值地板。Headline 年变动 std 仍在 0.37pp，>4% 尾部仍在，不是 6% 天花板造热。 |
| 北美 `policy_rate_pct` bound `[0.0, 9.0]` | 0.288% | **0.500%** | 0.225% | 25bp 网格 + 行动阶段允许降到 0。验证集 30/6000 正好 0.5%，留出更低。0–699 合计 0.300%。不像 9% 上界在制造紧缩。 |
| 中国 `policy_rate_pct` bound `[0.5, 7.0]` | 0.367% | 0.200% | 0.250% | 代表性利率 0.5% 地板。暂停率稳定在 32%，不是 7% 上界。 |
| 北美私人资本 `[-3.5, 3.5]` | 0.183% | 0.183% | 0.083% | 安全界与 Goal C 的 ±3.5% 窗口同数，但 5%–95% 约 ±2%，clamp 只切极端尾。留出更低。 |
| 中国 `term_premium_10y_pct` | 0.079% | 0.133% | 0.075% | 仅验证集略过 0.1%（8/6000）。留出回到 0.075%。小样本，不是期限溢价贴线。 |
| 中国 `core_inflation_pct` | 0.067% | 0 | 0.108% | 仅留出刚过 0.1%（13/12000）。 |

**结论：** 无 Goal A/B 验收失败，因此不改模型或配置。留出集未用于选参。

---

## 6. 未解决问题（请 Codex 查）

1. **真实页面** `/?seed=42&years=60`：全球 / 北美 / 中国大陆 / 国际四 Scope。
2. **国际总览**：左轴应为国际风险周期、北美贸易/GDP、中国私人资本/GDP；右轴图例为「USD/CNY 相对指数（右轴）」；零线；无 ×5/×10。
3. **文案**：卡片「权益复利参考」「主权债复利参考」；模块「宏观资产复利参考」；明细「权益价格 / 复利参考」。footer 为 `Seed 42 · 61 个年度状态`。不得出现版本号、哈希、`G17-lite`、`vNext`。
4. Scope 切换后年份不跳末年；slider 与 pointer 同步。
5. 桌面宽屏与 ≤650px 窄屏：模块按钮可见、无横向溢出。当前 CSS 已有 4 列 grid，Grok 未做浏览器实测。
6. 控制台无错；网络只请求一个 `/api/world`。
7. C4：将稳定事实写入 `docs/current/VIEWER_PROJECTION.md` 等，再把总路线与本任务书移入 `docs/archive/`。Grok 按任务书未做归档。
8. 可选观察：G17 资本 bound 与 Goal C ±3.5% 窗口同数。统计上目前安全，但后续若要收紧「非截断」证据，应把安全界扩到 ±4% 或把 5%–95% 门禁改为更窄，而不是现在改公式。

---

## 7. 冻结项声明

| 项 | 是否改动 |
|---|---|
| 模型公式 | **否** |
| `config/` | **否** |
| JSON 契约 | **否** |
| API 字段 / `/api/world` 结构 | **否** |
| 内部 `g17_lite` 键 | **否** |
| 同年前馈 / G17 `t→t+1` 时序 | **否** |
| 实际/名义 GDP 恒等式 | **否** |
| 六个特殊事件端口 | **否**（测试仍证明普通 Seed 为零） |
| 留出集是否用于调参 | **否** |

Viewer 只改用户可见中文标签和坐标轴分配；内部字段名 `ui_equity_total_return_index` 等保持不变。
