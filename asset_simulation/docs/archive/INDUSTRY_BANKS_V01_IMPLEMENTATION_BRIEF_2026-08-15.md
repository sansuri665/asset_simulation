# 行业层 v0.1 · 银行份额参考 实装任务书

> 历史归档，不是当前实现权威
> 原状态：活动 Goal，已于 2026-08-15 完成并迁入当前代码
> 当前事实以 `docs/current/INDUSTRY_LAYER.md`、`docs/current/` 与代码／JSON 契约为准
> 日期：2026-08-15
> 当时基线：全球 v0.7、宏观 UI v5.0
> 目标：行业层 Viewer + 银行盈利／营收／市值方向参考
> 产品边界：ADR-006；本层不是一二级市场 owner

## 0. 结论

在已经冻结的全球宏观之上，加一层**行业份额参考**。第一版只做银行。界面顶部增加可扩展的层按钮（宏观 / 行业），行业页先只开放银行。

银行对象只回答三件事，全是参考：

1. 占全球企业盈利的大概份额；
2. 由份额和净利率影子倒挤的营收参考；
3. 市值方向（偏低 PE 的金融折价），不宣称可交易盘子。

普通 Seed、无命名事件时，银行份额必须稳定：慢变、强均值回复，不跟科技或能源一样大起大落。本 Goal **不**实现一级发行、二级流动性、银行子业态，也 **不**同年回写 GDP／通胀／政策／HY。

完成后更新 `FUTURE_LAYERS.md`：行业份额 overlay 先于完整资本市场 owner。不跑第二次 700 Seed。

---

## 1. 为什么先做银行，以及稳不稳

全球同年链已经结账的对象，大半就是银行损益的输入：政策、2Y/10Y、HY、FCI、资金流动性、违约压力、产出缺口、名义盈利。能源和科技还缺自己的利润表。

无特殊事件时，上市银行占企业盈利的份额确实比较稳。信用周期改变的是盈利增速和 ROE，不是份额从 12% 跳到 30%。本层要把这句话写成机制：高持久、窄轨道；HY 变宽或产出缺口转负时，份额只允许小幅下移。

现实校准只取数量级，不当数据商：

| 锚 | 普通年区间 | 本版中枢 | 含义 |
|---|---:|---:|---|
| 银行盈利 / 全球企业盈利参考 | 10%–15% | **12%** | 对应上市银行（不是全部金融业） |
| 银行市值方向 / 全球权益市值参考 | 8%–12% | **10%** | 银行 PE 低于市场，份额低于盈利份额 |
| 净利率影子（盈利／营收） | 20%–30% | **25%** | 只为倒挤营收参考 |
| 普通年份额轨道 | 7%–20% | — | 越界视为机制失败，不是“危机” |

不要用银行资产／GDP（常超过 100%）当界面份额，那会和盈利饼混淆。

---

## 2. 架构

```text
run_global_macro()                # 不改公式，不改 42 项契约，不改 identity
        │
        ▼ 同年已结账 row
industry_overlay.step(banks)      # 新 owner；只读宏观
        │
        ├─ banks.* 参考
        └─ residual.earnings_share = 1 − banks.earnings_share
```

- 全球 `asset-simulation-global-macro-v0.7` **不升版**。
- 新身份：`asset-simulation-industry-banks-v0.1`。
- 服务升到 `asset-simulation-macro-ui-v5.1`。
- 覆盖层绑定 `upstream_global_identity_hash`；改银行配置不得改变全球 `result_hash`。
- Viewer 仍只请求一次 `GET /api/global`。响应增加 `industries`，不新增 `/api/world`，不恢复分区 Scope。
- `capital_market_minimum_v1` 继续未注册。本层是它的上游饼图，不是市场 runtime。

禁止：

- 把银行公式写进 `engine.py::_transition`；
- 拆投行／零售／城商行；
- 把市值参考写成可交易指数；
- 用宏观六端口假装银行危机；
- 为行业层单独重跑全球。

---

## 3. 字段与宏观接法

契约建议：`contracts/industry_banks_v1.json`（注册进 `load_registered_assets()`，与全球契约并列，不塞进 `global_macro_minimum_v3`）。

配置建议：`config/industry_banks_v0.1.json`。

Owner 建议：`model/industry_banks.py`，由 `model/industry_overlay.py` 逐年编排。初始年份额等于中枢，增长类字段可空。

最小字段：

