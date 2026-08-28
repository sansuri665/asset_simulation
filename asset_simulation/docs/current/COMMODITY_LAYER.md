# 商品层：种类与合约

> 状态：当前实现事实  
> 权威范围：全球宏观之上的只读商品 overlay、一级种类与二级合约  
> 代码基线：全球 v0.8.1、商品 overlay `asset-simulation-commodity-overlay-v0.1.2`、原油期货 overlay `asset-simulation-oil-futures-overlay-v0.8.0`、服务 v5.41  
> 最近核对：2026-08-25

## 产品位置

商品层是冻结全球宏观之后的只读 overlay。不回写同年 GDP、通胀、政策、信用或 `oil_commodity`。Brent 不另算一遍。

```text
run_global_macro()
        │
        ▼ 同年已结账 row
commodity_overlay
        │
        ├─ kinds.*     一级种类篮子
        └─ contracts.* 内部价格序列；Viewer 列表投影为二级通用商品名，当前只绘制原油
```

- 全球身份是 `asset-simulation-global-macro-v0.8.1`。
- 覆盖层身份是 `asset-simulation-commodity-overlay-v0.1.2`，绑定 `upstream_global_identity_hash`。
- Viewer 仍只请求 `/api/global`。响应增加 `commodities`，不再含银行行业或命名个股。商品页列表保留全部种类；当前只有原油绘制图表，其它品种点选后图表留空。
- `capital_market_minimum_v1` 仍未注册。游戏另有已注册但只读的01／05／09原油月份合约 overlay；竞技账户可在这一只读市场上结算，但没有玩家委托、撮合、现金冻结或市场价格反馈。

## 一级种类 / 二级合约

Overlay 仍计算下表全部合约。Viewer 列表可选这些品种；当前只绘制原油，天然气、金属、农产品以及未列入列表的 WTI 点选后图表留空。

| 一级 | 二级 |
|---|---|
| 能源 | 原油（内部 `brent`）、天然气（内部 `henry_hub`） |
| 工业金属 | 铜、铝、铁矿 |
| 贵金属 | 黄金、白银 |
| 农产品 | 小麦、玉米、大豆 |

```text
Brent 名义价 = 全球 brent_oil_price_usd
Brent 实际指数 = 全球 global_real_oil_price_index
原油年振幅：low ≤ min(上年收盘, 本年收盘) ≤ max(上年收盘, 本年收盘) ≤ high
  通道宽度由年度波动状态、库存松紧、供需缺口、美元资金条件和独立年内噪声给出；年收盘仍是石油 owner 结账，不另算
  上涨年剩余通道多在下影，下跌年多在上影，使收盘靠近当年极值
  月／周路径共同读取年度波动状态，在该加宽通道内形成冲击；越界收盘反射回通道，不把连续 K 线硬钳在同一价格
  不是第二条年结账，也不是分时成交
原油月线：12 根收盘沿年开到年收做年内波动，不把年涨跌均分成 12 个小实体；年高年低由月内影扫到，不把某月收盘钉在极值上
  月内影随当月涨跌方向：涨月剩余多在下影，跌月多在上影；实体偏小的月份额外加长上下影
  原油周线：每月 4 根，嵌在该月包络内；第 1 周开 = 月开，第 4 周收 = 月收；月高月低由周内影扫到，不把周收盘钉在月极值上
  不是成交月度／周度市场，也不回写 oil_commodity 或全球 42 项
WTI 名义价 = Brent × (1 + 粘性价差)
卫星名义价 = 锚价 × 实际指数 / 100 × CPI / 100
种类实际指数 = 成员实际指数的加权平均
种类名义指数 = 种类实际指数 × CPI / 100
```

卫星实际收益读取同年已结账的实际油价同比、综合商品同比和美元资金条件同比，再加本合约 Seed 噪声和向 100 的弱回复。不是独立物理库存市场。WTI 价差中枢约 −3.5%，夹在 −12% 到 +4%。

普通 Seed 不绑定石油危机。六个宏观特殊事件端口仍默认全零；油供给冲击若显式写入，仍只进全球 `oil_commodity`，本层只读结账后的 Brent。

v0.1.2 对应全球 v0.8.1：石油 owner 新增持续的年度收益动量与波动状态，年内振幅普通样本中位数校准到约 38%–40%；月线和周线普通波动较上一版分别提高约 20% 和 15%。这不是危机尾部生成器，战争、禁运等跳变仍只允许经显式事件端口进入。

## Viewer

顶部 `宏观 | 商品`。商品页一级／二级下拉对应种类和合约，不并列 Brent／WTI。原油绘制名义／实际价格图，年线带年振幅上下影，月线由该通道展开，周线再由各月包络展开；其它品种图表留空。交互见 [`VIEWER_PROJECTION.md`](VIEWER_PROJECTION.md)。

## 游戏01/05/09期货 overlay

`model/oil_futures_overlay.py` 服务 `/api/oil-futures`。固定挂牌 4 个01／05／09交割月份合约，代码为 `OIL-YYMM`，例如2030年5月合约显示 `OIL-3005`。每月分上、下半月两个游戏回合；命名合约最长 16 个月／32 回合／64 根周 K。上半月响应只公开 W1–W2，下半月补齐 W3–W4。v0.8 先用资金成本、仓储成本和期限风险溢价减去条件化便利收益，形成长期 carry 目标；便利收益随最近完整年度的库存松紧、供需缺口和近期可见趋势变化，再由持续的长期斜率、近端压力和曲率展开到各合约。宽松库存自然偏向 contango，紧张库存自然偏向 backwardation；三个基差分量均随剩余期限连续归零。因子只读取截至当前半月的原油周收与最近完整年度宏观行，截点之后的数据和玩家行为不参与定价。

