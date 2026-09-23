"""观澜 — Trading 212 decision dashboard (multi-account: ISA + Invest).

Live T212 data (HTTP Basic Auth per account, via .env), market data
(Finnhub / yfinance / marketdata.app), and the read-only feeds the monitors in
~/Library/guanlan write. The legacy codex chat / analysis endpoints remain for
compatibility but the dashboard no longer uses them.

Run:
    pip3 install -r requirements.txt
    python3 server.py
Open http://localhost:8787
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

T212_KEY_ID = os.getenv("TRADING212_API_KEY_ID", "").strip()
T212_SECRET = os.getenv("TRADING212_API_SECRET", "").strip()
T212_ENV = os.getenv("TRADING212_ENV", "demo").strip().lower()
DEFAULT_CMD = os.getenv("AGENT_CMD", "codex")
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
MARKETDATA_TOKEN = (os.getenv("MARKETDATA_API_TOKEN") or os.getenv("MARKETDATA_TOKEN") or "").strip()

T212_BASE = (
    "https://live.trading212.com/api/v0"
    if T212_ENV == "live"
    else "https://demo.trading212.com/api/v0"
)


# ============================================================================
# Trading 212 accounts — T212 issues one API key PER ACCOUNT (Invest and
# Stocks ISA are separate), so each account carries its own credentials.
#   primary : TRADING212_API_KEY_ID / _SECRET        (label TRADING212_LABEL, default "ISA")
#   extra   : TRADING212_<NAME>_API_KEY_ID / _SECRET (e.g. NAME=INVEST → id "invest")
# ============================================================================

class T212Account:
    def __init__(self, aid: str, label: str, key_id: str, secret: str, env: str) -> None:
        self.id = aid
        self.label = label
        self.key_id = key_id
        self.secret = secret
        self.env = env
        self.base = ("https://live.trading212.com/api/v0" if env == "live"
                     else "https://demo.trading212.com/api/v0")

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.secret)

    def __repr__(self) -> str:
        return f"<T212Account {self.id} {self.label} {self.env}>"


def _load_accounts(env: "dict | None" = None) -> list[T212Account]:
    e = os.environ if env is None else env
    g = lambda k, d="": (e.get(k) or d).strip()
    primary_env = g("TRADING212_ENV", "demo").lower()
    accts = [T212Account(g("TRADING212_ACCOUNT_ID", "isa").lower(), g("TRADING212_LABEL", "ISA"),
                         g("TRADING212_API_KEY_ID"), g("TRADING212_API_SECRET"), primary_env)]
    for k in sorted(e):
        m = re.fullmatch(r"TRADING212_([A-Z0-9]+)_API_KEY_ID", k)
        if not m:
            continue
        name = m.group(1)
        key_id, secret, aid = g(k), g(f"TRADING212_{name}_API_SECRET"), name.lower()
        if not key_id or not secret or any(a.id == aid for a in accts):
            continue
        accts.append(T212Account(aid, g(f"TRADING212_{name}_LABEL", name.capitalize()),
                                 key_id, secret, g(f"TRADING212_{name}_ENV", primary_env).lower()))
    return accts


ACCOUNTS = _load_accounts()
PRIMARY = ACCOUNTS[0]
_ENV_FILE = Path(__file__).parent / ".env"
_env_mtime = _ENV_FILE.stat().st_mtime if _ENV_FILE.exists() else 0.0


def maybe_reload_accounts() -> bool:
    """Re-read the TRADING212_* keys when .env changes on disk, so pasting a
    new account's key (e.g. Invest) takes effect without a server restart.
    Called from the hot paths (snapshot / accounts); costs one stat()."""
    global ACCOUNTS, PRIMARY, T212_KEY_ID, T212_SECRET, T212_ENV, _env_mtime
    try:
        mtime = _ENV_FILE.stat().st_mtime
    except OSError:
        return False
    if mtime == _env_mtime:
        return False
    _env_mtime = mtime
    from dotenv import dotenv_values
    fresh = {k: v for k, v in dotenv_values(_ENV_FILE).items()
             if k.startswith("TRADING212_") and v is not None}
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRADING212_")}
    env.update(fresh)
    new = _load_accounts(env)
    sig = lambda lst: [(a.id, a.label, a.key_id, a.secret, a.env) for a in lst]
    if sig(new) == sig(ACCOUNTS):
        return False
    ACCOUNTS, PRIMARY = new, new[0]
    T212_KEY_ID, T212_SECRET, T212_ENV = PRIMARY.key_id, PRIMARY.secret, PRIMARY.env
    ids = {a.id for a in new}
    for k in list(cache._store):                 # drop per-account caches → refetch with new keys
        if k.split(":", 1)[0] in ids or k in ("instruments",):
            cache._store.pop(k, None)
    print(f"  [accounts] reloaded from .env → {[a.id for a in new]}", flush=True)
    return True


def _acct(a: "T212Account | None") -> T212Account:
    return a or PRIMARY


def get_account(aid: str | None) -> "T212Account | None":
    for a in ACCOUNTS:
        if a.id == (aid or "").lower():
            return a
    return None


def _acct_file(base: str, a: "T212Account | None") -> Path:
    """Per-account disk cache path. The primary account keeps the legacy
    filename so existing history (transactions / daily totals) carries over."""
    a = _acct(a)
    if a is PRIMARY:
        return HERE / base
    stem, ext = base.rsplit(".", 1)
    return HERE / f"{stem}_{a.id}.{ext}"


app = FastAPI()
HERE = Path(__file__).parent


@app.on_event("startup")
async def _bigger_thread_pool() -> None:
    # yfinance / file IO run in worker threads; a page load fans out ~30 of
    # them (risk + tech batch). The default pool (cpu+4) made them queue.
    from concurrent.futures import ThreadPoolExecutor
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=48))

# PWA: serve manifest + icons under /static
_static_dir = HERE / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# widget.html drives the menubar popover + desktop floating window via
# WKWebView. WKWebView caches static HTML aggressively by default — without
# no-cache headers we'd see a stale version after we edit the file. Tell every
# caller (Safari, WKWebView, anything) to revalidate on each request.
@app.middleware("http")
async def _widget_html_no_cache(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/static/widget.html":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif path == "/" or path.startswith("/static/app/"):
        # dashboard shell + its ES modules: always revalidate (cheap 304s on
        # localhost) so a frontend edit shows up on the next reload.
        response.headers["Cache-Control"] = "no-cache"
    elif (path.startswith("/api/") and request.headers.get("sec-fetch-site") == "same-origin"
          and not request.headers.get("x-guanlan-ui")):
        # A browser tab still running the pre-2026-09-23 dashboard: that page
        # was served without Cache-Control, so Chrome keeps re-serving its
        # heuristically "fresh" stale copy on every new navigation. Ask the
        # browser (once per 10 min per UA) to drop its HTTP cache so the next
        # open gets the new page. The new UI tags its calls with X-Guanlan-UI.
        ua = request.headers.get("user-agent", "")
        now = time.time()
        if now - _CSD_SENT.get(ua, 0.0) > 600:
            _CSD_SENT[ua] = now
            response.headers["Clear-Site-Data"] = '"cache"'
    return response


_CSD_SENT: dict[str, float] = {}


# ============================================================================
# Trading 212 client with TTL cache
# ============================================================================

class T212Cache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def get(self, key: str, ttl: float, fetch):
        async with self._lock(key):
            now = time.time()
            entry = self._store.get(key)
            if entry and now - entry[0] < ttl:
                return entry[1]
            try:
                value = await fetch()
                self._store[key] = (now, value)
                return value
            except Exception:
                # Fetch failed (T212 rate limit / network blip / etc).
                # If we still have a stale entry, hand it back and bump its
                # timestamp so we don't retry on every poll for a while.
                # This is what keeps the widget showing data instead of 429s.
                if entry is not None:
                    self._store[key] = (now, entry[1])
                    return entry[1]
                raise


cache = T212Cache()


async def t212_get(path: str, acct: "T212Account | None" = None) -> Any:
    a = _acct(acct)
    if not a.configured:
        raise HTTPException(500, f"{a.label}: Trading 212 API key 未在 .env 中设置")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{a.base}{path}", auth=(a.key_id, a.secret))
    if r.status_code == 401:
        raise HTTPException(401, f"{a.label}: Trading 212 拒绝凭证 — 检查 key/secret 和 env (live vs demo)")
    if r.status_code == 403:
        raise HTTPException(403, f"{a.label}: 这把 key 没开该权限(scope)")
    if r.status_code == 429:
        raise HTTPException(429, f"{a.label}: Trading 212 限流,稍后再试")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"{a.label}: Trading 212 错误: {r.text[:200]}")
    return r.json()


def _ck(a: "T212Account | None", name: str) -> str:
    return f"{_acct(a).id}:{name}"


async def get_cash(a=None):         return await cache.get(_ck(a, "cash"),        5.0,  lambda: t212_get("/equity/account/cash", a))
async def get_info(a=None):         return await cache.get(_ck(a, "info"),        60.0, lambda: t212_get("/equity/account/info", a))
async def get_portfolio(a=None):    return await cache.get(_ck(a, "portfolio"),   8.0,  lambda: t212_get("/equity/portfolio", a))
async def get_orders(a=None):       return await cache.get(_ck(a, "orders_pend"), 15.0, lambda: t212_get("/equity/orders", a))
async def get_history_orders(a=None):  return await cache.get(_ck(a, "hist_orders"), 30.0, lambda: t212_get("/equity/history/orders?limit=50", a))
async def get_dividends(a=None):       return await cache.get(_ck(a, "hist_divs"),   30.0, lambda: t212_get("/history/dividends?limit=50", a))
async def get_transactions(a=None):    return await cache.get(_ck(a, "hist_trans"),  30.0, lambda: t212_get("/history/transactions?limit=50", a))


INSTRUMENTS_FILE = HERE / "instruments_cache.json"


async def get_instruments():
    """The instrument list is global (same for every account) — fetch it with
    whichever configured account answers first. T212 allows this call once
    per 50s, so a quick server restart used to 429 the whole snapshot: keep a
    compact disk copy and fall back to it."""
    async def _fetch():
        last_err = None
        for a in ACCOUNTS:
            if not a.configured:
                continue
            try:
                rows = await t212_get("/equity/metadata/instruments", a)
                try:
                    slim = [{k: r.get(k) for k in ("ticker", "name", "currencyCode", "type", "isin")}
                            for r in rows if isinstance(r, dict)]
                    INSTRUMENTS_FILE.write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
                return rows
            except Exception as e:   # noqa: BLE001 — try the next account
                last_err = e
        disk = _read_json_safe(INSTRUMENTS_FILE, None)
        if isinstance(disk, list) and disk:
            return disk
        raise last_err or HTTPException(500, "没有可用的 Trading 212 账户")
    if "instruments" not in cache._store and INSTRUMENTS_FILE.exists():
        # Cold start: serve the saved list at once (it barely changes and the
        # download is 2MB+), refresh from T212 in the background.
        disk = _read_json_safe(INSTRUMENTS_FILE, None)
        if isinstance(disk, list) and disk:
            cache._store["instruments"] = (time.time(), disk)

            async def _refresh():
                try:
                    cache._store["instruments"] = (time.time(), await _fetch())
                except Exception:
                    pass
            _BG_TASKS["instruments"] = asyncio.create_task(_refresh())
            return disk
    return await cache.get("instruments", 600.0, _fetch)


async def get_info_safe(a=None) -> dict:
    """/equity/account/info is limited to 1 call / 30s and only tells us the
    account currency + id; don't let a 429 there sink the whole snapshot."""
    try:
        info = await get_info(a)
        if isinstance(info, dict):
            _INFO_LAST[_acct(a).id] = info
        return info
    except Exception:
        return _INFO_LAST.get(_acct(a).id) or {"currencyCode": "GBP"}


_INFO_LAST: dict = {}


# --- Paginated T212 history (transactions / dividends / fills), per account ---
#
# The history endpoints return ≤50 rows per page and are rate-limited to ~6
# requests/min per key, so each feed keeps a per-account disk cache: ONE full
# backfill (run in the background, resumable via the saved cursor), then cheap
# "newest page until we hit a row we already have" diffs.

def _fill_key(it: dict) -> str:
    o, f = (it.get("order") or {}), (it.get("fill") or {})
    return f"{o.get('id')}:{f.get('id')}"


HISTORY_FEEDS = {
    #  feed            first page                          disk file                   row key
    "transactions": ("/history/transactions?limit=50",   "transactions_cache.json", lambda it: it.get("reference")),
    "dividends":    ("/history/dividends?limit=50",      "dividends_cache.json",    lambda it: it.get("reference")),
    "fills":        ("/equity/history/orders?limit=50",  "orders_cache.json",       _fill_key),
}
HISTORY_PAGE_GAP = 10.5          # seconds between pages → stays under 6 req/min
_BACKFILL_TASKS: dict[str, asyncio.Task] = {}


def _load_hist(feed: str, a=None) -> dict:
    path = _acct_file(HISTORY_FEEDS[feed][1], a)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(d.get("items"), list):
            d["items"] = []
        return d
    except Exception:
        return {"items": [], "last_sync": None, "complete": False}


def _save_hist(feed: str, data: dict, a=None) -> None:
    try:
        _acct_file(HISTORY_FEEDS[feed][1], a).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


async def _fetch_one_page(path: str, a=None) -> dict | None:
    consecutive_429 = 0
    while consecutive_429 < 4:
        try:
            return await t212_get(path, a)
        except HTTPException as e:
            if e.status_code == 429:
                consecutive_429 += 1
                await asyncio.sleep(6.0 * (2 ** (consecutive_429 - 1)))
                continue
            return None
    return None


def _next_path_from(data: dict, first_path: str = "/history/transactions") -> str | None:
    np = (data or {}).get("nextPagePath")
    if not np:
        return None
    if np.startswith("/api/v0"):
        np = np[len("/api/v0"):]
    if np.startswith("/"):
        return np
    return f"{first_path.split('?')[0]}?{np.lstrip('?')}"


async def _backfill_history(feed: str, a=None, max_pages: int = 80) -> None:
    """Walk every page (oldest last) into the disk cache. Resumable: the cursor
    is saved after each page, so a restart continues where it stopped."""
    first, _, keyfn = HISTORY_FEEDS[feed]
    disk = _load_hist(feed, a)
    items = list(disk.get("items") or []) if disk.get("next_path") else []
    path = disk.get("next_path") or first
    seen = {keyfn(it) for it in items}
    for _ in range(max_pages):
        data = await _fetch_one_page(path, a)
        if data is None:
            break                                  # give up for now; resume later
        for it in data.get("items") or []:
            k = keyfn(it)
            if k not in seen:
                seen.add(k)
                items.append(it)
        path = _next_path_from(data, first)
        _save_hist(feed, {"items": items, "last_sync": time.time(),
                          "complete": path is None, "next_path": path}, a)
        if path is None:
            break
        await asyncio.sleep(HISTORY_PAGE_GAP)
    cache._store.pop(_ck(a, f"hist:{feed}"), None)   # next read sees the full set


async def _sync_history(feed: str, a=None) -> list:
    first, _, keyfn = HISTORY_FEEDS[feed]
    disk = _load_hist(feed, a)
    items = list(disk.get("items") or [])
    if not disk.get("complete"):
        tkey = _ck(a, f"backfill:{feed}")
        task = _BACKFILL_TASKS.get(tkey)
        if task is None or task.done():
            _BACKFILL_TASKS[tkey] = asyncio.create_task(_backfill_history(feed, a))
        return items                               # partial while the backfill runs
    known = {keyfn(it) for it in items}
    new_items: list = []
    path = first
    for _ in range(10):
        data = await _fetch_one_page(path, a)
        if data is None:
            break
        page = data.get("items") or []
        fresh = [it for it in page if keyfn(it) not in known]
        new_items.extend(fresh)
        if len(fresh) < len(page):                 # reached rows we already have
            break
        path = _next_path_from(data, first)
        if not path:
            break
        await asyncio.sleep(HISTORY_PAGE_GAP)
    if new_items:
        items = new_items + items                  # T212 returns newest first
    _save_hist(feed, {"items": items, "last_sync": time.time(), "complete": True}, a)
    return items


async def get_history(feed: str, a=None, ttl: float = 300.0) -> list:
    """All-time rows of one history feed for one account (disk cache + diff).
    Cached in memory `ttl` seconds so rapid polls don't re-hit T212."""
    return await cache.get(_ck(a, f"hist:{feed}"), ttl, lambda: _sync_history(feed, a))


def history_status(feed: str, a=None) -> dict:
    disk = _load_hist(feed, a)
    task = _BACKFILL_TASKS.get(_ck(a, f"backfill:{feed}"))
    return {"complete": bool(disk.get("complete")), "rows": len(disk.get("items") or []),
            "syncing": bool(task and not task.done())}


async def get_all_transactions(a=None) -> list:
    return await get_history("transactions", a)


def txn_kind(it: dict) -> str:
    """deposit | withdraw | transfer | interest | fee | other.

    Until mid-July 2026 T212 booked the DAILY interest on free cash as type
    DEPOSIT (tiny amounts, stamped ~01:10 UTC); from 2026-07-14 it switched to
    INTEREST_ON_FREE_CASH. Counting those as deposits overstated 总成本 (net
    capital in) by ~£100 and hid that much return — reclassify them."""
    typ = (it.get("type") or "").upper()
    if "INTEREST" in typ:
        return "interest"
    if typ == "DEPOSIT":
        amt = float(it.get("amount") or 0)
        hour = (it.get("dateTime") or "")[11:13]
        if 0 < amt < 10 and hour in ("00", "01", "02"):
            return "interest"
        return "deposit"
    if typ in ("WITHDRAW", "WITHDRAWAL"):
        return "withdraw"
    if typ == "TRANSFER":
        return "transfer"
    if typ == "FEE":
        return "fee"
    return "other"


def capital_flow(it: dict) -> float | None:
    """Signed contributed-capital amount of a transaction (+in / −out), or
    None if it isn't a capital flow. TRANSFER is signed already and counts:
    assets/cash moved in are contributed capital just like a deposit."""
    k = txn_kind(it)
    amt = float(it.get("amount") or 0)
    if k in ("deposit", "transfer"):
        return amt
    if k == "withdraw":
        return -abs(amt)
    return None


# --- Daily total snapshot file (per account) for monthly P/L + the NAV curve ---

def _load_daily_totals(a=None) -> dict:
    try:
        return json.loads(_acct_file("daily_totals.json", a).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_daily_totals(data: dict, a=None) -> None:
    try:
        _acct_file("daily_totals.json", a).write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
        )
    except Exception:
        pass


def _record_today_total(total: float, a=None) -> dict:
    from datetime import date
    daily = _load_daily_totals(a)
    key = date.today().isoformat()
    if total > 0 and daily.get(key) != round(total, 2):
        daily[key] = round(total, 2)
        _save_daily_totals(daily, a)
    return daily


def _parse_dt(s: str):
    """Parse T212's ISO 8601 datetime string. Returns datetime or None."""
    from datetime import datetime
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ============================================================================
# Market data — OHLC from Stooq (no API key, no rate limit), news from Yahoo
# ============================================================================

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.6 Safari/605.1.15")
YAHOO_UA = UA  # backwards-compat alias for the older Yahoo helpers

# T212 ticker → base ticker. Strip suffix; remap known SPAC/de-SPAC.
TICKER_REMAP = {"VACQ": "RKLB", "YNDX": "NBIS", "FB": "META"}  # Nebius: YNDX→NBIS (YNDX delisted); Meta: FB→META

def _base_ticker(t212_ticker: str) -> str:
    m = re.match(r"^[A-Z]+", t212_ticker or "")
    base = m.group(0) if m else (t212_ticker or "")
    return TICKER_REMAP.get(base, base)


def t212_to_yahoo(ticker: str) -> str:
    """Yahoo wants the bare ticker (auto-resolves exchange)."""
    return _base_ticker(ticker)


def t212_to_stooq_candidates(t212_ticker: str) -> list[str]:
    """Return a ranked list of Stooq symbols to try.

    Stooq format is `<ticker>.<market>` (lowercase). Different markets:
      .us  — US (NASDAQ/NYSE)        — most US listings
      .uk  — London (LSE)
      .nl  — Amsterdam (AEX)
      .de  — Germany (Xetra)
      .fr  — Paris
    """
    base = _base_ticker(t212_ticker).lower()
    cands = []
    # T212 suffix gives a hint about primary market.
    if "_US_" in t212_ticker:
        cands.append(f"{base}.us")
    elif t212_ticker.endswith("a_EQ"):    # T212's `a` suffix = Amsterdam (e.g. ASMLa_EQ)
        cands += [f"{base}.nl", f"{base}.us"]
    elif t212_ticker.endswith("l_EQ"):    # London
        cands += [f"{base}.uk", f"{base}.us"]
    elif t212_ticker.endswith("d_EQ"):    # Germany
        cands += [f"{base}.de", f"{base}.us"]
    else:
        # Unknown — try US first since most retail data sources have it.
        cands += [f"{base}.us", f"{base}.uk", f"{base}.nl"]
    # De-dup preserving order
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c); out.append(c)
    return out


