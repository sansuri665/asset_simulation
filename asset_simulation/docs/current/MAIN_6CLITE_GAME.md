# Main–6C Lite：双航路最小完整游戏

`main-6Clite` 从 `main-6Cpreview3` 分叉。底层 Freight Board、货盘形成、卖方报价、indicative/firm 与物理 commit 继续由 Preview3 负责；Lite 只增加公司所有权、AI策略、三轮市场制度和 HTML 游戏界面。

## 标准局

- 世界时间：2030–2039；
- 10 operating days / turn；
- 36 turns/year；
- 360 turns/campaign；
- 两条航路：Gulf → East Asia、West Africa → East Asia；
- 10家公司，固定总船队 280 VLCC + 160 Suezmax + 40 Aframax；
- 每家公司恰好 28 VLCC + 16 Suezmax + 4 Aframax；
- 1真人 + 9个透明简单AI；
- 真人拥有一个只读公开信息的 AI Shipping Advisor。

## Seed 与 warm start

同一 World Seed 生成完整 Main 物理世界，但游戏Agent绝不能读取未来记录。Lite 从 Preview3 的只读 Main bridge 提取 2030–2039，并把 requirement turn 重新映射到游戏物理时钟。

正式2030开局前，后台用同一 Seed 运行 2029年7–12月共18个中性市场回合。中性策略只负责让真实船位、在途货和卖方报价记忆形成自然初态，不参与2030后的游戏排名。

公司所有权按船型分别确定性打乱后轮转分配，所以资产完全相同，但2030的空间位置并不镜像公平。玩家不是“创建公司”，而是接手一个已经运行中的船东。

## 三轮市场

每个10日物理回合只有三次公开信息更新：

1. **View**：看到 opening world，表达初始判断；
2. **React**：看到上一轮所有公司聚合后的公开市场，允许完全修正；
3. **Commit**：最后一次同时提交，结算后直接锁为 Preview3 firm plan，没有第四轮反悔。

所有AI与真人都只读取同一轮开始时的 public state。本轮真人提交不会被AI作为后手信息读取。

## “换航道”的物理含义

当前已经在 Gulf/WAF 的 prompt ship 只能留在本地货盘或退出；它不能同回合瞬移到另一来源。East Asia 已卸空船可以在三轮中修改 ballast target：Gulf / WAF / wait。该动作只进入 proposed future tonnage，必须经过真实压载航程才能成为目标来源的 prompt ship。

当前市场队列规则：

- opening queue 由 `hash(seed, turn, route, ship_id)` 一次确定；
- keep 保留位置；
- exit 删除；
- re-enter 进入队尾；
- queue 决定谁装货，offered set 决定市场竞争压力。

UI按数量操作，不要求玩家逐艘管理48条船；后端优先保留已有队列位置，再用确定性船ID规则补足数量。底层执行仍然始终使用真实 ship ID。

## AI

AI不是隐藏优化器。九家船东只使用公开的当前运价、future tonnage、本公司未装船和位置惯性，以公开权重形成简单策略族：追价、前瞻、惰性、逆向、队列和若干轻微变体。

真人 Advisor 使用同样信息，输出：

- 建议的当前 prompt offer 数；
- East Asia 空船建议压载方向；
- 建议理由；
- 最大公开风险。

玩家可以一键采用，也可以自行修改。最终提交权始终属于玩家。

## 当前计分

尚未接入 bunker/OPEX/融资/船价，因此第一版不伪装成净利润游戏。排行榜使用：

`Gross Freight Service Value = route service value per bbl × actual loaded bbl`

并保留 cargo carried、full/marginal fixtures、ballast orders、idle prompt ship-turns 等运营统计。以后成本层加入后再升级为真实利润。

## HTML / API

启动：

```powershell
py -3 -m asset_simulation.lite_server
```

或运行 `start_game.bat` / `start_game.ps1`。

默认地址：`http://127.0.0.1:8784/game`

Lite server 继承现有研究Viewer，同时新增：

- `POST /api/game/new`
- `POST /api/game/choose`
- `POST /api/game/round`
- `POST /api/game/next`
- `GET /api/game/state`
- `GET /api/game/save`
- `POST /api/game/load`

浏览器不计算货盘、报价、队列、AI或物理结算。所有写操作带 `round_token`，旧页面/双击重复提交会被后端拒绝。

## 当前刻意不做

- 第三条航线或全球25 OD；
- 买船、卖船、造船、拆船；
- 航次成本、融资、现金流；
- 原油价格与grade；
- 隐藏AI未来信息；
- 自动替真人Firm；
- 大型地图动画。

这一版唯一目标是验证：**固定船队、两个来源、三轮同时报价和多个简单参与者，是否已经足够生成可重复但不可算死的航运博弈。**
