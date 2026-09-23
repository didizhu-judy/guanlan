// 观澜 · cockpit (盯盘) — stats band, briefing, holdings, watchlist, market, map, catalysts, signals.
import {
  $, $$, h, api, store, on, emit, isNum, num, signed, pct, money, price, updown, compact, ago, countdown, ukTime,
  shortTicker, sectorOf, setText, flash, uiPref, setUiPref, findPosition, dateLabel, qty,
} from "./core.js";
import { sparkline, rangeBar, treemap, moveColor, tip } from "./charts.js";
import { addWatch, removeWatch, alertDistance } from "./ui.js";

// ═════════════════════════════ stats band ═════════════════════════════
const STAT_IDS = ["cost", "total", "cash", "today", "realized", "unreal", "alltime"];
export function renderStats() {
  const snap = store.snap;
  if (!snap) return;
  const s = snap.stats || {};
  const put = (id, text, dir = null, sub = null, subDir = null) => {
    const card = $(`#st-${id}`);
    if (!card) return;
    const v = card.querySelector(".st-v"), sb = card.querySelector(".st-sub");
    if (v.textContent !== text && v.textContent !== "—" && dir) flash(v, dir);
    setText(v, text);
    v.classList.remove("up", "down", "flat");
    if (dir) v.classList.add(dir);
    if (sb && sub != null) { setText(sb, sub); sb.className = `st-sub${subDir ? " " + subDir : ""}`; }
  };
  const tv = s.totalValue;
  put("cost", money(s.totalCost), null, "净入金 · GBP");
  const accts = (snap.accounts || []).filter((a) => a.ok);
  put("total", money(tv), null, accts.length > 1
    ? accts.map((a) => `${a.label} ${money(a.totalValue, { compactOver: 10000 })}`).join(" · ")
    : `持仓 ${money(s.holdingsValue, { compactOver: 10000 })}`);
  put("cash", money(s.totalCash), null, isNum(tv) && tv ? `占 ${pct((s.totalCash / tv) * 100, 1, false)}` : "");
  put("today", money(s.todayPnl, { sign: true }), updown(s.todayPnl), pct(s.todayPnlPct), updown(s.todayPnlPct));
  put("realized", money(s.realizedPnl, { sign: true }), updown(s.realizedPnl), `${pct(s.realizedPnlPct)} 本金`, updown(s.realizedPnlPct));
  put("unreal", money(s.unrealizedPnl, { sign: true }), updown(s.unrealizedPnl), `${pct(s.unrealizedPnlPct)} 持仓`, updown(s.unrealizedPnlPct));
  put("alltime", money(s.allTimePnl, { sign: true }), updown(s.allTimePnl), `${pct(s.allTimePnlPct)} 本金`, updown(s.allTimePnlPct));
  // 锁定收益 breakdown (reconciles: 锁定 + 浮动 = 总)
  const bd = $("#st-realized .st-breakdown");
  if (bd) {
    const row = (label, v, cls = "") => h("div", { class: `bd-row ${cls}` }, h("span", {}, label), h("b", { class: `money ${updown(v)}` }, money(v, { sign: true })));
    bd.replaceChildren(
      h("div", { class: "bd-title" }, "锁定收益 = 已落袋的全部"),
      row("交易已实现盈亏", s.realizedTrading),
      row("股息", s.dividends),
      row("现金利息", s.interest),
      row("换汇费", s.fxFees),
      Math.abs(s.otherFees || 0) >= 0.01 ? row("其他费用", s.otherFees) : null,
      Math.abs(s.unexplained || 0) >= 0.5 ? row("未归类", s.unexplained) : null,
      h("div", { class: "bd-sum" }, h("span", {}, "合计"), h("b", { class: "money" }, money(s.realizedPnl, { sign: true }))),
      h("div", { class: "bd-note" },
        s.historySyncing ? "成交历史同步中，明细稍后补全。" :
          "T212 的「已实现盈亏」只算买卖价差；换汇费、股息、利息另计。把它们算进来，锁定 + 浮动 = 总收益。"));
  }
  $("#stats")?.classList.toggle("syncing", !!s.historySyncing);
  const u = $("#updated");
  if (u) u.textContent = `更新于 ${new Date(snap.fetchedAt * 1000).toLocaleTimeString("en-GB")}`;
}

