// 观澜 · charts — dependency-free SVG/HTML chart primitives.
// Colors come from CSS custom properties so light/dark/涨跌配色 swap in one place.
import { h, esc, cssVar, isNum, clamp } from "./core.js";

const SVGNS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}) => {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  return el;
};

// ─────────────────────────────── color helpers ───────────────────────────────
function hexToRgb(hex) {
  const m = String(hex).trim().replace("#", "");
  const n = m.length === 3 ? m.split("").map((c) => c + c).join("") : m;
  return [parseInt(n.slice(0, 2), 16), parseInt(n.slice(2, 4), 16), parseInt(n.slice(4, 6), 16)];
}
const rgbToHex = (r, g, b) => "#" + [r, g, b].map((v) => Math.round(clamp(v, 0, 255)).toString(16).padStart(2, "0")).join("");
export function mix(a, b, t) {
  const A = hexToRgb(a), B = hexToRgb(b);
  return rgbToHex(A[0] + (B[0] - A[0]) * t, A[1] + (B[1] - A[1]) * t, A[2] + (B[2] - A[2]) * t);
}
export function luminance(hex) {
  const [r, g, b] = hexToRgb(hex).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
/** Ink that clears contrast on a given fill. */
export const inkOn = (fill) => (luminance(fill) > 0.36 ? cssVar("--ink-on-light") || "#1d1a15" : "#ffffff");

/** Diverging color: neg pole ← mid (gray) → pos pole, t ∈ [-1, 1]. */
export function diverge(t, neg, mid, pos) {
  t = clamp(t, -1, 1);
  return t >= 0 ? mix(mid, pos, t) : mix(mid, neg, -t);
}
/** Day-change color for tiles: 涨/跌 tokens, saturation by magnitude (±4% = full). */
export function moveColor(pctVal, full = 4) {
  const mid = cssVar("--tile-mid");
  if (!isNum(pctVal)) return mid;
  const t = clamp(Math.abs(pctVal) / full, 0, 1);
  const eased = 0.22 + 0.78 * Math.sqrt(t);
  return mix(mid, cssVar(pctVal >= 0 ? "--up-fill" : "--down-fill"), pctVal === 0 ? 0 : eased);
}

// ─────────────────────────────── tooltip (singleton) ───────────────────────────────
let tipEl = null;
export function tip(show, x, y, rows, title) {
  if (!tipEl) { tipEl = h("div", { class: "viz-tip", role: "tooltip" }); document.body.append(tipEl); }
  if (!show) { tipEl.classList.remove("on"); return; }
  tipEl.replaceChildren(
    title ? h("div", { class: "viz-tip-title" }, title) : null,
    ...rows.map((r) => h("div", { class: "viz-tip-row" },
      r.color ? h("span", { class: "viz-key", style: { background: r.color } }) : null,
      h("span", { class: "viz-tip-v" }, r.value),
      r.label ? h("span", { class: "viz-tip-l" }, r.label) : null)));
  tipEl.classList.add("on");
  const pad = 14, w = tipEl.offsetWidth, hh = tipEl.offsetHeight;
  let left = x + pad, top = y + pad;
  if (left + w > window.innerWidth - 8) left = x - w - pad;
  if (top + hh > window.innerHeight - 8) top = y - hh - pad;
  tipEl.style.transform = `translate(${Math.max(8, left)}px, ${Math.max(8, top)}px)`;
}

// ─────────────────────────────── sparkline ───────────────────────────────
/** Muted trend line + light wash; the end dot carries direction. */
export function sparkline(values, { w = 72, hgt = 22, dir = null } = {}) {
  const vals = (values || []).filter(isNum);
  const root = svg("svg", { class: "spark", viewBox: `0 0 ${w} ${hgt}`, width: w, height: hgt, "aria-hidden": "true" });
  if (vals.length < 2) return root;
  const lo = Math.min(...vals), hi = Math.max(...vals), rng = hi - lo || 1, pad = 2.5;
  const X = (i) => pad + (i / (vals.length - 1)) * (w - 2 * pad);
  const Y = (v) => pad + (1 - (v - lo) / rng) * (hgt - 2 * pad);
  const pts = vals.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`);
  const d = dir || (vals[vals.length - 1] >= vals[0] ? "up" : "down");
  root.append(
    svg("path", { d: `M${pts[0]} L${pts.join(" L")} L${X(vals.length - 1).toFixed(1)},${hgt} L${X(0).toFixed(1)},${hgt}Z`, class: "spark-area" }),
    svg("polyline", { points: pts.join(" "), class: "spark-line" }),
    svg("circle", { cx: X(vals.length - 1), cy: Y(vals[vals.length - 1]), r: 2.4, class: `spark-dot ${d}` }));
  return root;
}

// ─────────────────────────────── inline range bar ───────────────────────────────
/** low────●────high with a tick at the reference (prev close). */
export function rangeBar(lo, hi, cur, ref, { w = 84 } = {}) {
  const root = svg("svg", { class: "rangebar", viewBox: `0 0 ${w} 14`, width: w, height: 14, "aria-hidden": "true" });
  if (![lo, hi, cur].every(isNum) || hi <= lo) return root;
  const x = (v) => 3 + clamp((v - lo) / (hi - lo), 0, 1) * (w - 6);
  root.append(svg("line", { x1: 3, x2: w - 3, y1: 7, y2: 7, class: "rb-track" }));
  if (isNum(ref)) {
    root.append(svg("line", { x1: x(Math.min(ref, cur)), x2: x(Math.max(ref, cur)), y1: 7, y2: 7, class: `rb-fill ${cur >= ref ? "up" : "down"}` }));
    root.append(svg("line", { x1: x(ref), x2: x(ref), y1: 3, y2: 11, class: "rb-ref" }));
  }
  const out = cur > hi || cur < lo;
  root.append(svg("circle", { cx: x(cur), cy: 7, r: 3.2, class: `rb-dot${out ? " out" : ""}` }));
  return root;
}

// ─────────────────────────────── meter ───────────────────────────────
/** Horizontal meter; severity by thresholds (fractions of max). */
export function meter(value, max, { warn = 0.75, danger = 0.95, label = null, marker = null } = {}) {
  const f = max > 0 && isNum(value) ? clamp(value / max, 0, 1) : 0;
  const sev = f >= danger ? "danger" : f >= warn ? "warn" : "ok";
  const el = h("div", { class: `meter meter-${sev}`, role: "meter", "aria-valuemin": 0, "aria-valuemax": max, "aria-valuenow": value, "aria-label": label || "" },
    h("div", { class: "meter-fill", style: { width: `${(f * 100).toFixed(1)}%` } }));
  if (isNum(marker) && max > 0) el.append(h("div", { class: "meter-mark", style: { left: `${clamp(marker / max, 0, 1) * 100}%` } }));
  return el;
}

// ─────────────────────────────── line chart ───────────────────────────────
function niceTicks(lo, hi, n = 4) {
  if (!(hi > lo)) { const p = Math.abs(lo) || 1; lo -= p * 0.1; hi += p * 0.1; }
  const span = hi - lo, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n + 0.5) || raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

/**
 * Multi-series line chart on ONE y-axis, with crosshair + tooltip.
 * series: [{name, values, color, emphasis, width, dash}]  x: labels (dates)
 */
export function lineChart(container, { x, series, height = 220, yFmt = (v) => v, vFmt = null, xFmt = (v) => v,
  zero = false, area = false, endLabels = true, band = null } = {}) {
  container.classList.add("viz");
  const fmtV = vFmt || yFmt;
  const draw = () => {
    const W = Math.max(260, container.clientWidth);
    const H = height;
    const m = { t: 12, r: endLabels ? 64 : 14, b: 24, l: 46 };
    const all = series.flatMap((s) => s.values.filter(isNum));
    if (band) all.push(...band.hi.filter(isNum), ...band.lo.filter(isNum));
    if (zero) all.push(0);
    if (!all.length) { container.replaceChildren(h("div", { class: "viz-empty" }, "暂无数据")); return; }
    let lo = Math.min(...all), hi = Math.max(...all);
    const padY = (hi - lo) * 0.08 || 1;
    lo -= padY; hi += padY;
    const ticks = niceTicks(lo, hi, Math.max(3, Math.round(H / 60)));
    lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
    const n = x.length;
    const X = (i) => m.l + (n <= 1 ? 0 : (i / (n - 1)) * (W - m.l - m.r));
    const Y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "viz-svg", role: "img" });
    const g = svg("g");
    for (const t of ticks) {
      g.append(svg("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: t === 0 && zero ? "viz-zero" : "viz-grid" }));
      const lab = svg("text", { x: m.l - 8, y: Y(t) + 3.5, class: "viz-axis", "text-anchor": "end" });
      lab.textContent = yFmt(t);
      g.append(lab);
    }
    const xt = Math.min(5, n);
    for (let k = 0; k < xt; k++) {
      const i = Math.round((k / Math.max(1, xt - 1)) * (n - 1));
      const lab = svg("text", { x: X(i), y: H - 6, class: "viz-axis", "text-anchor": k === 0 ? "start" : k === xt - 1 ? "end" : "middle" });
      lab.textContent = xFmt(x[i]);
      g.append(lab);
    }
    if (band) {
      const top = band.hi.map((v, i) => (isNum(v) ? `${X(i)},${Y(v)}` : null)).filter(Boolean);
      const bot = band.lo.map((v, i) => (isNum(v) ? `${X(i)},${Y(v)}` : null)).filter(Boolean).reverse();
      if (top.length > 1) g.append(svg("path", { d: `M${top.join(" L")} L${bot.join(" L")}Z`, class: "viz-band" }));
    }
    const ordered = [...series].sort((a, b) => (a.emphasis ? 1 : 0) - (b.emphasis ? 1 : 0));
    for (const s of ordered) {
      const pts = s.values.map((v, i) => (isNum(v) ? [X(i), Y(v)] : null));
      const segs = [];
      let cur = [];
      for (const p of pts) { if (p) cur.push(p); else if (cur.length) { segs.push(cur); cur = []; } }
      if (cur.length) segs.push(cur);
      for (const sg of segs) {
        if (area && s.emphasis && sg.length > 1) {
          const base = Y(zero ? 0 : lo);
          g.append(svg("path", { d: `M${sg[0][0]},${base} L${sg.map((p) => p.join(",")).join(" L")} L${sg[sg.length - 1][0]},${base}Z`, fill: s.color, "fill-opacity": 0.1 }));
        }
        g.append(svg("polyline", { points: sg.map((p) => p.join(",")).join(" "), fill: "none", stroke: s.color,
          "stroke-width": s.width || (s.emphasis ? 2 : 1.5), "stroke-linejoin": "round", "stroke-linecap": "round",
          "stroke-dasharray": s.dash || null }));
      }
      const li = s.values.map((v, i) => (isNum(v) ? i : -1)).filter((i) => i >= 0).pop();
      if (li != null) g.append(svg("circle", { cx: X(li), cy: Y(s.values[li]), r: s.emphasis ? 4 : 3, fill: s.color, class: "viz-end" }));
    }
    if (endLabels) {
      const labs = series.map((s) => {
        const li = s.values.map((v, i) => (isNum(v) ? i : -1)).filter((i) => i >= 0).pop();
        return li == null ? null : { s, y: Y(s.values[li]), v: s.values[li] };
      }).filter(Boolean).sort((a, b) => a.y - b.y);
      let lastY = -1e9;
      for (const L of labs) {
        if (L.y - lastY < 13 && !L.s.emphasis) continue;          // no stacked/colliding labels
        lastY = L.y;
        const t = svg("text", { x: W - m.r + 8, y: L.y + 4, class: `viz-endlabel${L.s.emphasis ? " em" : ""}` });
        t.textContent = `${L.s.short || L.s.name} ${fmtV(L.v)}`;
        g.append(t);
      }
    }
    const cross = svg("line", { y1: m.t, y2: H - m.b, class: "viz-cross", visibility: "hidden" });
    const hit = svg("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" });
    g.append(cross, hit);
    const onMove = (ev) => {
      const r = root.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W;
      const i = clamp(Math.round(((px - m.l) / (W - m.l - m.r)) * (n - 1)), 0, n - 1);
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible");
      tip(true, ev.clientX, ev.clientY, series.filter((s) => isNum(s.values[i]))
        .map((s) => ({ color: s.color, value: fmtV(s.values[i]), label: s.name })), xFmt(x[i], true));
    };
    hit.addEventListener("pointermove", onMove);
    hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); tip(false); });
    root.append(g);
    container.replaceChildren(root);
  };
  draw();
  observe(container, draw);
}

// Re-render on width change (one observer for all charts).
const RO_CB = new WeakMap();
const ro = new ResizeObserver((entries) => {
  for (const e of entries) {
    const cb = RO_CB.get(e.target);
    const w = Math.round(e.contentRect.width);
    if (cb && cb.w !== w) { cb.w = w; cb.fn(); }
  }
});
export function observe(el, fn) {
  RO_CB.set(el, { fn, w: Math.round(el.clientWidth) });
  ro.observe(el);
}

// ─────────────────────────────── heatmap (correlation) ───────────────────────────────
export function heatmap(container, { labels, matrix, fmt = (v) => v.toFixed(2), title = "相关系数" }) {
  container.classList.add("viz");
  const n = labels.length;
  const neg = cssVar("--div-neg"), mid = cssVar("--div-mid"), pos = cssVar("--div-pos");
  const grid = h("div", { class: "heat", style: { gridTemplateColumns: `56px repeat(${n}, minmax(0, 1fr))` }, role: "table", "aria-label": title });
  grid.append(h("div", { class: "heat-corner" }));
  for (const l of labels) grid.append(h("div", { class: "heat-col", role: "columnheader" }, l));
  matrix.forEach((row, i) => {
    grid.append(h("div", { class: "heat-row", role: "rowheader" }, labels[i]));
    row.forEach((v, j) => {
      const fill = i === j ? cssVar("--surface-2") : diverge(v, neg, mid, pos);
      const cell = h("div", { class: "heat-cell", role: "cell", tabindex: 0, style: { background: fill, color: i === j ? cssVar("--muted") : inkOn(fill) } },
        i === j ? "—" : fmt(v));
      const show = (ev) => tip(true, ev.clientX ?? cell.getBoundingClientRect().right, ev.clientY ?? cell.getBoundingClientRect().top,
        [{ value: fmt(v), label: title }], `${labels[i]} × ${labels[j]}`);
      cell.addEventListener("pointermove", show);
      cell.addEventListener("focus", show);
      cell.addEventListener("pointerleave", () => tip(false));
      cell.addEventListener("blur", () => tip(false));
      grid.append(cell);
    });
  });
  const legend = h("div", { class: "heat-legend" },
    h("span", {}, "−1 反向"),
    h("span", { class: "heat-scale", style: { background: `linear-gradient(90deg, ${neg}, ${mid}, ${pos})` } }),
    h("span", {}, "+1 同涨同跌"));
  container.replaceChildren(grid, legend);
}

// ─────────────────────────────── dumbbell (weight → risk share) ───────────────────────────────
export function dumbbell(container, rows, { aName = "仓位", bName = "风险贡献", max = null, fmt = (v) => `${v.toFixed(0)}%` } = {}) {
  container.classList.add("viz");
  const mx = max ?? Math.max(10, ...rows.flatMap((r) => [r.a, r.b].filter(isNum))) * 1.08;
  const list = h("div", { class: "dumbbell" });
  for (const r of rows) {
    const pa = clamp((r.a / mx) * 100, 0, 100), pb = clamp(((r.b ?? 0) / mx) * 100, 0, 100);
    const lo = Math.min(pa, pb), hiP = Math.max(pa, pb);
    const worse = (r.b ?? 0) > r.a;
    const track = h("div", { class: "db-track" },
      h("span", { class: `db-bar ${worse ? "worse" : "better"}`, style: { left: `${lo}%`, width: `${hiP - lo}%` } }),
      h("span", { class: "db-a", style: { left: `${pa}%` }, title: `${aName} ${fmt(r.a)}` }),
      isNum(r.b) ? h("span", { class: `db-b ${worse ? "worse" : "better"}`, style: { left: `${pb}%` }, title: `${bName} ${fmt(r.b)}` }) : null);
    list.append(h("div", { class: "db-row", tabindex: 0 },
      h("span", { class: "db-label" }, r.label),
      track,
      h("span", { class: "db-val" }, h("span", { class: "muted" }, fmt(r.a)), " → ",
        h("b", { class: worse ? "db-worse" : "" }, isNum(r.b) ? fmt(r.b) : "—"))));
  }
  const legend = h("div", { class: "db-legend" },
    h("span", {}, h("i", { class: "db-a static" }), aName),
    h("span", {}, h("i", { class: "db-b worse static" }), `${bName}(高于仓位)`),
    h("span", {}, h("i", { class: "db-b better static" }), `${bName}(低于仓位)`));
  container.replaceChildren(list, legend);
}

// ─────────────────────────────── horizontal bars ───────────────────────────────
export function hbars(container, rows, { fmt = (v) => `${v.toFixed(1)}%`, max = null, colorOf = null } = {}) {
  const mx = max ?? Math.max(1e-9, ...rows.map((r) => Math.abs(r.value)));
  const list = h("div", { class: "hbars" });
  for (const r of rows) {
    const w = clamp((Math.abs(r.value) / mx) * 100, 0, 100);
    list.append(h("div", { class: "hb-row", title: r.title || "" },
      h("span", { class: "hb-label" }, r.label),
      h("span", { class: "hb-track" }, h("span", { class: `hb-fill ${r.cls || ""}`, style: { width: `${w}%`, background: colorOf ? colorOf(r) : null } })),
      h("span", { class: "hb-val" }, fmt(r.value))));
  }
  container.replaceChildren(list);
}

// ─────────────────────────────── squarified treemap ───────────────────────────────
function squarify(items, x, y, w, hgt) {
  const out = [];
  const total = items.reduce((s, it) => s + it.value, 0);
  if (!total || w <= 0 || hgt <= 0) return out;
  const scale = (w * hgt) / total;
  let rest = items.map((it) => ({ ...it, area: it.value * scale }));
  let rx = x, ry = y, rw = w, rh = hgt;
  const worst = (row, len) => {
    const s = row.reduce((a, r) => a + r.area, 0);
    const mx = Math.max(...row.map((r) => r.area)), mn = Math.min(...row.map((r) => r.area));
    return Math.max((len * len * mx) / (s * s), (s * s) / (len * len * mn));
  };
  while (rest.length) {
    const len = Math.min(rw, rh);
    const row = [rest[0]];
    let i = 1;
    while (i < rest.length && worst([...row, rest[i]], len) <= worst(row, len)) { row.push(rest[i]); i++; }
    rest = rest.slice(i);
    const s = row.reduce((a, r) => a + r.area, 0);
    if (rw >= rh) {
      const cw = s / rh;
      let cy = ry;
      for (const r of row) { const ch = r.area / cw; out.push({ ...r, x: rx, y: cy, w: cw, h: ch }); cy += ch; }
      rx += cw; rw -= cw;
    } else {
      const ch = s / rw;
      let cx = rx;
      for (const r of row) { const cw = r.area / ch; out.push({ ...r, x: cx, y: ry, w: cw, h: ch }); cx += cw; }
      ry += ch; rh -= ch;
    }
  }
  return out;
}

/** items: [{key, label, sub, value, color, tipRows, tipTitle}] */
export function treemap(container, items, { onClick = null } = {}) {
  container.classList.add("viz", "treemap");
  const draw = () => {
    const W = container.clientWidth, H = container.clientHeight;
    if (W < 20 || H < 20) return;
    const rects = squarify([...items].filter((i) => i.value > 0).sort((a, b) => b.value - a.value), 0, 0, W, H);
    container.replaceChildren(...rects.map((r) => {
      const gap = 2;
      const big = r.w > 64 && r.h > 38, mid = r.w > 40 && r.h > 24;
      const el = h("button", {
        class: `tm-cell${big ? " big" : mid ? " mid" : " tiny"}`, type: "button",
        style: { left: `${r.x + gap / 2}px`, top: `${r.y + gap / 2}px`, width: `${Math.max(0, r.w - gap)}px`,
          height: `${Math.max(0, r.h - gap)}px`, background: r.color, color: inkOn(r.color) },
        "aria-label": `${r.label} ${r.sub || ""}`,
      },
      mid ? h("span", { class: "tm-label" }, r.label) : null,
      big || (mid && r.h > 34) ? h("span", { class: "tm-sub" }, r.sub || "") : null);
      el.addEventListener("pointermove", (ev) => tip(true, ev.clientX, ev.clientY, r.tipRows || [], r.tipTitle || r.label));
      el.addEventListener("pointerleave", () => tip(false));
      if (onClick) el.addEventListener("click", () => { tip(false); onClick(r); });
      return el;
    }));
  };
  draw();
  observe(container, draw);
  return { redraw: draw };
}

export { svg, esc };
