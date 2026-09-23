#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
韩股→美光 跨市场领先指标  ·  korea_leadlag.py
================================================================
SK 海力士(000660.KS)/三星(005930.KS) 在亚洲时段交易、比美股早收盘 ~7 小时，
和 MU/SNDK 同属 HBM/DRAM 复合体 → 韩股当日涨跌对美股内存当日**开盘**有领先信息。

⚠️ 专业要点（决定怎么用）：
  在 MU/海力士这种大盘股里，领先效应**大部分已被套利进美股"开盘价(gap)"** ——
  你早上看到的韩股大跌，开盘价里已经反映了。所以真正"可交易"的，只剩
  **开盘之后 open→close 的残余漂移**，而这部分在大盘股里通常 ~0、甚至均值回归。
  → 本工具不替你下结论，而是**每天记账、累积样本、用真实数据验证到底有没有可交易的 edge**。

三种用法：
  --signal   盘前(韩股已收/美股未开)：算韩股综合涨跌 → 推飞书"今日内存大概率低/高开"
  --log      盘后(美股已收)：记录 韩股涨跌 + MU/SNDK 的 prevclose/open/close → 存 CSV
  --report   累积若干天后：算相关性/命中率/策略盈亏，判断 edge 真假
  （不带参数=按英国时间自动判断 signal/log）

数据：韩股=Yahoo v8 chart(000660.KS/005930.KS)，退回 Finnhub EWY(韩国ETF)代理；
      美股 open/close=Finnhub /quote。全部标准库，不碰会卡死的 yfinance。
