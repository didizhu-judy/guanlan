#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
观澜深度分析  ·  deep_analysis.py
================================================================
参考 TradingAgents（多智能体交易框架）的"精简版"：对每只持仓跑一轮
  分析师团队(基本面/技术/新闻) → 多空各一轮 → 交易员裁决
然后把每只票的结论汇成一份"日报"推到飞书，并把完整报告存档到 reports/。

与 tech_monitor.py 的分工：
  - tech_monitor  = 实时阈值告警（"什么时候动手"，秒级、便宜）
  - deep_analysis = 定期多智能体复盘（"该怎么看、该不该动"，每天盘前/盘中/盘后）

设计
- 标准库 + 直接调用 codex CLI（和观澜服务端同一个引擎），不导入会卡死的 yfinance。
- 数据：持仓走 /api/snapshot(实时)；指标走 /api/tech(没有则退回 /tmp 缓存)；
  新闻走 /tmp 市场缓存；触发告警读 alert_queue.json。
- 每只票 1 次 codex 调用（模型内部扮演各角色）→ 控成本。

用法
  python3 deep_analysis.py --session postclose        # 盘后整轮（默认 auto 按时间判断）
  python3 deep_analysis.py --session premarket
  python3 deep_analysis.py --stock MU_US_EQ           # 只跑一只
  python3 deep_analysis.py --stock MU_US_EQ --dry-run # 只打印喂给 codex 的 prompt，不调用、不推送
  python3 deep_analysis.py --no-feishu                # 跑但不推飞书（终端看结果）

⚠️ 这是分析/复盘工具，不构成买卖建议。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tech_monitor import feishu_send, load_env, API_BASE   # 复用飞书推送

MARKET_DIR = Path("/tmp/t212-codex-workspace/data/market")
WORKSPACE = Path("/tmp/t212-codex-workspace")
REPORTS_DIR = HERE / "reports"
ALERT_QUEUE = HERE / "alert_queue.json"
CODEX_BIN = shutil.which("codex") or os.path.expanduser("~/.local/bin/codex")
CODEX_CMD = [CODEX_BIN, "exec", "--skip-git-repo-check"]
CODEX_TIMEOUT = 240  # 秒/只


def _codex_env():
    """launchd 下没有用户 shell 环境：补 PATH(~/.local/bin) + 从 ~/.zshrc 取 CODEX_LB_API_KEY。"""
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

# ── 盯哪些票 ──
# CORE（盘中每小时跑的两只，密集追踪）；FULL（盘前/盘后跑 top 10，全面复盘）
WATCH_DEEP_CORE = ["NVDA_US_EQ", "AAPL_US_EQ"]          # 改成你的核心持仓
WATCH_DEEP_FULL = ["NVDA_US_EQ", "AAPL_US_EQ", "MSFT_US_EQ", "GOOGL_US_EQ"]   # 盘前/盘后全面复盘

def pick_watchlist(session: str) -> list:
    """盘中每小时 → CORE（节省 token）；盘前/盘后 → FULL（全面复盘）。"""
    # session 进来已经决定好；intraday 的 14:30(开盘) / 21:00(收盘) 也用 FULL
    h = datetime.now().hour
    if session in ("premarket", "postclose"):
        return WATCH_DEEP_FULL
    if session == "intraday" and h in (14, 20):   # 开盘 14:30 / 收盘前 20:30 → FULL
        return WATCH_DEEP_FULL
    return WATCH_DEEP_CORE

SESSION_LABEL = {
    "premarket": "盘前",
    "intraday":  "盘中",
    "postclose": "盘后",
}
SESSION_FOCUS = {
    "premarket": "重点放在【隔夜消息 + 今日开盘前的偏向和关键位】，给出今天该盯的支撑/阻力。",
    "intraday":  "重点放在【盘中相对今晨/关键位的位置、动能有没有变化】，简短即可，别长篇大论。",
    "postclose": "重点放在【今日已确认的日线信号 + 该不该动作】，这是最算数的一轮，给明确裁决。",
}


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def infer_session() -> str:
    """按英国本地时间粗判时段（美股 14:30–21:00 UK）。"""
    h = datetime.now().hour
    if h < 14:
        return "premarket"   # 盘前
    if h < 21:
        return "intraday"    # 盘中（14:30–21:00 UK）
    return "postclose"       # 盘后


