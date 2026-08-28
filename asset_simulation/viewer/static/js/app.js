const COLORS = {
  blue: "#60a5fa", cyan: "#22d3ee", green: "#34d399", red: "#fb7185",
  orange: "#fb923c", yellow: "#facc15", purple: "#a78bfa", magenta: "#e879f9",
};

const GLOBAL_MODES = {
  overview: ["宏观环境总览", [["实际增长", "realized_growth_pct", COLORS.green], ["总体通胀", "headline_inflation_pct", COLORS.yellow], ["政策率", "ui_policy_rate_pct", COLORS.purple], ["10Y", "ui_yield_10y_pct", COLORS.cyan]]],
  real_gdp: ["实际 GDP（2025 年不变价）", [["实际 GDP（万亿美元）", "ui_real_gdp", COLORS.blue]]],
  nominal_gdp: ["名义 GDP（当年价）", [["名义 GDP（万亿美元）", "ui_nominal_gdp", COLORS.green]]],
  earnings: ["企业盈利参考指数", [["企业盈利参考", "ui_earnings_index", COLORS.blue]]],
  growth: ["实际增长与潜在能力", [["实际增长", "realized_growth_pct", COLORS.green], ["潜在增长", "potential_growth_pct", COLORS.blue], ["产出缺口", "output_gap_pct", COLORS.orange]]],
  inflation: ["通胀、基础通胀与预期", [["总体通胀", "headline_inflation_pct", COLORS.yellow], ["核心", "core_inflation_pct", COLORS.orange], ["预期", "inflation_expectation_pct", COLORS.purple]]],
  rates: ["政策预期与收益率曲线", [["政策率", "ui_policy_rate_pct", COLORS.purple], ["2Y", "ui_yield_2y_pct", COLORS.blue], ["10Y", "ui_yield_10y_pct", COLORS.cyan], ["期限溢价", "term_premium_10y_pct", COLORS.orange]]],
  funding: ["美元资金条件、融资压力与上游流动性", [["美元资金条件", "ui_currency_index", COLORS.blue], ["融资压力", "ui_funding_stress_index", COLORS.orange], ["资金流动性", "ui_funding_liquidity_index", COLORS.green], ["风险偏好", "risk_appetite_index", COLORS.magenta]]],
  credit: ["企业信用条件", [["投资级利差", "ui_ig_spread_bps", COLORS.purple], ["高收益利差", "ui_hy_spread_bps", COLORS.orange], ["信用可得性（右轴）", "credit_availability_index", COLORS.green, 1, "right"], ["违约压力（右轴）", "default_risk_index", COLORS.red, 1, "right"]]],
  energy: ["名义与实际原油价格", [["原油名义价格", "ui_energy_price", COLORS.orange], ["名义商品指数", "broad_commodity_index", COLORS.magenta], ["实际油价指数（右轴）", "ui_real_oil_index", COLORS.cyan, 1, "right"], ["能源成本（右轴）", "energy_cost_pressure_index", COLORS.yellow, 1, "right"]]],
  assets: ["宏观资产复利参考", [["权益复利参考", "ui_equity_total_return_index", COLORS.green], ["权益价格参考", "ui_equity_price_index", COLORS.blue], ["主权债复利参考", "ui_bond_total_return_index", COLORS.purple]]],
};

const COMMODITY_MODES = {
  price: ["名义价格", [["名义价格", "ui_contract_price", COLORS.orange]]],
  real: ["实际价格指数（2025=100）", [["实际价格指数", "ui_contract_real", COLORS.cyan]]],
};

const MACRO_NOTE = "单一计价单位下的全球宏观环境；没有汇率，也没有分区账户。";

const MODE_NAV_LABELS = {
  overview: "总览",
  real_gdp: "实际 GDP",
  nominal_gdp: "名义 GDP",
  earnings: "盈利",
  growth: "增长",
  inflation: "通胀",
  rates: "利率",
  funding: "美元资金条件",
  credit: "信用",
  energy: "能源商品",
  assets: "资产参考",
  price: "名义价格",
  real: "实际价格",
};

const DEFAULT_KIND = "energy";
const DEFAULT_CONTRACT = "brent";
const OPEN_COMMODITY_CONTRACT = "brent";
const ANNUAL_PAN_RATE = 1.15;
const ANNUAL_DRAG_THRESHOLD = 5;
const MONTHS_PER_YEAR = 12;
const WEEKS_PER_MONTH = 4;
const WEEKS_PER_YEAR = MONTHS_PER_YEAR * WEEKS_PER_MONTH;
const MID_MONTH_INDEX = 6;
const MID_WEEK_INDEX = MID_MONTH_INDEX * WEEKS_PER_MONTH + 1;
const PRICE_AXIS_WIDTH = 58;

const state = {
  rows: [], mode: "overview", layer: "macro", index: 0,
  chartView: "overview", monthPointer: MID_MONTH_INDEX, weekPointer: MID_WEEK_INDEX,
  annualNeedsScroll: false, annualCenterIndex: null,
  annualPointerId: null, annualPan: null, annualClickFallback: null,
  annualSuppressClick: false,
  taxonomy: [], kind: DEFAULT_KIND, contract: DEFAULT_CONTRACT,
  baseRows: [], contractRows: {},
};
const $ = (id) => document.getElementById(id);
const fmt = (value, digits = 2) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(digits);
const pct = (value) => value == null ? "—" : `${Number(value) >= 0 ? "+" : ""}${fmt(value)}%`;
const CYCLE_LABELS = { recovery: "复苏", expansion: "扩张", late_cycle: "晚周期", contraction: "周期动量偏弱", neutral: "中性" };

