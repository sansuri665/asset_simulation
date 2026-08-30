# Asset Simulation

> 当前基线：全球宏观 v0.8.1、原油期货 v0.8.0、正式期货账户 v0.1.0、投资决策竞技 v0.6.0、服务／Viewer v5.42
> 最近核对：2026-08-30

Asset Simulation 是一个按 Seed 可复算的长周期全球宏观、原油期货和机构投资决策模拟器。当前产品已经不只是图表原型：它能从2030年开始按半月推进，让玩家机构与三家 AI 在同一条不可变市场路径上运行预测、策略研究和交易执行，并逐回合比较账户结果。

界面仍是功能性开发原型，信息密度、视觉层级和交互反馈尚未达到正式游戏品质。运行闭环和模型边界已经存在，产品化界面、正式存档、玩家投委会操作和收益平衡仍未完成。

## 当前运行链

```text
Seed + 注册配置
→ 全球年度宏观世界（2025—2085）
→ 只读商品与原油现货
→ 半月01／05／09原油期货曲线与可见周 K
→ 每家机构自己的双合约短期预测
→ 策略风格形成理想目标，构造能力形成提交方案
→ 投委会授权并批准注册的单策略边界与仓位曲线
→ 组合风险层在单策略阶段明确透传
→ 正式账户按现金与初始保证金最终约束
→ 交易部在两个周窗口执行
→ 下一半月盯市、利息／融资、追保强平、排名与回合报告
```

全球宏观只有一个计价单位，没有正式区域、G17、RoW 或双边汇率。同年沿 `real → inflation → rates → funding/credit → oil → asset` 前馈；返回上游的反馈只能经具名 `NextYearInputs` 在下一年生效。

## 当前可操作内容

- 主 Viewer：宏观与商品两层；原油总览、年 K、月 K、周 K。
- 游戏时间轴：2025—2029为观察期；2030-01上半月至2085-12下半月，共1344个半月状态。
- 市场行情：原油现货参考、不可交易主连和4个 `OIL-YYMM` 命名合约。
- 投资决策：玩家与3家 AI 的随机任命、权益排名和每回合报告。
- Seed：主界面与游戏页共享；同 Seed 可确定性重放，不同 Seed 会抽出不同团队与市场路径。
- 重置：普通推进不允许倒流；重置是返回2030-01上半月的正式入口。

短期预测已经从行情 K 线界面撤下，但模型和 API 没有删除。竞技会话在后台为每家机构生成预测，并把普通预测对象交给策略。研究能力分只改变预测质量与事后评分，不能直接改变策略仓位。

## 当前明确没有

- 玩家自主选择人员、策略、额度、止损或投委会审批；
- 正式服务器存档、用户账户、联网对战或 LLM 指令入口；
- 委托输入、盘口、外部追保注资、动态组合保证金和市场冲击回写；
- 多品种组合、期现套利或第二种独立信号引擎；
- 经大样本校准的机构公平性和成熟收益率分布；
- 可交付游戏级 UI。当前页面只承担模型观察与 Demo 验证。

## 文档入口

- 权威索引：[`INDEX.md`](INDEX.md)
- 新模型入场：[`MODEL_CONTEXT_GUIDE.md`](MODEL_CONTEXT_GUIDE.md)
- 当前项目状态：[`current/PROJECT_STATUS.md`](current/PROJECT_STATUS.md)
- 运行架构：[`current/RUNTIME_ARCHITECTURE.md`](current/RUNTIME_ARCHITECTURE.md)
- 游戏与 Viewer：[`current/VIEWER_PROJECTION.md`](current/VIEWER_PROJECTION.md)
- 投资决策竞技：[`current/OIL_INVESTMENT_COMPETITION.md`](current/OIL_INVESTMENT_COMPETITION.md)
- 机构组织基线：[`current/INSTITUTION_ORGANIZATION.md`](current/INSTITUTION_ORGANIZATION.md)
- 正式账户与收益校准：[`current/OIL_FORMAL_ACCOUNT_AND_CALIBRATION.md`](current/OIL_FORMAL_ACCOUNT_AND_CALIBRATION.md)

代码与注册 JSON 契约始终高于文档；`design/` 只能描述未完成路线，`archive/` 只保留历史。

## 启动

```powershell
cd asset_simulation
py -3 -m asset_simulation.server
```

主界面：`http://127.0.0.1:8783/?seed=42&years=60`  
游戏界面：`http://127.0.0.1:8783/game?seed=42`

## 主要 API

| 路由 | 作用 |
|---|---|
| `GET /api/health` | 服务、模型版本与缓存状态 |
| `GET /api/global` | 全球年度快照和商品 overlay |
| `GET /api/oil-futures` | 指定半月截点的原油期货市场 |
| `GET /api/oil-investment-competition` | 四机构任命、账户、排名与回合历史 |
| `GET /api/oil-short-term-profile` | 独立生成预测研究机构档案 |
| `GET /api/oil-short-term-forecast` | 独立生成双合约预测 vintage |
| `GET /api/oil-strategy-research-roster` | 策略研究候选名单 |
| `GET /api/oil-execution-desk-roster` | 交易部候选名单 |

## 验收

```powershell
py -3 -m unittest discover -s asset_simulation/tests -v
node --check asset_simulation/viewer/static/js/app.js
node --check asset_simulation/viewer/static/js/game.js
```

当前全量基线为157项测试通过。模型或 Viewer 改动还需按 owner 文档执行跨 Seed、恒等式、真实页面与控制台验收。
