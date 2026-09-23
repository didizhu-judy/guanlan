// 观澜 · trades (交易 · 税务) — working orders, price alerts, trading behaviour,
// fill journal, UK tax-year gauges (ISA allowance / GIA CGT).
import { $, h, api, store, on, emit, isNum, num, pct, money, price, updown, ago, shortTicker, savePref, qty } from "./core.js";
import { meter } from "./charts.js";
import { currentAccount, alertDistance, openSetup } from "./ui.js";

let fills = null, tax = null, loadedFor = null, loading = false;
let fillFilter = { q: "", side: "ALL", limit: 60 };

export async function loadTrades(force = false) {
  const acct = currentAccount();
  if (loading) return;
  if (!force && loadedFor === acct && fills) { renderTrades(); return; }
  loading = true;
  $("#view-trades")?.classList.add("refetch");
  try {
    [fills, tax] = await Promise.all([api(`/api/fills?account=${acct}&days=730&limit=2000`), api("/api/tax")]);
    if (acct === "all") store.fills = fills;
    loadedFor = acct;
  } catch (e) {
    $("#tr-fills").replaceChildren(h("div", { class: "muted pad" }, `加载失败：${e.message}`));
  } finally {
    loading = false;
    $("#view-trades")?.classList.remove("refetch");
  }
  renderTrades();
}
on("account", () => { loadedFor = null; if (store.view === "trades") loadTrades(true); });

export function renderTrades() {
  renderOrders();
  renderAlertList();
  renderBehaviour();
  renderFills();
  renderTax();
}

// ───────────────────────────── working orders ─────────────────────────────
export function renderOrders() {
  const box = $("#tr-orders");
  if (!box) return;
  const acct = currentAccount();
  const list = (store.orders?.orders || []).filter((o) => acct === "all" || o.account === acct);
  if (!store.orders) { box.replaceChildren(h("div", { class: "muted pad" }, "加载中…")); return; }
  if (!list.length) { box.replaceChildren(h("div", { class: "muted pad" }, "没有挂单")); return; }
  const now = Date.now();
  box.replaceChildren(h("table", { class: "tbl" },
    h("thead", {}, h("tr", {}, ...["账户", "代码", "方向", "类型", "数量", "触发价", "现价", "距离", "挂了", "有效期"].map((t, i) => h("th", { class: i >= 4 ? "num" : "" }, t)))),
    h("tbody", {}, ...list.map((o) => {
      const days = o.createdAt ? Math.floor((now - new Date(o.createdAt).getTime()) / 86400000) : null;
      const near = isNum(o.distancePct) && Math.abs(o.distancePct) <= 3;
      return h("tr", { class: near ? "near" : "", onclick: () => emit("open-stock", o.ticker) },
        h("td", { class: "muted" }, o.accountLabel),
        h("td", {}, h("b", {}, shortTicker(o.ticker)), h("div", { class: "muted small" }, o.name)),
        h("td", {}, h("span", { class: `side ${o.side === "BUY" ? "buy" : "sell"}` }, o.side === "BUY" ? "买" : "卖")),
        h("td", { class: "muted" }, { LIMIT: "限价", STOP: "止损", STOP_LIMIT: "止损限价", MARKET: "市价" }[o.type] || o.type),
        h("td", { class: "num" }, qty(o.quantity)),
        h("td", { class: "num" }, price(o.trigger, o.currency)),
        h("td", { class: "num" }, price(o.price, o.currency)),
        h("td", { class: "num" }, h("span", { class: `dist ${near ? "near" : ""}` }, pct(o.distancePct, 1))),
        h("td", { class: "num muted" }, days != null ? `${days} 天` : "—"),
        h("td", { class: "muted small" }, o.timeInForce === "GOOD_TILL_CANCEL" ? "撤销前有效" : o.timeInForce === "DAY" ? "当日" : o.timeInForce || ""));
    }))),
  h("div", { class: "tk-hint" }, "距离 = 还要涨/跌多少才触发。挂得离现价很远、又挂了几个月的单，值得回头看一眼当初的理由是否还成立。"));
}

