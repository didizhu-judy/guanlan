// 观澜 · main — boot, pollers, event wiring.
import { $, $$, h, api, store, on, emit, set, every, kick, loadPrefs, uiPref, setUiPref, shortTicker, toast, isNum } from "./core.js";
import {
  applyDisplayPrefs, buildTabs, showView, renderAccountSwitch, currentAccount, tickClock, openPalette,
  openSettings, bindKeys, checkAlerts, togglePrivacy, toggleTheme, closeModal,
} from "./ui.js";
import {
  renderStats, renderBrief, renderHoldings, renderWatchlist, renderMarket, renderMap, renderCatalysts,
  renderSignals, bindHoldingsToolbar, bindWatchInput,
} from "./cockpit.js";
import { drawerOpen } from "./drawer.js";
import { loadRisk, renderRiskLive } from "./risk.js";
import { renderCalendar, bindCalendar, loadNews } from "./calendar.js";
import { loadTrades, renderTrades, renderOrders } from "./trades.js";

let lastTickerSig = "";
let secondaryStarted = false;
function startSecondary() {
  if (secondaryStarted) return;
  secondaryStarted = true;
  for (const k of ["orders", "alertsFeed", "intel", "calendar", "quotes", "techs", "levels"]) kick(k);
}

// ───────────────────────────── loaders ─────────────────────────────
async function loadSnap() {
  try {
    const snap = await api(`/api/snapshot?account=${encodeURIComponent(currentAccount())}`);
    set("snap", snap);
    $("#err").hidden = true;
    const bad = (snap.accounts || []).filter((a) => !a.ok);
    if (bad.length) showErr(`部分账户读取失败：${bad.map((a) => a.error).join("；")}`, "warn");
  } catch (e) {
    if (e.status === 404 && currentAccount() !== "all") { setUiPref("account", "all"); return loadSnap(); }
    showErr(`持仓数据获取失败：${e.message}（显示的是上一次的数据）`);
  }
}
function showErr(msg, kind = "error") {
  const el = $("#err");
  el.textContent = msg;
  el.className = `err ${kind}`;
  el.hidden = false;
}
const heldTickers = () => (store.snap?.positions || []).map((p) => p.ticker);
async function loadTechs() {
  const list = [...new Set([...heldTickers(), ...(store.prefs.watchlist || [])])];
  if (!list.length) return;
  const r = await api(`/api/tech_batch?tickers=${encodeURIComponent(list.join(","))}`);
  // key watchlist entries by symbol too, so both tables find them
  const techs = { ...r.tech };
  for (const s of store.prefs.watchlist || []) {
    const held = (store.snap?.positions || []).find((p) => shortTicker(p.ticker) === s);
    if (held && techs[held.ticker]) techs[s] = techs[held.ticker];
  }
  set("techs", techs);
}
async function loadQuotes() {
  const wl = store.prefs.watchlist || [];
  if (!wl.length) return;
  const r = await api(`/api/quote?tickers=${encodeURIComponent(wl.join(","))}`);
  set("quotes", { ...store.quotes, ...r.quote });
}
async function loadMarket() { set("market", await api("/api/market")); }
async function loadAlertsFeed() { set("alertsFeed", await api("/api/alerts")); }
async function loadIntel() { set("intel", await api("/api/intel")); }
async function loadOrders() { set("orders", await api("/api/orders")); }
async function loadLevels() {
  const r = await api("/api/levels");
  const m = {};
  for (const lv of r.levels || []) if (lv.ticker) m[lv.ticker] = lv;
  set("levels", m);
}
async function loadCalendar() {
  const wl = (store.prefs.watchlist || []).join(",");
  set("calendar", await api(`/api/calendar?tickers=${encodeURIComponent(wl)}`));
}