async def _stooq_csv(stooq_symbol: str) -> str | None:
    """Fetch raw daily CSV from Stooq. Returns body text or None if not found."""
    url = "https://stooq.com/q/d/l/"
    params = {"s": stooq_symbol, "i": "d"}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        r = await client.get(url, params=params, headers={"User-Agent": UA})
    if r.status_code != 200:
        return None
    text = (r.text or "").strip()
    # Stooq returns "No data" for unknown symbols, or empty CSV with just header.
    if not text or "No data" in text or "no data" in text.lower():
        return None
    if "Date" not in text and "Open" not in text:
        return None
    return text


def _parse_stooq_csv(text: str) -> list[dict]:
    """Parse Stooq CSV into [{t, o, h, l, c, v}, ...]."""
    from datetime import datetime, timezone
    lines = text.split("\n")
    if not lines:
        return []
    header = [h.strip().lower() for h in lines[0].split(",")]
    idx = {h: i for i, h in enumerate(header)}
    if "date" not in idx or "close" not in idx:
        return []
    bars = []
    for line in lines[1:]:
        parts = line.strip().split(",")
        if len(parts) < len(header):
            continue
        try:
            dt = datetime.strptime(parts[idx["date"]], "%Y-%m-%d")
            ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
            c = float(parts[idx["close"]])
            o = float(parts[idx["open"]]) if "open" in idx else c
            hi = float(parts[idx["high"]]) if "high" in idx else c
            lo = float(parts[idx["low"]]) if "low" in idx else c
            v_str = parts[idx["volume"]] if "volume" in idx else "0"
            try: v = float(v_str)
            except ValueError: v = 0
            bars.append({"t": ts, "o": round(o, 4), "h": round(hi, 4),
                         "l": round(lo, 4), "c": round(c, 4), "v": v})
        except (ValueError, KeyError, IndexError):
            continue
    return bars


async def _stooq_chart(t212_ticker: str, range_days: int = 180) -> dict | None:
    """Try ranked Stooq candidates until one returns data."""
    for sym in t212_to_stooq_candidates(t212_ticker):
        text = await _stooq_csv(sym)
        if not text:
            continue
        bars = _parse_stooq_csv(text)
        if not bars:
            continue
        if len(bars) > range_days:
            bars = bars[-range_days:]
        return {"meta": {"symbol": sym, "source": "stooq"}, "bars": bars}
    return None


# --- yfinance: best free source for historical daily OHLC ---

try:
    import yfinance as _yf  # type: ignore
    _YF_AVAILABLE = True
except Exception:
    _yf = None
    _YF_AVAILABLE = False


async def _yfinance_chart(symbol: str, period: str = "6mo") -> dict | None:
    """Pull daily OHLC via yfinance (Yahoo with proper session/crumb auth)."""
    if not _YF_AVAILABLE:
        return None

    def _sync():
        try:
            hist = _yf.Ticker(symbol).history(period=period, auto_adjust=False)
            if hist is None or hist.empty:
                return None
            bars, nan_tail = [], None
            n_rows = len(hist)
            for i, (idx, row) in enumerate(hist.iterrows()):
                try:
                    c = float(row["Close"])
                    # Yahoo sometimes ships the latest session's bar with a NaN
                    # close (incomplete row). One NaN poisons every MA/RSI/MACD
                    # downstream and 500s the JSON — drop such bars entirely.
                    # For the LAST row keep its open/high/low/volume aside so a
                    # live quote can complete it (see get_daily_bars).
                    if c != c or c <= 0:
                        if i == n_rows - 1:
                            f = lambda k: (lambda v: v if v == v else None)(float(row[k]))
                            nan_tail = {"t": int(idx.timestamp()), "o": f("Open"), "h": f("High"),
                                        "l": f("Low"), "v": f("Volume") or 0}
                        continue
                    num = lambda k: (lambda v: v if v == v else c)(float(row[k]))
                    bars.append({
                        "t": int(idx.timestamp()),
                        "o": round(num("Open"), 4),
                        "h": round(num("High"), 4),
                        "l": round(num("Low"),  4),
                        "c": round(c, 4),
                        "v": float(row["Volume"]) if row["Volume"] == row["Volume"] else 0,
                    })
                except (KeyError, ValueError, TypeError):
                    continue
            if not bars:
                return None
            return {"meta": {"symbol": symbol, "source": "yfinance", "nan_tail": nan_tail},
                    "bars": bars}
        except Exception:
            return None

    return await asyncio.to_thread(_sync)


async def _yfinance_day_change(t212_ticker: str, currency: str | None = None) -> dict | None:
    """Day-change fallback via yfinance, for tickers Finnhub's free tier does
    not cover (e.g. LSE-listed ETFs like SGLN). Returns the same shape as the
    Finnhub day-change entries: {changePct, prev, open, high, low}."""
    if not _YF_AVAILABLE:
        return None
    # yahoo_chart_symbol maps T212's venue suffix properly (3HNXl_EQ → 3HNX.L).
    # The old "<base>.L" guess produced 3HNXL_EQ.L for tickers starting with a
    # digit — each such miss costs yfinance ~6.6s on a cold snapshot.
    candidates = [yahoo_chart_symbol(t212_ticker)]
    base = t212_to_yahoo(t212_ticker)
    if (currency or "").upper() in ("GBX", "GBP", "GBP_PENCE") and re.fullmatch(r"[A-Z0-9]+", base):
        candidates.append(base + ".L")
    candidates = list(dict.fromkeys(c for c in candidates if re.fullmatch(r"[A-Z0-9.^=-]+", c)))

    def _sync():
        for sym in candidates:
            try:
                hist = _yf.Ticker(sym).history(period="5d", auto_adjust=False)
                if hist is None or hist.empty or len(hist) < 2:
                    continue
                closes = hist["Close"].tolist()
                last, prev = float(closes[-1]), float(closes[-2])
                if not prev:
                    continue
                row = hist.iloc[-1]
                return {
                    "changePct": round((last - prev) / prev * 100, 3),
                    "prev": round(prev, 4),
                    "open": round(float(row["Open"]), 4),
                    "high": round(float(row["High"]), 4),
                    "low": round(float(row["Low"]), 4),
                }
            except Exception:
                continue
        return None

    return await asyncio.to_thread(_sync)


async def _fx_to_account(currency: str, account_ccy: str = "GBP") -> float:
    """Multiplier to convert an amount in `currency` into `account_ccy`.

    Needed because T212 reports per-instrument prices in the instrument's
    native currency (e.g. SGLN in GBX pence, US stocks in USD), while the
    account is GBP. Without conversion, marketValue/invested mix currencies —
    GBX holdings come out ~100× too large and USD ones ~25% off.
    """
    c = (currency or "").upper().strip()
    a = (account_ccy or "GBP").upper().strip()
    if not c or c == a:
        return 1.0
    # GBX / GBp are pence — 1/100 of GBP.
    pence = c in ("GBX", "GBP_PENCE", "GBPENCE", "GBPX")
    base = "GBP" if pence else c
    mult = 0.01 if pence else 1.0
    if base == a:
        return mult
    if not _YF_AVAILABLE:
        return mult
    def _sync():
        # Yahoo FX: "{base}{a}=X" = units of `a` per 1 `base` (direct);
        # fall back to the reciprocal pair if the direct one has no data.
        for sym, recip in ((f"{base}{a}=X", False), (f"{a}{base}=X", True)):
            try:
                h = _yf.Ticker(sym).history(period="5d")
                if h is None or h.empty:
                    continue
                px = float(h["Close"].tolist()[-1])
                if px:
                    return (1.0 / px) if recip else px
            except Exception:
                continue
        return None
    rate = await asyncio.to_thread(_sync)
    return mult * rate if rate else mult


# --- Options walls: call/put open-interest support & resistance (上限/下限) ---

