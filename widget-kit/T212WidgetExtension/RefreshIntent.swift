// AppIntent that triggers a widget timeline reload. macOS 14+ uses
// Button(intent:) inside widget views to wire interactive elements — the
// system runs the intent in the background and the widget refreshes.

import AppIntents
import WidgetKit

struct RefreshIntent: AppIntent {
    static var title: LocalizedStringResource = "刷新观澜"
    static var description = IntentDescription("立即重新拉取最新数据")

    // Hide this from the Shortcuts app — it's only meant for the widget button.
    static var isDiscoverable: Bool = false

    func perform() async throws -> some IntentResult {
        WidgetCenter.shared.reloadTimelines(ofKind: "T212PortfolioWidget")
        return .result()
    }
}
