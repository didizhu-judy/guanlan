"""T212 desktop widget — borderless transparent floating window on the Mac
desktop. Reuses /static/widget.html for its content.

Position is persisted to .desktop_widget_pos.json next to this file, so the
widget remembers where you dragged it.

Quit via: pkill -f desktop_widget.py  (or right-click → Quit if you wire that)
"""
from __future__ import annotations

import json
from pathlib import Path

import objc
from AppKit import (
    NSApplication,
    NSWindow, NSWindowStyleMaskBorderless,
    NSBackingStoreBuffered, NSColor, NSScreen,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
    NSMenu, NSMenuItem,
    NSEventTypeRightMouseDown, NSEventTrackingRunLoopMode,
    NSApp,
)
from Foundation import (
    NSObject, NSMakeRect, NSURL, NSURLRequest,
    NSNotificationCenter,
)
from WebKit import WKWebView, WKWebViewConfiguration

PORT = 8787
HERE = Path(__file__).resolve().parent
LAUNCHER_APP = HERE / "Guanlan.app"
POS_FILE = HERE / ".desktop_widget_pos.json"

WIDGET_W, WIDGET_H = 360, 480
WIDGET_URL = f"http://localhost:{PORT}/static/widget.html?mode=desktop"


class _NonFocusingWindow(NSWindow):
    """An NSWindow that refuses to become key/main — clicking it does NOT pull
    it to the front above app windows. Needed so the widget stays at desktop
    level even when the user clicks it accidentally."""
    def canBecomeKeyWindow(self):
        return False
    def canBecomeMainWindow(self):
        return False


# CGWindowLevel constants (we don't import Quartz to keep deps minimal).
# kCGDesktopIconWindowLevel = -2147483643 (where Finder draws desktop icons)
# kCGNormalWindowLevel       = 0          (regular app windows)
# We want the widget BETWEEN those — above icons, below normal apps.
DESKTOP_WIDGET_LEVEL = -2147483642   # = kCGDesktopIconWindowLevel + 1


class DesktopWidget(NSObject):
    def init(self):
        self = objc.super(DesktopWidget, self).init()
        if self is None:
            return None

        # ----- Position: load saved, fall back to top-right of main screen
        x, y = self._load_pos()
        frame = NSMakeRect(x, y, WIDGET_W, WIDGET_H)

        # ----- Window: borderless, transparent, behind app windows (desktop-y)
        self.window = _NonFocusingWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        # Opaque dark window so that *something* shows before the webview
        # finishes loading — previously the clear background + lazy webview
        # paint made the widget look like an empty gray rectangle.
        self.window.setOpaque_(True)
        self.window.setBackgroundColor_(
            NSColor.colorWithSRGBRed_green_blue_alpha_(0.055, 0.067, 0.090, 1.0)
        )
        # Desktop-icon-level + 1 → above wallpaper/icons, behind any app window.
        # This is the key fix: previously we used NSFloatingWindowLevel (3) which
        # forced it to always sit on top of everything — too intrusive.
        self.window.setLevel_(DESKTOP_WIDGET_LEVEL)
        # Drag-anywhere on the empty background (under WKWebView).
        self.window.setMovableByWindowBackground_(True)
        # Stick to all Spaces, fixed position during Mission Control.
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
        )
        self.window.setHasShadow_(True)

        # ----- WKWebView content
        cfg = WKWebViewConfiguration.alloc().init()
        # Enable Safari-style Web Inspector — right-click in the widget will
        # have a "Check Element" option that opens DevTools. Critical for
        # debugging why JS renders fine in Safari but not in WKWebView.
        try:
            cfg.preferences().setValue_forKey_(True, "developerExtrasEnabled")
        except Exception:
            pass
        self.webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, WIDGET_W, WIDGET_H), cfg,
        )
        # On macOS 13.3+ this is the public API for the inspectable flag.
        try:
            self.webview.setInspectable_(True)
        except Exception:
            pass

        self.webview.loadRequest_(
            NSURLRequest.requestWithURL_(NSURL.URLWithString_(WIDGET_URL))
        )

        self.window.setContentView_(self.webview)
        self.window.makeKeyAndOrderFront_(None)

        # Save position whenever the user drags it
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, b"windowDidMove:", "NSWindowDidMoveNotification", self.window,
        )

        return self

    # ---------- position persistence ----------

    def _default_pos(self):
        screen = NSScreen.mainScreen().visibleFrame()
        # Top-right corner with a small margin
        x = screen.origin.x + screen.size.width  - WIDGET_W - 24
        y = screen.origin.y + screen.size.height - WIDGET_H - 24
        return x, y

    def _load_pos(self):
        try:
            d = json.loads(POS_FILE.read_text())
            return float(d["x"]), float(d["y"])
        except Exception:
            return self._default_pos()

    def windowDidMove_(self, _notification):
        f = self.window.frame()
        try:
            POS_FILE.write_text(json.dumps(
                {"x": float(f.origin.x), "y": float(f.origin.y)},
            ))
        except Exception:
            pass


_DELEGATE_REF = None


def main():
    global _DELEGATE_REF
    app = NSApplication.sharedApplication()
    _DELEGATE_REF = DesktopWidget.alloc().init()
    app.run()


if __name__ == "__main__":
    main()