function currentKind() {
  return state.taxonomy.find((item) => item.id === state.kind) || state.taxonomy[0] || { id: DEFAULT_KIND, name: "能源", children: [] };
}
function currentContractMeta() {
  const kind = currentKind();
  return (kind.children || []).find((item) => item.id === state.contract) || (kind.children || [])[0] || { id: DEFAULT_CONTRACT, name: "原油" };
}
function currentModes() {
  return state.layer === "commodity" ? COMMODITY_MODES : GLOBAL_MODES;
}
function ensureMode() {
  const modes = currentModes();
  if (!Object.hasOwn(modes, state.mode)) state.mode = Object.keys(modes)[0];
}
function isCommodityChartOpen() {
  return state.contract === OPEN_COMMODITY_CONTRACT;
}
function parseChartView(value) {
  return value === "annual" || value === "monthly" || value === "weekly" ? value : "overview";
}
function isCandleView() {
  return state.chartView === "annual" || state.chartView === "monthly" || state.chartView === "weekly";
}
function isIntraYearView() {
  return state.chartView === "monthly" || state.chartView === "weekly";
}
function candleCenterBarIndex() {
  if (state.chartView === "weekly") return state.index * WEEKS_PER_YEAR + state.weekPointer;
  if (state.chartView === "monthly") return state.index * MONTHS_PER_YEAR + state.monthPointer;
  return state.index;
}
function resetIntraYearToMid() {
  state.monthPointer = MID_MONTH_INDEX;
  state.weekPointer = MID_WEEK_INDEX;
}
function alignWeekToMonth() {
  state.weekPointer = state.monthPointer * WEEKS_PER_MONTH + 1;
}
function alignMonthToWeek() {
  state.monthPointer = Math.floor(state.weekPointer / WEEKS_PER_MONTH);
}
function hidePriceAxis() {
  const axis = $("chartAxis");
  axis.setAttribute("hidden", "");
  axis.innerHTML = "";
  axis.style.height = "";
}
function renderPriceAxis(height, y, min, max) {
  const axis = $("chartAxis");
  axis.removeAttribute("hidden");
  axis.style.height = `${height}px`;
  const ticks = [];
  for (let i = 0; i <= 5; i++) {
    const value = min + (max - min) * i / 5;
    ticks.push(`<span class="price-tick" style="top:${y(value)}px">${fmt(value, Math.abs(value) >= 100 ? 0 : 1)}</span>`);
  }
  axis.innerHTML = ticks.join("");
}

function mergedGlobalRows(payload) {
  const support = new Map(payload.viewerSupportRows.map((row) => [row.year_index, row]));
  return payload.globalMacroSnapshots.map((row) => ({
    ...row,
    ...support.get(row.year_index),
  }));
}

function normalizeMacroRows(rows) {
  return rows.map((row, index) => {
    const previous = rows[index - 1];
    const nominal = row.global_nominal_gdp_trillion_usd;
    const priorNominal = previous && previous.global_nominal_gdp_trillion_usd;
    const price = row.global_equity_capitalization_reference_index;
    const priorPrice = previous && previous.global_equity_capitalization_reference_index;
    const earnings = row.global_corporate_earnings_reference_index;
    const priorEarnings = previous && previous.global_corporate_earnings_reference_index;
    const energy = row.brent_oil_price_usd;
    const priorEnergy = previous && previous.brent_oil_price_usd;
    return {
      ...row,
      ui_real_gdp: row.global_gdp_trillion_usd,
      ui_nominal_gdp: nominal,
      ui_nominal_growth_pct: row.nominal_growth_pct ?? (priorNominal == null ? null : 100 * (nominal / priorNominal - 1)),
      ui_policy_rate_pct: row.global_policy_rate_pct,
      ui_real_policy_rate_pct: row.real_policy_rate_pct,
      ui_yield_2y_pct: row.global_2y_yield_pct,
      ui_yield_10y_pct: row.global_10y_yield_pct,
      ui_real_10y_yield_pct: row.global_real_10y_yield_pct,
      ui_currency_index: row.global_dollar_index,
      ui_funding_stress_index: row.dollar_funding_stress_index,
      ui_funding_liquidity_index: row.global_funding_liquidity_index,
      ui_ig_spread_bps: row.global_investment_grade_spread_bps,
      ui_hy_spread_bps: row.global_high_yield_spread_bps,
      ui_fci: row.global_financial_conditions_index,
      ui_energy_price: energy,
      ui_energy_yoy_pct: row.oil_yoy_change_pct ?? (priorEnergy == null ? null : 100 * (energy / priorEnergy - 1)),
      ui_real_oil_index: row.global_real_oil_price_index,
      ui_equity_price_index: price,
      ui_equity_total_return_index: row.global_equity_total_return_reference_index,
      ui_equity_total_return_growth_pct: row.global_equity_total_return_reference_growth_pct,
      ui_equity_price_growth_pct: row.global_equity_capitalization_reference_growth_pct ?? (priorPrice == null ? null : 100 * (price / priorPrice - 1)),
      ui_equity_dividend_yield_pct: row.global_equity_dividend_yield_pct,
      ui_earnings_index: earnings,
      ui_earnings_growth_pct: row.global_corporate_earnings_reference_growth_pct ?? (priorEarnings == null ? null : 100 * (earnings / priorEarnings - 1)),
      ui_bond_total_return_index: row.global_sovereign_bond_wealth_reference_index,
      ui_bond_total_return_growth_pct: row.global_sovereign_bond_wealth_reference_growth_pct,
      ui_equity_real_wealth_index: row.global_equity_real_total_return_reference_index,
      ui_bond_real_wealth_index: row.global_sovereign_bond_real_wealth_reference_index,
      ui_erp_pct: row.global_equity_risk_premium_center_pct,
    };
  });
}

function bindCommodityView() {
  if (!state.baseRows.length) return;
  const contracts = isCommodityChartOpen()
    ? new Map((state.contractRows[state.contract] || []).map((row) => [row.year_index, row]))
    : new Map();
  state.rows = normalizeMacroRows(state.baseRows.map((row) => {
    const contract = contracts.get(row.year_index) || {};
    return {
      ...row,
      ui_contract_price: contract.nominal_price_usd,
      ui_contract_real: contract.real_price_index,
      ui_contract_high: contract.nominal_high_usd,
      ui_contract_low: contract.nominal_low_usd,
      ui_contract_real_high: contract.real_high_index,
      ui_contract_real_low: contract.real_low_index,
      ui_monthly: contract.monthly || [],
      ui_contract_yoy_pct: contract.yoy_change_pct,
      ui_contract_real_yoy_pct: contract.real_yoy_change_pct,
      ui_contract_unit: contract.unit_label,
      ui_contract_source: contract.source,
    };
  }));
}

function current() { return state.rows[state.index]; }
function stat(label, value, note) { return `<article class="stat"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`; }
function money(value, digits = 2) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const abs = Math.abs(Number(value));
  const places = abs >= 1000 ? 0 : (abs >= 100 ? 1 : digits);
  return `$${fmt(value, places)}`;
}

