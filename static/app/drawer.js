// 观澜 · drawer — per-stock decision panel (chart, tech check, options, fundamentals,
// thesis notes, price alerts, sizing, news, own fills).
import {
  $, h, api, store, on, emit, isNum, num, signed, pct, money, price, updown, compact, ago, ukTime, dateLabel,
  shortTicker, findPosition, cssVar, savePref, debounce, clamp, isUSListing, toast, qty,
} from "./core.js";
import { meter, tip } from "./charts.js";
import { addWatch, removeWatch, livePrice, ensureNotifyPermission } from "./ui.js";

let cur = null;          // { key, sym, t212, ... }
let chartCtx = null;     // lightweight-charts handles

const LW_URL = "https://cdn.jsdelivr.net/npm/lightweight-charts@5.2.1/dist/lightweight-charts.standalone.production.js";
let lwPromise = null;
function loadLW() {
  if (window.LightweightCharts) return Promise.resolve(window.LightweightCharts);
  lwPromise ||= new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = LW_URL; s.async = true;
    s.onload = () => res(window.LightweightCharts);
    s.onerror = () => rej(new Error("图表库加载失败（检查网络）"));
    document.head.append(s);
  });
  return lwPromise;
}
const barDate = (t) => new Date((t + 43200) * 1000).toISOString().slice(0, 10);

// ───────────────────────────── open / close ─────────────────────────────
export function openStock(key) {
  const k = String(key || "").trim();
  if (!k) return;
  const pos = findPosition(k);
  const sym = pos ? shortTicker(pos.ticker) : k.toUpperCase().replace(/_.*$/, "");
  cur = { key: pos ? pos.ticker : sym, sym, pos, seq: (cur?.seq || 0) + 1 };
  const d = $("#drawer");
  d.hidden = false;
  requestAnimationFrame(() => d.classList.add("in"));
  document.body.classList.add("drawer-open");
  const hash = new URLSearchParams(location.hash.slice(1));
  hash.set("stock", sym);
  history.replaceState(null, "", "#" + hash.toString());
  render();
}
export function closeDrawer() {
  const d = $("#drawer");
  if (!d || d.hidden) return;
  d.classList.remove("in");
  document.body.classList.remove("drawer-open");
  destroyChart();
  setTimeout(() => { d.hidden = true; }, 220);
  cur = null;
  const hash = new URLSearchParams(location.hash.slice(1));
  hash.delete("stock");
  history.replaceState(null, "", hash.toString() ? "#" + hash.toString() : location.pathname);
}
export const drawerOpen = () => !!cur;

function destroyChart() {
  try { chartCtx?.chart?.remove(); } catch { /* ignore */ }
  chartCtx = null;
}

// ───────────────────────────── skeleton ─────────────────────────────
function section(id, title, sub = null) {
  return h("section", { class: "dsec", id: `ds-${id}` },
    h("h3", { class: "dsec-h" }, title, sub ? h("span", { class: "dsec-sub" }, sub) : null),
    h("div", { class: "dsec-b" }, h("div", { class: "muted pad" }, "加载中…")));
}
function render() {
  const c = cur;
  const body = $("#drawer-body");
  destroyChart();
  body.scrollTop = 0;
  const held = !!c.pos;
  const inWatch = (store.prefs.watchlist || []).includes(c.sym);
  body.replaceChildren(
    h("header", { class: "dh" },
      h("div", { class: "dh-id" },
        h("img", { class: "dh-logo", id: "dh-logo", alt: "", hidden: true }),
        h("div", {}, h("div", { class: "dh-sym" }, c.sym, h("span", { class: "dh-held" }, held ? "持仓" : inWatch ? "自选" : "未持有")),
          h("div", { class: "dh-name", id: "dh-name" }, c.pos?.name || ""))),
      h("div", { class: "dh-px" }, h("div", { class: "dh-price num", id: "dh-price" }, "—"), h("div", { class: "dh-chg num", id: "dh-chg" }, "")),
      h("div", { class: "dh-actions" },
        h("button", { class: "btn btn-sm", type: "button", id: "dh-watch", onclick: () => {
          if ((store.prefs.watchlist || []).includes(c.sym)) removeWatch(c.sym); else addWatch(c.sym);
          setTimeout(() => { $("#dh-watch").textContent = (store.prefs.watchlist || []).includes(c.sym) ? "★ 已自选" : "☆ 加自选"; }, 50);
        } }, inWatch ? "★ 已自选" : "☆ 加自选"),
        h("button", { class: "btn btn-sm", type: "button", onclick: () => openTradingView(c) }, "TradingView ↗"),
        h("button", { class: "icon-btn", type: "button", "aria-label": "关闭", onclick: closeDrawer }, "×"))),
    h("div", { class: "dh-meta", id: "dh-meta" }),
    h("blockquote", { class: "ask" }, "以现价，我还会买它吗？",
      h("span", {}, "答案不会一秒变一次 —— 这才是该问的问题")),
    h("section", { class: "dsec", id: "ds-chart" },
      h("div", { class: "chart-bar" },
        h("div", { class: "seg", id: "chart-range" }, ...[["3M", 63], ["6M", 126], ["1Y", 252], ["2Y", 504]].map(([l, n]) =>
          h("button", { type: "button", class: `seg-btn${l === "6M" ? " on" : ""}`, dataset: { n, l }, onclick: (e) => setRange(e.currentTarget) }, l))),
        h("div", { class: "chart-toggles", id: "chart-toggles" },
          ...[["ma", "均线", true], ["boll", "布林", false], ["walls", "期权墙", true], ["orders", "挂单/提醒", true], ["fills", "买卖点", true]].map(([k, l, on0]) =>
            h("label", { class: "tg" }, h("input", { type: "checkbox", checked: on0, dataset: { k }, onchange: () => applyOverlays() }), l)))),
      h("div", { class: "chart-legend num", id: "chart-legend" }),
      h("div", { class: "chart", id: "chart" }, h("div", { class: "muted pad" }, "K 线加载中…"))),
    section("tech", "技术面体检", "看趋势、动能、位置 —— 不是买卖信号"),
    section("options", "期权定位", "未平仓合约堆出的支撑 / 阻力"),
    section("fund", "基本面快照"),
    section("notes", "我的逻辑", "写下来，才不会被每天的涨跌带着走"),
    section("alerts", "价格提醒", "触发时页面弹窗 + 系统通知"),
    section("size", "仓位计算器", "按可承受亏损反推股数 · 工具不是建议"),
    section("myfills", "我的成交", "这只股票的历史买卖"),
    section("news", "新闻", "近 7 天"));
  paintHeader();
  loadAll(c);
}

