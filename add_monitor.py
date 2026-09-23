#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
观澜「加仓信号灯」  ·  add_monitor.py   (v2: MA20 降级为因子 + 灯变好自动触发深度分析)
================================================================
每 10 分钟、按框架判断你的核心持仓是不是"可以分批加仓"的时机，给红/黄/绿灯。
纯规则、不调 LLM（几乎不费 token）。**不考虑现金**。

—— 为什么是 v2（你提的问题很对）——
v1 把 MA20 当"硬闸门"（跌破一票否决），对周期股可能是反的：你框架说
"周期股盈利见底时技术最烂、往往才是机会"。所以 v2：
  A) MA20 不再一票否决，只是 **6 个因子之一**（washed-out 但在拐头的也能得分）。
  B) 灯一旦从🔴改善到🟡/🟢，**自动触发一次多智能体深度复盘**（deep_analysis），
     把"方向/基本面/周期位置"这层补上——技术灯只当"该做功课了"的扳机。
     （有 2 小时冷却，避免反复触发烧 token。）

6 个因子（逐只，各 +1）：
  1 价≥MA20(短期结构)  2 价≥MA50(中期趋势)  3 当日翻红(≥昨收)
  4 站回昨日高(反弹确认)  5 MACD动能回升(柱≥上次)  6 RSI 健康区且回升(30–68 且≥上次)
得分→灯：≥4 🟢可考虑分批 · 2–3 🟡观察 · ≤1 🔴别加。
软帽：RSI>72(极度过热) → 最多给🟡(别追高)。