function classify(row) {
  if (state.layer === "commodity") {
    if (row.ui_contract_yoy_pct != null && row.ui_contract_yoy_pct < -12) return "价格大跌";
    if (row.ui_contract_yoy_pct != null && row.ui_contract_yoy_pct > 18) return "价格大涨";
    return CYCLE_LABELS[row.ordinary_cycle_phase] || "平稳";
  }
  if (row.realized_growth_pct != null && row.realized_growth_pct < 0) return "衰退";
  if (row.ui_fci > 1.25 || row.ui_hy_spread_bps > 700) return "金融偏紧";
  if (row.headline_inflation_pct > 3.2) return "通胀偏热";
  return CYCLE_LABELS[row.ordinary_cycle_phase] || "平稳";
}

function macroStats(r) {
  return [
    stat("实际 GDP", `$${fmt(r.ui_real_gdp, 1)}T`, `实际增长 ${pct(r.realized_growth_pct)}`),
    stat("名义 GDP", `$${fmt(r.ui_nominal_gdp, 1)}T`, `名义增长 ${pct(r.ui_nominal_growth_pct)} · 平减 ${fmt(r.gdp_deflator_price_level_index_2025_100, 1)}`),
    stat("企业盈利参考", fmt(r.ui_earnings_index, 1), `本年 ${pct(r.ui_earnings_growth_pct)} · 指数不是成交盈利`),
    stat("总体通胀", `${fmt(r.headline_inflation_pct)}%`, `核心 ${fmt(r.core_inflation_pct)}% · 预期 ${fmt(r.inflation_expectation_pct)}%`),
    stat("政策与 10Y", `${fmt(r.ui_policy_rate_pct)}%`, `10Y ${fmt(r.ui_yield_10y_pct)}% · 实际 ${fmt(r.ui_real_10y_yield_pct)}%`),
    stat("美元资金条件", fmt(r.ui_currency_index, 1), `融资压力 ${fmt(r.ui_funding_stress_index, 1)} · 流动性 ${fmt(r.ui_funding_liquidity_index, 1)}`),
    stat("高收益利差 / FCI", `${fmt(r.ui_hy_spread_bps, 0)}bp`, `FCI ${fmt(r.ui_fci)} · 可得性 ${fmt(r.credit_availability_index, 1)}`),
    stat("原油", `$${fmt(r.ui_energy_price, 1)}`, `本年 ${pct(r.ui_energy_yoy_pct)} · 实际 ${fmt(r.ui_real_oil_index, 1)} · 压力 ${fmt(r.energy_cost_pressure_index, 1)}`),
    stat("权益复利参考", fmt(r.ui_equity_total_return_index, 1), `本年 ${pct(r.ui_equity_total_return_growth_pct)} · 股息率 ${fmt(r.ui_equity_dividend_yield_pct)}%`),
  ];
}

function commodityStats(r) {
  const meta = currentContractMeta();
  const unit = r.ui_contract_unit || "";
  if (state.mode === "real") {
    return [stat(`${meta.name}实际价格`, fmt(r.ui_contract_real, 1), `2025=100 · 本年 ${pct(r.ui_contract_real_yoy_pct)}`)];
  }
  return [stat(`${meta.name}价格`, money(r.ui_contract_price), `${unit} · 本年 ${pct(r.ui_contract_yoy_pct)}`)];
}

function commonDetailFields() { return [
  ["实际 / 名义增长", r => `${pct(r.realized_growth_pct)} / ${pct(r.ui_nominal_growth_pct)}`],
  ["潜在增长 / 产出缺口", r => `${fmt(r.potential_growth_pct)}% / ${fmt(r.output_gap_pct)}%`],
  ["普通周期", r => `${CYCLE_LABELS[r.ordinary_cycle_phase] || "—"} · ${fmt(r.ordinary_cycle_index)}`],
  ["CPI / 平减指数", r => `${fmt(r.cpi_price_level_index_2025_100, 1)} / ${fmt(r.gdp_deflator_price_level_index_2025_100, 1)}`],
  ["总体 / 核心 / 预期通胀", r => `${fmt(r.headline_inflation_pct)} / ${fmt(r.core_inflation_pct)} / ${fmt(r.inflation_expectation_pct)}%`],
  ["政策 / 实际政策", r => `${fmt(r.ui_policy_rate_pct)}% / ${fmt(r.ui_real_policy_rate_pct)}%`],
  ["2Y / 10Y", r => `${fmt(r.ui_yield_2y_pct)}% / ${fmt(r.ui_yield_10y_pct)}%`],
  ["融资压力 / 流动性", r => `${fmt(r.ui_funding_stress_index, 1)} / ${fmt(r.ui_funding_liquidity_index, 1)}`],
  ["投资级 / 高收益利差", r => `${fmt(r.ui_ig_spread_bps, 0)} / ${fmt(r.ui_hy_spread_bps, 0)} bp`],
  ["原油年度变化 / 压力", r => `${pct(r.ui_energy_yoy_pct)} / ${fmt(r.energy_cost_pressure_index, 1)}`],
  ["企业盈利参考", r => `${fmt(r.ui_earnings_index, 1)} · ${pct(r.ui_earnings_growth_pct)}`],
  ["权益价格 / 复利参考", r => `${pct(r.ui_equity_price_growth_pct)} / ${pct(r.ui_equity_total_return_growth_pct)}`],
  ["主权债复利参考", r => `${fmt(r.ui_bond_total_return_index, 1)} · ${pct(r.ui_bond_total_return_growth_pct)}`],
  ["风险偏好 / ERP", r => `${fmt(r.risk_appetite_index, 1)} / ${fmt(r.ui_erp_pct)}%`],
  ["实际权益 / 债券财富", r => `${fmt(r.ui_equity_real_wealth_index, 1)} / ${fmt(r.ui_bond_real_wealth_index, 1)}`],
]; }

function renderStats() {
  const cards = state.layer === "commodity" ? commodityStats(current()) : macroStats(current());
  $("statsGrid").className = `stats stats-count-${cards.length}`;
  $("statsGrid").innerHTML = cards.join("");
}

