import Foundation

/// 快照放在哪，共三份，导出时间最新的那份算数（app 和小组件都这么读）。不联网。
/// - 装机时打包进 app（和小组件）的一份；
/// - App Group 共享目录根上的一份：用户隔空投送后在 app 里导入的，或 app 把更新的那份抄过来的；
/// - 共享目录 Library/Application Support/ 下的一份：Mac 收盘后用 devicectl 推过来的。
///   devicectl 写不进共享目录的根：根上已有同名文件时它也报成功，其实没写（2026-11-11 实测），所以推到这个子目录。
enum SnapshotStore {
    static let fileName = "snapshot.json"

    static var appGroup: String? {
        Bundle.main.object(forInfoDictionaryKey: "FFAppGroup") as? String
    }

    static var sharedURL: URL? {
        guard let g = appGroup,
              let dir = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: g) else { return nil }
        return dir.appendingPathComponent(fileName)
    }

    /// Mac 推过来的那份（ios-app/scripts/push-snapshot.sh 写到这里）。
    static var pushedURL: URL? {
        guard let g = appGroup,
              let dir = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: g) else { return nil }
        return dir.appendingPathComponent("Library/Application Support", isDirectory: true).appendingPathComponent(fileName)
    }

    static var bundledURL: URL? { Bundle.main.url(forResource: "snapshot", withExtension: "json") }

    private static func read(_ url: URL?) -> Snapshot? {
        guard let url, let data = try? Data(contentsOf: url) else { return nil }
        return try? Snapshot.decode(data)
    }

    /// 现在该显示的那份：展示模式开着就是示例数据，否则是真实数据。app 和小组件都从这里读。
    /// 展示模式开着但找不到示例数据时返回 nil，宁可显示「没有数据」，也不退回去显示真实数据。
    static func current() -> Snapshot? { DemoMode.isOn ? loadDemo() : load() }

    // MARK: 示例数据（展示模式）

    static let demoFileName = "demo-snapshot.json"
    static var bundledDemoURL: URL? { Bundle.main.url(forResource: "demo-snapshot", withExtension: "json") }
    /// Mac 每天收盘后顺手推过来的那份示例数据（日期是当天的），和真快照推到同一个目录。
    static var pushedDemoURL: URL? { pushedURL?.deletingLastPathComponent().appendingPathComponent(demoFileName) }

    /// 示例数据：打包的和推过来的两份里较新的那份。只认带 demo 标记的。
    static func loadDemo() -> Snapshot? {
        [pushedDemoURL, bundledDemoURL].compactMap { read($0) }.filter(\.isDemo)
            .max { ($0.generatedDate ?? .distantPast) < ($1.generatedDate ?? .distantPast) }
    }

    /// 三份里导出时间最新的那份（一样新时依次优先共享目录根、推送的、打包的）。
    /// 最新的不在共享目录根上时，顺手抄过去，导入和推送就不会互相盖掉更新的那份。
    static func load() -> Snapshot? {
        var best: (snap: Snapshot, url: URL?)?
        for url in [sharedURL, pushedURL, bundledURL] {
            guard let s = read(url), !s.isDemo else { continue }      // 示例数据不算真实数据（比如误导入了示例文件）
            if best == nil || (s.generatedDate ?? .distantPast) > (best!.snap.generatedDate ?? .distantPast) { best = (s, url) }
        }
        guard let best else { return nil }
        if best.url != sharedURL { copyToShared(from: best.url) }
        return best.snap
    }

    private static func copyToShared(from src: URL?) {
        guard let src, let dst = sharedURL, let data = try? Data(contentsOf: src) else { return }
        try? data.write(to: dst, options: .atomic)
    }

    /// 导入用户选的 JSON：先校验能解码，再写进共享目录。返回导入的快照。
    static func importFile(_ url: URL) throws -> Snapshot {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let data = try Data(contentsOf: url)
        let snap = try Snapshot.decode(data)
        guard !snap.isDemo else { throw SnapshotError.demoFile }
        guard let dst = sharedURL else { throw CocoaError(.fileWriteUnknown) }
        try data.write(to: dst, options: .atomic)
        return snap
    }
}

/// 展示模式：给别人看 app 时，换成一份虚构家庭的示例数据（Mac 上 core/demo.py 生成），真实数据一个字都不显示。
/// 开关存在 App Group 的 UserDefaults 里，app 和小组件读的是同一个；重装 app 也还在。
/// 启动参数 -FFDemo 1 强制打开（模拟器里截图用）。
enum DemoMode {
    static let key = "FFDemoMode"
    private static var defaults: UserDefaults {
        SnapshotStore.appGroup.flatMap { UserDefaults(suiteName: $0) } ?? .standard
    }
    static var isOn: Bool {
        get { UserDefaults.standard.bool(forKey: "FFDemo") || defaults.bool(forKey: key) }
        set { defaults.set(newValue, forKey: key) }
    }
}
