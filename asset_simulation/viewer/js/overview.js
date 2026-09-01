const MONTHLY_WINDOW = 72;
const SVG_WIDTH = 1000;
const SVG_HEIGHT = 500;
const PLOT = { left: 68, right: 978, top: 25, bottom: 452 };

const state = {
  seed: 42,
  years: 60,
  mode: "macro",
  oilPeriod: "annual",
  initialYear: 2030,
  selectedMacroIndex: 5,
  selectedAnnualIndex: 5,
  selectedMonthlyIndex: 71,
  monthlyWindowStart: 0,
  macro: null,
  oil: null,
  chartContext: null,
  dragging: false,
  dragStartX: 0,
  dragWindowStart: 0,
};

const $ = (id) => document.getElementById(id);

function numericParam(params, key, fallback) {
  const value = Number(params.get(key));
  return Number.isFinite(value) ? value : fallback;
}

function readUrl() {
  const params = new URLSearchParams(location.search);
  state.seed = Math.max(0, Math.trunc(numericParam(params, "seed", 42)));
  state.years = Math.min(90, Math.max(5, Math.trunc(numericParam(params, "years", 60))));
  state.initialYear = Math.trunc(numericParam(params, "year", 2030));
  state.mode = params.get("view") === "oil" ? "oil" : "macro";
  state.oilPeriod = params.get("priceView") === "monthly" ? "monthly" : "annual";
}

function writeUrl() {
  const params = new URLSearchParams({
    seed: String(state.seed),
    years: String(state.years),
    view: state.mode,
  });
  if (state.mode === "oil") params.set("priceView", state.oilPeriod);
  history.replaceState(null, "", `/?${params}`);
}

function syncControls() {
  $("seedInput").value = state.seed;
  $("yearsInput").value = state.years;
  for (const button of $("mainModeNav").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.mode === state.mode));
  }
  for (const button of $("oilPeriodNav").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.period === state.oilPeriod));
  }
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

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

async function loadWorld() {
  syncControls();
  writeUrl();
  $("overviewStatus").textContent = "正在生成年度世界与原油月线…";
  try {
    [state.macro, state.oil] = await Promise.all([
      getJson(`/api/global?seed=${state.seed}&years=${state.years}`),
      getJson(`/api/oil-price?seed=${state.seed}&years=${state.years}`),
    ]);
    initializeSelections();
    renderActiveView();
    $("overviewIdentity").textContent = `Seed ${state.seed} · 全球宏观年度结算 · ${state.oil.identity.model_version}`;
    $("overviewStatus").textContent = `${state.macro.globalMacroSnapshots.length} 个年度锚 · ${state.oil.monthly.length} 个月度价格`;
  } catch (error) {
    $("overviewStatus").textContent = `加载失败：${error.message}`;
  }
}

function initializeSelections() {
  const macroRows = state.macro.globalMacroSnapshots;
  const targetYear = clamp(state.initialYear, Number(macroRows[0].year), Number(macroRows.at(-1).year));
  state.selectedMacroIndex = Math.max(0, macroRows.findIndex((row) => Number(row.year) === targetYear));
  state.selectedAnnualIndex = Math.max(0, state.oil.annual.findIndex((row) => Number(row.year) === targetYear));
  const targetMonthly = state.oil.monthly.findIndex((row) => Number(row.year) === targetYear && Number(row.month) === 12);
  state.selectedMonthlyIndex = targetMonthly >= 0 ? targetMonthly : 0;
  state.monthlyWindowStart = clamp(
    state.selectedMonthlyIndex - MONTHLY_WINDOW + 1,
    0,
    Math.max(0, state.oil.monthly.length - MONTHLY_WINDOW),
  );
}

function renderActiveView() {
  syncControls();
  writeUrl();
  $("oilPeriodNav").hidden = state.mode !== "oil";
  $("focusChart").classList.toggle("is-pannable", state.mode === "oil" && state.oilPeriod === "monthly");
  if (state.mode === "macro") renderMacroView();
  else if (state.oilPeriod === "annual") renderAnnualOilView();
  else renderMonthlyOilView();
}

