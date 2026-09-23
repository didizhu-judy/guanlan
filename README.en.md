<div align="center">

<img src="static/icon-512.png" alt="Guanlan" width="128" height="128" />

# Guānlán · 观澜

**A poetic Trading 212 portfolio dashboard, running entirely on your Mac.**

<sub><a href="README.md">中文</a> · English</sub>

<br/>

> A half-acre pond opens like a mirror,
> sky-light and cloud-shadows wandering together.
> How can the water be so clear, you ask?
> Because at the source — there is fresh, living water.
> <sub>—— Zhu Xi, "Reflections on Reading"</sub>

<br/>

[![macOS](https://img.shields.io/badge/macOS-13%2B-1c1c1e?style=flat&logo=apple&logoColor=white)](#)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776ab?style=flat&logo=python&logoColor=white)](#)
[![FastAPI](https://img.shields.io/badge/FastAPI-local-009688?style=flat&logo=fastapi&logoColor=white)](#)
[![License](https://img.shields.io/badge/license-MIT-555?style=flat)](#license)
[![Status](https://img.shields.io/badge/status-v0.1%20preview-d29922?style=flat)](#)

</div>

---

## 🆕 v2 · Decision desk (2026-09)

The full dashboard was rebuilt as a *decision desk*: less P&L tick-watching, more of what a decision actually needs.

- **Watch**: account overview (realised + unrealised = total return; realised includes dividends, cash interest and FX-fee breakdown) · today's briefing · holdings grouped by sector (30-day trend / intraday range / MA & RSI) · watchlist · market barometer · holdings map · catalysts · monitor signals
- **Stock panel**: own candlestick chart (options walls, working orders, price alerts and your past fills overlaid) · technical check-up with plain-language notes · options range · fundamentals · thesis notes · price alerts · risk-based position sizing
- **Risk**: beta / volatility / VaR · weight vs risk contribution · stress tests · correlation · deposit-neutral performance curve
- **Calendar & news**: holdings' earnings + US macro releases + FOMC, in local time
- **Trades & tax**: distance to working orders · fill journal · trading-behaviour stats · UK ISA allowance and CGT annual-exempt estimates
- **Multi-account**: ISA and Invest each use their own API key (`TRADING212_INVEST_API_KEY_ID / _SECRET` in `.env`); view merged or per account; `.env` edits apply live
- Paper / night themes · red-up / green-up colour toggle · privacy blur · ⌘K search

---

## 1. What is Guānlán?

**Guānlán** (观澜) means "watching the waves" — a name borrowed from Zhu Xi's poem about fresh water as the source of clarity. The aim of this little dashboard is similar: **turn the noisy ticker tape into a quiet, visible ripple on your desk**.

Everything runs **on your Mac**. No third-party servers, no telemetry, your API credentials live only in `.env`. All data converges into a small pond at `localhost:8787`, then surfaces in four shapes:

| Form | Where you see it | When |
| :--- | :--- | :--- |
| 🖥️ **Full panel** | Safari PWA (fullscreen) | Reading the market, asking the AI, analyzing positions |
| 📊 **Menubar** | macOS top bar — `观澜  ▲ £293  0.66%` | Walking, coding, glancing |
| 🪟 **Desktop widget** | Translucent floating window, draggable | Always-on, in a corner |
| 📱 **Notification Center** | Native WidgetKit Small / Medium / Large | Swipe-down, one second to know |

All four share the same backend cache — they don't fight each other for the rate limit.

---

## 2. What can it do?

<table>
<tr>
<td width="50%" valign="top">

### 📈 Watch
- Live positions and unrealized P/L
- Today / Floating / All-time P/L cards
- Position-weight treemap (with ETF country pass-through)
- Per-stock 1-year chart with SMA / EMA / MACD / RSI / Bollinger Bands
- Today's movers (1h ≥ 4% / day ≥ 10% / swing ≥ 6%)

</td>
<td width="50%" valign="top">

### 🤖 Ask
- Embedded **Codex CLI** chat (local, no API bill)
- One-click "Portfolio Health", "Technical Read", "News Digest"
- AI is fed pre-fetched market JSON — it does not crawl on its own
- Chat history persists in localStorage (4h TTL)

</td>
</tr>
<tr>
<td valign="top">

### 🪶 Comfortable
- Fully Chinese-language UI, immersive dark theme
- Menubar + desktop widget can run simultaneously
- Closing and reopening will not re-trigger AI analyses
- Cache has a *stale-fallback* — Trading 212 rate-limits no longer freeze the UI

</td>
<td valign="top">

### 🔒 Safe
- Credentials stay in `.env`; nothing leaves your machine
- T212 API uses read-only scopes only (`orders.execute` never checked)
- Backend listens on `localhost`, never `0.0.0.0`
- Fully open source, auditable, hackable

</td>
</tr>
</table>

---

## 3. Get started

### A · DMG (recommended for non-developers)

1. Download `观澜-v0.1.dmg` from [Releases](#).
2. Open it, drag the four `.app` bundles into `Applications`.
3. Double-click `用 Claude Code 自动安装.command` (or `用 Codex 自动安装.command`) and let the AI walk you through credential setup.
4. Launch `观澜.app`. Done.

> No Claude / Codex installed? Double-click `手动安装.command` — a shell wizard walks you through it.

### B · From source (for developers)

```bash
git clone https://github.com/<your-username>/guanlan.git
cd guanlan
./scripts/install.sh        # creates venv, installs deps, guides .env setup
./scripts/start.sh          # starts backend + opens Safari PWA
```

Then read [`SETUP.en.md`](SETUP.en.md) for the menubar / desktop-widget / WidgetKit details.

### C · Hand this README to an AI

```bash
# After installing Claude Code or Codex CLI, clone the repo and, inside it:
claude   # or  codex

# Then say:
#   "Please read SETUP.en.md and install Guanlan on this Mac for me."
```

`SETUP.en.md` is written for AI consumption — every step has *detect / do / fail / skip* blocks, so the AI can install everything without further hand-holding.

---

## 4. What each form looks like

<table>
<tr>
<th>🖥️ Safari PWA</th>
<th>📊 Menubar</th>
</tr>
<tr>
<td>
Full dashboard: top stat cards, treemap, positions table, Codex chat panel, plus Technical / News analysis tabs.<br/><br/>
Use Safari's "Add to Dock" to install it as a standalone app with the Guanlan icon in your Dock.
</td>
<td>
Menubar title shows <code>观澜  ▲ £293  0.66%</code> live.<br/><br/>
Left-click opens a popover (mini dashboard + positions + movers badge). Right-click opens a native menu (Open Dashboard / Refresh / Quit).
</td>
</tr>
<tr>
<th>🪟 Desktop widget</th>
<th>📱 Notification Center (WidgetKit)</th>
</tr>
<tr>
<td>
Translucent floating window, sitting <em>above</em> desktop icons but <em>below</em> app windows. Draggable; position auto-saved.<br/><br/>
Clicking it does not steal focus — it watches quietly.
</td>
<td>
Native SwiftUI widget. Small / Medium / Large sizes supported.<br/><br/>
Medium and Large have an inline "Refresh" button powered by AppIntents — tap it for an instant reload.
</td>
</tr>
</table>

---

## 5. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       Your Mac                               │
│                                                              │
│   ┌───────────────┐    ┌──────────────────────────────┐     │
│   │  Trading 212  │ ←→ │   FastAPI backend (localhost) │     │
│   └───────────────┘    │                              │     │
│   ┌───────────────┐    │   • cache + stale fallback   │     │
│   │   Finnhub     │ ←→ │   • workspace → Codex CLI    │     │
│   └───────────────┘    │   • alert engine             │     │
│   ┌───────────────┐    │   • movers / technicals      │     │
│   │   yfinance    │ ←→ │   :8787                      │     │
│   └───────────────┘    └──────┬───────────────────────┘     │
│                               │                              │
│       ┌───────────────────────┼───────────────────────┐     │
│       ↓                       ↓                       ↓     │
│  ┌─────────┐            ┌────────────┐         ┌──────────┐ │
│  │ Safari  │            │ Menubar +  │         │ WidgetKit│ │
│  │  PWA    │            │ Desktop    │         │  Center  │ │
│  └─────────┘            └────────────┘         └──────────┘ │
└─────────────────────────────────────────────────────────────┘
```

- **Backend**: `server.py` (FastAPI), cache layer with TTL + stale-fallback
- **AI**: Codex CLI as a child process; we pre-stage `snapshot.json` / `market/*.json` into `/tmp/t212-codex-workspace`
- **Storage**: `transactions_cache.json` / `daily_totals.json` / `price_log.json` / `alert_queue.json`

See [`PLAN.md`](PLAN.md) and [`SETUP.en.md`](SETUP.en.md) for more.

---

## 6. Tech stack

| Layer | What we use |
| :--- | :--- |
| Backend | FastAPI · httpx · python-dotenv |
| Data | Trading 212 API (HTTP Basic Auth) · Finnhub · yfinance |
| Frontend | Plain HTML + JS (no build step) · Chart.js · D3 treemap |
| Menubar / Desktop widget | PyObjC (NSStatusBar / NSPopover / WKWebView / NSWindow) |
| Notification Center | SwiftUI · WidgetKit · AppIntents (Xcode 16 project) |
| AI | Codex CLI (local, no API billing) |

---

## 7. Dependencies — what's required vs. optional

Guanlan is built on the principle *install what you can, degrade gracefully for the rest*:

| Dependency | What it powers | If you skip it |
| :--- | :--- | :--- |
| Trading 212 API Key | Live data | **Required** — without this, no data |
| Python 3.9+ | Backend | **Required** |
| Codex CLI | AI analysis | Chat panel grays out; everything else works |
| Finnhub API Key | News, single-stock quotes | News tab shows "not configured"; positions still work |
| Xcode 16 | Builds WidgetKit | Notification-center widget unavailable; other three forms work |
| `create-dmg` | Builds the DMG | Developer-only |

---

## 8. Open source & privacy

- **MIT license** — fork, modify, ship commercially
- **No telemetry** — this is a personal tool; it never phones home
- **Local credentials** — `.env` is in `.gitignore`, can't accidentally be pushed
- **Audit-friendly** — fewer than 5k lines of code, everything visible

If you're worried about leaking API keys, there's a [credential self-check list](SETUP.en.md#10-security-self-check) that walks you through it.

---

## 9. Why the name?

- **观澜 (Guānlán)** literally means "to watch the waves". The phrase comes from Zhu Xi's *Reflections on Reading*, where fresh water at the source keeps a pond clear. The hope: this dashboard becomes the fresh water in your market-watching habit.
- **The icon**: a golden bull walking through waves, an arrow piercing the clouds — wishing you a bull market, and the calm to cross any wave.
- **Inspiration**: thanks to Trading 212 for the API, to Codex for making local AI possible, and to every quant who has ever filled their monitors with Bloomberg Terminal.

---

## 10. Contributing

- Bug? Open an issue.
- Want another form (Touch Bar, Today Extension)? Send a PR.
- Just want to chat? Guanlan watches you — you can come watch back.

<div align="center">
<br/>
<sub>Built with ❤️, SwiftUI, PyObjC, FastAPI · 2026</sub>
<br/>
<sub><b>观澜  v0.1</b></sub>
</div>

<a id="license"></a>

## License

MIT © 2026
