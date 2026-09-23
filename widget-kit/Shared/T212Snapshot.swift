// Shared data model + fetcher used by BOTH the host app and the widget
// extension. Keep this file in the "Shared" group in Xcode and add it to both
// targets' Compile Sources so they can refer to the same types.
//
// The widget pulls JSON from the local FastAPI server at http://localhost:8787
// and just decodes what it needs into a flat snapshot for SwiftUI rendering.

import Foundation

struct T212Snapshot: Codable {
    let env: String
    let info: Info?
    let cash: Cash?
    let positions: [Position]
    let stats: Stats?
    let fetchedAt: Double?

    struct Info: Codable {
        let currencyCode: String?
    }

    struct Cash: Codable {
        let total: Double?
        let free: Double?
        let invested: Double?
        let ppl: Double?
        let result: Double?
    }

    struct Position: Codable, Identifiable {
        let ticker: String
        let name: String?
        let currency: String?
        let quantity: Double
        let averagePrice: Double
        let currentPrice: Double
        let marketValue: Double
        let ppl: Double
        let pplPct: Double
        let dayChangePct: Double?

        var id: String { ticker }
        var shortTicker: String {
            ticker.split(separator: "_").first.map(String.init) ?? ticker
        }
    }

    struct Stats: Codable {
        let totalCost: Double?
        let totalValue: Double?
        let holdingsValue: Double?
        let totalCash: Double?
        let todayPnl: Double?
        let todayPnlPct: Double?
        let unrealizedPnl: Double?
        let unrealizedPnlPct: Double?
        let allTimePnl: Double?
        let allTimePnlPct: Double?
    }
}

extension T212Snapshot {
    var currencySymbol: String {
        switch (info?.currencyCode ?? "").lowercased() {
        case "gbp": return "£"
        case "usd": return "$"
        case "eur": return "€"
        default:    return ""
        }
    }
}

enum T212Fetcher {
    /// Single-shot fetch from the local dashboard server. Async/await API.
    static func fetch() async throws -> T212Snapshot {
        let url = URL(string: "http://localhost:8787/api/snapshot")!
        var req = URLRequest(url: url)
        req.timeoutInterval = 4
        let (data, _) = try await URLSession.shared.data(for: req)
        let dec = JSONDecoder()
        return try dec.decode(T212Snapshot.self, from: data)
    }
}
