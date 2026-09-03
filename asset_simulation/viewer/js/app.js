const MONTHLY_WINDOW = 72;
const SVG_WIDTH = 1100;
const SVG_HEIGHT = 500;
const PLOT = { left: 78, right: 1078, top: 24, bottom: 452 };

const state = {
  seed: 42, years: 60, initialYear: 2030, initialMonth: 1,
  mode: "overview", metric: "cargo", regionView: "balance",
  selectedRegionId: "gulf", selectedRouteId: "gulf_east_asia",
  payload: null, selectedIndex: 0, windowStart: 0, chartContext: null,
  dragging: false, dragStartX: 0, dragWindowStart: 0,
};

const $ = (id) => document.getElementById(id);

function numberParam(params, name, fallback) {
  const value = Number(params.get(name));
  return Number.isFinite(value) ? value : fallback;
}

function readUrl() {
  const params = new URLSearchParams(location.search);
  state.seed = Math.max(0, Math.trunc(numberParam(params, "seed", 42)));
  state.years = Math.min(90, Math.max(5, Math.trunc(numberParam(params, "years", 60))));
  state.initialYear = Math.trunc(numberParam(params, "year", 2030));
  state.initialMonth = Math.min(12, Math.max(1, Math.trunc(numberParam(params, "month", 1))));
  state.mode = ["overview", "regions", "routes"].includes(params.get("view")) ? params.get("view") : "overview";
  state.metric = ["cargo", "tonneMiles", "haul"].includes(params.get("metric")) ? params.get("metric") : "cargo";
  state.regionView = params.get("regionView") === "fundamentals" ? "fundamentals" : "balance";
  state.selectedRegionId = params.get("region") || "gulf";
  state.selectedRouteId = params.get("route") || "gulf_east_asia";
}

function clamp(value, low, high) { return Math.min(high, Math.max(low, value)); }

function fmt(value, digits = 1, suffix = "") {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}${suffix}`;
}

function signed(value, digits = 1, suffix = "") {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number >= 0 ? "+" : ""}${fmt(number, digits, suffix)}`;
}

function pctChange(current, previous) {
  const base = Number(previous);
  return Number.isFinite(base) && Math.abs(base) > 1e-12 ? (Number(current) / base - 1) * 100 : null;
}

function directionClass(value) { return Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : ""; }
function detailItem(label, value, tone = "") { return `<div class="detail-item"><span>${label}</span><strong class="${tone}">${value}</strong></div>`; }

function setBadge(label, tone = "") {
  $("detailBadge").textContent = label;
  $("detailBadge").className = `direction-badge ${tone}`;
}

function selectedRow() { return state.payload.history[state.selectedIndex]; }
function routeFor(row, routeId = state.selectedRouteId) { return row.routes.find((route) => route.route_id === routeId) || row.routes[0]; }
function regionFor(row, regionId = state.selectedRegionId) { return row.regional_balances.find((region) => region.region_id === regionId) || row.regional_balances[0]; }

function syncControls() {
  $("seedInput").value = state.seed;
  $("yearsInput").value = state.years;
  for (const button of $("mainModeNav").querySelectorAll("button[data-mode]")) button.setAttribute("aria-pressed", String(button.dataset.mode === state.mode));
  for (const button of $("metricNav").querySelectorAll("button[data-metric]")) button.setAttribute("aria-pressed", String(button.dataset.metric === state.metric));
  for (const button of $("regionViewNav").querySelectorAll("button[data-region-view]")) button.setAttribute("aria-pressed", String(button.dataset.regionView === state.regionView));
  $("metricNav").hidden = state.mode === "regions";
  $("regionViewNav").hidden = state.mode !== "regions";
  $("routeBoard").hidden = state.mode !== "routes";
  $("regionBoard").hidden = state.mode !== "regions";
}

