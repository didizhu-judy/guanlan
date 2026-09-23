// 观澜 · risk (风险) — beta / vol / VaR, concentration, risk contribution,
// stress test, correlation, deposit-neutral performance, event exposure.
import { $, h, api, store, on, emit, isNum, num, signed, pct, money, updown, shortTicker, sectorOf, AI_CHAIN, cssVar, clamp, countdown, dateLabel } from "./core.js";
import { meter, lineChart, heatmap, dumbbell, hbars, tip } from "./charts.js";
import { currentAccount } from "./ui.js";

let risk = null, nav = null, loadedFor = null, loading = false;

export async function loadRisk(force = false) {
  const acct = currentAccount();
  if (loading || (!force && loadedFor === acct && risk)) { renderRisk(); return; }
  loading = true;
  $("#view-risk")?.classList.add("refetch");
  try {
    [risk, nav] = await Promise.all([api(`/api/risk?account=${acct}`), api(`/api/nav?account=${acct}`)]);
    loadedFor = acct;
  } catch (e) {
    $("#risk-kpis").replaceChildren(h("div", { class: "muted pad" }, `风险数据加载失败：${e.message}`));
  } finally {
    loading = false;
    $("#view-risk")?.classList.remove("refetch");
  }
  renderRisk();
}
on("account", () => { loadedFor = null; if (store.view === "risk") loadRisk(true); });

function kpi(label, value, sub, cls = "") {
  return h("div", { class: `kpi ${cls}` }, h("div", { class: "kpi-l" }, label), h("div", { class: "kpi-v" }, value), sub ? h("div", { class: "kpi-s" }, sub) : null);
}

export function renderRisk() {
  if (!risk || risk.error) {
    if (risk?.error) $("#risk-kpis").replaceChildren(h("div", { class: "muted pad" }, risk.error));
    return;
  }
  const r = risk;
  const hv = r.holdingsValue;
  $("#risk-asof").textContent = `基于近 ${r.days} 个交易日 · 截至 ${r.asOf} · 当前权重回测`;
  $("#risk-kpis").replaceChildren(
    kpi("组合 Beta · 标普", num(r.beta?.SPY, 2), `纳指 ${num(r.beta?.QQQ, 2)} · 半导体 ${num(r.beta?.SMH, 2)}`, r.beta?.SPY >= 1.5 ? "warn" : ""),
    kpi("年化波动", `${num(r.vol, 0)}%`, `标普长期约 15–20%`, r.vol >= 40 ? "warn" : ""),
    kpi("单日 VaR 95%", h("span", { class: "money" }, money(r.var95.amount, { dp: 0 })), `约每 20 个交易日会有 1 天亏得比这多（${pct(r.var95.pct, 1, false)}）`, "warn"),
    kpi("最差单日（回测）", h("span", { class: "money down" }, money(r.worstDay?.amount, { dp: 0 })), r.worstDay ? `${r.worstDay.date} · ${pct(r.worstDay.pct, 1)}` : ""),
    kpi("有效持仓数", num(r.effectiveN, 1), `${(store.snap?.positions || []).length} 只票，分散效果≈ ${num(r.effectiveN, 1)} 只等权`, r.effectiveN < 5 ? "warn" : ""),
    kpi("平均相关性", num(r.avgCorr, 2), "前 10 大持仓两两之间，按仓位加权", r.avgCorr >= 0.5 ? "warn" : ""));

  // risk contribution vs weight
  const rows = (r.positions || []).filter((x) => x.covered).sort((a, b) => (b.riskShare ?? 0) - (a.riskShare ?? 0)).slice(0, 10)
    .map((x) => ({ label: shortTicker(x.ticker), a: x.weight, b: x.riskShare }));
  dumbbell($("#risk-contrib"), rows, { aName: "仓位占比", bName: "波动贡献" });
  const topR = (r.positions || []).filter((x) => x.covered).sort((a, b) => (b.riskShare ?? 0) - (a.riskShare ?? 0))[0];
  $("#risk-contrib-note").textContent = topR
    ? `${shortTicker(topR.ticker)} 占持仓 ${topR.weight.toFixed(0)}%，却贡献了组合 ${num(topR.riskShare, 0)}% 的波动（Beta ${num(topR.beta_SPY, 1)}，年化波动 ${num(topR.vol, 0)}%）。仓位看的是钱放在哪，风险贡献看的是账户的起伏由谁决定。`
    : "";

  renderStress();

  // correlation
  if (r.corr?.tickers?.length) heatmap($("#risk-corr"), { labels: r.corr.tickers.map((t) => shortTicker(t)), matrix: r.corr.matrix });
  $("#risk-corr-note").textContent = `近 ${r.corr?.window || 120} 个交易日日收益的相关系数。越红 = 越同涨同跌，分散效果越差。`;

  renderNav();
  renderRiskLive();
}

