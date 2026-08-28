const GAME_START_YEAR = 2030;
const GAME_START_MONTH = 1;
const GAME_END_YEAR = 2085;
const GAME_END_MONTH = 12;
const OBSERVATION_START_YEAR = 2025;
const MONTHS_PER_YEAR = 12;
const TURNS_PER_MONTH = 2;
const TURNS_PER_YEAR = MONTHS_PER_YEAR * TURNS_PER_MONTH;
const WEEKS_PER_MONTH = 4;
const TOTAL_TURNS = (GAME_END_YEAR - GAME_START_YEAR + 1) * TURNS_PER_YEAR;
const STORAGE_VERSION = "asset-simulation-game-progress-v3";
const PRICE_AXIS_WIDTH = 66;
const UP_COLOR = "#ef4444";
const DOWN_COLOR = "#22c55e";
const FLAT_COLOR = "#94a3b8";

const state = {
  seed: 42,
  turn: 0,
  period: "weekly",
  gameView: "market",
  screen: "market",
  instrumentId: "OIL-MAIN",
  futures: null,
  competition: null,
  selectedReportId: null,
  identity: null,
  resetArmed: false,
  resetTimer: null,
  chartFrame: null,
  chartRenderLock: false,
  chartDrag: null,
};

const $ = (id) => document.getElementById(id);
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

