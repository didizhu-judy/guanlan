#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
观澜「拥挤度/机构出货监控」  ·  crowding_monitor.py
================================================================
专业量化角度盯"机构是不是在悄悄出货"——把 5 个独立信号合成 0-5 分：
  1. 仓位过重     当前仓位 ≥ 25%（你这边的"被动超配"硬阈值）
  2. RSI 持续过热 当前 RSI ≥ 70 且与历史相比仍在高位
  3. 远离 MA200   现价 > MA200 × 2.5（"抛物线"特征）
  4. 累计涨幅极端  从 252日低点 +400%+（"6 个月翻倍"那种）
  5. 量价分布异常 看近 10 日：下跌日成交均量 vs 上涨日 → 比值 > 1.2 = 出货
合计 ≥3 分 = 🔴 高拥挤/机构出货风险；2 分 🟡；≤1 🟢
每天 21:30 跑一次（盘后数据稳定）→ 推飞书；记账历史趋势。

⚠️ 这是 risk signal，不是卖出信号。出现 🔴 意味"该评估"，不是"立刻卖"。
"""
import argparse, json, os, sys, time, urllib.request
from datetime import datetime
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tech_monitor import feishu_send, load_env, API_BASE

MARKET_DIR = Path("/tmp/t212-codex-workspace/data/market")
STATE_FILE = HERE / "crowding_state.json"

WEIGHT_HOT = 0.25       # 仓位 ≥25% 算超配
RSI_HOT = 70            # RSI ≥70 算偏热
MA200_PARABOLIC = 2.5   # 现价 > MA200×2.5 = 抛物线
PCT_FROM_LOW_EXTREME = 400  # 距 52w 低点 +400%+ = 极端涨幅
VOL_DIST_RATIO = 1.2    # 下跌日均量 / 上涨日均量 ≥1.2 = 出货分布


def log(m):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}", flush=True)


def get_snapshot():
    with urllib.request.urlopen(f"{API_BASE}/api/snapshot", timeout=15) as r:
        return json.load(r)


def cache_for(ticker):
    f = MARKET_DIR / f"{ticker}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def vol_distribution(bars):
    """近 10 日：下跌日均量 vs 上涨日均量。"""
    if not bars or len(bars) < 6:
        return None
    recent = bars[-10:]
    ups, downs = [], []
    for i in range(1, len(recent)):
        prev, cur = recent[i-1], recent[i]
        if cur.get("v") is None or cur.get("c") is None or prev.get("c") is None:
            continue
        (ups if cur["c"] >= prev["c"] else downs).append(cur["v"])
    if not ups or not downs:
        return None
    return round(sum(downs)/len(downs) / (sum(ups)/len(ups)), 2)


def score_one(ticker, weight, mkt):
    """返回 (score, signals[list of (label, hit, detail)])."""
    if not mkt:
        return None, [("无数据", False, "缓存缺失")]
    ind = mkt.get("indicators") or {}
    bars = mkt.get("recent_bars_30") or []
    price = mkt.get("last_close")
    ma200 = ind.get("ma200"); rsi = ind.get("rsi14")
    ranges = ind.get("ranges") or {}
    lo252 = ranges.get("low_252d")
    if not isinstance(price, (int, float)):
        return None, [("价格缺失", False, "")]
    sigs = []
    # 1 仓位过重
    sigs.append(("仓位过重", weight >= WEIGHT_HOT, f"{weight*100:.1f}%（阈值 {WEIGHT_HOT*100:.0f}%）"))
    # 2 RSI 偏热
    sigs.append(("RSI偏热", isinstance(rsi, (int, float)) and rsi >= RSI_HOT,
                 f"RSI {rsi}" if isinstance(rsi, (int, float)) else "?"))
    # 3 抛物线（vs MA200 倍数）
    par = (price / ma200) if isinstance(ma200, (int, float)) and ma200 else None
    sigs.append(("抛物线(vs MA200)", isinstance(par, (int, float)) and par >= MA200_PARABOLIC,
                 f"价 {price:.2f} / MA200 {ma200} = {par:.2f}x" if par else "?"))
    # 4 距 52w 低点极端涨幅
    pct_low = ((price / lo252 - 1) * 100) if isinstance(lo252, (int, float)) and lo252 else None
    sigs.append(("极端涨幅(自低点)", isinstance(pct_low, (int, float)) and pct_low >= PCT_FROM_LOW_EXTREME,
                 f"自 ${lo252} 涨 +{pct_low:.0f}%" if pct_low else "?"))
    # 5 量价分布（下跌日放量=出货）
    vr = vol_distribution(bars)
    sigs.append(("出货量分布", isinstance(vr, (int, float)) and vr >= VOL_DIST_RATIO,
                 f"down/up 量比 {vr}" if vr else "?"))
    score = sum(1 for _, hit, _ in sigs if hit)
    return score, sigs


def fmt_signals(sigs):
    return "  ".join(("✓" if h else "·") + l for l, h, _ in sigs)


def run(send=True):
    snap = get_snapshot()
    tot = snap["stats"]["holdingsValue"]
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    today = f"{datetime.now():%Y-%m-%d}"
    # 选 top 8 持仓做监控
    top = sorted(snap["positions"], key=lambda x: -x["marketValue"])[:8]
    rows, hot = [], []
    for p in top:
        tk = p["ticker"]; name = p["name"][:14]
        w = p["marketValue"] / tot
        score, sigs = score_one(tk, w, cache_for(tk))
        if score is None:
            continue
        lights = {3: "🔴", 4: "🔴", 5: "🔴", 2: "🟡", 1: "🟢", 0: "🟢"}
        light = lights.get(score, "🟢")
        rows.append((light, score, name, sigs, w))
        last = state.get(tk, {}).get("score", -1)
        # 历史记录（不写每个细节，省空间）
        state.setdefault(tk, {})["score"] = score
        state[tk].setdefault("hist", []).append({"d": today, "s": score, "rsi": (cache_for(tk) or {}).get("indicators",{}).get("rsi14")})
        state[tk]["hist"] = state[tk]["hist"][-30:]  # 保留最近30天
        if score >= 3:
            hot.append((name, score, sigs, w))
        log(f"{name:<14} {light} {score}/5  仓位{w*100:4.1f}%   {fmt_signals(sigs)}")
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    if send:
        title = f"🎯 拥挤度/机构出货监控 · {today}"
        lines = []
        if hot:
            lines.append("🔴 高拥挤度警告（机构可能出货）：\n")
            for name, score, sigs, w in hot:
                hits = ", ".join(l for l, h, _ in sigs if h)
                lines.append(f"• {name}  {score}/5 · 仓位 {w*100:.1f}%")
                lines.append(f"   触发：{hits}")
            lines.append("")
        lines.append("Top 8 全景：")
        for light, score, name, sigs, w in rows:
            lines.append(f"  {light} {name:<10} {score}/5  仓{w*100:4.1f}%")
        lines += ["", "5 信号：仓位过重 / RSI偏热 / 抛物线 / 自低点极端涨幅 / 出货量分布",
                  "⚠️ 风险评估信号，不是卖出指令"]
        feishu_send(title, lines, load_env())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-feishu", action="store_true")
    a = ap.parse_args()
    run(send=not a.no_feishu)


if __name__ == "__main__":
    main()
