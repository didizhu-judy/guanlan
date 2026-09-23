// TimelineProvider — WidgetKit calls this to ask "what should I render now?"
// We hit the local server at http://localhost:8787/api/snapshot and turn the
// JSON into a TimelineEntry that the SwiftUI view can render.
//
// macOS rate-limits how often widgets get refreshed (the system decides based
// on activity, battery, etc.). We *ask* for ~15-minute refreshes; the actual
// cadence depends on the system's widget budget.

import WidgetKit
import SwiftUI
import Foundation

struct T212Entry: TimelineEntry {
    let date: Date
    let snapshot: T212Snapshot?
    let errorMessage: String?
}

struct T212Provider: TimelineProvider {

    func placeholder(in context: Context) -> T212Entry {
        T212Entry(date: Date(), snapshot: nil, errorMessage: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping (T212Entry) -> Void) {
        Task {
            let entry = await loadEntry()
            completion(entry)
        }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<T212Entry>) -> Void) {
        Task {
            let entry = await loadEntry()
            // Ask the system to wake us in ~15 minutes. It may honor it, or
            // delay it depending on the budget.
            let next = Date().addingTimeInterval(15 * 60)
            completion(Timeline(entries: [entry], policy: .after(next)))
        }
    }

    private func loadEntry() async -> T212Entry {
        do {
            let snap = try await T212Fetcher.fetch()
            return T212Entry(date: Date(), snapshot: snap, errorMessage: nil)
        } catch {
            return T212Entry(
                date: Date(),
                snapshot: nil,
                errorMessage: "本地 server 未连接 — 双击「观澜」"
            )
        }
    }
}