function syncLinksAndUrl() {
  const row = state.payload ? selectedRow() : { year: state.initialYear, month: state.initialMonth };
  const shared = `seed=${state.seed}&years=${state.years}&year=${row.year}&month=${row.month}`;
  history.replaceState(null, "", `/shipping?${shared}&view=${state.mode}&metric=${state.metric}&regionView=${state.regionView}&region=${encodeURIComponent(state.selectedRegionId)}&route=${encodeURIComponent(state.selectedRouteId)}`);
  $("overviewLink").href = `/?seed=${state.seed}&years=${state.years}&year=${row.year}`;
  $("physicalLink").href = `/physical?${shared}`;
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

async function loadWorld() {
  syncControls();
  $("statusText").textContent = "正在生成区域物理平衡与航线…";
  try {
    const endYear = 2025 + state.years;
    state.payload = await getJson(`/api/oil-shipping?seed=${state.seed}&years=${state.years}&year=${endYear}&month=12`);
    const rows = state.payload.history;
    if (!rows[0].routes.some((route) => route.route_id === state.selectedRouteId)) state.selectedRouteId = rows[0].routes[0].route_id;
    if (!rows[0].regional_balances.some((region) => region.region_id === state.selectedRegionId)) state.selectedRegionId = rows[0].regional_balances[0].region_id;
    const targetYear = clamp(state.initialYear, Number(rows[0].year), Number(rows.at(-1).year));
    const found = rows.findIndex((row) => Number(row.year) === targetYear && Number(row.month) === state.initialMonth);
    state.selectedIndex = found >= 0 ? found : 0;
    state.windowStart = clamp(state.selectedIndex - MONTHLY_WINDOW + 12, 0, Math.max(0, rows.length - MONTHLY_WINDOW));
    render();
    $("identityText").textContent = `Seed ${state.seed} · ${state.payload.identity.model_version} · 区域物理差额生成货量`;
    $("statusText").textContent = `${rows.length.toLocaleString("zh-CN")} 个月度状态 · 10个区域 · 14条航线状况参考 + 其他航线池 · 13节基准航程 · 尚无运价`;
  } catch (error) {
    $("statusText").textContent = `加载失败：${error.message}`;
  }
}

function activeDefinition(rows) {
  if (state.mode === "regions") {
    const selected = regionFor(selectedRow());
    if (state.regionView === "fundamentals") {
      return {
        eyebrow: "REGIONAL CRUDE PRODUCTION / RUNS · MONTHLY", title: `${selected.region_name} · 原油产量与炼厂原油加工`, unit: "百万桶/日", digits: 2, zeroBased: false,
        series: [
          { label: "区域原油产量", color: "#41d39a", fill: false, values: rows.map((row) => Number(regionFor(row).crude_production_mbd)) },
          { label: "炼厂原油加工量", color: "#f3a357", fill: false, values: rows.map((row) => Number(regionFor(row).crude_refinery_runs_mbd)) },
        ],
      };
    }
    return {
      eyebrow: "NET SEABORNE BALANCE · MONTHLY", title: `${selected.region_name} · 海运净平衡`, unit: "百万桶/日（正值出口／负值进口）", digits: 2, zeroBased: false, includeZero: false,
      series: [{ label: "海运净平衡", color: "#53d4dd", fill: true, values: rows.map((row) => Number(regionFor(row).net_seaborne_balance_mbd)) }],
    };
  }
  if (state.mode === "routes") {
    const selected = routeFor(selectedRow());
    const definitions = {
      tonneMiles: ["ROUTE TONNE-MILES · MONTHLY", `${selected.route_name} · 吨海里`, "十亿吨海里/年", 0, "#53d4dd", "年化吨海里", "annualized_tonne_nautical_miles_billion"],
      haul: ["EFFECTIVE HAUL · MONTHLY", `${selected.route_name} · 有效航程`, "海里", 0, "#67a8ff", "有效航程", "effective_haul_nm"],
      cargo: ["ROUTE CARGO · MONTHLY", `${selected.route_name} · 货量`, "百万桶/日", 2, "#f3a357", "航线货量", "cargo_mbd"],
    };
    const [eyebrow, title, unit, digits, color, label, field] = definitions[state.metric];
    return { eyebrow, title, unit, digits, zeroBased: false, series: [{ label, color, fill: true, values: rows.map((row) => Number(routeFor(row)[field])) }] };
  }
  const definitions = {
    tonneMiles: ["TONNE-MILE DEMAND · MONTHLY", "原油吨海里需求", "十亿吨海里/年", 0, "#53d4dd", "年化吨海里需求", "annualized_tonne_nautical_miles_billion"],
    haul: ["WEIGHTED HAUL · MONTHLY", "加权平均航程", "海里", 0, "#67a8ff", "加权平均航程", "average_haul_nm"],
    cargo: ["SEABORNE CARGO · MONTHLY", "海运原油流量", "百万桶/日", 1, "#f3a357", "海运原油流量", "seaborne_cargo_mbd"],
  };
  const [eyebrow, title, unit, digits, color, label, field] = definitions[state.metric];
  return { eyebrow, title, unit, digits, zeroBased: false, series: [{ label, color, fill: true, values: rows.map((row) => Number(row[field])) }] };
}

function render() {
  syncControls();
  const allRows = state.payload.history;
  state.windowStart = clamp(state.windowStart, 0, Math.max(0, allRows.length - MONTHLY_WINDOW));
  const rows = allRows.slice(state.windowStart, state.windowStart + MONTHLY_WINDOW);
  state.selectedIndex = clamp(state.selectedIndex, state.windowStart, state.windowStart + rows.length - 1);
  const definition = activeDefinition(rows);
  $("chartEyebrow").textContent = definition.eyebrow;
  $("chartTitle").textContent = definition.title;
  $("windowLabel").textContent = `${rows[0].label} — ${rows.at(-1).label} · 显示 ${rows.length} 个月`;
  renderChart(rows, definition, state.selectedIndex - state.windowStart);
  updateDetails();
  syncLinksAndUrl();
}

function renderChart(rows, definition, selectedLocalIndex) {
  const allValues = definition.series.flatMap((series) => series.values);
  let minimum = definition.zeroBased ? 0 : Math.min(...allValues);
  let maximum = Math.max(...allValues);
  if (definition.includeZero) { minimum = Math.min(0, minimum); maximum = Math.max(0, maximum); }
  const padding = Math.max(
    (maximum - minimum) * 0.12,
    Math.abs(maximum) * 0.012,
    definition.includeZero || definition.zeroBased ? 0.25 : 0.04,
  );
  if (!definition.zeroBased) minimum -= padding;
  maximum += padding;
  const plotWidth = PLOT.right - PLOT.left;
  const plotHeight = PLOT.bottom - PLOT.top;
  const x = (index) => PLOT.left + (rows.length <= 1 ? plotWidth : index / (rows.length - 1) * plotWidth);
  const y = (value) => PLOT.top + (maximum - value) / (maximum - minimum || 1) * plotHeight;
  const pathFor = (values) => values.map((value, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(value).toFixed(2)}`).join(" ");
  const yTicks = Array.from({ length: 6 }, (_, index) => minimum + (maximum - minimum) * index / 5);
  const xIndexes = [...new Set(Array.from({ length: 7 }, (_, index) => Math.round((rows.length - 1) * index / 6)))];
  const grid = yTicks.map((value) => `<line class="chart-grid" x1="${PLOT.left}" x2="${PLOT.right}" y1="${y(value)}" y2="${y(value)}"/><text class="chart-tick" x="${PLOT.left - 10}" y="${y(value) + 4}" text-anchor="end">${fmt(value, definition.digits)}</text>`).join("");
  const zeroLine = minimum < 0 && maximum > 0 ? `<line class="chart-zero" x1="${PLOT.left}" x2="${PLOT.right}" y1="${y(0)}" y2="${y(0)}"/>` : "";
  const ticks = xIndexes.map((index) => `<text class="chart-tick" x="${x(index)}" y="${SVG_HEIGHT - 16}" text-anchor="middle">${rows[index].label}</text>`).join("");
  const paths = definition.series.map((series) => {
    const path = pathFor(series.values);
    const floor = y(definition.includeZero ? 0 : minimum);
    const area = series.fill ? `<path class="overview-area" fill="${series.color}" d="${path} L${x(rows.length - 1)},${floor} L${x(0)},${floor} Z"/>` : "";
    return `${area}<path class="overview-line" stroke="${series.color}" d="${path}"/>`;
  }).join("");
  const primary = definition.series[0];
  $("focusChart").innerHTML = `<svg viewBox="0 0 ${SVG_WIDTH} ${SVG_HEIGHT}" preserveAspectRatio="none" aria-hidden="true">${grid}${zeroLine}${paths}<circle class="focus-selected-dot" fill="${primary.color}" r="4"/><line id="focusCursor" class="focus-cursor" y1="${PLOT.top}" y2="${PLOT.bottom}"/><rect class="focus-hit" x="${PLOT.left}" y="${PLOT.top}" width="${plotWidth}" height="${plotHeight}"/>${ticks}</svg>`;
  $("focusLegend").innerHTML = `${definition.series.map((series) => `<span><i style="background:${series.color}"></i>${series.label}</span>`).join("")}<span>单位：${definition.unit}</span>`;
  state.chartContext = { rows, x, y, values: primary.values };
  updateChartSelection(selectedLocalIndex);
}

function updateChartSelection(localIndex) {
  if (!state.chartContext) return;
  const index = clamp(localIndex, 0, state.chartContext.rows.length - 1);
  const cursorX = state.chartContext.x(index);
  $("focusCursor").setAttribute("x1", cursorX);
  $("focusCursor").setAttribute("x2", cursorX);
  const dot = $("focusChart").querySelector(".focus-selected-dot");
  dot.setAttribute("cx", cursorX);
  dot.setAttribute("cy", state.chartContext.y(state.chartContext.values[index]));
}

function renderChain(row) {
  const imbalance = Math.max(Number(row.regional_export_supply_mbd), Number(row.regional_import_requirement_mbd));
  $("shippingChain").innerHTML = [
    ["01", "全球原油物理池", fmt(row.crude_production_mbd, 1, " 百万桶/日"), `炼厂原油加工 ${fmt(row.crude_refinery_runs_mbd, 1, " 百万桶/日")}`],
    ["02", "区域原油海运差额", fmt(imbalance, 1, " 百万桶/日"), `原油库存变化 ${signed(row.crude_inventory_change_mmbbl, 1, " 百万桶")}`],
    ["03", "航线货量", fmt(row.seaborne_cargo_mbd, 1, " 百万桶/日"), `加权航程 ${fmt(row.average_haul_nm, 0, " 海里")}`],
    ["04", "吨海里需求", fmt(row.annualized_tonne_nautical_miles_billion, 0, " 十亿吨海里/年"), "各航线货量 × 有效航程"],
  ].map(([step, label, value, note], index) => `<div class="shipping-chain-node"><span>${step}</span><div><small>${label}</small><strong>${value}</strong><em>${note}</em></div></div>${index < 3 ? '<i class="shipping-chain-arrow">→</i>' : ""}`).join("");
}

function renderRouteList() {
  if (state.mode !== "routes") return;
  $("routeList").innerHTML = `<div class="route-row route-row-labels"><span>航线</span><span>货量（百万桶/日）</span><span>份额</span><span>基准／有效航程（海里）</span><span>年化吨海里（十亿）</span><span>状态</span></div>${selectedRow().routes.map((route) => `<button type="button" class="route-row ${route.route_id === state.selectedRouteId ? "is-selected" : ""}" data-route-id="${route.route_id}"><strong>${route.route_name}</strong><span>${fmt(route.cargo_mbd, 2, " 百万桶/日")}</span><span>${fmt(Number(route.market_share) * 100, 1, "%")}</span><span>${fmt(route.baseline_haul_nm, 0)} / ${fmt(route.effective_haul_nm, 0)}</span><span>${fmt(route.annualized_tonne_nautical_miles_billion, 0)}</span><em>${route.route_status === "rerouted" ? "绕行" : route.route_status === "shortened" ? "缩短" : "正常"}</em></button>`).join("")}`;
}

function renderRegionList() {
  if (state.mode !== "regions") return;
  $("regionList").innerHTML = `<div class="route-row region-row route-row-labels"><span>区域</span><span>角色</span><span>原油产量</span><span>炼厂原油加工</span><span>原油管道净出口</span><span>海运净差额</span></div>${selectedRow().regional_balances.map((region) => `<button type="button" class="route-row region-row ${region.region_id === state.selectedRegionId ? "is-selected" : ""}" data-region-id="${region.region_id}"><strong>${region.region_name}</strong><span>${Number(region.net_seaborne_balance_mbd) >= 0 ? "出口区" : "进口区"}</span><span>${fmt(region.crude_production_mbd, 2, " 百万桶/日")}</span><span>${fmt(region.crude_refinery_runs_mbd, 2, " 百万桶/日")}</span><span>${signed(region.crude_pipeline_net_exports_mbd, 2, " 百万桶/日")}</span><em class="${directionClass(region.net_seaborne_balance_mbd)}">${signed(region.net_seaborne_balance_mbd, 2, " 百万桶/日")}</em></button>`).join("")}`;
}

function updateDetails() {
  const row = selectedRow();
  const previous = state.selectedIndex > 0 ? state.payload.history[state.selectedIndex - 1] : null;
  $("detailTitle").textContent = row.label;
  renderChain(row); renderRouteList(); renderRegionList();
  if (state.mode === "regions") {
    const region = regionFor(row);
    const previousRegion = previous ? regionFor(previous) : null;
    const change = previousRegion ? Number(region.net_seaborne_balance_mbd) - Number(previousRegion.net_seaborne_balance_mbd) : null;
    const exporter = Number(region.net_seaborne_balance_mbd) >= 0;
    $("detailEyebrow").textContent = "REGIONAL PHYSICAL BALANCE";
    setBadge(exporter ? "海运盈余" : "海运缺口", exporter ? "up" : "down");
    $("focusPrimary").innerHTML = `<span>${region.region_name} · 海运净平衡</span><strong>${signed(region.net_seaborne_balance_mbd, 2, " 百万桶/日")}</strong><small class="${directionClass(change)}">${change == null ? "月度路径起点" : `环比变化 ${signed(change, 2, " 百万桶/日")}`}</small>`;
    $("focusDetails").innerHTML = [detailItem("基础原油产量", fmt(region.base_crude_production_mbd, 2, " 百万桶/日")), detailItem("自身政策覆盖", signed(region.production_policy_adjustment_mbd, 2, " 百万桶/日")), detailItem("自身生产周期", signed(region.production_cycle_adjustment_mbd, 2, " 百万桶/日")), detailItem("无约束产量", fmt(region.unconstrained_crude_production_mbd, 2, " 百万桶/日")), detailItem("跨区守恒调整", signed(region.conservation_adjustment_mbd, 2, " 百万桶/日")), detailItem("最终原油产量", fmt(region.crude_production_mbd, 2, " 百万桶/日")), detailItem("产量有效总调整", signed(region.effective_production_adjustment_mbd, 2, " 百万桶/日")), detailItem("基础炼厂加工", fmt(region.base_crude_refinery_runs_mbd, 2, " 百万桶/日")), detailItem("自身炼厂周期", signed(region.refinery_cycle_adjustment_mbd, 2, " 百万桶/日")), detailItem("炼厂跨区守恒", signed(region.refinery_conservation_adjustment_mbd, 2, " 百万桶/日")), detailItem("最终炼厂加工", fmt(region.crude_refinery_runs_mbd, 2, " 百万桶/日")), detailItem("炼厂有效总调整", signed(region.effective_refinery_adjustment_mbd, 2, " 百万桶/日")), detailItem("原油库存变化", signed(region.crude_inventory_change_mmbbl, 2, " 百万桶")), detailItem("原油管道净出口", signed(region.crude_pipeline_net_exports_mbd, 2, " 百万桶/日")), detailItem("贸易角色", exporter ? "海运出口区" : "海运进口区")].join("");
    $("detailNote").textContent = "自身政策或运营覆盖完整进入本区无约束目标；反向守恒量只按物理份额分散给其他区域，航线与吨海里不回写上游。海运净平衡仍等于最终原油产量 − 最终炼厂加工 − 原油库存变化（日率）− 原油管道净出口。";
    return;
  }
  if (state.mode === "routes") {
    const route = routeFor(row);
    const previousRoute = previous ? routeFor(previous) : null;
    const fields = { cargo: "cargo_mbd", tonneMiles: "annualized_tonne_nautical_miles_billion", haul: "effective_haul_nm" };
    const suffixes = { cargo: " 百万桶/日", tonneMiles: " 十亿吨海里/年", haul: " 海里" };
    const digits = state.metric === "cargo" ? 2 : 0;
    const value = Number(route[fields[state.metric]]);
    const change = previousRoute ? pctChange(value, previousRoute[fields[state.metric]]) : null;
    $("detailEyebrow").textContent = "ROUTE STATUS REFERENCE";
    setBadge(route.route_status === "rerouted" ? "绕行" : route.route_status === "shortened" ? "航程缩短" : "正常航线", route.route_status === "rerouted" ? "down" : "");
    $("focusPrimary").innerHTML = `<span>${route.route_name}</span><strong>${fmt(value, digits, suffixes[state.metric])}</strong><small class="${directionClass(change)}">${change == null ? "月度路径起点" : `环比 ${signed(change, 2, "%")}`}</small>`;
    $("focusDetails").innerHTML = [detailItem("全球份额", fmt(Number(route.market_share) * 100, 2, "%")), detailItem(`${route.reference_year}参考货量`, fmt(route.reference_cargo_mbd, 2, " 百万桶/日")), detailItem("边际缩放参考", fmt(route.margin_scaled_reference_mbd, 2, " 百万桶/日")), detailItem("相对参考", signed(route.cargo_vs_reference_pct, 1, "%")), detailItem("本月货物", fmt(route.cargo_million_tonnes, 2, " 百万吨")), detailItem("基准航程", fmt(route.baseline_haul_nm, 0, " 海里")), detailItem(`${fmt(route.planning_speed_knots, 0)}节纯海上天数`, fmt(route.baseline_sea_days, 1, " 天")), detailItem("有效航程", fmt(route.effective_haul_nm, 0, " 海里")), detailItem("本月吨海里", fmt(route.tonne_nautical_miles_billion, 1, " 十亿吨海里")), detailItem("经过节点", route.chokepoints.join(" · "))].join("");
    $("detailNote").textContent = route.is_other_pool ? "其他航线池聚合11条未单列的小额区域联系；它仍参与相同的货量、航程与吨海里守恒。13节天数仅为纯海上航行参考，不含港口、排队和装卸。" : "2024参考货量校准IPF内部流向，但不会覆盖当月区域出口与进口边际；基准航程采用盆地平均海里，13节天数不含港口、排队和装卸。";
    return;
  }
  const fields = { cargo: "seaborne_cargo_mbd", tonneMiles: "annualized_tonne_nautical_miles_billion", haul: "average_haul_nm" };
  const labels = { cargo: "海运原油流量", tonneMiles: "年化吨海里需求", haul: "加权平均航程" };
  const suffixes = { cargo: " 百万桶/日", tonneMiles: " 十亿吨海里/年", haul: " 海里" };
  const digits = state.metric === "cargo" ? 2 : 0;
  const value = Number(row[fields[state.metric]]);
  const change = previous ? pctChange(value, previous[fields[state.metric]]) : null;
  $("detailEyebrow").textContent = "GLOBAL OVERVIEW";
  setBadge(change == null ? "月度起点" : change >= 0 ? "指标上升" : "指标下降", change == null ? "" : change >= 0 ? "up" : "down");
  $("focusPrimary").innerHTML = `<span>${labels[state.metric]}</span><strong>${fmt(value, digits, suffixes[state.metric])}</strong><small class="${directionClass(change)}">${change == null ? "月度路径起点" : `环比 ${signed(change, 2, "%")}`}</small>`;
  $("focusDetails").innerHTML = [detailItem("出口供给", fmt(row.regional_export_supply_mbd, 2, " 百万桶/日")), detailItem("进口需求", fmt(row.regional_import_requirement_mbd, 2, " 百万桶/日")), detailItem("本月货物", fmt(row.cargo_million_tonnes, 2, " 百万吨")), detailItem("平均航程", fmt(row.average_haul_nm, 0, " 海里")), detailItem("年化吨海里", fmt(row.annualized_tonne_nautical_miles_billion, 0, " 十亿吨海里/年")), detailItem("航线网络", `${row.explicit_route_count} 条状况参考 + 其他池`)].join("");
  $("detailNote").textContent = "海运货量来自 crude-only 区域物理盈余与缺口，不使用固定海运比例，也不把全球总液体消费按地区拆成原油进口需求。";
}

function pointerLocalIndex(event) {
  const rect = $("focusChart").getBoundingClientRect();
  const svgX = (event.clientX - rect.left) / rect.width * SVG_WIDTH;
  return Math.round(clamp((svgX - PLOT.left) / (PLOT.right - PLOT.left), 0, 1) * (state.chartContext.rows.length - 1));
}

function selectLocalIndex(localIndex) {
  const index = clamp(localIndex, 0, state.chartContext.rows.length - 1);
  state.selectedIndex = state.windowStart + index;
  updateChartSelection(index); updateDetails(); syncLinksAndUrl();
}

$("focusChart").addEventListener("pointerdown", (event) => {
  state.dragging = true; state.dragStartX = event.clientX; state.dragWindowStart = state.windowStart;
  $("focusChart").classList.add("is-dragging"); $("focusChart").setPointerCapture(event.pointerId);
});

$("focusChart").addEventListener("pointermove", (event) => {
  if (!state.chartContext) return;
  if (state.dragging) {
    const rect = $("focusChart").getBoundingClientRect();
    const pixelsPerPoint = rect.width * (PLOT.right - PLOT.left) / SVG_WIDTH / (MONTHLY_WINDOW - 1);
    const nextStart = clamp(state.dragWindowStart + Math.round((state.dragStartX - event.clientX) / Math.max(1, pixelsPerPoint)), 0, Math.max(0, state.payload.history.length - MONTHLY_WINDOW));
    if (nextStart !== state.windowStart) { state.windowStart = nextStart; state.selectedIndex = clamp(state.selectedIndex, nextStart, nextStart + MONTHLY_WINDOW - 1); render(); }
    return;
  }
  selectLocalIndex(pointerLocalIndex(event));
});

function endDrag(event) {
  if (!state.dragging) return;
  state.dragging = false; $("focusChart").classList.remove("is-dragging");
  if ($("focusChart").hasPointerCapture(event.pointerId)) $("focusChart").releasePointerCapture(event.pointerId);
}

$("focusChart").addEventListener("pointerup", endDrag);
$("focusChart").addEventListener("pointercancel", endDrag);
$("mainModeNav").addEventListener("click", (event) => { const button = event.target.closest("button[data-mode]"); if (button) { state.mode = button.dataset.mode; render(); } });
$("metricNav").addEventListener("click", (event) => { const button = event.target.closest("button[data-metric]"); if (button) { state.metric = button.dataset.metric; render(); } });
$("regionViewNav").addEventListener("click", (event) => { const button = event.target.closest("button[data-region-view]"); if (button) { state.regionView = button.dataset.regionView; render(); } });
$("routeList").addEventListener("click", (event) => { const button = event.target.closest("button[data-route-id]"); if (button) { state.selectedRouteId = button.dataset.routeId; render(); } });
$("regionList").addEventListener("click", (event) => { const button = event.target.closest("button[data-region-id]"); if (button) { state.selectedRegionId = button.dataset.regionId; render(); } });

$("shippingForm").addEventListener("submit", (event) => {
  event.preventDefault();
  state.seed = Math.max(0, Math.trunc(Number($("seedInput").value) || 42));
  state.years = Math.min(90, Math.max(5, Math.trunc(Number($("yearsInput").value) || 60)));
  state.initialYear = 2030; state.initialMonth = 1; loadWorld();
});

readUrl(); syncControls(); syncLinksAndUrl(); loadWorld();
