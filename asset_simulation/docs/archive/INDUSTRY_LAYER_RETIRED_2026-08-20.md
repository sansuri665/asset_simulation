# 行业层：银行营收份额参考
> 历史归档，不是当前实现权威
> 2026-08-20 退出运行时；当前事实见 docs/current/COMMODITY_LAYER.md


> 状态：当前实现事实  
> 权威范围：全球宏观之上的银行行业 overlay、字段守恒与银行行业第一类压力事件  
> 代码基线：全球 v0.7、银行 `asset-simulation-industry-banks-v0.3`、服务 v5.11  
> 最近核对：2026-08-17

## 产品位置

银行层是冻结全球宏观之后的只读 overlay，不是一二级市场 owner，也不回写同年 GDP、通胀、政策或信用。

```text
run_global_macro()
        │
        ▼ 同年已结账 row
industry_overlay.step(banks)
        │
        ├─ banks.* 参考
        └─ residual.revenue_share = 1 − banks.revenue_share
```

- 全球身份仍是 `asset-simulation-global-macro-v0.7`；改银行配置不得改变全球 `result_hash`。
- 覆盖层身份是 `asset-simulation-industry-banks-v0.3`，绑定 `upstream_global_identity_hash`。
- Viewer 仍只请求 `/api/global`。响应增加 `industries`，不新增 `/api/world`。命名银行占银行营收饼，见 [`ISSUER_ICBC.md`](ISSUER_ICBC.md)，不回写银行 `result_hash`。
- `capital_market_minimum_v1` 仍未注册。本层是它的上游营收／盈利饼图。

## 银行对象

第一版不拆投行／零售／城商行。普通年把上市银行近似成全球上市企业**营收**的一块慢变份额；盈利由营收 × 净利率派生。

| 锚 | 普通年 | 中枢 |
|---|---:|---:|
| 上市营收 / 名义 GDP | 约 75%–90% | 80% |
| 上市净利率 | 利润份额指数打在净利率上，弹性 2 | 10%（指数=100） |
| 银行营收份额 | 4.8%–7.5% | 6.0% |
| 银行净利率 | 独立 AR：信用、NIM、周期、自身营收收缩 | 20% |
| 市值方向份额 | 8%–12% | 10% |

80% × 10% 对上旧的上市盈利 / GDP ≈ 8%。银行 6% 营收份额 × 20% 净利率 / 10% 上市净利率，对上约 12% 盈利份额。营收份额轨道 3.5%–10.0%。越界视为机制失败。

数量级来自公开行业综述，不是数据商拉数。麦肯锡 2023 年全球金融中介营收约 $7T、银行净利润 $1.1T，落到本模型上市营收饼后大约是 6% 营收份额、20% 净利率。这是参考饼，不是国民账户全口径。

```text
上市营收 = 名义 GDP × β
上市净利率 = clamp(10% × (1 + 2 × (利润份额指数 / 100 − 1)), 6%, 16%)
上市盈利 = 上市营收 × 上市净利率
银行营收 = 银行营收份额 × 上市营收
银行盈利 = 银行营收 × 银行净利率
```

指数=100 时上市净利率仍是 10%，80% × 10% 仍对上旧的上市盈利 / GDP ≈ 8%。弹性 2 只放大指数偏离 100 的部分：普通年指数大约 90–110 时，上市净利率大约 8%–12%。不改全球利润份额指数，也不把缺口／HY／能源再写进上市净利率。

银行净利率是独立 AR，不缩放上市净利率。中枢仍是 20%，硬轨道 14%–28%。目标项分四块：信用（HY、违约、衰退）、NIM（期限利差、倒挂、政策变动）、周期（产出缺口、负缺口）以及银行自身美元营收收缩（只在营收同比为负时压一截）。份额侧仍保留较弱的期限利差／加息／倒挂，作为开新业务，不再把 NIM 全部算进份额。普通年大约 16%–23%，均值靠近 20%；不对称，差年往下比好年往上多走。命名发行人压力事件不回写本层。

`β` 普通年中枢从约 80% 起缓慢上移，轨道 55%–115%。Seed 通过地址 `industry.listed_earnings.revenue` 给出初始偏置和逐年小噪声。利润份额指数不再缩放营收，只缩放上市净利率。银行营收份额中枢 6.0%；地址 `industry.banks.share` 在 t0 给小起点偏置。银行净利率有独立地址 `industry.banks.margin`。派生盈利份额仍发布，但不是控制量。市值方向按派生盈利份额 × 金融 PE 折价。这不是国民账户全口径。

每年守恒：

```text
banks_revenue_share + residual_revenue_share = 1
上市营收 = 名义 GDP × listed_revenue_to_ngdp
上市盈利 = 上市营收 × listed_net_margin
银行营收 = banks_revenue_share × 上市营收
银行盈利 = 银行营收 × banks_net_margin
banks_earnings_share = 银行盈利 / 上市盈利
banks_earnings_index = banks_earnings_share × global_corporate_earnings_reference_index
```

