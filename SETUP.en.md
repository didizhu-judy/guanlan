# SETUP — Installing 观澜 Guānlán

<sub>[中文](SETUP.md) · English</sub>

> This document is written **for both humans and AIs**. If you are an AI (Claude Code / Codex CLI), execute in order;
> every step has **Detect / Do / Fail / Skip cost** blocks.
> The user **may skip any step marked ⚠️ Optional**; the corresponding feature will gracefully degrade, never crash the rest.

---

## Contents

1. [Pre-flight check](#1-pre-flight-check)
2. [Required — Python backend](#2-required--python-backend)
3. [Required — Trading 212 credentials](#3-required--trading-212-credentials)
4. [Optional — Finnhub (news feed)](#4-optional--finnhub-news-feed)
5. [Optional — Codex CLI (AI analysis)](#5-optional--codex-cli-ai-analysis)
6. [Launching — the four forms](#6-launching--the-four-forms)
7. [Optional — WidgetKit notification-center widget](#7-optional--widgetkit-notification-center-widget)
8. [Optional — Launch on login](#8-optional--launch-on-login)
9. [Troubleshooting](#9-troubleshooting)
10. [Security self-check](#10-security-self-check)

---

## 1. Pre-flight check

**Detect**:
```bash
sw_vers -productVersion          # need ≥ 13.0
python3 --version                # need ≥ 3.9
xcode-select -p 2>/dev/null      # if present, great; without it the first three forms still work
```

**Do**:
- macOS < 13: not supported (WidgetKit also won't run). Please upgrade.
- No Python: download 3.11+ from [python.org](https://www.python.org/downloads/), or `brew install python@3.11`.
- No Xcode Command Line Tools: `xcode-select --install` (click "Install" in the popup).

**Fail**: if `python3` won't install, stop and ask the user.

**Skip cost**: cannot be skipped (macOS + Python is the foundation).

---

## 2. Required — Python backend

**Detect**:
```bash
cd <project-root>
ls server.py requirements.txt
```

**Do**:
```bash
# a) Create a venv (don't pollute system Python)
python3 -m venv .venv
source .venv/bin/activate

# b) Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# c) PyObjC (used by menubar + desktop widget)
pip install "pyobjc-core<11" "pyobjc-framework-Cocoa<11" "pyobjc-framework-WebKit<11"
# Note: pyobjc-core<11 keeps Python 3.9 compatibility.
# If your Python ≥ 3.11, you may drop the upper bound.

# d) Health check (does not touch the real account)
python3 -c "import fastapi, httpx, yfinance, objc; print('ok')"
```

**Fail**:
- `error: command 'gcc' failed`: install Xcode CLT (step 1).
- `pyobjc-core` compile error: force a prebuilt wheel — `pip install "pyobjc-core<11" --only-binary :all:`.
- `yfinance` install fails: you may skip it for now; the K-line chart will show "no data" while everything else works.

**Skip cost**: not skippable. This is the minimal kernel.

---

## 3. Required — Trading 212 credentials

**Detect**:
```bash
cat .env 2>/dev/null | grep TRADING212_API_KEY_ID
```

**Do**:

1. Log in to [Trading 212](https://www.trading212.com/) → Settings → API → **Generate new key**.
2. **Check read-only scopes only** (do not check `orders.execute` — Guanlan never places trades):
   - ✅ `account.read`
   - ✅ `portfolio.read`
   - ✅ `history.read`
3. Copy the **Key ID** and **Secret** (the page shows them only once).
4. In the repo root, create `.env` (if not present):
   ```bash
   cp .env.example .env
   open -t .env    # opens in TextEdit
   ```
5. Fill in:
   ```
   TRADING212_API_KEY_ID=41311829xxxxxxxxxxxxxxxxx
   TRADING212_API_SECRET=Ozl0xxxxxxxxxxxxxxxxxxxxxxxx
   TRADING212_ENV=live           # or demo
   ```

**Fail**:
- 401: 99% of the time it's misused Basic Auth. **Key ID is the username, Secret is the password** — not a token in `Authorization: Bearer`. If you are an AI, check that `server.py` uses `httpx.BasicAuth`.
- 403: account-type restriction (e.g. some endpoints for ISA accounts). Guanlan handles ISA-specific quirks; report any new ones as an issue.
- 429: Trading 212 rate limit. Guanlan's stale-fallback covers this — wait 30s and retry.

**Skip cost**: cannot be skipped — no credentials means no data.

---

## 4. Optional ⚠️ — Finnhub (news feed)

**Detect**:
```bash
grep FINNHUB_API_KEY .env 2>/dev/null
```

**Do**:

1. Register a free account at [finnhub.io](https://finnhub.io/).
2. Copy the API Key from the dashboard top (40 characters; it is **not two halves joined** — that's T212's pattern).
3. Add to `.env`:
   ```
   FINNHUB_API_KEY=d872xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

**Fail**:
- Wrong key: 401. Check for stray spaces or newlines.
- Free-tier rate limit: 60 calls/min. Guanlan automatically backs off.
- Free tier lacks `/stock/candle`: this is a Finnhub policy change; Guanlan moved K-line data to yfinance, so it doesn't matter.

**Skip cost**:
- The "News" tab shows "FINNHUB_API_KEY not configured".
- Single-stock modal's "Company News" section stays blank.
- **Everything else works** — positions, menubar, desktop widget, AI chat all run without Finnhub.

---

## 5. Optional ⚠️ — Codex CLI (AI analysis)

**Detect**:
```bash
which codex && codex --version
```

**Do**:

1. Install Codex CLI (official OpenAI):
   ```bash
   brew install codex     # or: npm i -g @openai/codex
   ```
2. Log in:
   ```bash
   codex login
   # follow the OAuth flow
   ```
3. In `.env` (defaults to `codex`, can be omitted):
   ```
   AGENT_CMD=codex
   ```

**Fail**:
- `command not found: codex`: reinstall; or switch to `AGENT_CMD=claude` for Claude Code (experimental — you'll need to tweak the output parser in `server.py`).
- Codex hangs: check login + network.

**Skip cost**:
- The right-hand "Codex chat" box grays out; button reads "AI not configured".
- "Technical read" / "Portfolio health" buttons disabled.
- **Dashboard, menubar, desktop widget, WidgetKit all unaffected.**

---

## 6. Launching — the four forms

**Detect**:
```bash
curl -s http://localhost:8787/api/snapshot >/dev/null && echo "already running" || echo "not started"
```

**Do (in order, only what you want)**:

### 6.1 Backend + Safari PWA (must run)

```bash
# Option 1: double-click the launcher (recommended for users)
open Guanlan.app

# Option 2: run manually (recommended for development)
source .venv/bin/activate
python3 server.py
# Open http://localhost:8787 in Safari
# Then: File → Add to Dock for a standalone app entry
```

`Guanlan.app` will:
1. start the backend
2. wait for the port to come up
3. open the PWA in Safari

### 6.2 Menubar (Optional ⚠️)

```bash
open Guanlan-Menubar.app
```

A `观澜 ▲ £...` indicator appears in the top bar. **Left-click** opens the popover; **right-click** opens the native menu.

### 6.3 Desktop widget (Optional ⚠️)

```bash
open Guanlan-Desktop.app
```

A floating panel appears in the top-right of your screen. Drag it; position auto-saves.
**To quit**: `pkill -f desktop_widget.py` (a tray menu will come in a future version).

### 6.4 WidgetKit notification center — see section 7

---

## 7. Optional ⚠️ — WidgetKit notification-center widget

**Prerequisite**: Xcode 16 (not 26). macOS 14.x users should install Xcode 16 from [Apple Developer Downloads](https://developer.apple.com/download/applications/) rather than the App Store version (which requires macOS 26).

**Detect**:
```bash
xcodebuild -version | head -1
ls widget-kit/project.yml
```

**Do (developer)**:

```bash
cd widget-kit
brew install xcodegen          # one-time
xcodegen generate              # produces T212Companion.xcodeproj
open T212Companion.xcodeproj
# In Xcode: Product → Archive → Distribute → Copy App
# Or:       xcodebuild -scheme T212Companion -configuration Release build
```

After building:
```bash
cp -R build/Build/Products/Release/Guanlan-Widget.app /Applications/
# register with LaunchServices + pluginkit
./scripts/register-widget.sh
# launch the host app so the system discovers the widget extension
open /Applications/Guanlan-Widget.app
```

Then go to "Notification Center → Edit Widgets → search 观澜" and add Small / Medium / Large.

**Do (end user)**: the DMG already ships a pre-built `Guanlan-Widget.app`. Just run `scripts/register-widget.sh` — no Xcode needed.

**Fail**:
- Notification Center can't find "观澜": run `./scripts/register-widget.sh`, wait 30s. Still nothing? Try `killall chronod` (warning: this restarts every widget on your system, but no data is lost).
- Widget says "No data": confirm `http://localhost:8787/api/snapshot` returns 200. Widget refreshes every 15 min, or tap the refresh button in the widget for an instant reload.

**Skip cost**: no Guanlan widget in Notification Center. The other three forms are untouched.

---

## 8. Optional ⚠️ — Launch on login

**Do**:

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.guanlan.menubar.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.guanlan.menubar</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/open</string>
    <string>-a</string>
    <string>/Applications/Guanlan-Menubar.app</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
EOF
launchctl load ~/Library/LaunchAgents/com.guanlan.menubar.plist
```

Repeat for `Guanlan.app` and `Guanlan-Desktop.app` (different `Label` + path).

**Skip cost**: you'll have to open the apps manually every reboot.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| Backend 401 after 5s | Wrong T212 key, or used plain Bearer | Use Basic Auth (`server.py` already does — check `.env` spelling) |
| Menubar shows `观澜 ⚠` | `localhost:8787` not running | `open Guanlan.app` |
| Desktop widget gray, no data | Backend 429 or stale `widget.html` cache | Wait 30s (stale-fallback kicks in); force refresh from menubar right-click → "立即刷新" |
| Notification Center can't find "观澜" | LaunchServices didn't pick it up | `./scripts/register-widget.sh` |
| Codex output garbled / empty | Not logged in / network issue | `codex login`; or `AGENT_CMD=echo` to disable temporarily |
| K-line chart blank | Yahoo temporary throttle | Wait 5–10 min, recovers automatically |
| `.app` double-click says "cannot open" | Gatekeeper | Right-click → Open → confirm; or `xattr -d com.apple.quarantine <App>` |

More in [Issues](#) or in [`PLAN.md`](PLAN.md).

---

## 10. Security self-check

Run through this — **all answers should be "yes"**:

```bash
# a) Is .env really in .gitignore?
grep -q '^\.env$' .gitignore && echo "✅" || echo "❌ add .env to .gitignore"

# b) Does git status show .env?
git status --porcelain | grep -q '\.env$' && echo "❌ run git rm --cached .env now" || echo "✅"

# c) Does the backend only bind localhost?
grep -E 'host\s*=\s*["\x27]localhost' server.py >/dev/null && echo "✅" || echo "❌ check server.py launch line"

# d) Does the T212 key NOT have orders.execute?
echo "→ Verify on the T212 web UI that this key has no orders.execute scope"

# e) Is Finnhub key NOT in git history?
git log -p | grep -q "FINNHUB_API_KEY=d8" && echo "❌ key in history — run git filter-repo and rotate" || echo "✅"
```

If you accidentally pushed a commit containing a key:
1. Go to the provider and **revoke that key** immediately
2. Generate a new one and put it in `.env`
3. Rewrite history with `git filter-repo` (or delete the repo and recreate)

---

<div align="center">

Guanlan v0.1 — everything in order. Now go watch your pond.

</div>