function paintHeader() {
  const c = cur;
  if (!c) return;
  const pos = findPosition(c.key);
  const lp = livePrice(c.sym);
  const q = store.quotes[c.sym];
  const px = pos ? pos.currentPrice : lp?.price ?? c.techPrice;
  const ccy = pos?.currency || "USD";
  const dp = pos ? pos.dayChangePct : q?.changePct;
  const pe = $("#dh-price"), ce = $("#dh-chg");
  if (pe) pe.textContent = price(px, ccy);
  if (ce) { ce.textContent = isNum(dp) ? `${pct(dp)} 今日` : ""; ce.className = `dh-chg num ${updown(dp)}`; }
  const meta = $("#dh-meta");
  if (meta && pos) {
    const tot = (store.snap?.positions || []).reduce((a, p) => a + (p.marketValue || 0), 0) || 1;
    const w = (pos.marketValue || 0) / tot * 100;
    const parts = [`持有 ${qty(pos.quantity)} 股`, h("span", { class: "money" }, money(pos.marketValue)), `占持仓 ${w.toFixed(1)}%`];
    if ((pos.accounts || []).length > 1) parts.push(pos.accounts.map((a) => `${a.label} ${qty(a.quantity)}`).join(" · "));
    meta.replaceChildren(...parts.flatMap((p, i) => (i ? [h("span", { class: "sep" }, "·"), p] : [p])));
  }
}
on("snap", () => { if (cur) { paintHeader(); liveCandle(); } });
on("quotes", () => { if (cur && !cur.pos) { paintHeader(); liveCandle(); } });
on("escape", () => closeDrawer());
on("theme", () => { if (cur && chartCtx) buildChart(chartCtx.data, chartCtx.range); });

// ───────────────────────────── data ─────────────────────────────
async function loadAll(c) {
  const seq = c.seq;
  const alive = () => cur && cur.seq === seq;
  const sym = c.sym, key = c.key;
  const us = isUSListing(key) || isUSListing(sym);
  const jobs = {
    candles: api(`/api/candles?ticker=${encodeURIComponent(key)}&range=2y`),
    tech: api(`/api/tech?ticker=${encodeURIComponent(key)}`),
    bench: api(`/api/tech?ticker=SMH`).catch(() => null),
    profile: us ? api(`/api/profile?ticker=${encodeURIComponent(sym)}`).catch(() => null) : Promise.resolve(null),
    news: us ? api(`/api/news?tickers=${encodeURIComponent(sym)}&days=7&per=10`).catch(() => null) : Promise.resolve(null),
    levels: store.levels[key] ? Promise.resolve({ levels: [store.levels[key]] })
      : us ? api(`/api/levels?tickers=${encodeURIComponent(sym)}`).catch(() => null) : Promise.resolve(null),
    fills: store.fills ? Promise.resolve(store.fills) : api(`/api/fills?days=730&limit=2000`).then((f) => { store.fills = f; return f; }).catch(() => null),
  };
  jobs.tech.then((t) => { if (!alive()) return; c.tech = t; c.techPrice = t?.price; paintHeader(); renderTech(t); renderSize(t); })
    .catch((e) => alive() && fail("tech", e));
  jobs.bench.then((b) => { if (alive()) { c.bench = b; if (c.tech) renderTech(c.tech); } });
  jobs.levels.then((lv) => { if (!alive()) return; c.levels = (lv?.levels || [])[0] || null; renderOptions(c.levels); applyOverlays(); renderAlerts(); });
  jobs.profile.then((p) => { if (!alive()) return; c.profile = p; renderFund(p, us); if (p?.profile?.name) $("#dh-name").textContent = p.profile.name + (p.profile.finnhubIndustry ? ` · ${p.profile.finnhubIndustry}` : ""); if (p?.profile?.logo) { const im = $("#dh-logo"); im.src = p.profile.logo; im.hidden = false; im.onerror = () => { im.hidden = true; }; } });
  jobs.news.then((n) => alive() && renderNews(n, us));
  jobs.fills.then((f) => { if (!alive()) return; c.fills = (f?.fills || []).filter((x) => x.ticker === key || x.symbol === sym || x.short === sym); renderMyFills(c.fills); applyOverlays(); });
  jobs.candles.then(async (d) => {
    if (!alive()) return;
    if (!d?.bars?.length) { $("#chart").replaceChildren(h("div", { class: "muted pad" }, d?.error || "没有 K 线数据")); return; }
    try { await loadLW(); } catch (e) { $("#chart").replaceChildren(h("div", { class: "muted pad" }, e.message)); return; }
    if (alive()) buildChart(d, 126);
  }).catch((e) => alive() && $("#chart").replaceChildren(h("div", { class: "muted pad" }, `K 线加载失败：${e.message}`)));
  renderNotes();
  renderAlerts();
}
function fail(id, e) { const b = $(`#ds-${id} .dsec-b`); if (b) b.replaceChildren(h("div", { class: "muted pad" }, `加载失败：${e.message || e}`)); }

