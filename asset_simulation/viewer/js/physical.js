const MONTHLY_WINDOW = 72;
const SVG_WIDTH = 1100;
const SVG_HEIGHT = 500;
const PLOT = { left: 72, right: 1078, top: 24, bottom: 452 };

const state = {
  seed: 42,
  years: 60,
  initialYear: 2030,
  initialMonth: 1,
  mode: "balance",
  payload: null,
  selectedIndex: 0,
  windowStart: 0,
  chartContext: null,
  dragging: false,
  dragStartX: 0,
  dragWindowStart: 0,
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
  const mode = params.get("view");
  state.mode = ["balance", "inventory", "capacity"].includes(mode) ? mode : "balance";
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

function fmt(value, digits = 1, suffix = "") {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}${suffix}`;
}

function signed(value, digits = 1, suffix = "") {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number >= 0 ? "+" : ""}${fmt(number, digits, suffix)}`;
}

function directionClass(value) {
  return Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "";
}

function detailItem(label, value, tone = "") {
  return `<div class="detail-item"><span>${label}</span><strong class="${tone}">${value}</strong></div>`;
}

function regimeLabel(regime) {
  return ({
    continuation: "延续增长",
    transition_plateau: "转型平台",
    accelerated_transition: "较快转型",
  })[regime] || regime;
}

function setBadge(label, tone = "") {
  $("detailBadge").textContent = label;
  $("detailBadge").className = `direction-badge ${tone}`;
}

function syncControls() {
  $("seedInput").value = state.seed;
  $("yearsInput").value = state.years;
  for (const button of $("mainModeNav").querySelectorAll("button[data-mode]")) {
    button.setAttribute("aria-pressed", String(button.dataset.mode === state.mode));
  }
}

function selectedRow() {
  return state.payload.history[state.selectedIndex];
}