/** The parts that follow the live snapshot (weights, events, currency) —
 *  cheap to repaint every tick, unlike the charts above. */
export function renderRiskLive() {
  if (!risk || risk.error || !store.snap) return;
  const pos = store.snap?.positions || [];
  const tot = pos.reduce((a, p) => a + (p.marketValue || 0), 0) || 1;
  const ws = pos.map((p) => ({ sym: shortTicker(p.ticker), w: (p.marketValue || 0) / tot * 100, p })).sort((a, b) => b.w - a.w);
  const top1 = ws[0] || { sym: "—", w: 0 };
  const top3 = ws.slice(0, 3).reduce((a, x) => a + x.w, 0);
  const ai = ws.filter((x) => AI_CHAIN.has(x.sym.toUpperCase())).reduce((a, x) => a + x.w, 0);
  const cc = (label, v, limit, note) => h("div", { class: "cc" },
    h("div", { class: "cc-h" }, h("span", {}, label), h("b", { class: v > limit ? "warn" : "" }, `${v.toFixed(1)}%`)),
    meter(v, 100, { warn: limit / 100 * 0.85, danger: limit / 100, marker: limit }),
    h("div", { class: "cc-note" }, note));
  const sectors = {};
  for (const x of ws) { const s = sectorOf(x.p); sectors[s] = (sectors[s] || 0) + x.w; }
  const secRows = Object.entries(sectors).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: k, value: v }));
  const secBox = h("div", {});
  hbars(secBox, secRows, { max: 100 });
  $("#risk-conc").replaceChildren(
    cc(`最大单票 · ${top1.sym}`, top1.w, 25, "竖线 = 25% 警戒：单票超过四分之一，它的一次财报就能决定整个账户的季度"),
    cc("前三大合计", top3, 60, `${ws.slice(0, 3).map((x) => x.sym).join(" + ")}`),
    cc("AI / 半导体链", ai, 60, "名字不同，押的是同一个故事 —— 行情逆转时会一起跌"),
    h("div", { class: "cc-h sub" }, "板块分布"), secBox);
  const effN = $("#risk-kpis .kpi:nth-child(5) .kpi-s");
  if (effN) effN.textContent = `${pos.length} 只票，分散效果≈ ${num(risk.effectiveN, 1)} 只等权`;
  renderEventExposure(ws);
  renderCurrency();
}