// ═════════════════════════════ briefing strip ═════════════════════════════
function heldWeights() {
  const pos = store.snap?.positions || [];
  const tot = pos.reduce((a, p) => a + (p.marketValue || 0), 0) || 1;
  const m = {};
  for (const p of pos) m[shortTicker(p.ticker).toUpperCase()] = (p.marketValue || 0) / tot * 100;
  return m;
}
export function nextEarningsBySymbol() {
  const out = {};
  const now = Date.now() / 1000;
  for (const e of store.calendar?.events || []) {
    if (e.kind !== "earnings" || e.ts < now - 86400 * 1.5) continue;
    const k = e.ticker.toUpperCase();
    if (!out[k] || e.ts < out[k].ts) out[k] = e;
  }
  return out;
}
export function renderBrief() {
  const box = $("#brief");
  if (!box || !store.snap) return;
  const chips = [];
  const holds = new Set((store.snap.positions || []).map((p) => shortTicker(p.ticker).toUpperCase()));
  const swings = (store.alertsFeed?.alerts || []).filter((a) => holds.has(String(a.ticker).toUpperCase()));
  const byT = {};
  for (const a of swings) if (!byT[a.ticker] || Math.abs(a.change) > Math.abs(byT[a.ticker].change)) byT[a.ticker] = a;
  const sw = Object.values(byT);
  chips.push(sw.length
    ? h("button", { class: "chip chip-warn", type: "button", title: sw.map((a) => a.msg).join("\n"), onclick: () => emit("open-stock", sw[0].ticker) },
      h("i", { class: "dot" }), `${sw.length} 只持仓 24h 内大幅波动`,
      h("span", { class: "chip-sub" }, sw.slice(0, 3).map((a) => `${shortTicker(a.ticker)} ${signed(a.change, 1)}%`).join(" · ")))
    : h("span", { class: "chip chip-calm" }, h("i", { class: "dot" }), "无异动 · 系统在盯",
      h("span", { class: "chip-sub" }, "有事会推飞书，现在什么都不用做")));

  const w = heldWeights();
  const now = Date.now() / 1000;
  const evs = (store.calendar?.events || []).filter((e) => e.ts >= now - 3600 && e.ts <= now + 86400 * 21);
  const earn = evs.filter((e) => e.kind === "earnings" && e.held).slice(0, 2);
  for (const e of earn) {
    const wt = w[e.ticker.toUpperCase()] || 0;
    chips.push(h("button", { class: `chip ${wt >= 15 ? "chip-accent" : ""}`, type: "button", onclick: () => emit("open-stock", e.ticker),
      title: `${e.title} · ${ukTime(e.ts)} 伦敦时间 · EPS 预期 ${num(e.detail?.epsEstimate)}` },
    h("i", { class: "ico" }, "◆"), `${e.ticker} 财报 ${dateLabel(e.date)} ${e.timeLabel}`,
    h("span", { class: "chip-sub" }, `还有 ${countdown(e.ts)}${wt ? ` · 占持仓 ${wt.toFixed(0)}%` : ""}`)));
  }
  const macro = evs.filter((e) => (e.kind === "macro" || e.kind === "fomc") && e.importance >= 3)[0];
  if (macro) {
    chips.push(h("button", { class: "chip", type: "button", onclick: () => emit("goto", "calendar"), title: `${macro.title} · 伦敦 ${ukTime(macro.ts)}` },
      h("i", { class: "ico" }, macro.kind === "fomc" ? "⚖" : "▤"), `${macro.title}`,
      h("span", { class: "chip-sub" }, `${ukTime(macro.ts, { withWeekday: false })} · ${countdown(macro.ts)}后`)));
  }
  const orders = store.orders?.orders || [];
  if (orders.length) {
    const o = orders[0];
    chips.push(h("button", { class: "chip", type: "button", onclick: () => emit("goto", "trades"),
      title: orders.map((x) => `${x.accountLabel} ${x.short} ${x.side === "SELL" ? "卖" : "买"} ${qty(x.quantity)} @ ${price(x.trigger, x.currency)} (${pct(x.distancePct, 1)})`).join("\n") },
    h("i", { class: "ico" }, "⌖"), `${orders.length} 张挂单`,
    h("span", { class: "chip-sub" }, `最近 ${shortTicker(o.ticker)} ${o.side === "SELL" ? "卖" : "买"} ${price(o.trigger, o.currency)} · ${pct(o.distancePct, 1)}`)));
  }
  const act = (store.prefs.alerts || []).filter((a) => !a.firedAt);
  if (act.length) {
    const ds = act.map((a) => ({ a, d: alertDistance(a) })).filter((x) => x.d).sort((x, y) => Math.abs(x.d.dist) - Math.abs(y.d.dist));
    const n0 = ds[0];
    chips.push(h("span", { class: "chip", title: act.map((a) => `${a.ticker} ${a.op === ">=" ? "≥" : "≤"} ${a.price}`).join("\n") },
      h("i", { class: "ico" }, "🔔"), `${act.length} 个价格提醒`,
      n0 ? h("span", { class: "chip-sub" }, `最近 ${n0.a.ticker} ${n0.a.op === ">=" ? "≥" : "≤"} ${price(n0.a.price, n0.d.ccy)} · 差 ${pct(n0.d.dist, 1)}`) : null));
  }
  box.replaceChildren(...chips);
}