# ───────────────────────── 取数 ─────────────────────────
def http_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def get_snapshot():
    try:
        return http_json(f"{API_BASE}/api/snapshot", timeout=30)
    except Exception as e:
        log(f"取 snapshot 失败：{e}")
        return {}


def get_tech(ticker):
    """优先 /api/tech（最新），失败退回 /tmp 市场缓存里的指标。"""
    try:
        d = http_json(f"{API_BASE}/api/tech?ticker={ticker}", timeout=90)
        if not d.get("error"):
            return d, "api"
    except Exception:
        pass
    f = MARKET_DIR / f"{ticker}.json"
    if f.exists():
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
            ind = m.get("indicators") or {}
            return {
                "ticker": ticker.split("_")[0],
                "price": m.get("last_close"),
                "today": {"close": m.get("last_close"), "ma20": ind.get("ma20"),
                          "ma50": ind.get("ma50"), "ma200": ind.get("ma200"),
                          "rsi": ind.get("rsi14"), "macd": ind.get("macd"),
                          "signal": ind.get("macd_signal"), "hist": ind.get("macd_hist")},
                "bollinger": ind.get("bollinger") or {},
                "high_20d": (ind.get("ranges") or {}).get("high_20d"),
                "low_20d": (ind.get("ranges") or {}).get("low_20d"),
                "_news": m.get("news") or [],
                "stale": True,
            }, "cache"
        except Exception:
            pass
    return {}, "none"


def get_news(ticker):
    f = MARKET_DIR / f"{ticker}.json"
    if f.exists():
        try:
            news = json.loads(f.read_text(encoding="utf-8")).get("news") or []
            return news[:5]
        except Exception:
            return []
    return []


def get_alerts(short):
    if not ALERT_QUEUE.exists():
        return []
    try:
        q = json.loads(ALERT_QUEUE.read_text(encoding="utf-8"))
        return [a.get("msg", "") for a in q if a.get("ticker") == short][:5]
    except Exception:
        return []


# ───────────────────────── 组装上下文 + prompt ─────────────────────────
def position_of(snap, ticker):
    for p in (snap.get("positions") or []):
        if p.get("ticker") == ticker:
            return p
    return None


def fmt_position(p):
    if not p:
        return "（未持仓）"
    return (f"{p['quantity']} 股，均价 ${p['averagePrice']:.2f}，现价 ${p['currentPrice']:.2f}，"
            f"市值 £{p['marketValue']:.0f}，累计 {p['pplPct']:+.1f}%（£{p['ppl']:+.0f}），"
            f"今日 {p.get('dayChangePct', 0):+.2f}%")


def fmt_indicators(tech):
    t = tech.get("today") or {}
    bb = tech.get("bollinger") or {}
    def n(x, f="{:.2f}"):
        return f.format(x) if isinstance(x, (int, float)) else "?"
    return (f"现价 ${n(tech.get('price'))}｜MA20 {n(t.get('ma20'))} / MA50 {n(t.get('ma50'))} / MA200 {n(t.get('ma200'))}"
            f"｜RSI {n(t.get('rsi'),'{:.1f}')}｜MACD {n(t.get('macd'),'{:.1f}')}/{n(t.get('signal'),'{:.1f}')}"
            f"(hist {n(t.get('hist'),'{:+.1f}')})｜布林%B {bb.get('percent_b')} BW {bb.get('bandwidth')}"
            f"｜20日 高 {n(tech.get('high_20d'))}/低 {n(tech.get('low_20d'))}"
            + ("｜⚠️指标为缓存(可能不最新)" if tech.get("stale") else ""))


FRAMEWORK = ("基本面定方向、技术面定时点、仓位定结果；周期股盈利见顶时 PE 最低=最危险；"
             "MACD 比'两个驼峰'高度找背离；超买≠马上卖；最站得住的减仓理由是'被动超配再平衡'(风控非择时)；"
             "别把'想锁利润'包装成'估值透支'。")


