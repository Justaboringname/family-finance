import SwiftUI
import WidgetKit

// 桌面小组件：只读快照里的家族净资产和今日盈亏（中号再加走势和构成色条）。数据不会自己变，一小时看一次有没有新导入的快照。

struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> Entry { Entry(date: .now, snap: nil) }
    func getSnapshot(in context: Context, completion: @escaping (Entry) -> Void) {
        completion(Entry(date: .now, snap: SnapshotStore.current()))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<Entry>) -> Void) {
        let e = Entry(date: .now, snap: SnapshotStore.current())
        completion(Timeline(entries: [e], policy: .after(.now.addingTimeInterval(3600))))
    }
}

struct FamilyWidgetEntryView: View {
    var entry: Entry
    @Environment(\.widgetFamily) private var family
    var body: some View { FamilyWidgetView(entry: entry, family: family) }
}

struct FamilyWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "FamilyWidget", provider: Provider()) { entry in
            FamilyWidgetEntryView(entry: entry)
                .containerBackground(for: .widget) { WidgetBackground() }
        }
        .configurationDisplayName("家族净资产")
        .description("Mac 上导出的只读快照：净资产、今日盈亏、走势和构成。")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

@main
struct FamilyWidgets: WidgetBundle {
    var body: some Widget { FamilyWidget() }
}