// ═════════════════════════════ holdings table ═════════════════════════════
const rowRefs = new Map();     // T212 ticker → refs
let tableSig = "";
const collapsed = new Set(uiPref("collapsedSectors", []));

function sortMode() { return uiPref("holdSort", "sector"); }

function techCell(t) {
  const box = h("span", { class: "tech" });
  if (!t || t.error) { box.append(h("span", { class: "muted" }, "—")); return box; }
  const dot = (v, label) => h("i", { class: `ma ${isNum(v) ? (v >= 0 ? "above" : "below") : ""}`, "aria-label": `${label} ${isNum(v) ? (v >= 0 ? "上方" : "下方") : ""}` });
  const rsiCls = isNum(t.rsi) ? (t.rsi >= 70 ? "hot" : t.rsi <= 30 ? "cold" : "") : "";
  box.append(h("span", { class: "ma-dots", "aria-hidden": "true" }, dot(t.ma20, "MA20"), dot(t.ma50, "MA50"), dot(t.ma200, "MA200")),
    h("span", { class: `rsi ${rsiCls}` }, isNum(t.rsi) ? `RSI ${t.rsi.toFixed(0)}` : "RSI —"));
  box.title = [
    `MA20 ${pct(t.ma20, 1)} · MA50 ${pct(t.ma50, 1)} · MA200 ${pct(t.ma200, 1)}`,
    `RSI ${isNum(t.rsi) ? t.rsi.toFixed(1) : "—"}${isNum(t.rsiPrev) ? `（昨 ${t.rsiPrev.toFixed(1)}）` : ""} · MACD ${t.macdBull ? "多头" : "空头"}${isNum(t.hist) && isNum(t.histPrev) ? (Math.abs(t.hist) > Math.abs(t.histPrev) ? " 柱扩张" : " 柱收敛") : ""}`,
    `距 52 周高 ${pct(t.fromHigh, 1)} · 距低 ${pct(t.fromLow, 1)} · ATR ${pct(t.atrPct, 1, false)}/日`,
  ].join("\n");
  return box;
}

function buildRow(p, { watch = false } = {}) {
  const tr = h("tr", { class: "pos-row", tabindex: 0, dataset: { key: p.key } });
  const refs = {
    tr,
    sym: h("span", { class: "sym" }),
    name: h("span", { class: "name" }),
    badges: h("span", { class: "badges" }),
    wbar: h("span", { class: "wbar-fill" }),
    wpct: h("span", { class: "wpct" }),
    spark: h("span", { class: "spark-cell" }),
    price: h("span", { class: "px" }),
    sess: h("span", { class: "sess" }),
    day: h("td", { class: "c-day num" }),
    range: h("span", { class: "range-cell" }),
    tech: h("td", { class: "c-tech" }),
    flags: h("td", { class: "c-flags" }),
    last: {},
  };
  tr.append(
    h("td", { class: "c-sym" }, h("div", { class: "sym-line" }, refs.sym, refs.badges), refs.name),
    h("td", { class: "c-wt" }, watch ? h("span", { class: "muted" }, "自选") : [h("span", { class: "wbar" }, refs.wbar), refs.wpct]),
    h("td", { class: "c-spark" }, refs.spark),
    h("td", { class: "c-px num" }, refs.price, refs.sess),
    refs.day,
    h("td", { class: "c-range" }, refs.range),
    refs.tech,
    refs.flags);
  const open = () => emit("open-stock", p.openKey);
  tr.addEventListener("click", (e) => { if (!e.target.closest(".row-x")) open(); });
  tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  return refs;
}

