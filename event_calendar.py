#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
观澜「6 月关键事件日历」  ·  event_calendar.py
================================================================
事件来源：同目录 events.json（手工维护，可加可改）
两种推送：
  --morning (默认 09:00 UK)：今日 + 明日的事件清单（"今天等什么数据/财报/会议"）
  --postevent SEC（默认每小时 :05）：事件结束 ~30min 后，推一条
       "事件刚发生，组合实时反应 + 该盯什么"
"""
import argparse, json, os, re, shutil, subprocess, time, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tech_monitor import feishu_send, load_env, API_BASE

# ── codex 调用（同 deep_analysis 架构） ──
_CODEX_BIN = shutil.which("codex") or os.path.expanduser("~/.local/bin/codex")
_CODEX_CMD = [_CODEX_BIN, "exec", "--skip-git-repo-check"]
_CODEX_TIMEOUT = 90
_NOISE = ("•", "└", "⚠", "✔", "◦", "─", "╭", "│", "╰", ">_ OpenAI Codex",
          "Tip:", "  Tip:", "▌", "workdir:", "provider:", "session id:", "model:")


def _codex_env():
    env = dict(os.environ)
    env["TERM"] = "dumb"; env["NO_COLOR"] = "1"
    env["PATH"] = os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "/usr/bin:/bin")
    if not env.get("CODEX_LB_API_KEY"):
        try:
            for line in open(os.path.expanduser("~/.zshrc"), encoding="utf-8", errors="ignore"):
                s = line.strip()
                if s.startswith("#") or "CODEX_LB_API_KEY=" not in s:
                    continue
                env["CODEX_LB_API_KEY"] = s.split("CODEX_LB_API_KEY=", 1)[1].strip().strip('"').strip("'").split()[0]
                break
        except Exception:
            pass
    return env


def _run_codex(prompt: str) -> str:
    try:
        r = subprocess.run(_CODEX_CMD + [prompt], env=_codex_env(),
                           capture_output=True, text=True, timeout=_CODEX_TIMEOUT)
        raw = r.stdout or r.stderr or ""
        lines = raw.splitlines()
        out, capturing = [], False
        for ln in lines:
            s = ln.strip()
            if s in ("codex", "assistant"):
                capturing = True; out = []; continue
            if s in ("user", "exec", "thinking", "tool", "tool_use"):
                capturing = False; continue
            if s.startswith("tokens used") or re.match(r"^\[20\d\d-", s):
                capturing = False; continue
            if capturing and not any(s.startswith(p) for p in _NOISE):
                out.append(ln)
        text = "\n".join(out).strip()
        if len(text) < 20:
            text = "\n".join(l for l in lines
                             if not any(l.strip().startswith(p) for p in _NOISE)).strip()
        return text
    except subprocess.TimeoutExpired:
        return f"（搜索超时 {_CODEX_TIMEOUT}s）"
    except Exception as e:
        return f"（搜索失败：{e}）"


def search_event_result(event) -> str:
    """用 codex 联网搜事件实际结果，返回 2-3 句中文结论。"""
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = (
        f"今天 {today}，{event['title']} 刚刚发布/发生。"
        f"请联网搜索最新结果：实际公布的数字/结论是什么？和市场预期相比是高了还是低了？"
        f"市场初始反应如何（涨/跌/震荡）？"
        f"只给我 2-3 句中文结论，要有具体数字，不要废话。"
        f"背景参考：{event['desc']}"
    )
    return _run_codex(prompt)

EVENTS = HERE / "events.json"
STATE = HERE / "event_state.json"
PORTFOLIO_WATCH = ("NVDA_US_EQ", "AAPL_US_EQ", "MSFT_US_EQ")   # 改成你的持仓

# 事件后推送里显示的持仓
DISPLAY = (("NVDA_US_EQ", "NVDA"), ("AAPL_US_EQ", "AAPL"), ("MSFT_US_EQ", "MSFT"))

# 你当前挂着的关键单子——事件推送顺带提醒"到没到价"（示例；改单后同步这里即可）
ORDER_LEVELS = {
    "NVDA_US_EQ": {"name": "NVDA", "side": "buy",  "level": 100, "note": "示例：加仓位"},
    "AAPL_US_EQ": {"name": "AAPL", "side": "sell", "level": 300, "note": "示例：止盈位"},
}


def log(m):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}", flush=True)


def load_events():
    return json.loads(EVENTS.read_text(encoding="utf-8")) if EVENTS.exists() else []


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def to_dt(ev):
    return datetime.strptime(f"{ev['date']} {ev['time']}", "%Y-%m-%d %H:%M")


def snap_quotes(tickers):
    out = {}
    try:
        with urllib.request.urlopen(f"{API_BASE}/api/snapshot", timeout=10) as r:
            for p in (json.load(r).get("positions") or []):
                if p["ticker"] in tickers:
                    out[p["ticker"]] = (p["currentPrice"], p.get("dayChangePct", 0))
    except Exception:
        pass
    return out


def morning_brief(send=True):
    now = datetime.now()
    today, tomorrow = now.date(), (now + timedelta(days=1)).date()
    # 每天只发一次
    state = load_state()
    if state.get("morning_sent") == str(today):
        log(f"早报今日已发（{today}）→ 跳过")
        return
    evs = load_events()
    today_evs = [e for e in evs if to_dt(e).date() == today]
    tomo_evs  = [e for e in evs if to_dt(e).date() == tomorrow]
    # 用户要求"没事就别发"：今明都没有事件 → 静默退出
    if not today_evs and not tomo_evs:
        log("今明无事件 → 静默，不推送")
        return
    lines = []
    if today_evs:
        lines.append(f"📅 今天（{today}）：")
        for e in sorted(today_evs, key=to_dt):
            lines.append(f"  ⏰ {e['time']} UK · {e['title']}")
            lines.append(f"     {e['desc']}")
    if tomo_evs:
        if lines: lines.append("")
        lines.append(f"📅 明天（{tomorrow}）：")
        for e in sorted(tomo_evs, key=to_dt):
            lines.append(f"  ⏰ {e['time']} UK · {e['title']}")
            lines.append(f"     {e['desc']}")
    lines += ["", "—— 关键事件日历"]
    title = f"☕ 早报 · {today}（事件日历）"
    if send:
        feishu_send(title, lines, load_env())
        state["morning_sent"] = str(today)
        save_state(state)
    else:
        print(title + "\n" + "\n".join(lines))


def _build_post_lines(e, delta, quotes, search_result=""):
    """拼一条'事件刚发生'的推送：实际结果 + 持仓反应 + 你挂单到没到价。"""
    lines = [f"⏰ {e['time']} UK · {e['title']}（刚发生 ~{int(delta)} 分钟）", ""]
    lines.append(e["desc"]); lines.append("")
    if search_result:
        lines.append("📋 实际结果（网搜）：")
        for ln in search_result.strip().splitlines():
            lines.append(f"  {ln.strip()}" if ln.strip() else "")
        lines.append("")
    lines.append("你的持仓实时反应：")
    for tk, name in DISPLAY:
        if tk in quotes:
            px, dp = quotes[tk]
            lines.append(f"  {name:<6} ${px:.2f}  {dp:+.2f}%")
    # 顺带提醒你挂的单到没到价
    lvl = []
    for tk, cfg in ORDER_LEVELS.items():
        if tk not in quotes:
            continue
        px = quotes[tk][0]; L = cfg["level"]
        if cfg["side"] == "buy":
            if px <= L:
                lvl.append(f"  🎯 {cfg['name']} 已触及{cfg['note']} ${L}（现价 ${px:.2f}）→ 限价买单可能已成交，去确认")
            else:
                lvl.append(f"  {cfg['name']} {cfg['note']} ${L}：现价 ${px:.2f}，还差 {(L/px-1)*100:+.1f}%")
        else:
            if px >= L:
                lvl.append(f"  🎯 {cfg['name']} 已达{cfg['note']} ${L}（现价 ${px:.2f}）→ 卖单可能已成交，去确认")
            else:
                lvl.append(f"  {cfg['name']} {cfg['note']} ${L}：现价 ${px:.2f}，还差 {(L/px-1)*100:+.1f}%")
    if lvl:
        lines += ["", "你的挂单关注位："] + lvl
    lines += ["", "👉 去看盘后真实反应；明早再决定下一步，别熬夜。", "—— 事件刚发生·实时跟踪"]
    return lines


def postevent_check(send=True):
    """每小时 :05 跑：找出 30-95min 前发生的事件，推一条'刚发生 + 持仓反应 + 挂单到没到价'。"""
    now = datetime.now()
    state = load_state(); pushed_recent = []
    for e in load_events():
        et = to_dt(e)
        delta = (now - et).total_seconds() / 60   # 距事件多少分钟
        if 30 <= delta <= 95 and not state.get(e["id"], {}).get("post"):
            log(f"  搜索 {e['title']} 实际结果…")
            search_result = search_event_result(e) if send else ""
            quotes = snap_quotes(PORTFOLIO_WATCH)
            lines = _build_post_lines(e, delta, quotes, search_result)
            title = f"📣 事件 · {e['title']}"
            if send:
                feishu_send(title, lines, load_env())
            else:
                print(title + "\n" + "\n".join(lines))
            state.setdefault(e["id"], {})["post"] = int(time.time())
            pushed_recent.append(e["id"])
    if pushed_recent:
        save_state(state)
        log(f"推送事件后跟踪：{pushed_recent}")
    else:
        log("当前小时无刚结束事件。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--morning", action="store_true", help="早报：今日+明日事件")
    ap.add_argument("--postevent", action="store_true", help="事件后跟踪（建议每小时跑）")
    ap.add_argument("--no-feishu", action="store_true")
    a = ap.parse_args()
    send = not a.no_feishu
    if a.morning:
        morning_brief(send=send)
    elif a.postevent:
        postevent_check(send=send)
    else:
        # 默认：先早报后跟踪（无害幂等）
        morning_brief(send=send); postevent_check(send=send)


if __name__ == "__main__":
    main()
