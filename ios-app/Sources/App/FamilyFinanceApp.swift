import LocalAuthentication
import SwiftUI
import UniformTypeIdentifiers
import WidgetKit

@main
struct FamilyFinanceApp: App {
    @State private var model = AppModel()
    var body: some Scene {
        WindowGroup {
            RootView().environment(model)
        }
    }
}

/// 只读：快照来自装机时打包的那份，或者用户导入的那份（新的那份算数）。app 里没有任何联网代码。
@Observable @MainActor
final class AppModel {
    var snapshot: Snapshot?
    var importError: String?
    var importedNote: String?
    /// 展示模式：显示虚构家庭的示例数据（见 DemoMode）。
    private(set) var demo = DemoMode.isOn
    /// app 里带着示例数据时才给展示模式的开关。
    let demoAvailable = SnapshotStore.loadDemo() != nil

    init() { snapshot = SnapshotStore.current() }

    func reload() {
        demo = DemoMode.isOn
        snapshot = SnapshotStore.current()
    }

    /// 左上角的开关。进展示模式一点就进；退出要先过面容 ID / 密码：
    /// 手机在别人手里时，对方点一下左上角不该看到真实数据。手机没设密码时验证不了，直接放行。
    func toggleDemo() async {
        if demo {
            let ctx = LAContext()
            var err: NSError?
            if ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: &err) {
                guard (try? await ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "退出展示模式，显示真实数据")) == true
                else { return }
            }
        }
        DemoMode.isOn = !demo
        reload()
        WidgetCenter.shared.reloadAllTimelines()
    }

    func importFile(_ result: Result<URL, Error>) {
        switch result {
        case .success(let url):
            do {
                let s = try SnapshotStore.importFile(url)
                snapshot = SnapshotStore.current() ?? s
                importedNote = "已导入 \(s.asOfLabel) 的快照"
                importError = nil
                WidgetCenter.shared.reloadAllTimelines()
            } catch {
                importError = "导入失败：\(error.localizedDescription)"
            }
        case .failure(let e):
            importError = "导入失败：\(e.localizedDescription)"
        }
    }
}

struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var phase
    // 启动参数 -FFTab 1 / 2 直接打开持仓 / 日程（模拟器里截图用）
    @State private var tab = UserDefaults.standard.integer(forKey: "FFTab")

    var body: some View {
        Group {
            if UserDefaults.standard.bool(forKey: "FFWidgetPreview") {
                WidgetPreviewGallery(snap: model.snapshot)
            } else if let s = model.snapshot {
                TabView(selection: $tab) {
                    Tab("总览", systemImage: "rectangle.stack", value: 0) { OverviewView(snap: s) }
                    Tab("持仓", systemImage: "list.bullet.rectangle", value: 1) { HoldingsView(snap: s) }
                    Tab("日程", systemImage: "calendar", value: 2) { AgendaView(snap: s) }
                }
                .tint(Ink.accent)
                .id(s.isDemo)      // 进出展示模式时整页重建：展开的行、选中的日期不带过去，数字滚轮重新滚一遍
            } else {
                EmptySnapshotView()
            }
        }
        .onChange(of: phase) { _, p in if p == .active { model.reload() } }
        .task {
            guard UserDefaults.standard.bool(forKey: "FFRenderReport"), let s = model.snapshot,
                  let img = FamilyReport.render(s), let data = img.pngData() else { return }
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("report.png")
            try? data.write(to: url)
            print("FFRenderReport \(Int(img.size.width * img.scale))x\(Int(img.size.height * img.scale)) \(url.path)")
        }
    }
}

/// 从「文件」导入快照（隔空投送来的 JSON）。Mac 每天收盘后会自动推数据，这只是后备：
/// 没数据时放在空白页右上角，平时是页尾的一行小字（textOnly）。右上角现在是「分享长图」。
struct ImportButton: View {
    var textOnly = false
    @Environment(AppModel.self) private var model
    @State private var picking = false

    var body: some View {
        Button { picking = true } label: {
            if textOnly {
                Text("从文件导入快照").font(.caption).underline()
            } else {
                Label("导入快照", systemImage: "square.and.arrow.down")
            }
        }
            .fileImporter(isPresented: $picking, allowedContentTypes: [.json]) { model.importFile($0) }
            .alert(model.importError ?? model.importedNote ?? "", isPresented: Binding(
                get: { model.importError != nil || model.importedNote != nil },
                set: { if !$0 { model.importError = nil; model.importedNote = nil } })) {
                Button("好") {}
            }
    }
}

