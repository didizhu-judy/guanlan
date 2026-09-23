// 观澜 · core — API client, shared store, formatting, prefs, polling.
// Everything else imports from here; nothing here touches layout.

// The DOM stringifies null children ("null" text). Renderers here build child
// lists as `cond ? node : null`, so drop empties at the one place they land.
for (const proto of [Element.prototype, DocumentFragment.prototype]) {
  for (const m of ["replaceChildren", "append"]) {
    const orig = proto[m];
    proto[m] = function (...kids) { return orig.apply(this, kids.filter((k) => k != null && k !== false)); };
  }
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

/** Tiny DOM builder. Strings become text nodes (never parsed as HTML). */
export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

// ─────────────────────────────── API ───────────────────────────────
export async function api(path, opts = {}) {
  const init = { ...opts, headers: { Accept: "application/json", "X-Guanlan-UI": "2", ...(opts.headers || {}) } };
  if (opts.body && typeof opts.body !== "string") {
    init.body = JSON.stringify(opts.body);
    init.headers["Content-Type"] = "application/json";
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = "";
    try { const j = await r.json(); msg = j.detail || JSON.stringify(j); } catch { msg = await r.text().catch(() => ""); }
    const e = new Error(`${r.status} ${msg}`.slice(0, 300));
    e.status = r.status;
    throw e;
  }
  return r.json();
}

// ─────────────────────────────── store ───────────────────────────────
export const store = {
  snap: null,          // /api/snapshot (current account view)
  market: null,        // /api/market
  alertsFeed: null,    // /api/alerts (server price-swing queue)
  intel: null,         // /api/intel
  orders: null,        // /api/orders
  calendar: null,      // /api/calendar
  levels: {},          // T212 ticker → options walls
  techs: {},           // key (T212 ticker or symbol) → compact tech
  quotes: {},          // symbol → live quote (watchlist)
  accounts: null,      // /api/accounts
  prefs: { watchlist: [], alerts: [], notes: {}, ui: {} },
  view: "cockpit",
};

const subs = new Map();
export function on(key, fn) {
  if (!subs.has(key)) subs.set(key, new Set());
  subs.get(key).add(fn);
  return () => subs.get(key).delete(fn);
}
export function emit(key, payload) {
  for (const fn of subs.get(key) || []) {
    try { fn(payload); } catch (e) { console.error(`[${key}]`, e); }
  }
}
export function set(key, val) { store[key] = val; emit(key, val); }

// ─────────────────────────────── formatting ───────────────────────────────
const nf = new Map();
function numFmt(dp, compact = false) {
  const k = `${dp}:${compact}`;
  if (!nf.has(k)) {
    nf.set(k, new Intl.NumberFormat("en-GB", compact
      ? { notation: "compact", maximumFractionDigits: dp }
      : { minimumFractionDigits: dp, maximumFractionDigits: dp }));
  }
  return nf.get(k);
}
export const isNum = (v) => typeof v === "number" && Number.isFinite(v);
export const num = (v, dp = 2) => (isNum(v) ? numFmt(dp).format(v) : "—");
export const signed = (v, dp = 2) => (isNum(v) ? (v > 0 ? "+" : v < 0 ? "−" : "") + numFmt(dp).format(Math.abs(v)) : "—");
export const pct = (v, dp = 2, withSign = true) =>
  (isNum(v) ? (withSign ? signed(v, dp) : numFmt(dp).format(v)) + "%" : "—");
export const compact = (v, dp = 1) => (isNum(v) ? numFmt(dp, true).format(v) : "—");
const QTY = new Intl.NumberFormat("en-GB", { minimumFractionDigits: 0, maximumFractionDigits: 4 });
/** Share counts: 20 · 3.5 · 0.2849 (no padded zeros). */
export const qty = (v) => (isNum(v) ? QTY.format(v) : "—");

const CCY_SYM = { GBP: "£", USD: "$", EUR: "€", GBX: "", JPY: "¥", HKD: "HK$" };
/** Account-currency money (GBP): "£12,345.67", signed form "+£1,234.00". */
export function money(v, { dp = 2, sign = false, ccy = "GBP", compactOver = null } = {}) {
  if (!isNum(v)) return "—";
  const s = CCY_SYM[ccy] ?? "";
  const a = Math.abs(v);
  const body = compactOver != null && a >= compactOver ? compact(a, 1) : numFmt(dp).format(a);
  const pre = v < 0 ? "−" : sign && v > 0 ? "+" : "";
  return `${pre}${s}${body}`;
}
/** Instrument price in its native currency: $1,098.47 · 6,300p */
export function price(v, ccy = "USD") {
  if (!isNum(v)) return "—";
  const c = (ccy || "").toUpperCase();
  if (c === "GBX") return `${numFmt(v >= 1000 ? 0 : 2).format(v)}p`;
  const dp = v >= 1000 ? 2 : v >= 1 ? 2 : 4;
  return `${CCY_SYM[c] ?? ""}${numFmt(dp).format(v)}`;
}
export const updown = (v) => (isNum(v) ? (v > 0 ? "up" : v < 0 ? "down" : "flat") : "flat");

const RTF = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });
export function ago(tsSec) {
  if (!isNum(tsSec)) return "";
  const d = tsSec - Date.now() / 1000;
  const a = Math.abs(d);
  if (a < 60) return "刚刚";
  if (a < 3600) return RTF.format(Math.round(d / 60), "minute");
  if (a < 86400) return RTF.format(Math.round(d / 3600), "hour");
  return RTF.format(Math.round(d / 86400), "day");
}
export function countdown(tsSec) {
  if (!isNum(tsSec)) return "";
  let s = Math.round(tsSec - Date.now() / 1000);
  const past = s < 0;
  s = Math.abs(s);
  const d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), mm = Math.floor((s % 3600) / 60);
  const body = d >= 2 ? `${d} 天` : d === 1 ? `1 天 ${hh} 时` : hh ? `${hh} 时 ${mm} 分` : `${mm} 分`;
  return past ? `${body}前` : body;
}
const WD = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
export function ukTime(tsSec, { withDate = true, withWeekday = true } = {}) {
  if (!isNum(tsSec)) return "";
  const d = new Date(tsSec * 1000);
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
    weekday: "short",
  }).formatToParts(d);
  const g = (t) => parts.find((p) => p.type === t)?.value;
  const wd = WD[new Date(d.toLocaleString("en-US", { timeZone: "Europe/London" })).getDay()];
  const date = `${g("month")}/${g("day")}`;
  return [withDate ? date : null, withWeekday ? wd : null, `${g("hour")}:${g("minute")}`].filter(Boolean).join(" ");
}
export function dateLabel(isoDate) {
  if (!isoDate) return "";
  const [y, m, d] = isoDate.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d, 12));
  const today = new Date();
  const t0 = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate(), 12);
  const diff = Math.round((dt - t0) / 86400000);
  const rel = diff === 0 ? "今天" : diff === 1 ? "明天" : diff === -1 ? "昨天" : diff === 2 ? "后天" : "";
  return `${m}/${d} ${WD[dt.getUTCDay()]}${rel ? " · " + rel : ""}`;
}

