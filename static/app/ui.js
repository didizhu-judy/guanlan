// 观澜 · ui — shell chrome: theme, privacy, account switcher, views, clock,
// command palette (⌘K), settings, price-alert engine.
import {
  $, $$, h, api, store, on, emit, set, uiPref, setUiPref, savePref, toast, notify, debounce,
  usSession, dur, nyParts, shortTicker, findPosition, isNum, price as fmtPrice, pct, kick,
} from "./core.js";

// ─────────────────────────────── theme & display prefs ───────────────────────────────
export function applyDisplayPrefs() {
  const root = document.documentElement;
  const theme = uiPref("theme", "auto");
  if (theme === "auto") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
  root.dataset.updown = uiPref("updown", "west");           // west = 绿涨红跌 · cn = 红涨绿跌
  document.body.classList.toggle("privacy", !!uiPref("privacy", false));
  emit("theme");
}
export function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme")
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  setUiPref("theme", cur === "dark" ? "light" : "dark");
  applyDisplayPrefs();
}
export function togglePrivacy() {
  setUiPref("privacy", !uiPref("privacy", false));
  applyDisplayPrefs();
  toast(uiPref("privacy", false) ? "隐私模式：金额已隐藏" : "已显示金额", { timeout: 1800 });
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => emit("theme"));

// ─────────────────────────────── views (tabs) ───────────────────────────────
export const VIEWS = [
  { id: "cockpit", label: "盯盘", key: "1" },
  { id: "risk", label: "风险", key: "2" },
  { id: "calendar", label: "日历 · 新闻", key: "3" },
  { id: "trades", label: "交易 · 税务", key: "4" },
];
export function showView(id, { push = true } = {}) {
  if (!VIEWS.some((v) => v.id === id)) id = "cockpit";
  store.view = id;
  for (const v of VIEWS) {
    $(`#view-${v.id}`)?.toggleAttribute("hidden", v.id !== id);
    const tab = $(`.tab[data-view="${v.id}"]`);
    tab?.classList.toggle("on", v.id === id);
    tab?.setAttribute("aria-selected", v.id === id ? "true" : "false");
  }
  if (push) {
    const hash = location.hash.includes("stock=") ? location.hash.replace(/view=[^&]*/, `view=${id}`) : `#view=${id}`;
    history.replaceState(null, "", hash.includes("view=") ? hash : `${hash}&view=${id}`);
  }
  emit("view", id);
}
export function buildTabs() {
  const nav = $("#tabs");
  nav.replaceChildren(...VIEWS.map((v) => h("button", {
    class: "tab", type: "button", role: "tab", dataset: { view: v.id }, title: `${v.label} (${v.key})`,
    onclick: () => showView(v.id),
  }, v.label)));
}

// ─────────────────────────────── account switcher ───────────────────────────────
export function currentAccount() { return uiPref("account", "all"); }
export function renderAccountSwitch() {
  const box = $("#acct");
  const avail = store.snap?.accountsAvailable || store.accounts?.accounts?.filter((a) => a.configured) || [];
  const missing = store.accounts?.missing || [];
  const cur = currentAccount();
  const opts = avail.length > 1 ? [{ id: "all", label: "全部" }, ...avail] : avail;
  const kids = opts.map((a) => h("button", {
    type: "button", class: `seg-btn${a.id === cur || (opts.length === 1) ? " on" : ""}`,
    onclick: () => { if (opts.length > 1) { setUiPref("account", a.id); renderAccountSwitch(); emit("account", a.id); } },
    title: a.id === "all" ? "合并所有账户" : `${a.label} 账户`,
  }, a.label));
  for (const m of missing) {
    kids.push(h("button", { type: "button", class: "seg-btn seg-add", title: `接入 ${m.label} 账户`, onclick: () => openSetup(m) }, `+ ${m.label}`));
  }
  box.replaceChildren(...kids);
  const errs = (store.snap?.accounts || []).filter((a) => !a.ok);
  box.classList.toggle("has-err", errs.length > 0);
  box.title = errs.map((e) => e.error).join("\n");
}
export function openSetup(m) {
  const body = h("div", { class: "setup" },
    h("p", {}, `T212 的 API key 按账户分开发放：ISA 和 Invest 各需要一把。现在接入的是 ISA；接入 ${m.label} 需要你自己生成 key（涉及密钥，观澜不代填）。`),
    h("ol", {},
      h("li", {}, `在 T212 App 里切到 `, h("b", {}, m.label), ` 账户 → Settings → API → Generate key，权限只勾只读（账户、持仓、历史、订单）。`),
      h("li", {}, "打开 ", h("code", {}, "~/guanlan/.env"), "，填到这两行等号后面：", h("pre", {}, m.envKeys.map((k) => `${k}=…`).join("\n"))),
      h("li", {}, "保存文件。观澜每次刷新都会检查 .env —— 十秒左右页面就会出现 ", h("b", {}, m.label), "，不用重启。")),
    h("p", { class: "muted" }, "接入后：顶部可切换 全部 / ISA / Invest；持仓合并并标注账户；Invest 的资本利得免税额度会出现在「交易 · 税务」里。"));
  modal(`接入 ${m.label} 账户`, body);
}