function renderMacroView() {
  const rows = state.macro.globalMacroSnapshots;
  $("chartEyebrow").textContent = `ANNUAL MACRO · ${rows[0].year}—${rows.at(-1).year}`;
  $("chartTitle").textContent = "全球宏观年度路径";
  $("interactionHint").textContent = "在图上移动鼠标查看每一年";
  $("windowLabel").textContent = "年度数据 · 鼠标悬停定位";
  const series = [
    {
      label: "实际增长",
      color: "#67a8ff",
      values: rows.map((row) => row.realized_growth_pct == null ? 2.35 : Number(row.realized_growth_pct)),
    },
    { label: "通胀", color: "#f3a357", values: rows.map((row) => Number(row.headline_inflation_pct)) },
    { label: "政策利率", color: "#53d4dd", values: rows.map((row) => Number(row.global_policy_rate_pct)) },
  ];
  renderChart(rows, series, {
    selectedLocalIndex: state.selectedMacroIndex,
    label: (row) => String(row.year),
    digits: 1,
    includeZero: true,
    globalStart: 0,
  });
  updateMacroDetails();
}

function renderAnnualOilView() {
  const rows = state.oil.annual;
  $("chartEyebrow").textContent = `CRUDE OIL · ANNUAL · ${rows[0].year}—${rows.at(-1).year}`;
  $("chartTitle").textContent = "原油年度价格路径";
  $("interactionHint").textContent = "在图上移动鼠标查看每一年";
  $("windowLabel").textContent = "单位：美元／桶 · 鼠标悬停定位";
  renderChart(rows, [{
    label: "年度收盘价",
    color: "#f3a357",
    fill: true,
    values: rows.map((row) => Number(row.close_usd_per_bbl)),
  }], {
    selectedLocalIndex: state.selectedAnnualIndex,
    label: (row) => String(row.year),
    digits: 0,
    includeZero: false,
    globalStart: 0,
  });
  updateAnnualOilDetails();
}

function renderMonthlyOilView() {
  const allRows = state.oil.monthly;
  const maximumStart = Math.max(0, allRows.length - MONTHLY_WINDOW);
  state.monthlyWindowStart = clamp(state.monthlyWindowStart, 0, maximumStart);
  const rows = allRows.slice(state.monthlyWindowStart, state.monthlyWindowStart + MONTHLY_WINDOW);
  state.selectedMonthlyIndex = clamp(
    state.selectedMonthlyIndex,
    state.monthlyWindowStart,
    state.monthlyWindowStart + rows.length - 1,
  );
  const selectedLocalIndex = state.selectedMonthlyIndex - state.monthlyWindowStart;
  $("chartEyebrow").textContent = "CRUDE OIL · MONTHLY";
  $("chartTitle").textContent = "原油月度价格路径";
  $("interactionHint").textContent = "按住图表左右拖动；移动鼠标查看月份";
  $("windowLabel").textContent = `${rows[0].label} — ${rows.at(-1).label} · 显示 ${rows.length} 个月`;
  renderChart(rows, [{
    label: "月度收盘价",
    color: "#f3a357",
    fill: true,
    values: rows.map((row) => Number(row.close_usd_per_bbl)),
  }], {
    selectedLocalIndex,
    label: (row) => row.label,
    digits: 0,
    includeZero: false,
    globalStart: state.monthlyWindowStart,
  });
  updateMonthlyOilDetails();
}

