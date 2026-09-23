// Host app entry point. WidgetKit requires the widget extension to live
// inside a regular .app bundle, so this acts as the minimal "shell" the
// widget gets registered through.
//
// Tapping the app icon just brings up a tiny status screen that explains
// how to add the widget; the real action is right-clicking the desktop and
// picking T212 from the widget gallery.

import SwiftUI

@main
struct T212CompanionApp: App {
    var body: some Scene {
        WindowGroup("观澜") {
            ContentView()
                .frame(minWidth: 380, minHeight: 320)
        }
        .windowResizability(.contentSize)
    }
}
