# Asset Simulation 模型入场规则

本目录是面向资本市场的独立精简宏观模拟器。开始分析或修改前，先完整阅读：

`asset_simulation/docs/MODEL_CONTEXT_GUIDE.md`

这是从仓库根开始的相对路径。读完后，再按其中“任务路由”读取本次所需的 owner 文件。

## 硬规则

1. **代码和 JSON 契约是实现事实的唯一最终来源。** 指引中的“设计意图”“未来方向”和“推断”不得冒充当前代码行为；若指引与代码冲突，以代码为准并报告文档陈旧。
2. **不要从旧项目推断当前实现。** `capital_market_lab`、旧 8781 Viewer、已删除的四区／G17 和历史路线只可作为背景，不是本目录运行时依赖。
3. **先确认版本。** 当前全球版本从 `model/engine.py` 和 `model/registry.py` 读取，不凭记忆作答。
4. **严格区分同年前馈和跨年反馈。** 全球同年按 `real → inflation → rates → funding/credit → oil/commodity → asset` 前馈。返回上游的作用只经 `NextYearInputs` 滞后一年。不得笼统声称“所有作用都滞后一年”。
5. **默认产品只有一个 `run_global_macro`。** 没有正式区域、G17 或双边汇率。世界是单一计价单位下的全球宏观。
6. **实际与名义口径必须写清。** 不得把名义价格长期上涨解释为实际供需改善；不得把全球美元资金条件代理称为真实 DXY 或汇率。
7. **普通 Seed 不等于命名事件。** 普通运行不绑定危机；事件冲击只能通过显式端口进入。
8. **审计时列出真正读取的文件。** Glob/Grep 命中不等于完整 Read；未读文件不得声称已读。
9. **不要为证明理解而修改代码或运行无关命令。** 若工具被拒绝，说明限制并使用正常的 Read/Glob/Grep；不得借测试或其它工具绕过权限意图。
10. **严格区分竞技 Demo 与完成游戏。** 四机构随机任命、正式期货现金／保证金账本、追保强平、排名和报告已经运行；玩家自主决策、持久存档、逐笔委托、多策略、长期收益平衡和产品级 UI 尚未完成。
11. **退役银行属性卡不是当前设计。** `docs/design/*_CARDS.md` 只保留历史数字，当前运行时没有银行行业、命名发行人或 `current/ISSUER_ICBC.md`。

## 路径

- 仓库根是克隆得到的 `asset_simulation/` 目录。
- Python 包与模型根是仓库根下的 `asset_simulation/`。
- 从仓库根运行时使用 `asset_simulation/...`；进入包目录后才使用模型内相对路径。
- 不要假定开发者本机存在固定盘符；先确认当前工作目录。

## 修改后的最低验收

```powershell
py -3.13 -m unittest discover -s asset_simulation/tests -v
node --check asset_simulation/viewer/static/js/app.js
node --check asset_simulation/viewer/static/js/game.js
```

模型公式、公共字段或 Viewer 图形发生变化时，还要按指引执行对应的跨 Seed、恒等式或实际页面验收。