v0.8 的三段审计覆盖 Seed 0—199、每个 Seed 2030—2085 共1344个半月状态。合计约56.4%正向、38.5%反向、5.1%平坦；远近价差中位数约1.7%，全样本尾部为 −13.22% 至 +18.60%。库存松紧不高于 −4 时，约99.9%为正向；不低于 +4 时，各段约89.7%—92.0%为反向。三段均保持正价格、到期零基差和 ±25%普通尾部门禁。该比例不是人为设定的50/50配额，而是物理状态经过持久因子后的结果。

01／05／09近月合约在到期月进入仅结算状态，下半月最终收敛现货，下一月删除并在远端补一个新月份合约。主连在到期月上半月切换下一合约，因此相邻换仓相隔 8 个半月回合；换仓点按新旧合约共同的上一半月结算价比例回溯调整历史 OHLC。游戏列表把主连、现货参考和当前 4 个命名合约做成六个可点击行情入口；现货与主连有完整可见历史，命名合约只展示自身生命周期。它们当前均为只读模拟结算价。

v0.5 只给期货增加周成交量、周末持仓量和持仓变化；现货参考不增加这些字段。全球市场总持仓先由2030年约320万手的锚、全球实际规模、原油需求及有界慢周期生成，再乘周换手率得到总成交量；普通状态不会按固定增长率滚到无限大。每周总量按主力、下一主力、到期近月和远月的生命周期份额分配，换月前逐步从旧主力迁向新主力，到期周末旧合约持仓归零。四合约成交量／持仓量之和分别严格等于当周全球市场总量；主连继承来源合约原始流动性，不随价格复权。

v0.7 把成交量和持仓量的换月迁移统一为同一条最后 8 周 `smoothstep` S 形进度：四个生命周期份额从换月前向换月后整组连续插值，不再在主连来源切换时直接换用另一组静态份额；最终结算周仍归零。参与者单回合成交额度改用最近 4 周成交量周均并折算为 2 周回合规模，再乘 0.8%。这不改变长期额度尺度，只减少相邻半月被单组两周成交噪声支配的跳动。

v0.6 发布标准合约规格：1000桶／手，报价为美元／桶，最小跳动0.01美元／桶、每手每跳10美元；现金结算，到期前一个月停止新交易、到期月仅作最终结算。15%初始与12%维持保证金已经进入后台策略的保证金预算，但仍没有现金冻结、追保或强平账户。

v0.7 发布统一的“全球巨型机构”参与者限额。常态单合约净持仓上限取当前合约持仓量3%与75,000手的较低者；四个挂牌合约绝对头寸合计不得超过150,000手；每半月回合成交上限为最近4周周均折算两周后的0.8%。距到期不超过1.5／1.0个月时再收紧至15,000／8,000手，到期月不允许新交易、仅结算。后台策略和四机构竞技已经执行这些边界；游戏界面仍没有玩家委托或正式持仓操作。所有成交仍不会反向改变价格、公开成交量或市场持仓量。

交易链采用“理想目标 → 公司风控审批 → 正式账户保证金授权 → 限仓／容量裁剪 → 两个周窗口执行 → 盯市／追保强平 → 回合报告”，不模拟逐笔委托，也不拆分开仓／平仓额度。玩家和3家 AI 已通过 `/api/oil-investment-competition` 接入独立正式账户和多实体排名；持久存档、委托输入、动态组合保证金和机构间撮合仍未实现。当前事实见 [`OIL_INVESTMENT_COMPETITION.md`](OIL_INVESTMENT_COMPETITION.md) 与 [`OIL_FORMAL_ACCOUNT_AND_CALIBRATION.md`](OIL_FORMAL_ACCOUNT_AND_CALIBRATION.md)，长期扩展见 [`OIL_FUTURES_TURN_EXECUTION.md`](../design/OIL_FUTURES_TURN_EXECUTION.md)。

当前另有独立的短期研究信息层，只联合预测当前主力和下一主力命名合约的剩余周线，不预测不可交易的连续主连。它不向 `/api/oil-futures` 混入预测字段；游戏行情页已撤下预测 K，竞技会话在后台把预测交给策略。详见 [`OIL_SHORT_TERM_FORECAST.md`](OIL_SHORT_TERM_FORECAST.md)。

## Owner

- 配置：`config/commodity_overlay_v0.1.json`、`config/oil_futures_overlay_v0.8.json`
- 契约：`contracts/commodity_overlay_v1.json`、`contracts/oil_futures_overlay_v8.json`
- 期限结构审计：`audit_oil_futures_curve.py`
- 公式：`model/commodity_overlay.py`、`model/oil_futures_overlay.py`、原油年振幅 `model/oil_commodity.py` 的 `annual_price_envelope`、月线展开 `expand_annual_to_months`、周线展开 `expand_month_to_weeks`
- 短期预测：`model/oil_short_term_forecast.py`、`model/oil_forecast_research_profile.py`、`config/oil_short_term_forecast_v0.2.json`、`contracts/oil_short_term_forecast_v2.json`
- 策略研究：`model/oil_strategy_research.py`、`config/oil_strategy_research_v0.2.json`、`contracts/oil_strategy_research_v2.json`
- 策略风控：`model/oil_strategy_risk.py`、`config/oil_strategy_risk_v0.1.json`、`contracts/oil_strategy_risk_v1.json`
- 基础策略：`model/oil_trading_strategy.py`、`model/oil_strategy_thesis.py`、`config/oil_trading_strategy_v1.2.json`、`contracts/oil_trading_strategy_v8.json`
- 投资决策竞技：`model/oil_investment_competition.py`
- 目录：`viewer/static/data/commodities.json`
- 全球石油 owner 仍是 `model/oil_commodity.py`