function renderDetail() {
  const panel = document.querySelector(".detail-panel");
  const priceOnly = state.layer === "commodity";
  panel.hidden = priceOnly;
  $("workspace").classList.toggle("price-only", priceOnly);
  if (priceOnly) return;
  const row = current();
  $("detailYear").textContent = row.year;
  $("regime").textContent = classify(row);
  $("detailEyebrow").textContent = "当前年份";
  $("detailGrid").className = "detail-grid";
  $("detailGrid").style.gridTemplateColumns = "";
  $("detailGrid").innerHTML = commonDetailFields().map(([label, render]) => `<div class="detail-item"><span>${label}</span><strong>${render(row)}</strong></div>`).join("");
}

function renderLegend(series) { $("legend").innerHTML = series.map(([label,,color]) => `<span><i class="swatch" style="background:${color}"></i>${label}</span>`).join(""); }

function annualBars() {
  const isReal = state.mode === "real";
  const key = isReal ? "ui_contract_real" : "ui_contract_price";
  const highKey = isReal ? "ui_contract_real_high" : "ui_contract_high";
  const lowKey = isReal ? "ui_contract_real_low" : "ui_contract_low";
  return state.rows.map((row, i) => {
    const close = Number(row[key]);
    const open = i === 0 ? close : Number(state.rows[i - 1][key]);
    return {
      yearIndex: i,
      year: row.year,
      month: null,
      open,
      high: Number.isFinite(Number(row[highKey])) ? Number(row[highKey]) : Math.max(open, close),
      low: Number.isFinite(Number(row[lowKey])) ? Number(row[lowKey]) : Math.min(open, close),
      close,
      title: String(row.year),
    };
  });
}

function monthlyBars() {
  const isReal = state.mode === "real";
  const bars = [];
  for (let i = 0; i < state.rows.length; i++) {
    const months = state.rows[i].ui_monthly || [];
    for (const item of months) {
      bars.push({
        yearIndex: i,
        year: state.rows[i].year,
        month: Number(item.month),
        open: Number(isReal ? item.real_open : item.open),
        high: Number(isReal ? item.real_high : item.high),
        low: Number(isReal ? item.real_low : item.low),
        close: Number(isReal ? item.real_close : item.close),
        title: `${state.rows[i].year}-${String(Number(item.month)).padStart(2, "0")}`,
      });
    }
  }
  return bars;
}

function weeklyBars() {
  const isReal = state.mode === "real";
  const bars = [];
  for (let i = 0; i < state.rows.length; i++) {
    const months = state.rows[i].ui_monthly || [];
    for (const item of months) {
      const weeks = item.weekly || [];
      for (const week of weeks) {
        bars.push({
          yearIndex: i,
          year: state.rows[i].year,
          month: Number(item.month),
          week: Number(week.week),
          open: Number(isReal ? week.real_open : week.open),
          high: Number(isReal ? week.real_high : week.high),
          low: Number(isReal ? week.real_low : week.low),
          close: Number(isReal ? week.real_close : week.close),
          title: `${state.rows[i].year}-${String(Number(item.month)).padStart(2, "0")}-W${Number(week.week)}`,
        });
      }
    }
  }
  return bars;
}

function renderAnnualCandles() {
  renderCandleChart(annualBars(), state.mode === "real" ? "实际年线" : "名义年线");
}

function renderMonthlyCandles() {
  renderCandleChart(monthlyBars(), state.mode === "real" ? "实际月线" : "名义月线");
}

function renderWeeklyCandles() {
  renderCandleChart(weeklyBars(), state.mode === "real" ? "实际周线" : "名义周线");
}