// ─────────────────────────────── tickers & sectors ───────────────────────────────
const DISPLAY_REMAP = { FB: "META", YNDX: "NBIS", VACQ: "RKLB" };
/** "NVDA_US_EQ" → "NVDA"; "SGLNl_EQ" → "SGLN"; "3HNXl_EQ" → "3HNX"; "YNDX_US_EQ" → "NBIS" */
export function shortTicker(t) {
  const s = String(t || "");
  const m = s.match(/^([A-Z0-9.]+?)(?:[a-z])?_(?:[A-Z]{2}_)?EQ$/) || s.match(/^([A-Z0-9.]+)/);
  const base = m ? m[1] : s;
  return DISPLAY_REMAP[base] || base;
}
export const isUSListing = (t) => /_US_EQ$/.test(t || "") || (!String(t || "").includes("_") && !/[.=^:]/.test(t || ""));

export const SECTORS = {
  MU: "半导体", LITE: "半导体", MRVL: "半导体", AAOI: "半导体", AXTI: "半导体", NVDA: "半导体", AMD: "半导体",
  ASML: "半导体", TSM: "半导体", SNDK: "半导体", AVGO: "半导体", SMCI: "半导体", QCOM: "半导体", INTC: "半导体",
  ARM: "半导体", "3HNX": "半导体", COHR: "半导体", ALAB: "半导体", CRDO: "半导体", WDC: "半导体", STX: "半导体",
  MSFT: "AI 软件 · 云", GOOGL: "AI 软件 · 云", GOOG: "AI 软件 · 云", ORCL: "AI 软件 · 云", NBIS: "AI 软件 · 云",
  META: "AI 软件 · 云", AMZN: "AI 软件 · 云", PLTR: "AI 软件 · 云", CRWD: "AI 软件 · 云", SNOW: "AI 软件 · 云",
  IREN: "算力 · 数据中心", CRWV: "算力 · 数据中心", VRT: "算力 · 数据中心", CIFR: "算力 · 数据中心", APLD: "算力 · 数据中心",
  LMT: "国防", RTX: "国防", NOC: "国防", GD: "国防",
  SGLN: "黄金", GLD: "黄金", IAU: "黄金",
  BABA: "中概", PDD: "中概", JD: "中概", NIO: "中概",
  CRCL: "加密 · 金融", COIN: "加密 · 金融", HOOD: "加密 · 金融",
  BATL: "能源", XOM: "能源", CVX: "能源",
  NOK: "电信", ERIC: "电信",
  QQQ: "指数 ETF", SPY: "指数 ETF", VOO: "指数 ETF", DIA: "指数 ETF", IWM: "指数 ETF", SMH: "指数 ETF",
};
/** The AI / semiconductor supply chain — one bet, however many tickers. */
export const AI_CHAIN = new Set(["MU", "LITE", "MRVL", "AAOI", "AXTI", "IREN", "MSFT", "GOOGL", "GOOG", "ORCL",
  "NBIS", "3HNX", "NVDA", "AMD", "AVGO", "TSM", "ASML", "SNDK", "META", "AMZN", "CRWV", "VRT", "SMCI", "ARM",
  "COHR", "ALAB", "CRDO", "PLTR", "SMH"]);