随机地址：`industry.listed_earnings.revenue`、`industry.banks.share`、`industry.banks.margin`。普通 Seed 不绑定银行危机，也不把宏观六端口接到本层。

## 银行行业特殊事件（第一类）

这是**银行行业自己的**随机特殊事件，不是 `model/impulses.py` 六个宏观端口，也不写回银行营收饼或全球 `result_hash`。类名 `named_issuer_stress`，行业字段 `banks`。目录在 `config/bank_issuer_events_v0.1.json`。**当前打开**：`events_open: true`，`max_events_per_run: 2`，第一次 55%、第二次 24%。形态公式在 `model/issuer_fade.py`。抽中后改写该票剩余份额中枢：暴雷靠亏损缩，跃进靠大赚扩。步进细节见 [`ISSUER_ICBC.md`](ISSUER_ICBC.md)「命名发行人压力事件」。

Viewer 属性卡写可抽形态或「本次抽中」。目录里的可抽池、排除名单和开局名次先验仍发布在 overlay `identity.issuer_event_catalog`。

**形态怎么跟体量走。** 可抽池（104 家，`excluded_ids` 为空）按公布的银行营收份额排名，残差饼不进分母。名次 1 最大；并列先比份额、再按 `issuer_id` 升序。`size_unit` 从 0（最大）到 1（最小）。动手年份在运行开头抽定；**形态在动手前一年**用当时公布份额重核，暴雷过程中不再升级。第二张票用它自己动手前一年的名次；组加总吃饼已不在 overlay 里，同档新闻零和仍可能让其余票显得更大、形态偏轻。

百分位切在约前 34% / 后 34%，边界 `blend_width = 0.08` 让相邻名次的形态权重重叠，`stub_frac` 随 `size_unit` 向大行 0.94、尾票 0.42 插值。绝对份额是安全轨，切点不随名单长度改含义；否决与名次共用同一个 `blend_width`，半宽是切点的 8%（0.32% 附近约 0.294%–0.346%，0.10% 附近约 0.092%–0.108%）：

- 公布份额远高于 0.32%：只走盈利受压，禁止挤兑重组。
- 贴在 0.32% 上：大行混权被抬高，不是一刀切到 100%。
- 公布份额远高于 0.10%：重组权重为 0。
- 贴在 0.10% 上：重组混权被压低，不是清零。
- 公布份额低于 0.10% 轨下沿、且名次落在尾段：才可能挤兑重组。

**更新名录后，本事件必须一起改。** 排名分母是当前可抽池。西欧北欧三已接入；再加一串更小的本地行，会改变百分位切出来的人，但不改变 0.32% / 0.10% 安全轨切点。接入清单包括重核 JSON 里的百分位切点和安全轨是否还对应「大行受压、尾票才重组」；必要时当场改目录，不必等下一次大重构。当前可抽池 104 家，百分位切点仍是约前 34% / 后 34%。法兴／劳埃德远高于 0.32%，锁盈利受压；NatWest／CIBC 贴在切点上，大行混权被抬高。Nordea／KBC／商行／Erste 落在中票。AIB 份额 0.092% 落在 0.10% 轨下沿，百分位仍是中票；爱行／日德兰／沃州才是开局尾票。

**新增命名银行时，事件必须一起接入，并写入文档。** 只加 config、registry 和属性卡不算完成：

1. 在 [`ISSUER_ICBC.md`](ISSUER_ICBC.md) 对应名录表登记份额、影子净利率、派息和 id。漏写文档视为未接入。
2. 打开事件目录，**默认进入可抽池**。体量掀不起行业份额 Lorenz 天花板、也进不了个股硬轨道的票不要进名录（大众已踢出，`excluded_ids` 目前为空）。加入后按本节排名规则重核：新票落在哪一段、会不会把旧票挤出大行百分位、安全轨是否仍挡住工行档。
3. 梯队只做身份和份额软地板，不是组墙。核对行业 Lorenz 天花板仍松过开局前缀；贴在个股硬墙上的薄票会被抬离，硬轨道仍赢。
4. 属性卡事件栏从 catalog 读开局名次先验，不必改中枢轨道。

其它申万行业以后可挂同一 `event_class` 的目录，另开该行业文档，不改宏观六端口。

## Viewer

行业页交互见 [`VIEWER_PROJECTION.md`](VIEWER_PROJECTION.md)。银行既是一级也是二级。电子一级已删除；半导体、电子元件、光电、消费电子、电子材料升为一级。半导体二级为芯片设计、晶圆制造、存储、封装测试、半导体设备，尚未建模。

## Owner

- 配置：`config/industry_banks_v0.1.json`
- 契约：`contracts/industry_banks_v1.json`（已注册，不并入全球 42 项）
- 上市盈利金额：`model/listed_earnings.py`
- 银行份额：`model/industry_banks.py`
- 编排：`model/industry_overlay.py`
- 行业事件目录：`config/bank_issuer_events_v0.1.json`（类记录在本文；步进见 [`ISSUER_ICBC.md`](ISSUER_ICBC.md)）