// ───────────────────────────── chart ─────────────────────────────
function sma(vals, n) {
  const out = new Array(vals.length).fill(null);
  let s = 0;
  for (let i = 0; i < vals.length; i++) { s += vals[i]; if (i >= n) s -= vals[i - n]; if (i >= n - 1) out[i] = s / n; }
  return out;
}
function rsiSeries(vals, n = 14) {
  const out = new Array(vals.length).fill(null);
  if (vals.length <= n) return out;
  let g = 0, l = 0;
  for (let i = 1; i <= n; i++) { const d = vals[i] - vals[i - 1]; if (d >= 0) g += d; else l -= d; }
  g /= n; l /= n;
  out[n] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
  for (let i = n + 1; i < vals.length; i++) {
    const d = vals[i] - vals[i - 1];
    g = (g * (n - 1) + Math.max(d, 0)) / n; l = (l * (n - 1) + Math.max(-d, 0)) / n;
    out[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
  }
  return out;
}
function bollinger(vals, n = 20, k = 2) {
  const up = new Array(vals.length).fill(null), lo = new Array(vals.length).fill(null);
  for (let i = n - 1; i < vals.length; i++) {
    const w = vals.slice(i - n + 1, i + 1), m = w.reduce((a, b) => a + b, 0) / n;
    const sd = Math.sqrt(w.reduce((a, b) => a + (b - m) ** 2, 0) / n);
    up[i] = m + k * sd; lo[i] = m - k * sd;
  }
  return { up, lo };
}

function buildChart(data, range) {
  const LW = window.LightweightCharts;
  const el = $("#chart");
  if (!LW || !el) return;
  destroyChart();
  el.replaceChildren();
  const C = (n) => cssVar(n);
  const chart = LW.createChart(el, {
    autoSize: true,
    layout: { background: { type: "solid", color: C("--surface") }, textColor: C("--muted"), fontFamily: "Inter, 'PingFang SC', system-ui, sans-serif", fontSize: 11, panes: { separatorColor: C("--line"), separatorHoverColor: C("--line") } },
    grid: { vertLines: { color: C("--grid") }, horzLines: { color: C("--grid") } },
    rightPriceScale: { borderColor: C("--line"), scaleMargins: { top: 0.08, bottom: 0.06 } },
    timeScale: { borderColor: C("--line"), rightOffset: 4, minBarSpacing: 2 },
    crosshair: { mode: LW.CrosshairMode.Normal, vertLine: { color: C("--muted"), labelBackgroundColor: C("--ink-2") }, horzLine: { color: C("--muted"), labelBackgroundColor: C("--ink-2") } },
    localization: { locale: "zh-CN" },
  });
  const bars = data.bars;
  const times = bars.map((b) => barDate(b.t));
  const closes = bars.map((b) => b.c);
  const up = C("--up"), down = C("--down");
  const candles = chart.addSeries(LW.CandlestickSeries, { upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false, priceLineColor: C("--muted") });
  candles.setData(bars.map((b, i) => ({ time: times[i], open: b.o, high: b.h, low: b.l, close: b.c })));
  // volume + RSI each get their own pane — sharing the price pane's scale
  // pushed nonsense negative ticks onto the price axis
  const vol = chart.addSeries(LW.HistogramSeries, { priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false }, 1);
  vol.setData(bars.map((b, i) => ({ time: times[i], value: b.v || 0, color: (b.c >= b.o ? up : down) + "66" })));
  const mkLine = (color, width = 1, pane = 0) => chart.addSeries(LW.LineSeries, { color, lineWidth: width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, pane);
  const toLine = (arr) => arr.map((v, i) => (v == null ? { time: times[i] } : { time: times[i], value: v }));
  const ma = { ma20: sma(closes, 20), ma50: sma(closes, 50), ma200: sma(closes, 200) };
  const maS = { ma20: mkLine(C("--s1")), ma50: mkLine(C("--s2")), ma200: mkLine(C("--s3")) };
  for (const k in maS) maS[k].setData(toLine(ma[k]));
  const bb = bollinger(closes);
  const bbS = { up: mkLine(C("--band-line")), lo: mkLine(C("--band-line")) };
  bbS.up.setData(toLine(bb.up)); bbS.lo.setData(toLine(bb.lo));
  const rsi = rsiSeries(closes);
  const rsiS = chart.addSeries(LW.LineSeries, { color: C("--accent"), lineWidth: 1, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false, priceFormat: { type: "price", precision: 0, minMove: 1 } }, 2);
  rsiS.setData(toLine(rsi));
  for (const lvl of [70, 30]) rsiS.createPriceLine({ price: lvl, color: C("--muted"), lineWidth: 1, lineStyle: LW.LineStyle.Dotted, axisLabelVisible: false });
  try { const ps = chart.panes(); ps[1].setHeight(54); ps[2].setHeight(72); } catch { /* older API */ }

  chartCtx = { chart, LW, data, candles, vol, maS, bbS, rsiS, times, bars, ma, rsi, lines: [], markers: null, range };
  const leg = $("#chart-legend");
  const legend = (i) => {
    const b = bars[i];
    if (!b) return;
    const ccy = cur?.pos?.currency || "USD";
    const chg = i > 0 ? (b.c / bars[i - 1].c - 1) * 100 : null;
    leg.replaceChildren(
      h("span", { class: "lg-d" }, times[i]),
      h("span", {}, "开 ", h("b", {}, price(b.o, ccy))), h("span", {}, "高 ", h("b", {}, price(b.h, ccy))),
      h("span", {}, "低 ", h("b", {}, price(b.l, ccy))), h("span", {}, "收 ", h("b", { class: updown(chg) }, `${price(b.c, ccy)} ${isNum(chg) ? pct(chg) : ""}`)),
      h("span", { class: "lg-ma" }, h("i", { style: { background: C("--s1") } }), `MA20 ${num(ma.ma20[i], 2)}`),
      h("span", { class: "lg-ma" }, h("i", { style: { background: C("--s2") } }), `MA50 ${num(ma.ma50[i], 2)}`),
      h("span", { class: "lg-ma" }, h("i", { style: { background: C("--s3") } }), `MA200 ${num(ma.ma200[i], 2)}`),
      h("span", { class: "lg-ma" }, h("i", { style: { background: C("--accent") } }), `RSI ${num(rsi[i], 0)}`));
  };
  legend(bars.length - 1);
  chart.subscribeCrosshairMove((p) => {
    if (!p || !p.time) { legend(bars.length - 1); return; }
    const i = times.indexOf(typeof p.time === "string" ? p.time : `${p.time.year}-${String(p.time.month).padStart(2, "0")}-${String(p.time.day).padStart(2, "0")}`);
    if (i >= 0) legend(i);
  });
  showRange(range);
  applyOverlays();
  liveCandle();
}
function setRange(btn) {
  [...btn.parentNode.children].forEach((b) => b.classList.toggle("on", b === btn));
  if (chartCtx) { chartCtx.range = +btn.dataset.n; showRange(+btn.dataset.n); }
}
function showRange(n) {
  const c = chartCtx;
  if (!c) return;
  const len = c.bars.length;
  c.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, len - n), to: len + 3 });
}
function toggles() {
  const o = {};
  document.querySelectorAll("#chart-toggles input").forEach((i) => { o[i.dataset.k] = i.checked; });
  return o;
}
function applyOverlays() {
  const c = chartCtx;
  if (!c || !cur) return;
  const t = toggles();
  const LW = c.LW;
  for (const s of Object.values(c.maS)) s.applyOptions({ visible: !!t.ma });
  for (const s of Object.values(c.bbS)) s.applyOptions({ visible: !!t.boll });
  for (const pl of c.lines) { try { c.candles.removePriceLine(pl); } catch { /* ignore */ } }
  c.lines = [];
  const add = (p, color, title, style = LW.LineStyle.Dashed) => {
    if (!isNum(p)) return;
    c.lines.push(c.candles.createPriceLine({ price: p, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }));
  };
  const lv = cur.levels;
  if (t.walls && lv && !lv.error) {
    add(lv.putWall, cssVar("--up"), "Put 墙");
    add(lv.callWall, cssVar("--down"), "Call 墙");
    if (isNum(lv.maxPain) && lv.maxPain !== lv.putWall && lv.maxPain !== lv.callWall) add(lv.maxPain, cssVar("--muted"), "MaxPain", LW.LineStyle.Dotted);
    if (!lv.ivSuspect) { add(lv.emUpper, cssVar("--band-line"), "预期上沿", LW.LineStyle.Dotted); add(lv.emLower, cssVar("--band-line"), "预期下沿", LW.LineStyle.Dotted); }
  }
  if (t.orders) {
    for (const o of (store.orders?.orders || []).filter((o) => o.ticker === cur.key || o.symbol === cur.sym)) {
      add(o.trigger, cssVar("--accent"), `${o.side === "SELL" ? "卖" : "买"}单 ${qty(o.quantity)}`, LW.LineStyle.Solid);
    }
    for (const a of (store.prefs.alerts || []).filter((a) => !a.firedAt && a.ticker.toUpperCase() === cur.sym.toUpperCase())) {
      add(a.price, cssVar("--warn"), `提醒 ${a.op === ">=" ? "≥" : "≤"}`, LW.LineStyle.LargeDashed);
    }
  }
  const fills = t.fills ? (cur.fills || []) : [];
  const days = new Map();
  for (const f of fills) {
    const d = barDate(Math.floor(new Date(f.filledAt).getTime() / 1000));
    const k = `${d}|${f.side}`;
    const e = days.get(k) || { d, side: f.side, qty: 0, n: 0 };
    e.qty += f.quantity; e.n += 1;
    days.set(k, e);
  }
  const first = c.times[0];
  const markers = [...days.values()].filter((e) => e.d >= first).sort((a, b) => a.d.localeCompare(b.d)).map((e) => ({
    time: c.times.includes(e.d) ? e.d : c.times.find((x) => x >= e.d) || c.times[c.times.length - 1],
    position: e.side === "BUY" ? "belowBar" : "aboveBar",
    color: e.side === "BUY" ? cssVar("--s1") : cssVar("--accent"),
    shape: e.side === "BUY" ? "arrowUp" : "arrowDown",
    size: 0.8,
  })).sort((a, b) => a.time.localeCompare(b.time));
  try {
    if (!c.markers) c.markers = LW.createSeriesMarkers(c.candles, markers);
    else c.markers.setMarkers(markers);
  } catch { /* markers API unavailable */ }
}
function liveCandle() {
  const c = chartCtx;
  if (!c || !cur) return;
  const lp = livePrice(cur.sym);
  if (!lp || !isNum(lp.price)) return;
  const last = c.bars[c.bars.length - 1];
  const sess = store.market?.session;
  // only extend the last (current-session) candle; never invent a new one
  if (!last || !(sess === "regular" || sess === "post" || sess === "pre")) return;
  const today = new Date().toISOString().slice(0, 10);
  const lastD = c.times[c.times.length - 1];
  if (lastD < today && sess !== "regular") return;
  const bar = { time: lastD, open: last.o, high: Math.max(last.h, lp.price), low: Math.min(last.l, lp.price), close: lp.price };
  try { c.candles.update(bar); } catch { /* ignore */ }
}