// ───────────────────────────── wiring ─────────────────────────────
on("snap", () => {
  renderStats(); renderHoldings(); renderMap(); renderBrief(); renderAccountSwitch(); renderSignals();
  renderMarket(); renderWatchlist();
  if (store.view === "risk") renderRiskLive();
  checkAlerts();
  const sig = heldTickers().join(",");
  if (!secondaryStarted) { lastTickerSig = sig; startSecondary(); }
  else if (sig !== lastTickerSig) {     // holdings changed → refresh ticker-keyed feeds
    lastTickerSig = sig;
    kick("techs"); kick("levels"); kick("calendar");
  }
});
on("techs", () => { renderHoldings(); renderWatchlist(); });
on("quotes", () => { renderWatchlist(); checkAlerts(); });
on("market", () => { renderMarket(); tickClock(); renderHoldings(); renderWatchlist(); });
on("alertsFeed", () => { renderBrief(); renderHoldings(); });
on("intel", renderSignals);
on("orders", () => { renderBrief(); renderHoldings(); if (store.view === "trades") renderOrders(); });
on("levels", () => { if (store.view === "calendar") renderCalendar(); });
on("calendar", () => { renderCatalysts(); renderBrief(); renderHoldings(); renderWatchlist(); if (store.view === "calendar") renderCalendar(); });
on("prefs:watchlist", () => { renderWatchlist(); renderSignals(); kick("quotes"); kick("techs"); kick("calendar"); });
on("prefs:alerts", () => { renderBrief(); renderHoldings(); renderWatchlist(); });
on("brief", renderBrief);
on("goto", (v) => showView(v));
on("account", () => { store.snap = null; lastTickerSig = ""; kick("snap"); });
on("theme", () => { renderMap(); try { localStorage.setItem("guanlan-theme", document.documentElement.getAttribute("data-theme") || "auto"); } catch { /* ignore */ } });
on("view", (v) => {
  setUiPref("view", v);
  if (v === "risk") loadRisk();
  if (v === "calendar") { renderCalendar(); loadNews(); }
  if (v === "trades") loadTrades();
  window.scrollTo({ top: 0 });
});

// ───────────────────────────── boot ─────────────────────────────
async function boot() {
  await loadPrefs();
  applyDisplayPrefs();
  buildTabs();
  bindKeys();
  bindHoldingsToolbar();
  bindWatchInput();
  bindCalendar();
  $("#btn-search").addEventListener("click", () => openPalette());
  $("#btn-theme").addEventListener("click", toggleTheme);
  $("#btn-privacy").addEventListener("click", togglePrivacy);
  $("#btn-settings").addEventListener("click", (e) => openSettings(e.currentTarget));
  $$("[data-goto]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.goto)));
  $$("[data-reload]").forEach((b) => b.addEventListener("click", () => {
    const v = b.dataset.reload;
    if (v === "risk") loadRisk(true); else if (v === "trades") loadTrades(true); else if (v === "news") loadNews(true);
  }));

  const hp = new URLSearchParams(location.hash.slice(1));
  showView(hp.get("view") || uiPref("view", "cockpit"), { push: false });
  api("/api/accounts").then((a) => { set("accounts", a); renderAccountSwitch(); }).catch(() => {});
  setInterval(() => { if (store.accounts?.missing?.length) api("/api/accounts").then((a) => { const had = store.accounts.missing.length; set("accounts", a); if (had && !a.missing.length) { toast("检测到新账户，已接入 ✓", { kind: "good" }); kick("snap"); } renderAccountSwitch(); }).catch(() => {}); }, 15000);

  // Holdings first: the browser allows ~6 parallel requests per host, so the
  // secondary feeds start only once the first snapshot has painted.
  every("snap", 10000, loadSnap);            // original cadence — unchanged
  every("market", 30000, loadMarket);
  every("quotes", 30000, loadQuotes, { immediate: false });
  every("alertsFeed", 60000, loadAlertsFeed, { immediate: false });
  every("techs", 300000, loadTechs, { immediate: false });
  every("levels", 300000, loadLevels, { immediate: false });
  every("intel", 180000, loadIntel, { immediate: false });
  every("orders", 60000, loadOrders, { immediate: false });
  every("calendar", 1800000, loadCalendar, { immediate: false });
  every("clock", 1000, async () => tickClock());
  setTimeout(startSecondary, 5000);          // fallback if T212 is slow/down
  if (hp.get("stock")) setTimeout(() => emit("open-stock", hp.get("stock")), 400);
  window.addEventListener("hashchange", () => {
    const p = new URLSearchParams(location.hash.slice(1));
    const v = p.get("view");
    if (v && v !== store.view) showView(v, { push: false });
    const s = p.get("stock");
    if (s && s.toUpperCase() !== (document.querySelector(".dh-sym")?.firstChild?.textContent || "")) emit("open-stock", s);
    if (!s && drawerOpen()) emit("escape");
  });
}
boot();