function renderCandleChart(bars, label) {
  if (!bars.length) {
    renderBlankCommodityChart();
    return;
  }
  const svg = $("chart");
  const wrap = svg.parentElement;
  const meta = currentContractMeta();
  const isReal = state.mode === "real";
  const grain = state.chartView === "weekly" ? "weekly" : state.chartView === "monthly" ? "monthly" : "annual";
  const upColor = "#ef4444";
  const downColor = "#22c55e";
  const flatColor = "#94a3b8";
  $("chartTitle").textContent = `${meta.name} · ${label}`;
  renderLegend([["上涨", "close", upColor], ["下跌", "close", downColor]]);
  wrap.classList.add("annual-chart-wrap");

  const viewportWidth = Math.max(wrap.clientWidth - 20, 320);
  const targetVisible = grain === "weekly"
    ? (viewportWidth < 600 ? 32 : viewportWidth < 1000 ? 44 : 52)
    : grain === "monthly"
    ? (viewportWidth < 600 ? 24 : viewportWidth < 1000 ? 36 : 48)
    : (viewportWidth < 600 ? 20 : viewportWidth < 1000 ? 28 : 36);
  const minStep = grain === "weekly" ? 8 : grain === "monthly" ? 12 : 16;
  const maxStep = grain === "weekly" ? 14 : grain === "monthly" ? 20 : 26;
  const step = Math.max(minStep, Math.min(maxStep, Math.floor(viewportWidth / targetVisible)));
  const candleWidth = Math.max(grain === "weekly" ? 3 : grain === "monthly" ? 5 : 7, Math.floor(step * .58));
  const margin = { left: 24, right: PRICE_AXIS_WIDTH, top: 24, bottom: 42 };
  const width = Math.max(viewportWidth, margin.left + margin.right + step * bars.length);
  const height = Math.max(svg.clientHeight || 540, 320);
  const innerH = height - margin.top - margin.bottom;
  const values = [];
  for (let i = 0; i < bars.length; i++) {
    const bar = bars[i];
    if (Number.isFinite(bar.open)) values.push(bar.open);
    if (Number.isFinite(bar.close)) values.push(bar.close);
    if (Number.isFinite(bar.high)) values.push(bar.high);
    if (Number.isFinite(bar.low)) values.push(bar.low);
  }
  let min = Math.min(...values), max = Math.max(...values);
  if (!values.length) { min = 0; max = 1; }
  const padding = Math.max((max - min) * .10, Math.abs(max || 1) * .03, .000001);
  min -= padding;
  max += padding;
  const y = (value) => margin.top + innerH * (max - value) / Math.max(1e-12, max - min);
  const x = (index) => margin.left + step * index + step / 2;
  const cursorIndex = Math.max(0, Math.min(bars.length - 1, candleCenterBarIndex()));
  const parts = [`<rect width="${width}" height="${height}" fill="#0c1118"/>`];
  for (let i = 0; i <= 5; i++) {
    const value = min + (max - min) * i / 5;
    const yy = y(value);
    parts.push(`<line class="grid" x1="${margin.left}" x2="${width - margin.right}" y1="${yy}" y2="${yy}"/>`);
  }
  for (let i = 0; i < bars.length; i++) {
    const bar = bars[i];
    if (!Number.isFinite(bar.open) || !Number.isFinite(bar.close)) continue;
    const high = Number.isFinite(bar.high) ? bar.high : Math.max(bar.open, bar.close);
    const low = Number.isFinite(bar.low) ? bar.low : Math.min(bar.open, bar.close);
    const color = bar.close > bar.open ? upColor : bar.close < bar.open ? downColor : flatColor;
    const bodyTop = Math.min(y(bar.open), y(bar.close));
    const bodyHeight = Math.max(2, Math.abs(y(bar.close) - y(bar.open)));
    const center = x(i);
    parts.push(
      `<line class="candle-wick" x1="${center}" x2="${center}" y1="${y(high)}" y2="${y(low)}" stroke="${color}"/>`,
      `<rect class="candle-body" x="${center - candleWidth / 2}" y="${bodyTop}" width="${candleWidth}" height="${bodyHeight}" fill="${color}"><title>${bar.title} · 开 ${fmt(bar.open, isReal ? 1 : 2)} · 高 ${fmt(high, isReal ? 1 : 2)} · 低 ${fmt(low, isReal ? 1 : 2)} · 收 ${fmt(bar.close, isReal ? 1 : 2)}</title></rect>`,
      `<rect class="candle-hit" data-index="${i}" x="${center - step / 2}" y="${margin.top}" width="${step}" height="${innerH}"/>`,
    );
    const showTick = grain === "weekly"
      ? ((bar.month === 1 && bar.week === 1) || i === bars.length - 1)
      : grain === "monthly"
      ? (bar.month === 1 || i === bars.length - 1)
      : (i % 5 === 0 || i === bars.length - 1);
    if (showTick) {
      parts.push(`<text class="tick" x="${center}" y="${height - 15}" text-anchor="middle">${bar.year}</text>`);
    }
  }
  parts.push(
    `<line class="cursor" x1="${x(cursorIndex)}" x2="${x(cursorIndex)}" y1="${margin.top}" y2="${height - margin.bottom}"/>`,
    `<line class="cursor-hit" x1="${x(cursorIndex)}" x2="${x(cursorIndex)}" y1="${margin.top}" y2="${height - margin.bottom}"/>`,
  );
  svg.style.width = `${width}px`;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("aria-label", `${meta.name}${label}`);
  svg.innerHTML = parts.join("");
  renderPriceAxis(height, y, min, max);
  const selectIndex = (next) => {
    const bar = bars[next];
    if (!bar) return;
    const nextMonth = bar.month == null ? state.monthPointer : bar.month - 1;
    const nextWeek = bar.week == null
      ? nextMonth * WEEKS_PER_MONTH + 1
      : nextMonth * WEEKS_PER_MONTH + bar.week - 1;
    if (
      bar.yearIndex === state.index
      && nextMonth === state.monthPointer
      && (state.chartView !== "weekly" || nextWeek === state.weekPointer)
    ) return;
    state.index = bar.yearIndex;
    state.monthPointer = nextMonth;
    state.weekPointer = nextWeek;
    render();
  };
  const indexFromPointer = (event) => {
    const rect = svg.getBoundingClientRect();
    const localX = (event.clientX - rect.left) * width / Math.max(1, rect.width);
    return Math.max(0, Math.min(bars.length - 1, Math.round((localX - margin.left - step / 2) / step)));
  };
  wrap.onclick = (event) => {
    if (state.annualClickFallback) {
      const fallback = state.annualClickFallback;
      state.annualClickFallback = null;
      const deltaX = event.clientX - fallback.startX;
      if (Math.abs(deltaX) >= ANNUAL_DRAG_THRESHOLD) {
        wrap.scrollLeft = fallback.startScrollLeft - deltaX * ANNUAL_PAN_RATE;
        return;
      }
    }
    if (state.annualSuppressClick) {
      state.annualSuppressClick = false;
      return;
    }
    const hit = event.target.closest(".candle-hit");
    if (hit) selectIndex(Number(hit.dataset.index));
  };
  wrap.onpointerdown = (event) => {
    const rect = svg.getBoundingClientRect();
    const localY = (event.clientY - rect.top) * height / Math.max(1, rect.height);
    if (localY < margin.top || localY > height - margin.bottom) return;
    const draggingCursor = Boolean(event.target.closest(".cursor-hit"));
    state.annualClickFallback = null;
    state.annualSuppressClick = false;
    if (draggingCursor) state.annualPointerId = event.pointerId;
    else state.annualPan = { pointerId: event.pointerId, startX: event.clientX, startScrollLeft: wrap.scrollLeft, moved: false };
    wrap.setPointerCapture(event.pointerId);
    if (draggingCursor) event.preventDefault();
  };
  wrap.onpointermove = (event) => {
    if (state.annualPointerId === event.pointerId) {
      event.preventDefault();
      selectIndex(indexFromPointer(event));
      return;
    }
    if (state.annualPan?.pointerId === event.pointerId) {
      const deltaX = event.clientX - state.annualPan.startX;
      if (Math.abs(deltaX) >= ANNUAL_DRAG_THRESHOLD) {
        state.annualPan.moved = true;
        wrap.classList.add("is-panning");
        wrap.scrollLeft = state.annualPan.startScrollLeft - deltaX * ANNUAL_PAN_RATE;
        event.preventDefault();
      }
      return;
    }
  };
  const endPointerDrag = (event, cancelled = false) => {
    const cursorDrag = state.annualPointerId === event.pointerId;
    const panDrag = state.annualPan?.pointerId === event.pointerId;
    if (!cursorDrag && !panDrag) return;
    const pan = panDrag ? state.annualPan : null;
    const deltaX = pan ? event.clientX - pan.startX : 0;
    const panMoved = Boolean(pan && (pan.moved || Math.abs(deltaX) >= ANNUAL_DRAG_THRESHOLD));
    const next = !cancelled && cursorDrag ? indexFromPointer(event) : null;
    if (panMoved) {
      wrap.scrollLeft = pan.startScrollLeft - deltaX * ANNUAL_PAN_RATE;
      state.annualClickFallback = null;
      state.annualSuppressClick = true;
    } else if (!cancelled && panDrag) {
      state.annualClickFallback = { startX: pan.startX, startScrollLeft: pan.startScrollLeft };
      state.annualSuppressClick = false;
    } else if (panDrag) {
      state.annualClickFallback = null;
    }
    if (wrap.hasPointerCapture(event.pointerId)) wrap.releasePointerCapture(event.pointerId);
    if (cursorDrag) state.annualPointerId = null;
    if (panDrag) state.annualPan = null;
    wrap.classList.remove("is-panning");
    if (next != null) selectIndex(next);
  };
  wrap.onpointerup = endPointerDrag;
  wrap.onpointercancel = (event) => endPointerDrag(event, true);
  if (state.annualCenterIndex != null) {
    const targetLeft = x(state.annualCenterIndex) - (wrap.clientWidth - PRICE_AXIS_WIDTH) / 2;
    wrap.scrollLeft = Math.max(0, Math.min(wrap.scrollWidth - wrap.clientWidth, targetLeft));
    state.annualCenterIndex = null;
    state.annualNeedsScroll = false;
  } else if (state.annualNeedsScroll) {
    wrap.scrollLeft = wrap.scrollWidth - wrap.clientWidth;
    state.annualNeedsScroll = false;
  }
}