// ───────────────────────────── tech check ─────────────────────────────
function verdict(text, cls) { return h("span", { class: `verdict ${cls}` }, text); }
function renderTech(t) {
  const b = $("#ds-tech .dsec-b");
  if (!b) return;
  if (!t || t.error) { b.replaceChildren(h("div", { class: "muted pad" }, t?.error || "技术数据暂不可用")); return; }
  const td = t.today || {}, pv = t.prev || {};
  const px = t.price;
  const rel = (ma) => (isNum(ma) && ma ? (px - ma) / ma * 100 : null);
  const r20 = rel(td.ma20), r50 = rel(td.ma50), r200 = rel(td.ma200);
  const bull = isNum(td.ma20) && isNum(td.ma50) && isNum(td.ma200) && px > td.ma20 && td.ma20 > td.ma50 && td.ma50 > td.ma200;
  const bear = isNum(td.ma20) && isNum(td.ma50) && px < td.ma20 && td.ma20 < td.ma50;
  const trendV = bull ? verdict("多头排列", "good") : bear ? verdict("空头排列", "bad") : verdict("均线纠缠", "neutral");
  const rsi = td.rsi;
  const rsiWord = !isNum(rsi) ? "—" : rsi >= 70 ? "超买区" : rsi >= 60 ? "健康偏强" : rsi >= 45 ? "中性" : rsi >= 30 ? "偏弱" : "超卖区";
  const macdBull = (td.macd ?? 0) >= (td.signal ?? 0);
  let histDir = "";
  if (isNum(td.hist) && isNum(pv.hist)) histDir = td.hist >= 0 && pv.hist < 0 ? "柱刚转正" : td.hist < 0 && pv.hist >= 0 ? "柱刚转负" : Math.abs(td.hist) > Math.abs(pv.hist) ? "柱在放大" : "柱在收敛";
  const momV = isNum(rsi) && rsi >= 70 ? verdict("偏热", "warn") : macdBull ? verdict("动能向上", "good") : verdict("动能向下", "bad");
  const bb = t.bollinger || {};
  const pb = bb.percent_b;
  const bbWord = !isNum(pb) ? "—" : pb > 1 ? "冲出上轨" : pb >= 0.8 ? "贴近上轨" : pb < 0 ? "跌破下轨" : pb <= 0.2 ? "贴近下轨" : "通道中部";
  const hi = t.high_252d, lo = t.low_252d;
  const fromHi = isNum(hi) ? (px / hi - 1) * 100 : null, fromLo = isNum(lo) ? (px / lo - 1) * 100 : null;
  const volRatio = isNum(t.last_vol) && isNum(t.avg_vol20) && t.avg_vol20 ? t.last_vol / t.avg_vol20 : null;
  const ccy = cur?.pos?.currency || "USD";
  const bench = cur?.bench;
  const rs21 = isNum(t.ret_21d) && isNum(bench?.ret_21d) ? t.ret_21d - bench.ret_21d : null;
  const rs63 = isNum(t.ret_63d) && isNum(bench?.ret_63d) ? t.ret_63d - bench.ret_63d : null;
  const pos52 = isNum(hi) && isNum(lo) && hi > lo ? clamp((px - lo) / (hi - lo), 0, 1) : null;
  const row = (k, v, main, hint) => h("div", { class: "tk" },
    h("div", { class: "tk-k" }, k, v), h("div", { class: "tk-v" }, main), hint ? h("div", { class: "tk-hint" }, hint) : null);
  b.replaceChildren(h("div", { class: "tk-grid" },
    row("趋势", trendV, h("span", {}, "MA20 ", h("b", { class: updown(r20) }, pct(r20, 1)), " · MA50 ", h("b", { class: updown(r50) }, pct(r50, 1)), " · MA200 ", h("b", { class: updown(r200) }, pct(r200, 1))),
      "价格在均线上方 = 这段时间买的人大多赚钱；MA20>MA50>MA200 叫多头排列。"),
    row("动能", momV, h("span", {}, `RSI ${num(rsi, 0)} ${rsiWord} · MACD ${macdBull ? "多头" : "空头"}${histDir ? " · " + histDir : ""}`),
      "RSI>70 是超买区 —— 超买≠马上跌，强势股能在这里待很久；MACD 柱放大 = 动能在加速。"),
    row("波动", isNum(t.atr_pct) ? verdict(`日波动 ±${t.atr_pct.toFixed(1)}%`, t.atr_pct >= 5 ? "warn" : "neutral") : null,
      h("span", {}, `布林 %b ${num(pb, 2)} ${bbWord}${isNum(t.atr14) ? ` · ATR ${price(t.atr14, ccy)}` : ""}`),
      isNum(t.atr14) ? `正常一天就能上下晃 ${price(t.atr14, ccy)}。止损设得比这还近，基本等于被噪音洗出去。` : null),
    row("位置", pos52 != null ? verdict(`52 周区间 ${(pos52 * 100).toFixed(0)}% 处`, pos52 >= 0.85 ? "warn" : pos52 <= 0.2 ? "bad" : "neutral") : null,
      h("div", { class: "pos52" },
        h("span", { class: "muted num" }, price(lo, ccy)),
        h("span", { class: "pos52-track" }, pos52 != null ? h("i", { style: { left: `${pos52 * 100}%` } }) : null),
        h("span", { class: "muted num" }, price(hi, ccy))),
      `距一年高点 ${pct(fromHi, 1)}，距一年低点 ${pct(fromLo, 0)}。`),
    row("量能", isNum(volRatio) ? verdict(`${volRatio.toFixed(1)}× 均量`, volRatio >= 1.5 ? "warn" : "neutral") : null,
      h("span", {}, `最新成交 ${compact(t.last_vol, 1)} · 20 日均量 ${compact(t.avg_vol20, 1)}`),
      "放量上涨 = 有资金认；放量下跌要小心；缩量 = 大家在观望。"),
    row("相对强弱", isNum(rs63) ? verdict(rs63 >= 0 ? "跑赢半导体" : "跑输半导体", rs63 >= 0 ? "good" : "bad") : null,
      h("span", {}, `近 1 月 ${pct(t.ret_21d, 1)}${isNum(rs21) ? `（vs SMH ${pct(rs21, 1)}）` : ""} · 近 3 月 ${pct(t.ret_63d, 1)}${isNum(rs63) ? `（vs SMH ${pct(rs63, 1)}）` : ""}`),
      "和板块比，才知道是它自己强，还是整个行业在涨。")),
    h("div", { class: "tk-foot muted" }, `日线来源 ${t.source || "—"} · 最新 K 线 ${t.last_bar || "—"}${t.live_patch ? "（已用实时价补齐）" : ""}`));
}