// ───────────────────────────── stress test ─────────────────────────────
const SCENARIOS = [
  { id: "ndx5", label: "纳指 −5%", bench: "QQQ", shock: -5 },
  { id: "smh10", label: "半导体 −10%", bench: "SMH", shock: -10 },
  { id: "spx10", label: "标普 −10%", bench: "SPY", shock: -10 },
  { id: "smh20", label: "半导体 −20%（2022 式）", bench: "SMH", shock: -20 },
  { id: "top15", label: "最大持仓财报 −15%", single: true, shock: -15 },
  { id: "gbp5", label: "英镑兑美元 +5%", fx: 5 },
];
let scen = SCENARIOS[1];
let custom = null;
let stressUI = null;      // controls are built once; only the result pane repaints
function renderStress() {
  const box = $("#risk-stress");
  if (!box || !risk) return;
  if (!stressUI || !box.contains(stressUI.btns)) {
    const btns = h("div", { class: "seg wrap" }, ...SCENARIOS.map((x) => h("button", {
      type: "button", class: "seg-btn", dataset: { id: x.id },
      onclick: () => { scen = x; custom = null; paintStress(); },
    }, x.label)));
    const slider = h("input", { type: "range", min: -30, max: 10, step: 1, value: -10, "aria-label": "自定义冲击幅度" });
    const bsel = h("select", { "aria-label": "基准" }, ...[["SMH", "半导体 SMH"], ["QQQ", "纳指 QQQ"], ["SPY", "标普 SPY"]].map(([v, l]) => h("option", { value: v }, l)));
    const val = h("b", { class: "num" }, "−10%");
    const onCustom = () => { custom = { id: "custom", label: "自定义", bench: bsel.value, shock: +slider.value }; paintStress(); };
    slider.addEventListener("input", onCustom);
    bsel.addEventListener("change", onCustom);
    const res = h("div", {});
    stressUI = { btns, slider, bsel, val, res };
    box.replaceChildren(btns, h("div", { class: "stress-custom" }, h("span", { class: "muted" }, "自定义："), bsel, slider, val), res);
  }
  paintStress();
}
function paintStress() {
  const r = risk, ui = stressUI;
  if (!r || !ui) return;
  const s = custom || scen;
  for (const b of ui.btns.children) b.classList.toggle("on", !custom && b.dataset.id === scen.id);
  ui.val.textContent = `${ui.slider.value > 0 ? "+" : ui.slider.value < 0 ? "−" : ""}${Math.abs(ui.slider.value)}%`;
  const topSym = [...(r.positions || [])].sort((a, b) => b.weight - a.weight)[0];
  const impacts = (r.positions || []).map((x) => {
    let move = 0;
    if (s.fx) move = (x.currency || "").toUpperCase() === "USD" ? (1 / (1 + s.fx / 100) - 1) * 100 : 0;
    else if (s.single) move = x.ticker === topSym?.ticker ? s.shock : 0;
    else move = (x[`beta_${s.bench}`] ?? 1) * s.shock;
    move = clamp(move, -95, 300);
    return { sym: shortTicker(x.ticker), w: x.weight, move, gbp: x.marketValue * move / 100 };
  });
  const total = impacts.reduce((a, x) => a + x.gbp, 0);
  const tv = r.totalValue || r.holdingsValue;
  const list = impacts.filter((x) => Math.abs(x.gbp) >= 1).sort((a, b) => a.gbp - b.gbp).slice(0, 8);
  const bars = h("div", {});
  hbars(bars, list.map((x) => ({ label: x.sym, value: x.gbp, cls: x.gbp < 0 ? "down" : "up", title: `${x.sym} 预计 ${pct(x.move, 1)}` })),
    { fmt: (v) => money(v, { dp: 0, sign: true }) });
  ui.res.replaceChildren(
    h("div", { class: "stress-res" },
      h("div", {}, h("div", { class: "kpi-l" }, `情景：${s.label}${custom ? ` · ${s.bench} ${s.shock > 0 ? "+" : ""}${s.shock}%` : ""}`),
        h("div", { class: `kpi-v money ${updown(total)}` }, money(total, { dp: 0, sign: true })),
        h("div", { class: "kpi-s" }, `约占账户总值 ${pct(total / tv * 100, 1)}（现金不动）`))),
    bars,
    h("div", { class: "tk-hint" }, s.fx ? "英镑升值 → 同样的美元资产换回的英镑变少；不影响美元计价的涨跌。" :
      s.single ? `假设只有 ${shortTicker(topSym?.ticker)} 单独下跌，其他不动。` :
        "按每只股票对基准的历史 Beta 线性放大 —— 真实暴跌时相关性会上升，损失通常比这更大。"));
}