function renderBlankCommodityChart() {
  const meta = currentContractMeta();
  const [title] = currentModes()[state.mode];
  $("chartTitle").textContent = `${meta.name} · ${title}`;
  renderLegend([]);
  const svg = $("chart");
  const wrap = svg.parentElement;
  state.annualPointerId = null;
  state.annualPan = null;
  state.annualClickFallback = null;
  state.annualSuppressClick = false;
  wrap.onclick = null;
  wrap.onpointerdown = null;
  wrap.onpointermove = null;
  wrap.onpointerup = null;
  wrap.onpointercancel = null;
  svg.onclick = null;
  svg.onpointerdown = null;
  svg.onpointermove = null;
  svg.onpointerup = null;
  svg.onpointercancel = null;
  svg.onpointerleave = null;
  wrap.classList.remove("annual-chart-wrap");
  wrap.scrollLeft = 0;
  svg.style.width = "";
  hidePriceAxis();
  const width = Math.max(svg.clientWidth || 900, 500);
  const height = Math.max(svg.clientHeight || 540, 320);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("aria-label", `${meta.name}图表暂未绘制`);
  svg.innerHTML = `<rect width="${width}" height="${height}" fill="#0c1118"/>`;
}

function renderChart() {
  if (state.layer === "commodity" && !isCommodityChartOpen()) {
    renderBlankCommodityChart();
    return;
  }
  if (state.layer === "commodity" && state.chartView === "annual") {
    renderAnnualCandles();
    return;
  }
  if (state.layer === "commodity" && state.chartView === "monthly") {
    renderMonthlyCandles();
    return;
  }
  if (state.layer === "commodity" && state.chartView === "weekly") {
    renderWeeklyCandles();
    return;
  }
  const modes = currentModes();
  const [title, series] = modes[state.mode];
  $("chartTitle").textContent = state.layer === "commodity" ? `${currentContractMeta().name} · ${title}` : title;
  renderLegend(series);
  const hasRight = series.some(([, , , , axis]) => axis === "right");
  const svg = $("chart");
  const wrap = svg.parentElement;
  state.annualPointerId = null;
  state.annualPan = null;
  state.annualClickFallback = null;
  state.annualSuppressClick = false;
  wrap.onclick = null;
  wrap.onpointerdown = null;
  wrap.onpointermove = null;
  wrap.onpointerup = null;
  wrap.onpointercancel = null;
  svg.onclick = null;
  svg.onpointerdown = null;
  svg.onpointermove = null;
  svg.onpointerup = null;
  svg.onpointercancel = null;
  svg.onpointerleave = null;
  wrap.classList.remove("annual-chart-wrap");
  wrap.scrollLeft = 0;
  svg.style.width = "";
  hidePriceAxis();
  svg.setAttribute("aria-label", "全球宏观年度轨迹");
  const width = Math.max(svg.clientWidth || 900, 500);
  const height = Math.max(svg.clientHeight || 540, 320);
  const margin = { left: 60, right: hasRight ? 60 : 20, top: 24, bottom: 42 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;
  const bounds = (axis) => {
    const values = [];
    for (const row of state.rows) {
      for (const [, key, , scale = 1, seriesAxis = "left"] of series) {
        if (seriesAxis === axis && row[key] != null && Number.isFinite(Number(row[key]))) values.push(Number(row[key]) * scale);
      }
    }
    let min = Math.min(...values), max = Math.max(...values);
    if (!values.length) { min = -1; max = 1; }
    const padding = Math.max((max - min) * .12, Math.abs(max || 1) * .04, .000001);
    min -= padding;
    max += padding;
    if (axis === "left" && min > 0 && min < max * .25) min = 0;
    return { min, max };
  };
  const left = bounds("left");
  const right = hasRight ? bounds("right") : null;
  const x = (i) => margin.left + innerW * i / Math.max(1, state.rows.length - 1);
  const yFor = (value, axis = "left") => {
    const range = axis === "right" ? right : left;
    return margin.top + innerH * (range.max - value) / Math.max(1e-12, range.max - range.min);
  };
  const parts = [`<rect width="${width}" height="${height}" fill="#0c1118"/>`];
  for (let i = 0; i <= 5; i++) {
    const value = left.min + (left.max - left.min) * i / 5;
    const yy = yFor(value);
    parts.push(`<line class="grid" x1="${margin.left}" x2="${width - margin.right}" y1="${yy}" y2="${yy}"/>`, `<text class="tick" x="${margin.left - 10}" y="${yy + 4}" text-anchor="end">${fmt(value, Math.abs(value) > 100 ? 0 : Math.abs(value) < .01 ? 4 : 1)}</text>`);
    if (hasRight) {
      const rv = right.min + (right.max - right.min) * i / 5;
      parts.push(`<text class="tick" x="${width - margin.right + 10}" y="${yy + 4}" text-anchor="start">${fmt(rv, 1)}</text>`);
    }
  }
  if (left.min < 0 && left.max > 0) parts.push(`<line class="zero-line" x1="${margin.left}" x2="${width - margin.right}" y1="${yFor(0)}" y2="${yFor(0)}"/>`);
  const ticks = Math.min(5, state.rows.length - 1);
  for (let i = 0; i <= ticks; i++) {
    const n = Math.round((state.rows.length - 1) * i / Math.max(1, ticks));
    parts.push(`<text class="tick" x="${x(n)}" y="${height - 15}" text-anchor="middle">${state.rows[n].year}</text>`);
  }
  for (const [, key, color, scale = 1, axis = "left"] of series) {
    const points = state.rows.map((row, i) => row[key] == null || !Number.isFinite(Number(row[key])) ? null : `${x(i).toFixed(2)},${yFor(Number(row[key]) * scale, axis).toFixed(2)}`).filter(Boolean).join(" ");
    parts.push(`<polyline points="${points}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>`);
  }
  parts.push(`<line class="cursor" x1="${x(state.index)}" x2="${x(state.index)}" y1="${margin.top}" y2="${height - margin.bottom}"/>`);
  const hitWidth = innerW / Math.max(1, state.rows.length - 1);
  for (let i = 0; i < state.rows.length; i++) {
    const hitLeft = Math.max(margin.left, x(i) - hitWidth / 2);
    const hitRight = Math.min(width - margin.right, x(i) + hitWidth / 2);
    parts.push(`<rect class="chart-hit" data-index="${i}" x="${hitLeft}" y="${margin.top}" width="${Math.max(1, hitRight - hitLeft)}" height="${innerH}"/>`);
  }
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = parts.join("");
  svg.querySelectorAll(".chart-hit").forEach((hit) => hit.addEventListener("pointermove", () => {
    const next = Number(hit.dataset.index);
    if (next !== state.index) {
      state.index = next;
      render();
    }
  }));
}

function fillCommodityPicker() {
  const kindSelect = $("kindSelect");
  const contractSelect = $("contractSelect");
  kindSelect.innerHTML = state.taxonomy.map((item) => `<option value="${item.id}">${item.name}</option>`).join("");
  if (!state.taxonomy.some((item) => item.id === state.kind)) state.kind = DEFAULT_KIND;
  kindSelect.value = state.kind;
  const children = currentKind().children || [];
  contractSelect.innerHTML = children.map((item) => `<option value="${item.id}">${item.name}</option>`).join("");
  if (!children.some((item) => item.id === state.contract)) state.contract = children[0] ? children[0].id : DEFAULT_CONTRACT;
  contractSelect.value = state.contract;
}

function fillModeNav() {
  const nav = $("modeNav");
  const modes = currentModes();
  nav.hidden = false;
  nav.classList.toggle("commodity-mode-nav", state.layer === "commodity");
  nav.setAttribute("aria-label", state.layer === "commodity" ? "商品模块" : "全球宏观模块");
  const modeButtons = Object.keys(modes).map((mode) => (
    `<button type="button" data-mode="${mode}" aria-pressed="${mode === state.mode ? "true" : "false"}">${MODE_NAV_LABELS[mode]}</button>`
  )).join("");
  const overviewIndicator = state.layer === "commodity"
    ? `<button type="button" class="overview-button" data-chart-view="overview" aria-pressed="${state.chartView === "overview" ? "true" : "false"}">总览</button><button type="button" data-chart-view="annual" aria-pressed="${state.chartView === "annual" ? "true" : "false"}">年线</button><button type="button" data-chart-view="monthly" aria-pressed="${state.chartView === "monthly" ? "true" : "false"}">月线</button><button type="button" data-chart-view="weekly" aria-pressed="${state.chartView === "weekly" ? "true" : "false"}">周线</button><span class="year-jump"><input type="number" inputmode="numeric" data-year-jump-input aria-label="跳转年份" min="${state.rows[0]?.year ?? ""}" max="${state.rows.at(-1)?.year ?? ""}" value="${current()?.year ?? ""}"><button type="button" data-year-jump>跳转</button></span>`
    : "";
  nav.innerHTML = modeButtons + overviewIndicator;
}

function renderNav() {
  fillModeNav();
  for (const button of $("layerNav").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", button.dataset.layer === state.layer ? "true" : "false");
  }
  $("commodityPicker").hidden = state.layer !== "commodity";
  fillCommodityPicker();
}

function render() {
  $("yearRange").value = String(state.index);
  $("yearLabel").textContent = current().year;
  $("identityText").textContent = `Seed ${current().seed} · ${state.rows.length} 个年度状态`;
  const kind = currentKind();
  const contract = currentContractMeta();
  $("scopeEyebrow").textContent = state.layer === "commodity" ? `${kind.name} · ${contract.name}` : "全球普通基座";
  $("scopeNote").textContent = state.layer === "commodity"
    ? !isCommodityChartOpen()
      ? `${contract.name}图表暂未开放。`
      : state.chartView === "weekly"
      ? `${contract.name}模拟周线：每月 4 根，收盘沿月开到月收波动，月高月低由周内影扫到，不是成交周线。拖动图表浏览，输入年份跳到该年中段；点击仍选择年份。`
      : state.chartView === "monthly"
      ? `${contract.name}模拟月线：12 根收盘沿年开到年收波动，年高年低由月内影扫到，不是成交月线。拖动图表浏览，输入年份跳到该年中段；点击仍选择年份。`
      : state.chartView === "annual"
      ? `${contract.name}模拟年线：开盘取上年收盘；年内高低是年振幅通道，趋势年收在极值附近，不是分时成交。拖动图表浏览，输入年份跳转，点击 K 线或拖动白线选择年份。`
      : `${contract.name}为年度模拟参考价格；名义价格包含累计通胀，实际价格以 2025=100。不是成交价格。`
    : MACRO_NOTE;
  $("status").textContent = state.layer === "commodity" ? `商品价格 · ${contract.name}` : "全球宏观 · 单一计价单位";
  renderNav();
  renderStats();
  renderDetail();
  renderChart();
}

function setLayer(layer) {
  state.layer = layer === "commodity" ? "commodity" : "macro";
  ensureMode();
}

function applyCommoditySelection(kindId, contractId) {
  const kind = state.taxonomy.find((item) => item.id === kindId) || currentKind();
  state.kind = kind.id;
  const children = kind.children || [];
  state.contract = children.some((item) => item.id === contractId) ? contractId : (children[0] ? children[0].id : DEFAULT_CONTRACT);
  bindCommodityView();
}

function jumpToYear(rawYear) {
  const targetYear = Math.round(Number(rawYear));
  if (!Number.isFinite(targetYear) || !state.rows.length) return;
  let next = 0;
  let bestDistance = Infinity;
  for (let i = 0; i < state.rows.length; i++) {
    const distance = Math.abs(Number(state.rows[i].year) - targetYear);
    if (distance < bestDistance) {
      next = i;
      bestDistance = distance;
    }
  }
  state.index = next;
  if (isIntraYearView()) resetIntraYearToMid();
  state.annualCenterIndex = state.layer === "commodity" && isCandleView() ? candleCenterBarIndex() : null;
  render();
}

function syncUrl() {
  if (!state.rows.length) return;
  const url = new URL(location.href);
  url.searchParams.set("seed", String(current().seed));
  url.searchParams.set("years", String(state.rows.length - 1));
  url.searchParams.set("layer", state.layer);
  url.searchParams.set("kind", state.kind);
  url.searchParams.set("contract", state.contract);
  if (state.layer === "commodity") url.searchParams.set("view", state.chartView);
  else url.searchParams.delete("view");
  url.searchParams.delete("sw1");
  url.searchParams.delete("sw2");
  url.searchParams.delete("region");
  url.searchParams.delete("stock");
  url.searchParams.delete("finance");
  url.searchParams.delete("period");
  history.replaceState(null, "", url);
  $("gameEntry").href = `/game?seed=${encodeURIComponent(current().seed)}`;
}

async function loadRun(seed, years) {
  $("status").textContent = "正在生成全球宏观…";
  const response = await fetch(`/api/global?seed=${encodeURIComponent(seed)}&years=${encodeURIComponent(years)}`);
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error || "全球宏观生成失败");
  state.baseRows = mergedGlobalRows(payload);
  state.contractRows = payload.commodities?.contracts || {};
  bindCommodityView();
  state.index = state.rows.length - 1;
  resetIntraYearToMid();
  state.annualNeedsScroll = isCandleView();
  $("yearRange").max = String(state.index);
  syncUrl();
  render();
}

