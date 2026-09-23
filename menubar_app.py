"""T212 menu bar widget — NSPopover + WKWebView version.

Status bar text shows today's P/L ("▲ £293  0.66%"). Clicking the status
item toggles a popover that embeds a WKWebView pointing at the local server's
/static/widget.html — a compact, multi-color, multi-line position list.

Backed up: menubar_app_v1_rumps.py contains the previous rumps-menu version.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

import objc
from AppKit import (
    NSApplication,
    NSStatusBar,
    NSMenu, NSMenuItem,
    NSPopover,
    NSViewController, NSView,
    NSEvent, NSEventTypeRightMouseDown, NSEventTypeLeftMouseDown,
    NSEventMaskRightMouseDown, NSEventMaskLeftMouseDown,
    NSApp,
)
from Foundation import (
    NSObject, NSURL, NSURLRequest, NSTimer,
    NSMakeRect, NSMakeSize,
)
from WebKit import (
    WKWebView, WKWebViewConfiguration,
    WKNavigationActionPolicyAllow, WKNavigationActionPolicyCancel,
)

PORT = 8787
BASE = f"http://localhost:{PORT}"
HERE = Path(__file__).resolve().parent
LAUNCHER_APP = HERE / "Guanlan.app"
WIDGET_URL = f"{BASE}/static/widget.html"

CURRENCY_SYMS = {"gbp": "£", "usd": "$", "eur": "€"}

POPOVER_W, POPOVER_H = 400, 540


def _fetch_json(path: str, timeout: float = 2.0):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def _money_abs(v) -> str:
    try: return f"{abs(float(v)):,.2f}"
    except Exception: return "—"


class MenubarController(NSObject):
    """Owns the status item, popover, webview, and refresh timer."""

    def init(self):
        self = objc.super(MenubarController, self).init()
        if self is None:
            return None

        # ---- Status item in menu bar (-1 = variable length) ----
        self.statusItem = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
        button = self.statusItem.button()
        button.setTitle_("观澜 …")
        button.setTarget_(self)
        button.setAction_(b"statusItemClicked:")
        # Receive both left and right mouse-down events
        button.sendActionOn_(NSEventMaskLeftMouseDown | NSEventMaskRightMouseDown)

        # ---- Popover ----
        self.popover = NSPopover.alloc().init()
        self.popover.setBehavior_(1)  # NSPopoverBehaviorTransient — auto-dismiss
        self.popover.setContentSize_(NSMakeSize(POPOVER_W, POPOVER_H))
        self.popover.setAnimates_(True)

        # WKWebView config — keep drawsBackground=True so the page's dark
        # CSS background paints opaquely. widget.html is served with
        # Cache-Control: no-store from server.py so we don't need to disable
        # WKWebView's data store here.
        cfg = WKWebViewConfiguration.alloc().init()
        try:
            cfg.preferences().setValue_forKey_(True, "developerExtrasEnabled")
        except Exception:
            pass
        frame = NSMakeRect(0, 0, POPOVER_W, POPOVER_H)
        self.webview = WKWebView.alloc().initWithFrame_configuration_(frame, cfg)
        try:
            self.webview.setInspectable_(True)
        except Exception:
            pass

        # Force the popover itself into dark mode so the rounded chrome around
        # the webview matches the page's #0e1117 background.
        try:
            from AppKit import NSAppearance
            self.popover.setAppearance_(
                NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
            )
        except Exception:
            pass
        # Intercept t212:// links (e.g. "open-dashboard") so the in-page button works
        self.webview.setNavigationDelegate_(self)

        url = NSURL.URLWithString_(WIDGET_URL)
        self.webview.loadRequest_(NSURLRequest.requestWithURL_(url))

        # Host the webview in a view controller
        self.vc = NSViewController.alloc().init()
        self.vc.setView_(self.webview)
        self.popover.setContentViewController_(self.vc)

        # ---- Right-click context menu (Quit / Open Dashboard / Refresh) ----
        self.contextMenu = NSMenu.alloc().init()
        items = [
            ("打开 Dashboard", b"openDashboard:"),
            ("立即刷新",        b"refreshNow:"),
            ("",                None),               # separator placeholder
            ("退出",            b"quitApp:"),
        ]
        for title, sel in items:
            if not title:
                self.contextMenu.addItem_(NSMenuItem.separatorItem())
                continue
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, "")
            mi.setTarget_(self)
            self.contextMenu.addItem_(mi)

        # ---- Periodic title refresh ----
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            30.0, self, b"refreshTitle:", None, True
        )
        self.refreshTitle_(None)

        return self

    # ---------- Click handling ----------

    def statusItemClicked_(self, sender):
        event = NSApp.currentEvent()
        if event is not None and event.type() == NSEventTypeRightMouseDown:
            # Pop the native right-click menu attached to the status item
            self.statusItem.popUpStatusItemMenu_(self.contextMenu)
            return
        # Left click → toggle popover
        if self.popover.isShown():
            self.popover.performClose_(sender)
        else:
            button = self.statusItem.button()
            # Force webview to revalidate on each open. Server sends
            # Cache-Control: no-store for widget.html so this is a real refetch.
            self.webview.reload()
            self.popover.showRelativeToRect_ofView_preferredEdge_(
                button.bounds(), button, 3   # NSRectEdgeMinY = 3 → below
            )

    # ---------- Menu actions ----------

    def openDashboard_(self, _sender):
        subprocess.Popen(["/usr/bin/open", str(LAUNCHER_APP)])

    def refreshNow_(self, _sender):
        self.refreshTitle_(None)
        try: self.webview.reload()
        except Exception: pass

    def quitApp_(self, _sender):
        NSApp.terminate_(self)

    # ---------- Status bar title refresh ----------

    def refreshTitle_(self, _timer):
        snap = _fetch_json("/api/snapshot")
        button = self.statusItem.button()
        if not snap:
            button.setTitle_("观澜 ⚠")
            return
        s = snap.get("stats") or {}
        cur = (snap.get("info") or {}).get("currencyCode", "").lower()
        sym = CURRENCY_SYMS.get(cur, "")
        amt = s.get("todayPnl")
        pct = s.get("todayPnlPct")
        # Lead the title with the brand so the menubar reads as
        # "观澜  ▲ £293.33  0.65%" — establishes identity at a glance,
        # and keeps the data right next to it.
        if amt is not None and pct is not None:
            arrow = "▲" if amt >= 0 else "▼"
            button.setTitle_(f"观澜  {arrow} {sym}{_money_abs(amt)}  {abs(pct):.2f}%")
        elif amt is not None:
            arrow = "▲" if amt >= 0 else "▼"
            button.setTitle_(f"观澜  {arrow} {sym}{_money_abs(amt)}")
        else:
            button.setTitle_("观澜")

    # ---------- WKNavigationDelegate ----------
    # Intercept the in-popover "Open Dashboard" button (t212://open-dashboard).
    # Lets the WebView call out to native code without us setting up a full
    # JS↔Native bridge.

    def webView_decidePolicyForNavigationAction_decisionHandler_(
        self, webview, action, handler
    ):
        url = action.request().URL()
        scheme = (url.scheme() or "").lower()
        if scheme == "t212":
            host = (url.host() or "")
            if host == "open-dashboard":
                self.openDashboard_(None)
                self.popover.performClose_(None)
            handler(WKNavigationActionPolicyCancel)
            return
        handler(WKNavigationActionPolicyAllow)


_DELEGATE_REF = None  # module-level pin to prevent GC


def main():
    global _DELEGATE_REF
    app = NSApplication.sharedApplication()
    _DELEGATE_REF = MenubarController.alloc().init()
    app.run()


if __name__ == "__main__":
    main()
