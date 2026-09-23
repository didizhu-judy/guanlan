// 观澜 · calendar (日历 · 新闻) — catalyst timeline + holdings news feed.
import { $, h, api, store, on, emit, isNum, num, pct, compact, ago, countdown, ukTime, dateLabel, shortTicker, uiPref, setUiPref } from "./core.js";

let news = null, newsFor = "", newsLoading = false;
const KIND = { earnings: "财报", macro: "数据", fomc: "议息", custom: "事件" };

function heldW() {
  const pos = store.snap?.positions || [];
  const tot = pos.reduce((a, p) => a + (p.marketValue || 0), 0) || 1;
  const m = {};
  for (const p of pos) m[shortTicker(p.ticker).toUpperCase()] = (p.marketValue || 0) / tot * 100;
  return m;
}

export function renderCalendar() {
  const box = $("#cal-list");
  if (!box) return;
  if (!store.calendar) { box.replaceChildren(h("div", { class: "muted pad" }, "加载中…")); return; }
  const filt = uiPref("calFilter", "all");
  const watch = new Set((store.prefs.watchlist || []).map((s) => s.toUpperCase()));
  const w = heldW();
  const now = Date.now() / 1000;
  const evs = store.calendar.events.filter((e) => {
    if (e.ts < now - 86400 * 2) return false;
    if (filt === "held") return e.kind === "earnings" && e.held;
    if (filt === "macro") return e.kind === "macro" || e.kind === "fomc";
    if (filt === "watch") return e.kind === "earnings" && watch.has(e.ticker.toUpperCase());
    return e.kind !== "earnings" || e.held || watch.has(e.ticker.toUpperCase());
  });
  $$filters(filt);
  if (!evs.length) { box.replaceChildren(h("div", { class: "muted pad" }, "没有符合条件的事件")); return; }
  const byDay = new Map();
  for (const e of evs) { if (!byDay.has(e.date)) byDay.set(e.date, []); byDay.get(e.date).push(e); }
  const today = new Date().toISOString().slice(0, 10);
  box.replaceChildren(...[...byDay.entries()].map(([d, list]) => h("div", { class: `cal-day${d < today ? " past" : d === today ? " today" : ""}` },
    h("div", { class: "cal-date" }, h("b", {}, dateLabel(d).split(" · ")[0]), h("span", {}, dateLabel(d).split(" · ")[1] || "")),
    h("div", { class: "cal-items" }, ...list.map((e) => {
      const wt = e.kind === "earnings" ? w[e.ticker.toUpperCase()] : null;
      const det = e.detail || {};
      const lv = e.kind === "earnings" ? Object.values(store.levels).find((l) => l && l.symbol === e.ticker) : null;
      const em = lv && !lv.error && !lv.ivSuspect && isNum(lv.expMove) && lv.expiry >= e.date ? (lv.expMove / lv.spot * 100) : null;
      return h("div", { class: `cal-item k-${e.kind} imp-${e.importance}${e.held ? " held" : ""}`, tabindex: 0,
        onclick: () => { if (e.kind === "earnings") emit("open-stock", e.ticker); } },
      h("span", { class: "cal-time" }, e.kind === "earnings" ? e.timeLabel : ukTime(e.ts, { withDate: false, withWeekday: false }), h("i", {}, e.kind === "earnings" ? "" : "伦敦")),
      h("span", { class: `cal-kind k-${e.kind}` }, KIND[e.kind] || e.kind),
      h("span", { class: "cal-title" }, e.title,
        e.kind === "earnings" && det.quarter ? h("span", { class: "muted" }, ` Q${det.quarter} ${det.year}`) : null),
      h("span", { class: "cal-detail muted" },
        e.kind === "earnings" ? [isNum(det.epsEstimate) ? `EPS 预期 ${num(det.epsEstimate, 2)}` : "", isNum(det.revenueEstimate) ? ` · 营收 $${compact(det.revenueEstimate, 1)}` : "",
          isNum(det.epsActual) ? h("b", {}, ` · 实际 ${num(det.epsActual, 2)}`) : ""] : (e.source || "")),
      h("span", { class: "cal-right" },
        isNum(wt) ? h("span", { class: `cal-w${wt >= 15 ? " big" : ""}` }, `占持仓 ${wt.toFixed(0)}%`) : null,
        isNum(em) ? h("span", { class: "cal-em", title: "期权到期前的隐含波动（1σ）" }, `期权定价 ±${em.toFixed(1)}%`) : null,
        h("span", { class: "cal-cd" }, e.ts < now ? `${countdown(e.ts)}` : `${countdown(e.ts)}后`)));
    })))));
  $("#cal-note").textContent = "宏观日期来自美国 OMB 2026 年官方发布日程 + 美联储议息日历；财报日期来自 Finnhub（公司确认前可能变动）。";
}
function $$filters(cur) {
  document.querySelectorAll("#cal-filter .seg-btn").forEach((b) => b.classList.toggle("on", b.dataset.f === cur));
}
export function bindCalendar() {
  document.querySelectorAll("#cal-filter .seg-btn").forEach((b) => b.addEventListener("click", () => { setUiPref("calFilter", b.dataset.f); renderCalendar(); }));
  $("#news-general")?.addEventListener("change", () => loadNews(true));
}

export async function loadNews(force = false) {
  const general = $("#news-general")?.checked ? 1 : 0;
  const key = `${(store.snap?.positions || []).map((p) => p.ticker).join()}|${general}`;
  if (newsLoading || (!force && news && newsFor === key)) { renderNews(); return; }
  newsLoading = true;
  try { news = await api(`/api/news?days=3&per=6&general=${general}`); newsFor = key; }
  catch (e) { $("#news-list").replaceChildren(h("div", { class: "muted pad" }, `新闻加载失败：${e.message}`)); }
  finally { newsLoading = false; }
  renderNews();
}
let newsTicker = "ALL";
export function renderNews() {
  const box = $("#news-list"), chips = $("#news-chips");
  if (!box || !news) return;
  const counts = {};
  for (const n of news.items) counts[n.ticker || "市场"] = (counts[n.ticker || "市场"] || 0) + 1;
  chips.replaceChildren(...[["ALL", `全部 ${news.items.length}`], ...Object.entries(counts).map(([k, v]) => [k, `${k} ${v}`])].map(([k, l]) =>
    h("button", { type: "button", class: `chip chip-sm${newsTicker === k ? " on" : ""}`, onclick: () => { newsTicker = k; renderNews(); } }, l)));
  const items = news.items.filter((n) => newsTicker === "ALL" || (n.ticker || "市场") === newsTicker);
  box.replaceChildren(...items.slice(0, 80).map((n) => h("a", { class: "news-row", href: n.url, target: "_blank", rel: "noopener noreferrer" },
    h("span", { class: "news-top" }, n.ticker ? h("span", { class: "news-tk" }, n.ticker) : h("span", { class: "news-tk mkt" }, "市场"),
      h("span", { class: "news-m" }, `${n.source || ""} · ${ago(n.datetime)}`)),
    h("span", { class: "news-t" }, n.headline),
    n.summary && n.summary !== n.headline ? h("span", { class: "news-s" }, n.summary) : null)));
  if (!items.length) box.replaceChildren(h("div", { class: "muted pad" }, "暂无新闻"));
}