export function sectorOf(p) {
  const s = shortTicker(p.ticker || p).toUpperCase();
  if (SECTORS[s]) return SECTORS[s];
  const n = String(p.name || "").toLowerCase();
  if (/hynix|semiconduct|micron|optoelectron|photonic|foundry/.test(n)) return "半导体";
  if (/gold|silver|physical metal/.test(n)) return "黄金";
  if (/cloud|software/.test(n)) return "AI 软件 · 云";
  if (/etf|index|ishares|leverage shares|vanguard/.test(n)) return "指数 ETF";
  return "其他";
}

// ─────────────────────────────── US session clock ───────────────────────────────
export function nyParts(date = new Date()) {
  const p = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hour12: false, weekday: "short", hour: "2-digit", minute: "2-digit",
    second: "2-digit", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(date);
  const g = (t) => p.find((x) => x.type === t)?.value;
  return { wd: g("weekday"), h: +g("hour") % 24, m: +g("minute"), s: +g("second"), date: `${g("year")}-${g("month")}-${g("day")}` };
}
/** {key: pre|regular|post|closed, label, next: {label, secs}} for US equities. */
export function usSession(holidayName = null, date = new Date()) {
  const n = nyParts(date);
  const mins = n.h * 60 + n.m + n.s / 60;
  const weekend = n.wd === "Sat" || n.wd === "Sun";
  const secsTo = (targetMin) => Math.round((targetMin - mins) * 60);
  if (holidayName && !weekend) return { key: "closed", label: `休市 · ${holidayName}`, next: null };
  if (weekend) return { key: "closed", label: "周末休市", next: { label: "周一开盘", secs: null } };
  if (mins < 240) return { key: "closed", label: "休市", next: { label: "距盘前", secs: secsTo(240) } };
  if (mins < 570) return { key: "pre", label: "盘前", next: { label: "距开盘", secs: secsTo(570) } };
  if (mins < 960) return { key: "regular", label: "盘中", next: { label: "距收盘", secs: secsTo(960) } };
  if (mins < 1200) return { key: "post", label: "盘后", next: { label: "距盘后结束", secs: secsTo(1200) } };
  return { key: "closed", label: "休市", next: { label: "明日盘前", secs: secsTo(1440 + 240) } };
}
export function dur(secs) {
  if (!isNum(secs)) return "";
  const hh = Math.floor(secs / 3600), mm = Math.floor((secs % 3600) / 60);
  return hh ? `${hh}h${String(mm).padStart(2, "0")}m` : `${mm}m`;
}
/** London Stock Exchange (08:00–16:30 UK). */
export function lseOpen(date = new Date()) {
  const p = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(date);
  const g = (t) => p.find((x) => x.type === t)?.value;
  const m = +g("hour") * 60 + +g("minute");
  return !["Sat", "Sun"].includes(g("weekday")) && m >= 480 && m < 990;
}

