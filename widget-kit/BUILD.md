# T212 Widget — Build Guide

Native macOS 14+ desktop widget (WidgetKit + SwiftUI). All source files are
in this directory; you just need to create the Xcode project shell and drop
them in.

## 0. Prereqs

- macOS Sonoma 14.5+ (you have 14.8.3 ✓)
- Xcode 16.x installed at `/Applications/Xcode.app`
- Apple ID signed into Xcode (System Settings → Apple ID is enough)

## 1. Create the Xcode project (one-time setup, ~5 min)

1. Open **Xcode** → **File → New → Project**
2. Pick **macOS** tab → **App** → Next
3. Fill in:
   - Product Name: **T212Companion**
   - Team: pick your Apple ID (or "None — sign locally")
   - Organization Identifier: `com.local` (anything works)
   - Interface: **SwiftUI**
   - Language: **Swift**
   - Storage: **None**
   - Tests: uncheck
4. Save location: pick **this `widget-kit/` directory**. Xcode will create
   `T212Companion.xcodeproj` next to the source folders we already have.
5. After the project opens, **File → New → Target** → **macOS** → **Widget Extension**
   - Product Name: **T212WidgetExtension**
   - Include Configuration Intent: **uncheck**
   - Activate scheme: Yes

## 2. Plug in our source files

In Xcode's left sidebar (Project Navigator):

1. **Delete** the auto-generated files Xcode just created
   (right-click → Move to Trash, choose "Move to Trash"):
   - `T212Companion/T212CompanionApp.swift`        (auto-generated)
   - `T212Companion/ContentView.swift`              (auto-generated)
   - `T212Companion/Assets.xcassets` — KEEP (Xcode needs it)
   - `T212WidgetExtension/T212WidgetExtension.swift` (auto-generated)
   - Any *.intentdefinition file in widget folder

2. **Add our source files** (right-click each group → Add Files to "T212Companion"...)
   - Add `T212Companion/T212CompanionApp.swift` to the **T212Companion** target
   - Add `T212Companion/ContentView.swift`       to the **T212Companion** target
   - Add `T212WidgetExtension/T212WidgetExtension.swift` to **T212WidgetExtension**
   - Add `T212WidgetExtension/T212Provider.swift`        to **T212WidgetExtension**
   - Add `T212WidgetExtension/T212WidgetView.swift`      to **T212WidgetExtension**

3. **Important — add `Shared/T212Snapshot.swift` to BOTH targets**:
   - Right-click → Add Files → select `Shared/T212Snapshot.swift`
   - In the dialog, check BOTH `T212Companion` AND `T212WidgetExtension`
     under "Add to targets"

## 3. Settings to tweak in Xcode

For each of the two targets (T212Companion and T212WidgetExtension):

- **General tab → Minimum Deployments → macOS = 14.0**
- **Signing & Capabilities tab**:
  - Team: your Apple ID
  - Signing Certificate: "Sign to Run Locally" (no dev account needed)
  - Click **+ Capability → App Sandbox** (if not already there)
  - Inside App Sandbox, check **Outgoing Connections (Client)** — widget
    needs this to reach `localhost:8787`

For **the widget target only** (T212WidgetExtension):
- Build Settings → "Info.plist File" → point to our
  `T212WidgetExtension/Info.plist` (so the ATS NSAllowsLocalNetworking
  exception we set takes effect)
- Same for T212Companion target.

## 4. Build and run (~30 sec)

1. Pick scheme **T212Companion** in the toolbar
2. Click ▶️ (or ⌘R)
3. The host app window appears — close it (host doesn't need to stay open)

## 5. Add the widget to the desktop

1. Make sure the local server is running:
   ```
   open T212-Dashboard.app
   ```
   (or just visit http://localhost:8787 in any browser to wake it up)

2. Right-click anywhere on the desktop → **Edit Widgets**
3. Scroll the widget gallery on the left, find **T212 持仓**
4. Drag it onto your desktop (try **Medium** or **Large** size first)
5. Done — widget pulls fresh data every ~15 min (system-decided cadence)

## 6. If things go wrong

- Widget shows "本地 server 未连接" → start `T212-Dashboard.app`
- Widget shows old data → that's the system refresh budget; right-click the
  widget → there's no manual refresh, but data will update next cycle
- Build fails on "Sign to Run Locally" → System Settings → Privacy & Security
  → scroll down and approve the developer ID prompt that pops up

## File layout (what you should see in this directory)

```
widget-kit/
├── BUILD.md                          ← this file
├── T212Companion.xcodeproj/          ← created by Xcode in step 1
├── Shared/
│   └── T212Snapshot.swift            (added to BOTH targets)
├── T212Companion/
│   ├── T212CompanionApp.swift        (host target only)
│   ├── ContentView.swift             (host target only)
│   ├── Info.plist
│   └── Assets.xcassets/              (auto-generated, keep)
└── T212WidgetExtension/
    ├── T212WidgetExtension.swift     (widget target only)
    ├── T212Provider.swift            (widget target only)
    ├── T212WidgetView.swift          (widget target only)
    └── Info.plist
```