function sessionBadge(p) {
  const cur = (p.currency || "").toUpperCase();
  if (cur === "USD") {
    const k = store.market?.session;
    const hol = store.market?.status?.holiday;
    const label = hol ? "休市" : { pre: "盘前", regular: "盘中", post: "盘后", closed: "休市" }[k] || "";
    return { label, cls: hol ? "closed" : k || "closed" };
  }
  return null;
}

function updateRow(r, p, { watch = false, weight = 0 } = {}) {
  const key = p.key;
  setText(r.sym, p.sym);
  setText(r.name, p.name || "");
  // badges: accounts + earnings soon
  const earn = nextEarningsBySymbol()[p.sym.toUpperCase()];
  const badges = [];
  const multi = (store.snap?.accountsAvailable || []).length > 1 && (store.snap?.account || "all") === "all";
  if (!watch && multi) for (const a of p.accounts || []) badges.push(h("span", { class: `badge acct-${a.id}` }, a.id === "invest" ? "INV" : a.label));
  if (earn) {
    const days = (earn.ts - Date.now() / 1000) / 86400;
    if (days <= 21) badges.push(h("span", { class: `badge earn${days <= 7 ? " soon" : ""}`, title: `${earn.title} ${dateLabel(earn.date)} ${earn.timeLabel}` }, days < 0 ? "财报已出" : `财报 ${Math.max(0, Math.ceil(days))}天`));
  }
  const sig = badges.map((b) => b.textContent).join("|");
  if (r.last.badges !== sig) { r.badges.replaceChildren(...badges); r.last.badges = sig; }

  if (!watch) {
    r.wbar.style.width = `${Math.min(100, weight * 2).toFixed(1)}%`;   // 50% weight = full bar
    setText(r.wpct, `${weight.toFixed(1)}%`);
    r.wbar.classList.toggle("heavy", weight >= 25);
  }
  // price + flash
  const pxTxt = price(p.price, p.currency);
  if (r.last.px != null && isNum(p.price) && p.price !== r.last.px) flash(r.price, p.price > r.last.px ? "up" : "down");
  setText(r.price, pxTxt);
  r.last.px = p.price;
  const sb = sessionBadge(p);
  if (sb) { setText(r.sess, sb.label); r.sess.className = `sess s-${sb.cls}`; } else { setText(r.sess, ""); r.sess.className = "sess"; }
  // day %
  const dTxt = pct(p.dayPct);
  if (r.last.day != null && r.day.textContent !== dTxt && isNum(p.dayPct)) flash(r.day, p.dayPct > r.last.day ? "up" : "down");
  setText(r.day, dTxt);
  r.day.className = `c-day num ${updown(p.dayPct)}`;
  r.last.day = p.dayPct;
  // intraday range (regular session H/L vs prev close)
  const rk = [p.low, p.high, p.price, p.prev].join(",");
  if (r.last.range !== rk) {
    r.range.replaceChildren(rangeBar(p.low, p.high, p.price, p.prev));
    r.range.title = isNum(p.low) ? `日内 ${price(p.low, p.currency)} – ${price(p.high, p.currency)} · 昨收 ${price(p.prev, p.currency)}` : "";
    r.last.range = rk;
  }
  // tech + spark (refresh when the tech payload changes)
  const t = store.techs[key];
  const tk = t ? `${t.rsi}|${t.ma20}|${(t.spark || []).length}|${t.spark?.[t.spark.length - 1]}` : "none";
  if (r.last.tech !== tk) {
    r.tech.replaceChildren(techCell(t));
    r.spark.replaceChildren(sparkline(t?.spark || []));
    r.last.tech = tk;
  }
  // flags: pending order · price alert · swing alert · (watch) remove
  const orders = (store.orders?.orders || []).filter((o) => o.ticker === key || o.symbol === p.sym);
  const alerts = (store.prefs.alerts || []).filter((a) => !a.firedAt && a.ticker.toUpperCase() === p.sym.toUpperCase());
  const swing = (store.alertsFeed?.alerts || []).some((a) => String(a.ticker).toUpperCase() === p.sym.toUpperCase());
  const fk = `${orders.map((o) => o.id).join()}|${alerts.length}|${swing}|${watch}`;
  if (r.last.flags !== fk) {
    const kids = [];
    if (orders.length) kids.push(h("span", { class: "flag flag-order", title: orders.map((o) => `挂单：${o.side === "SELL" ? "卖" : "买"} ${qty(o.quantity)} @ ${price(o.trigger, o.currency)}（${pct(o.distancePct, 1)}）`).join("\n") }, "⌖"));
    if (alerts.length) kids.push(h("span", { class: "flag flag-alert", title: alerts.map((a) => `提醒 ${a.op === ">=" ? "≥" : "≤"} ${a.price}`).join("\n") }, "🔔"));
    if (swing) kids.push(h("span", { class: "flag flag-swing", title: "24h 内大幅波动" }, "●"));
    if (watch) kids.push(h("button", { class: "row-x", type: "button", title: "移出自选", "aria-label": `移出自选 ${p.sym}`, onclick: (e) => { e.stopPropagation(); removeWatch(p.sym); } }, "×"));
    r.flags.replaceChildren(...kids);
    r.last.flags = fk;
  }
}

