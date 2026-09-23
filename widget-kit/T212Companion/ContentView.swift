// Host-app window. Minimal — its only job is to exist so the widget extension
// is registered. Shows the user how to add the widget to the desktop.

import SwiftUI

struct ContentView: View {
    @State private var snap: T212Snapshot?
    @State private var fetchError: String?

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "chart.line.uptrend.xyaxis.circle.fill")
                .font(.system(size: 56))
                .foregroundStyle(.green.gradient)

            Text("观澜")
                .font(.title2.bold())

            Text("把 widget 加到桌面：右键点桌面 → **编辑小组件** → 在小组件库里找 **观澜** → 拖到桌面")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .padding(.horizontal)

            Divider().padding(.horizontal, 40)

            if let s = snap, let stats = s.stats {
                VStack(spacing: 8) {
                    Text("当前账户")
                        .font(.caption).foregroundStyle(.secondary)
                    Text("\(s.currencySymbol)\(formatMoney(stats.totalValue))")
                        .font(.title3.monospacedDigit().bold())
                    Text(formatPnl(stats.todayPnl, stats.todayPnlPct, sym: s.currencySymbol))
                        .font(.callout.monospacedDigit())
                        .foregroundStyle((stats.todayPnl ?? 0) >= 0 ? .green : .red)
                }
            } else if let err = fetchError {
                Text(err).font(.caption).foregroundStyle(.orange)
            } else {
                ProgressView().controlSize(.small)
            }

            Spacer()
        }
        .padding(.vertical, 28)
        .task { await refresh() }
    }

    @MainActor
    private func refresh() async {
        do {
            snap = try await T212Fetcher.fetch()
            fetchError = nil
        } catch {
            fetchError = "本地 server 没起来,先双击「观澜」把服务起一下。"
        }
    }

    private func formatMoney(_ v: Double?) -> String {
        guard let v = v else { return "—" }
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.maximumFractionDigits = 0
        return f.string(from: NSNumber(value: v)) ?? "—"
    }

    private func formatPnl(_ amt: Double?, _ pct: Double?, sym: String) -> String {
        guard let a = amt else { return "—" }
        let sign = a >= 0 ? "+" : ""
        let amount = "\(sign)\(sym)\(String(format: "%.2f", a))"
        if let p = pct {
            return "\(amount)  \(sign)\(String(format: "%.2f", p))%"
        }
        return amount
    }
}