// ───────────────────────────── price alerts ─────────────────────────────
function renderAlertList() {
  const box = $("#tr-alerts");
  if (!box) return;
  const al = store.prefs.alerts || [];
  if (!al.length) { box.replaceChildren(h("div", { class: "muted pad" }, "还没有价格提醒 —— 在个股详情里添加。")); return; }
  const rows = al.map((a) => ({ a, d: alertDistance(a) })).sort((x, y) => (x.a.firedAt ? 1 : 0) - (y.a.firedAt ? 1 : 0) || Math.abs(x.d?.dist ?? 1e9) - Math.abs(y.d?.dist ?? 1e9));
  box.replaceChildren(...rows.map(({ a, d }) => h("div", { class: `al-row${a.firedAt ? " fired" : ""}` },
    h("button", { type: "button", class: "link", onclick: () => emit("open-stock", a.ticker) }, a.ticker),
    h("span", { class: "num" }, `${a.op === ">=" ? "≥" : "≤"} ${price(a.price, d?.ccy || "USD")}`),
    h("span", { class: "muted" }, a.firedAt ? `已触发 ${ago(a.firedAt)}` : d ? `现价 ${price(d.price, d.ccy)} · 差 ${pct(d.dist, 1)}` : ""),
    h("span", { class: "al-note" }, a.note || ""),
    h("button", { class: "icon-btn sm", type: "button", "aria-label": "删除", onclick: async () => {
      await savePref("alerts", (store.prefs.alerts || []).filter((x) => x.id !== a.id), 0); renderAlertList(); emit("alerts-changed");
    } }, "×"))));
}

// ───────────────────────────── behaviour ─────────────────────────────
function renderBehaviour() {
  const box = $("#tr-behaviour");
  if (!box || !fills) return;
  const s = fills.summary || {};
  const tile = (k, label) => {
    const x = s[k] || {};
    return h("div", { class: "beh" },
      h("div", { class: "kpi-l" }, label),
      h("div", { class: "beh-grid" },
        h("span", {}, "成交"), h("b", { class: "num" }, `${x.trades ?? 0} 笔`),
        h("span", {}, "买 / 卖"), h("b", { class: "num" }, `${x.buys ?? 0} / ${x.sells ?? 0}`),
        h("span", {}, "成交额"), h("b", { class: "num money" }, money(x.turnover, { dp: 0 })),
        h("span", {}, "换汇费"), h("b", { class: "num money down" }, money(x.fees, { dp: 2 })),
        h("span", {}, "卖出胜率"), h("b", { class: "num" }, isNum(x.winRate) ? `${x.winRate.toFixed(0)}%` : "—"),
        h("span", {}, "涉及"), h("b", { class: "num" }, `${x.tickers ?? 0} 只`)));
  };
  const all = store.snap?.stats?.fxFees;
  const tv = store.snap?.stats?.totalValue || 0;
  const d90 = s.d90 || {};
  const turnoverX = tv ? d90.turnover / tv : null;
  box.replaceChildren(h("div", { class: "beh-row" }, tile("d7", "近 7 天"), tile("d30", "近 30 天"), tile("d90", "近 90 天")),
    h("div", { class: "tk-hint" },
      isNum(all) ? `累计换汇费 ${money(all)}：每笔美股买卖都付 0.15%，一买一卖 0.3%。` : "",
      isNum(turnoverX) ? ` 近 90 天成交额是账户总值的 ${turnoverX.toFixed(1)} 倍。` : "",
      " 胜率只看卖出那一笔的已实现盈亏，不含仍持有的仓位。"));
}

