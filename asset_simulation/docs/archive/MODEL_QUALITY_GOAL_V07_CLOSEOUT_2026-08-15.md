# 模型质量 Goal · v0.7 实施报告

> 状态：历史归档，不是当前实现权威  
> 执行者：Cursor / Grok  
> 日期：2026-08-15  
> 范围：必须修复 + 条件校准 + Viewer 减法；禁止修复 Reserved  
> 当前事实去向：`docs/current/*`、`docs/regions/*`、`docs/components/GLOBAL_ORDINARY_CYCLE.md`

## 0. 结论

- 审计总状态 **`warning`**，`failure_count = 0`，漂移 **`pass`**，warning 40→29。
- 真正修正：中国 terminal 锚、区域契约 v3、全球潜在增速夹具、IG/HY 扩张期贴下沿（减轻）、Viewer 8/6 卡片与中文文案。
- 有意保留：产出缺口轨道、MBS/QE、RoW、权益波动中枢、名义油价口径。
- unittest 33 项 OK；`node --check` 通过。未做真实浏览器四 Scope／窄屏／pointer／控制台，留给 Codex。

## 1. 实际读取

完整：`CLAUDE.md`、`docs/MODEL_CONTEXT_GUIDE.md`、`docs/INDEX.md`、`docs/current/RUNTIME_ARCHITECTURE.md`、`CONTRACTS_AND_UNITS.md`、`G17_LITE.md`、`MODEL_QUALITY_AUDIT.md`、`VIEWER_PROJECTION.md`、`model/registry.py`、`contracts/regional_macro_extension_v2.json`、`model/funding_credit.py`、`model/china_components/real_prices.py`、`model/real_economy.py`、`viewer/static/js/app.js`（到 EOF）、`viewer/index.html`、`tests/test_service_viewer.py`、`tests/test_volatility_audit.py`、`tests/test_china_mainland.py`。

部分：`model/engine.py`、`world.py`、`china_mainland.py`、`north_america.py`、`g17_lite.py`、`impulses.py` 的版本／身份／公式段；`config/*.json` bounds 与锚；`server.py` 服务身份；`docs/regions/*`。

失败读取：无。

## 2. 版本迁移表

| 对象 | before | after |
|---|---|---|
| 全球 | v0.6 · `global_macro_v0.6.json` · stack v6 | **v0.7** · `global_macro_v0.7.json` · stack v7 |
| 北美 | v0.6 | v0.6（公式未改；契约哈希变） |
| 中国 | v0.3 · `china_mainland_v0.3.json` | **v0.4** · `china_mainland_v0.4.json` |
| 中国支持契约 | v1 | **v2**（`common_contract_id` → v3） |
| 公共区域契约 | v2（保留历史文件） | **v3** 注册 |
| G17 | v0.2 | v0.2；`world_model_version` → World v0.3 |
| World | v0.2 | **v0.3** |
| Viewer / 服务 | v4.1 | **v4.2** |
| 内部 `g17_lite` Scope 键 | 不变 | 不变 |
| 单一 CoupledWorldRun 时序 | 不变 | 不变 |

历史配置／契约文件留在仓库，registry 不再指向它们。

## 3. 实际修改文件

| 文件 | 原因 |
|---|---|
| `model/china_components/real_prices.py` | terminal 长期锚 |
| `model/china_mainland.py` | v0.4 |
| `config/china_mainland_v0.4.json` | 新配置 |
| `model/real_economy.py` | 潜在增速对生产率残差 ×0.35 |
| `model/funding_credit.py` | IG/HY 锚回复 0.22 |
| `model/engine.py` | 全球 v0.7 |
| `config/global_macro_v0.7.json` | 新配置 |
| `contracts/regional_macro_extension_v3.json` | 新契约 |
| `contracts/china_mainland_support_v2.json` | 指向 v3 |
| `model/registry.py` | 注册新路径 |
| `model/world.py` | World v0.3 |
| `config/g17_lite_v0.2.json` | `world_model_version` |
| `server.py` | UI v4.2 |
| `viewer/static/js/app.js`、`viewer/index.html` | 减法与中文 |
| `tests/test_*` | 迁移、terminal 方向、Viewer 静态契约 |
| `docs/current/*`、`regions/*`、`components/GLOBAL_ORDINARY_CYCLE.md`、`INDEX.md`、`MODEL_CONTEXT_GUIDE.md`、`README.md` | 事实收口 |

**未改：** `contracts/regional_macro_extension_v2.json` 内容；北美 MBS/QE 公式；油价；权益波动；RoW 会计；事件端口；无关用户改动。

## 4. 测试与三段

```powershell
py -3.13 -m unittest discover -s asset_simulation/tests -v
node --check asset_simulation/viewer/static/js/app.js
py -3.13 -m asset_simulation.audit_volatility --profile goal-c --years 60 --output $env:TEMP\asset_simulation_goal_c_audit_v07.json
```

- unittest：`Ran 33 tests in 3.878s` OK  
- JS syntax：退出码 0  
- 三段：校准／验证／留出均为 warning，0 fail，漂移 pass，warning_count=29，info_count=6  

选参只依据 2026-08-14 before 审计与公式诊断，一次 after 复算，没有对留出集迭代调参。

## 5. 不同意见

- 产出缺口贴边是状态方程相对轨道过冲，但减小 0.58 会削弱通胀／衰退。按“不能只扩大 bounds、不能削弱方向”保留 warning。
- 全球负增长频率下降，判断为去掉潜在增速 1.25 夹具的伪弱年；北美衰退率几乎不变，通胀尾部仍在。
- IG/HY 未降地板，只加强锚回复；仍有 ≥0.1% 下沿 warning，接受为平静扩张残差。

## 6. 未解决 / 留给 Codex

- 真实页面：四 Scope、窄屏、pointer 年份、控制台。
- 产出缺口 warning 是否在未来版本用新周期标定（需新版本，不能藏进 bounds）。
- IG/HY 下沿 warning 是否还要再降；本轮不再看留出集调参。
- 历史 v2 契约文件的最终归档策略。