// ───────────────────────────── options ─────────────────────────────
function renderOptions(lv) {
  const b = $("#ds-options .dsec-b");
  if (!b) return;
  if (!lv || lv.error) { b.replaceChildren(h("div", { class: "muted pad" }, lv?.error ? `无期权墙：${lv.error}` : "非美股 / 无期权链")); return; }
  const px = lv.spot || cur?.pos?.currentPrice;
  const pts = [lv.putWall, lv.callWall, lv.emLower, lv.emUpper, lv.maxPain, px].filter(isNum);
  const lo = Math.min(...pts) * 0.97, hi = Math.max(...pts) * 1.03;
  const X = (v) => `${clamp((v - lo) / (hi - lo), 0, 1) * 100}%`;
  const em = !lv.ivSuspect && isNum(lv.emLower) && isNum(lv.emUpper);
  const scale = h("div", { class: "opt-scale" },
    em ? h("span", { class: "opt-em", style: { left: X(lv.emLower), width: `calc(${X(lv.emUpper)} - ${X(lv.emLower)})` }, title: "期权隐含的 1σ 预期区间" }) : null,
    h("span", { class: "opt-mark put", style: { left: X(lv.putWall) } }, h("b", {}, "Put 墙"), h("i", {}, num(lv.putWall, 0))),
    h("span", { class: "opt-mark call", style: { left: X(lv.callWall) } }, h("b", {}, "Call 墙"), h("i", {}, num(lv.callWall, 0))),
    isNum(lv.maxPain) ? h("span", { class: "opt-mark pain", style: { left: X(lv.maxPain) } }, h("b", {}, "MaxPain"), h("i", {}, num(lv.maxPain, 0))) : null,
    isNum(px) ? h("span", { class: "opt-now", style: { left: X(px) } }, h("b", {}, "现价"), h("i", {}, num(px, 2))) : null);
  const cells = [
    ["到期", `${lv.expiry}（${lv.days} 天）`],
    ["Put 墙", `${num(lv.putWall, 0)} · ${compact(lv.putWallOI, 1)} 张 · ${pct((lv.putWall / px - 1) * 100, 1)}`],
    ["Call 墙", `${num(lv.callWall, 0)} · ${compact(lv.callWallOI, 1)} 张 · ${pct((lv.callWall / px - 1) * 100, 1)}`],
    ["预期波动", em ? `±${num(lv.expMove, 0)}（±${(lv.expMove / px * 100).toFixed(1)}%）· IV ${(lv.atmIV * 100).toFixed(0)}%` : "IV 数据异常，暂不显示"],
  ];
  b.replaceChildren(scale, h("div", { class: "kv" }, ...cells.map(([k, v]) => [h("span", {}, k), h("b", { class: "num" }, v)])),
    h("div", { class: "tk-hint" }, "Put 墙 = 最多人押「跌不破」的价位，常起支撑；Call 墙 = 最多人押「涨不过」的价位，常成阻力。到期日前后引力最强，只是参考，不是保证。"));
}