推送：任一只🟡/🟢 或 灯变色 → 推；全🔴未变 → 只记日志。PUSH_ALWAYS=True 可改成每次都推。
⚠️ 规则化参考，不构成买卖建议。
"""
import argparse, json, subprocess, sys, time, urllib.request
from datetime import datetime
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tech_monitor import feishu_send, load_env, API_BASE

STATE_FILE = HERE / "add_monitor_state.json"
MARKET_DIR = Path("/tmp/t212-codex-workspace/data/market")
DEEP_PY = HERE / ".venv" / "bin" / "python3"
DEEP_SCRIPT = HERE / "deep_analysis.py"
PUSH_ALWAYS = False
DEEP_COOLDOWN = 2 * 3600   # 灯改善后，2 小时内不重复触发深度分析
RANK = {"🔴": 0, "🟡": 1, "🟢": 2}

WATCH_ADD = {"NVDA_US_EQ": {"name": "NVDA"}, "AAPL_US_EQ": {"name": "AAPL"}}   # 改成你的核心持仓


def log(m):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}", flush=True)


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(s):
    STATE_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def num(*xs):
    return all(isinstance(x, (int, float)) for x in xs)


def get_data(ticker):
    """实时价/昨收 ← /api/snapshot；MA/RSI/MACD/前高 ← /tmp 缓存。"""
    price = prev_close = None
    try:
        with urllib.request.urlopen(f"{API_BASE}/api/snapshot", timeout=15) as r:
            snap = json.load(r)
        for p in (snap.get("positions") or []):
            if p["ticker"] == ticker:
                price, prev_close = p.get("currentPrice"), p.get("dayPrevClose")
                break
    except Exception:
        pass
    f = MARKET_DIR / f"{ticker}.json"
    if not f.exists():
        return None
    try:
        m = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    ind = m.get("indicators") or {}
    bars = m.get("recent_bars_30") or []
    prev_high = bars[-2]["h"] if len(bars) >= 2 else None
    if price is None:
        price = m.get("last_close")
    return {"price": price, "prev_close": prev_close, "prev_high": prev_high,
            "ma20": ind.get("ma20"), "ma50": ind.get("ma50"),
            "rsi": ind.get("rsi14"), "hist": ind.get("macd_hist"),
            "age": int((time.time() - f.stat().st_mtime) / 60)}


def evaluate(d, last_hist, last_rsi):
    """灯由【转向信号】驱动（不是由"在不在均线上方"驱动）；MA 只作背景展示。
    返回 (light, reason, checks, T)。"""
    price, ma20, ma50 = d["price"], d["ma20"], d["ma50"]
    rsi, hist, prev_c, prev_h = d["rsi"], d["hist"], d["prev_close"], d["prev_high"]
    # 结构（背景，不决定灯）
    s20 = num(price, ma20) and price >= ma20
    s50 = num(price, ma50) and price >= ma50
    # 转向四信号（决定灯）；动能/RSI 用严格 ">"（须真在回升，避免与上次持平的假阳）
    t_green   = num(price, prev_c) and price >= prev_c          # 当日翻红
    t_reclaim = num(price, prev_h) and price >= prev_h          # 站回昨日高
    t_macd    = num(hist, last_hist) and hist > last_hist       # MACD 柱回升
    t_rsi     = num(rsi, last_rsi) and 30 <= rsi <= 68 and rsi > last_rsi  # RSI 健康区且回升
    T = t_green + t_reclaim + t_macd + t_rsi
    overheated = num(rsi) and rsi > 72
    if T >= 3 and not overheated:
        light = "🟢"
    elif T == 2:
        light = "🟡"
    else:
        light = "🔴"
    checks = {"≥MA20": s20, "≥MA50": s50,
              "翻红": t_green, "站回昨高": t_reclaim, "动能↑": t_macd, "RSI↑": t_rsi}
    reason = {
        "🟢": f"转向 {T}/4 项确认 → 可考虑小仓分批" + ("（仍偏热，注意）" if overheated else ""),
        "🟡": f"转向 {T}/4 项 → 企稳/修复中，观察",
        "🔴": f"转向 {T}/4 项 → 还没真正转头，别加" + ("（且过热）" if overheated else ""),
    }[light]
    return light, reason, checks, T


def fmt_checks(ch):
    return " ".join(("✓" if v else "✗") + k for k, v in ch.items())


def trigger_deep(ticker):
    """灯改善 → 后台跑一次多智能体深度复盘（它会自己推飞书）。"""
    try:
        logf = open("/tmp/guanlan_addmonitor_deep.log", "a")
        subprocess.Popen(["/usr/bin/python3", str(HERE / "deep_analysis.py"), "--stock", ticker],
                         cwd=str(HERE), stdout=logf, stderr=subprocess.STDOUT,
                         start_new_session=True)
        log(f"🔎 已触发 {ticker} 深度复盘（后台，约1分钟后推飞书）")
        return True
    except Exception as e:
        log(f"触发深度复盘失败 {e}")
        return False


def run(send=True):
    env = load_env()
    state = load_state()
    now = int(time.time())
    rows, lights, changed = [], {}, False
    for tk, cfg in WATCH_ADD.items():
        d = get_data(tk)
        if not d or not num(d.get("price")):
            log(f"{cfg['name']}: 无数据"); continue
        st = state.setdefault(cfg["name"], {})
        prev_light = st.get("light", "🔴")
        light, reason, checks, score = evaluate(d, st.get("hist"), st.get("rsi"))
        # C) 灯改善到 🟡/🟢 → 触发深度分析（带冷却）
        triggered = False
        if RANK[light] > RANK[prev_light] and RANK[light] >= 1 \
                and now - st.get("deep_at", 0) > DEEP_COOLDOWN:
            triggered = trigger_deep(tk)
            if triggered:
                st["deep_at"] = now
        if light != prev_light:
            changed = True
        st.update({"hist": d["hist"], "rsi": d["rsi"], "light": light})
        lights[cfg["name"]] = light
        pxinfo = (f"${d['price']:.2f}  MA20 ${d['ma20']:.2f}/MA50 ${d['ma50']:.2f}  RSI {d['rsi']:.0f}"
                  if num(d.get("ma20"), d.get("ma50"), d.get("rsi")) else f"${d['price']:.2f}")
        rows.append((cfg["name"], light, reason, checks, pxinfo, d["age"], triggered))
        log(f"{cfg['name']} {light} {reason} | {pxinfo} [缓存{d['age']}min]"
            + ("  +触发深度" if triggered else ""))

    actionable = any(v in ("🟡", "🟢") for v in lights.values())
    save_state(state)

    if rows and send and (PUSH_ALWAYS or actionable or changed):
        title = f"🚦 加仓信号灯 · {datetime.now():%m-%d %H:%M}"
        lines = []
        for name, light, reason, checks, pxinfo, age, triggered in rows:
            lines.append(f"{light} {name} — {reason}")
            lines.append(f"    {pxinfo}")
            lines.append(f"    {fmt_checks(checks)}")
            if triggered:
                lines.append("    🔎 灯转好，已自动触发深度复盘，约1分钟后单独推送")
        lines += ["", "—— 加仓监控（规则化，仅参考，不构成买卖建议）"]
        feishu_send(title, lines, env)
    elif rows:
        log("无看点且无变化（全🔴未变）→ 不推送。")


def status():
    env = load_env()
    log(f"FEISHU: {'已配 ✓' if env.get('FEISHU_WEBHOOK') else '未配 ✗'}  推送模式: {'每次都推' if PUSH_ALWAYS else '有看点/变化才推'}")
    state = load_state()
    for tk, cfg in WATCH_ADD.items():
        d = get_data(tk)
        if not d or not num(d.get("price")):
            print(f"{cfg['name']}: 无数据"); continue
        st = state.get(cfg["name"]) or {}
        light, reason, checks, score = evaluate(d, st.get("hist"), st.get("rsi"))
        print(f"\n{light} {cfg['name']}  ${d['price']:.2f}  MA20 ${d['ma20']:.2f} / MA50 ${d['ma50']:.2f}  RSI {d['rsi']:.0f}  (缓存{d['age']}min)")
        print(f"   {reason}")
        print(f"   {fmt_checks(checks)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--loop", type=int, metavar="SECONDS")
    ap.add_argument("--test-feishu", action="store_true")
    a = ap.parse_args()
    if a.test_feishu:
        feishu_send("✅ 加仓信号灯 · 测试", [f"配好了。{datetime.now():%H:%M:%S}"], load_env()); return
    if a.status:
        status(); return
    if a.loop:
        log(f"循环每 {a.loop}s")
        while True:
            try: run(send=True)
            except Exception as e: log(f"异常 {e}")
            time.sleep(a.loop)
        return
    run(send=True)


if __name__ == "__main__":
    main()
