// SwiftUI views for the widget. WidgetKit asks us to lay out for 3 standard
// sizes — small (~155×155), medium (~329×155), large (~329×345). Each shows
// progressively more data:
//
//   small:  today's P/L only
//   medium: today + unrealized + top 3 positions
//   large:  today + unrealized + all positions table + alert count

import SwiftUI
import WidgetKit
import AppIntents

private struct Palette {
    static let bg     = Color(red: 0.05, green: 0.07, blue: 0.10)
    static let panel  = Color(red: 0.09, green: 0.11, blue: 0.14)
    static let border = Color(red: 0.17, green: 0.20, blue: 0.25)
    static let muted  = Color(red: 0.55, green: 0.58, blue: 0.62)
    static let green  = Color(red: 0.25, green: 0.73, blue: 0.31)
    static let red    = Color(red: 0.97, green: 0.32, blue: 0.29)
    static let accent = Color(red: 0.35, green: 0.65, blue: 1.00)
}

struct T212WidgetView: View {
    @Environment(\.widgetFamily) var family
    let entry: T212Entry

    var body: some View {
        Group {
            switch family {
            case .systemSmall:  smallView
            case .systemMedium: mediumView
            case .systemLarge:  largeView
            default:            mediumView
            }
        }
        .foregroundStyle(.white)
    }