// ───────────────────────────── fundamentals ─────────────────────────────
function renderFund(p, us) {
  const b = $("#ds-fund .dsec-b");
  if (!b) return;
  if (!us || !p || !p.supported) { b.replaceChildren(h("div", { class: "muted pad" }, "非美股：Finnhub 免费档没有基本面数据")); return; }
  const m = p.metrics || {}, pr = p.profile || {};
  const cap = isNum(pr.marketCapitalization) ? (pr.marketCapitalization >= 1e6 ? `$${(pr.marketCapitalization / 1e6).toFixed(2)}T` : `$${(pr.marketCapitalization / 1e3).toFixed(1)}B`) : "—";
  const kv = [
    ["市值", cap], ["市盈率 TTM", num(m.peTTM, 1)], ["市销率", num(m.psTTM, 1)], ["Beta", num(m.beta, 2)],
    ["营收增速 TTM", pct(m.revGrowthTTM, 1)], ["最新季度营收", pct(m.revGrowthQ, 1)], ["毛利率", pct(m.grossMargin, 1, false)],
    ["净利率", pct(m.netMargin, 1, false)], ["EPS TTM", num(m.epsTTM, 2)], ["股息率", isNum(m.divYield) ? pct(m.divYield, 2, false) : "—"],
    ["近 13 周", pct(m.ret13w, 1)], ["近 52 周", pct(m.ret52w, 0)],
  ];
  const rec = (p.recommendation || [])[0];
  let recEl = null;
  if (rec) {
    const parts = [["strongBuy", "强买"], ["buy", "买入"], ["hold", "持有"], ["sell", "卖出"], ["strongSell", "强卖"]];
    const tot = parts.reduce((a, [k]) => a + (rec[k] || 0), 0) || 1;
    recEl = h("div", { class: "rec" },
      h("div", { class: "rec-h" }, `分析师评级 · ${rec.period?.slice(0, 7)} · ${tot} 位`),
      h("div", { class: "rec-bar" }, ...parts.filter(([k]) => rec[k]).map(([k, l]) => h("span", { class: `rec-${k}`, style: { flexGrow: rec[k] }, title: `${l} ${rec[k]}` }))),
      h("div", { class: "rec-leg" }, ...parts.map(([k, l]) => h("span", {}, h("i", { class: `rec-${k}` }), `${l} ${rec[k] || 0}`))));
  }
  const ne = p.nextEarnings;
  const lv = cur?.levels;
  let neEl = null;
  if (ne) {
    const hour = { bmo: "盘前", amc: "盘后", dmh: "盘中" }[ne.hour] || "";
    neEl = h("div", { class: "earn-next" },
      h("div", {}, h("b", {}, `下次财报 ${dateLabel(ne.date)} ${hour}`), ` · Q${ne.quarter} ${ne.year}`),
      h("div", { class: "muted" }, `EPS 预期 ${num(ne.epsEstimate, 2)} · 营收预期 ${isNum(ne.revenueEstimate) ? "$" + compact(ne.revenueEstimate, 1) : "—"}`,
        lv && !lv.ivSuspect && isNum(lv.atmIV) && lv.expiry >= ne.date ? ` · 期权定价到 ${lv.expiry} 的波动 ±${(lv.expMove / lv.spot * 100).toFixed(1)}%` : ""));
  }
  const hist = (p.earnings || []).map((e) => h("div", { class: "eh" },
    h("span", { class: "muted" }, `${e.year} Q${e.quarter}`), h("span", { class: "num" }, `实际 ${num(e.actual, 2)} / 预期 ${num(e.estimate, 2)}`),
    h("b", { class: `num ${updown(e.surprisePercent)}` }, `${pct(e.surprisePercent, 1)}`)));
  b.replaceChildren(
    neEl,
    h("div", { class: "kv kv-3" }, ...kv.map(([k, v]) => [h("span", {}, k), h("b", { class: "num" }, v)])),
    recEl,
    hist.length ? h("div", { class: "earn-hist" }, h("div", { class: "rec-h" }, "近 4 季 EPS 超预期幅度"), ...hist) : null,
    (p.peers || []).length ? h("div", { class: "peers" }, h("span", { class: "muted" }, "同业 "), ...p.peers.map((s) =>
      h("button", { type: "button", class: "chip chip-sm", onclick: () => openStock(s) }, s))) : null);
}