// ─────────────────────────────── modal ───────────────────────────────
export function modal(title, body, { wide = false } = {}) {
  closeModal();
  const box = h("div", { class: "modal-bg", id: "modal", onclick: (e) => { if (e.target.id === "modal") closeModal(); } },
    h("div", { class: `modal${wide ? " wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "modal-head" }, h("h2", {}, title),
        h("button", { class: "icon-btn", "aria-label": "关闭", onclick: closeModal }, "×")),
      h("div", { class: "modal-body" }, body)));
  document.body.append(box);
  requestAnimationFrame(() => box.classList.add("in"));
  return box;
}
export function closeModal() { $("#modal")?.remove(); }

// ─────────────────────────────── session clock ───────────────────────────────
export function tickClock() {
  const el = $("#clock");
  if (!el) return;
  const hol = store.market?.status?.holiday || null;
  const s = usSession(hol);
  const uk = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date());
  const ny = nyParts();
  const nx = s.next && isNum(s.next.secs) ? `${s.next.label} ${dur(s.next.secs)}` : (s.next?.label || "");
  el.className = `clock s-${s.key}`;
  el.replaceChildren(
    h("span", { class: "clock-dot" }),
    h("span", { class: "clock-main" }, `美股 ${s.label}`),
    nx ? h("span", { class: "clock-sub" }, nx) : null);
  el.title = `伦敦 ${uk} · 纽约 ${String(ny.h).padStart(2, "0")}:${String(ny.m).padStart(2, "0")}` +
    (store.market?.holidays?.length ? `\n下个休市：${store.market.holidays.map((x) => `${x.atDate} ${x.eventName}${x.tradingHour ? `（${x.tradingHour} 短市）` : ""}`).join("、")}` : "");
}

// ─────────────────────────────── command palette ───────────────────────────────
let palette = null;
export function openPalette(prefill = "") {
  if (palette) { palette.input.focus(); return; }
  const input = h("input", { class: "pal-input", placeholder: "代码或公司名 · 回车打开 · Tab 加自选", autocomplete: "off", spellcheck: "false", "aria-label": "搜索" });
  const list = h("div", { class: "pal-list", role: "listbox" });
  const bg = h("div", { class: "pal-bg", onclick: (e) => { if (e.target === bg) closePalette(); } },
    h("div", { class: "pal" }, h("div", { class: "pal-top" }, h("span", { class: "pal-icon" }, "⌕"), input,
      h("kbd", {}, "esc")), list));
  document.body.append(bg);
  palette = { bg, input, list, items: [], sel: 0 };
  input.value = prefill;
  input.focus();
  const commands = [
    { kind: "cmd", label: "切换 日 / 夜 主题", hint: "T", run: toggleTheme },
    { kind: "cmd", label: "隐私模式（隐藏金额）", hint: "P", run: togglePrivacy },
    ...VIEWS.map((v) => ({ kind: "cmd", label: `前往：${v.label}`, hint: v.key, run: () => showView(v.id) })),
  ];
  const render = () => {
    const { items, sel } = palette;
    list.replaceChildren(...items.map((it, i) => h("div", {
      class: `pal-item${i === sel ? " on" : ""}`, role: "option", "aria-selected": i === sel ? "true" : "false",
      onmousemove: () => { if (palette.sel !== i) { palette.sel = i; render(); } },
      onclick: () => choose(it),
    },
    h("span", { class: `pal-kind k-${it.kind}` }, { held: "持仓", watch: "自选", remote: "搜索", cmd: "命令" }[it.kind]),
    h("span", { class: "pal-label" }, it.label),
    h("span", { class: "pal-hint" }, it.hint || ""))));
  };
  const localMatches = (q) => {
    const Q = q.toUpperCase();
    const held = (store.snap?.positions || []).map((p) => ({ kind: "held", sym: shortTicker(p.ticker), label: `${shortTicker(p.ticker)} · ${p.name}`, hint: fmtPrice(p.currentPrice, p.currency) }));
    const watch = (store.prefs.watchlist || []).map((s) => ({ kind: "watch", sym: s, label: s, hint: store.quotes[s] ? fmtPrice(store.quotes[s].price, "USD") : "" }));
    return [...held, ...watch].filter((x) => !Q || x.label.toUpperCase().includes(Q));
  };
  const remote = debounce(async (q) => {
    if (!q || q.length < 1) return;
    try {
      const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
      if (!palette || palette.input.value.trim() !== q) return;
      const have = new Set(palette.items.map((x) => x.sym));
      const add = r.results.filter((x) => !have.has(x.symbol)).map((x) => ({ kind: "remote", sym: x.symbol, label: `${x.display} · ${x.name || ""}`, hint: x.type }));
      palette.items = [...palette.items.filter((x) => x.kind !== "cmd"), ...add, ...palette.items.filter((x) => x.kind === "cmd")];
      render();
    } catch { /* ignore */ }
  }, 220);
  const update = () => {
    const q = input.value.trim();
    const cmds = commands.filter((c) => !q || c.label.includes(q));
    palette.items = [...localMatches(q).slice(0, 8), ...cmds];
    palette.sel = 0;
    render();
    remote(q);
  };
  const choose = (it, { watch = false } = {}) => {
    if (!it) return;
    closePalette();
    if (it.kind === "cmd") { it.run(); return; }
    if (watch) { addWatch(it.sym); return; }
    emit("open-stock", it.sym);
  };
  input.addEventListener("input", update);
  input.addEventListener("keydown", (e) => {
    const p = palette;
    if (e.key === "ArrowDown") { e.preventDefault(); p.sel = Math.min(p.items.length - 1, p.sel + 1); render(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); p.sel = Math.max(0, p.sel - 1); render(); }
    else if (e.key === "Enter") {
      e.preventDefault();
      const it = p.items[p.sel];
      if (it) choose(it);
      else if (input.value.trim()) { closePalette(); emit("open-stock", input.value.trim().toUpperCase()); }
    } else if (e.key === "Tab") {
      e.preventDefault();
      const it = p.items[p.sel];
      if (it && it.kind !== "cmd") choose(it, { watch: true });
    } else if (e.key === "Escape") closePalette();
  });
  update();
}
export function closePalette() { palette?.bg.remove(); palette = null; }

// ─────────────────────────────── watchlist ops ───────────────────────────────
export function addWatch(sym) {
  const s = String(sym || "").trim().toUpperCase().replace(/[^A-Z0-9.:=^-]/g, "");
  if (!s) return;
  const wl = store.prefs.watchlist || [];
  if (wl.includes(s)) { toast(`${s} 已在自选`, { timeout: 1600 }); return; }
  if (findPosition(s)) toast(`${s} 已在持仓里，也加入了自选`, { timeout: 2200 });
  savePref("watchlist", [...wl, s], 0);
  kick("quotes"); kick("techs");
  toast(`已加入自选：${s}`, { timeout: 1600 });
}
export function removeWatch(sym) {
  savePref("watchlist", (store.prefs.watchlist || []).filter((x) => x !== sym), 0);
}

// ─────────────────────────────── price alerts engine ───────────────────────────────
/** Latest known price for a symbol (holdings via T212 snapshot, else live quote). */
export function livePrice(sym) {
  const p = findPosition(sym);
  if (p && isNum(p.currentPrice)) return { price: p.currentPrice, ccy: p.currency };
  const q = store.quotes[String(sym).toUpperCase()];
  return q && isNum(q.price) ? { price: q.price, ccy: "USD" } : null;
}
export function checkAlerts() {
  const alerts = store.prefs.alerts || [];
  let changed = false;
  const next = alerts.map((a) => {
    if (a.firedAt) return a;
    const lp = livePrice(a.ticker);
    if (!lp) return a;
    const hit = a.op === ">=" ? lp.price >= a.price : lp.price <= a.price;
    if (!hit) return a;
    changed = true;
    const msg = `${a.ticker} 现价 ${fmtPrice(lp.price, lp.ccy)} ${a.op === ">=" ? "≥" : "≤"} ${fmtPrice(a.price, lp.ccy)}${a.note ? ` · ${a.note}` : ""}`;
    toast(msg, { kind: "alert", title: "价格提醒", timeout: 0, action: { label: "看详情", fn: () => emit("open-stock", a.ticker) } });
    notify("观澜 · 价格提醒", msg);
    return { ...a, firedAt: Date.now() / 1000 };
  });
  if (changed) savePref("alerts", next, 0);
}
export function alertDistance(a) {
  const lp = livePrice(a.ticker);
  return lp ? { price: lp.price, ccy: lp.ccy, dist: (a.price - lp.price) / lp.price * 100 } : null;
}
export async function ensureNotifyPermission() {
  if (!("Notification" in window)) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  return (await Notification.requestPermission()) === "granted";
}

// ─────────────────────────────── settings popover ───────────────────────────────
export function openSettings(anchor) {
  if ($("#settings-pop")) { $("#settings-pop").remove(); return; }
  const seg = (key, dflt, opts, after) => h("div", { class: "seg" }, ...opts.map(([v, label]) => h("button", {
    type: "button", class: `seg-btn${uiPref(key, dflt) === v ? " on" : ""}`,
    onclick: (e) => { setUiPref(key, v); applyDisplayPrefs(); after?.(); [...e.currentTarget.parentNode.children].forEach((b) => b.classList.toggle("on", b === e.currentTarget)); },
  }, label)));
  const pop = h("div", { class: "popover", id: "settings-pop", role: "dialog", "aria-label": "设置" },
    h("div", { class: "pop-row" }, h("span", {}, "主题"), seg("theme", "auto", [["auto", "跟随系统"], ["light", "纸"], ["dark", "夜"]])),
    h("div", { class: "pop-row" }, h("span", {}, "涨跌配色"), seg("updown", "west", [["west", "绿涨红跌"], ["cn", "红涨绿跌"]])),
    h("div", { class: "pop-row" }, h("span", {}, "隐私模式"), seg("privacy", false, [[false, "显示金额"], [true, "隐藏金额"]])),
    h("div", { class: "pop-row" }, h("span", {}, "价格提醒通知"),
      h("button", { type: "button", class: "btn btn-sm", onclick: async (e) => {
        const ok = await ensureNotifyPermission();
        e.currentTarget.textContent = ok ? "已开启 ✓" : "浏览器未允许";
      } }, "Notification" in window && Notification.permission === "granted" ? "已开启 ✓" : "开启系统通知")),
    h("div", { class: "pop-foot" }, "快捷键：⌘K 搜索 · 1–4 切页 · T 主题 · P 隐私 · Esc 关闭"));
  document.body.append(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.top = `${r.bottom + 8}px`;
  pop.style.right = `${Math.max(12, window.innerWidth - r.right)}px`;
  setTimeout(() => {
    const off = (e) => { if (!pop.contains(e.target) && e.target !== anchor) { pop.remove(); document.removeEventListener("pointerdown", off); } };
    document.addEventListener("pointerdown", off);
  });
}

// ─────────────────────────────── keyboard ───────────────────────────────
export function bindKeys() {
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
    if (e.key === "Escape") {
      if (palette) closePalette();
      else if ($("#modal")) closeModal();
      else emit("escape");
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "/") { e.preventDefault(); openPalette(); }
    else if (e.key === "t" || e.key === "T") toggleTheme();
    else if (e.key === "p" || e.key === "P") togglePrivacy();
    else { const v = VIEWS.find((x) => x.key === e.key); if (v) showView(v.id); }
  });
}

export { pct };