function fmt(value, digits = 2) {
  if (!Number.isFinite(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function money(value) {
  if (!Number.isFinite(Number(value))) return "—";
  const abs = Math.abs(Number(value));
  const digits = abs >= 1000 ? 0 : abs >= 100 ? 1 : 2;
  return `$${fmt(value, digits)}`;
}

function moneyWhole(value) {
  if (!Number.isFinite(Number(value))) return "—";
  return `$${Math.round(Number(value)).toLocaleString("en-US")}`;
}

function signedPct(value) {
  if (!Number.isFinite(Number(value))) return "—";
  return `${Number(value) >= 0 ? "+" : ""}${fmt(value, 2)}%`;
}

function whole(value) {
  if (!Number.isFinite(Number(value))) return "—";
  return Math.trunc(Number(value)).toLocaleString("en-US");
}

function signedWhole(value) {
  if (!Number.isFinite(Number(value))) return "—";
  const numeric = Math.trunc(Number(value));
  return `${numeric > 0 ? "+" : ""}${numeric.toLocaleString("en-US")}`;
}

function signedMoneyWhole(value) {
  if (!Number.isFinite(Number(value))) return "—";
  const numeric = Math.round(Number(value));
  return `${numeric > 0 ? "+" : numeric < 0 ? "−" : ""}$${Math.abs(numeric).toLocaleString("en-US")}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function dateFromTurn(turn = state.turn) {
  const offset = clamp(Math.trunc(turn), 0, TOTAL_TURNS - 1);
  const monthOffset = Math.floor(offset / TURNS_PER_MONTH);
  return {
    year: GAME_START_YEAR + Math.floor(monthOffset / MONTHS_PER_YEAR),
    month: monthOffset % MONTHS_PER_YEAR + 1,
    half: offset % TURNS_PER_MONTH + 1,
  };
}

function turnFromDate(year, month, half = 1) {
  return (Number(year) - GAME_START_YEAR) * TURNS_PER_YEAR
    + (Number(month) - GAME_START_MONTH) * TURNS_PER_MONTH
    + Number(half) - 1;
}

function turnLabel(year, month, half) {
  return `${year}年${String(month).padStart(2, "0")}月${Number(half) === 1 ? "上半月" : "下半月"}`;
}

function storageKey(seed = state.seed) {
  return `${STORAGE_VERSION}:${seed}`;
}

function loadStoredProgress(seed) {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey(seed)) || "null");
    return saved && typeof saved === "object" ? saved : {};
  } catch {
    return {};
  }
}

function saveProgress() {
  localStorage.setItem(storageKey(), JSON.stringify({
    schemaVersion: STORAGE_VERSION,
    seed: state.seed,
    turn: state.turn,
    savedAt: new Date().toISOString(),
  }));
}

function setMessage(message, tone = "") {
  const node = $("turnMessage");
  node.textContent = message;
  node.dataset.tone = tone;
}

function setDirectionalClass(node, value) {
  node.classList.remove("market-up", "market-down", "market-flat");
  node.classList.add(value > 0 ? "market-up" : value < 0 ? "market-down" : "market-flat");
}

function marketInstruments() {
  if (!state.futures) return [];
  return [
    { ...state.futures.mainContinuous, type: "main" },
    { ...state.futures.reference, type: "spot" },
    ...(state.futures.curve?.contracts || []).map((item) => ({
      ...item,
      instrument_id: item.contract_id,
      code: item.contract_id,
      type: "contract",
    })),
  ];
}

function selectedInstrument() {
  const instruments = marketInstruments();
  return instruments.find((item) => item.instrument_id === state.instrumentId)
    || instruments[0]
    || null;
}

function syncPeriodNav() {
  for (const item of $("periodNav").querySelectorAll("button")) {
    item.setAttribute("aria-pressed", item.dataset.period === state.period ? "true" : "false");
  }
}

function instrumentDescriptor(instrument = selectedInstrument()) {
  if (!instrument) return { name: "原油行情", kicker: "OIL", boundary: "正在装载行情。" };
  if (instrument.type === "main") {
    const lastRoll = instrument.rolls?.at(-1);
    const rollText = lastRoll
      ? `最近换月 ${lastRoll.label}：${lastRoll.from_contract_id} → ${lastRoll.to_contract_id}。`
      : "当前可见区间尚无换月。";
    return {
      name: "原油主连",
      kicker: `OIL · MAIN · ${instrument.active_contract_id}`,
      boundary: `完整显示2025年至当前的主连行情；01/05/09到期月上半月切换下一合约并按价格比回溯调整，成交量和持仓量直接继承当时的来源合约。${rollText}`,
    };
  }
  if (instrument.type === "spot") {
    return {
      name: "原油现货参考",
      kicker: "OIL · SPOT REFERENCE",
      boundary: "完整显示2025年至当前的现货参考行情；上半月只公开前两根周K，它是月份合约现金结算锚，不是可交易现货。",
    };
  }
  return {
    name: instrument.name,
    kicker: `${instrument.contract_id} · QUARTERLY FUTURE`,
    boundary: `${instrument.listing_label}挂牌，${instrument.expiry_label}到期；生命周期最多16根月K、64根周K、32个半月回合。成交量与周末持仓按全球期货市场规模模拟，当前为第${instrument.visible_turn_count || 0}回合。`,
  };
}

function visibleMonths() {
  const cutoff = dateFromTurn();
  return (selectedInstrument()?.monthly || [])
    .filter((item) => {
      const year = Number(item.year);
      const month = Number(item.month);
      return year >= OBSERVATION_START_YEAR
        && (year < cutoff.year || (year === cutoff.year && month <= cutoff.month));
    })
    .map((item) => ({
      year: Number(item.year),
      month: Number(item.month),
      open: Number(item.open),
      high: Number(item.high),
      low: Number(item.low),
      close: Number(item.close),
      volumeLots: Number(item.volume_lots),
      openInterestLots: Number(item.open_interest_lots),
      openInterestChangeLots: Number(item.open_interest_change_lots),
      visibleHalf: Number(item.visible_half || 2),
      title: Number(item.visible_half || 2) === 1
        ? `${item.year}-${String(item.month).padStart(2, "0")}-H1`
        : `${item.year}-${String(item.month).padStart(2, "0")}`,
      weekly: item.weekly || [],
      sourceContractId: item.source_contract_id,
      rollFromContractId: item.roll_from_contract_id,
    }));
}

function annualBars() {
  const cutoff = dateFromTurn();
  const years = new Map();
  for (const month of visibleMonths()) {
    if (!years.has(month.year)) years.set(month.year, []);
    years.get(month.year).push(month);
  }
  return [...years.entries()].map(([year, months]) => ({
    year,
    month: null,
    week: null,
    open: months[0].open,
    high: Math.max(...months.map((item) => item.high)),
    low: Math.min(...months.map((item) => item.low)),
    close: months.at(-1).close,
    volumeLots: months.reduce((sum, item) => sum + (Number.isFinite(item.volumeLots) ? item.volumeLots : 0), 0),
    openInterestLots: months.at(-1).openInterestLots,
    openInterestChangeLots: months.reduce((sum, item) => sum + (Number.isFinite(item.openInterestChangeLots) ? item.openInterestChangeLots : 0), 0),
    title: year === cutoff.year && cutoff.month < 12 ? `${year} YTD` : String(year),
  }));
}

function monthlyBars() {
  return visibleMonths().map(({ weekly, ...bar }) => ({ ...bar, week: null }));
}

function weeklyBars() {
  const bars = [];
  for (const month of visibleMonths()) {
    for (const week of month.weekly) {
      const weekNumber = Number(week.week);
      bars.push({
        year: month.year,
        month: month.month,
        week: weekNumber,
        open: Number(week.open),
        high: Number(week.high),
        low: Number(week.low),
        close: Number(week.close),
        volumeLots: Number(week.volume_lots),
        openInterestLots: Number(week.open_interest_lots),
        openInterestChangeLots: Number(week.open_interest_change_lots),
        title: `${month.year}-${String(month.month).padStart(2, "0")}-W${weekNumber}`,
        sourceContractId: month.sourceContractId,
        rollFromContractId: weekNumber === 1 ? month.rollFromContractId : null,
      });
    }
  }
  return bars;
}

function currentBars() {
  if (state.period === "annual") return annualBars();
  if (state.period === "monthly") return monthlyBars();
  return weeklyBars();
}

function latestMarketSnapshot(instrument = selectedInstrument()) {
  const months = (instrument?.monthly || []).map((item) => ({
    open: Number(item.open),
    high: Number(item.high),
    low: Number(item.low),
    close: Number(item.close),
  }));
  const latest = months.at(-1);
  const previous = months.at(-2);
  const change = latest && previous && previous.close
    ? 100 * (latest.close / previous.close - 1)
    : 0;
  return { latest, previous, change };
}

function renderClock() {
  const current = dateFromTurn();
  const progress = TOTAL_TURNS <= 1 ? 1 : state.turn / (TOTAL_TURNS - 1);
  $("gameMonth").textContent = turnLabel(current.year, current.month, current.half);
  $("clockCode").textContent = `${current.year}.${String(current.month).padStart(2, "0")}.H${current.half}`;
  $("turnCounter").textContent = `第 ${state.turn + 1} / ${TOTAL_TURNS} 回合`;
  $("timelineProgress").style.width = `${progress * 100}%`;
  $("timelineMarker").style.left = `${progress * 100}%`;
  $("jumpYear").value = String(current.year);
  $("jumpMonth").value = String(current.month);
  $("jumpHalf").value = String(current.half);
  $("nextTurn").disabled = state.turn >= TOTAL_TURNS - 1;
  $("nextTurn").textContent = state.turn >= TOTAL_TURNS - 1 ? "已到 2085年12月下半月" : "推进半个月";
}

function curveStateMeta() {
  const curveState = state.futures?.curve?.state || "flat";
  return {
    label: curveState === "contango" ? "升水结构" : curveState === "backwardation" ? "贴水结构" : "近月平坦",
    tone: curveState === "backwardation" ? "up" : curveState === "contango" ? "down" : "flat",
  };
}

function marketRowStatus(instrument) {
  if (instrument.type === "main") return { label: "主连", tone: curveStateMeta().tone };
  if (instrument.type === "spot") return { label: "结算锚", tone: "flat" };
  if (instrument.status === "expiring") return { label: "本月到期", tone: "expiry" };
  if (instrument.is_main_source) return { label: "主连来源", tone: "main" };
  return { label: "挂牌", tone: "listed" };
}

function renderMarketRows() {
  const rows = marketInstruments().map((instrument) => {
    const status = marketRowStatus(instrument);
    const change = Number(instrument.monthly_change_pct) || 0;
    const direction = change > 0 ? "market-up" : change < 0 ? "market-down" : "market-flat";
    const subtitle = instrument.type === "main"
      ? `当前映射 ${instrument.active_contract_id} · 比例复权`
      : instrument.type === "spot"
        ? "完整历史 · 到期结算锚"
        : `${instrument.listing_label}挂牌 · ${instrument.expiry_label}到期 · ${instrument.visible_turn_count || 0}/32回合`;
    return `<button class="market-row" type="button" role="row" data-instrument="${instrument.instrument_id}">
      <span class="market-name" role="cell"><i>${instrument.code}</i><strong>${instrument.name}</strong><small>${subtitle}</small></span>
      <span role="cell">${money(instrument.price_usd)}</span>
      <span class="${direction}" role="cell">${signedPct(change)}</span>
      <span role="cell">${money(instrument.monthly_high_usd)} / ${money(instrument.monthly_low_usd)}</span>
      <span role="cell"><em class="market-status" data-status="${status.tone}">${status.label}</em></span>
      <span class="market-open" role="cell">查看 K 线 →</span>
    </button>`;
  });
  $("marketRows").innerHTML = rows.join("");
}

function renderInstrumentHeader() {
  const instrument = selectedInstrument();
  if (!instrument) return;
  const descriptor = instrumentDescriptor(instrument);
  const { latest, change } = latestMarketSnapshot(instrument);
  $("instrumentKicker").textContent = descriptor.kicker;
  $("instrumentTitle").textContent = descriptor.name;
  $("dataBoundary").textContent = descriptor.boundary;
  $("detailLast").textContent = latest ? money(latest.close) : "—";
  $("detailChange").textContent = latest ? `${signedPct(change)} · 本月` : "—";
  setDirectionalClass($("detailChange"), change);
}

function renderContractSpecification() {
  const spec = state.futures?.contractSpecification;
  const policy = state.futures?.participantLimitsPolicy;
  if (!spec) return;
  $("specContractSize").textContent = `1手 = ${whole(spec.contract_size_bbl)}桶`;
  $("specQuoteUnit").textContent = "美元 / 桶";
  $("specPriceTick").textContent = `${money(spec.minimum_price_fluctuation_usd_per_bbl)} / 桶`;
  $("specTickValue").textContent = `${money(spec.tick_value_usd_per_lot)} / 手`;
  $("specInitialMargin").textContent = `${fmt(spec.initial_margin_rate_pct, 0)}%（账户执行）`;
  $("specMaintenanceMargin").textContent = `${fmt(spec.maintenance_margin_rate_pct, 0)}%（账户执行）`;
  $("specSettlement").textContent = "现金结算";
  $("specExpiryRule").textContent = "前月停交 · 到期月仅结算";
  if (policy) {
    $("specSinglePositionLimit").textContent = `min(持仓量 ${fmt(policy.single_contract_open_interest_rate_pct, 0)}%, ${whole(policy.single_contract_hard_cap_lots)}手)`;
    $("specGrossPositionLimit").textContent = `${whole(policy.all_contract_gross_position_cap_lots)}手`;
    $("specTurnTradeLimit").textContent = `近${whole(policy.turn_volume_reference_weeks)}周平滑 · 折算${whole(policy.turn_volume_equivalent_weeks)}周 × ${fmt(policy.turn_volume_rate_pct, 1)}%`;
    const expiryCaps = (policy.expiry_stepdown || []).map((item) => `${whole(item.position_cap_lots)}手`);
    $("specExpiryPositionLimit").textContent = `${expiryCaps.join(" → ")} → 仅结算`;
  }

  const instrument = selectedInstrument();
  const card = $("detailContractSpec");
  const showsFuturesSpec = instrument?.type === "contract" || instrument?.type === "main";
  card.hidden = !showsFuturesSpec;
  if (!showsFuturesSpec) return;
  const notional = Number(instrument.price_usd) * Number(spec.contract_size_bbl);
  $("detailLotSize").textContent = `${whole(spec.contract_size_bbl)}桶`;
  $("detailNotional").textContent = moneyWhole(notional);
  $("detailInitialMargin").textContent = `${moneyWhole(notional * Number(spec.initial_margin_rate_pct) / 100)} · ${fmt(spec.initial_margin_rate_pct, 0)}%`;
  $("detailMaintenanceMargin").textContent = `${moneyWhole(notional * Number(spec.maintenance_margin_rate_pct) / 100)} · ${fmt(spec.maintenance_margin_rate_pct, 0)}%`;
  const limits = instrument.participantLimits;
  if (limits) {
    $("detailSinglePositionLimit").textContent = `${whole(limits.single_contract_position_limit_lots)}手`;
    $("detailPositionLimitNotional").textContent = moneyWhole(limits.position_limit_notional_usd);
    $("detailGrossPositionLimit").textContent = `${whole(limits.all_contract_gross_position_cap_lots)}手`;
    $("detailTurnTradeLimit").textContent = limits.new_trades_allowed
      ? `${whole(limits.turn_trade_limit_lots)}手`
      : "0手 · 仅结算";
  }
  $("detailSpecNote").textContent = instrument.type === "main"
    ? `主连不可交易；持仓与成交限额映射当前合约 ${instrument.active_contract_id}。竞技账户只持有命名合约。`
    : limits && !limits.new_trades_allowed
      ? "该合约处于到期月，只做最终现金结算，不允许新交易；账户仍执行持仓、保证金和强制减险规则。"
      : "单合约上限取当前持仓量3%与75,000手较低者，并受临近到期规则收紧；竞技交易和账户逐回合执行。";
}

function contractStatus(item) {
  if (item.status === "expiring") return "本月到期";
  if (item.status === "front") return "主力";
  return "挂牌";
}

function renderFuturesCurveChart(contracts, spot) {
  const svg = $("futuresCurveChart");
  if (!contracts.length) {
    svg.innerHTML = "";
    return;
  }
  const width = 620;
  const height = 150;
  const margin = { left: 34, right: 24, top: 22, bottom: 34 };
  const values = [spot, ...contracts.map((item) => Number(item.futures_price_usd))];
  let min = Math.min(...values);
  let max = Math.max(...values);
  const padding = Math.max((max - min) * .25, Math.abs(max || 1) * .01);
  min -= padding;
  max += padding;
  const x = (index) => margin.left + index * (width - margin.left - margin.right) / Math.max(1, contracts.length - 1);
  const y = (value) => margin.top + (height - margin.top - margin.bottom) * (max - value) / Math.max(1e-9, max - min);
  const points = contracts.map((item, index) => `${x(index)},${y(Number(item.futures_price_usd))}`).join(" ");
  const parts = [
    `<line class="curve-spot-line" x1="${margin.left}" x2="${width - margin.right}" y1="${y(spot)}" y2="${y(spot)}"/>`,
    `<polyline class="curve-price-line" points="${points}"/>`,
  ];
  contracts.forEach((item, index) => {
    const cx = x(index);
    const cy = y(Number(item.futures_price_usd));
    const shortCode = item.contract_id;
    parts.push(
      `<circle class="curve-price-dot" cx="${cx}" cy="${cy}" r="4"><title>${item.contract_id} · ${money(item.futures_price_usd)}</title></circle>`,
      `<text class="curve-label" x="${cx}" y="${height - 10}" text-anchor="middle">${shortCode}</text>`,
    );
  });
  parts.push(`<text class="curve-spot-label" x="${width - margin.right}" y="${Math.max(11, y(spot) - 6)}" text-anchor="end">现货 ${money(spot)}</text>`);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = parts.join("");
}

function renderFutures() {
  const payload = state.futures;
  const contracts = payload?.curve?.contracts || [];
  if (!contracts.length) return;
  const curveState = payload.curve.state;
  const stateMeta = curveState === "contango"
    ? { label: "升水 / CONTANGO", note: "远月高于近月", tone: "contango" }
    : curveState === "backwardation"
      ? { label: "贴水 / BACKWARDATION", note: "远月低于近月", tone: "backwardation" }
      : { label: "平坦 / FLAT", note: "远近月价差较小", tone: "flat" };
  $("curveState").textContent = stateMeta.label;
  $("curveState").dataset.tone = stateMeta.tone;
  $("curveStateNote").textContent = stateMeta.note;
  $("spotReference").textContent = money(payload.reference.price_usd);
  $("frontContract").textContent = payload.curve.main_contract_id;
  $("frontCarry").textContent = signedPct(payload.curve.inputs.near_pressure_pct);
  renderFuturesCurveChart(contracts, Number(payload.reference.price_usd));
}

function renderLatestBar(bars) {
  const bar = bars.at(-1);
  if (!bar) return;
  $("latestBarKicker").textContent = "LATEST BAR";
  const change = bar.open ? 100 * (bar.close / bar.open - 1) : 0;
  const range = (bar.high + bar.low) ? 200 * (bar.high - bar.low) / (bar.high + bar.low) : 0;
  $("latestBarTitle").textContent = bar.title;
  $("barOpen").textContent = money(bar.open);
  $("barHigh").textContent = money(bar.high);
  $("barLow").textContent = money(bar.low);
  $("barClose").textContent = money(bar.close);
  $("barRange").textContent = `${fmt(range, 2)}%`;
  $("barChange").textContent = signedPct(change);
  setDirectionalClass($("barChange"), change);
  const hasLiquidity = selectedInstrument()?.type !== "spot"
    && Number.isFinite(bar.volumeLots)
    && Number.isFinite(bar.openInterestLots);
  $("liquidityMetrics").hidden = !hasLiquidity;
  if (hasLiquidity) {
    $("barVolumeLabel").textContent = `${periodLabel()}成交量`;
    $("barVolume").textContent = `${whole(bar.volumeLots)} 手`;
    $("barOpenInterest").textContent = `${whole(bar.openInterestLots)} 手`;
    $("barOpenInterestChange").textContent = `${signedWhole(bar.openInterestChangeLots)} 手`;
    setDirectionalClass($("barOpenInterestChange"), Number(bar.openInterestChangeLots));
  }
}

function periodLabel() {
  if (state.period === "annual") return "年 K";
  if (state.period === "monthly") return "月 K";
  return "周 K";
}

function renderAxis(height, margin, min, max, y) {
  const axis = $("gameChartAxis");
  axis.style.height = `${height}px`;
  const ticks = [];
  for (let i = 0; i <= 5; i++) {
    const value = min + (max - min) * i / 5;
    ticks.push(`<span style="top:${y(value)}px">${money(value)}</span>`);
  }
  axis.innerHTML = ticks.join("");
  axis.style.setProperty("--chart-top", `${margin.top}px`);
}

function visibleBarRange(bars, scrollLeft, viewportWidth, step, margin) {
  const start = clamp(Math.floor((scrollLeft - margin.left) / step) - 1, 0, bars.length - 1);
  const end = clamp(Math.ceil((scrollLeft + viewportWidth - margin.left) / step) + 1, start + 1, bars.length);
  return { start, end };
}

function shouldShowTick(bar, index, bars) {
  if (index === bars.length - 1) return true;
  if (state.period === "weekly") return bar.month === 1 && bar.week === 1;
  if (state.period === "monthly") return bar.month === 1;
  return index % 5 === 0;
}

function renderGameChart(scrollToEnd = false) {
  if (state.gameView !== "market" || state.screen !== "oil") return;
  const bars = currentBars();
  if (!bars.length) return;
  const descriptor = instrumentDescriptor();
  const svg = $("gameChart");
  const wrap = $("gameChartWrap");
  const viewportWidth = Math.max(wrap.clientWidth - PRICE_AXIS_WIDTH, 300);
  const visibleTarget = state.period === "weekly" ? 54 : state.period === "monthly" ? 42 : 28;
  const minStep = state.period === "weekly" ? 9 : state.period === "monthly" ? 13 : 20;
  const maxStep = state.period === "weekly" ? 14 : state.period === "monthly" ? 20 : 30;
  const step = clamp(Math.floor(viewportWidth / visibleTarget), minStep, maxStep);
  const bodyWidth = Math.max(state.period === "weekly" ? 4 : state.period === "monthly" ? 6 : 9, Math.floor(step * .58));
  const margin = { left: 18, right: PRICE_AXIS_WIDTH, top: 22, bottom: 38 };
  const width = Math.max(viewportWidth + PRICE_AXIS_WIDTH, margin.left + margin.right + step * bars.length);
  const height = Math.max(svg.clientHeight || 520, 330);
  const innerHeight = height - margin.top - margin.bottom;
  const hasLiquidity = selectedInstrument()?.type !== "spot"
    && bars.some((bar) => Number.isFinite(bar.volumeLots) && Number.isFinite(bar.openInterestLots));
  const priceHeight = hasLiquidity ? innerHeight * .71 : innerHeight;
  const liquidityTop = margin.top + priceHeight + (hasLiquidity ? 17 : 0);
  const liquidityBottom = height - margin.bottom;
  const liquidityHeight = Math.max(1, liquidityBottom - liquidityTop);
  svg.style.width = `${width}px`;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  const maxScroll = Math.max(0, width - wrap.clientWidth);
  const scrollLeft = scrollToEnd ? maxScroll : Math.min(wrap.scrollLeft, maxScroll);
  state.chartRenderLock = true;
  wrap.scrollLeft = scrollLeft;
  state.chartRenderLock = false;

  const visible = visibleBarRange(bars, scrollLeft, viewportWidth, step, margin);
  const scaleBars = bars.slice(visible.start, visible.end);
  const values = scaleBars.flatMap((bar) => [bar.open, bar.high, bar.low, bar.close]).filter(Number.isFinite);
  let min = values.length ? Math.min(...values) : 0;
  let max = values.length ? Math.max(...values) : 1;
  const padding = Math.max((max - min) * .12, Math.abs(max || 1) * .025, .000001);
  min -= padding;
  max += padding;
  const y = (value) => margin.top + priceHeight * (max - value) / Math.max(1e-12, max - min);
  const x = (index) => margin.left + step * index + step / 2;
  const parts = [`<rect width="${width}" height="${height}" fill="#090e14"/>`];
  for (let i = 0; i <= 5; i++) {
    const value = min + (max - min) * i / 5;
    parts.push(`<line class="grid" x1="${margin.left}" x2="${width - margin.right}" y1="${y(value)}" y2="${y(value)}"/>`);
  }
  let volumeY = null;
  let openInterestY = null;
  if (hasLiquidity) {
    const visibleVolumes = scaleBars.map((bar) => bar.volumeLots).filter(Number.isFinite);
    const visibleOpenInterest = scaleBars.map((bar) => bar.openInterestLots).filter(Number.isFinite);
    const volumeMax = Math.max(1, ...visibleVolumes) * 1.08;
    let openInterestMin = Math.min(...visibleOpenInterest);
    let openInterestMax = Math.max(...visibleOpenInterest);
    const openInterestPadding = Math.max((openInterestMax - openInterestMin) * .18, openInterestMax * .025, 1);
    openInterestMin = Math.max(0, openInterestMin - openInterestPadding);
    openInterestMax += openInterestPadding;
    volumeY = (value) => liquidityBottom - liquidityHeight * Number(value) / volumeMax;
    openInterestY = (value) => clamp(
      liquidityTop + liquidityHeight * (openInterestMax - Number(value)) / Math.max(1, openInterestMax - openInterestMin),
      liquidityTop,
      liquidityBottom,
    );
    parts.push(
      `<line class="liquidity-separator" x1="${margin.left}" x2="${width - margin.right}" y1="${liquidityTop - 9}" y2="${liquidityTop - 9}"/>`,
      `<text class="liquidity-label" x="${margin.left + 3}" y="${liquidityTop - 1}">VOL</text>`,
      `<text class="liquidity-label" x="${margin.left + 29}" y="${liquidityTop - 1}">OI</text>`,
    );
  }
  for (let i = 0; i < bars.length; i++) {
    const bar = bars[i];
    const color = bar.close > bar.open ? UP_COLOR : bar.close < bar.open ? DOWN_COLOR : FLAT_COLOR;
    const center = x(i);
    const bodyTop = Math.min(y(bar.open), y(bar.close));
    const bodyHeight = Math.max(2, Math.abs(y(bar.close) - y(bar.open)));
    const tooltip = `${bar.title} · 开 ${fmt(bar.open)} · 高 ${fmt(bar.high)} · 低 ${fmt(bar.low)} · 收 ${fmt(bar.close)}`;
    parts.push(`<line class="candle-wick" x1="${center}" x2="${center}" y1="${y(bar.high)}" y2="${y(bar.low)}" stroke="${color}"/>`);
    parts.push(`<rect class="candle-body" x="${center - bodyWidth / 2}" y="${bodyTop}" width="${bodyWidth}" height="${bodyHeight}" fill="${color}"><title>${tooltip}</title></rect>`);
    if (hasLiquidity && Number.isFinite(bar.volumeLots)) {
      const volumeTop = volumeY(bar.volumeLots);
      parts.push(
        `<rect class="volume-bar" x="${center - bodyWidth / 2}" y="${volumeTop}" width="${bodyWidth}" height="${Math.max(1, liquidityBottom - volumeTop)}" fill="${color}"><title>${bar.title} · 成交量 ${whole(bar.volumeLots)}手 · 持仓量 ${whole(bar.openInterestLots)}手</title></rect>`,
      );
    }
    if (bar.rollFromContractId) {
      parts.push(
        `<line class="game-roll-line" x1="${center}" x2="${center}" y1="${margin.top}" y2="${liquidityBottom}"/>`,
        `<text class="game-roll-label" x="${center + 4}" y="${margin.top + 12}">换月</text>`,
      );
    }
    if (shouldShowTick(bar, i, bars)) {
      const label = state.period === "annual" ? bar.year : `${bar.year}`;
      parts.push(`<text class="tick" x="${center}" y="${height - 13}" text-anchor="middle">${label}</text>`);
    }
  }
  if (hasLiquidity) {
    const openInterestPoints = bars
      .map((bar, index) => Number.isFinite(bar.openInterestLots) ? `${x(index)},${openInterestY(bar.openInterestLots)}` : null)
      .filter(Boolean)
      .join(" ");
    parts.push(`<polyline class="oi-line" points="${openInterestPoints}"/>`);
  }
  const currentIndex = bars.length - 1;
  const currentCenter = x(currentIndex);
  parts.push(`<line class="game-now-line" x1="${currentCenter}" x2="${currentCenter}" y1="${margin.top}" y2="${liquidityBottom}"/>`);
  svg.innerHTML = parts.join("");
  const cutoff = dateFromTurn();
  svg.setAttribute("aria-label", `${descriptor.name}${periodLabel()}，截至${turnLabel(cutoff.year, cutoff.month, cutoff.half)}${hasLiquidity ? "，含成交量与持仓量" : ""}`);
  renderAxis(height, margin, min, max, y);
  renderLatestBar(bars);
  $("gameChartTitle").textContent = `${descriptor.name} · ${periodLabel()}`;
  $("chartCutoff").textContent = `截至 ${turnLabel(cutoff.year, cutoff.month, cutoff.half)}`;
  const latest = bars.at(-1);
  const liquidityText = hasLiquidity
    ? ` · 成交 ${whole(latest.volumeLots)}手 · 持仓 ${whole(latest.openInterestLots)}手`
    : "";
  $("visibleWindow").textContent = `可视区间 ${bars[visible.start].title} — ${bars[visible.end - 1].title} · 共 ${bars.length} 根已生成 K 线${liquidityText}`;
}

const APPOINTMENT_LABELS = {
  forecast: "预测",
  strategy: "策略研究",
  risk: "公司风控",
  execution: "交易执行",
};

function appointmentNote(kind, appointment) {
  if (kind === "forecast") return `能力 ${fmt(appointment.capability_total_score, 1)}`;
  if (kind === "execution") return `能力 ${fmt(appointment.capability_total_score, 1)}${appointment.tags?.length ? ` · ${appointment.tags.join(" / ")}` : ""}`;
  return appointment.tags?.length ? appointment.tags.join(" / ") : "均衡授权";
}

function renderCommitteeRoster() {
  const participants = state.competition?.participants || [];
  $("committeeRoster").innerHTML = participants.map((participant) => {
    const appointments = participant.appointments || {};
    const seats = Object.entries(APPOINTMENT_LABELS).map(([kind, label]) => {
      const appointment = appointments[kind] || {};
      return `<div class="committee-seat">
        <span>${label}</span>
        <strong>${escapeHtml(appointment.display_name || "—")}</strong>
        <small>${escapeHtml(appointmentNote(kind, appointment))}</small>
      </div>`;
    }).join("");
    return `<article class="committee-card${participant.is_player ? " is-player" : ""}">
      <header><span>${participant.is_player ? "PLAYER" : "AI RIVAL"}</span><h4>${escapeHtml(participant.display_name)}</h4></header>
      <div class="committee-seats">${seats}</div>
    </article>`;
  }).join("");
}

function renderCompetitionLeaderboard() {
  const rows = state.competition?.leaderboard || [];
  $("competitionTurnCount").textContent = state.competition?.completed_turns
    ? `已完成 ${whole(state.competition.completed_turns)} 个半月结算`
    : "尚未结算";
  $("competitionLeaderboard").innerHTML = rows.map((row) => {
    const returnClass = Number(row.cumulative_return_pct) > 0 ? "market-up" : Number(row.cumulative_return_pct) < 0 ? "market-down" : "market-flat";
    return `<div class="competition-row${row.is_player ? " is-player" : ""}" role="row">
      <span role="cell"><b>${row.rank}</b><strong>${escapeHtml(row.display_name)}</strong>${row.is_player ? "<em>你</em>" : ""}</span>
      <span role="cell">${moneyWhole(row.equity_usd)}</span>
      <span role="cell" class="${returnClass}">${signedPct(row.cumulative_return_pct)}</span>
      <span role="cell">${fmt(row.maximum_drawdown_pct, 2)}%</span>
      <span role="cell">${whole(row.gross_position_lots)}手</span>
    </div>`;
  }).join("");
}

function reportLabel(report) {
  const from = report.from_as_of;
  const to = report.to_as_of;
  return `${turnLabel(from.year, from.month, from.half)} → ${turnLabel(to.year, to.month, to.half)}`;
}

function selectedTurnReport() {
  const reports = state.competition?.report_history || [];
  return reports.find((report) => report.report_id === state.selectedReportId)
    || reports[0]
    || null;
}

function riskStatusLabel(status) {
  return ({ normal: "正常", watch: "观察", restricted: "受限", reduce_only: "仅减险" })[status] || status || "—";
}

function accountStatusLabel(status) {
  return ({
    normal: "正常",
    reduce_only: "只减仓",
    forced_liquidation: "已强平",
    insolvent: "破产",
  })[status] || status || "—";
}

function renderTurnReport() {
  const reports = state.competition?.report_history || [];
  const select = $("turnReportSelect");
  if (!reports.length) {
    state.selectedReportId = null;
    select.disabled = true;
    select.innerHTML = '<option value="">暂无报告</option>';
    $("turnReportTitle").textContent = "等待首个回合结算";
    $("turnReportMarket").textContent = "推进到下一半月后，将生成第一份同场竞技报告。";
    $("turnReportRows").innerHTML = "";
    return;
  }
  if (!reports.some((report) => report.report_id === state.selectedReportId)) {
    state.selectedReportId = reports[0].report_id;
  }
  select.disabled = false;
  select.innerHTML = reports.map((report) => (
    `<option value="${escapeHtml(report.report_id)}"${report.report_id === state.selectedReportId ? " selected" : ""}>第${whole(report.turn_number)}回合 · ${escapeHtml(reportLabel(report))}</option>`
  )).join("");
  const report = selectedTurnReport();
  $("turnReportTitle").textContent = `第 ${whole(report.turn_number)} 回合报告`;
  const market = report.market;
  $("turnReportMarket").innerHTML = `<strong>${escapeHtml(reportLabel(report))}</strong><span>${escapeHtml(market.main_contract_id)} ${money(market.main_price_before_usd)} → ${money(market.main_price_after_usd)}</span><em class="${Number(market.main_return_pct) >= 0 ? "market-up" : "market-down"}">${signedPct(market.main_return_pct)}</em><small>${escapeHtml(market.curve_state_before)} → ${escapeHtml(market.curve_state_after)}</small>`;
  const participants = [...(report.participants || [])].sort((left, right) => Number(left.rank) - Number(right.rank));
  $("turnReportRows").innerHTML = participants.map((row) => {
    const pnlClass = Number(row.turn_pnl_usd) > 0 ? "market-up" : Number(row.turn_pnl_usd) < 0 ? "market-down" : "market-flat";
    const riskText = `${riskStatusLabel(row.risk_status)}${Number(row.risk_clipped_gross_lots) ? ` · 裁剪${whole(row.risk_clipped_gross_lots)}手` : ""}`;
    const marginText = row.margin_to_equity_pct == null ? "—" : `${fmt(row.margin_to_equity_pct, 1)}%`;
    const accountEvent = row.margin_call_triggered
      ? ` · 追保 ${moneyWhole(row.margin_call_amount_usd)}`
      : Number(row.forced_liquidation_lots)
        ? ` · 强平 ${whole(row.forced_liquidation_lots)}手`
        : "";
    return `<div class="competition-row report-row${row.is_player ? " is-player" : ""}" role="row">
      <span role="cell"><b>${row.rank}</b><strong>${escapeHtml(row.display_name)}</strong>${row.is_player ? "<em>你</em>" : ""}</span>
      <span role="cell" class="${pnlClass}">${signedMoneyWhole(row.turn_pnl_usd)}<small>${signedPct(row.turn_return_pct)}</small></span>
      <span role="cell">${signedPct(row.cumulative_return_pct)}</span>
      <span role="cell">${whole(row.buy_lots)} / ${whole(row.sell_lots)}手</span>
      <span role="cell">${moneyWhole(row.execution_cost_usd)}</span>
      <span role="cell">${escapeHtml(riskText)}</span>
      <span role="cell">${escapeHtml(accountStatusLabel(row.account_status))} · ${marginText}<small>可用 ${moneyWhole(row.available_funds_usd)}${escapeHtml(accountEvent)}</small></span>
    </div>`;
  }).join("");
}

function renderInvestment() {
  renderCommitteeRoster();
  renderCompetitionLeaderboard();
  renderTurnReport();
}

function scheduleChartRescale() {
  if (state.chartRenderLock || state.gameView !== "market" || state.screen !== "oil") return;
  if (state.chartFrame) cancelAnimationFrame(state.chartFrame);
  state.chartFrame = requestAnimationFrame(() => {
    state.chartFrame = null;
    renderGameChart(false);
  });
}

function renderScreens(scrollChartToEnd = false) {
  const marketView = state.gameView === "market";
  $("marketScreen").hidden = !marketView || state.screen !== "market";
  $("oilScreen").hidden = !marketView || state.screen !== "oil";
  $("investmentScreen").hidden = state.gameView !== "investment";
  for (const button of $("gamePrimaryNav").querySelectorAll("button[data-game-view]")) {
    button.setAttribute("aria-pressed", button.dataset.gameView === state.gameView ? "true" : "false");
  }
  if (marketView && state.screen === "oil") renderGameChart(scrollChartToEnd);
}

function syncUrl() {
  const url = new URL(location.href);
  url.pathname = "/game";
  url.search = "";
  url.searchParams.set("seed", String(state.seed));
  if (state.gameView === "investment") {
    url.searchParams.set("view", "investment");
  } else if (state.screen === "oil") {
    url.searchParams.set("market", "oil");
    url.searchParams.set("instrument", state.instrumentId);
    url.searchParams.set("period", state.period);
  }
  history.replaceState(null, "", url);
  $("backToViewer").href = `/?seed=${encodeURIComponent(state.seed)}&years=60`;
}

function renderAll(scrollChartToEnd = false) {
  syncPeriodNav();
  renderClock();
  renderMarketRows();
  renderFutures();
  renderInstrumentHeader();
  renderContractSpecification();
  renderInvestment();
  renderScreens(scrollChartToEnd);
  syncUrl();
  const current = dateFromTurn();
  $("gameIdentity").textContent = `Seed ${state.seed} · 回合 ${state.turn + 1}/${TOTAL_TURNS} · 数据截至 ${turnLabel(current.year, current.month, current.half)}`;
}

async function requestFutures(seed, turn) {
  const cutoff = dateFromTurn(turn);
  const query = new URLSearchParams({
    seed: String(seed),
    years: "60",
    year: String(cutoff.year),
    month: String(cutoff.month),
    half: String(cutoff.half),
  });
  const response = await fetch(`/api/oil-futures?${query}`);
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error || "原油期货曲线装载失败");
  if (payload.asOf?.year !== cutoff.year || payload.asOf?.month !== cutoff.month || payload.asOf?.half !== cutoff.half) {
    throw new Error("原油期货曲线的时间截点不一致");
  }
  if ((payload.curve?.contracts || []).length !== 4) {
    throw new Error("原油月份合约没有保持四个01/05/09挂牌月份");
  }
  return payload;
}

async function requestCompetition(seed, turn) {
  const cutoff = dateFromTurn(turn);
  const query = new URLSearchParams({
    seed: String(seed),
    years: "60",
    year: String(cutoff.year),
    month: String(cutoff.month),
    half: String(cutoff.half),
  });
  const response = await fetch(`/api/oil-investment-competition?${query}`);
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error || "投资决策竞技装载失败");
  if (payload.asOf?.year !== cutoff.year || payload.asOf?.month !== cutoff.month || payload.asOf?.half !== cutoff.half) {
    throw new Error("投资决策竞技的时间截点不一致");
  }
  if ((payload.participants || []).length !== 4 || (payload.leaderboard || []).length !== 4) {
    throw new Error("投资决策竞技没有保持玩家与三家竞争对手");
  }
  return payload;
}

function applyFuturesPayload(payload) {
  state.futures = payload;
  if (!marketInstruments().some((item) => item.instrument_id === state.instrumentId)) {
    state.instrumentId = "OIL-MAIN";
  }
  state.identity = payload.identity;
}

async function refreshWorld() {
  const [futures, competition] = await Promise.all([
    requestFutures(state.seed, state.turn),
    requestCompetition(state.seed, state.turn),
  ]);
  applyFuturesPayload(futures);
  state.competition = competition;
  state.selectedReportId = competition.latest_report?.report_id || null;
}

function setTurnBusy(busy) {
  $("nextTurn").disabled = busy || state.turn >= TOTAL_TURNS - 1;
  $("jumpForm").querySelector("button[type='submit']").disabled = busy;
}

async function advanceOneTurn({ render = true } = {}) {
  if (state.turn >= TOTAL_TURNS - 1) return false;
  state.turn += 1;
  if (render) {
    await refreshWorld();
    saveProgress();
    renderAll(true);
  }
  return true;
}

async function advanceTo(target) {
  const next = clamp(Math.trunc(target), 0, TOTAL_TURNS - 1);
  if (next < state.turn) {
    setMessage("普通推进不能回到过去；如需重新开始请使用“重置游戏”。", "error");
    return;
  }
  if (next === state.turn) {
    setMessage("已经处于该半月回合。", "neutral");
    return;
  }
  const previousTurn = state.turn;
  setTurnBusy(true);
  try {
    while (state.turn < next) await advanceOneTurn({ render: false });
    await refreshWorld();
    saveProgress();
    renderAll(true);
    const current = dateFromTurn();
    setMessage(`已按半月顺序推进到 ${turnLabel(current.year, current.month, current.half)}。`, "success");
  } catch (error) {
    state.turn = previousTurn;
    await refreshWorld();
    renderAll(false);
    setMessage(error.message, "error");
  } finally {
    setTurnBusy(false);
  }
}

function disarmReset() {
  state.resetArmed = false;
  clearTimeout(state.resetTimer);
  $("resetGame").textContent = "重置游戏";
  $("resetGame").classList.remove("is-armed");
}

async function loadWorld(seed) {
  if (!Number.isInteger(seed) || seed < 0) throw new Error("Seed 必须是非负整数");
  $("gameStatus").textContent = `正在装载 Seed ${seed}…`;
  const stored = loadStoredProgress(seed);
  const nextTurn = clamp(Number(stored.turn) || 0, 0, TOTAL_TURNS - 1);
  state.seed = seed;
  state.turn = nextTurn;
  await refreshWorld();
  $("gameSeedInput").value = String(seed);
  $("gameStatus").textContent = `游戏世界 · Seed ${seed}`;
  disarmReset();
  renderAll(true);
}

$("gameSeedForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await loadWorld(Number($("gameSeedInput").value));
    setMessage(`Seed ${state.seed} 已加载。`, "success");
  } catch (error) {
    $("gameStatus").textContent = error.message;
    setMessage(error.message, "error");
  }
});

$("gameMarketPicker").addEventListener("submit", (event) => event.preventDefault());

$("nextTurn").addEventListener("click", async () => {
  const previousTurn = state.turn;
  setTurnBusy(true);
  try {
    if (!await advanceOneTurn()) {
      setMessage("已经到达 2085年12月下半月。", "neutral");
      return;
    }
    setMessage(`已结算 ${whole(state.competition?.completed_turns || 0)} 个半月回合。`, "success");
  } catch (error) {
    state.turn = previousTurn;
    await refreshWorld();
    renderAll(false);
    setMessage(error.message, "error");
  } finally {
    setTurnBusy(false);
  }
});

$("jumpForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const year = Number($("jumpYear").value);
  const month = Number($("jumpMonth").value);
  const half = Number($("jumpHalf").value);
  if (!Number.isInteger(year) || !Number.isInteger(month) || ![1, 2].includes(half) || month < 1 || month > 12 || year < GAME_START_YEAR || year > GAME_END_YEAR) {
    setMessage("目标月份必须在 2030年01月—2085年12月之间。", "error");
    return;
  }
  await advanceTo(turnFromDate(year, month, half));
});

$("resetGame").addEventListener("click", async () => {
  if (!state.resetArmed) {
    state.resetArmed = true;
    $("resetGame").textContent = "确认重置";
    $("resetGame").classList.add("is-armed");
    setMessage("再次点击“确认重置”才会回到 2030年01月上半月。", "error");
    state.resetTimer = setTimeout(disarmReset, 5000);
    return;
  }
  const previousTurn = state.turn;
  state.turn = 0;
  setTurnBusy(true);
  try {
    await refreshWorld();
    saveProgress();
    disarmReset();
    renderAll(true);
    setMessage("当前 Seed 的游戏进度已重置到2030年01月上半月。", "success");
  } catch (error) {
    state.turn = previousTurn;
    await refreshWorld();
    renderAll(false);
    setMessage(error.message, "error");
  } finally {
    setTurnBusy(false);
  }
});

$("marketRows").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-instrument]");
  if (!button) return;
  state.instrumentId = button.dataset.instrument;
  state.screen = "oil";
  renderAll(true);
});

$("backToMarkets").addEventListener("click", () => {
  state.screen = "market";
  renderAll(false);
});

$("gamePrimaryNav").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-game-view]");
  if (!button) return;
  state.gameView = button.dataset.gameView === "investment" ? "investment" : "market";
  renderAll(false);
});

$("turnReportSelect").addEventListener("change", (event) => {
  state.selectedReportId = event.target.value || null;
  renderTurnReport();
});

$("periodNav").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-period]");
  if (!button) return;
  const requestedPeriod = ["weekly", "monthly", "annual"].includes(button.dataset.period)
    ? button.dataset.period
    : "weekly";
  state.period = requestedPeriod;
  renderAll(true);
});

$("gameChartWrap").addEventListener("scroll", scheduleChartRescale, { passive: true });
$("gameChartWrap").addEventListener("pointerdown", (event) => {
  state.chartDrag = { pointerId: event.pointerId, x: event.clientX, scrollLeft: $("gameChartWrap").scrollLeft };
  $("gameChartWrap").setPointerCapture(event.pointerId);
  $("gameChartWrap").classList.add("is-dragging");
});
$("gameChartWrap").addEventListener("pointermove", (event) => {
  if (state.chartDrag?.pointerId !== event.pointerId) return;
  $("gameChartWrap").scrollLeft = state.chartDrag.scrollLeft - (event.clientX - state.chartDrag.x) * 1.1;
});
const endChartDrag = (event) => {
  if (state.chartDrag?.pointerId !== event.pointerId) return;
  state.chartDrag = null;
  $("gameChartWrap").classList.remove("is-dragging");
};
$("gameChartWrap").addEventListener("pointerup", endChartDrag);
$("gameChartWrap").addEventListener("pointercancel", endChartDrag);

window.addEventListener("resize", () => {
  if (state.gameView === "market" && state.screen === "oil") renderGameChart(false);
});

async function boot() {
  const params = new URLSearchParams(location.search);
  const seed = Number(params.get("seed") ?? 42);
  state.gameView = params.get("view") === "investment" ? "investment" : "market";
  state.screen = params.get("market") === "oil" ? "oil" : "market";
  state.instrumentId = params.get("instrument") || "OIL-MAIN";
  state.period = ["weekly", "monthly", "annual"].includes(params.get("period"))
    ? params.get("period")
    : "weekly";
  await loadWorld(seed);
}

boot().catch((error) => {
  $("gameStatus").textContent = error.message;
  setMessage(error.message, "error");
});