def build_prompt(name, ticker, session, position, indicators, news, alerts):
    def _hl(h):
        if isinstance(h, dict):
            t = h.get("title") or h.get("headline") or "?"
            pub = h.get("publisher") or h.get("source") or ""
            return f"{t}（{pub}）" if pub else t
        return str(h)
    news_txt = "\n".join(f"  - {_hl(h)}" for h in news) or "  （无近期新闻）"
    alerts_txt = "\n".join(f"  - {a}" for a in alerts) or "  （无）"
    return f"""你是一支专业交易团队，对单只股票做一轮"精简版多智能体"复盘。今天 {datetime.now():%Y-%m-%d}，当前时段：{SESSION_LABEL[session]}。
{SESSION_FOCUS[session]}
严格基于下面给的数据，**不要编造任何数字**。这是分析记录，不构成买卖建议。

【标的】{name}（{ticker}）
【我的持仓】{position}
【技术指标·观澜日线】{indicators}
【近期新闻】
{news_txt}
【近期实时触发告警】
{alerts_txt}
【我的分析框架要点】{FRAMEWORK}

请依次扮演以下角色，每个角色 **2–4 句、简洁**，中文：
1) 📊 基本面分析师：增长/估值/逻辑是否还在？
2) 📈 技术分析师：趋势(MA)、动能(MACD 比驼峰有无背离)、冷热(RSI/布林)、关键支撑阻力。
3) 📰 新闻情绪分析师：近期消息/事件对它整体偏多还是偏空。
4) 🐂 多头研究员：最强的看多理由（一段）。
5) 🐻 空头研究员：最强的看空/风险理由（一段）。
6) 🎯 交易员裁决：综合以上，针对"我这笔持仓"给操作倾向，并按我的框架点明动机属于（逻辑变/估值透支/被动超配/纯情绪）哪一类。

最后**必须**用这一行格式收尾（严格，供程序解析）：
决策: <买入|加仓|持有|减仓|清仓> | 信心: <高|中|低> | 关键位: <一句支撑/阻力> | 一句话: <≤30字结论>
"""


# ───────────────────────── 调 codex ─────────────────────────
NOISE_PREFIX = ("•", "└", "⚠", "✔", "◦", "─", "╭", "│", "╰", ">_ OpenAI Codex",
                "Tip:", "  Tip:", "▌", "workdir:", "provider:", "session id:", "model:")


def extract_codex_answer(raw: str) -> str:
    """从 codex exec 原始输出里抠出 'codex' 角色段的正文。"""
    lines = raw.splitlines()
    out, capturing = [], False
    for ln in lines:
        s = ln.strip()
        if s in ("codex", "assistant"):
            capturing = True
            out = []  # 取最后一段 codex 输出
            continue
        if s in ("user", "exec", "thinking", "tool", "tool_use"):
            capturing = False
            continue
        if s.startswith("tokens used") or s.startswith("[202"):
            capturing = False
            continue
        if capturing:
            if re.match(r"^-{4,}\s*$", s):
                continue
            if any(s.startswith(p) for p in NOISE_PREFIX):
                continue
            out.append(ln)
    text = "\n".join(out).strip()
    if len(text) < 40:  # 解析失败兜底：返回去噪后的整段
        text = "\n".join(l for l in lines
                         if not any(l.strip().startswith(p) for p in NOISE_PREFIX)).strip()
    return text


def run_codex(prompt: str) -> str:
    if not (os.path.exists(CODEX_CMD[0]) or shutil.which("codex")):
        return "[错误] 找不到 codex CLI"
    try:
        r = subprocess.run(
            CODEX_CMD + [prompt],
            cwd=str(WORKSPACE) if WORKSPACE.exists() else None,
            env=_codex_env(),
            capture_output=True, text=True, timeout=CODEX_TIMEOUT,
        )
        return extract_codex_answer(r.stdout or r.stderr or "")
    except subprocess.TimeoutExpired:
        return f"[超时] codex 超过 {CODEX_TIMEOUT}s 未返回"
    except Exception as e:
        return f"[错误] 调用 codex 失败：{e}"


VERDICT_RE = re.compile(r"决策[:：]\s*(\S+).*?信心[:：]\s*(\S+)", re.S)


def parse_verdict(text):
    m = VERDICT_RE.search(text)
    if not m:
        return {"action": "?", "confidence": "?", "line": ""}
    line = text[m.start():].splitlines()[0].strip()
    return {"action": m.group(1).strip(" |"), "confidence": m.group(2).strip(" |"), "line": line}