⚠️ 研究/验证工具，不构成买卖建议。
"""
import argparse, csv, json, os, subprocess, sys, time, urllib.request
from datetime import datetime
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tech_monitor import feishu_send, load_env

CSV_FILE = HERE / "korea_leadlag.csv"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
# 韩股内存双雄 + 美股内存双雄
KOREA = [("000660.KS", "海力士"), ("005930.KS", "三星")]
US = [("MU", "MU"), ("SNDK", "SNDK")]


def log(m):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}", flush=True)


def _finnhub_key():
    for l in open(HERE / ".env", encoding="utf-8", errors="ignore"):
        if l.startswith("FINNHUB_API_KEY="):
            return l.split("=", 1)[1].strip()
    return os.environ.get("FINNHUB_API_KEY", "")


def yahoo_daily_ret(symbol):
    """Yahoo v8 chart → 最近一日 % 涨跌(今收 vs 昨收)。失败返回 None。"""
    # Yahoo 在更深层（TLS 指纹）blocks urllib，所以走系统 curl —— curl 经测可拿到 200。
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}?range=7d&interval=1d"
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "10", "-H", f"User-Agent: {UA}", url],
                capture_output=True, text=True, timeout=12)
            if r.returncode != 0 or not r.stdout:
                continue
            d = json.loads(r.stdout)
            chart = d.get("chart") or {}
            if chart.get("error") or not chart.get("result"):
                continue
            rr = chart["result"][0]
            closes = [c for c in (rr.get("indicators", {}).get("quote", [{}])[0].get("close") or []) if c is not None]
            if len(closes) >= 2:
                return round((closes[-1] / closes[-2] - 1) * 100, 2)
            m = rr.get("meta", {})
            if m.get("regularMarketPrice") and m.get("chartPreviousClose"):
                return round((m["regularMarketPrice"] / m["chartPreviousClose"] - 1) * 100, 2)
        except Exception:
            continue
    return None


def finnhub_quote(symbol):
    """→ dict(c,pc,o,dp) 或 None。"""
    try:
        d = json.load(urllib.request.urlopen(
            f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={_finnhub_key()}", timeout=10))
        if d.get("c"):
            return {"c": d.get("c"), "pc": d.get("pc"), "o": d.get("o"), "dp": d.get("dp")}
    except Exception:
        pass
    return None


_KOREA_CACHE = HERE / "korea_cache.json"

def get_korea():
    """韩股综合涨跌 %。EWY(Finnhub) 作主源（最稳）；海力士/三星(Yahoo) 作增强源（不稳，靠日缓存避免重复打 Yahoo）。"""
    # 1) EWY 永远试（Finnhub 很稳）
    out = {}
    ewy = finnhub_quote("EWY")
    out["ewy"] = round(ewy["dp"], 2) if ewy and ewy.get("dp") is not None else None
    # 2) 海力士/三星：先看今天的日缓存；缓存里没有再打 Yahoo，并加 2 秒间隔避免触发 throttle
    today = datetime.now().strftime("%Y-%m-%d")
    cache = {}
    if _KOREA_CACHE.exists():
        try:
            cache = json.loads(_KOREA_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    cache_today = cache.get(today, {})
    for i, (key, sym) in enumerate((("hynix", "000660.KS"), ("samsung", "005930.KS"))):
        # 缓存里"今天试过了"就不再试（不管成功失败）—— 避免每次跑都重打 Yahoo
        if key in cache_today:
            out[key] = cache_today[key]
        else:
            if i > 0: time.sleep(5)   # 第一次后再隔 5 秒，避开 Yahoo throttle
            v = yahoo_daily_ret(sym)
            out[key] = v
            cache_today[key] = v   # 即使是 None 也写，标记"今天已试过"
    cache[today] = cache_today
    # 只保留最近 30 天
    if len(cache) > 30:
        cache = dict(sorted(cache.items())[-30:])
    try:
        _KOREA_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    # 3) 综合：优先海力士/三星均值，否则用 EWY
    mem = [x for x in (out.get("hynix"), out.get("samsung")) if x is not None]
    if len(mem) == 2:
        out["composite"] = round(sum(mem)/2, 2); out["src"] = "海力士+三星"
    elif len(mem) == 1 and out.get("ewy") is not None:
        out["composite"] = round((mem[0] + out["ewy"])/2, 2); out["src"] = "韩股+EWY 混合"
    elif len(mem) == 1:
        out["composite"] = mem[0]; out["src"] = "海力士/三星单只"
    elif out.get("ewy") is not None:
        out["composite"] = out["ewy"]; out["src"] = "EWY ETF代理"
    else:
        out["composite"] = None; out["src"] = "无数据"
    return out


def fmt_pct(x):
    return f"{x:+.2f}%" if isinstance(x, (int, float)) else "?"


# ───────── signal: 盘前推送 ─────────
def do_signal(send=True):
    k = get_korea()
    if k["composite"] is None:
        log("韩股数据拿不到，跳过信号。"); return
    comp = k["composite"]
    bias = "偏空，MU/SNDK 大概率**低开**" if comp < -0.5 else ("偏多，大概率**高开**" if comp > 0.5 else "中性/方向不明")
    title = f"🇰🇷 韩股领先信号 · {datetime.now():%m-%d}"
    # 只显示**有效**的细分数据（不再显示 "?"）
    detail_bits = []
    if k.get("hynix") is not None:   detail_bits.append(f"海力士 {fmt_pct(k['hynix'])}")
    if k.get("samsung") is not None: detail_bits.append(f"三星 {fmt_pct(k['samsung'])}")
    if k.get("ewy") is not None:     detail_bits.append(f"EWY {fmt_pct(k['ewy'])}")
    lines = [
        f"韩股内存({k['src']})综合 {fmt_pct(comp)} → 美股内存今日 {bias}",
        f"  细分：{' · '.join(detail_bits) if detail_bits else '仅 EWY'}",
        "",
        "⚠️ 这是领先指标：开盘价里多半已 price-in；真正可交易的是开盘后残余漂移（本工具正在记账验证，别凭感觉重仓）。",
        "—— 韩股领先信号 · 研究用，不构成买卖建议",
    ]
    if send:
        feishu_send(title, lines, load_env())
    else:
        print(title + "\n" + "\n".join(lines))


# ───────── log: 盘后记账 ─────────
def do_log():
    k = get_korea()
    if k["composite"] is None:
        log("韩股数据拿不到，今日不记账。"); return
    row = {"date": f"{datetime.now():%Y-%m-%d}", "hynix": k["hynix"], "samsung": k["samsung"],
           "ewy": k["ewy"], "korea": k["composite"]}
    for sym, name in US:
        q = finnhub_quote(sym)
        if not q or not all(isinstance(q.get(x), (int, float)) for x in ("pc", "o", "c")) or not q["pc"] or not q["o"]:
            log(f"{name} 行情不全，跳过"); row[f"{name}_gap"] = row[f"{name}_intra"] = row[f"{name}_full"] = None; continue
        row[f"{name}_pc"], row[f"{name}_o"], row[f"{name}_c"] = q["pc"], q["o"], q["c"]
        row[f"{name}_gap"]   = round((q["o"] / q["pc"] - 1) * 100, 2)   # 隔夜 gap（已被韩股 price-in 的部分）
        row[f"{name}_intra"] = round((q["c"] / q["o"] - 1) * 100, 2)    # 开盘→收盘（可交易残余）
        row[f"{name}_full"]  = round((q["c"] / q["pc"] - 1) * 100, 2)
    cols = ["date", "hynix", "samsung", "ewy", "korea"] + \
           [f"{n}_{s}" for n, _ in US for s in ("pc", "o", "c", "gap", "intra", "full")]
    new = not CSV_FILE.exists()
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)
    log(f"已记账 {row['date']}：韩股 {fmt_pct(k['composite'])} | " +
        " ".join(f"{n} gap{fmt_pct(row.get(n+'_gap'))}/intra{fmt_pct(row.get(n+'_intra'))}" for n, _ in US))


# ───────── report: 验证 edge ─────────
def _pearson(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if isinstance(x, (int, float)) and isinstance(y, (int, float))]
    n = len(pts)
    if n < 4:
        return None, n
    mx = sum(p[0] for p in pts) / n; my = sum(p[1] for p in pts) / n
    cov = sum((p[0]-mx)*(p[1]-my) for p in pts)
    vx = sum((p[0]-mx)**2 for p in pts); vy = sum((p[1]-my)**2 for p in pts)
    if vx == 0 or vy == 0:
        return None, n
    return round(cov/(vx*vy)**0.5, 3), n


def do_report():
    if not CSV_FILE.exists():
        print("还没有数据。先让 --log 跑几天（建议 ≥15-20 个交易日再看）。"); return
    _by = {}
    for _r in csv.DictReader(open(CSV_FILE, encoding="utf-8")):
        _by[_r["date"]] = _r          # 同一天多次记录只保留最后一条（防重复）
    rows = list(_by.values())
    def col(name):
        out = []
        for r in rows:
            try: out.append(float(r[name]) if r.get(name) not in ("", None) else None)
            except: out.append(None)
        return out
    korea = col("korea")
    print(f"\n=== 韩股领先指标 · edge 验证（n={len(rows)} 交易日）===")
    print("解读：gap 相关性高=韩股确实领先(但已 price-in、不可交易)；")
    print("      intra(开→收)相关性≈0 = 残余没edge(别交易)；明显>0=动量可跟；明显<0=该反着做(fade)。\n")
    for n, _ in US:
        gap, intra = col(f"{n}_gap"), col(f"{n}_intra")
        cg, ng = _pearson(korea, gap)
        ci, ni = _pearson(korea, intra)
        # 跟随动量策略：韩股跌→做空当日 open→close（涨→做多），日均收益
        pnl = [(1 if k > 0 else -1) * iv for k, iv in zip(korea, intra)
               if isinstance(k, (int, float)) and isinstance(iv, (int, float)) and k != 0]
        hit = [1 for k, iv in zip(korea, intra)
               if isinstance(k, (int, float)) and isinstance(iv, (int, float)) and (k > 0) == (iv > 0)]
        navg = len(pnl)
        print(f"[{n}]")
        print(f"  corr(韩股, gap隔夜)   = {cg}  (n={ng})  ← 越高越说明韩股领先、且已进开盘价")
        print(f"  corr(韩股, intra开→收)= {ci}  (n={ni})  ← 这才是可交易的；≈0 就别做")
        if navg:
            print(f"  跟随策略 日均 {sum(pnl)/navg:+.3f}% / 累计 {sum(pnl):+.2f}% / 命中率 {len(hit)/navg*100:.0f}%  (n={navg})")
        print()
    print("注：样本少时全部仅供参考；统计上一般要 ≥20-30 天才有点说服力。")


def infer_mode():
    h = datetime.now().hour
    return "signal" if h < 14 else ("log" if h >= 21 else "signal")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signal", action="store_true")
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--no-feishu", action="store_true")
    a = ap.parse_args()
    if a.report:
        do_report()
    elif a.log:
        do_log()
    elif a.signal:
        do_signal(send=not a.no_feishu)
    else:  # 定时无参数：按时间自动
        m = infer_mode()
        log(f"自动模式 → {m}")
        (do_log if m == "log" else do_signal)()


if __name__ == "__main__":
    main()