function holdingModels() {
  const pos = store.snap?.positions || [];
  const tot = pos.reduce((a, p) => a + (p.marketValue || 0), 0) || 1;
  return pos.map((p) => ({
    key: p.ticker, openKey: p.ticker, sym: shortTicker(p.ticker), name: p.name, currency: p.currency,
    price: p.currentPrice, dayPct: p.dayChangePct, low: p.dayLow, high: p.dayHigh, prev: p.dayPrevClose,
    mv: p.marketValue || 0, weight: (p.marketValue || 0) / tot * 100, sector: sectorOf(p), accounts: p.accounts,
  }));
}

export function renderHoldings() {
  const tbody = $("#hold-body");
  if (!tbody || !store.snap) return;
  const models = holdingModels();
  const mode = sortMode();
  let groups;
  if (mode === "sector") {
    const by = {};
    for (const m of models) (by[m.sector] ||= []).push(m);
    groups = Object.entries(by).map(([name, arr]) => ({ name, arr: arr.sort((a, b) => b.mv - a.mv) }))
      .sort((a, b) => b.arr.reduce((s, x) => s + x.mv, 0) - a.arr.reduce((s, x) => s + x.mv, 0));
  } else {
    const cmp = { weight: (a, b) => b.mv - a.mv, day: (a, b) => (b.dayPct ?? -1e9) - (a.dayPct ?? -1e9), name: (a, b) => a.sym.localeCompare(b.sym) }[mode];
    groups = [{ name: null, arr: [...models].sort(cmp) }];
  }
  const sig = mode + "|" + groups.map((g) => `${g.name}:${collapsed.has(g.name) ? "c" : ""}:${g.arr.map((m) => m.key).join(",")}`).join(";");
  if (sig !== tableSig) {
    tableSig = sig;
    rowRefs.clear();
    const frag = document.createDocumentFragment();
    for (const g of groups) {
      if (g.name) {
        const hr = h("tr", { class: `sec-row${collapsed.has(g.name) ? " collapsed" : ""}`, dataset: { sector: g.name } },
          h("td", { colspan: 8 }, h("button", { class: "sec-head", type: "button", "aria-expanded": collapsed.has(g.name) ? "false" : "true",
            onclick: () => { collapsed.has(g.name) ? collapsed.delete(g.name) : collapsed.add(g.name); setUiPref("collapsedSectors", [...collapsed]); renderHoldings(); } },
          h("span", { class: "caret" }, "▾"), h("span", { class: "sec-name" }, g.name),
          h("span", { class: "sec-bar" }, h("span", { class: "sec-fill" })), h("span", { class: "sec-w" }), h("span", { class: "sec-day" }))));
        frag.append(hr);
      }
      if (g.name && collapsed.has(g.name)) continue;
      for (const m of g.arr) {
        const refs = buildRow(m);
        rowRefs.set(m.key, refs);
        frag.append(refs.tr);
      }
    }
    tbody.replaceChildren(frag);
  }
  for (const g of groups) {
    if (!g.name) continue;
    const hr = tbody.querySelector(`tr.sec-row[data-sector="${CSS.escape(g.name)}"]`);
    if (!hr) continue;
    const wt = g.arr.reduce((s, x) => s + x.weight, 0);
    const mv = g.arr.reduce((s, x) => s + x.mv, 0) || 1;
    const dayW = g.arr.reduce((s, x) => s + (isNum(x.dayPct) ? x.dayPct * x.mv : 0), 0) / mv;
    hr.querySelector(".sec-fill").style.width = `${Math.min(100, wt).toFixed(1)}%`;
    setText(hr.querySelector(".sec-w"), `${wt.toFixed(0)}%`);
    const sd = hr.querySelector(".sec-day");
    setText(sd, `今日 ${pct(dayW, 2)}`);
    sd.className = `sec-day ${updown(dayW)}`;
  }
  for (const m of models) {
    const r = rowRefs.get(m.key);
    if (r) updateRow(r, m, { weight: m.weight });
  }
  setText($("#hold-count"), `${models.length} 只`);
  $$(".hold-sort .seg-btn").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
}
export function bindHoldingsToolbar() {
  $$(".hold-sort .seg-btn").forEach((b) => b.addEventListener("click", () => { setUiPref("holdSort", b.dataset.mode); tableSig = ""; renderHoldings(); }));
}