async def _options_levels_yf(symbol: str, spot: float | None = None,
                             window: float = 0.30) -> dict | None:
    """Options-derived 上限/下限 for a US-listed optionable stock.

    - Call Wall (上限/阻力): nearest-expiry strike with the largest call open
      interest at-or-above spot — dealers short gamma there tend to cap price.
    - Put Wall (下限/支撑): largest put OI strike at-or-below spot — tends to
      act as a floor.
    - Expected move band: spot ± spot·ATM_IV·sqrt(days/365) — the range the
      option market is pricing in to that expiry.
    - Max pain: strike that would expire worthless for the most option holders.

    Strikes are restricted to ±`window` of spot so far-tail OI (e.g. a stray
    leveraged $650 put on a $1,140 stock) doesn't masquerade as "support".
    Returns {error: ...} when the name has no/thin options. Heavy + blocking,
    so it runs in a thread and is meant to be cached (OI only updates daily).
    """
    if not _YF_AVAILABLE:
        return None

    def _sync():
        import math
        from datetime import datetime, timezone
        try:
            tk = _yf.Ticker(symbol)
            exps = list(tk.options or [])
            if not exps:
                return {"symbol": symbol, "error": "无期权 (no options)"}

            def _days(e):
                try:
                    d = datetime.strptime(e, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    return max((d - datetime.now(timezone.utc)).days, 0)
                except Exception:
                    return 0
            def _is_monthly(e):
                try:
                    d = datetime.strptime(e, "%Y-%m-%d")
                    return d.weekday() == 4 and 15 <= d.day <= 21   # 3rd Friday
                except Exception:
                    return False
            # Real open interest concentrates on the standard MONTHLY expiries
            # (3rd Friday) — weeklies are thin and their "wall" is noise. Prefer
            # the nearest monthly >=3 days out; else nearest >=5d; else nearest.
            exp = (next((e for e in exps if _is_monthly(e) and _days(e) >= 3), None)
                   or next((e for e in exps if _days(e) >= 5), exps[0]))
            oc = tk.option_chain(exp)
            calls, puts = oc.calls, oc.puts
            if calls is None or puts is None or calls.empty or puts.empty:
                return {"symbol": symbol, "error": "期权链为空"}
            # Guard: Yahoo intermittently returns a chain with EMPTY open interest
            # (seen even on AAPL). Without this, idxmax() silently picks the ATM
            # strike and fabricates a fake "wall" glued to spot. Bail out honestly.
            total_oi = float(calls["openInterest"].fillna(0).sum()
                             + puts["openInterest"].fillna(0).sum())
            if total_oi < 100:
                return {"symbol": symbol, "error": "OI 数据暂缺(Yahoo)", "expiry": exp}

            sp = spot
            if not sp:
                try:
                    sp = float(tk.fast_info.get("lastPrice"))
                except Exception:
                    sp = None
            if not sp:
                sp = float(calls["strike"].median())

            lo, hi = sp * (1 - window), sp * (1 + window)
            cw_df = calls[(calls["strike"] >= sp) & (calls["strike"] <= hi)]
            pw_df = puts[(puts["strike"] <= sp) & (puts["strike"] >= lo)]
            if cw_df.empty:
                cw_df = calls
            if pw_df.empty:
                pw_df = puts
            cw = cw_df.loc[cw_df["openInterest"].fillna(0).idxmax()]
            pw = pw_df.loc[pw_df["openInterest"].fillna(0).idxmax()]

            # ATM implied vol = avg of the call & put nearest spot
            ci = (calls["strike"] - sp).abs().idxmin()
            pi = (puts["strike"] - sp).abs().idxmin()
            ivs = [v for v in (float(calls.loc[ci, "impliedVolatility"]),
                               float(puts.loc[pi, "impliedVolatility"]))
                   if v and v == v and 0 < v < 5]
            atm_iv = (sum(ivs) / len(ivs)) if ivs else None

            days = max(_days(exp), 1)
            exp_move = (sp * atm_iv * math.sqrt(days / 365.0)) if atm_iv else None

            # Max pain over the windowed strike grid
            strikes = sorted(s for s in set(calls["strike"]).union(set(puts["strike"]))
                             if lo <= s <= hi)
            coi = dict(zip(calls["strike"], calls["openInterest"]))
            poi = dict(zip(puts["strike"], puts["openInterest"]))
            best_strike, best_pain = None, None
            for K in strikes:
                pain = 0.0
                for s2 in strikes:
                    if s2 < K:
                        pain += (coi.get(s2, 0) or 0) * (K - s2)
                    elif s2 > K:
                        pain += (poi.get(s2, 0) or 0) * (s2 - K)
                if best_pain is None or pain < best_pain:
                    best_pain, best_strike = pain, K

            return {
                "symbol": symbol, "spot": round(sp, 2), "expiry": exp, "days": days,
                "callWall": float(cw["strike"]), "callWallOI": int(cw["openInterest"] or 0),
                "putWall": float(pw["strike"]), "putWallOI": int(pw["openInterest"] or 0),
                "atmIV": round(atm_iv, 4) if atm_iv else None,
                "expMove": round(exp_move, 2) if exp_move else None,
                "emUpper": round(sp + exp_move, 2) if exp_move else None,
                "emLower": round(sp - exp_move, 2) if exp_move else None,
                "maxPain": best_strike,
            }
        except Exception as e:
            return {"symbol": symbol, "error": str(e)[:120]}

    return await asyncio.to_thread(_sync)


async def _options_levels_marketdata(symbol: str, spot: float | None = None,
                                     window: float = 0.30) -> dict | None:
    """Same 上限/下限 metrics as the yfinance version but sourced from
    marketdata.app, which has reliable open interest. Picks the nearest
    standard MONTHLY expiry (3rd Friday), computed locally so it costs ONE
    request per symbol, and caps the response with strikeLimit to stay light on
    the free tier. Returns {error:...} on any failure so the dispatcher can
    fall back to yfinance."""
    import math
    from datetime import datetime, timezone, timedelta
    if not MARKETDATA_TOKEN:
        return None

    def _third_friday(y, m):
        d = datetime(y, m, 1)
        return d + timedelta(days=(4 - d.weekday()) % 7 + 14)   # 1st Friday + 14

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    exp_dt = None
    for add in range(0, 4):
        y = now.year + (now.month - 1 + add) // 12
        m = (now.month - 1 + add) % 12 + 1
        tf = _third_friday(y, m)
        if (tf - now).days >= 4:
            exp_dt = tf
            break
    if exp_dt is None:
        return {"symbol": symbol, "error": "无法确定月度到期"}
    exp = exp_dt.strftime("%Y-%m-%d")

    global _MD_BLOCKED_UNTIL
    if time.time() < _MD_BLOCKED_UNTIL:
        return {"symbol": symbol, "error": _md_blocked_msg(), "quota": True}
    try:
        async with _MD_SEM:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(
                    f"https://api.marketdata.app/v1/options/chain/{symbol}/",
                    params={"expiration": exp, "strikeLimit": 50},
                    headers={"Authorization": f"Bearer {MARKETDATA_TOKEN}"},
                )
        try:
            remaining = int(r.headers.get("x-api-ratelimit-remaining", "-1"))
            reset = float(r.headers.get("x-api-ratelimit-reset", "0") or 0)
        except ValueError:
            remaining, reset = -1, 0.0
        MD_QUOTA.update({"remaining": remaining, "reset": reset, "limit": r.headers.get("x-api-ratelimit-limit")})
        if r.status_code == 429 or remaining == 0:
            # Free tier = 100 requests/day, resetting at the US open. Stop
            # calling until the reset instead of burning retries.
            _MD_BLOCKED_UNTIL = reset or (time.time() + 3600)
        if r.status_code == 429:
            return {"symbol": symbol, "error": _md_blocked_msg(), "quota": True}
        if r.status_code in (204, 404):
            return {"symbol": symbol, "error": "无期权", "expiry": exp}
        if r.status_code not in (200, 203):
            return {"symbol": symbol, "error": f"marketdata HTTP {r.status_code}"}
        j = r.json()
        if j.get("s") != "ok":
            return {"symbol": symbol, "error": f"marketdata: {j.get('errmsg') or j.get('s')}"}

        strikes = j.get("strike") or []
        sides = j.get("side") or []
        ois = j.get("openInterest") or []
        ivs = j.get("iv") or [None] * len(strikes)
        upx = j.get("underlyingPrice") or []
        sp = spot or (float(upx[0]) if upx else None)

        calls, puts = [], []
        for i in range(len(strikes)):
            iv = float(ivs[i]) if (i < len(ivs) and ivs[i] not in (None, "")) else None
            row = (float(strikes[i]), int(ois[i] or 0), iv)
            (calls if sides[i] == "call" else puts).append(row)
        if not sp:
            allk = sorted(k for k, _, _ in calls) or sorted(k for k, _, _ in puts)
            sp = allk[len(allk) // 2] if allk else None
        if not sp:
            return {"symbol": symbol, "error": "无现价"}

        total_oi = sum(o for _, o, _ in calls) + sum(o for _, o, _ in puts)
        if total_oi < 100:
            return {"symbol": symbol, "error": "OI 数据暂缺(marketdata)", "expiry": exp}

        lo, hi = sp * (1 - window), sp * (1 + window)
        cw_c = [(k, o) for k, o, _ in calls if sp <= k <= hi] or [(k, o) for k, o, _ in calls]
        pw_c = [(k, o) for k, o, _ in puts if lo <= k <= sp] or [(k, o) for k, o, _ in puts]
        cwk, cwoi = max(cw_c, key=lambda x: x[1])
        pwk, pwoi = max(pw_c, key=lambda x: x[1])

        def _atm_iv(lst):
            best = None
            for k, _, iv in lst:
                if iv and 0 < iv < 5 and (best is None or abs(k - sp) < abs(best[0] - sp)):
                    best = (k, iv)
            return best[1] if best else None
        ivv = [v for v in (_atm_iv(calls), _atm_iv(puts)) if v]
        atm_iv = sum(ivv) / len(ivv) if ivv else None

        days = max((exp_dt - now).days, 1)
        exp_move = (sp * atm_iv * math.sqrt(days / 365.0)) if atm_iv else None

        grid = sorted(({k for k, _, _ in calls} | {k for k, _, _ in puts}))
        grid = [k for k in grid if lo <= k <= hi]
        coi = {k: o for k, o, _ in calls}
        poi = {k: o for k, o, _ in puts}
        best_strike, best_pain = None, None
        for K in grid:
            pain = (sum(coi.get(s2, 0) * (K - s2) for s2 in grid if s2 < K)
                    + sum(poi.get(s2, 0) * (s2 - K) for s2 in grid if s2 > K))
            if best_pain is None or pain < best_pain:
                best_pain, best_strike = pain, K

        return {
            "symbol": symbol, "spot": round(sp, 2), "expiry": exp, "days": days,
            "callWall": cwk, "callWallOI": cwoi, "putWall": pwk, "putWallOI": pwoi,
            "atmIV": round(atm_iv, 4) if atm_iv else None,
            "expMove": round(exp_move, 2) if exp_move else None,
            "emUpper": round(sp + exp_move, 2) if exp_move else None,
            "emLower": round(sp - exp_move, 2) if exp_move else None,
            "maxPain": best_strike, "source": "marketdata",
        }
    except Exception as e:
        return {"symbol": symbol, "error": f"marketdata 异常: {str(e)[:80]}"}


_MD_BLOCKED_UNTIL = 0.0
MD_QUOTA: dict = {}
_MD_SEM = asyncio.Semaphore(3)          # no request bursts against marketdata.app


def _md_blocked_msg() -> str:
    from datetime import datetime
    if _MD_BLOCKED_UNTIL:
        t = datetime.fromtimestamp(_MD_BLOCKED_UNTIL).strftime("%H:%M")
        return f"marketdata 今日 100 次额度已用完，{t}（伦敦）重置后自动恢复"
    return "marketdata 额度已用完"


def _sanitize_iv(lv: dict | None) -> dict | None:
    """Guard the options feed against data artefacts:
    · walls whose open interest is 0 are not walls (yfinance returns all-zero
      OI intermittently) → report 数据暂缺 instead of drawing a fake level;
    · an ATM IV under 5% annualised (yfinance off-hours) produces a nonsense
      expected-move band glued to spot → keep the walls, drop the band."""
    if lv and not lv.get("error"):
        if not (lv.get("callWallOI") or 0) or not (lv.get("putWallOI") or 0):
            return {"symbol": lv.get("symbol"), "expiry": lv.get("expiry"),
                    "error": "OI 数据暂缺（回退源没有未平仓数据）"}
        iv = lv.get("atmIV")
        if iv is not None and iv < 0.05:
            lv = dict(lv, atmIV=None, expMove=None, emUpper=None, emLower=None, ivSuspect=True)
    return lv


async def _options_levels(symbol: str, spot: float | None = None,
                          window: float = 0.30) -> dict | None:
    """Dispatcher: prefer marketdata.app (reliable OI) when a token is set;
    fall back to yfinance otherwise, or if marketdata errors out."""
    if MARKETDATA_TOKEN:
        md = await _options_levels_marketdata(symbol, spot, window)
        if md and not md.get("error"):
            return _sanitize_iv(md)
        yb = _sanitize_iv(await _options_levels_yf(symbol, spot, window))
        if yb and not yb.get("error"):
            return yb
        return md or yb
    return _sanitize_iv(await _options_levels_yf(symbol, spot, window))


# --- Finnhub: free OHLC + news + quote (60 req/min on free tier) ---

FINNHUB_BASE = "https://finnhub.io/api/v1"


class _RateLimiter:
    """Sliding-window limiter: at most `rate` calls per `per` seconds. The
    Finnhub free tier (60/min) is shared by quotes, day changes, calendar,
    news and profiles — queue instead of getting 429'd."""

    def __init__(self, rate: int, per: float) -> None:
        from collections import deque
        self.rate, self.per = rate, per
        self.calls = deque()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                while self.calls and now - self.calls[0] >= self.per:
                    self.calls.popleft()
                if len(self.calls) < self.rate:
                    self.calls.append(now)
                    return
                await asyncio.sleep(self.per - (now - self.calls[0]) + 0.05)


_FINNHUB_RL = _RateLimiter(55, 60.0)


async def _finnhub_get(path: str, params: dict) -> dict | None:
    if not FINNHUB_KEY:
        return None
    p = {**params, "token": FINNHUB_KEY}
    await _FINNHUB_RL.acquire()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{FINNHUB_BASE}{path}", params=p,
                              headers={"User-Agent": UA})
    if r.status_code != 200:
        return {"_finnhub_error": f"HTTP {r.status_code}: {r.text[:200]}"}
    return r.json()


async def _finnhub_candle(symbol: str, days: int = 180) -> dict | None:
    """Daily OHLC for the last `days` days. Returns same shape as _stooq_chart."""
    to_ts = int(time.time())
    from_ts = to_ts - days * 86400
    data = await _finnhub_get("/stock/candle",
                               {"symbol": symbol, "resolution": "D",
                                "from": from_ts, "to": to_ts})
    if not data or data.get("s") != "ok":
        return None
    t = data.get("t") or []
    o = data.get("o") or []
    h = data.get("h") or []
    l_ = data.get("l") or []
    c = data.get("c") or []
    v = data.get("v") or []
    bars = []
    for i in range(len(t)):
        if i >= len(c) or c[i] is None:
            continue
        bars.append({"t": int(t[i]),
                     "o": round(o[i], 4), "h": round(h[i], 4),
                     "l": round(l_[i], 4), "c": round(c[i], 4),
                     "v": v[i] if i < len(v) else 0})
    return {"meta": {"symbol": symbol, "source": "finnhub"}, "bars": bars}


async def _finnhub_quote(symbol: str) -> dict | None:
    """Current quote: c (current), pc (prev close), d (change), dp (change%)."""
    data = await _finnhub_get("/quote", {"symbol": symbol})
    if not data or "_finnhub_error" in data:
        return None
    if data.get("c") in (None, 0) and data.get("pc") in (None, 0):
        return None  # symbol unknown
    # Record price point for the rolling 24h log used by /api/alerts
    try:
        _record_price(symbol, float(data.get("c") or 0))
    except Exception:
        pass
    return data


# ============================================================================
# Rolling 24h price log + rule-based alert detection
# ============================================================================

PRICE_LOG_FILE = HERE / "price_log.json"

ALERT_THRESHOLDS = {
    "1h":    4.0,    # |change| over rolling 1h window
    "today": 10.0,   # |change| since previous close
    "swing": 6.0,    # today's (high - low) / prev_close
}


_PRICE_LOG: dict | None = None      # in-memory; flushed to disk at most once a minute
_PRICE_LOG_FLUSHED = 0.0


def _load_price_log() -> dict:
    global _PRICE_LOG
    if _PRICE_LOG is None:
        try:
            _PRICE_LOG = json.loads(PRICE_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            _PRICE_LOG = {}
    return _PRICE_LOG


def _save_price_log(d: dict, force: bool = False) -> None:
    global _PRICE_LOG_FLUSHED
    if not force and time.time() - _PRICE_LOG_FLUSHED < 60:
        return
    _PRICE_LOG_FLUSHED = time.time()
    try:
        PRICE_LOG_FILE.write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


def _record_price(symbol: str, price: float) -> None:
    """Append (timestamp, price) to per-symbol log; trim to last 24h.
    (Used to re-read + re-write the whole ~0.5MB file on EVERY quote.)"""
    if not symbol or not price or price <= 0:
        return
    log = _load_price_log()
    ts = int(time.time())
    arr = log.get(symbol) or []
    if arr and ts - arr[-1][0] < 30:
        return   # skip if last sample is < 30s old
    arr.append([ts, float(price)])
    cutoff = ts - 86400
    if arr[0][0] < cutoff:
        arr = [p for p in arr if p[0] >= cutoff]
    log[symbol] = arr
    _save_price_log(log)


@app.on_event("shutdown")
async def _flush_price_log() -> None:
    if _PRICE_LOG is not None:
        _save_price_log(_PRICE_LOG, force=True)


def _short_ticker(t212_ticker: str) -> str:
    m = re.match(r"^[A-Z]+", t212_ticker or "")
    return m.group(0) if m else (t212_ticker or "")


def _humanize_age(seconds: int) -> str:
    if seconds < 60: return "just now"
    if seconds < 3600: return f"{seconds // 60}min ago"
    if seconds < 86400: return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


async def compute_active_alerts(tickers_t212: list) -> list:
    """Return currently-active alerts based on the latest price log + quote data."""
    log = _load_price_log()
    alerts: list = []
    now_ts = int(time.time())

    for t212t in tickers_t212:
        yh = t212_to_yahoo(t212t)
        short = _short_ticker(t212t)

        # Get cached quote (no new fetch — piggybacks on snapshot's calls)
        q = None
        try:
            q = await cache.get(f"fh_quote:{yh}", _quote_ttl(60.0), lambda: _finnhub_quote(yh))
        except Exception:
            q = None

        # Rule 1: rolling 1h change from price log
        arr = log.get(yh) or []
        if len(arr) >= 2:
            latest_ts, latest_px = arr[-1]
            window_start = latest_ts - 3600
            anchor = next(((ts, px) for ts, px in arr if ts >= window_start), None)
            if anchor and anchor[0] != latest_ts and anchor[1] > 0:
                change = (latest_px - anchor[1]) / anchor[1] * 100
                age_min = max(1, (latest_ts - anchor[0]) // 60)
                if abs(change) >= ALERT_THRESHOLDS["1h"]:
                    label = f"{age_min}min" if age_min < 60 else "1h"
                    alerts.append({
                        "ticker": short, "rule": "1h",
                        "change": round(change, 2),
                        "msg": f"{short} {change:+.2f}% in {label}",
                        "ts": latest_ts,
                    })

        # Rule 2: today change vs previous close (from Finnhub quote)
        if q and q.get("dp") is not None:
            dp = float(q["dp"])
            if abs(dp) >= ALERT_THRESHOLDS["today"]:
                alerts.append({
                    "ticker": short, "rule": "today",
                    "change": round(dp, 2),
                    "msg": f"{short} {dp:+.2f}% today",
                    "ts": int(q.get("t") or now_ts),
                })

        # Rule 3: intraday swing (today's high-low range vs prev close)
        if q and q.get("h") and q.get("l") and q.get("pc"):
            try:
                swing = (float(q["h"]) - float(q["l"])) / float(q["pc"]) * 100
                if swing >= ALERT_THRESHOLDS["swing"]:
                    alerts.append({
                        "ticker": short, "rule": "swing",
                        "change": round(swing, 2),
                        "msg": f"{short} swung {swing:.2f}% today (H ${q['h']:.2f} / L ${q['l']:.2f})",
                        "ts": int(q.get("t") or now_ts),
                    })
            except (TypeError, ValueError):
                pass

    # Sort by absolute magnitude descending
    alerts.sort(key=lambda a: -abs(a.get("change", 0)))
    return alerts


ALERT_QUEUE_FILE = HERE / "alert_queue.json"
ALERT_DEDUP_SEC = 1800   # 30 min — don't re-fire the same (ticker, rule) within this window
ALERT_RETAIN_SEC = 86400  # keep last 24h of fired alerts


def _load_alert_queue() -> list:
    if not ALERT_QUEUE_FILE.exists():
        return []
    try:
        d = json.loads(ALERT_QUEUE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_alert_queue(q: list) -> None:
    try:
        ALERT_QUEUE_FILE.write_text(
            json.dumps(q, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _merge_active_into_queue(active: list) -> list:
    """Add newly-fired alerts to the persistent queue with dedup."""
    queue = _load_alert_queue()
    now_ts = int(time.time())
    for a in active:
        # Skip if same (ticker, rule) was queued in the dedup window
        dup = any(
            q.get("ticker") == a["ticker"] and q.get("rule") == a["rule"]
            and now_ts - int(q.get("fired_at", 0)) < ALERT_DEDUP_SEC
            for q in queue
        )
        if not dup:
            entry = dict(a)
            entry["fired_at"] = now_ts
            queue.append(entry)
    # Retain last 24h, cap at 100 entries
    cutoff = now_ts - ALERT_RETAIN_SEC
    queue = [q for q in queue if int(q.get("fired_at", 0)) >= cutoff]
    queue = queue[-100:]
    queue.sort(key=lambda q: -int(q.get("fired_at", 0)))
    _save_alert_queue(queue)
    return queue


@app.get("/api/alerts")
async def alerts_endpoint():
    """Returns the persistent alert queue (24h rolling, deduped 30min per ticker+rule).

    On each call, also samples the current state and merges any newly-fired
    triggers into the queue.
    """
    try:
        snap = await snapshot_cached()
        tickers = [p["ticker"] for p in (snap.get("positions") or [])]
    except Exception:
        tickers = []

    active = await compute_active_alerts(tickers)
    queue = _merge_active_into_queue(active)
    log = _load_price_log()

    return {
        "alerts": queue,
        "active_count": len(active),
        "samples_per_ticker": {
            _short_ticker(t): len(log.get(t212_to_yahoo(t)) or [])
            for t in tickers
        },
        "fetchedAt": time.time(),
        "thresholds": ALERT_THRESHOLDS,
        "dedup_window_sec": ALERT_DEDUP_SEC,
    }


async def _finnhub_news(symbol: str, days: int = 7) -> list:
    """Recent company news. Returns up to ~10 items."""
    from datetime import date, timedelta
    today = date.today()
    frm = (today - timedelta(days=days)).isoformat()
    to = today.isoformat()
    data = await _finnhub_get("/company-news",
                               {"symbol": symbol, "from": frm, "to": to})
    if not data or not isinstance(data, list):
        return []
    out = []
    for n in data[:8]:
        out.append({
            "title": n.get("headline"),
            "publisher": n.get("source"),
            "providerPublishTime": n.get("datetime"),
            "summary": (n.get("summary") or "")[:200],
            "link": n.get("url"),
            "category": n.get("category"),
        })
    return out


# Yahoo chart kept around as a last-resort fallback (mostly disabled due to 429s).
async def _yahoo_chart(symbol: str, range_: str = "6mo", interval: str = "1d") -> dict | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": range_, "interval": interval}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params, headers={"User-Agent": UA})
    if r.status_code != 200:
        return None
    data = r.json()
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        return None
    res = result[0]
    ts = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    o = quote.get("open") or []; h = quote.get("high") or []
    lo = quote.get("low") or []; c = quote.get("close") or []
    v = quote.get("volume") or []
    bars = []
    for i in range(len(ts)):
        if i >= len(c) or c[i] is None:
            continue
        bars.append({"t": ts[i],
                     "o": round(o[i], 4) if i < len(o) and o[i] is not None else None,
                     "h": round(h[i], 4) if i < len(h) and h[i] is not None else None,
                     "l": round(lo[i], 4) if i < len(lo) and lo[i] is not None else None,
                     "c": round(c[i], 4),
                     "v": v[i] if i < len(v) else None})
    return {"meta": res.get("meta") or {}, "bars": bars}


async def _yahoo_news(symbol: str, count: int = 6) -> list:
    url = "https://query1.finance.yahoo.com/v1/finance/search"
    params = {"q": symbol, "newsCount": count, "quotesCount": 0,
              "enableFuzzyQuery": "false"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params, headers={"User-Agent": YAHOO_UA})
    if r.status_code != 200:
        return []
    items = (r.json() or {}).get("news") or []
    out = []
    for n in items[:count]:
        out.append({
            "title": n.get("title"),
            "publisher": n.get("publisher"),
            "providerPublishTime": n.get("providerPublishTime"),
            "type": n.get("type"),
            "link": n.get("link"),
            "relatedTickers": n.get("relatedTickers"),
        })
    return out


# --- Indicator math (no external deps) ---

def _sma(values: list, n: int):
    if len(values) < n:
        return None
    return round(sum(values[-n:]) / n, 4)


def _ema_series(values: list, n: int) -> list:
    if len(values) < n:
        return []
    out = [None] * (n - 1)
    e = sum(values[:n]) / n
    out.append(e)
    k = 2 / (n + 1)
    for v in values[n:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def _macd(closes: list):
    if len(closes) < 35:
        return None, None, None
    e12 = _ema_series(closes, 12)
    e26 = _ema_series(closes, 26)
    macd_line = []
    for i in range(len(closes)):
        a = e12[i] if i < len(e12) else None
        b = e26[i] if i < len(e26) else None
        macd_line.append(a - b if (a is not None and b is not None) else None)
    valid = [v for v in macd_line if v is not None]
    if len(valid) < 9:
        return None, None, None
    sig = _ema_series(valid, 9)
    if not sig or sig[-1] is None:
        return None, None, None
    macd_now = macd_line[-1]
    sig_now = sig[-1]
    return round(macd_now, 4), round(sig_now, 4), round(macd_now - sig_now, 4)


def _stdev(values: list) -> float | None:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return (sum((v - m) ** 2 for v in values) / len(values)) ** 0.5


def _bollinger(closes: list, n: int = 20, k: float = 2.0):
    """Bollinger Bands: returns dict with middle, upper, lower, percent_b, bandwidth."""
    if len(closes) < n:
        return None
    window = closes[-n:]
    middle = sum(window) / n
    std = _stdev(window)
    if std is None:
        return None
    upper = middle + k * std
    lower = middle - k * std
    last = closes[-1]
    percent_b = (last - lower) / (upper - lower) if upper != lower else 0.5
    bandwidth = (upper - lower) / middle if middle else 0
    return {
        "middle":   round(middle, 4),
        "upper":    round(upper, 4),
        "lower":    round(lower, 4),
        "percent_b": round(percent_b, 4),
        "bandwidth": round(bandwidth, 4),
    }


def _rsi(closes: list, n: int = 14):
    if len(closes) < n + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / n
    avg_loss = losses / n
    for i in range(n + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        g = max(diff, 0)
        l_ = max(-diff, 0)
        avg_gain = (avg_gain * (n - 1) + g) / n
        avg_loss = (avg_loss * (n - 1) + l_) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


async def fetch_market_for_ticker(t212_ticker: str) -> dict:
    """Pull chart + indicators + news for one ticker.
    OHLC: Stooq (primary, no rate limit) → Yahoo (fallback).
    News: Yahoo Finance (tolerates 429 by returning empty list).
    Best-effort: never raises; errors go into the output dict.
    """
    yh = t212_to_yahoo(t212_ticker)
    out: dict = {"t212_ticker": t212_ticker, "yahoo_symbol": yh}

    # --- Finnhub real-time quote (works on free tier) ---
    try:
        q = await cache.get(f"fh_quote:{yh}", _quote_ttl(60.0), lambda: _finnhub_quote(yh))
        if q:
            out["quote"] = {
                "price": q.get("c"),
                "prev_close": q.get("pc"),
                "day_change": q.get("d"),
                "day_change_pct": q.get("dp"),
                "day_open": q.get("o"),
                "day_high": q.get("h"),
                "day_low": q.get("l"),
                "ts": q.get("t"),
                "source": "finnhub",
            }
    except Exception as e:
        out["quote_error"] = str(e)

    # --- OHLC chart ---
    # Order: yfinance (free, no key, most reliable) → Finnhub candle (paid)
    # → Stooq → raw Yahoo (last resort, usually 429s).
    try:
        chart = await cache.get(f"yf_chart:{yh}", 1800.0,
                                lambda: _yfinance_chart(yh, "1y"))
        if not chart or not chart.get("bars"):
            chart = await cache.get(f"fh_candle:{yh}", 600.0,
                                    lambda: _finnhub_candle(yh, days=180))
        if not chart or not chart.get("bars"):
            chart = await cache.get(f"stooq_chart:{t212_ticker}", 900.0,
                                    lambda: _stooq_chart(t212_ticker, range_days=180))
        if not chart or not chart.get("bars"):
            chart = await cache.get(f"yh_chart:{yh}", 300.0,
                                    lambda: _yahoo_chart(yh, "6mo", "1d"))
        if not chart or not chart.get("bars"):
            out["chart_error"] = "no OHLC available from any source (yfinance/Finnhub/Stooq/Yahoo)"
        else:
            bars = chart["bars"]
            closes = [b["c"] for b in bars if b["c"] is not None]
            meta = chart.get("meta", {}) or {}
            m, s, h = _macd(closes)
            out["data_source"] = meta.get("source") or "yahoo"
            out["meta"] = {
                "symbol": meta.get("symbol"),
                "currency": meta.get("currency"),
                "exchangeName": meta.get("exchangeName"),
                "regularMarketPrice": meta.get("regularMarketPrice"),
                "previousClose": meta.get("chartPreviousClose"),
                "fiftyTwoWeekHigh": meta.get("fiftyTwoWeekHigh"),
                "fiftyTwoWeekLow": meta.get("fiftyTwoWeekLow"),
            }
            out["bars_count"] = len(bars)
            out["last_bar_ts"] = bars[-1]["t"]
            out["last_close"] = bars[-1]["c"]
            out["last_volume"] = bars[-1]["v"]
            out["window_high"] = max((b["h"] for b in bars if b["h"] is not None), default=None)
            out["window_low"]  = min((b["l"] for b in bars if b["l"] is not None), default=None)
            # Bollinger Bands (20-period, 2 std)
            bb = _bollinger(closes, 20, 2.0)

            # Explicit support/resistance candidates from recent ranges
            last_n_highs = lambda n: max((b["h"] for b in bars[-n:] if b["h"] is not None), default=None)
            last_n_lows  = lambda n: min((b["l"] for b in bars[-n:] if b["l"] is not None), default=None)

            cur = bars[-1]["c"]
            ranges = {
                "high_20d":  last_n_highs(20),
                "low_20d":   last_n_lows(20),
                "high_60d":  last_n_highs(60),
                "low_60d":   last_n_lows(60),
                "high_252d": last_n_highs(252),
                "low_252d":  last_n_lows(252),
            }
            # Distances from key levels (% from current price)
            def _pct_from(level):
                if not level or not cur: return None
                return round((cur - level) / level * 100, 2)
            distances = {
                "from_high_20d_pct":  _pct_from(ranges["high_20d"]),
                "from_low_20d_pct":   _pct_from(ranges["low_20d"]),
                "from_high_60d_pct":  _pct_from(ranges["high_60d"]),
                "from_low_60d_pct":   _pct_from(ranges["low_60d"]),
                "from_high_252d_pct": _pct_from(ranges["high_252d"]),
                "from_low_252d_pct":  _pct_from(ranges["low_252d"]),
            }

            out["indicators"] = {
                "ma20":        _sma(closes, 20),
                "ma50":        _sma(closes, 50),
                "ma200":       _sma(closes, 200),
                "rsi14":       _rsi(closes, 14),
                "macd":        m,
                "macd_signal": s,
                "macd_hist":   h,
                "bollinger":   bb,        # {middle, upper, lower, percent_b, bandwidth}
                "ranges":      ranges,    # high/low over 20/60/252-day windows
                "distances":   distances, # % from those highs/lows
            }
            out["recent_bars_30"] = bars[-30:]
    except Exception as e:
        out["chart_error"] = f"chart fetch failed: {e}"

    # --- News (Finnhub primary, Yahoo fallback) ---
    try:
        news = await cache.get(f"fh_news:{yh}", 1800.0,
                                lambda: _finnhub_news(yh, days=7))
        if not news:
            news = await cache.get(f"yh_news:{yh}", 600.0,
                                    lambda: _yahoo_news(yh, 6))
        out["news"] = news or []
    except Exception as e:
        out["news_error"] = str(e)
        out["news"] = []
    return out


def _ind_at(closes: list) -> dict:
    """Compute the trigger-relevant indicators for the last point of `closes`."""
    m, s, h = _macd(closes)
    return {
        "close": round(closes[-1], 4),
        "ma20":  _sma(closes, 20),
        "ma50":  _sma(closes, 50),
        "ma200": _sma(closes, 200),
        "rsi":   _rsi(closes),
        "macd":  m, "signal": s, "hist": h,
    }


def yahoo_chart_symbol(t212_or_sym: str) -> str:
    """Yahoo symbol for daily bars. US listings → bare ticker (with the
    de-SPAC/rename remap); T212's lowercase venue suffix → Yahoo exchange
    suffix (SGLNl_EQ → SGLN.L, ASMLa_EQ → ASML.AS). Plain symbols pass through."""
    t = (t212_or_sym or "").strip()
    m = re.fullmatch(r"([A-Z0-9.]+?)([a-z])_EQ", t)
    if m:
        suffix = {"l": ".L", "a": ".AS", "d": ".DE", "p": ".PA"}.get(m.group(2), "")
        return m.group(1) + suffix
    if "_" in t:
        return t212_to_yahoo(t)
    return TICKER_REMAP.get(t.upper(), t.upper())


def _is_us_symbol(sym: str) -> bool:
    return bool(sym) and "." not in sym and "=" not in sym and not sym.startswith("^")


def _et_date(ts: int) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _bar_date(b: dict) -> str:
    """Session date of a daily bar. Bars are stamped at local midnight of
    their exchange (NY, London, …) — shifting by +12h before taking the UTC
    date maps any of those midnights onto the right calendar day."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(b["t"]) + 43200, timezone.utc).strftime("%Y-%m-%d")


async def get_daily_bars(t212_or_sym: str, period: str = "1y", live: bool = True) -> dict:
    """NaN-free daily OHLCV bars for any holding / symbol.

    Source chain: yfinance → Finnhub candle → Stooq → raw Yahoo. For US
    symbols the last bar is then reconciled with the 60s Finnhub quote, so
    indicators reflect the live session even when Yahoo's bar is stale or
    incomplete (the NaN-close case that used to 500 /api/tech)."""
    yh = yahoo_chart_symbol(t212_or_sym)
    days = {"6mo": 200, "1y": 400, "2y": 800, "5y": 2000}.get(period, 400)
    key = f"yf_chart:{yh}" if period == "1y" else f"yf_chart:{yh}:{period}"
    chart = await cache.get(key, 14400.0, lambda: _yfinance_chart(yh, period))
    if (not chart or not chart.get("bars")) and _is_us_symbol(yh):
        chart = await cache.get(f"fh_candle:{yh}:{days}", 600.0, lambda: _finnhub_candle(yh, days=days))
    if not chart or not chart.get("bars"):
        st = t212_or_sym if "_" in t212_or_sym else f"{yh}_US_EQ"
        chart = await cache.get(f"stooq_chart:{st}:{days}", 900.0, lambda: _stooq_chart(st, range_days=days))
    if not chart or not chart.get("bars"):
        chart = await cache.get(f"yh_chart:{yh}:{period}", 300.0, lambda: _yahoo_chart(yh, period, "1d"))
    if not chart or not chart.get("bars"):
        return {"symbol": yh, "bars": [], "source": None, "error": "no OHLC from any source"}

    ok = lambda v: isinstance(v, (int, float)) and v == v and v > 0
    bars = [dict(b) for b in chart["bars"] if ok(b.get("c"))]
    for b in bars:                                   # patch holes in o/h/l with the close
        for k in ("o", "h", "l"):
            if not ok(b.get(k)):
                b[k] = b["c"]
    meta = chart.get("meta") or {}
    patched = None
    if live and bars and _is_us_symbol(yh):
        try:
            q = await cache.get(f"fh_quote:{yh}", _quote_ttl(60.0), lambda: _finnhub_quote(yh))
        except Exception:
            q = None
        if q and ok(q.get("c")) and q.get("t"):
            qd, ld = _et_date(int(q["t"])), _bar_date(bars[-1])
            c = float(q["c"])
            if qd == ld:
                last = bars[-1]
                if abs(last["c"] - c) > 1e-9:
                    last["c"] = c
                    last["h"] = max(last["h"], c)
                    last["l"] = min(last["l"], c)
                    patched = "updated"
            elif qd > ld:
                tail = meta.get("nan_tail") or {}
                o = tail.get("o") or q.get("o") or c
                h = max(v for v in (tail.get("h"), q.get("h"), c) if ok(v))
                l_ = min(v for v in (tail.get("l"), q.get("l"), c) if ok(v))
                from datetime import datetime
                from zoneinfo import ZoneInfo
                d0 = datetime.strptime(qd, "%Y-%m-%d").replace(tzinfo=ZoneInfo("America/New_York"))
                bars.append({"t": int(d0.timestamp()), "o": round(float(o), 4), "h": round(float(h), 4),
                             "l": round(float(l_), 4), "c": round(c, 4), "v": tail.get("v") or 0})
                patched = "appended"
    return {"symbol": yh, "bars": bars, "source": meta.get("source") or "yahoo", "patched": patched}


def _atr(bars: list, n: int = 14) -> float | None:
    """Wilder's Average True Range over the last `n` bars."""
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l_, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l_, abs(h - pc), abs(l_ - pc)))
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return round(atr, 4)


async def compute_trigger_state(t212_ticker: str) -> dict:
    """Lightweight feed for the technical-trigger monitor (tech_monitor.py).

    Returns TODAY's and YESTERDAY's indicator values (so the monitor can detect
    day-over-day transitions like a fresh MACD death cross or an MA20 break),
    plus tails of the close + MACD-line series for divergence checks. Reuses the
    server's warm, cached OHLC fetch chain — same math as the 技术面 panel.
    """
    d = await get_daily_bars(t212_ticker, "1y")
    bars = d["bars"]
    short = _short_ticker(t212_ticker) or t212_ticker
    if not bars:
        return {"ticker": short, "error": d.get("error") or "no OHLC from any source"}
    closes = [b["c"] for b in bars]
    vols = [(b.get("v") or 0) for b in bars]
    if len(closes) < 36:
        return {"ticker": short, "error": f"only {len(closes)} bars (need 36+)"}

    e12 = _ema_series(closes, 12)
    e26 = _ema_series(closes, 26)
    macd_series = [(a - b) if (a is not None and b is not None) else None
                   for a, b in zip(e12, e26)]
    avg_vol20 = round(sum(vols[-21:-1]) / 20, 0) if len(vols) >= 21 else None
    hi20 = max((b["h"] for b in bars[-20:]), default=None)
    lo20 = min((b["l"] for b in bars[-20:]), default=None)
    hi252 = max((b["h"] for b in bars[-252:]), default=None)
    lo252 = min((b["l"] for b in bars[-252:]), default=None)
    atr14 = _atr(bars[-60:], 14)
    ret = lambda n: round((closes[-1] / closes[-1 - n] - 1) * 100, 2) if len(closes) > n and closes[-1 - n] else None

    return {
        "ticker":     short,
        "yahoo":      d["symbol"],
        "price":      round(closes[-1], 4),
        "today":      _ind_at(closes),
        "prev":       _ind_at(closes[:-1]),
        "bollinger":  _bollinger(closes, 20, 2.0),
        "avg_vol20":  avg_vol20,
        "last_vol":   vols[-1],
        "high_20d":   round(hi20, 4) if hi20 else None,
        "low_20d":    round(lo20, 4) if lo20 else None,
        "high_252d":  round(hi252, 4) if hi252 else None,
        "low_252d":   round(lo252, 4) if lo252 else None,
        "atr14":      atr14,
        "atr_pct":    round(atr14 / closes[-1] * 100, 2) if atr14 else None,
        "ret_5d":     ret(5), "ret_21d": ret(21), "ret_63d": ret(63),
        "closes_tail": [round(c, 4) for c in closes[-70:]],
        "macd_tail":   [(round(x, 4) if x is not None else None) for x in macd_series[-70:]],
        "source":     d.get("source"),
        "live_patch": d.get("patched"),
        "last_bar":   _bar_date(bars[-1]),
        "ts":         int(time.time()),
    }


_LAST_POSITIONS: list = []          # refreshed by every snapshot — cheap short→T212 lookups


def resolve_t212(ticker: str) -> str:
    """"MU" → "MU_US_EQ" when held (either account); anything else unchanged."""
    t = (ticker or "").strip()
    if "_" in t:
        return t
    for p in _LAST_POSITIONS:
        if _short_ticker(p["ticker"]).upper() == t.upper() or \
           t212_to_yahoo(p["ticker"]).upper() == t.upper():
            return p["ticker"]
    return t


@app.get("/api/tech")
async def tech_endpoint(ticker: str = "MU_US_EQ"):
    """Read-only technical state for one ticker, for the trigger monitor."""
    t = (ticker or "").strip()
    if "_" not in t and not _LAST_POSITIONS:
        try:
            await snapshot_data()           # warm the ticker map once
        except Exception:
            pass
    t = resolve_t212(t)
    return _json_safe(await cache.get(f"tech:{t}", 20.0, lambda: compute_trigger_state(t)))


@app.get("/api/tech_batch")
async def tech_batch_endpoint(tickers: str = ""):
    """Compact technical state for many tickers at once (holdings table)."""
    syms = [resolve_t212(s) for s in tickers.split(",") if s.strip()][:40]

    async def _one(t):
        try:
            st = await cache.get(f"tech:{t}", 120.0, lambda: compute_trigger_state(t))
        except Exception as e:
            return t, {"error": str(e)[:80]}
        if st.get("error"):
            return t, {"error": st["error"]}
        td, pv, bb = st["today"], st["prev"], st.get("bollinger") or {}
        px = st["price"]
        pos = lambda ma: (None if not ma else round((px - ma) / ma * 100, 2))
        return t, {
            "price": px, "rsi": td.get("rsi"), "rsiPrev": pv.get("rsi"),
            "ma20": pos(td.get("ma20")), "ma50": pos(td.get("ma50")), "ma200": pos(td.get("ma200")),
            "macdBull": (td.get("macd") or 0) >= (td.get("signal") or 0),
            "hist": td.get("hist"), "histPrev": pv.get("hist"),
            "pctb": bb.get("percent_b"),
            "fromHigh": pos(st.get("high_252d")), "fromLow": pos(st.get("low_252d")),
            "atrPct": st.get("atr_pct"), "ret21": st.get("ret_21d"), "ret63": st.get("ret_63d"),
            "spark": st["closes_tail"][-30:],
        }

    pairs = await asyncio.gather(*[_one(t) for t in syms])
    return _json_safe({"tech": dict(pairs), "fetchedAt": time.time()})


async def refresh_market_data(positions: list) -> None:
    market_dir = CODEX_DATA / "market"
    market_dir.mkdir(parents=True, exist_ok=True)
    tasks = [fetch_market_for_ticker(p["ticker"]) for p in positions]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for p, res in zip(positions, results):
        ticker = p["ticker"]
        path = market_dir / f"{ticker}.json"
        if isinstance(res, Exception):
            res = {"t212_ticker": ticker, "error": str(res)}
        path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")


# Lightweight daily-change fetch for the dashboard treemap.
# Yahoo "spark" returns recent closes for many tickers in ONE call.
async def _yahoo_spark(symbols: list) -> dict:
    if not symbols:
        return {}
    url = "https://query1.finance.yahoo.com/v7/finance/spark"
    params = {"symbols": ",".join(symbols), "range": "5d", "interval": "1d"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, params=params, headers={"User-Agent": YAHOO_UA})
    if r.status_code != 200:
        return {}
    payload = r.json() or {}
    out = {}
    # Two response shapes; handle both.
    spark = (payload.get("spark") or {}).get("result") or []
    for item in spark:
        sym = item.get("symbol")
        resp = (item.get("response") or [{}])[0]
        meta = resp.get("meta") or {}
        prev = meta.get("chartPreviousClose")
        closes = (((resp.get("indicators") or {}).get("quote") or [{}])[0]
                  .get("close") or [])
        closes = [c for c in closes if c is not None]
        last = closes[-1] if closes else None
        if last is not None and prev:
            out[sym] = {
                "last": last,
                "prev": prev,
                "changePct": round((last - prev) / prev * 100, 3),
            }
    return out


async def get_day_changes(yh_symbols: list) -> dict:
    """Legacy Yahoo-spark version. Kept but no longer used (429-prone)."""
    if not yh_symbols:
        return {}
    key = "spark:" + ",".join(sorted(set(yh_symbols)))
    return await cache.get(key, 90.0, lambda: _yahoo_spark(sorted(set(yh_symbols))))


async def get_day_changes_stooq(t212_tickers: list) -> dict:
    """Legacy Stooq-based day-change. Kept as fallback."""
    out = {}
    async def _one(t212t):
        try:
            chart = await cache.get(f"stooq_chart:{t212t}", 900.0,
                                     lambda: _stooq_chart(t212t, range_days=10))
            if not chart or not chart.get("bars"):
                return t212t, None
            closes = [b["c"] for b in chart["bars"] if b["c"] is not None]
            if len(closes) < 2:
                return t212t, None
            prev, last = closes[-2], closes[-1]
            return t212t, {"last": last, "prev": prev,
                           "changePct": round((last - prev) / prev * 100, 3)}
        except Exception:
            return t212t, None
    pairs = await asyncio.gather(*[_one(t) for t in t212_tickers])
    for k, v in pairs:
        if v is not None:
            out[k] = v
    return out


async def get_day_changes_finnhub(t212_tickers: list) -> dict:
    """Day-change % via Finnhub /quote (1 call per ticker, very fast).
    Returns {t212_ticker: {"last": c, "prev": pc, "changePct": dp,
                            "dayChange": d, "high": h, "low": l, "open": o}}.
    Includes a bit more (intraday high/low/open) so the table can show richer info.
    """
    out = {}
    async def _one(t212t):
        yh = t212_to_yahoo(t212t)
        try:
            q = await cache.get(f"fh_quote:{yh}", _quote_ttl(60.0),
                                 lambda: _finnhub_quote(yh))
            if not q or q.get("c") in (None, 0):
                return t212t, None
            c = q.get("c"); pc = q.get("pc")
            dp = q.get("dp")
            if dp is None and c and pc:
                dp = round((c - pc) / pc * 100, 3)
            return t212t, {
                "last": c, "prev": pc, "changePct": dp,
                "dayChange": q.get("d"),
                "open": q.get("o"), "high": q.get("h"), "low": q.get("l"),
            }
        except Exception:
            return t212t, None
    pairs = await asyncio.gather(*[_one(t) for t in t212_tickers])
    for k, v in pairs:
        if v is not None:
            out[k] = v
    return out


async def _account_snapshot(a: T212Account, inst_map: dict) -> dict:
    """One account's positions (FX-normalised to the account currency, with
    day change) + its top-bar stats."""
    cash, info, portfolio = await asyncio.gather(get_cash(a), get_info_safe(a), get_portfolio(a))
    account_ccy = (info or {}).get("currencyCode") or "GBP"

    # Prefetch FX rates (instrument currency → account currency), cached 1h.
    # Prices are native (GBX/USD/…) but invested/marketValue must be in the
    # account currency, or the treemap sizes and pplPct are wrong.
    raw_positions = portfolio or []
    needed_ccys = {
        (inst_map.get(p.get("ticker"), {}).get("currencyCode") or account_ccy)
        for p in raw_positions
    }
    ccys = sorted(needed_ccys)
    rates = await asyncio.gather(*[cache.get(
        f"fx:{c}:{account_ccy}", 3600.0,
        lambda c=c: _fx_to_account(c, account_ccy)) for c in ccys])
    fx_map = {c: (r or 1.0) for c, r in zip(ccys, rates)}

    enriched = []
    for p in raw_positions:
        ticker = p.get("ticker")
        meta = inst_map.get(ticker, {})
        ccy = meta.get("currencyCode") or account_ccy
        fx = fx_map.get(ccy) or 1.0
        qty = float(p.get("quantity") or 0)
        avg = float(p.get("averagePrice") or 0)
        cur = float(p.get("currentPrice") or 0)
        ppl = float(p.get("ppl") or 0)          # already in account currency
        invested = qty * avg * fx               # → account currency
        mv = qty * cur * fx                      # → account currency
        pct = (ppl / invested * 100) if invested else 0
        enriched.append({
            "ticker": ticker,
            "name": meta.get("name") or ticker,
            "type": meta.get("type"),
            "currency": ccy,
            "quantity": qty, "averagePrice": avg, "currentPrice": cur,
            "invested": round(invested, 2), "marketValue": round(mv, 2),
            "ppl": ppl, "pplPct": pct,
            "initialFillDate": p.get("initialFillDate"),
            "accounts": [{"id": a.id, "label": a.label, "quantity": qty,
                          "marketValue": round(mv, 2)}],
        })
    enriched.sort(key=lambda x: x["marketValue"], reverse=True)

    # Enrich with today's % change via Finnhub /quote (1 call/ticker, cached 60s)
    try:
        tickers = [p["ticker"] for p in enriched]
        changes = await get_day_changes_finnhub(tickers)

        # For tickers Finnhub's free tier doesn't cover (e.g. LSE-listed ETFs
        # like SGLN), fall back to yfinance — cached 5 min per ticker so rapid
        # polls don't hammer Yahoo.
        async def _fill_day(p):
            d = changes.get(p["ticker"])
            if not d:
                d = await cache.get(
                    f"yf_day:{p['ticker']}", 300.0,
                    lambda p=p: _yfinance_day_change(p["ticker"], p.get("currency")),
                ) or {}
            p["dayChangePct"] = d.get("changePct")
            p["dayPrevClose"] = d.get("prev")
            p["dayHigh"] = d.get("high")
            p["dayLow"] = d.get("low")
            p["dayOpen"] = d.get("open")

        await asyncio.gather(*[_fill_day(p) for p in enriched])
    except Exception:
        for p in enriched:
            p.setdefault("dayChangePct", None)

    # Real today's P&L = mark-to-market move of holdings today, summed from
    # each position's day change. (NOT cash.result, which is realized all-time
    # P&L and doesn't move intraday.)
    today_pnl = 0.0
    for p in enriched:
        dc = p.get("dayChangePct")
        mv = p.get("marketValue") or 0
        if dc is not None and mv and (1 + dc / 100) != 0:
            today_pnl += mv - mv / (1 + dc / 100)

    stats = await compute_extended_stats(cash or {}, today_pnl=today_pnl, a=a)

    return {
        "id": a.id, "label": a.label, "env": a.env, "currency": account_ccy,
        "info": info, "cash": cash or {}, "positions": enriched, "stats": stats,
    }


async def _account_snapshot_safe(a: T212Account, inst_map: dict) -> dict:
    try:
        return await _account_snapshot(a, inst_map)
    except HTTPException as e:
        return {"id": a.id, "label": a.label, "env": a.env, "error": str(e.detail)}
    except Exception as e:   # noqa: BLE001 — one broken account must not blank the other
        return {"id": a.id, "label": a.label, "env": a.env, "error": f"{a.label}: {str(e)[:160]}"}


def _merge_positions(parts: list[dict]) -> list[dict]:
    """Same ticker held in several accounts → one row: summed quantity /
    value / P&L, quantity-weighted average price, per-account breakdown kept
    in `accounts`. Shape stays identical to a single-account position so the
    monitors and the Swift widget keep decoding it."""
    by: dict[str, dict] = {}
    for part in parts:
        for p in part["positions"]:
            t = p["ticker"]
            if t not in by:
                q = dict(p)
                q["accounts"] = list(p.get("accounts") or [])
                q["_cost"] = p["quantity"] * p["averagePrice"]
                by[t] = q
                continue
            q = by[t]
            q["quantity"] += p["quantity"]
            q["invested"] = round(q["invested"] + p["invested"], 2)
            q["marketValue"] = round(q["marketValue"] + p["marketValue"], 2)
            q["ppl"] += p["ppl"]
            q["_cost"] += p["quantity"] * p["averagePrice"]
            q["accounts"] += list(p.get("accounts") or [])
            if (p.get("initialFillDate") or "9") < (q.get("initialFillDate") or "9"):
                q["initialFillDate"] = p["initialFillDate"]
    out = []
    for q in by.values():
        cost = q.pop("_cost")
        if len(q["accounts"]) > 1:
            q["averagePrice"] = cost / q["quantity"] if q["quantity"] else 0.0
            q["pplPct"] = q["ppl"] / q["invested"] * 100 if q["invested"] else 0.0
        out.append(q)
    out.sort(key=lambda x: x["marketValue"], reverse=True)
    return out


_SUM_CASH_KEYS = ("free", "total", "ppl", "result", "invested", "pieCash", "blocked")
_SUM_STAT_KEYS = ("totalValue", "holdingsValue", "totalCash", "todayPnl", "unrealizedPnl",
                  "investedCost", "realizedTrading", "realizedPnl", "dividends", "interest",
                  "fxFees", "otherFees", "unexplained")


def _combine_stats(parts: list[dict]) -> dict:
    """Account-level stats → portfolio-level. Amounts add; percentages are
    recomputed on the combined base (never averaged)."""
    out = {k: round(sum((s.get(k) or 0) for s in parts), 2) for k in _SUM_STAT_KEYS}
    known = lambda k: all(s.get(k) is not None for s in parts)
    pct = lambda num, den: round(num / den * 100, 3) if den else None
    out["totalCost"] = round(sum(s["totalCost"] for s in parts), 2) if known("totalCost") else None
    out["allTimePnl"] = round(sum(s["allTimePnl"] for s in parts), 2) if known("allTimePnl") else None
    out["todayPnlPct"] = pct(out["todayPnl"], out["totalValue"] - out["todayPnl"])
    out["unrealizedPnlPct"] = pct(out["unrealizedPnl"], out["investedCost"])
    out["realizedPnlPct"] = pct(out["realizedPnl"], out["totalCost"] or 0) if known("totalCost") else None
    out["allTimePnlPct"] = pct(out["allTimePnl"], out["totalCost"]) if out["allTimePnl"] is not None else None
    if known("monthPnl") and known("monthSnapshotValue"):
        out["monthPnl"] = round(sum(s["monthPnl"] for s in parts), 2)
        out["monthPnlPct"] = pct(out["monthPnl"], sum(s["monthSnapshotValue"] for s in parts))
    else:
        out["monthPnl"] = out["monthPnlPct"] = None
    out["monthSnapshotDate"] = min((s.get("monthSnapshotDate") or "9") for s in parts)
    out["monthSnapshotValue"] = out["monthDeposits"] = None
    out["breakdownComplete"] = all(s.get("breakdownComplete") for s in parts)
    out["historySyncing"] = any(s.get("historySyncing") for s in parts)
    return out


async def snapshot_data(account: str | None = None) -> dict:
    """Portfolio snapshot. `account` = None/"all" merges every configured
    account (default — what the monitors and widgets read); an account id
    ("isa" / "invest") returns just that account, same shape."""
    global _LAST_POSITIONS
    maybe_reload_accounts()
    configured = [a for a in ACCOUNTS if a.configured]
    if not configured:
        raise HTTPException(500, "TRADING212_API_KEY_ID / TRADING212_API_SECRET 未在 .env 中设置")
    view = (account or "all").lower()
    if view != "all":
        a = get_account(view)
        if not a or not a.configured:
            raise HTTPException(404, f"未知或未配置的账户: {account}")
        targets = [a]
    else:
        targets = configured

    async def _warm(coro):          # prefetch in parallel; errors surface per account below
        try:
            await coro
        except Exception:
            pass
    instruments, *_ = await asyncio.gather(
        get_instruments(),
        *[_warm(f(a)) for a in targets for f in (get_cash, get_info_safe, get_portfolio)])
    inst_map = {i.get("ticker"): i for i in instruments} if isinstance(instruments, list) else {}
    parts = await asyncio.gather(*[_account_snapshot_safe(a, inst_map) for a in targets])
    good = [p for p in parts if not p.get("error")]
    if not good:
        errs = "；".join(p["error"] for p in parts)
        raise HTTPException(502, errs or "Trading 212 无响应")

    positions = _merge_positions(good)
    cash = {k: round(sum(float((p["cash"] or {}).get(k) or 0) for p in good), 2) for k in _SUM_CASH_KEYS}
    stats = dict(good[0]["stats"]) if len(good) == 1 else _combine_stats([p["stats"] for p in good])
    if view == "all":
        _LAST_POSITIONS = positions

    return {
        "env": good[0]["env"], "info": good[0]["info"], "cash": cash,
        "positions": positions, "fetchedAt": time.time(), "stats": stats,
        "account": view,
        "accounts": [{
            "id": p["id"], "label": p["label"], "env": p.get("env"),
            "ok": not p.get("error"), "error": p.get("error"),
            "currency": p.get("currency"),
            "totalValue": (p.get("stats") or {}).get("totalValue"),
            "todayPnl": (p.get("stats") or {}).get("todayPnl"),
            "positions": len(p.get("positions") or []),
        } for p in parts],
        "accountsAvailable": [{"id": a.id, "label": a.label} for a in configured],
    }


async def compute_extended_stats(cash: dict, today_pnl: float | None = None, a=None) -> dict:
    """Compute the top-bar metrics for ONE account.

    Logic (per user spec):
      总成本   = net deposits (all-time DEPOSIT − WITHDRAW ± TRANSFER from transactions)
      总价值   = cash.total (current account NAV)
      总持仓   = cash.invested + cash.ppl (mark-to-market of all holdings)
      总现金   = cash.free + cash.blocked + cash.pieCash
      今日收益 = Σ(holding day P&L) from day changes, % vs yesterday's NAV
      浮动收益 = cash.ppl, % vs cash.invested  (positions only)
      锁定收益 = 总收益 − 浮动收益 — i.e. everything already booked:
                 T212's realised trade P&L (cash.result) + dividends + interest
                 on cash − FX conversion fees − other fees. cash.result alone
                 omits the last four, which is why 锁定+浮动 didn't add up.
      本月收益 = (total now) − (earliest snapshot this month) − (net deposits since)
                 — needs ≥ 2 daily snapshots in the current month
      总收益   = total − net_deposits, % vs net_deposits
    """
    from datetime import date, datetime, timezone

    total = float(cash.get("total") or 0)
    invested = float(cash.get("invested") or 0)
    free = float(cash.get("free") or 0)
    blocked = float(cash.get("blocked") or 0)
    pie = float(cash.get("pieCash") or 0)
    ppl = float(cash.get("ppl") or 0)
    result = float(cash.get("result") or 0)

    # Today's P&L: prefer the real mark-to-market day move computed from each
    # holding's day change (passed in). cash.result is realized all-time P&L,
    # not today's change, so only fall back to it if no day data is available.
    day_pnl = today_pnl if today_pnl is not None else result
    today_pct = None
    base = total - day_pnl            # ≈ yesterday's NAV
    if base > 0:
        today_pct = round(day_pnl / base * 100, 3)
    unreal_pct = None
    if invested > 0:
        unreal_pct = round(ppl / invested * 100, 3)

    stats = {
        "totalCost": None,
        "totalValue": round(total, 2),
        "holdingsValue": round(invested + ppl, 2),
        "totalCash": round(free + blocked + pie, 2),
        "todayPnl": round(day_pnl, 2),
        "todayPnlPct": today_pct,
        "unrealizedPnl": round(ppl, 2),
        "unrealizedPnlPct": unreal_pct,
        "investedCost": round(invested, 2),
        "realizedTrading": round(result, 2),     # T212 cash.result — trade P&L only
        "realizedPnl": round(result, 2),         # replaced below by the reconciled figure
        "realizedPnlPct": None,
        "dividends": 0.0, "interest": 0.0, "fxFees": 0.0, "otherFees": 0.0, "unexplained": 0.0,
        "breakdownComplete": False, "historySyncing": False,
        "monthPnl": None,
        "monthPnlPct": None,
        "allTimePnl": None,
        "allTimePnlPct": None,
        # diagnostics for the UI hover
        "monthSnapshotDate": None,
        "monthSnapshotValue": None,
        "monthDeposits": None,
    }

    # Net deposits & monthly deposit window — the three history feeds hit
    # different T212 endpoints (separate rate limits), so fetch them together.
    async def _safe(coro):
        try:
            return await coro
        except Exception:
            return []
    all_txns, divs, fills = await asyncio.gather(
        _safe(get_all_transactions(a)),
        _safe(get_history("dividends", a, ttl=1800.0)),
        _safe(get_history("fills", a, ttl=300.0)))

    net_deposits = 0.0
    seen_any_deposit = False
    today_date = date.today()
    month_start = today_date.replace(day=1)

    deposits_after_month_start = 0.0
    for it in all_txns:
        flow = capital_flow(it)       # None → not contributed capital (interest, fees…)
        if flow is None:
            continue
        seen_any_deposit = True
        net_deposits += flow
        dt = _parse_dt(it.get("dateTime") or "")
        if dt and dt.date() >= month_start:
            deposits_after_month_start += flow

    if seen_any_deposit and net_deposits > 0:
        stats["totalCost"] = round(net_deposits, 2)
        gain = total - net_deposits
        stats["allTimePnl"] = round(gain, 2)
        stats["allTimePnlPct"] = round(gain / net_deposits * 100, 3)

    # ---- 锁定收益 reconciliation: total − deposits = result + ppl + divs + interest − fees
    interest = sum(float(it.get("amount") or 0) for it in all_txns if txn_kind(it) == "interest")
    other_fees = sum(float(it.get("amount") or 0) for it in all_txns if txn_kind(it) == "fee")
    dividends = sum(float(d.get("amount") or 0) for d in divs)
    fx_fees = 0.0            # every per-fill charge T212 lists (here: all FX conversion fees)
    for it in fills:
        for t in (((it.get("fill") or {}).get("walletImpact") or {}).get("taxes") or []):
            fx_fees += float(t.get("quantity") or 0)
    st_f, st_d, st_t = (history_status("fills", a), history_status("dividends", a),
                        history_status("transactions", a))
    stats.update({
        "dividends": round(dividends, 2), "interest": round(interest, 2),
        "fxFees": round(fx_fees, 2), "otherFees": round(other_fees, 2),
        "breakdownComplete": st_f["complete"] and st_d["complete"] and st_t["complete"],
        "historySyncing": st_f["syncing"] or st_d["syncing"] or st_t["syncing"],
    })
    if stats["totalCost"] is not None:
        realized_net = total - net_deposits - ppl          # by identity → 锁定 + 浮动 = 总
        stats["realizedPnl"] = round(realized_net, 2)
        stats["realizedPnlPct"] = round(realized_net / net_deposits * 100, 3)
        stats["unexplained"] = round(realized_net - result - dividends - interest - fx_fees - other_fees, 2)

    # Record today's total snapshot, then look for earliest snapshot in current month.
    daily = _record_today_total(total, a)
    in_month_keys = sorted(k for k in daily if k >= month_start.isoformat())
    if len(in_month_keys) >= 1:
        earliest_key = in_month_keys[0]
        earliest_total = float(daily[earliest_key])
        if earliest_key != today_date.isoformat() and earliest_total > 0:
            month_change = (total - earliest_total) - deposits_after_month_start
            stats["monthPnl"] = round(month_change, 2)
            stats["monthPnlPct"] = round(month_change / earliest_total * 100, 3)
            stats["monthSnapshotDate"] = earliest_key
            stats["monthSnapshotValue"] = round(earliest_total, 2)
            stats["monthDeposits"] = round(deposits_after_month_start, 2)
        elif earliest_key == today_date.isoformat():
            # First snapshot is today — no historical baseline yet.
            stats["monthSnapshotDate"] = earliest_key
            stats["monthSnapshotValue"] = round(earliest_total, 2)

    return stats


def _json_safe(obj):
    """Recursively null out NaN/Inf floats so Starlette's JSON encoder
    (allow_nan=False) doesn't 500 the whole feed on one bad T212/market value
    (e.g. a divide-by-zero % on a position with zero cost). Frontend renders
    null as '—'."""
    if isinstance(obj, float):
        return obj if (obj == obj and obj not in (float("inf"), float("-inf"))) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


async def snapshot_cached(account: str | None = None, ttl: float = 3.0) -> dict:
    """snapshot_data() shared by concurrent callers for a few seconds —
    /api/levels, /api/calendar, /api/news, /api/alerts and /api/risk all need
    the merged holdings and used to recompute it in parallel on page load."""
    view = (account or "all").lower()
    return await cache.get(f"snap:{view}", ttl, lambda: snapshot_data(view))


@app.get("/api/snapshot")
async def snapshot(account: str = "all"):
    return _json_safe(await snapshot_cached(account, 2.0))


@app.get("/api/health")
async def health():
    return {
        "t212_env": T212_ENV,
        "t212_id_set": bool(T212_KEY_ID),
        "t212_secret_set": bool(T212_SECRET),
        "agent_cmd": DEFAULT_CMD,
        "agent_found": shutil.which(DEFAULT_CMD.split()[0]) is not None,
    }


def _oi_bucket() -> str:
    """A per-trading-day bucket string that flips ~07:00 US Eastern (≈11:00 UTC)
    — i.e. in US pre-market, after OCC publishes the prior session's open
    interest. Used in the options-levels cache key so the walls auto-refresh
    once each trading morning (the frontend's 5-min poll then pulls the new OI),
    with no separate scheduler."""
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) - timedelta(hours=11)).strftime("%Y-%m-%d")


OPT_CACHE_FILE = HERE / "options_cache.json"
_OPT_DISK: dict = {}


async def _levels_cached(sym: str, spot: float | None, bucket: str) -> dict | None:
    """Options walls for one symbol, fetched at most once per trading-day
    bucket. Good results persist to disk (a server restart must not re-spend
    marketdata.app's 100-requests/day budget); errors are retried after 30 min
    at the earliest (and not at all while the quota is known to be exhausted)."""
    global _OPT_DISK
    key = f"opt:{sym}:{bucket}"
    async with cache._lock(key):
        ent = cache._store.get(key)
        if ent is None:
            if not _OPT_DISK:
                _OPT_DISK = _read_json_safe(OPT_CACHE_FILE, {}) or {"_": None}
            d = _OPT_DISK.get(key)
            if isinstance(d, dict) and d.get("lv"):
                ent = (float(d.get("ts") or time.time()), d["lv"])
                cache._store[key] = ent
        if ent and ent[1] and not ent[1].get("error"):
            return ent[1]
        if ent and time.time() - ent[0] < 1800:
            return ent[1]
        lv = await _options_levels(sym, spot)
        now = time.time()
        cache._store[key] = (now, lv)
        if lv and not lv.get("error"):
            _OPT_DISK = {k: v for k, v in _OPT_DISK.items() if k.endswith(f":{bucket}")}
            _OPT_DISK[key] = {"ts": now, "lv": lv}
            try:
                tmp = OPT_CACHE_FILE.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(_OPT_DISK, ensure_ascii=False), encoding="utf-8")
                tmp.replace(OPT_CACHE_FILE)
            except Exception:
                pass
        return lv


@app.get("/api/levels")
async def levels_endpoint(tickers: str = ""):
    """期权上下限: per-holding call wall (上限) / put wall (下限) + expected-move
    band. US-listed optionable holdings only — UK ETPs (SGLN, 3HNX) and the
    Korean SK Hynix underlying have no US options chain. Cached per trading-day
    bucket (auto-refreshes each US pre-market) since open interest is daily."""
    targets = []
    if tickers.strip():
        # on-demand (drawer for a watchlist symbol): US symbols only, spot from the quote
        for raw in [t.strip().upper() for t in tickers.split(",") if t.strip()][:5]:
            t212 = resolve_t212(raw)
            sym = t212_to_yahoo(t212) if "_" in t212 else yahoo_chart_symbol(raw)
            if not _is_us_symbol(sym) or ("_" in t212 and "_US_" not in t212):
                continue
            q = await cache.get(f"fh_quote:{sym}", 60.0, lambda sym=sym: _finnhub_quote(sym))
            spot = float((q or {}).get("c") or 0) or None
            targets.append((t212, sym, spot, sym, 0.0))
    else:
        snap = await snapshot_cached()          # every account's holdings, merged
        for p in snap.get("positions") or []:
            t212 = p.get("ticker") or ""
            if "_US_" not in t212:            # options walls: US listings only
                continue
            sym = t212_to_yahoo(t212)
            spot = float(p.get("currentPrice") or 0) or None
            targets.append((t212, sym, spot, p.get("name") or sym, float(p.get("marketValue") or 0)))
    targets.sort(key=lambda x: x[4], reverse=True)

    bucket = _oi_bucket()   # flips each US pre-market → forces one fresh OI pull/day
    # Drop yesterday's bucket entries so the cache doesn't grow unbounded.
    for _k in [k for k in list(cache._store) if k.startswith("opt:") and not k.endswith(f":{bucket}")]:
        cache._store.pop(_k, None)

    async def _one(t212, sym, spot, name):
        lv = await _levels_cached(sym, spot, bucket)
        if not lv or lv.get("error"):
            return {"ticker": t212, "symbol": sym, "name": name,
                    "error": (lv or {}).get("error", "无数据")}
        out = dict(lv)
        out["ticker"] = t212
        out["name"] = name
        if spot:
            out["spot"] = round(spot, 2)   # keep spot live even if walls cached
        return out

    results = await asyncio.gather(*[_one(t, s, sp, nm) for (t, s, sp, nm, _) in targets])
    return _json_safe({"levels": results, "fetchedAt": time.time(), "quota": MD_QUOTA,
                       "blockedUntil": _MD_BLOCKED_UNTIL or None})


# ============================================================================
# Portfolio → prompt context
# ============================================================================

def format_portfolio(snap: dict) -> str:
    cash = snap.get("cash") or {}
    info = snap.get("info") or {}
    positions = snap.get("positions") or []
    cur = info.get("currencyCode") or "?"
    lines = [
        f"账户币种: {cur}   环境: {snap.get('env')}",
        f"总价值: {cash.get('total')}   现金: {cash.get('free')}   已投入: {cash.get('invested')}",
        f"浮动盈亏: {cash.get('ppl')}   今日结果: {cash.get('result')}",
        "",
        f"持仓 ({len(positions)} 只,按市值降序):",
    ]
    for p in positions:
        lines.append(
            f"- {p['ticker']} ({p.get('name')}): "
            f"数量 {p['quantity']:.4f}, 成本 {p['averagePrice']:.4f}, "
            f"现价 {p['currentPrice']:.4f}, 市值 {p['marketValue']:.2f}, "
            f"P/L {p['ppl']:.2f} ({p['pplPct']:.2f}%)"
        )
    return "\n".join(lines)


CHAT_SYSTEM = """你是一个友好、说中文的投资组合分析助手。用户的 Trading 212 数据已经同步到当前工作目录,直接读取分析即可。

## 数据文件 (位于当前工作目录)
- `data/snapshot.json` — ⭐ 账户摘要 + 现金 + 全部持仓
- `data/orders_pending.json` / `orders_history.json` / `dividends.json` / `transactions.json` — T212 历史 (可能含 `{"error":"限流"}`,跳过即可)
- `data/market/<T212_TICKER>.json` — ⭐⭐ **每只持仓**的真实历史 K 线 + 技术指标 + 新闻 (来自 Yahoo Finance)

## 字段速查 — snapshot.json
顶层: `env`, `fetchedAt`, `info.currencyCode` (账户币种,如 GBP), `info.id`
`cash`: `{ total 总价值, free 现金, invested 已投入, ppl 浮动盈亏, result 当日结果, blocked 冻结, pieCash 投资篮现金 }`
`positions[]`: `{ ticker (T212 内部码,如 NVDA_US_EQ), name, type, currency, quantity 数量, averagePrice 成本均价, currentPrice 现价, invested 总成本, marketValue 市值, ppl 浮动盈亏(账户币种), pplPct 盈亏%, dayChangePct 当日涨跌%, initialFillDate 首次买入时间 }`

注: positions 数组里某些标的 `currency` 是 USD/EUR 但 `marketValue` 已折算成账户币种 GBP — 直接相加即可。

## 字段速查 — data/market/<TICKER>.json (⭐⭐ 消息面/盘中数据看这里)
- `quote`: 当日实时报价 `{price 现价, prev_close 昨收, day_change 涨跌, day_change_pct 涨跌%, day_open 开盘, day_high 日高, day_low 日低, ts}` — ✅ **总是可用** (Finnhub)
- `news`: 近 7 天 8 条新闻 `[{ title, publisher, providerPublishTime, summary, link }]` — ✅ **通常可用** (Finnhub)
- `indicators`: { ma20, ma50, ma200, rsi14, macd, macd_signal, macd_hist } — ⚠️ **可能为 null** (需付费历史数据,免费版没拉到)
- `recent_bars_30`: 近 30 日 OHLC+量 — ⚠️ **可能为空**
- `chart_error`: 如果有此字段,说明历史 K 线拉取失败,只能用 quote + news

**重要**: 如果 indicators / recent_bars_30 为空或缺失,**不要编造技术指标值** —— 直接说"历史 K 线数据未拉到,只能基于当日报价 + 训练数据中关于该标的的通用认知做评论"。

## ⚙️ 环境工具说明 (重要,避免试错)
- ✅ **有**: `cat`, `python3 -c '...'`, `head`, `grep`, `wc`, `ls`
- ❌ **没有**: `jq` (未安装), `node`, `npm`, 网络访问
- 标准做法: `python3 -c 'import json; d=json.load(open("data/snapshot.json")); ...'` 一行解决
- 不需要读 README, 字段说明在上方; 不需要 `pwd`/`ls`, 文件就上面列着
- 历史文件里可能有 `{"error": "..."}` (T212 限流),跳过即可

## 回答风格
- 中文口语风, Markdown 列表/加粗
- 默认 250 字内, 引用具体数字
- 不要把整段 JSON 倒回去给用户
- 没有实时新闻/行情数据时, 坦白说"以训练数据为准"
- 不给强买卖建议; 给观点时说明是个人看法

## ⛔ 输出禁令 (严格)
- 不要输出 "我先读取..." / "我先看看..." / "我会先..." / "让我先..." 等任何 **preamble / 行动声明**
- 不要解释你接下来要做什么、用什么工具、为什么用 python3 而不是 jq
- 直接给最终结论。读数据/执行命令是你自己的事,用户不需要看到这个过程
"""

ANALYSIS_INTRO = (
    "## 数据文件 (当前工作目录)\n"
    "- `data/snapshot.json` — 账户摘要 + 持仓 (cash, positions[]: ticker, name, "
    "quantity, averagePrice, currentPrice, marketValue, ppl, pplPct, dayChangePct)\n"
    "- `data/market/<T212_TICKER>.json` — ⭐ 每只持仓的 Yahoo 数据:\n"
    "    - `indicators` {ma20, ma50, ma200, rsi14, macd, macd_signal, macd_hist}\n"
    "    - `recent_bars_30` 近 30 日 OHLC+量\n"
    "    - `news` 近期新闻列表\n"
    "    - `meta.fiftyTwoWeekHigh/Low`, `window_high/low` (6 个月内极值)\n"
    "- `data/orders_history.json` / `dividends.json` / `transactions.json` — 历史 (可能含 error)\n\n"
    "## 环境\n"
    "- ✅ `python3 -c 'import json; d=json.load(open(\"data/...\")); ...'`\n"
    "- ❌ 没有 jq, 没有网络 (数据已经预拉取好了)\n"
    "- 字段说明已在上方,不用读 README\n\n"
    "## ⛔ 输出禁令\n"
    "- 不要输出'我先读取...'、'我会先...'、'让我先...'等任何 preamble\n"
    "- 不要解释你要用什么工具、为什么用 python3 不用 jq\n"
    "- 直接给最终结论,不要展示思考过程\n\n"
    "## 任务\n"
)

ANALYSIS_PROMPTS = {
    "anomaly": ANALYSIS_INTRO + (
        "用 python3 读 `data/snapshot.json`,挑出最值得关注的**异动**:仓位占比突出、"
        "盈亏百分比 / 绝对盈亏金额突出的标的。\n\n"
        "## 输出格式 (严格 Markdown 表格)\n\n"
        "| 代码 | 占比 | 浮盈/亏 | 收益% | 提示 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| AAA | 38.0% | +2100.00 | +21.0% | **最大仓位/最强** |\n"
        "| BBB | 24.0% | +800.00 | +9.5% | 次大 |\n"
        "| CCC | 18.0% | −450.00 | −6.2% | **最大输家** |\n"
        "| DDD | 12.0% | +300.00 | +7.1% | 健康 |\n"
        "| EEE | 2.0% | +40.00 | +12.0% | 小仓但强 |\n\n"
        "## 备注 (2-4 字简洁)\n"
        "- 提示词:**最大仓位** / **最强** / **最大输家** / 次大 / 健康 / 小仓但强 / 弱势 / 整理 等\n"
        "- 浮盈/亏:正数加 `+`,负数加 `−` (不用 ASCII `-`)\n"
        "- 收益% 同理\n\n"
        "**表格后另起一行**,用 1-2 句话点评组合层面的偏向 (集中度 / 行业暴露 / 整体浮盈率)。\n"
        "中文。引用真实数字。"
    ),
    "news": ANALYSIS_INTRO + (
        "用 python3 读 `data/market/*.json` 的 `news` 字段 + 你训练数据中关于这些标的的认知,"
        "整理近期消息面。\n\n"
        "## 输出格式 (严格 Markdown 表格)\n\n"
        "| 代码 | 近期关键消息 | 出处 / 类型 | 倾向 |\n"
        "| --- | --- | --- | --- |\n"
        "| AAA | 季度财报超预期,上调指引 | Bloomberg / 财报 | **正面** |\n"
        "| BBB | 行业需求回暖,提价预期 | Reuters / 行业 | **正面** |\n"
        "| CCC | 大客户订单延后 | WSJ / 行业 | 中性偏弱 |\n"
        "| DDD | 新工艺良率改善 | 行业媒体 / 公司 | 偏正面 |\n\n"
        "## 规则\n"
        "- 优先用 `data/market/<TICKER>.json` 的 news 数组里的 title + publisher\n"
        "- 若 news 缺失或不显著,可用训练数据中的通用认知补充,**标注 (训练数据)**\n"
        "- 倾向词库:**正面** / 偏正面 / **中性** / 中性偏弱 / 偏负面 / **负面**\n"
        "- 挑最重要的 4-6 只\n\n"
        "表格后用 1 句话总结整体消息面氛围。中文。"
    ),
    "technical": ANALYSIS_INTRO + (
        "**重要**: 用 python3 读 `data/market/<TICKER>.json` 的 `indicators` 字段。\n\n"
        "## 输出格式 (严格遵守)\n"
        "输出**一张 Markdown 表格**,每只持仓一行,**每个数字后跟 2-4 字中文备注**:\n\n"
        "| 代码 | 现价 | 布林线 %B (BW) | MA 趋势 | RSI | MACD hist | 关键位 (20d) |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| AAA | 120.00 | 0.71 (偏上) / 0.18 (正常) | 多头排列 | 64.0 (偏强) | −0.50 (动能转弱) | 阻 130 / 支 105 |\n"
        "| BBB | 85.00 | 0.73 (偏上) / **0.08 (蓄势)** | 多头排列 | 61.0 (偏强) | +0.30 (温和向上) | 阻 90 / 支 78 |\n"
        "| CCC | 40.00 | 0.29 (偏下) / 0.12 (正常) | MA20 下方 | 47.0 (中性) | **−1.20 (空头扩张)** | 阻 46 / 支 36 |\n"
        "...\n\n"
        "## 备注用语速查\n"
        "- BB %B: <0.2 `近下轨` / 0.2-0.4 `偏下` / 0.4-0.6 `中间` / 0.6-0.8 `偏上` / >0.8 `近上轨`\n"
        "- BB BW: <0.2 `蓄势` / 0.2-0.4 `正常` / 0.4-0.6 `偏高` / >0.6 `高波动`\n"
        "- MA: 现价在 MA20/50/200 全部上方 → `多头排列` / 全部下方 → `空头排列` / 混合 → `MA20 下方` 等\n"
        "- RSI: <30 `超卖` / 30-45 `偏弱` / 45-55 `中性` / 55-70 `偏强` / >70 `超买`\n"
        "- MACD hist: 正值上升 `多头扩张` / 正值收敛 `多头减弱` / 负值下行 `空头扩张` / 负值收敛 `空头减弱` / 接近 0 `动能转弱` 或 `动能转向`\n"
        "- 关键位: 用 `indicators.ranges.high_20d` 作阻力,`low_20d` 作支撑,直接写整数\n\n"
        "## 总结\n"
        "表格之后另起一段,**2-3 句**总结组合整体的趋势 / 波动率 / 超买超卖偏向 + 值得警惕的 1-2 个名字。\n\n"
        "⛔ 不要给买卖建议\n"
        "⛔ 备注 2-4 字,简洁干净,不要长句\n"
        "⛔ 必须用真实数字 (从 indicators 字段读出来),不要凑数"
    ),
}


# ============================================================================
# Codex subprocess runner with streaming output
# ============================================================================

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")

# Workspace codex sees: only contains README.md + data/*.json snapshots.
# This gives codex everything it needs to analyze the portfolio while
# isolating it from the project source code. Use /tmp directly so the path
# is easy to inspect / tail (macOS's tempfile.gettempdir() is per-user/ugly).
CODEX_WORKSPACE = Path("/tmp/t212-codex-workspace")
CODEX_DATA = CODEX_WORKSPACE / "data"
CODEX_WORKSPACE.mkdir(parents=True, exist_ok=True)
CODEX_DATA.mkdir(parents=True, exist_ok=True)

WORKSPACE_README = """# Trading 212 实盘数据快照

此目录由本地工具自动同步,每次提问前刷新。**只读**。

## 文件

- `data/snapshot.json` — ⭐ 账户 + 现金 + 持仓详情
- `data/orders_pending.json` / `orders_history.json` / `dividends.json` / `transactions.json` — T212 历史
- `data/market/<T212_TICKER>.json` — ⭐⭐ **每只持仓**的 Yahoo Finance 历史 K 线 +
  技术指标 (MA20/50/200, MACD, RSI14) + 近期新闻

部分历史文件可能包含 `{"error": "..."}`,表示当时 T212 API 返了错(通常是限流),
直接跳过那个数据维度即可。

技术面 / 消息面问题 → 看 `data/market/<TICKER>.json` 而不是 snapshot.json。

## snapshot.json 字段说明

顶层:
- `env`: "live" | "demo"
- `fetchedAt`: unix epoch 秒
- `info.currencyCode`: 账户币种 (如 "GBP")
- `info.id`: 账户 ID

`cash`:
- `total`: 账户总价值
- `free`: 可用现金
- `invested`: 已投入金额(成本基础)
- `ppl`: 浮动盈亏(未实现)
- `result`: 当日结果
- `blocked`: 冻结资金
- `pieCash`: 投资篮 (Pie) 中的现金

`positions`: 数组,每个元素:
- `ticker`: T212 内部代码 (如 "NVDA_US_EQ")
- `name`: 标的名称
- `currency`: 标的币种
- `quantity`: 持有数量(可能是小数)
- `averagePrice`: 平均成本
- `currentPrice`: 当前价
- `invested`: 总成本 = quantity * averagePrice
- `marketValue`: 市值 = quantity * currentPrice
- `ppl`: 浮动盈亏(以账户币种计)
- `pplPct`: 浮动盈亏百分比 (ppl / invested * 100)
- `initialFillDate`: 首次买入时间 (ISO 字符串)

## 环境工具

- ✅ 可用: `cat`, `python3 -c '...'`, `head`, `grep`, `wc`, `ls`
- ❌ **没有 `jq`**, 没有 `node`, 没有网络
- 解析 JSON 请用 `python3 -c`,不要用 jq

## 常用查询示例 (python3)

```bash
# 持仓 ticker + 市值 + 盈亏%
python3 -c 'import json; d=json.load(open("data/snapshot.json")); [print(p["ticker"], p["marketValue"], p["pplPct"]) for p in d["positions"]]'

# 按市值降序
python3 -c 'import json; d=json.load(open("data/snapshot.json")); [print(p["ticker"], p["marketValue"]) for p in sorted(d["positions"], key=lambda x: -x["marketValue"])]'

# 按浮动盈亏降序
python3 -c 'import json; d=json.load(open("data/snapshot.json")); [print(p["ticker"], p["ppl"]) for p in sorted(d["positions"], key=lambda x: -x["ppl"])]'

# 最大输家
python3 -c 'import json; d=json.load(open("data/snapshot.json")); [print(p["ticker"], p["ppl"], p["pplPct"]) for p in sorted(d["positions"], key=lambda x: x["ppl"])[:3]]'

# 现金占比
python3 -c 'import json; d=json.load(open("data/snapshot.json")); print(d["cash"]["free"] / d["cash"]["total"] * 100)'
```

## 注意

- 数据是只读快照
- 不要访问 trading212.com,数据已经为你拉取好了
- 回答时引用具体数字,但不要把整段 JSON 倒回去给用户
"""


async def refresh_workspace() -> dict:
    """Refresh data files in CODEX_WORKSPACE/data. Returns the snapshot dict."""
    (CODEX_WORKSPACE / "README.md").write_text(WORKSPACE_README, encoding="utf-8")

    snap = await snapshot_data()
    (CODEX_DATA / "snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for name, fn in [
        ("orders_pending", get_orders),
        ("orders_history", get_history_orders),
        ("dividends",      get_dividends),
        ("transactions",   get_transactions),
    ]:
        path = CODEX_DATA / f"{name}.json"
        try:
            data = await fn()
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            path.write_text(
                json.dumps({"error": str(e), "ts": time.time()},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    # Market data (OHLC, indicators, news) — best-effort, parallel per ticker.
    try:
        await refresh_market_data(snap.get("positions") or [])
    except Exception as e:
        (CODEX_DATA / "market" / "_error.json").write_text(
            json.dumps({"error": str(e), "ts": time.time()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return snap

# Lines starting with these markers are codex's TUI tool-use/banner noise — drop them.
NOISE_LINE_PREFIXES = (
    "•",       # codex tool-use bullet
    "└",       # tool output continuation
    "⚠",       # auto-approval review
    "✔",       # auto-approval approved
    "◦",       # codex's "Running ..." marker
    "─",       # separator lines
    "╭", "│", "╰",  # box-drawing for banner
    "  Tip:",  # codex marketing tip
    "Tip:",
    "▌ model:",
    "▌ directory:",
    "▌ approval:",
    "▌ sandbox:",
    "▌ workdir:",
    ">_ OpenAI Codex",
    "model:",
    "directory:",
    "approval:",
    "sandbox:",
)

# Whole lines exactly matching these (after strip) — drop too.
NOISE_LINE_EXACT = {"", ">_ OpenAI Codex", "tokens used:", "tokens:"}


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def is_noise_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False  # keep blank lines, they're part of markdown formatting
    if any(s.startswith(p) for p in NOISE_LINE_PREFIXES):
        return True
    # also drop "Ran ..." style codex command summaries that might not start with •
    if re.match(r"^Ran\s", s):
        return True
    return False


# Role/section markers codex uses to delimit its `exec` transcript.
_CODEX_ANSWER_MARKERS = ("codex", "assistant")
_CODEX_SKIP_MARKERS = ("user", "exec", "thinking", "tool", "tool_use")
_CODEX_HEADER_PREFIXES = ("workdir:", "provider:", "model:", "approval:",
                          "sandbox:", "reasoning effort:", "reasoning summaries:",
                          "session id:", "directory:")
_SEP_RE = re.compile(r"^-{4,}\s*$")


def extract_codex_answer(lines: list[str]) -> str:
    """Robust fallback: pull the assistant's final answer out of a full codex
    `exec` transcript. Mirrors deep_analysis.extract_codex_answer (which keeps
    working across codex versions): capture only the LAST `codex`/`assistant`
    segment; if that's too short, return the whole de-noised transcript.

    Used when the line-by-line streaming parser yields nothing — e.g. codex
    changed its tool-use output layout (0.132 → 0.137), which is exactly what
    broke the on-demand analysis tabs."""
    out: list[str] = []
    capturing = False
    for ln in lines:
        s = ln.strip()
        if s in _CODEX_ANSWER_MARKERS:
            capturing = True
            out = []  # keep only the last codex segment
            continue
        if s in _CODEX_SKIP_MARKERS:
            capturing = False
            continue
        if s.startswith("tokens used") or s == "tokens":
            capturing = False
            continue
        if capturing:
            if _SEP_RE.match(s) or is_noise_line(ln):
                continue
            out.append(ln)
    text = "\n".join(out).strip()
    if len(text) >= 40:
        return text
    # Primary parse failed — return the whole transcript minus banner/markers/noise.
    keep: list[str] = []
    for ln in lines:
        s = ln.strip()
        if (not s or s in _CODEX_ANSWER_MARKERS or s in _CODEX_SKIP_MARKERS
                or _SEP_RE.match(s) or is_noise_line(ln)
                or s.startswith("tokens used") or s == "tokens"
                or s.startswith("OpenAI Codex")
                or s.startswith("Reading additional input")
                or any(s.startswith(h) for h in _CODEX_HEADER_PREFIXES)):
            continue
        keep.append(ln)
    return "\n".join(keep).strip()


async def run_codex_stream(prompt: str) -> AsyncIterator[bytes]:
    """Refresh data files, then spawn codex in the workspace and stream cleaned answer."""
    # 1) Sync latest T212 data → workspace files. If sync fails but we already
    # have a previous snapshot on disk, silently use the stale data — better
    # than failing the whole turn. Only complain if we have nothing at all.
    snap_file = CODEX_DATA / "snapshot.json"
    sync_error: str | None = None
    try:
        await refresh_workspace()
    except Exception as e:
        sync_error = str(e)

    if not snap_file.exists():
        yield (f"[错误] 没拿到 Trading 212 数据,无法分析: "
               f"{sync_error or '未知错误'}\n").encode()
        return

    cmd_parts = (DEFAULT_CMD.split() or ["codex"])
    argv = cmd_parts + ["exec", "--skip-git-repo-check", prompt]

    if shutil.which(argv[0]) is None:
        yield f"[错误] 找不到 {argv[0]} —— 请装好 codex CLI 或修改 .env 中的 AGENT_CMD".encode()
        return

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(CODEX_WORKSPACE),  # ← workspace with the JSON data files
            env={**os.environ, "TERM": "dumb", "NO_COLOR": "1"},
        )
    except Exception as e:
        yield f"[错误] 启动 {argv[0]} 失败: {e}".encode()
        return

    # Codex `exec` output format (v0.132.x):
    #
    #   Reading additional input from stdin...
    #   OpenAI Codex v0.132.0
    #   --------
    #   workdir: ...
    #   provider: ...
    #   session id: ...
    #   --------
    #   user
    #   <our prompt — to be skipped>
    #   --------
    #   codex
    #   <the actual response — what we want to stream>
    #   --------
    #   tokens used: ...
    #
    # State machine: skip everything until we see role marker "codex"/"assistant",
    # then stream subsequent lines (minus tool-use noise) until next separator or EOF.

    # Codex v0.132 alternates between sections, each prefixed by a role line:
    #   user      → our prompt (skip)
    #   codex     → assistant text — reasoning OR final answer (yield)
    #   exec      → tool call + command + output (skip)
    #   thinking  → internal reasoning trace (skip — too noisy for chat UI)
    # tokens used: ...   → end of stream
    assert proc.stdout
    pending = bytearray()
    state = "init"  # init → seen_user → response → exec/thinking → response → ...
    yielded_any = False
    all_lines: list[str] = []  # full transcript, for the robust fallback parser
    SEP_RE = re.compile(r"^-{4,}\s*$")
    ROLE_MARKERS = {
        "user": "user",
        "codex": "response",
        "assistant": "response",
        "exec": "skip",
        "thinking": "skip",
        "tool": "skip",
        "tool_use": "skip",
    }
    SKIPPED_SECTION_HEADERS = {"workdir:", "provider:", "reasoning effort:",
                               "reasoning summaries:", "session id:", "model:",
                               "approval:", "sandbox:"}
    try:
        while True:
            chunk = await proc.stdout.read(512)
            eof = not chunk
            if chunk:
                pending.extend(chunk)
            while True:
                nl = pending.find(b"\n")
                if nl < 0:
                    if eof and pending:
                        raw = bytes(pending); pending.clear()
                        line = strip_ansi(raw.decode("utf-8", errors="replace"))
                        all_lines.append(line)
                        if state == "response" and not is_noise_line(line):
                            yield line.encode(); yielded_any = True
                    break
                raw = bytes(pending[:nl])
                del pending[:nl + 1]
                line = strip_ansi(raw.decode("utf-8", errors="replace"))
                all_lines.append(line)
                stripped = line.strip()

                # End-of-stream markers. Codex prints "tokens used\n<N>" after
                # the streamed answer, then repeats the final message as a
                # parseable result block — we drop everything from here on.
                if stripped.startswith("tokens used") or stripped == "tokens":
                    state = "done"
                    continue
                if state == "done":
                    continue

                # Role marker transitions
                if stripped in ROLE_MARKERS:
                    new_state = ROLE_MARKERS[stripped]
                    # "user" only sets seen_user once; subsequent "user" (unlikely) → skip
                    if new_state == "user":
                        state = "skip_until_role"
                    else:
                        state = new_state
                    continue

                # Banner / session header lines (before first role marker)
                if state == "init":
                    if any(stripped.startswith(p) for p in SKIPPED_SECTION_HEADERS):
                        continue
                    if SEP_RE.match(stripped):
                        continue
                    if stripped.startswith("OpenAI Codex") or stripped.startswith("Reading"):
                        continue
                    # Otherwise stay in init (might be unexpected text)
                    continue

                if state in ("skip", "skip_until_role"):
                    continue

                if state == "response":
                    if SEP_RE.match(stripped):
                        continue  # decoration between sections
                    if is_noise_line(line):
                        continue
                    yield (line + "\n").encode()
                    yielded_any = True
            if eof:
                break

        await proc.wait()
        if not yielded_any:
            # Streaming parser found no answer (e.g. codex changed its tool-use
            # output layout across versions). Re-parse the whole transcript with
            # the robust extractor before giving up.
            answer = extract_codex_answer(all_lines)
            if answer.strip():
                yield answer.encode()
            else:
                yield ("(Codex 没有返回可显示的文本。如果总这样,告诉我,"
                       "可能 codex 输出格式变了需要更新过滤规则。)").encode()
        elif proc.returncode and proc.returncode != 0:
            yield f"\n[codex 退出码 {proc.returncode}]".encode()
    except asyncio.CancelledError:
        try:
            proc.kill()
        except Exception:
            pass
        raise


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    if not messages:
        raise HTTPException(400, "messages 为空")

    convo = []
    for m in messages[:-1]:
        role = "用户" if m.get("role") == "user" else "助手"
        convo.append(f"{role}: {m.get('content','')}")
    latest = messages[-1].get("content", "")

    prompt = (
        CHAT_SYSTEM
        + ("\n\n## 历史对话\n" + "\n".join(convo) if convo else "")
        + f"\n\n## 用户最新提问\n{latest}\n\n请读取 data/ 下的数据,然后用中文回答:"
    )
    return StreamingResponse(run_codex_stream(prompt), media_type="text/plain; charset=utf-8")


@app.post("/api/analysis")
async def analysis(request: Request):
    body = await request.json()
    kind = (body.get("type") or "").strip()
    if kind not in ANALYSIS_PROMPTS:
        raise HTTPException(400, f"未知 type: {kind}")
    return StreamingResponse(run_codex_stream(ANALYSIS_PROMPTS[kind]),
                              media_type="text/plain; charset=utf-8")


# ============================================================================
# Read-only intel + sparkline endpoints (dashboard 情报看板 + 迷你走势图).
# The live monitor state files live in ~/Library/guanlan/ (where the
# LaunchAgents run), NOT this server dir — so read from there.
# ============================================================================
GUANLAN_LIB = Path.home() / "Library" / "guanlan"


def _read_json_safe(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


@app.get("/api/intel")
async def intel_endpoint():
    """Surface the monitors' Feishu intel on-page: live structural tech triggers
    (monitor_state.json), the latest Korea lead-lag reading (korea_leadlag.csv),
    and upcoming hand-maintained events (events.json)."""
    lib = GUANLAN_LIB

    # 1) active structural tech triggers per ticker
    ms = _read_json_safe(lib / "monitor_state.json", {})
    triggers = []
    if isinstance(ms, dict):
        for tk, rules in ms.items():
            if not isinstance(rules, dict):
                continue
            active, latest = [], 0
            for rule, v in rules.items():
                if isinstance(v, dict) and v.get("active"):
                    active.append(rule)
                    latest = max(latest, int(v.get("fired_at") or 0))
            if active:
                triggers.append({"ticker": tk, "active": active, "fired_at": latest})
    triggers.sort(key=lambda x: x["fired_at"], reverse=True)

    # 2) latest Korea lead-lag row
    korea = None
    try:
        lines = (lib / "korea_leadlag.csv").read_text(encoding="utf-8").strip().splitlines()
        if len(lines) >= 2:
            row = dict(zip(lines[0].split(","), lines[-1].split(",")))
            def _f(k):
                try:
                    return float(row.get(k) or "")
                except Exception:
                    return None
            korea = {"date": row.get("date"), "korea": _f("korea"),
                     "mu_gap": _f("MU_gap"), "sndk_gap": _f("SNDK_gap")}
    except Exception:
        korea = None

    # 3) upcoming events (date >= today)
    from datetime import date as _date
    today = _date.today().isoformat()
    ev_raw = _read_json_safe(lib / "events.json", [])
    ev_list = ev_raw if isinstance(ev_raw, list) else (ev_raw.get("events") or [])
    events = sorted(
        [e for e in ev_list if isinstance(e, dict) and (e.get("date") or "") >= today],
        key=lambda e: (e.get("date") or "", e.get("time") or ""))[:6]

    return {"triggers": triggers, "korea": korea, "events": events,
            "events_stale": bool(ev_list) and not events}


@app.get("/api/spark")
async def spark_endpoint(tickers: str = ""):
    """Batch ~30-day close series for inline sparklines, from the daily-bars
    cache (no longer depends on the codex workspace dump). Degenerate/unknown
    tickers are omitted."""
    syms = [t.strip() for t in tickers.split(",") if t.strip()][:40]

    async def _one(tk):
        try:
            d = await get_daily_bars(tk, "1y")
        except Exception:
            return tk, None
        closes = [b["c"] for b in d["bars"][-30:]]
        return tk, (closes if len(closes) >= 5 and max(closes) > min(closes) else None)

    pairs = await asyncio.gather(*[_one(t) for t in syms])
    return _json_safe({"spark": {k: v for k, v in pairs if v}})


# ============================================================================
# Market clock, quotes & the 大盘 barometer
# ============================================================================

def us_session(now=None) -> str:
    """'pre' | 'regular' | 'post' | 'closed' from the US/Eastern wall clock.
    (Exchange holidays come from Finnhub's market-status in /api/market.)"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    n = now or datetime.now(ZoneInfo("America/New_York"))
    if n.weekday() >= 5:
        return "closed"
    m = n.hour * 60 + n.minute
    if 240 <= m < 570:
        return "pre"
    if 570 <= m < 960:
        return "regular"
    if 960 <= m < 1200:
        return "post"
    return "closed"


def _quote_ttl(regular: float = 30.0) -> float:
    """Finnhub's free quotes only move in the regular session — poll gently
    otherwise so the shared 60/min budget goes where it matters."""
    s = us_session()
    return regular if s == "regular" else (60.0 if s in ("pre", "post") else 300.0)


async def quote_compact(sym: str) -> dict | None:
    sym = (sym or "").strip().upper()
    if not sym:
        return None
    try:
        q = await cache.get(f"fh_quote:{sym}", _quote_ttl(), lambda: _finnhub_quote(sym))
    except Exception:
        q = None
    if not q or not q.get("c"):
        return None
    c, pc, dp = q.get("c"), q.get("pc"), q.get("dp")
    if dp is None and c and pc:
        dp = (c - pc) / pc * 100
    return {"price": c, "changePct": dp, "prevClose": pc, "change": q.get("d"),
            "high": q.get("h"), "low": q.get("l"), "open": q.get("o"), "t": q.get("t")}


@app.get("/api/quote")
async def quote_endpoint(tickers: str = ""):
    """Live-ish quotes (Finnhub, session-aware cache) for arbitrary tickers —
    powers the watchlist rows so their numbers tick (T212 snapshot only covers
    holdings). Returns {ticker: {price, changePct, prevClose, ...}}."""
    syms = [t.strip().upper() for t in tickers.split(",") if t.strip()][:30]
    results = await asyncio.gather(*[quote_compact(s) for s in syms])
    return _json_safe({"quote": {s: v for s, v in zip(syms, results) if v},
                       "session": us_session()})


MARKET_TILES = [
    # symbol,            label,        group
    ("SPY",              "标普 500",   "指数"),
    ("QQQ",              "纳指 100",   "指数"),
    ("DIA",              "道指",       "指数"),
    ("IWM",              "罗素 2000",  "指数"),
    ("SMH",              "半导体",     "板块"),
    ("IGV",              "软件",       "板块"),
    ("VIXY",             "波动率 VIXY", "风险"),
    ("TLT",              "长债 TLT",   "风险"),
    ("GLD",              "黄金",       "另类"),
    ("BINANCE:BTCUSDT",  "比特币",     "另类"),
]


async def _yf_daily_last(sym: str) -> dict | None:
    """Last value + day change for FX / yields that Finnhub's free tier lacks
    (GBPUSD=X, ^TNX). yfinance's daily row for the current session is live."""
    if not _YF_AVAILABLE:
        return None

    def _sync():
        try:
            h = _yf.Ticker(sym).history(period="5d", interval="1d")
            closes = [float(x) for x in h["Close"].tolist() if x == x]
            if len(closes) < 2:
                return None
            return {"price": round(closes[-1], 5), "prev": round(closes[-2], 5),
                    "changePct": round((closes[-1] / closes[-2] - 1) * 100, 3),
                    "change": round(closes[-1] - closes[-2], 5)}
        except Exception:
            return None
    return await asyncio.to_thread(_sync)


async def _market_status() -> dict:
    d = await cache.get("fh:market_status", 60.0,
                        lambda: _finnhub_get("/stock/market-status", {"exchange": "US"}))
    return d if isinstance(d, dict) and "_finnhub_error" not in d else {}


async def _market_holidays() -> list:
    d = await cache.get("fh:market_holiday", 43200.0,
                        lambda: _finnhub_get("/stock/market-holiday", {"exchange": "US"}))
    return (d or {}).get("data") or [] if isinstance(d, dict) else []


_BG_TASKS: dict[str, asyncio.Task] = {}


async def _bg_cached(key: str, ttl: float, fetch, wait: float = 1.5):
    """cache.get that never holds a response hostage: if a refresh takes
    longer than `wait`, answer with the previous value (or None) and let the
    refresh finish in the background for the next poll."""
    ent = cache._store.get(key)
    if ent and time.time() - ent[0] < ttl:
        return ent[1]
    task = _BG_TASKS.get(key)
    if task is None or task.done():
        task = _BG_TASKS[key] = asyncio.create_task(cache.get(key, ttl, fetch))
    try:
        return await asyncio.wait_for(asyncio.shield(task), wait)
    except asyncio.TimeoutError:
        return ent[1] if ent else None
    except Exception:
        return ent[1] if ent else None


@app.get("/api/market")
async def market_endpoint():
    """大盘 barometer: session clock + index/sector/risk tiles + GBP/USD + US 10Y."""
    from datetime import date as _date
    tiles_q = await asyncio.gather(*[quote_compact(s) for s, _, _ in MARKET_TILES])
    gbpusd, tnx, status, holidays = await asyncio.gather(
        _bg_cached("yf_last:GBPUSD=X", 300.0, lambda: _yf_daily_last("GBPUSD=X")),
        _bg_cached("yf_last:^TNX", 600.0, lambda: _yf_daily_last("^TNX")),
        _market_status(), _market_holidays())
    today = _date.today().isoformat()
    upcoming = sorted([h for h in holidays if (h.get("atDate") or "") >= today],
                      key=lambda h: h.get("atDate") or "")[:3]
    return _json_safe({
        "session": us_session(),
        "status": {"isOpen": status.get("isOpen"), "session": status.get("session"),
                   "holiday": status.get("holiday")},
        "holidays": upcoming,
        "tiles": [dict(sym=s, name=n, group=g, **(q or {})) for (s, n, g), q in zip(MARKET_TILES, tiles_q)],
        "fx": {"GBPUSD": gbpusd},
        "rates": {"US10Y": tnx},
        "fetchedAt": time.time(),
    })


# ============================================================================
# Catalyst calendar — earnings (Finnhub, auto) + US macro (official 2026
# schedule) + the hand-kept events.json the Feishu morning brief also reads
# ============================================================================

# Source: OMB "Schedule of release dates for principal federal economic
# indicators for 2026" (BLS / BEA) + the Federal Reserve's 2026 FOMC calendar.
# Times are US/Eastern; converted to UTC timestamps per event (DST-safe).
MACRO_EVENTS_2026 = [
    ("2026-09-30", "08:30", "PCE 物价(8月) · GDP 二季度终值", 2),
    ("2026-10-02", "08:30", "非农就业(9月)", 3),
    ("2026-10-14", "08:30", "CPI(9月)", 3),
    ("2026-10-15", "08:30", "PPI(9月) · 零售销售", 2),
    ("2026-10-28", "14:00", "FOMC 利率决议", 3),
    ("2026-10-29", "08:30", "GDP 三季度初值 · PCE(9月)", 2),
    ("2026-11-06", "08:30", "非农就业(10月)", 3),
    ("2026-11-10", "08:30", "CPI(10月)", 3),
    ("2026-11-13", "08:30", "PPI(10月)", 2),
    ("2026-11-17", "08:30", "零售销售(10月)", 1),
    ("2026-11-25", "08:30", "PCE(10月) · GDP 修正", 2),
    ("2026-12-04", "08:30", "非农就业(11月)", 3),
    ("2026-12-09", "14:00", "FOMC 利率决议 + 点阵图", 3),
    ("2026-12-10", "08:30", "CPI(11月)", 3),
    ("2026-12-15", "08:30", "PPI(11月)", 2),
    ("2026-12-16", "08:30", "零售销售(11月)", 1),
    ("2026-12-23", "08:30", "PCE(11月) · GDP 终值", 2),
]

_EARN_HOUR = {"bmo": ("07:00", "盘前"), "amc": ("16:05", "盘后"), "dmh": ("12:00", "盘中")}


def _et_ts(d: str, hhmm: str) -> int:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return int(datetime.strptime(f"{d} {hhmm}", "%Y-%m-%d %H:%M")
               .replace(tzinfo=ZoneInfo("America/New_York")).timestamp())


async def _earnings_for(sym: str) -> list:
    from datetime import date as _date, timedelta
    t0 = _date.today()
    frm, to = (t0 - timedelta(days=3)).isoformat(), (t0 + timedelta(days=100)).isoformat()
    d = await cache.get(f"fh:earn:{sym}", 43200.0,
                        lambda: _finnhub_get("/calendar/earnings", {"symbol": sym, "from": frm, "to": to}))
    rows = (d or {}).get("earningsCalendar") if isinstance(d, dict) else None
    return [r for r in (rows or []) if (r.get("symbol") or "").upper() == sym.upper()]


@app.get("/api/calendar")
async def calendar_endpoint(tickers: str = ""):
    """Upcoming catalysts for holdings (+ optional watchlist tickers):
    earnings with EPS/revenue estimates, US macro releases, FOMC, and the
    user's own events.json. Sorted by time; includes the last ~2 days."""
    from datetime import date as _date, timedelta
    held = []
    try:
        snap = await snapshot_cached()
        held = [t212_to_yahoo(p["ticker"]) for p in snap.get("positions") or []
                if "_US_" in (p.get("ticker") or "")]
    except Exception:
        pass
    extra = [yahoo_chart_symbol(t) for t in tickers.split(",") if t.strip()]
    syms = list(dict.fromkeys([s for s in held + extra if _is_us_symbol(s)]))[:30]
    lo = (_date.today() - timedelta(days=2)).isoformat()
    hi = (_date.today() + timedelta(days=75)).isoformat()

    events = []
    for sym, rows in zip(syms, await asyncio.gather(*[_earnings_for(s) for s in syms])):
        for r in rows:
            d = r.get("date") or ""
            if not (lo <= d <= hi):
                continue
            hhmm, label = _EARN_HOUR.get((r.get("hour") or "").lower(), ("09:30", "时间未定"))
            events.append({
                "id": f"earn:{sym}:{d}", "kind": "earnings", "ticker": sym,
                "held": sym in held, "date": d, "ts": _et_ts(d, hhmm), "timeLabel": label,
                "title": f"{sym} 财报",
                "detail": {"quarter": r.get("quarter"), "year": r.get("year"),
                           "epsEstimate": r.get("epsEstimate"), "epsActual": r.get("epsActual"),
                           "revenueEstimate": r.get("revenueEstimate"),
                           "revenueActual": r.get("revenueActual")},
                "importance": 3 if sym in held else 1,
            })
    for d, hhmm, title, imp in MACRO_EVENTS_2026:
        if lo <= d <= hi:
            events.append({"id": f"macro:{d}:{title}", "kind": "fomc" if "FOMC" in title else "macro",
                           "date": d, "ts": _et_ts(d, hhmm), "timeLabel": f"{hhmm} ET",
                           "title": title, "importance": imp, "source": "OMB/Fed 2026 日程"})
    ev_raw = _read_json_safe(GUANLAN_LIB / "events.json", [])
    for e in (ev_raw if isinstance(ev_raw, list) else (ev_raw.get("events") or [])):
        d = (e or {}).get("date") or ""
        if isinstance(e, dict) and lo <= d <= hi:
            try:
                from datetime import datetime
                ts = int(datetime.strptime(f"{d} {e.get('time') or '09:00'}", "%Y-%m-%d %H:%M").timestamp())
            except Exception:
                ts = _et_ts(d, "09:00")
            events.append({"id": f"custom:{d}:{e.get('title')}", "kind": "custom", "date": d, "ts": ts,
                           "timeLabel": (e.get("time") or "") + " 本地", "title": e.get("title") or "事件",
                           "importance": 2, "source": "events.json"})
    events.sort(key=lambda e: e["ts"])
    return _json_safe({"events": events, "symbols": syms, "fetchedAt": time.time()})


# ============================================================================
# News, company profile, candles, symbol search (Finnhub free tier + yfinance)
# ============================================================================

async def _company_news(sym: str, days: int = 3) -> list:
    from datetime import date as _date, timedelta
    t0 = _date.today()
    d = await cache.get(f"fh:news:{sym}:{days}", 1200.0, lambda: _finnhub_get(
        "/company-news", {"symbol": sym, "from": (t0 - timedelta(days=days)).isoformat(),
                          "to": t0.isoformat()}))
    return d if isinstance(d, list) else []


@app.get("/api/news")
async def news_endpoint(tickers: str = "", days: int = 3, general: int = 0, per: int = 6):
    """Headlines for holdings (default) or the given tickers, newest first."""
    if tickers.strip():
        syms = [yahoo_chart_symbol(t) for t in tickers.split(",") if t.strip()]
    else:
        try:
            snap = await snapshot_cached()
            syms = [t212_to_yahoo(p["ticker"]) for p in snap.get("positions") or []
                    if "_US_" in (p.get("ticker") or "")]
        except Exception:
            syms = []
    syms = list(dict.fromkeys(s for s in syms if _is_us_symbol(s)))[:20]
    days = max(1, min(int(days or 3), 14))
    items, seen = [], set()
    for sym, rows in zip(syms, await asyncio.gather(*[_company_news(s, days) for s in syms])):
        for n in sorted(rows, key=lambda x: -(x.get("datetime") or 0))[:max(1, min(per, 20))]:
            key = (n.get("headline") or "").strip().lower()[:90]
            if not key or key in seen:
                continue
            seen.add(key)
            items.append({"id": n.get("id"), "ticker": sym, "headline": n.get("headline"),
                          "summary": (n.get("summary") or "")[:280], "source": n.get("source"),
                          "url": n.get("url"), "datetime": n.get("datetime"),
                          "related": n.get("related")})
    if general:
        g = await cache.get("fh:news:general", 600.0,
                            lambda: _finnhub_get("/news", {"category": "general"}))
        for n in (g if isinstance(g, list) else [])[:25]:
            key = (n.get("headline") or "").strip().lower()[:90]
            if key and key not in seen:
                seen.add(key)
                items.append({"id": n.get("id"), "ticker": None, "headline": n.get("headline"),
                              "summary": (n.get("summary") or "")[:280], "source": n.get("source"),
                              "url": n.get("url"), "datetime": n.get("datetime"), "related": ""})
    items.sort(key=lambda x: -(x.get("datetime") or 0))
    return _json_safe({"items": items, "symbols": syms, "fetchedAt": time.time()})


_METRIC_KEYS = {
    "beta": "beta", "peTTM": "peTTM", "psTTM": "psTTM", "pb": "pbQuarterly",
    "epsTTM": "epsTTM", "epsGrowthTTM": "epsGrowthTTMYoy", "revGrowthTTM": "revenueGrowthTTMYoy",
    "revGrowthQ": "revenueGrowthQuarterlyYoy", "grossMargin": "grossMarginTTM",
    "netMargin": "netProfitMarginTTM", "divYield": "currentDividendYieldTTM",
    "high52": "52WeekHigh", "high52Date": "52WeekHighDate", "low52": "52WeekLow",
    "low52Date": "52WeekLowDate", "ret5d": "5DayPriceReturnDaily", "ret13w": "13WeekPriceReturnDaily",
    "ret26w": "26WeekPriceReturnDaily", "ret52w": "52WeekPriceReturnDaily",
    "retYtd": "yearToDatePriceReturnDaily", "vol10d": "10DayAverageTradingVolume",
    "vol3m": "3MonthAverageTradingVolume",
}


@app.get("/api/profile")
async def profile_endpoint(ticker: str = "MU"):
    """Fundamentals snapshot for the stock drawer: profile, key metrics,
    analyst recommendation trend, last EPS surprises, next earnings, peers."""
    sym = yahoo_chart_symbol(resolve_t212(ticker))
    if not _is_us_symbol(sym):
        return {"symbol": sym, "supported": False}
    fh = lambda path, params, ttl: cache.get(f"fh:{path}:{sym}", ttl, lambda: _finnhub_get(path, params))
    prof, met, rec, earn, peers, cal = await asyncio.gather(
        fh("/stock/profile2", {"symbol": sym}, 43200.0),
        fh("/stock/metric", {"symbol": sym, "metric": "all"}, 21600.0),
        fh("/stock/recommendation", {"symbol": sym}, 43200.0),
        fh("/stock/earnings", {"symbol": sym}, 43200.0),
        fh("/stock/peers", {"symbol": sym}, 86400.0),
        _earnings_for(sym))
    ok = lambda v: v if (v is not None and not (isinstance(v, dict) and "_finnhub_error" in v)) else None
    prof, met, rec, earn, peers = map(ok, (prof, met, rec, earn, peers))
    m = (met or {}).get("metric") or {}
    from datetime import date as _date
    nxt = next((r for r in sorted(cal, key=lambda r: r.get("date") or "")
                if (r.get("date") or "") >= _date.today().isoformat()), None)
    return _json_safe({
        "symbol": sym, "supported": True,
        "profile": {k: (prof or {}).get(k) for k in
                    ("name", "logo", "finnhubIndustry", "exchange", "marketCapitalization",
                     "weburl", "ipo", "country", "currency", "shareOutstanding")},
        "metrics": {k: m.get(v) for k, v in _METRIC_KEYS.items()},
        "recommendation": (rec or [])[:4] if isinstance(rec, list) else [],
        "earnings": (earn or [])[:4] if isinstance(earn, list) else [],
        "nextEarnings": nxt,
        "peers": [p for p in (peers or []) if p != sym][:8] if isinstance(peers, list) else [],
    })


@app.get("/api/candles")
async def candles_endpoint(ticker: str = "MU", range: str = "1y"):   # noqa: A002 — query name
    """Daily OHLCV for the drawer chart (NaN-free; last bar live-patched)."""
    period = range if range in ("6mo", "1y", "2y", "5y") else "1y"
    t = resolve_t212(ticker)
    d = await get_daily_bars(t, period)
    return _json_safe({"ticker": ticker, "t212": t, "symbol": d["symbol"], "source": d.get("source"),
                       "bars": d["bars"], "patched": d.get("patched"), "error": d.get("error")})


@app.get("/api/search")
async def search_endpoint(q: str = ""):
    """Symbol autocomplete (Finnhub /search), US listings first."""
    qq = (q or "").strip()
    if len(qq) < 1:
        return {"results": []}
    d = await cache.get(f"fh:search:{qq.lower()}", 3600.0,
                        lambda: _finnhub_get("/search", {"q": qq}))
    rows = (d or {}).get("result") or [] if isinstance(d, dict) else []
    out = []
    for r in rows:
        sym = r.get("symbol") or ""
        if not sym or r.get("type") not in ("Common Stock", "ETP", "ADR", "REIT", "ETF", ""):
            continue
        out.append({"symbol": sym, "display": r.get("displaySymbol") or sym,
                    "name": r.get("description"), "type": r.get("type"), "us": "." not in sym})
    out.sort(key=lambda r: (not r["us"], len(r["symbol"])))
    return {"results": out[:12]}


# ============================================================================
# Orders, fills, UK tax-year gauges, NAV curve (all accounts)
# ============================================================================

def _configured_accounts(account: str | None = None) -> list:
    maybe_reload_accounts()
    accts = [a for a in ACCOUNTS if a.configured]
    if account and account != "all":
        accts = [a for a in accts if a.id == account.lower()]
    return accts


@app.get("/api/orders")
async def orders_endpoint(account: str = "all"):
    """Working (pending) orders in every account, with distance to trigger."""
    prices = {p["ticker"]: p.get("currentPrice") for p in _LAST_POSITIONS}
    out, errors = [], []
    for a in _configured_accounts(account):
        try:
            rows = await get_orders(a)
        except HTTPException as e:
            errors.append(str(e.detail))
            continue
        for o in rows or []:
            t = o.get("ticker") or ""
            inst = o.get("instrument") or {}
            px = prices.get(t)
            if px is None and "_US_" in t:
                q = await quote_compact(t212_to_yahoo(t))
                px = (q or {}).get("price")
            trig = o.get("limitPrice") if o.get("limitPrice") is not None else o.get("stopPrice")
            qty = float(o.get("quantity") or 0)
            out.append({
                "account": a.id, "accountLabel": a.label, "id": o.get("id"),
                "ticker": t, "short": _short_ticker(t), "symbol": t212_to_yahoo(t),
                "name": inst.get("name") or t, "currency": inst.get("currency"),
                "side": o.get("side") or ("SELL" if qty < 0 else "BUY"), "type": o.get("type"),
                "quantity": abs(qty), "filled": abs(float(o.get("filledQuantity") or 0)),
                "limitPrice": o.get("limitPrice"), "stopPrice": o.get("stopPrice"),
                "trigger": trig, "price": px,
                "distancePct": round((trig - px) / px * 100, 2) if (trig and px) else None,
                "value": round(abs(qty) * trig, 2) if trig else None,
                "status": o.get("status"), "timeInForce": o.get("timeInForce"),
                "extendedHours": o.get("extendedHours"), "createdAt": o.get("createdAt"),
            })
    out.sort(key=lambda o: abs(o["distancePct"]) if o["distancePct"] is not None else 1e9)
    return _json_safe({"orders": out, "errors": errors, "fetchedAt": time.time()})


def _fill_row(it: dict, a: T212Account) -> dict | None:
    o, f = it.get("order") or {}, it.get("fill")
    if not f:
        return None
    wi = f.get("walletImpact") or {}
    t = o.get("ticker") or ""
    return {
        "account": a.id, "accountLabel": a.label, "id": f.get("id"), "orderId": o.get("id"),
        "ticker": t, "short": _short_ticker(t), "symbol": t212_to_yahoo(t),
        "name": (o.get("instrument") or {}).get("name") or t,
        "currency": (o.get("instrument") or {}).get("currency"),
        "side": o.get("side") or ("SELL" if float(f.get("quantity") or 0) < 0 else "BUY"),
        "orderType": o.get("type"), "quantity": abs(float(f.get("quantity") or 0)),
        "price": f.get("price"), "value": wi.get("netValue"), "fxRate": wi.get("fxRate"),
        "fee": round(sum(float(x.get("quantity") or 0) for x in wi.get("taxes") or []), 2),
        "realised": wi.get("realisedProfitLoss"), "filledAt": f.get("filledAt"),
    }


@app.get("/api/fills")
async def fills_endpoint(account: str = "all", days: int = 365, limit: int = 400):
    """Executed trades (newest first) + trading-behaviour summary."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=max(1, min(int(days), 3650)))).isoformat()
    rows, status = [], {}
    for a in _configured_accounts(account):
        try:
            items = await get_history("fills", a)
        except Exception:
            items = []
        status[a.id] = history_status("fills", a)
        rows += [r for r in (_fill_row(it, a) for it in items) if r and (r["filledAt"] or "") >= cutoff]
    rows.sort(key=lambda r: r["filledAt"] or "", reverse=True)

    def _summary(since_days: int) -> dict:
        since = (now - timedelta(days=since_days)).isoformat()
        rs = [r for r in rows if (r["filledAt"] or "") >= since]
        sells = [r for r in rs if r["side"] == "SELL" and r["realised"] is not None]
        wins = [r for r in sells if (r["realised"] or 0) > 0]
        return {"days": since_days, "trades": len(rs),
                "buys": sum(1 for r in rs if r["side"] == "BUY"), "sells": len(sells),
                "turnover": round(sum(abs(r["value"] or 0) for r in rs), 2),
                "fees": round(sum(r["fee"] for r in rs), 2),
                "realised": round(sum(r["realised"] or 0 for r in sells), 2),
                "winRate": round(len(wins) / len(sells) * 100, 1) if sells else None,
                "tickers": len({r["ticker"] for r in rs})}
    return _json_safe({"fills": rows[:max(1, min(int(limit), 2000))], "status": status,
                       "summary": {"d7": _summary(7), "d30": _summary(30), "d90": _summary(90)},
                       "fetchedAt": time.time()})


def _tax_year_start():
    from datetime import date as _date
    t = _date.today()
    y = t.year if (t.month, t.day) >= (4, 6) else t.year - 1
    return _date(y, 4, 6)


def _uk_date(iso: str | None) -> str:
    """UK calendar date (YYYY-MM-DD) of a T212 ISO timestamp."""
    dt = _parse_dt(iso or "")
    if not dt:
        return (iso or "")[:10]
    from zoneinfo import ZoneInfo
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d")
    return dt.astimezone(ZoneInfo("Europe/London")).strftime("%Y-%m-%d")


UK_TAX = {"isa_allowance": 20000.0, "cgt_exempt": 3000.0, "dividend_allowance": 500.0,
          "savings_allowance_basic": 1000.0, "savings_allowance_higher": 500.0,
          "cgt_report_proceeds": 50000.0}


@app.get("/api/tax")
async def tax_endpoint():
    """UK tax-year gauges per account (6 Apr → 5 Apr). ISA: subscriptions vs
    the £20k allowance. Invest (GIA): realised gains vs the £3k CGT annual
    exempt amount, dividends vs £500, disposal proceeds vs the £50k reporting
    threshold. Estimates from T212's average-cost realised P&L — the same-day /
    30-day matching rules are NOT applied. Not tax advice."""
    start = _tax_year_start()
    s_iso = start.isoformat()
    out = []
    for a in _configured_accounts():
        kind = "isa" if "isa" in f"{a.id} {a.label}".lower() else "gia"
        txns, fills, divs = await asyncio.gather(
            get_all_transactions(a), get_history("fills", a), get_history("dividends", a, ttl=1800.0))
        in_ty = lambda s: _uk_date(s) >= s_iso           # the tax year runs on UK clock time
        amt_of = lambda kind: sum(float(t.get("amount") or 0) for t in txns
                                  if txn_kind(t) == kind and in_ty(t.get("dateTime")))
        dep, trf, intr = amt_of("deposit"), amt_of("transfer"), amt_of("interest")
        wdr = abs(amt_of("withdraw"))
        rows = [r for r in (_fill_row(it, a) for it in fills) if r and in_ty(r["filledAt"])]
        sells = [r for r in rows if r["side"] == "SELL"]
        gains = sum(max(0.0, r["realised"] or 0) for r in sells)
        losses = sum(min(0.0, r["realised"] or 0) for r in sells)
        fees = sum(r["fee"] for r in rows)
        dv = sum(float(d.get("amount") or 0) for d in divs if in_ty(d.get("paidOn")))
        out.append({
            "id": a.id, "label": a.label, "kind": kind,
            # T212's Stocks ISA is flexible: money withdrawn can be put back in
            # the same tax year without using more allowance.
            "isaUsedTY": round(max(0.0, dep - wdr), 2) if kind == "isa" else None,
            "depositsTY": round(dep, 2), "withdrawalsTY": round(wdr, 2), "transfersTY": round(trf, 2),
            "interestTY": round(intr, 2), "dividendsTY": round(dv, 2),
            "realisedGainsTY": round(gains, 2), "realisedLossesTY": round(losses, 2),
            "realisedNetTY": round(gains + losses, 2), "feesTY": round(fees, 2),
            "disposalsTY": len(sells), "proceedsTY": round(sum(abs(r["value"] or 0) for r in sells), 2),
            "history": {"fills": history_status("fills", a), "transactions": history_status("transactions", a)},
        })
    return _json_safe({"taxYear": f"{start.year}/{str(start.year + 1)[2:]}", "start": s_iso,
                       "end": f"{start.year + 1}-04-05", "limits": UK_TAX, "accounts": out})


def _deposit_events(txns: list) -> list:
    """[(UK date, signed capital flow)] — daily totals are keyed by UK date."""
    ev = []
    for t in txns:
        flow = capital_flow(t)
        if flow is not None:
            ev.append((_uk_date(t.get("dateTime")), flow))
    return sorted(ev)


@app.get("/api/nav")
async def nav_endpoint(account: str = "all"):
    """Daily account value → deposit-neutral performance: cumulative gain
    (value − net deposits) and a time-weighted return index with drawdown,
    benchmarked against SPY / QQQ over the same days."""
    accts = _configured_accounts(account)
    per = {}
    for a in accts:
        daily = _load_daily_totals(a)
        ev = _deposit_events(await get_all_transactions(a))
        per[a.id] = (daily, ev)
    if not per:
        return {"series": []}
    # the combined curve only spans days every selected account has a value
    days = sorted(set.intersection(*[set(d) for d, _ in per.values()])) if per else []
    series, idx, peak, mdd, prev = [], 1.0, 1.0, 0.0, None
    for d in days:
        total = sum(float(per[k][0][d]) for k in per)
        deps = sum(sum(x for dt, x in ev if dt <= d) for _, ev in per.values())
        if prev is not None:
            flow = sum(sum(x for dt, x in ev if prev[0] < dt <= d) for _, ev in per.values())
            if prev[1] > 0:
                idx *= (total - flow) / prev[1]
        peak = max(peak, idx)
        dd = idx / peak - 1
        mdd = min(mdd, dd)
        series.append({"date": d, "total": round(total, 2), "deposits": round(deps, 2),
                       "gain": round(total - deps, 2), "twr": round((idx - 1) * 100, 3),
                       "drawdown": round(dd * 100, 3)})
        prev = (d, total)
    bench = {}
    if series:
        for sym in ("SPY", "QQQ"):
            try:
                bars = (await get_daily_bars(sym, "1y"))["bars"]
            except Exception:
                continue
            closes = {_bar_date(b): b["c"] for b in bars}
            base, pts, last = None, [], None
            for s in series:
                c = closes.get(s["date"], last)
                last = c if c else last
                if c and base is None:
                    base = c
                pts.append(round((c / base - 1) * 100, 3) if (c and base) else None)
            bench[sym] = pts
    return _json_safe({"series": series, "bench": bench, "maxDrawdown": round(mdd * 100, 3),
                       "accounts": [{"id": a.id, "label": a.label,
                                     "since": min(per[a.id][0]) if per[a.id][0] else None} for a in accts]})


# ============================================================================
# Portfolio risk — beta, volatility, VaR, correlation, risk contribution
# ============================================================================

def _stdev_s(xs: list) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5


def _cov(xs: list, ys: list) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx, my = sum(xs[:n]) / n, sum(ys[:n]) / n
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / (n - 1)


async def _compute_risk(account: str) -> dict:
    snap = await snapshot_cached(account)
    pos = [p for p in snap.get("positions") or [] if (p.get("marketValue") or 0) > 0]
    hold_val = sum(p["marketValue"] for p in pos)
    if not pos or hold_val <= 0:
        return {"error": "无持仓"}
    bench = ["SPY", "QQQ", "SMH"]
    keys = [p["ticker"] for p in pos] + bench + ["GBPUSD=X"]
    bar_sets = await asyncio.gather(*[get_daily_bars(k, "1y") for k in keys], return_exceptions=True)
    closes = {}
    for k, d in zip(keys, bar_sets):
        if isinstance(d, dict) and d.get("bars"):
            closes[k] = {_bar_date(b): b["c"] for b in d["bars"]}
    if "SPY" not in closes:
        return {"error": "基准数据暂缺"}
    cal = sorted(closes["SPY"])[-253:]                     # US trading-day master calendar

    def _series(k):                                         # forward-filled on the master calendar
        src, out, last = closes.get(k) or {}, [], None
        for d in cal:
            last = src.get(d, last)
            out.append(last)
        return out

    def _rets(vals):
        r = []
        for a_, b_ in zip(vals, vals[1:]):
            r.append((b_ / a_ - 1) if (a_ and b_) else 0.0)
        return r

    fx = _series("GBPUSD=X")
    fx_r = _rets(fx) if all(fx) else [0.0] * (len(cal) - 1)
    b_r = {b: _rets(_series(b)) for b in bench if b in closes}
    w = {p["ticker"]: p["marketValue"] / hold_val for p in pos}
    loc, gbp = {}, {}
    for p in pos:
        t = p["ticker"]
        if t not in closes:
            continue
        r = _rets(_series(t))
        loc[t] = r
        if (p.get("currency") or "").upper() == "USD":      # GBP investor: USD return ÷ GBPUSD move
            gbp[t] = [(1 + ri) / (1 + fi) - 1 for ri, fi in zip(r, fx_r)]
        else:
            gbp[t] = r
    covered = sum(w[t] for t in loc)
    n = len(cal) - 1
    port = [sum(w[t] * gbp[t][i] for t in gbp) / (covered or 1) for i in range(n)]
    port_loc = [sum(w[t] * loc[t][i] for t in loc) / (covered or 1) for i in range(n)]
    var_p = _stdev_s(port) ** 2 or 1e-12

    rows = []
    for p in pos:
        t = p["ticker"]
        r = loc.get(t)
        row = {"ticker": t, "short": _short_ticker(t) or t, "name": p.get("name"),
               "weight": round(w[t] * 100, 2), "marketValue": p["marketValue"],
               "currency": p.get("currency"), "covered": r is not None}
        if r:
            for b in b_r:
                vb = _stdev_s(b_r[b]) ** 2
                row[f"beta_{b}"] = round(_cov(r, b_r[b]) / vb, 2) if vb else None
            row["vol"] = round(_stdev_s(r) * (252 ** 0.5) * 100, 1)
            row["riskShare"] = round(w[t] * _cov(gbp[t], port) / var_p * 100, 1)
            row["corrPort"] = round(_cov(r, port_loc) / ((_stdev_s(r) * _stdev_s(port_loc)) or 1), 2)
        rows.append(row)

    beta = {b: round(sum(w[x["ticker"]] * (x.get(f"beta_{b}") or 0) for x in rows if x["covered"])
                     / (covered or 1), 2) for b in b_r}
    srt = sorted(port)
    k5 = max(1, int(len(srt) * 0.05))
    var95 = -srt[k5 - 1] if srt else 0.0
    cvar95 = -sum(srt[:k5]) / k5 if srt else 0.0
    sd = _stdev_s(port)
    worst_i = min(range(n), key=lambda i: port[i]) if n else None
    idx, peak, mdd = 1.0, 1.0, 0.0
    for x in port:
        idx *= 1 + x
        peak = max(peak, idx)
        mdd = min(mdd, idx / peak - 1)

    top = sorted([x for x in rows if x["covered"]], key=lambda x: -x["weight"])[:10]
    win = 120
    tail = {x["ticker"]: loc[x["ticker"]][-win:] for x in top}
    corr = [[round(_cov(tail[a_["ticker"]], tail[b_["ticker"]]) /
                   ((_stdev_s(tail[a_["ticker"]]) * _stdev_s(tail[b_["ticker"]])) or 1), 2)
             for b_ in top] for a_ in top]
    pairs = [(i, j) for i in range(len(top)) for j in range(i + 1, len(top))]
    wsum = sum(top[i]["weight"] * top[j]["weight"] for i, j in pairs)
    avg_corr = (sum(corr[i][j] * top[i]["weight"] * top[j]["weight"] for i, j in pairs) / wsum) if wsum else None
    ccy = {}
    for p in pos:
        c = (p.get("currency") or "?").upper()
        c = "GBP" if c in ("GBX", "GBP") else c
        ccy[c] = round(ccy.get(c, 0) + p["marketValue"], 2)
    total_val = (snap.get("stats") or {}).get("totalValue") or hold_val
    return {
        "asOf": cal[-1], "days": n, "holdingsValue": round(hold_val, 2), "totalValue": total_val,
        "cash": (snap.get("stats") or {}).get("totalCash"),
        "coveredWeight": round(covered * 100, 1),
        "beta": beta, "vol": round(sd * (252 ** 0.5) * 100, 1),
        "var95": {"pct": round(var95 * 100, 2), "amount": round(var95 * hold_val, 2)},
        "cvar95": {"pct": round(cvar95 * 100, 2), "amount": round(cvar95 * hold_val, 2)},
        "varParam": {"pct": round(1.645 * sd * 100, 2), "amount": round(1.645 * sd * hold_val, 2)},
        "worstDay": ({"date": cal[worst_i + 1], "pct": round(port[worst_i] * 100, 2),
                      "amount": round(port[worst_i] * hold_val, 2)} if worst_i is not None else None),
        "maxDrawdown1y": round(mdd * 100, 2),
        "effectiveN": round(1 / sum(v * v for v in w.values()), 2),
        "avgCorr": round(avg_corr, 2) if avg_corr is not None else None,
        "currency": ccy, "positions": rows,
        "corr": {"tickers": [x["short"] for x in top], "matrix": corr, "window": win},
        "fxNote": "组合收益按英镑计(美股含汇率变动);Beta 为本币口径",
    }


@app.get("/api/risk")
async def risk_endpoint(account: str = "all"):
    return _json_safe(await cache.get(f"risk:{account}", 600.0, lambda: _compute_risk(account)))


# ============================================================================
# Accounts status + server-side prefs (watchlist / price alerts / notes / UI)
# ============================================================================

@app.get("/api/accounts")
async def accounts_endpoint():
    maybe_reload_accounts()
    have = {a.id for a in ACCOUNTS if a.configured}
    missing = [] if len(have) > 1 or "invest" in have else [{
        "id": "invest", "label": "Invest",
        "envKeys": ["TRADING212_INVEST_API_KEY_ID", "TRADING212_INVEST_API_SECRET"]}]
    return {"accounts": [{"id": a.id, "label": a.label, "env": a.env, "configured": a.configured}
                         for a in ACCOUNTS],
            "missing": missing}


PREFS_FILE = HERE / "user_prefs.json"
PREFS_SCHEMA = {"watchlist": list, "alerts": list, "notes": dict, "ui": dict}


def _load_prefs() -> dict:
    d = _read_json_safe(PREFS_FILE, {})
    d = d if isinstance(d, dict) else {}
    out = {k: (d.get(k) if isinstance(d.get(k), t) else t()) for k, t in PREFS_SCHEMA.items()}
    out["rev"] = int(d.get("rev") or 0)
    out["updatedAt"] = d.get("updatedAt")
    return out


def _clean_pref(key: str, val):
    if key == "watchlist":
        seen, out = set(), []
        for s in val:
            s = re.sub(r"[^A-Z0-9.:=^-]", "", str(s).upper())[:24]
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out[:80]
    if key == "alerts":
        out = []
        for a in val[:200]:
            if not isinstance(a, dict):
                continue
            try:
                price = float(a.get("price"))
            except (TypeError, ValueError):
                continue
            out.append({"id": str(a.get("id") or f"al{int(time.time() * 1000)}")[:40],
                        "ticker": re.sub(r"[^A-Za-z0-9._]", "", str(a.get("ticker") or ""))[:24],
                        "op": ">=" if a.get("op") == ">=" else "<=", "price": price,
                        "note": str(a.get("note") or "")[:200],
                        "createdAt": a.get("createdAt"), "firedAt": a.get("firedAt")})
        return out
    return val


@app.get("/api/prefs")
async def prefs_get():
    return _load_prefs()


@app.put("/api/prefs/{key}")
async def prefs_put(key: str, request: Request):
    """Replace ONE top-level pref (watchlist | alerts | notes | ui) — per-key
    writes keep two open tabs from clobbering each other's unrelated edits."""
    if key not in PREFS_SCHEMA:
        raise HTTPException(404, f"unknown pref '{key}'")
    body = await request.json()
    val = (body or {}).get("value")
    if not isinstance(val, PREFS_SCHEMA[key]):
        raise HTTPException(400, f"'{key}' must be a {PREFS_SCHEMA[key].__name__}")
    val = _clean_pref(key, val)
    if len(json.dumps(val, ensure_ascii=False)) > 250_000:
        raise HTTPException(413, "too large")
    prefs = _load_prefs()
    prefs[key] = val
    prefs["rev"] += 1
    prefs["updatedAt"] = time.time()
    tmp = PREFS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(prefs, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PREFS_FILE)
    return prefs


@app.get("/")
async def index():
    return FileResponse(HERE / "index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8787"))
    found = shutil.which(DEFAULT_CMD.split()[0])
    print()
    print(f"  Dashboard:    http://localhost:{port}")
    print(f"  T212 env:     {T212_ENV}")
    print(f"  T212 creds:   {'set ✓' if (T212_KEY_ID and T212_SECRET) else 'MISSING — edit .env'}")
    print(f"  Agent:        {DEFAULT_CMD}  {'(found ✓)' if found else '(NOT FOUND in PATH)'}")
    print()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