| 字段 | 单位 | 口径 |
|---|---|---|
| `banks_earnings_share` | 比例 | 占 `global_corporate_earnings_reference_index` |
| `banks_earnings_index` | 指数 | `share × 全球盈利参考` |
| `banks_earnings_growth_pct` | % YoY | 参考 |
| `banks_net_margin` | 比例 | 影子净利率，中枢 0.25 |
| `banks_revenue_index` | 指数 | `earnings / net_margin` |
| `banks_revenue_growth_pct` | % YoY | 参考 |
| `banks_pe_ratio` | 倍数 | 全球 PE × 金融折价（约 0.80） |
| `banks_market_cap_share` | 比例 | 市值方向份额，中枢 0.10 |
| `banks_market_cap_index` | 指数 | 方向，不是可交易市值 |
| `residual_earnings_share` | 比例 | `1 − banks_earnings_share` |

守恒（每年、含初始年）：

```text
banks_earnings_share + residual_earnings_share = 1
banks_earnings_index = banks_earnings_share × global_corporate_earnings_reference_index
```

宏观映射（同年只读，系数在配置里，任务书不写死公式）：

- 份额：向 0.12 强回复（持久约 0.90）；HY 高于 420bp、产出缺口为负、资金压力上升 → 份额小幅下降。
- 盈利增速：全球名义盈利增速 + 息差环境（政策／期限利差）− 信用成本（HY、违约、缺口）。
- 营收：倒挤，不另建贷款余额状态机。
- 市值方向：盈利 × 偏低 PE；FCI 收紧时折价略加深。
- 随机：最多一个小地址 `industry.banks.share`；普通年创新必须很小，份额主要靠宏观，不靠噪声。

方向性验收（用已有 `run_global_macro_with_impulses`，不要新危机机）：

- 信用利差冲击 → 银行盈利增速低于全球盈利增速，份额不升。
- 需求正冲击 → 银行盈利升，但份额仍在轨道内。

---

## 4. 界面

顶部在品牌和 Seed 表单之间加一层导航，可扩展：

```text
宏观 | 行业
```

- `layer=macro`：现有全球页，一个字段不删。
- `layer=industry`：行业页。第二级只显示 **银行**；不要放未实现板块的空按钮。页脚写清「其它行业 = 盈利残差，尚未建模」。
- 切换层保留同一 Seed、年数、当前年份。URL：`/?seed=42&years=60&layer=industry`。
- 银行卡片约 6 张：盈利份额、盈利参考、营收参考、市值方向份额、相对全球盈利增速、净利率影子。
- 模块：总览、份额、盈利、营收、市值方向。
- 文案必须带「参考」「方向」；禁止「总市值」「可交易」「板块涨跌」。
- 前端不补算份额。`industries.banks` 由服务投影。

服务：`build_run_payload` 增加 `industries`；缓存仍以全球运行为键，覆盖层从缓存的 `GlobalMacroRun` 派生。

---

## 5. 测试与文档

新增 `tests/test_industry_banks.py`：

- 确定性、短窗前缀；
- 份额 + 残差 = 1（阈值 `<1e-8`）；
- 全球 `result_hash`／42 项快照不被覆盖层改变；
- 普通 40 个 Seed、60 年：盈利份额均值在 10%–15%，年度变化 std 明显小于全球盈利增速 std；
- 信用冲击方向性。

扩展 `tests/test_service_viewer.py`：层按钮、银行文案、`industries` 载荷、UI v5.1。

验收：

```powershell
cd C:\d_e\oiltanker\airport
py -3.13 -m unittest discover -s asset_simulation/tests -v
node --check asset_simulation/viewer/static/js/app.js
```

不跑 700 Seed。行业层不进 Goal C 全球门禁。

文档（实装时写，本任务书不算当前事实）：

- `docs/current/INDUSTRY_LAYER.md`
- 更新 Runtime、Contracts、Viewer Projection、INDEX、FUTURE_LAYERS、入场指引
- 完成后把本文件迁入 `docs/archive/`

---

## 6. 明确不做

- 一二级市场端口与簿记；
- 银行子业态、监管资本、准备金、QE；
- 其它正式板块（残差占位即可）；
- 恢复汇率、分区 Scope、Coupled World；
- 修改全球 v0.7 公式。

## 7. 开工必读

完整读取：`CLAUDE.md`、`docs/INDEX.md`、`docs/current/RUNTIME_ARCHITECTURE.md`、`CONTRACTS_AND_UNITS.md`、`VIEWER_PROJECTION.md`、`model/engine.py`、`model/asset_reference.py`、`model/funding_credit.py`、`model/impulses.py`、`server.py`、`viewer/index.html`、`viewer/static/js/app.js`（到 EOF）、`contracts/capital_market_minimum_v1.json`。