// ═════════════════════════════ watchlist ═════════════════════════════
const watchRefs = new Map();
let watchSig = "";
function watchModels() {
  return (store.prefs.watchlist || []).map((s) => {
    const q = store.quotes[s] || {};
    const held = findPosition(s);
    return {
      key: s, openKey: s, sym: s, name: held?.name || q.name || (store.techs[s]?.name) || "", currency: held?.currency || "USD",
      price: held ? held.currentPrice : q.price, dayPct: held ? held.dayChangePct : q.changePct,
      low: held ? held.dayLow : q.low, high: held ? held.dayHigh : q.high, prev: held ? held.dayPrevClose : q.prevClose,
    };
  });
}
export function renderWatchlist() {
  const tbody = $("#watch-body");
  if (!tbody) return;
  const models = watchModels();
  const sig = models.map((m) => m.key).join(",");
  if (!models.length) {
    watchSig = "";
    tbody.replaceChildren(h("tr", {}, h("td", { colspan: 8, class: "empty" },
      "还没有自选。在上面输入代码（如 NVDA、QQQ、AVGO）回车加入，或按 ⌘K 搜索后按 Tab。")));
    return;
  }
  if (sig !== watchSig) {
    watchSig = sig;
    watchRefs.clear();
    const frag = document.createDocumentFragment();
    for (const m of models) { const r = buildRow(m, { watch: true }); watchRefs.set(m.key, r); frag.append(r.tr); }
    tbody.replaceChildren(frag);
  }
  for (const m of models) { const r = watchRefs.get(m.key); if (r) updateRow(r, m, { watch: true }); }
  setText($("#watch-count"), `${models.length} 只`);
}
export function bindWatchInput() {
  const input = $("#watch-input");
  const drop = $("#watch-suggest");
  if (!input) return;
  let items = [], sel = -1, seq = 0;
  const close = () => { drop.hidden = true; items = []; sel = -1; };
  const paint = () => {
    drop.replaceChildren(...items.map((it, i) => h("button", { type: "button", class: `sug${i === sel ? " on" : ""}`,
      onmousedown: (e) => { e.preventDefault(); pick(it.symbol); } },
    h("b", {}, it.display), h("span", {}, it.name || ""), h("i", {}, it.type || ""))));
    drop.hidden = !items.length;
  };
  const pick = (s) => { addWatch(s); input.value = ""; close(); };
  input.addEventListener("input", async () => {
    const q = input.value.trim();
    const my = ++seq;
    if (!q) { close(); return; }
    try {
      const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
      if (my !== seq) return;
      items = (r.results || []).slice(0, 7);
      sel = -1;
      paint();
    } catch { /* ignore */ }
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && items.length) { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); paint(); }
    else if (e.key === "ArrowUp" && items.length) { e.preventDefault(); sel = Math.max(0, sel - 1); paint(); }
    else if (e.key === "Enter") { e.preventDefault(); pick(sel >= 0 ? items[sel].symbol : input.value.trim()); }
    else if (e.key === "Escape") close();
  });
  input.addEventListener("blur", () => setTimeout(close, 120));
}