function renderChart(rows, series, options) {
  const allValues = series.flatMap((item) => item.values);
  let minimum = Math.min(...allValues);
  let maximum = Math.max(...allValues);
  if (options.includeZero) {
    minimum = Math.min(0, minimum);
    maximum = Math.max(0, maximum);
  }
  const padding = Math.max((maximum - minimum) * .11, Math.abs(maximum) * .012, .25);
  minimum -= padding;
  maximum += padding;
  const plotWidth = PLOT.right - PLOT.left;
  const plotHeight = PLOT.bottom - PLOT.top;
  const x = (index) => PLOT.left + (rows.length <= 1 ? plotWidth : index / (rows.length - 1) * plotWidth);
  const y = (value) => PLOT.top + (maximum - value) / (maximum - minimum || 1) * plotHeight;
  const linePath = (values) => values.map((value, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(value).toFixed(2)}`).join(" ");
  const yTicks = Array.from({ length: 6 }, (_, index) => minimum + (maximum - minimum) * index / 5);
  const xIndexes = [...new Set(Array.from({ length: 7 }, (_, index) => Math.round((rows.length - 1) * index / 6)))];
  const grid = yTicks.map((value) => {
    const py = y(value);
    return `<line class="chart-grid" x1="${PLOT.left}" x2="${PLOT.right}" y1="${py}" y2="${py}"/><text class="chart-tick" x="${PLOT.left - 10}" y="${py + 4}" text-anchor="end">${fmt(value, options.digits)}</text>`;
  }).join("");
  const ticks = xIndexes.map((index) => `<text class="chart-tick" x="${x(index)}" y="${SVG_HEIGHT - 16}" text-anchor="middle">${options.label(rows[index])}</text>`).join("");
  const lines = series.map((item, seriesIndex) => {
    const path = linePath(item.values);
    const area = item.fill ? `<path class="overview-area" fill="${item.color}" d="${path} L${x(rows.length - 1)},${PLOT.bottom} L${x(0)},${PLOT.bottom} Z"/>` : "";
    return `${area}<path class="overview-line" stroke="${item.color}" d="${path}"/><circle class="focus-selected-dot" data-series="${seriesIndex}" fill="${item.color}" r="4"/>`;
  }).join("");
  $("focusChart").innerHTML = `<svg viewBox="0 0 ${SVG_WIDTH} ${SVG_HEIGHT}" preserveAspectRatio="none" aria-hidden="true">
    ${grid}
    ${lines}
    <line id="focusCursor" class="focus-cursor" y1="${PLOT.top}" y2="${PLOT.bottom}"/>
    <rect class="focus-hit" x="${PLOT.left}" y="${PLOT.top}" width="${plotWidth}" height="${plotHeight}"/>
    ${ticks}
  </svg>`;
  $("focusLegend").innerHTML = series.map((item) => `<span><i style="background:${item.color}"></i>${item.label}</span>`).join("");
  state.chartContext = { rows, series, x, y, globalStart: options.globalStart };
  updateChartSelection(options.selectedLocalIndex);
}

function updateChartSelection(localIndex) {
  const context = state.chartContext;
  if (!context) return;
  const index = clamp(localIndex, 0, context.rows.length - 1);
  const cursorX = context.x(index);
  const cursor = $("focusCursor");
  cursor.setAttribute("x1", cursorX);
  cursor.setAttribute("x2", cursorX);
  for (const dot of $("focusChart").querySelectorAll(".focus-selected-dot")) {
    const seriesIndex = Number(dot.dataset.series);
    dot.setAttribute("cx", cursorX);
    dot.setAttribute("cy", context.y(context.series[seriesIndex].values[index]));
  }
}

function selectLocalIndex(localIndex) {
  const context = state.chartContext;
  const index = clamp(localIndex, 0, context.rows.length - 1);
  if (state.mode === "macro") {
    state.selectedMacroIndex = index;
    updateMacroDetails();
  } else if (state.oilPeriod === "annual") {
    state.selectedAnnualIndex = index;
    updateAnnualOilDetails();
  } else {
    state.selectedMonthlyIndex = context.globalStart + index;
    updateMonthlyOilDetails();
  }
  updateChartSelection(index);
}

function updateShippingLink(year, month = 1) {
  const selectedYear = Math.max(2025, year);
  $("physicalLink").href = `/physical?seed=${state.seed}&years=${state.years}&year=${selectedYear}&month=${month}`;
  $("shippingLink").href = `/shipping?seed=${state.seed}&years=${state.years}&year=${selectedYear}&month=${month}`;
}

function detailItem(label, value, tone = "") {
  return `<div class="detail-item"><span>${label}</span><strong class="${tone}">${value}</strong></div>`;
}

function setDetailBadge(label, tone = "") {
  $("detailBadge").textContent = label;
  $("detailBadge").className = `direction-badge ${tone}`;
}

function updateMacroDetails() {
  const rows = state.macro.globalMacroSnapshots;
  const row = rows[state.selectedMacroIndex];
  const previous = state.selectedMacroIndex > 0 ? rows[state.selectedMacroIndex - 1] : null;
  const growth = row.realized_growth_pct == null ? 2.35 : Number(row.realized_growth_pct);
  const change = previous?.realized_growth_pct == null || row.realized_growth_pct == null
    ? null
    : growth - Number(previous.realized_growth_pct);
  const regime = growth >= 3 ? ["扩张", "up"] : growth < 1 ? ["低迷", "down"] : ["常态", ""];
  $("detailEyebrow").textContent = "SELECTED YEAR";
  $("detailTitle").textContent = row.year;
  setDetailBadge(...regime);
  $("focusPrimary").innerHTML = `<span>实际经济增长</span><strong class="${directionClass(growth)}">${row.realized_growth_pct == null ? "基准年" : signed(growth, 2, "%")}</strong><small>${change == null ? "年度模型起点" : `较上年 ${signed(change, 2, " 个百分点")}`}</small>`;
  $("focusDetails").innerHTML = [
    detailItem("全球实际 GDP", fmt(row.global_gdp_trillion_usd, 2, " 万亿美元")),
    detailItem("产出缺口", signed(row.output_gap_pct, 2, "%"), directionClass(row.output_gap_pct)),
    detailItem("通胀", fmt(row.headline_inflation_pct, 2, "%")),
    detailItem("政策利率", fmt(row.global_policy_rate_pct, 2, "%")),
    detailItem("美元指数", fmt(row.global_dollar_index, 1)),
    detailItem("高收益利差", fmt(row.global_high_yield_spread_bps, 0, " bps")),
  ].join("");
  $("detailNote").textContent = "宏观数据按年度结算；移动鼠标即可改变当前观察年份。";
  updateShippingLink(Number(row.year), 1);
}

function updateAnnualOilDetails() {
  const rows = state.oil.annual;
  const row = rows[state.selectedAnnualIndex];
  const previous = state.selectedAnnualIndex > 0 ? rows[state.selectedAnnualIndex - 1] : null;
  const annualReturn = previous ? (Number(row.close_usd_per_bbl) / Number(previous.close_usd_per_bbl) - 1) * 100 : null;
  $("detailEyebrow").textContent = "SELECTED YEAR";
  $("detailTitle").textContent = row.year;
  setDetailBadge(annualReturn == null ? "价格锚" : annualReturn >= 0 ? "上涨" : "下跌", annualReturn == null ? "" : annualReturn >= 0 ? "up" : "down");
  $("focusPrimary").innerHTML = `<span>年度原油收盘</span><strong>${fmt(row.close_usd_per_bbl, 2, " USD/bbl")}</strong><small class="${directionClass(annualReturn)}">${annualReturn == null ? "年度模型起点" : `同比 ${signed(annualReturn, 2, "%")}`}</small>`;
  $("focusDetails").innerHTML = [
    detailItem("年度开盘", fmt(row.open_usd_per_bbl, 2)),
    detailItem("年度最高", fmt(row.high_usd_per_bbl, 2)),
    detailItem("年度最低", fmt(row.low_usd_per_bbl, 2)),
    detailItem("年度振幅", fmt(100 * (row.high_usd_per_bbl - row.low_usd_per_bbl) / ((row.high_usd_per_bbl + row.low_usd_per_bbl) / 2), 2, "%")),
    detailItem("实际价格指数", fmt(row.real_close_index, 1)),
    detailItem("波动状态", fmt(row.volatility_regime_index, 3)),
  ].join("");
  $("detailNote").textContent = "年度收盘是宏观世界的正式价格锚；年线在整段60年世界上悬停查看。";
  updateShippingLink(Number(row.year), 1);
}

function updateMonthlyOilDetails() {
  const rows = state.oil.monthly;
  const row = rows[state.selectedMonthlyIndex];
  const previous = state.selectedMonthlyIndex > 0 ? rows[state.selectedMonthlyIndex - 1] : null;
  const monthlyReturn = previous ? (Number(row.close_usd_per_bbl) / Number(previous.close_usd_per_bbl) - 1) * 100 : null;
  $("detailEyebrow").textContent = "SELECTED MONTH";
  $("detailTitle").textContent = row.label;
  setDetailBadge(monthlyReturn == null ? "月度起点" : monthlyReturn >= 0 ? "上涨" : "下跌", monthlyReturn == null ? "" : monthlyReturn >= 0 ? "up" : "down");
  $("focusPrimary").innerHTML = `<span>月度原油收盘</span><strong>${fmt(row.close_usd_per_bbl, 2, " USD/bbl")}</strong><small class="${directionClass(monthlyReturn)}">${monthlyReturn == null ? "月度路径起点" : `环比 ${signed(monthlyReturn, 2, "%")}`}</small>`;
  $("focusDetails").innerHTML = [
    detailItem("月度开盘", fmt(row.open_usd_per_bbl, 2)),
    detailItem("月度最高", fmt(row.high_usd_per_bbl, 2)),
    detailItem("月度最低", fmt(row.low_usd_per_bbl, 2)),
    detailItem("月度振幅", fmt(100 * (row.high_usd_per_bbl - row.low_usd_per_bbl) / ((row.high_usd_per_bbl + row.low_usd_per_bbl) / 2), 2, "%")),
    detailItem("年度收盘锚", fmt(row.annual_close_anchor_usd_per_bbl, 2)),
    detailItem("路径位置", `${row.month} / 12`),
  ].join("");
  $("detailNote").textContent = "按住图表向左拖看更晚月份，向右拖看更早月份；拖动不改变价格数据。";
  updateShippingLink(Number(row.year), Number(row.month));
}

function pointerLocalIndex(event) {
  const rect = $("focusChart").getBoundingClientRect();
  const svgX = (event.clientX - rect.left) / rect.width * SVG_WIDTH;
  const fraction = clamp((svgX - PLOT.left) / (PLOT.right - PLOT.left), 0, 1);
  return Math.round(fraction * (state.chartContext.rows.length - 1));
}

$("focusChart").addEventListener("pointerdown", (event) => {
  if (!(state.mode === "oil" && state.oilPeriod === "monthly")) return;
  state.dragging = true;
  state.dragStartX = event.clientX;
  state.dragWindowStart = state.monthlyWindowStart;
  $("focusChart").classList.add("is-dragging");
  $("focusChart").setPointerCapture(event.pointerId);
});

$("focusChart").addEventListener("pointermove", (event) => {
  if (!state.chartContext) return;
  if (state.dragging && state.mode === "oil" && state.oilPeriod === "monthly") {
    const rect = $("focusChart").getBoundingClientRect();
    const pixelsPerPoint = rect.width * (PLOT.right - PLOT.left) / SVG_WIDTH / (MONTHLY_WINDOW - 1);
    const shift = Math.round((state.dragStartX - event.clientX) / Math.max(1, pixelsPerPoint));
    const nextStart = clamp(
      state.dragWindowStart + shift,
      0,
      Math.max(0, state.oil.monthly.length - MONTHLY_WINDOW),
    );
    if (nextStart !== state.monthlyWindowStart) {
      state.monthlyWindowStart = nextStart;
      state.selectedMonthlyIndex = clamp(
        state.selectedMonthlyIndex,
        nextStart,
        nextStart + MONTHLY_WINDOW - 1,
      );
      renderMonthlyOilView();
    }
    return;
  }
  selectLocalIndex(pointerLocalIndex(event));
});

function endDrag(event) {
  if (!state.dragging) return;
  state.dragging = false;
  $("focusChart").classList.remove("is-dragging");
  if ($("focusChart").hasPointerCapture(event.pointerId)) {
    $("focusChart").releasePointerCapture(event.pointerId);
  }
}

$("focusChart").addEventListener("pointerup", endDrag);
$("focusChart").addEventListener("pointercancel", endDrag);

$("mainModeNav").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-mode]");
  if (!button) return;
  state.mode = button.dataset.mode;
  renderActiveView();
});

$("oilPeriodNav").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-period]");
  if (!button) return;
  state.oilPeriod = button.dataset.period;
  renderActiveView();
});

$("overviewForm").addEventListener("submit", (event) => {
  event.preventDefault();
  state.seed = Math.max(0, Math.trunc(Number($("seedInput").value) || 42));
  state.years = Math.min(90, Math.max(5, Math.trunc(Number($("yearsInput").value) || 60)));
  loadWorld();
});

readUrl();
syncControls();
loadWorld();