// ─────────────────────────────── prefs (server-side) ───────────────────────────────
const saveTimers = {};
export async function loadPrefs() {
  try {
    const p = await api("/api/prefs");
    store.prefs = { watchlist: p.watchlist || [], alerts: p.alerts || [], notes: p.notes || {}, ui: p.ui || {} };
  } catch { /* offline → defaults */ }
  // one-time migration of the old per-browser watchlist
  try {
    const old = JSON.parse(localStorage.getItem("guanlan-watchlist") || "[]");
    if (Array.isArray(old) && old.length && !store.prefs.watchlist.length) {
      store.prefs.watchlist = old.map((s) => String(s).toUpperCase());
      await savePref("watchlist", store.prefs.watchlist, 0);
      localStorage.removeItem("guanlan-watchlist");
    }
  } catch { /* ignore */ }
  emit("prefs", store.prefs);
  return store.prefs;
}
export function savePref(key, value, debounceMs = 500) {
  store.prefs[key] = value;
  emit(`prefs:${key}`, value);
  clearTimeout(saveTimers[key]);
  return new Promise((resolve) => {
    saveTimers[key] = setTimeout(async () => {
      try { await api(`/api/prefs/${key}`, { method: "PUT", body: { value } }); resolve(true); }
      catch (e) { toast(`保存失败：${e.message}`, { kind: "error" }); resolve(false); }
    }, debounceMs);
  });
}
export const uiPref = (k, dflt) => (store.prefs.ui && k in store.prefs.ui ? store.prefs.ui[k] : dflt);
export function setUiPref(k, v) {
  const ui = { ...(store.prefs.ui || {}), [k]: v };
  savePref("ui", ui, 400);
}

// ─────────────────────────────── polling ───────────────────────────────
const jobs = new Map();
/** Run fn now (optional) and every `ms` while the page is visible. */
export function every(key, ms, fn, { immediate = true } = {}) {
  stop(key);
  const job = { ms, fn, timer: null, running: false, last: 0 };
  const tick = async (force = false) => {
    // first load always runs (a tab opened in the background is ready when
    // shown); afterwards pause while hidden to spare the API quotas
    if (document.hidden && job.last && !force) return;
    if (job.running) return;
    job.running = true;
    try { await fn(); } catch (e) { console.warn(`[poll ${key}]`, e.message || e); }
    finally { job.running = false; job.last = Date.now(); }
  };
  job.tick = tick;
  job.timer = setInterval(tick, ms);
  jobs.set(key, job);
  if (immediate) tick();
  return job;
}
export function stop(key) {
  const j = jobs.get(key);
  if (j) clearInterval(j.timer);
  jobs.delete(key);
}
export function kick(key) { jobs.get(key)?.tick(true); }
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  for (const j of jobs.values()) if (Date.now() - j.last > j.ms) j.tick();   // catch up after sleep
});

// ─────────────────────────────── toasts & notifications ───────────────────────────────
export function toast(msg, { kind = "info", title = null, timeout = 5200, action = null } = {}) {
  let box = document.getElementById("toasts");
  if (!box) { box = h("div", { id: "toasts", role: "status", "aria-live": "polite" }); document.body.append(box); }
  const el = h("div", { class: `toast toast-${kind}` },
    title ? h("div", { class: "toast-title" }, title) : null,
    h("div", { class: "toast-msg" }, msg),
    action ? h("button", { class: "btn btn-sm", onclick: () => { action.fn(); el.remove(); } }, action.label) : null,
    h("button", { class: "toast-x", "aria-label": "关闭", onclick: () => el.remove() }, "×"));
  box.append(el);
  requestAnimationFrame(() => el.classList.add("in"));
  if (timeout) setTimeout(() => { el.classList.remove("in"); setTimeout(() => el.remove(), 250); }, timeout);
  return el;
}
export function notify(title, body) {
  try {
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification(title, { body, icon: "/static/icon-192.png", tag: title });
    }
  } catch { /* ignore */ }
}

// ─────────────────────────────── misc ───────────────────────────────
export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
export function cssVar(name, el = document.documentElement) {
  return getComputedStyle(el).getPropertyValue(name).trim();
}
export function flash(el, dir) {
  if (!el || !dir || dir === "flat") return;
  el.classList.remove("flash-up", "flash-down");
  void el.offsetWidth;
  el.classList.add(dir === "up" ? "flash-up" : "flash-down");
}
/** Replace an element's text only when it changed (keeps selection + avoids reflow). */
export function setText(el, txt) { if (el && el.textContent !== txt) el.textContent = txt; }
export function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

/** Position lookup across the current snapshot by short ticker or T212 id. */
export function findPosition(key) {
  const k = String(key || "").toUpperCase();
  return (store.snap?.positions || []).find((p) => p.ticker.toUpperCase() === k || shortTicker(p.ticker).toUpperCase() === k) || null;
}