function syncLinksAndUrl() {
  const row = state.payload ? selectedRow() : { year: state.initialYear, month: state.initialMonth };
  const shared = `seed=${state.seed}&years=${state.years}&year=${row.year}&month=${row.month}`;
  history.replaceState(null, "", `/physical?${shared}&view=${state.mode}`);
  $("overviewLink").href = `/?seed=${state.seed}&years=${state.years}&year=${row.year}`;
  $("shippingLink").href = `/shipping?${shared}`;
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

async function loadWorld() {
  syncControls();
  $("statusText").textContent = "正在生成月度总液体物理世界…";
  try {
    const endYear = 2025 + state.years;
    state.payload = await getJson(`/api/oil-shipping?seed=${state.seed}&years=${state.years}&year=${endYear}&month=12`);
    const rows = state.payload.history;
    const targetYear = clamp(state.initialYear, Number(rows[0].year), Number(rows.at(-1).year));
    const found = rows.findIndex((row) => Number(row.year) === targetYear && Number(row.month) === state.initialMonth);
    state.selectedIndex = found >= 0 ? found : 0;
    state.windowStart = clamp(
      state.selectedIndex - MONTHLY_WINDOW + 12,
      0,
      Math.max(0, rows.length - MONTHLY_WINDOW),
    );
    render();
    $("identityText").textContent = `Seed ${state.seed} · ${state.payload.identity.model_version} · ${regimeLabel(state.payload.identity.long_run_demand_regime)}`;
    $("statusText").textContent = `${rows.length.toLocaleString("zh-CN")} 个月度状态 · 质量守恒`;
  } catch (error) {
    $("statusText").textContent = `加载失败：${error.message}`;
  }
}

function activeDefinition(rows) {
  if (state.mode === "inventory") {
    return {
      eyebrow: "INVENTORY · MONTHLY",
      title: "全球商业库存状态",
      series: [
        { label: "库存天数", color: "#53d4dd", fill: true, values: rows.map((row) => Number(row.inventory_days)) },
        { label: "动态目标", color: "#7e91a9", dashed: true, values: rows.map((row) => Number(row.target_inventory_days)) },
      ],
      digits: 0,
    };
  }
  if (state.mode === "capacity") {
    return {
      eyebrow: "PRODUCTION CAPACITY · MONTHLY",
      title: "全球原油产能与利用",
      series: [
        { label: "总液体生产能力", color: "#67a8ff", values: rows.map((row) => Number(row.production_capacity_mbd)) },
        { label: "总液体实际产量", color: "#f3a357", values: rows.map((row) => Number(row.production_mbd)) },
      ],
      digits: 0,
    };
  }
  return {
    eyebrow: "SUPPLY & DEMAND · MONTHLY",
    title: "全球原油供需",
    series: [
      { label: "总液体实际需求", color: "#53d4dd", values: rows.map((row) => Number(row.realized_demand_mbd)) },
      { label: "总液体实际产量", color: "#f3a357", values: rows.map((row) => Number(row.production_mbd)) },
    ],
    digits: 0,
  };
}

function render() {
  syncControls();
  const allRows = state.payload.history;
  const maximumStart = Math.max(0, allRows.length - MONTHLY_WINDOW);
  state.windowStart = clamp(state.windowStart, 0, maximumStart);
  const rows = allRows.slice(state.windowStart, state.windowStart + MONTHLY_WINDOW);
  state.selectedIndex = clamp(state.selectedIndex, state.windowStart, state.windowStart + rows.length - 1);
  const selectedLocalIndex = state.selectedIndex - state.windowStart;
  const definition = activeDefinition(rows);
  $("chartEyebrow").textContent = definition.eyebrow;
  $("chartTitle").textContent = definition.title;
  $("windowLabel").textContent = `${rows[0].label} — ${rows.at(-1).label} · 显示 ${rows.length} 个月`;
  renderChart(rows, definition.series, definition.digits, selectedLocalIndex);
  updateDetails();
  syncLinksAndUrl();
}

function renderChart(rows, series, digits, selectedLocalIndex) {
  const allValues = series.flatMap((item) => item.values);
  let minimum = Math.min(...allValues);
  let maximum = Math.max(...allValues);
  const padding = Math.max((maximum - minimum) * .12, Math.abs(maximum) * .012, .25);
  minimum -= padding;
  maximum += padding;
  const plotWidth = PLOT.right - PLOT.left;
  const plotHeight = PLOT.bottom - PLOT.top;
  const x = (index) => PLOT.left + (rows.length <= 1 ? plotWidth : index / (rows.length - 1) * plotWidth);
  const y = (value) => PLOT.top + (maximum - value) / (maximum - minimum || 1) * plotHeight;
  const path = (values) => values.map((value, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(value).toFixed(2)}`).join(" ");
  const yTicks = Array.from({ length: 6 }, (_, index) => minimum + (maximum - minimum) * index / 5);
  const xIndexes = [...new Set(Array.from({ length: 7 }, (_, index) => Math.round((rows.length - 1) * index / 6)))];
  const grid = yTicks.map((value) => {
    const py = y(value);
    return `<line class="chart-grid" x1="${PLOT.left}" x2="${PLOT.right}" y1="${py}" y2="${py}"/><text class="chart-tick" x="${PLOT.left - 10}" y="${py + 4}" text-anchor="end">${fmt(value, digits)}</text>`;
  }).join("");
  const ticks = xIndexes.map((index) => `<text class="chart-tick" x="${x(index)}" y="${SVG_HEIGHT - 16}" text-anchor="middle">${rows[index].label}</text>`).join("");
  const lines = series.map((item, seriesIndex) => {
    const line = path(item.values);
    const area = item.fill ? `<path class="overview-area" fill="${item.color}" d="${line} L${x(rows.length - 1)},${PLOT.bottom} L${x(0)},${PLOT.bottom} Z"/>` : "";
    const dash = item.dashed ? ' stroke-dasharray="7 7" opacity=".7"' : "";
    return `${area}<path class="overview-line" stroke="${item.color}"${dash} d="${line}"/><circle class="focus-selected-dot" data-series="${seriesIndex}" fill="${item.color}" r="4"/>`;
  }).join("");
  $("focusChart").innerHTML = `<svg viewBox="0 0 ${SVG_WIDTH} ${SVG_HEIGHT}" preserveAspectRatio="none" aria-hidden="true">
    ${grid}${lines}
    <line id="focusCursor" class="focus-cursor" y1="${PLOT.top}" y2="${PLOT.bottom}"/>
    <rect class="focus-hit" x="${PLOT.left}" y="${PLOT.top}" width="${plotWidth}" height="${plotHeight}"/>
    ${ticks}
  </svg>`;
  $("focusLegend").innerHTML = `${series.map((item) => `<span><i style="background:${item.color}"></i>${item.label}</span>`).join("")}<span>单位：${state.mode === "inventory" ? "天" : "mbd"}</span>`;
  state.chartContext = { rows, series, x, y };
  updateChartSelection(selectedLocalIndex);
}

function updateChartSelection(localIndex) {
  const context = state.chartContext;
  if (!context) return;
  const index = clamp(localIndex, 0, context.rows.length - 1);
  const cursorX = context.x(index);
  $("focusCursor").setAttribute("x1", cursorX);
  $("focusCursor").setAttribute("x2", cursorX);
  for (const dot of $("focusChart").querySelectorAll(".focus-selected-dot")) {
    const seriesIndex = Number(dot.dataset.series);
    dot.setAttribute("cx", cursorX);
    dot.setAttribute("cy", context.y(context.series[seriesIndex].values[index]));
  }
}

function updateDetails() {
  const row = selectedRow();
  $("detailTitle").textContent = row.label;
  if (state.mode === "inventory") {
    const change = Number(row.inventory_change_mmbbl);
    setBadge(change > .01 ? "累库" : change < -.01 ? "去库" : "库存平稳", change > .01 ? "up" : change < -.01 ? "down" : "");
    $("focusPrimary").innerHTML = `<span>库存可用天数</span><strong>${fmt(row.inventory_days, 1, " 天")}</strong><small class="${directionClass(change)}">本月 ${signed(change, 2, " 百万桶")}</small>`;
    $("focusDetails").innerHTML = [
      detailItem("期初库存", fmt(row.opening_inventory_mmbbl, 1, " 百万桶")),
      detailItem("期末库存", fmt(row.closing_inventory_mmbbl, 1, " 百万桶")),
      detailItem("库存变化", signed(change, 2, " 百万桶"), directionClass(change)),
      detailItem("动态目标", fmt(row.target_inventory_days, 1, " 天")),
      detailItem("总液体实际需求", fmt(row.realized_demand_mbd, 2, " mbd")),
      detailItem("质量残差", fmt(row.mass_balance_residual_mmbbl, 6, " 百万桶")),
    ].join("");
    $("detailNote").textContent = "期末库存严格等于期初库存加生产、减消费；虚线目标会随库存偏好缓慢漂移，不再固定在58天。";
    return;
  }
  if (state.mode === "capacity") {
    const spare = Number(row.spare_capacity_mbd);
    const utilization = 100 * Number(row.production_mbd) / Math.max(Number(row.production_capacity_mbd), 1e-9);
    setBadge(spare < 2 ? "产能紧张" : spare < 4 ? "余量偏低" : "余量充足", spare < 2 ? "down" : spare >= 4 ? "up" : "");
    $("focusPrimary").innerHTML = `<span>剩余产能</span><strong>${fmt(spare, 2, " mbd")}</strong><small>产能利用率 ${fmt(utilization, 1, "%")}</small>`;
    $("focusDetails").innerHTML = [
      detailItem("总液体生产能力", fmt(row.production_capacity_mbd, 2, " mbd")),
      detailItem("总液体实际产量", fmt(row.production_mbd, 2, " mbd")),
      detailItem("停产冲击", fmt(row.production_outage_mbd, 2, " mbd"), Number(row.production_outage_mbd) ? "negative" : ""),
      detailItem("产能增速目标", signed(row.annual_capacity_growth_target_pct, 2, "%"), directionClass(row.annual_capacity_growth_target_pct)),
      detailItem("需求增速目标", signed(row.annual_demand_growth_target_pct, 2, "%"), directionClass(row.annual_demand_growth_target_pct)),
      detailItem("宏观信息年", String(row.macro_information_year)),
    ].join("");
    $("detailNote").textContent = "生产不能超过当月可用产能；实际产量与生产能力之间的距离就是剩余产能。";
    return;
  }
  const gap = Number(row.production_mbd) - Number(row.realized_demand_mbd);
  setBadge(gap > .02 ? "产量高于需求" : gap < -.02 ? "需求高于产量" : "供需接近", gap > .02 ? "up" : gap < -.02 ? "down" : "");
  $("focusPrimary").innerHTML = `<span>总液体实际需求</span><strong>${fmt(row.realized_demand_mbd, 2, " mbd")}</strong><small class="${directionClass(gap)}">总液体产量差 ${signed(gap, 2, " mbd")}</small>`;
  $("focusDetails").innerHTML = [
    detailItem("总液体实际产量", fmt(row.production_mbd, 2, " mbd")),
    detailItem("总液体期望需求", fmt(row.desired_demand_mbd, 2, " mbd")),
    detailItem("库存变化", signed(row.inventory_change_mmbbl, 2, " 百万桶"), directionClass(row.inventory_change_mmbbl)),
    detailItem("未满足需求", fmt(row.unmet_demand_mmbbl, 2, " 百万桶"), Number(row.unmet_demand_mmbbl) ? "negative" : ""),
    detailItem("需求季节项", signed(row.demand_seasonal_pct, 2, "%"), directionClass(row.demand_seasonal_pct)),
    detailItem("需求消息项", signed(row.demand_news_pct, 2, "%"), directionClass(row.demand_news_pct)),
    detailItem("长期需求路径", regimeLabel(row.long_run_demand_regime)),
    detailItem("结构转型拖累", fmt(row.structural_demand_drag_pct, 2, " 个百分点")),
  ].join("");
  $("detailNote").textContent = "当月产量与需求的差额进入库存；库存耗尽后才会出现未满足需求。";
}

function pointerLocalIndex(event) {
  const rect = $("focusChart").getBoundingClientRect();
  const svgX = (event.clientX - rect.left) / rect.width * SVG_WIDTH;
  const fraction = clamp((svgX - PLOT.left) / (PLOT.right - PLOT.left), 0, 1);
  return Math.round(fraction * (state.chartContext.rows.length - 1));
}

function selectLocalIndex(localIndex) {
  const index = clamp(localIndex, 0, state.chartContext.rows.length - 1);
  state.selectedIndex = state.windowStart + index;
  updateChartSelection(index);
  updateDetails();
  syncLinksAndUrl();
}

$("focusChart").addEventListener("pointerdown", (event) => {
  state.dragging = true;
  state.dragStartX = event.clientX;
  state.dragWindowStart = state.windowStart;
  $("focusChart").classList.add("is-dragging");
  $("focusChart").setPointerCapture(event.pointerId);
});

$("focusChart").addEventListener("pointermove", (event) => {
  if (!state.chartContext) return;
  if (state.dragging) {
    const rect = $("focusChart").getBoundingClientRect();
    const pixelsPerPoint = rect.width * (PLOT.right - PLOT.left) / SVG_WIDTH / (MONTHLY_WINDOW - 1);
    const shift = Math.round((state.dragStartX - event.clientX) / Math.max(1, pixelsPerPoint));
    const nextStart = clamp(
      state.dragWindowStart + shift,
      0,
      Math.max(0, state.payload.history.length - MONTHLY_WINDOW),
    );
    if (nextStart !== state.windowStart) {
      state.windowStart = nextStart;
      state.selectedIndex = clamp(state.selectedIndex, nextStart, nextStart + MONTHLY_WINDOW - 1);
      render();
    }
    return;
  }
  selectLocalIndex(pointerLocalIndex(event));
});

function endDrag(event) {
  if (!state.dragging) return;
  state.dragging = false;
  $("focusChart").classList.remove("is-dragging");
  if ($("focusChart").hasPointerCapture(event.pointerId)) $("focusChart").releasePointerCapture(event.pointerId);
}

$("focusChart").addEventListener("pointerup", endDrag);
$("focusChart").addEventListener("pointercancel", endDrag);

$("mainModeNav").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-mode]");
  if (!button) return;
  state.mode = button.dataset.mode;
  render();
});

$("physicalForm").addEventListener("submit", (event) => {
  event.preventDefault();
  state.seed = Math.max(0, Math.trunc(Number($("seedInput").value) || 42));
  state.years = Math.min(90, Math.max(5, Math.trunc(Number($("yearsInput").value) || 60)));
  state.initialYear = 2030;
  state.initialMonth = 1;
  loadWorld();
});

readUrl();
syncControls();
syncLinksAndUrl();
loadWorld();