# ───────────────────────── 主流程 ─────────────────────────
def analyze_one(snap, ticker, session, dry_run=False):
    p = position_of(snap, ticker)
    name = (p or {}).get("name", ticker.split("_")[0])
    short = ticker.split("_")[0]
    tech, src = get_tech(ticker)
    prompt = build_prompt(name, ticker, session, fmt_position(p),
                          fmt_indicators(tech), get_news(ticker), get_alerts(short))
    if dry_run:
        print(f"\n========== {name} ({ticker}) · 数据源={src} ==========\n{prompt}", flush=True)
        return {"ticker": short, "name": name, "report": "(dry-run)", "verdict": {}}
    log(f"  分析 {name} …（数据源 {src}）")
    report = run_codex(prompt)
    return {"ticker": short, "name": name, "report": report, "verdict": parse_verdict(report)}


def assemble_and_send(results, session, send=True):
    date = f"{datetime.now():%Y-%m-%d}"
    label = SESSION_LABEL[session]
    # 组合速览：统计裁决
    tally = {}
    for r in results:
        a = r["verdict"].get("action", "?")
        tally[a] = tally.get(a, 0) + 1
    tally_str = "  ".join(f"{k}×{v}" for k, v in tally.items()) or "（无）"

    title = f"📊 观澜深度分析 · {label} · {date}"
    summary_lines = [f"组合裁决速览：{tally_str}", ""] + [
        f"• {r['ticker']} {r['verdict'].get('action','?')}({r['verdict'].get('confidence','?')})" for r in results
    ]

    # 存档完整报告
    REPORTS_DIR.mkdir(exist_ok=True)
    md = [f"# 观澜深度分析 · {label} · {date}\n", f"> 组合裁决速览：{tally_str}\n", "> 多智能体复盘，分析记录，不构成买卖建议。\n"]
    for r in results:
        md.append(f"\n---\n\n## {r['name']}（{r['ticker']}）\n\n{r['report']}\n")
    report_path = REPORTS_DIR / f"{date}_{session}.md"
    report_path.write_text("\n".join(md), encoding="utf-8")
    log(f"报告已存档：{report_path}")

    if send:
        env = load_env()
        # 概览一条 + 每只【完整 6 角色分析】各一条（详细版）
        feishu_send(title, summary_lines + ["", "（每只详细分析见下条 ↓）"], env)
        for r in results:
            v = r["verdict"]
            feishu_send(f"📊 {r['name']}（{r['ticker']}）· {v.get('action','?')}({v.get('confidence','?')})",
                        [r["report"].strip(), "", "—— 观澜深度分析，不构成买卖建议"], env)
    else:
        print("\n" + title + "\n" + "\n".join(summary_lines), flush=True)
        for r in results:
            print(f"\n=== {r['name']} ===\n{r['report']}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="观澜深度分析（多智能体定期复盘）")
    ap.add_argument("--session", choices=["premarket", "intraday", "postclose", "auto"], default="auto")
    ap.add_argument("--stock", help="只分析一只（如 MU_US_EQ）")
    ap.add_argument("--dry-run", action="store_true", help="只打印 prompt，不调用 codex、不推送")
    ap.add_argument("--no-feishu", action="store_true", help="跑但不推飞书")
    args = ap.parse_args()

    session = infer_session() if args.session == "auto" else args.session
    if args.session == "auto" and datetime.now().weekday() >= 5:
        log("周末美股休市，跳过定时分析（手动想跑就显式加 --session）。")
        return
    log(f"开始：{SESSION_LABEL[session]}时段分析")
    snap = get_snapshot()
    tickers = [args.stock] if args.stock else pick_watchlist(session)
    log(f"本轮盯 {len(tickers)} 只：{', '.join(t.split('_')[0] for t in tickers)}")

    results = []
    for tk in tickers:
        try:
            results.append(analyze_one(snap, tk, session, dry_run=args.dry_run))
        except Exception as e:
            log(f"  {tk} 分析异常：{e}")
    if args.dry_run:
        return
    if not results:
        log("无结果，结束。")
        return
    assemble_and_send(results, session, send=not args.no_feishu)
    log("完成。")


if __name__ == "__main__":
    main()
