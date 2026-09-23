#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
观澜技术触发监控  ·  tech_monitor.py
================================================================
盯住配置好的股票的技术触发条件，一旦"图破了"（出现转向信号）就往飞书推送。

数据来源（自动择优，不依赖会冷启动卡死的 yfinance）：
  1) GET /api/tech            —— 若服务端已重启、带了该接口，用它（最新、含日线序列）
  2) 退回：/tmp 指标缓存 + /api/snapshot 实时价
     · MA20/50/200 来自缓存（日线均线变化慢，几小时内基本不变）
     · 收盘价用 /api/snapshot 的实时价覆盖 → "跌破 MA" 类信号是实时、准确的
     · MACD/RSI 用缓存值（观澜面板刷新时更新）
检测方式：**run-over-run（每次运行对比上次）** 的边沿触发 + 冷却，只在"刚破"那一刻推一次。

用法
  python3 tech_monitor.py              # 跑一次（cron / launchd 用）
  python3 tech_monitor.py --status     # 只看当前指标 + 各触发器状态，不推送、不改状态
  python3 tech_monitor.py --loop 1800  # 前台每 30 分钟一次（测试）
  python3 tech_monitor.py --test-feishu# 发一条测试消息

飞书配置：同目录 .env 里 FEISHU_WEBHOOK=...（开了签名校验再加 FEISHU_SECRET=...）
"""
import argparse, base64, hashlib, hmac, json, os, sys, time, urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "monitor_state.json"
MARKET_DIR = Path("/tmp/t212-codex-workspace/data/market")
API_BASE = "http://localhost:8787"
COOLDOWN = 4 * 3600   # 同一触发器 4 小时内不重复推（防止价格在均线附近来回抖造成刷屏）

# ───────────────────────── 监控配置 ─────────────────────────
# 标准规则集（所有票通用）— 想个性化可以单独定义
DEFAULT_RULES = [
    "below_ma20",         # 跌破 MA20（结构破位）
    "below_ma50",         # 跌破 MA50（中期支撑破）
    "recover_ma20",       # 收复 MA20（修复信号）
    "macd_death_cross",   # MACD 死叉
    "macd_golden_cross",  # MACD 金叉（修复）
    "rsi_lose_70",        # RSI 跌破 70（降温）
    "rsi_oversold",       # RSI < 30（超卖反弹机会）
    "down_on_volume",     # 放量下跌
    "day_drop_5",         # 单日 ≤ −5%
    "day_rip_5",          # 单日 ≥ +5%
]
WATCH = {
    # 改成你自己想盯的持仓（T212 代码 → 显示名）
    "NVDA_US_EQ": {"name": "NVDA", "vol_mult": 1.5, "rules": DEFAULT_RULES},
    "AAPL_US_EQ": {"name": "AAPL", "vol_mult": 1.5, "rules": DEFAULT_RULES},
}


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_env():
    env = {}
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("FEISHU_WEBHOOK", "FEISHU_SECRET"):
        if os.getenv(k):
            env[k] = os.getenv(k)
    return env


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ───────────────────────── 飞书推送 ─────────────────────────
def feishu_sign(timestamp, secret):
    return base64.b64encode(
        hmac.new(f"{timestamp}\n{secret}".encode("utf-8"), b"", hashlib.sha256).digest()
    ).decode("utf-8")


def feishu_send(title, lines, env):
    webhook = (env.get("FEISHU_WEBHOOK") or "").strip()
    if not webhook:
        log("⚠️ 未配置 FEISHU_WEBHOOK，跳过推送（内容见下）")
        print("----\n" + title + "\n" + "\n".join(lines) + "\n----", flush=True)
        return False
    payload = {"msg_type": "text", "content": {"text": title + "\n\n" + "\n".join(lines)}}
    secret = (env.get("FEISHU_SECRET") or "").strip()
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = feishu_sign(ts, secret)
    req = urllib.request.Request(webhook, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.load(r)
        ok = resp.get("StatusCode") == 0 or resp.get("code") == 0
        log(f"飞书推送 {'成功 ✓' if ok else '返回异常: ' + json.dumps(resp, ensure_ascii=False)}")
        return ok
    except Exception as e:
        log(f"飞书推送失败: {e}")
        return False


# ───────────────────────── 取数（择优 + 兜底） ─────────────────────────
def _http_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _snapshot_price(ticker):
    try:
        snap = _http_json(f"{API_BASE}/api/snapshot", timeout=15)
        for p in (snap.get("positions") or []):
            if p.get("ticker") == ticker:
                return p.get("currentPrice"), p.get("dayChangePct")
    except Exception:
        pass
    return None, None


def get_today(ticker):
    """返回 (t, source, age_min)：t 是今天的指标平铺字典；source ∈ api/cache/none。"""
    # 1) /api/tech（服务端重启后才有）
    try:
        d = _http_json(f"{API_BASE}/api/tech?ticker={ticker}", timeout=60)
        if not d.get("error") and d.get("today"):
            t = dict(d["today"])
            bb = d.get("bollinger") or {}
            t.update({"percent_b": bb.get("percent_b"), "bandwidth": bb.get("bandwidth"),
                      "last_vol": d.get("last_vol"), "avg_vol20": d.get("avg_vol20"),
                      "high_20d": d.get("high_20d"), "low_20d": d.get("low_20d"),
                      "prev_close": (d.get("prev") or {}).get("close"),
                      "closes_tail": d.get("closes_tail"), "macd_tail": d.get("macd_tail"),
                      "ticker": d.get("ticker")})
            return t, "api", 0
    except Exception:
        pass
    # 2) 兜底：/tmp 缓存指标 + 实时价覆盖收盘
    f = MARKET_DIR / f"{ticker}.json"
    if not f.exists():
        return None, "none", None
    try:
        m = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None, "none", None
    ind = m.get("indicators") or {}
    bars = m.get("recent_bars_30") or []
    vols = [b.get("v") or 0 for b in bars]
    avg_vol20 = round(sum(vols[-21:-1]) / 20, 0) if len(vols) >= 21 else None
    prev_close = bars[-2]["c"] if len(bars) >= 2 else m.get("last_close")
    live_px, _ = _snapshot_price(ticker)
    ranges = ind.get("ranges") or {}
    bb = ind.get("bollinger") or {}
    age_min = int((time.time() - f.stat().st_mtime) / 60)
    t = {
        "ticker": ticker.split("_")[0],
        "close": live_px if isinstance(live_px, (int, float)) else m.get("last_close"),
        "ma20": ind.get("ma20"), "ma50": ind.get("ma50"), "ma200": ind.get("ma200"),
        "rsi": ind.get("rsi14"), "macd": ind.get("macd"),
        "signal": ind.get("macd_signal"), "hist": ind.get("macd_hist"),
        "percent_b": bb.get("percent_b"), "bandwidth": bb.get("bandwidth"),
        "last_vol": m.get("last_volume"), "avg_vol20": avg_vol20, "prev_close": prev_close,
        "high_20d": ranges.get("high_20d"), "low_20d": ranges.get("low_20d"),
        "closes_tail": None, "macd_tail": None,
    }
    return t, "cache", age_min


# ───────────────────────── 触发器规则（返回 {cond,title,detail}） ─────────────────────────
def _num(*xs):
    return all(isinstance(x, (int, float)) for x in xs)


def r_macd_death_cross(t, cfg):
    if not _num(t.get("macd"), t.get("signal")):
        return None
    return {"cond": t["macd"] < t["signal"], "title": "MACD 死叉",
            "detail": f"MACD 死叉：蓝线 {t['macd']:.2f} 在信号线 {t['signal']:.2f} 下方（上涨动能转向）"}


def r_below_ma20(t, cfg):
    if not _num(t.get("close"), t.get("ma20")):
        return None
    return {"cond": t["close"] < t["ma20"], "title": "跌破 MA20",
            "detail": f"跌破 MA20：现价 ${t['close']:.2f} < MA20 ${t['ma20']:.2f}（上升结构破位）"}


def r_rsi_lose_70(t, cfg):
    if not _num(t.get("rsi")):
        return None
    return {"cond": t["rsi"] < 70, "title": "RSI 跌破 70",
            "detail": f"RSI 回落到 {t['rsi']:.1f}（< 70，超买降温）"}


def r_down_on_volume(t, cfg):
    if not _num(t.get("close"), t.get("prev_close"), t.get("last_vol"), t.get("avg_vol20")) or not t["avg_vol20"]:
        return None
    mult = cfg.get("vol_mult", 1.5)
    chg = (t["close"] / t["prev_close"] - 1) * 100 if t["prev_close"] else 0
    return {"cond": (t["close"] < t["prev_close"]) and (t["last_vol"] > mult * t["avg_vol20"]),
            "title": "放量下跌",
            "detail": f"放量下跌：{chg:+.1f}%，量 {t['last_vol']/1e6:.1f}M > {mult}×均量 {t['avg_vol20']/1e6:.1f}M"}


def _diverge(closes, macd):
    if not closes or not macd or len(closes) < 25:
        return False
    if closes[-1] < max(closes[-10:]) * 0.97:
        return False
    recent = closes[-6:]
    ri = len(closes) - 6 + recent.index(max(recent))
    prior = closes[-45:-6] if len(closes) >= 45 else closes[:-6]
    if not prior:
        return False
    pi = ((len(closes) - 45) if len(closes) >= 45 else 0) + prior.index(max(prior))
    if macd[ri] is None or macd[pi] is None:
        return False
    return closes[ri] >= closes[pi] * 0.995 and macd[ri] < macd[pi]


def r_bearish_divergence(t, cfg):
    ct, mt = t.get("closes_tail"), t.get("macd_tail")
    if not ct or not mt:   # 仅在有日线序列（/api/tech）时才算；缓存模式跳过
        return None
    return {"cond": _diverge(ct, mt), "title": "顶背离",
            "detail": "潜在顶背离：价逼近/创新高但 MACD 驼峰更矮（动能没跟上，建议人工确认）"}


# ---- 新增规则 ----
def r_below_ma50(t, cfg):
    if not _num(t.get("close"), t.get("ma50")):
        return None
    return {"cond": t["close"] < t["ma50"], "title": "跌破 MA50",
            "detail": f"跌破 MA50：${t['close']:.2f} < ${t['ma50']:.2f}（中期支撑破）"}


def r_recover_ma20(t, cfg):
    if not _num(t.get("close"), t.get("ma20")):
        return None
    return {"cond": t["close"] >= t["ma20"], "title": "收复 MA20",
            "detail": f"收复 MA20：${t['close']:.2f} ≥ ${t['ma20']:.2f}（结构修复信号）"}


def r_macd_golden_cross(t, cfg):
    if not _num(t.get("macd"), t.get("signal")):
        return None
    return {"cond": t["macd"] > t["signal"], "title": "MACD 金叉",
            "detail": f"MACD 金叉：蓝线 {t['macd']:.2f} > 信号 {t['signal']:.2f}（动能转正）"}


def r_rsi_oversold(t, cfg):
    if not _num(t.get("rsi")):
        return None
    return {"cond": t["rsi"] < 30, "title": "RSI 超卖",
            "detail": f"RSI {t['rsi']:.0f} < 30（极度超卖，反弹机会窗口）"}


def r_day_drop_5(t, cfg):
    if not _num(t.get("close"), t.get("prev_close")) or not t["prev_close"]:
        return None
    chg = (t["close"]/t["prev_close"] - 1) * 100
    return {"cond": chg <= -5, "title": "单日大跌",
            "detail": f"单日 {chg:+.1f}%（≤ −5%，触发深杀阈值）"}


def r_day_rip_5(t, cfg):
    if not _num(t.get("close"), t.get("prev_close")) or not t["prev_close"]:
        return None
    chg = (t["close"]/t["prev_close"] - 1) * 100
    return {"cond": chg >= 5, "title": "单日大涨",
            "detail": f"单日 {chg:+.1f}%（≥ +5%，触发深弹阈值）"}


RULES = {
    "macd_death_cross": r_macd_death_cross,
    "macd_golden_cross": r_macd_golden_cross,
    "below_ma20": r_below_ma20,
    "below_ma50": r_below_ma50,
    "recover_ma20": r_recover_ma20,
    "rsi_lose_70": r_rsi_lose_70,
    "rsi_oversold": r_rsi_oversold,
    "down_on_volume": r_down_on_volume,
    "day_drop_5": r_day_drop_5,
    "day_rip_5": r_day_rip_5,
    "bearish_divergence": r_bearish_divergence,
}


# ───────────────────────── 核心 ─────────────────────────
def build_message(short, cfg, fired, t, source, age_min):
    title = f"🔴 {cfg['name']} 技术触发 · {len(fired)} 项"
    lines = [f"• {ev['detail']}" for ev in fired]
    lines.append("")
    def n(x, f="{:.2f}"):
        return f.format(x) if isinstance(x, (int, float)) else "?"
    lines.append(f"现价 ${n(t.get('close'))}   MA20 ${n(t.get('ma20'))}   RSI {n(t.get('rsi'),'{:.0f}')}")
    if _num(t.get("macd"), t.get("signal")):
        lines.append(f"MACD {n(t.get('macd'),'{:.1f}')}/{n(t.get('signal'),'{:.1f}')}   布林%B {t.get('percent_b')}")
    stale = "" if source == "api" else f"（指标取自缓存，约 {age_min} 分钟前；价为实时）"
    lines.append(f"⏰ {datetime.now():%Y-%m-%d %H:%M}{stale}")
    lines.append("—— 观澜技术监控")
    return title, lines


def check_once(send=True):
    env = load_env()
    state = load_state()
    now = int(time.time())
    fired_any = False
    for t212, cfg in WATCH.items():
        t, source, age = get_today(t212)
        if not t:
            log(f"{cfg['name']}: 暂无数据（缓存缺失且 /api/tech 不可用）")
            continue
        short = t.get("ticker") or t212.split("_")[0]
        tstate = state.setdefault(short, {})
        fired, armed = [], []
        for rule in cfg["rules"]:
            ev = RULES[rule](t, cfg)
            if not ev:
                continue
            rs = tstate.get(rule)
            cond = ev["cond"]
            if rs is None:                       # 首见：播种，不推（避免把"早就成立"的状态当新信号）
                tstate[rule] = {"active": cond, "fired_at": 0}
            else:
                if cond and not rs["active"] and (now - rs.get("fired_at", 0) > COOLDOWN):
                    fired.append(ev)
                    rs["fired_at"] = now
                rs["active"] = cond
            armed.append(f"{rule}={'🔴' if cond else '·'}")
        px = t.get("close")
        log(f"{cfg['name']}  ${px:.2f} [{source}{'/'+str(age)+'min' if source=='cache' else ''}]  {'  '.join(armed)}"
            if isinstance(px, (int, float)) else f"{cfg['name']} [{source}] {'  '.join(armed)}")
        if fired:
            fired_any = True
            title, lines = build_message(short, cfg, fired, t, source, age)
            if send:
                feishu_send(title, lines, env)
            else:
                print("----\n" + title + "\n" + "\n".join(lines) + "\n----", flush=True)
    save_state(state)
    if not fired_any:
        log("本轮无新触发。")


def status():
    env = load_env()
    log(f"FEISHU_WEBHOOK: {'已配 ✓' if env.get('FEISHU_WEBHOOK') else '未配 ✗'}")
    for t212, cfg in WATCH.items():
        t, source, age = get_today(t212)
        if not t:
            log(f"{cfg['name']}: 无数据"); continue
        def n(x, f="{:.2f}"):
            return f.format(x) if isinstance(x, (int, float)) else "?"
        src = source + (f"（缓存 {age} 分钟前）" if source == "cache" else "")
        print(f"\n=== {cfg['name']}  现价 ${n(t.get('close'))}  [数据源 {src}] ===", flush=True)
        print(f"  MA20 {n(t.get('ma20'))} / MA50 {n(t.get('ma50'))} / MA200 {n(t.get('ma200'))}"
              f"   RSI {n(t.get('rsi'),'{:.1f}')}   MACD {n(t.get('macd'),'{:.1f}')}/{n(t.get('signal'),'{:.1f}')}"
              f"   布林%B {t.get('percent_b')}", flush=True)
        for rule in cfg["rules"]:
            ev = RULES[rule](t, cfg)
            if not ev:
                print(f"    - {rule:<18} （数据不足，跳过）", flush=True); continue
            print(f"    - {ev['title']:<10} {'🔴 触发中' if ev['cond'] else '· 未触发'}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="观澜技术触发监控")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=int, metavar="SECONDS")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--test-feishu", action="store_true")
    args = ap.parse_args()
    if args.test_feishu:
        feishu_send("✅ 观澜技术监控 · 测试消息",
                    ["如果你看到这条，说明飞书 webhook 配好了。",
                     f"⏰ {datetime.now():%Y-%m-%d %H:%M:%S}"], load_env())
        return
    if args.status:
        status(); return
    if args.loop:
        log(f"循环模式，每 {args.loop}s 一次（Ctrl+C 退出）")
        while True:
            try:
                check_once(send=True)
            except Exception as e:
                log(f"本轮异常: {e}")
            time.sleep(args.loop)
        return
    check_once(send=True)


if __name__ == "__main__":
    main()
