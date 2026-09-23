// Widget extension entry point. Declares the widget(s) the system can offer
// in the widget gallery (右键桌面 → 编辑小组件 → T212).

import WidgetKit
import SwiftUI

@main
struct T212WidgetExtensionBundle: WidgetBundle {
    var body: some Widget {
        T212PortfolioWidget()
    }
}

struct T212PortfolioWidget: Widget {
    let kind: String = "T212PortfolioWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: T212Provider()) { entry in
            T212WidgetView(entry: entry)
                .containerBackground(for: .widget) {
                    LinearGradient(
                        colors: [
                            Color(red: 0.05, green: 0.07, blue: 0.10),
                            Color(red: 0.09, green: 0.11, blue: 0.14),
                        ],
                        startPoint: .top, endPoint: .bottom
                    )
                }
        }
        .configurationDisplayName("观澜")
        .description("Trading 212 实盘:今日盈亏 + 持仓 + 异动。")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}