// ───────────────────────────── fills journal ─────────────────────────────
function renderFills() {
  const box = $("#tr-fills");
  if (!box || !fills) return;
  const q = fillFilter.q.trim().toUpperCase();
  const list = (fills.fills || []).filter((f) => (!q || f.short.includes(q) || shortTicker(f.ticker).includes(q) || (f.name || "").toUpperCase().includes(q))
    && (fillFilter.side === "ALL" || f.side === fillFilter.side));
  const status = Object.values(fills.status || {}).some((s) => s.syncing) ? h("span", { class: "muted small" }, " · 历史同步中…") : null;
  const search = h("input", { type: "search", placeholder: "筛选代码", value: fillFilter.q, "aria-label": "筛选代码", oninput: (e) => { fillFilter.q = e.target.value; fillFilter.limit = 60; renderFills(); requestAnimationFrame(() => { const el = $("#tr-fills input[type=search]"); el?.focus(); el?.setSelectionRange(el.value.length, el.value.length); }); } });
  const sideSeg = h("div", { class: "seg" }, ...[["ALL", "全部"], ["BUY", "买"], ["SELL", "卖"]].map(([v, l]) => h("button", { type: "button", class: `seg-btn${fillFilter.side === v ? " on" : ""}`, onclick: () => { fillFilter.side = v; renderFills(); } }, l)));
  const rows = list.slice(0, fillFilter.limit).map((f) => h("tr", { onclick: () => emit("open-stock", f.ticker) },
    h("td", { class: "muted num" }, new Date(f.filledAt).toLocaleString("en-GB", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" })),
    h("td", { class: "muted" }, f.accountLabel),
    h("td", {}, h("b", {}, shortTicker(f.ticker))),
    h("td", {}, h("span", { class: `side ${f.side === "BUY" ? "buy" : "sell"}` }, f.side === "BUY" ? "买" : "卖")),
    h("td", { class: "num" }, qty(f.quantity)),
    h("td", { class: "num" }, price(f.price, f.currency)),
    h("td", { class: "num money" }, money(Math.abs(f.value || 0))),
    h("td", { class: "num muted money" }, f.fee ? money(f.fee) : "—")));
  box.replaceChildren(
    h("div", { class: "tbl-tools" }, search, sideSeg, h("span", { class: "muted small" }, `${list.length} 笔`), status),
    h("table", { class: "tbl" },
      h("thead", {}, h("tr", {}, ...["时间", "账户", "代码", "方向", "数量", "成交价", "金额", "换汇费"].map((t, i) => h("th", { class: i >= 4 ? "num" : "" }, t)))),
      h("tbody", {}, ...rows)),
    list.length > fillFilter.limit ? h("button", { class: "btn btn-sm more", type: "button", onclick: () => { fillFilter.limit += 100; renderFills(); } }, `再显示 100 笔（还有 ${list.length - fillFilter.limit}）`) : null);
}

// ───────────────────────────── UK tax year ─────────────────────────────
function renderTax() {
  const box = $("#tr-tax");
  if (!box || !tax) return;
  const L = tax.limits;
  const cards = tax.accounts.map((a) => {
    if (a.kind === "isa") {
      const used = a.isaUsedTY ?? a.depositsTY;
      return h("div", { class: "tax-card" },
        h("div", { class: "tax-h" }, h("b", {}, `${a.label} · 股票 ISA`), h("span", { class: "muted" }, "账户内收益免税，只需管额度")),
        h("div", { class: "cc-h" }, h("span", {}, "本税年存入额度"), h("b", { class: used >= L.isa_allowance ? "warn" : "" }, `${money(used, { dp: 0 })} / ${money(L.isa_allowance, { dp: 0 })}`)),
        meter(used, L.isa_allowance, { warn: 0.8, danger: 0.999 }),
        h("div", { class: "cc-note" }, used >= L.isa_allowance ? "今年额度已用满 —— 再往里投要等 4 月 6 日新税年；多出的钱只能进 Invest（应税）。" : `还能存 ${money(L.isa_allowance - used, { dp: 0 })}。`),
        h("div", { class: "kv" },
          h("span", {}, "存入"), h("b", { class: "num" }, money(a.depositsTY, { dp: 2 })),
          h("span", {}, "取出（灵活 ISA 可回补）"), h("b", { class: "num" }, money(a.withdrawalsTY, { dp: 2 })),
          a.transfersTY ? h("span", {}, "转入") : null, a.transfersTY ? h("b", { class: "num" }, money(a.transfersTY)) : null,
          h("span", {}, "本税年已实现（免税）"), h("b", { class: `num ${updown(a.realisedNetTY)}` }, money(a.realisedNetTY, { sign: true })),
          h("span", {}, "股息 + 利息（免税）"), h("b", { class: "num" }, money(a.dividendsTY + a.interestTY))));
    }
    const taxable = a.realisedNetTY + a.feesTY;
    return h("div", { class: "tax-card" },
      h("div", { class: "tax-h" }, h("b", {}, `${a.label} · 普通账户（应税）`), h("span", { class: "muted" }, "资本利得税按税年结算")),
      h("div", { class: "cc-h" }, h("span", {}, "已实现净收益 vs 免税额"), h("b", { class: taxable > L.cgt_exempt ? "warn" : "" }, `${money(taxable, { dp: 0 })} / ${money(L.cgt_exempt, { dp: 0 })}`)),
      meter(Math.max(0, taxable), L.cgt_exempt, { warn: 0.8, danger: 1 }),
      h("div", { class: "cc-note" }, taxable > L.cgt_exempt ? `已超出免税额 ${money(taxable - L.cgt_exempt, { dp: 0 })}，超出部分需缴资本利得税。` : `离 £3,000 免税额还有 ${money(L.cgt_exempt - Math.max(0, taxable), { dp: 0 })}。`),
      h("div", { class: "cc-h" }, h("span", {}, "股息 vs £500 免税额"), h("b", {}, `${money(a.dividendsTY)} / ${money(L.dividend_allowance, { dp: 0 })}`)),
      meter(a.dividendsTY, L.dividend_allowance, { warn: 0.8, danger: 1 }),
      h("div", { class: "cc-h" }, h("span", {}, "卖出总额 vs £50,000 申报线"), h("b", { class: a.proceedsTY > L.cgt_report_proceeds ? "warn" : "" }, `${money(a.proceedsTY, { dp: 0 })}`)),
      meter(a.proceedsTY, L.cgt_report_proceeds, { warn: 0.8, danger: 1 }),
      h("div", { class: "cc-note" }, "卖出总额超过 £50,000，即使没税要交也得在 Self Assessment 申报。"),
      h("div", { class: "kv" },
        h("span", {}, "盈利卖出"), h("b", { class: "num up" }, money(a.realisedGainsTY, { sign: true })),
        h("span", {}, "亏损卖出（可抵扣）"), h("b", { class: "num down" }, money(a.realisedLossesTY)),
        h("span", {}, "交易费（可抵扣）"), h("b", { class: "num" }, money(a.feesTY)),
        h("span", {}, "现金利息（储蓄免税额另计）"), h("b", { class: "num" }, money(a.interestTY))));
  });
  if (!tax.accounts.some((a) => a.kind === "gia")) {
    const m = store.accounts?.missing?.[0];
    cards.push(h("div", { class: "tax-card ghost" },
      h("div", { class: "tax-h" }, h("b", {}, "Invest · 普通账户"), h("span", { class: "muted" }, "尚未接入")),
      h("p", { class: "muted" }, "Invest 账户的卖出要计资本利得税。接入后这里会实时显示本税年已实现收益离 £3,000 免税额还差多少、卖出总额是否过 £50,000 申报线。"),
      m ? h("button", { class: "btn btn-accent", type: "button", onclick: () => openSetup(m) }, "接入 Invest 账户") : null));
  }
  box.replaceChildren(h("div", { class: "tax-grid" }, ...cards),
    h("div", { class: "tk-hint" }, `税年 ${tax.taxYear}（${tax.start} → ${tax.end}）。按 T212 的平均成本口径估算，未处理同日 / 30 天匹配规则（bed & breakfasting）。仅供参考，不是税务建议。`));
}