struct EmptySnapshotView: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 14) {
                Text("还没有数据").font(Type_.song(26))
                Text("这个 app 只显示 Mac 上「家庭资产与税务」导出的只读快照，不联网。")
                    .foregroundStyle(Ink.text2)
                Text("在 Mac 上运行 scripts/export_snapshot.py --downloads，把 ~/Downloads 里的快照隔空投送到手机，存到「文件」，再点右上角的导入。")
                    .font(.callout).foregroundStyle(Ink.muted)
                if model.demoAvailable {
                    Text("想先看看 app 长什么样：点左上角的标志，进展示模式（虚构家庭的示例数据）。")
                        .font(.callout).foregroundStyle(Ink.muted)
                }
                Spacer()
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background { PaperBackground() }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { DemoToggle() }
                ToolbarItem(placement: .topBarTrailing) { ImportButton() }
            }
        }
    }
}

/// 左上角的标志：点一下进展示模式（换成虚构家庭的示例数据，给别人看 app 时用），再点一下退出（要过面容 ID）。
/// 开着的时候标志旁边有「示例数据」四个字，免得自己把示例当成真的。app 里没带示例数据时只是个标志，点了没反应。
struct DemoToggle: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Button {
            guard model.demoAvailable || model.demo else { return }
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            Task { await model.toggleDemo() }
        } label: {
            HStack(spacing: 6) {
                Logo().frame(width: 24, height: 24)
                if model.demo {
                    Text("示例数据").font(Type_.sans(12, weight: .semibold)).foregroundStyle(Ink.text2)
                        .transition(.opacity.combined(with: .move(edge: .leading)))
                }
            }
            .animation(.snappy, value: model.demo)
        }
        .accessibilityLabel(model.demo ? "展示模式开着，点一下退出" : "进入展示模式")
    }
}

// MARK: - 共用的小部件

/// 区块标题：两位编号 + 宋体标题 + 一条墨线（和网页一样）。
struct SectionHeader: View {
    var no: Int
    var title: String
    var hint: String = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(String(format: "%02d", no)).font(Type_.sans(11)).foregroundStyle(Ink.muted)
                Text(title).font(Type_.song(19))
                Spacer()
                if !hint.isEmpty { Text(hint).font(.caption).foregroundStyle(Ink.muted) }
            }
            VStack(spacing: 3) {
                Rectangle().fill(Ink.ruleStrong).frame(height: 1)
                WaveBand(period: 12).stroke(Ink.accent, lineWidth: 0.6).frame(height: 5).opacity(0.6)
            }
        }
        .padding(.top, 30)
    }
}

/// 美元一行、人民币一行。
struct MoneyStack: View {
    var m: Money
    var size: CGFloat = 15
    var colored = false
    var align: HorizontalAlignment = .trailing
    var body: some View {
        VStack(alignment: align, spacing: 1) {
            Text(m.usd).font(Type_.sans(size, weight: .semibold))
                .foregroundStyle(colored ? Ink.signed(m.sign) : Ink.text)
            if !m.cny.isEmpty {
                Text(m.cny).font(Type_.sans(size * 0.8)).foregroundStyle(colored ? Ink.signed(m.sign) : Ink.muted)
            }
        }
    }
}

struct Chip: View {
    var text: String
    var sign: Int
    var body: some View {
        Text(text).font(Type_.sans(12, weight: .semibold)).foregroundStyle(Ink.signed(sign))
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(Ink.signedSoft(sign), in: RoundedRectangle(cornerRadius: 5))
    }
}

/// 页尾的数据来源说明。
struct ProvenanceFooter: View {
    var snap: Snapshot
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Rectangle().fill(Ink.rule).frame(height: 1)
            Text("数据截至 \(snap.asOfLabel)，在 Mac 上导出于 \(snap.generatedAt.prefix(16).replacingOccurrences(of: "T", with: " "))。")
            if snap.isDemo {
                Text("展示模式：这是一个虚构家庭的示例数据，不是真实持仓。点左上角的标志退出。")
            } else {
                Text("只读快照，app 不联网。Mac 每天收盘后自动推新数据；也可以重新装机或导入快照。只列事实，不构成投资建议。")
                ImportButton(textOnly: true).foregroundStyle(Ink.accent).padding(.top, 4)
            }
        }
        .font(.caption).foregroundStyle(Ink.muted)
        .padding(.top, 30)
    }
}

/// 小组件预览（启动参数 -FFWidgetPreview 1）：按主屏小组件的实际尺寸画出小号和中号，方便截图检查。
struct WidgetPreviewGallery: View {
    var snap: Snapshot?
    var body: some View {
        let e = Entry(date: .now, snap: snap)
        VStack(spacing: 24) {
            FamilyWidgetView(entry: e, family: .systemSmall).padding(16)
                .frame(width: 170, height: 170).background(WidgetBackground())
                .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
            FamilyWidgetView(entry: e, family: .systemMedium).padding(16)
                .frame(width: 364, height: 170).background(WidgetBackground())
                .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(LinearGradient(colors: [Color(hex: 0x2b4f7d), Color(hex: 0x6a87ad)], startPoint: .top, endPoint: .bottom))
    }
}