    // ---------- Small (155x155) ----------
    private var smallView: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text("观澜").font(.caption2.bold()).foregroundStyle(Palette.muted)
                Text("v0.1").font(.system(size: 8)).foregroundStyle(Palette.muted.opacity(0.7))
                Spacer()
                refreshButton(showText: false)
            }
            if let err = entry.errorMessage {
                Text(err).font(.caption2).foregroundStyle(.orange)
                    .multilineTextAlignment(.leading)
            } else if let snap = entry.snapshot, let st = snap.stats {
                Text(amtString(st.todayPnl, sym: snap.currencySymbol))
                    .font(.title2.monospacedDigit().bold())
                    .foregroundStyle(color(for: st.todayPnl))
                Text(pctString(st.todayPnlPct))
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(color(for: st.todayPnl))
                Spacer(minLength: 6)
                HStack(spacing: 6) {
                    Text("浮").font(.caption2).foregroundStyle(Palette.muted)
                    Text(amtString(st.unrealizedPnl, sym: snap.currencySymbol, signed: true))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(color(for: st.unrealizedPnl))
                }
            } else {
                Text("加载中…").font(.caption2).foregroundStyle(Palette.muted)
            }
            Spacer()
            Text(updatedString())
                .font(.system(size: 9)).foregroundStyle(Palette.muted)
        }
        .padding(2)
    }

    // ---------- Medium (329x155) ----------
    private var mediumView: some View {
        VStack(spacing: 8) {
            statsRow(spread: true)
            if let snap = entry.snapshot, !snap.positions.isEmpty {
                ForEach(Array(snap.positions.prefix(3))) { p in
                    positionRow(p, sym: snap.currencySymbol)
                }
            } else if entry.errorMessage != nil {
                Text(entry.errorMessage ?? "")
                    .font(.caption).foregroundStyle(.orange)
            }
            Spacer(minLength: 0)
            footer
        }
    }

    // ---------- Large (329x345) ----------
    private var largeView: some View {
        VStack(spacing: 8) {
            statsRow(spread: true)
            Divider().overlay(Palette.border)
            if let snap = entry.snapshot, !snap.positions.isEmpty {
                ForEach(Array(snap.positions.prefix(8))) { p in
                    positionRow(p, sym: snap.currencySymbol)
                }
            } else if entry.errorMessage != nil {
                Text(entry.errorMessage ?? "")
                    .font(.caption).foregroundStyle(.orange)
            }
            Spacer(minLength: 0)
            footer
        }
    }

    // ---------- Shared building blocks ----------

    private func statsRow(spread: Bool) -> some View {
        let stats = entry.snapshot?.stats
        let sym = entry.snapshot?.currencySymbol ?? ""
        return HStack(spacing: spread ? 16 : 8) {
            statCell("今日",
                     amtString(stats?.todayPnl, sym: sym, signed: true),
                     pctString(stats?.todayPnlPct),
                     color(for: stats?.todayPnl))
            statCell("浮动",
                     amtString(stats?.unrealizedPnl, sym: sym, signed: true),
                     pctString(stats?.unrealizedPnlPct),
                     color(for: stats?.unrealizedPnl))
            statCell("总收益",
                     amtString(stats?.allTimePnl, sym: sym, signed: true),
                     pctString(stats?.allTimePnlPct),
                     color(for: stats?.allTimePnl))
        }
    }

    private func statCell(_ label: String, _ value: String, _ sub: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.system(size: 9)).foregroundStyle(Palette.muted)
            Text(value).font(.system(size: 13, weight: .semibold).monospacedDigit())
                .foregroundStyle(color)
            Text(sub).font(.system(size: 10).monospacedDigit())
                .foregroundStyle(color.opacity(0.85))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func positionRow(_ p: T212Snapshot.Position, sym: String) -> some View {
        let arrow = (p.dayChangePct ?? 0) >= 0 ? "▲" : "▼"
        let c = color(for: p.dayChangePct ?? 0)
        return HStack(spacing: 6) {
            Text(arrow).foregroundStyle(c)
            Text(p.shortTicker).font(.system(size: 11, weight: .semibold))
                .frame(width: 52, alignment: .leading)
            Text(listedPrice(p)).font(.system(size: 11).monospacedDigit())
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(pctString(p.dayChangePct))
                .font(.system(size: 11).monospacedDigit())
                .foregroundStyle(c)
            Text("浮\(p.ppl >= 0 ? "+" : "")\(sym)\(intString(p.ppl))")
                .font(.system(size: 10).monospacedDigit())
                .foregroundStyle(color(for: p.ppl))
                .frame(width: 70, alignment: .trailing)
        }
    }

    private var footer: some View {
        HStack {
            refreshButton(showText: true)
            Spacer()
            // Brand mark + version, centred between the refresh button
            // and the env badge. Kept tiny and muted so it doesn't dominate.
            Text("观澜  v0.1")
                .font(.system(size: 9, weight: .regular))
                .tracking(0.4)
                .foregroundStyle(Palette.muted)
            Spacer()
            if let env = entry.snapshot?.env {
                Text(env.uppercased())
                    .font(.system(size: 9, weight: .medium))
                    .padding(.horizontal, 5).padding(.vertical, 1)
                    .background(Palette.panel)
                    .overlay(RoundedRectangle(cornerRadius: 3).stroke(Palette.border))
                    .cornerRadius(3)
                    .foregroundStyle(Palette.muted)
            }
        }
    }

    /// Tappable refresh control. Tapping triggers `RefreshIntent`, which calls
    /// `WidgetCenter.reloadTimelines` → our provider re-runs → new data.
    @ViewBuilder
    private func refreshButton(showText: Bool) -> some View {
        Button(intent: RefreshIntent()) {
            HStack(spacing: 3) {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 9, weight: .medium))
                if showText {
                    Text(updatedString())
                        .font(.system(size: 9))
                }
            }
            .foregroundStyle(Palette.muted)
        }
        .buttonStyle(.plain)
    }

    // ---------- Formatting helpers ----------

    private func amtString(_ v: Double?, sym: String, signed: Bool = false) -> String {
        guard let v = v else { return "—" }
        let prefix = signed && v >= 0 ? "+" : (signed && v < 0 ? "−" : "")
        let abs = String(format: "%.2f", Swift.abs(v))
        return "\(prefix)\(sym)\(abs)"
    }
    private func pctString(_ v: Double?) -> String {
        guard let v = v else { return "—" }
        return "\(v >= 0 ? "+" : "")\(String(format: "%.2f", v))%"
    }
    private func intString(_ v: Double) -> String {
        String(format: "%.0f", Swift.abs(v))
    }
    private func color(for v: Double?) -> Color {
        guard let v = v else { return Palette.muted }
        return v > 0 ? Palette.green : v < 0 ? Palette.red : Palette.muted
    }
    private func listedPrice(_ p: T212Snapshot.Position) -> String {
        let s: String
        switch (p.currency ?? "").lowercased() {
        case "usd": s = "$"
        case "eur": s = "€"
        case "gbp": s = "£"
        default:    s = ""
        }
        return "\(s)\(String(format: "%.2f", p.currentPrice))"
    }
    private func updatedString() -> String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return "更新 \(f.string(from: entry.date))"
    }
}