// ───────────────────────────── notes ─────────────────────────────
function renderNotes() {
  const b = $("#ds-notes .dsec-b");
  if (!b || !cur) return;
  const sym = cur.sym;
  const n = (store.prefs.notes || {})[sym] || {};
  const status = h("span", { class: "note-status muted" }, n.updatedAt ? `上次更新 ${ago(n.updatedAt)}` : "自动保存");
  const save = debounce(() => {
    const notes = { ...(store.prefs.notes || {}) };
    notes[sym] = { thesis: ta1.value, exit: ta2.value, updatedAt: Date.now() / 1000 };
    if (!ta1.value.trim() && !ta2.value.trim()) delete notes[sym];
    savePref("notes", notes, 0).then((ok) => { status.textContent = ok ? "已保存 ✓" : "保存失败"; });
  }, 700);
  const ta1 = h("textarea", { rows: 3, placeholder: "为什么持有 / 想买？（基本面定方向：它赚的是什么钱？）", oninput: () => { status.textContent = "…"; save(); } });
  const ta2 = h("textarea", { rows: 2, placeholder: "什么情况下我会卖？（逻辑失效的信号、价格、时间）", oninput: () => { status.textContent = "…"; save(); } });
  ta1.value = n.thesis || ""; ta2.value = n.exit || "";
  b.replaceChildren(h("label", { class: "note-l" }, "买入逻辑"), ta1, h("label", { class: "note-l" }, "卖出条件"), ta2, status);
}

// ───────────────────────────── alerts ─────────────────────────────
function renderAlerts() {
  const b = $("#ds-alerts .dsec-b");
  if (!b || !cur) return;
  const sym = cur.sym;
  const lp = livePrice(sym);
  const ccy = cur.pos?.currency || lp?.ccy || "USD";
  const mine = (store.prefs.alerts || []).filter((a) => a.ticker.toUpperCase() === sym.toUpperCase());
  const op = h("select", { "aria-label": "条件" }, h("option", { value: "<=" }, "跌到 ≤"), h("option", { value: ">=" }, "涨到 ≥"));
  const inp = h("input", { type: "number", step: "any", placeholder: "价格", "aria-label": "提醒价格", class: "num" });
  const note = h("input", { type: "text", placeholder: "备注（可选）", maxlength: 60 });
  const add = async (o, p, nt = "") => {
    if (!isNum(p) || p <= 0) { toast("请输入有效价格", { kind: "error" }); return; }
    const a = { id: `al${Date.now()}`, ticker: sym, op: o, price: +p.toFixed(4), note: nt, createdAt: Date.now() / 1000, firedAt: null };
    await savePref("alerts", [...(store.prefs.alerts || []), a], 0);
    ensureNotifyPermission();
    renderAlerts(); applyOverlays(); emit("alerts-changed");
  };
  const quick = [];
  const t = cur.tech, lv = cur.levels, px = lp?.price;
  if (t?.today?.ma20) quick.push([t.today.ma20 < (px ?? 0) ? "<=" : ">=", t.today.ma20, "MA20"]);
  if (lv && !lv.error && isNum(lv.putWall)) quick.push(["<=", lv.putWall, "Put 墙"]);
  if (lv && !lv.error && isNum(lv.callWall)) quick.push([">=", lv.callWall, "Call 墙"]);
  if (isNum(px)) quick.push(["<=", px * 0.9, "−10%"], [">=", px * 1.1, "+10%"]);
  const list = mine.length ? mine.map((a) => {
    const dist = isNum(px) ? (a.price - px) / px * 100 : null;
    return h("div", { class: `al-row${a.firedAt ? " fired" : ""}` },
      h("span", { class: "num" }, `${a.op === ">=" ? "≥" : "≤"} ${price(a.price, ccy)}`),
      h("span", { class: "muted" }, a.firedAt ? `已触发 ${ago(a.firedAt)}` : isNum(dist) ? `差 ${pct(dist, 1)}` : ""),
      h("span", { class: "al-note" }, a.note || ""),
      a.firedAt ? h("button", { class: "btn btn-xs", type: "button", onclick: async () => {
        await savePref("alerts", (store.prefs.alerts || []).map((x) => (x.id === a.id ? { ...x, firedAt: null } : x)), 0); renderAlerts(); applyOverlays();
      } }, "重新启用") : null,
      h("button", { class: "icon-btn sm", type: "button", "aria-label": "删除提醒", onclick: async () => {
        await savePref("alerts", (store.prefs.alerts || []).filter((x) => x.id !== a.id), 0); renderAlerts(); applyOverlays(); emit("alerts-changed");
      } }, "×"));
  }) : [h("div", { class: "muted" }, "还没有提醒。")];
  b.replaceChildren(
    h("div", { class: "al-list" }, ...list),
    h("form", { class: "al-form", onsubmit: (e) => { e.preventDefault(); add(op.value, parseFloat(inp.value), note.value.trim()); } },
      op, inp, note, h("button", { class: "btn btn-sm btn-accent", type: "submit" }, "添加")),
    quick.length ? h("div", { class: "al-quick" }, h("span", { class: "muted" }, "快捷："), ...quick.map(([o, p, l]) =>
      h("button", { type: "button", class: "chip chip-sm", onclick: () => add(o, p, l) }, `${l} ${price(p, ccy)}`))) : null);
}

