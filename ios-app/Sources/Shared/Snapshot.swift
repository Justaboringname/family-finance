import Foundation

// Mac 上 core/mobile.py 导出的只读快照。所有金额、占比的文字都由网页同一套 Python 函数格式化好，
// 这里只解码、排版；数值（v、c0、c1、bp）只用来画条形和走势。字段名是 snake_case，解码时自动转成驼峰。

struct Money: Codable, Hashable, Sendable {
    var v: Double
    var usd: String
    var cny: String
    var sign: Int
}

struct StripCell: Codable, Hashable, Sendable {
    var label: String
    var qual: String
    var v: Double
    var usd: String
    var cny: String
    var sign: Int
    var pct: String?
}

struct Contrib: Codable, Hashable, Sendable {
    var name: String
    var v: Double
    var usd: String
    var cny: String
    var sign: Int
}

struct Certificate: Codable, Hashable, Sendable, Identifiable {
    var key: String
    var title: String
    var net: Money
    var day: Money
    var dayPct: String
    var contrib: [Contrib]
    var strip: [StripCell]
    var id: String { key }
}

struct CompGroup: Codable, Hashable, Sendable, Identifiable {
    var name: String
    var slot: Int
    var bp: Int
    var share: String
    var desc: String
    var v: Double
    var usd: String
    var cny: String
    var day: Money?
    var id: String { name }
}

struct LiqRow: Codable, Hashable, Sendable {
    var name: String
    var who: String
    var usd: String
}

struct LiqBucket: Codable, Hashable, Sendable, Identifiable {
    var bucket: String
    var timing: String
    var fill: String
    var c0: Double
    var c1: Double
    var share: String
    var cum: String
    var usd: String
    var cny: String
    var rows: [LiqRow]
    var id: String { bucket }
}

struct Concentration: Codable, Hashable, Sendable, Identifiable {
    var name: String
    var who: String
    var usd: String
    var cny: String
    var share: String
    var shareV: Double
    var flag: Bool
    var id: String { name }
}

struct TrendPoint: Codable, Hashable, Sendable, Identifiable {
    var date: String
    var v: Double
    var usd: String
    var cny: String
    var id: String { date }
}

struct HoldRow: Codable, Hashable, Sendable, Identifiable {
    var name: String
    var meta: String
    var shares: String
    var close: String
    var prev: String
    var chg: String
    var chgSign: Int
    var mv: Money?
    var day: Money?
    var cost: Money?
    var unr: Money?
    var unrPct: String
    var id: String { name + meta }
}

struct HoldGroup: Codable, Hashable, Sendable, Identifiable {
    var key: String
    var title: String
    var member: String
    var sub: String
    var total: Money
    var day: Money?
    var rows: [HoldRow]
    var id: String { key }
}

struct Milestone: Codable, Hashable, Sendable, Identifiable {
    var at: String
    var end: String
    var hasTime: Bool
    var text: String
    var short: String
    var label: String
    var state: String
    var id: String { at + text }
    var atDate: Date? { ISO.date(at) }
    var endDate: Date? { ISO.date(end) }
}

struct Expected: Codable, Hashable, Sendable {
    var date: String
    var monthOnly: Bool
    var year: Int
    var month: Int
    var day: Int
    var weekday: String
    var left: String
}

struct NextMilestone: Codable, Hashable, Sendable {
    var short: String
    var at: String
    var hasTime: Bool
}

struct Plan: Codable, Hashable, Sendable, Identifiable {
    var label: String
    var member: String
    var note: String
    var amount: Money
    var expected: Expected?
    var milestones: [Milestone]
    var next: NextMilestone
    var cash: String
    var cashOk: Bool
    var cashGap: String
    var id: String { label }

    /// 现在还没过去的第一个节点（快照导出之后时间在走，按手机上的时间重新判断）。
    func upcoming(at now: Date) -> Milestone? {
        milestones.first { ($0.endDate ?? .distantPast) > now }
    }
}

struct EventItem: Codable, Hashable, Sendable, Identifiable {
    var date: String
    var monthOnly: Bool
    var end: String?
    var kind: String
    var title: String
    var member: String
    var note: String
    var money: Money?
    var weekday: String
    var left: String
    var id: String { date + title }
}

struct Members: Codable, Hashable, Sendable {
    var me: String
    var father: String
    enum CodingKeys: String, CodingKey { case me = "self", father }
}

struct Snapshot: Codable, Hashable, Sendable {
    var version: Int
    var generatedAt: String
    var asOf: String
    var asOfLabel: String
    var intraday: Bool
    var fx: String
    var members: Members
    var certificates: [Certificate]
    var composition: [String: [CompGroup]]
    var liquidity: [LiqBucket]
    var concentration: [Concentration]
    var trend: [TrendPoint]
    var holdings: [HoldGroup]
    var plans: [Plan]
    var events: [EventItem]
    /// 示例数据（core/demo.py 生成的虚构家庭）才有这个标记；真快照没有这个字段。
    var demo: Bool?
    var isDemo: Bool { demo == true }

    /// app 里给的构成视角。快照里还有「按家庭成员」，2026-11-12 用户说按人分不重要，网站和 app 都不显示了。
    static let compositionViews = ["按资产类别", "按账户"]
    var generatedDate: Date? { ISO.date(generatedAt) }
    var family: Certificate? { certificates.first { $0.key == "family" } ?? certificates.first }

    static func decode(_ data: Data) throws -> Snapshot {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        let s = try d.decode(Snapshot.self, from: data)
        guard s.version == 1 else { throw SnapshotError.unsupportedVersion(s.version) }
        return s
    }
}

enum SnapshotError: LocalizedError {
    case unsupportedVersion(Int)
    case demoFile
    var errorDescription: String? {
        switch self {
        case .demoFile: "这是示例数据，不能当成真实数据导入。"
        case .unsupportedVersion(let v): "这份快照是第 \(v) 版格式，app 只认第 1 版。请更新 app。"
        }
    }
}

enum ISO {
    nonisolated(unsafe) static let parser: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    static func date(_ s: String) -> Date? { parser.date(from: s) }
}