$("runForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try { await loadRun(Number($("seedInput").value), Number($("yearsInput").value)); }
  catch (error) { $("status").textContent = error.message; }
});
$("yearRange").addEventListener("input", () => {
  state.index = Number($("yearRange").value);
  if (isIntraYearView()) resetIntraYearToMid();
  render();
});
$("modeNav").addEventListener("click", (event) => {
  const jumpButton = event.target.closest("button[data-year-jump]");
  if (jumpButton) {
    jumpToYear($("modeNav").querySelector("[data-year-jump-input]").value);
    return;
  }
  const modeButton = event.target.closest("button[data-mode]");
  if (modeButton && !modeButton.hidden) state.mode = modeButton.dataset.mode;
  const viewButton = event.target.closest("button[data-chart-view]");
  if (viewButton && !viewButton.hidden) {
    const next = parseChartView(viewButton.dataset.chartView);
    const prev = state.chartView;
    const switched = next !== prev;
    if (next === "weekly") {
      if (prev === "monthly") alignWeekToMonth();
      else if (prev !== "weekly") resetIntraYearToMid();
      alignMonthToWeek();
    } else if (next === "monthly") {
      if (prev === "weekly") alignMonthToWeek();
      else if (prev !== "monthly") state.monthPointer = MID_MONTH_INDEX;
    }
    state.chartView = next;
    if (isIntraYearView()) {
      state.annualCenterIndex = candleCenterBarIndex();
      state.annualNeedsScroll = false;
    } else {
      state.annualNeedsScroll = next === "annual" && switched;
    }
  }
  if (!modeButton && !viewButton) return;
  syncUrl();
  render();
});
$("modeNav").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || !event.target.matches("[data-year-jump-input]")) return;
  event.preventDefault();
  jumpToYear(event.target.value);
});
$("layerNav").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-layer]");
  if (!button) return;
  setLayer(button.dataset.layer);
  if (state.layer === "commodity" && isCandleView()) {
    if (isIntraYearView()) state.annualCenterIndex = candleCenterBarIndex();
    else state.annualNeedsScroll = true;
  }
  syncUrl();
  if (state.rows.length) render();
});
$("commodityPicker").addEventListener("submit", (event) => event.preventDefault());
$("kindSelect").addEventListener("change", () => {
  applyCommoditySelection($("kindSelect").value, null);
  setLayer("commodity");
  if (isIntraYearView()) state.annualCenterIndex = candleCenterBarIndex();
  else state.annualNeedsScroll = state.chartView === "annual";
  syncUrl();
  if (state.rows.length) render();
});
$("contractSelect").addEventListener("change", () => {
  applyCommoditySelection(state.kind, $("contractSelect").value);
  setLayer("commodity");
  if (isIntraYearView()) state.annualCenterIndex = candleCenterBarIndex();
  else state.annualNeedsScroll = state.chartView === "annual";
  syncUrl();
  if (state.rows.length) render();
});
window.addEventListener("resize", () => state.rows.length && renderChart());

async function boot() {
  const params = new URLSearchParams(location.search);
  const initialSeed = Number(params.get("seed") ?? 42);
  const initialYears = Number(params.get("years") ?? 60);
  state.chartView = parseChartView(params.get("view"));
  setLayer(params.get("layer") || "macro");
  $("seedInput").value = String(initialSeed);
  $("yearsInput").value = String(initialYears);
  const taxonomy = await fetch("/static/data/commodities.json").then((response) => {
    if (!response.ok) throw new Error("商品分类加载失败");
    return response.json();
  });
  state.taxonomy = taxonomy.kinds || [];
  applyCommoditySelection(params.get("kind") || DEFAULT_KIND, params.get("contract") || DEFAULT_CONTRACT);
  ensureMode();
  fillCommodityPicker();
  await loadRun(initialSeed, initialYears);
}

boot().catch((error) => { $("status").textContent = error.message; });