// ═════════════════════════════ market barometer ═════════════════════════════
export function renderMarket() {
  const box = $("#market-tiles");
  const m = store.market;
  if (!box || !m) return;
  if (!box.children.length || box.dataset.sig !== m.tiles.map((t) => t.sym).join()) {
    box.dataset.sig = m.tiles.map((t) => t.sym).join();
    box.replaceChildren(...m.tiles.map((t) => h("button", { class: "mkt", type: "button", dataset: { sym: t.sym },
      title: `${t.name} ${t.sym}`, onclick: () => { if (!t.sym.includes(":")) emit("open-stock", t.sym); } },
    h("span", { class: "mkt-n" }, t.name), h("span", { class: "mkt-p num" }), h("span", { class: "mkt-d num" }))));
  }
  for (const t of m.tiles) {
    const el = box.querySelector(`.mkt[data-sym="${CSS.escape(t.sym)}"]`);
    if (!el) continue;
    const p = el.querySelector(".mkt-p"), d = el.querySelector(".mkt-d");
    const txt = isNum(t.price) ? (t.price >= 10000 ? compact(t.price, 1) : num(t.price, 2)) : "—";
    if (p.textContent !== txt && p.textContent !== "") flash(el, isNum(t.change) ? updown(t.change) : null);
    setText(p, txt);
    setText(d, pct(t.changePct));
    d.className = `mkt-d num ${updown(t.changePct)}`;
  }
  const fx = m.fx?.GBPUSD, r10 = m.rates?.US10Y;
  const usd = (store.snap?.positions || []).filter((p) => (p.currency || "").toUpperCase() === "USD").reduce((a, p) => a + (p.marketValue || 0), 0);
  const tv = store.snap?.stats?.totalValue || 0;
  const line = $("#market-macro");
  if (line) {
    line.replaceChildren(
      h("span", { class: "mm" }, "英镑/美元 ", h("b", { class: "num" }, isNum(fx?.price) ? fx.price.toFixed(4) : "—"), " ",
        h("span", { class: `num ${updown(fx?.changePct)}` }, pct(fx?.changePct))),
      h("span", { class: "mm" }, "美债10Y ", h("b", { class: "num" }, isNum(r10?.price) ? `${r10.price.toFixed(2)}%` : "—"), " ",
        h("span", { class: `num ${updown(r10?.change)}` }, isNum(r10?.change) ? `${signed(r10.change * 100, 0)}bp` : "")),
      tv ? h("span", { class: "mm muted", title: "美元资产的英镑价值随汇率波动：英镑升 1%，这部分约缩水 1%" },
        `美元资产占 ${(usd / tv * 100).toFixed(0)}%`) : null);
  }
}

// ═════════════════════════════ treemap ═════════════════════════════
export function renderMap() {
  const box = $("#map");
  if (!box || !store.snap) return;
  const pos = store.snap.positions || [];
  const tot = pos.reduce((a, p) => a + (p.marketValue || 0), 0) || 1;
  const cash = store.snap.stats?.totalCash || 0;
  const items = pos.map((p) => ({
    key: p.ticker, label: shortTicker(p.ticker), value: p.marketValue || 0,
    sub: isNum(p.dayChangePct) ? pct(p.dayChangePct) : "—", color: moveColor(p.dayChangePct),
    tipTitle: `${shortTicker(p.ticker)} · ${p.name}`,
    tipRows: [{ value: `${((p.marketValue || 0) / tot * 100).toFixed(1)}%`, label: "占持仓" },
      { value: pct(p.dayChangePct), label: "今日" }, { value: price(p.currentPrice, p.currency), label: "现价" }],
  }));
  if (uiPref("mapCash", true) && cash > 0) items.push({ key: "__cash", label: "现金", value: cash, sub: `${(cash / (tot + cash) * 100).toFixed(0)}%`, color: getComputedStyle(document.documentElement).getPropertyValue("--tile-cash").trim(), tipTitle: "现金", tipRows: [{ value: money(cash), label: "可用现金" }] });
  treemap(box, items, { onClick: (r) => { if (r.key !== "__cash") emit("open-stock", r.key); } });
}