// ───────────────────────────── performance (deposit-neutral) ─────────────────────────────
function renderNav() {
  const box = $("#risk-nav");
  if (!box) return;
  const s = nav?.series || [];
  if (s.length < 3) { box.replaceChildren(h("div", { class: "muted pad" }, "每日净值记录不足（每天首次打开观澜会记一笔）")); return; }
  const x = s.map((p) => p.date);
  const series = [
    { name: "我的组合（剔除入金）", short: "组合", values: s.map((p) => p.twr), color: cssVar("--accent"), emphasis: true },
    nav.bench?.SPY ? { name: "标普 500 SPY", short: "SPY", values: nav.bench.SPY, color: cssVar("--bench-1") } : null,
    nav.bench?.QQQ ? { name: "纳指 100 QQQ", short: "QQQ", values: nav.bench.QQQ, color: cssVar("--bench-2") } : null,
  ].filter(Boolean);
  const fmtD = (d, long) => { const [y, m, dd] = d.split("-"); return long ? `${y}-${m}-${dd}` : `${+m}/${+dd}`; };
  lineChart(box, { x, series, height: 220, yFmt: (v) => `${v > 0 ? "+" : ""}${Math.abs(v) < 0.5 ? "0" : v.toFixed(0)}%`, vFmt: (v) => pct(v, 1), xFmt: fmtD, zero: true });
  lineChart($("#risk-dd"), { x, series: [{ name: "回撤", short: "回撤", values: s.map((p) => p.drawdown), color: cssVar("--down"), emphasis: true }], height: 110, yFmt: (v) => `${v.toFixed(0)}%`, vFmt: (v) => pct(v, 1), xFmt: fmtD, area: true, endLabels: false });
  const last = s[s.length - 1];
  const bSpy = nav.bench?.SPY?.[nav.bench.SPY.length - 1];
  const since = s[0].date;
  $("#risk-nav-note").replaceChildren(
    h("span", {}, `自 ${since} 起：组合 `, h("b", { class: updown(last.twr) }, pct(last.twr, 1)),
      isNum(bSpy) ? [" · 标普 ", h("b", { class: updown(bSpy) }, pct(bSpy, 1))] : null,
      " · 期间最大回撤 ", h("b", { class: "down" }, pct(nav.maxDrawdown, 1)),
      " · 累计收益 ", h("b", { class: `money ${updown(last.gain)}` }, money(last.gain, { dp: 0, sign: true }))),
    h("span", { class: "muted" }, " · 时间加权收益：入金/出金不算作涨跌"));
}

// ───────────────────────────── event & currency exposure ─────────────────────────────
function renderEventExposure(ws) {
  const box = $("#risk-events");
  if (!box) return;
  const now = Date.now() / 1000;
  const map = {};
  for (const e of store.calendar?.events || []) if (e.kind === "earnings" && e.held && e.ts >= now - 3600) map[e.ticker.toUpperCase()] ||= e;
  const within = (d) => ws.filter((x) => map[x.sym.toUpperCase()] && map[x.sym.toUpperCase()].ts - now <= d * 86400);
  const w14 = within(14).reduce((a, x) => a + x.w, 0), w30 = within(30).reduce((a, x) => a + x.w, 0);
  const rows = within(45).map((x) => {
    const e = map[x.sym.toUpperCase()];
    return h("button", { class: "ev-row", type: "button", onclick: () => emit("open-stock", x.sym) },
      h("b", {}, x.sym), h("span", {}, `${dateLabel(e.date)} ${e.timeLabel}`), h("span", { class: "muted" }, `${countdown(e.ts)}后`),
      h("span", { class: "num" }, `${x.w.toFixed(1)}%`));
  });
  box.replaceChildren(
    h("div", { class: "kpi-row" }, kpi("14 天内有财报", `${w14.toFixed(0)}%`, "的持仓市值", w14 >= 30 ? "warn" : ""), kpi("30 天内", `${w30.toFixed(0)}%`, "的持仓市值")),
    rows.length ? h("div", { class: "ev-list" }, ...rows) : h("div", { class: "muted pad" }, "45 天内没有持仓财报"));
}
function renderCurrency() {
  const box = $("#risk-ccy");
  if (!box || !risk) return;
  const c = risk.currency || {};
  const cash = risk.cash || 0;
  const tot = Object.values(c).reduce((a, v) => a + v, 0) + cash || 1;
  const rows = [...Object.entries(c).map(([k, v]) => ({ label: k === "USD" ? "美元资产" : k === "GBP" ? "英镑资产" : k, value: v / tot * 100 })),
    { label: "现金（英镑）", value: cash / tot * 100 }].sort((a, b) => b.value - a.value);
  const bars = h("div", {});
  hbars(bars, rows, { max: 100 });
  const accts = (store.snap?.accounts || []).filter((a) => a.ok);
  box.replaceChildren(bars, h("div", { class: "tk-hint" }, risk.fxNote || ""),
    accts.length > 1 ? h("div", { class: "acct-split" }, ...accts.map((a) => h("div", {}, h("span", {}, a.label), h("b", { class: "money num" }, money(a.totalValue, { dp: 0 }))))) : null);
}
on("theme", () => { if (store.view === "risk" && risk) renderRisk(); });