// ───────────────────────────── sizing ─────────────────────────────
function renderSize(t) {
  const b = $("#ds-size .dsec-b");
  if (!b || !cur) return;
  const acct = store.snap?.stats?.totalValue || 0;
  const px0 = livePrice(cur.sym)?.price ?? t?.price;
  const atr = t?.atr14;
  const ccy = cur.pos?.currency || "USD";
  const fx = store.market?.fx?.GBPUSD?.price;
  const gbpPer = ccy === "USD" && isNum(fx) ? 1 / fx : ccy === "GBX" ? 0.01 : 1;
  const risk = h("input", { type: "number", step: "0.25", min: "0.1", max: "10", value: "1", class: "num" });
  const entry = h("input", { type: "number", step: "any", value: isNum(px0) ? px0.toFixed(2) : "", class: "num" });
  const stopI = h("input", { type: "number", step: "any", value: isNum(px0) && isNum(atr) ? (px0 - 2 * atr).toFixed(2) : "", class: "num" });
  const out = h("div", { class: "size-out" });
  const calc = () => {
    const r = parseFloat(risk.value) / 100, e = parseFloat(entry.value), s = parseFloat(stopI.value);
    if (!(r > 0) || !(e > 0) || !(s > 0) || s >= e) { out.replaceChildren(h("span", { class: "muted" }, "止损价需低于入场价")); return; }
    const riskGBP = acct * r;
    const perShareGBP = (e - s) * gbpPer;
    const shares = riskGBP / perShareGBP;
    const posGBP = shares * e * gbpPer;
    const held = cur.pos ? cur.pos.quantity : 0;
    out.replaceChildren(
      h("div", { class: "kv" },
        h("span", {}, "可承受亏损"), h("b", { class: "num money" }, money(riskGBP)),
        h("span", {}, "每股风险"), h("b", { class: "num" }, `${price(e - s, ccy)}（${pct((e - s) / e * 100, 1, false)}）`),
        h("span", {}, "最多买"), h("b", { class: "num" }, `${num(shares, shares < 10 ? 2 : 0)} 股`),
        h("span", {}, "对应仓位"), h("b", { class: "num money" }, `${money(posGBP)} · 占账户 ${pct(posGBP / acct * 100, 1, false)}`),
        held ? h("span", {}, "已持有") : null, held ? h("b", { class: "num" }, `${qty(held)} 股`) : null),
      isNum(atr) ? h("div", { class: "tk-hint" }, `止损距离 ${((e - s) / atr).toFixed(1)}× ATR。小于 1× ATR 的止损，正常波动就会碰到。`) : null);
  };
  for (const i of [risk, entry, stopI]) i.addEventListener("input", calc);
  b.replaceChildren(h("div", { class: "size-in" },
    h("label", {}, "单笔最多亏账户的", risk, "%"),
    h("label", {}, "入场价", entry),
    h("label", {}, "止损价", stopI, isNum(atr) ? h("span", { class: "muted" }, "（默认 = 入场 − 2×ATR）") : null)), out);
  calc();
}

// ───────────────────────────── fills & news ─────────────────────────────
function renderMyFills(fills) {
  const b = $("#ds-myfills .dsec-b");
  if (!b) return;
  if (!fills?.length) { b.replaceChildren(h("div", { class: "muted pad" }, "没有成交记录")); return; }
  const rows = fills.slice(0, 12).map((f) => h("tr", {},
    h("td", { class: "muted" }, new Date(f.filledAt).toLocaleDateString("en-GB", { day: "2-digit", month: "2-digit", year: "2-digit" })),
    h("td", {}, h("span", { class: `side ${f.side === "BUY" ? "buy" : "sell"}` }, f.side === "BUY" ? "买" : "卖")),
    h("td", { class: "num" }, qty(f.quantity)),
    h("td", { class: "num" }, price(f.price, f.currency)),
    h("td", { class: "num muted money" }, money(Math.abs(f.value || 0))),
    h("td", { class: "muted" }, f.accountLabel)));
  const buys = fills.filter((f) => f.side === "BUY").length;
  b.replaceChildren(h("div", { class: "muted small" }, `共 ${fills.length} 笔（买 ${buys} · 卖 ${fills.length - buys}）· 图上箭头 = 买卖点`),
    h("table", { class: "mini" }, h("tbody", {}, ...rows)));
}
function renderNews(n, us) {
  const b = $("#ds-news .dsec-b");
  if (!b) return;
  if (!us) { b.replaceChildren(h("div", { class: "muted pad" }, "非美股：暂无新闻源")); return; }
  const items = n?.items || [];
  if (!items.length) { b.replaceChildren(h("div", { class: "muted pad" }, "近 7 天没有新闻")); return; }
  b.replaceChildren(...items.slice(0, 10).map((x) => h("a", { class: "news-row", href: x.url, target: "_blank", rel: "noopener noreferrer" },
    h("span", { class: "news-t" }, x.headline), h("span", { class: "news-m" }, `${x.source || ""} · ${ago(x.datetime)}`))));
}

// ───────────────────────────── TradingView (full charting) ─────────────────────────────
function openTradingView(c) {
  const sym = c.sym;
  const url = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(sym)}`;
  window.open(url, "_blank", "noopener");
}

on("open-stock", (k) => openStock(k));
on("alerts-changed", () => emit("brief"));