// ═════════════════════════════ catalysts (rail) ═════════════════════════════
export function renderCatalysts() {
  const box = $("#catalysts");
  if (!box) return;
  const now = Date.now() / 1000;
  const w = heldWeights();
  const watch = new Set((store.prefs.watchlist || []).map((s) => s.toUpperCase()));
  const evs = (store.calendar?.events || []).filter((e) => e.ts >= now - 3 * 3600 && e.ts <= now + 86400 * 30)
    .filter((e) => e.kind !== "earnings" || e.held || watch.has(e.ticker.toUpperCase()))
    .filter((e) => e.kind !== "macro" || e.importance >= 2)
    .slice(0, 7);
  if (!store.calendar) { box.replaceChildren(h("div", { class: "muted pad" }, "加载中…")); return; }
  if (!evs.length) { box.replaceChildren(h("div", { class: "muted pad" }, "未来 30 天没有标记事件")); return; }
  box.replaceChildren(...evs.map((e) => {
    const wt = e.kind === "earnings" ? w[e.ticker.toUpperCase()] : null;
    const soon = e.ts - now < 86400 * 3;
    return h("button", { class: `cat-row k-${e.kind}${soon ? " soon" : ""}`, type: "button",
      onclick: () => (e.kind === "earnings" ? emit("open-stock", e.ticker) : emit("goto", "calendar")),
      title: `${e.title} · 伦敦 ${ukTime(e.ts)}${e.source ? ` · ${e.source}` : ""}` },
    h("span", { class: "cat-when" }, h("b", {}, dateLabel(e.date).split(" · ")[0]), h("i", {}, e.kind === "earnings" ? e.timeLabel : ukTime(e.ts, { withDate: false, withWeekday: false }))),
    h("span", { class: "cat-what" }, h("span", { class: "cat-kind" }, { earnings: "财报", macro: "数据", fomc: "议息", custom: "事件" }[e.kind]), e.title),
    h("span", { class: "cat-meta" }, wt ? `${wt.toFixed(0)}%` : "", h("i", {}, countdown(e.ts))));
  }));
}

// ═════════════════════════════ signals (monitor intel) ═════════════════════════════
const TRIG = {
  macd_death_cross: ["MACD 死叉", "bad"], macd_golden_cross: ["MACD 金叉", "good"],
  below_ma20: ["跌破 MA20", "bad"], below_ma50: ["跌破 MA50", "bad"], recover_ma20: ["收回 MA20", "good"],
  rsi_lose_70: ["RSI 跌破 70", "bad"], rsi_oversold: ["RSI 超卖", "bad"], down_on_volume: ["放量下跌", "bad"],
  bearish_divergence: ["顶背离", "bad"], day_drop_5: ["单日跌 5%", "bad"], day_rip_5: ["单日涨 5%", "good"],
};
export function renderSignals() {
  const box = $("#signals");
  const d = store.intel;
  if (!box) return;
  if (!d) { box.replaceChildren(h("div", { class: "muted pad" }, "加载中…")); return; }
  const held = new Set((store.snap?.positions || []).map((p) => shortTicker(p.ticker).toUpperCase()));
  const watch = new Set((store.prefs.watchlist || []).map((s) => s.toUpperCase()));
  const trig = (d.triggers || []).map((t) => ({ ...t, sym: shortTicker(t.ticker).toUpperCase() }));
  const rel = trig.filter((t) => held.has(t.sym) || watch.has(t.sym));
  const stale = trig.filter((t) => !held.has(t.sym) && !watch.has(t.sym));
  const kids = [];
  if (rel.length) {
    kids.push(...rel.map((t) => h("div", { class: "sig-row" },
      h("button", { class: "sig-sym", type: "button", onclick: () => emit("open-stock", t.sym) }, t.sym),
      h("span", { class: "sig-chips" }, ...(t.active || []).map((r) => { const [l, k] = TRIG[r] || [r, "bad"]; return h("span", { class: `trig ${k}` }, l); })),
      h("span", { class: "sig-ago" }, ago(t.fired_at)))));
  } else {
    kids.push(h("div", { class: "muted pad" }, "当前持仓没有结构性技术触发"));
  }
  if (stale.length) kids.push(h("details", { class: "sig-stale" }, h("summary", {}, `监控里还有 ${stale.length} 只非持仓的旧信号`),
    h("div", { class: "muted" }, stale.map((t) => t.sym).join(" · "), " —— tech_monitor 的盯盘名单是 6 月定的，已与当前持仓不符")));
  if (d.korea && isNum(d.korea.korea)) {
    const k = d.korea;
    const fresh = k.date && (Date.now() - new Date(k.date).getTime()) / 86400000 < 4;
    kids.push(h("div", { class: `sig-korea${fresh ? "" : " stale"}` }, h("span", { class: "muted" }, `韩股领先 · ${k.date}`),
      h("span", {}, "EWY ", h("b", { class: updown(k.korea) }, pct(k.korea)), isNum(k.mu_gap) ? [" → MU 开盘缺口 ", h("b", { class: updown(k.mu_gap) }, pct(k.mu_gap, 1))] : null)));
  }
  box.replaceChildren(...kids);
}

export function renderCockpit() {
  renderStats(); renderBrief(); renderHoldings(); renderWatchlist(); renderMarket(); renderMap(); renderCatalysts(); renderSignals();
}
export { tip };
