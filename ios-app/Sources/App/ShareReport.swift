import SwiftUI
import UIKit

/// 给家人看的一张图（2026-11-12：每天用微信发给不装 app 的家人）。
/// 用户要「尽可能简洁、只留重点」：哪天收盘、家族净资产、今天涨跌多少、Charles Schwab 和 Apple 两类持仓各涨跌多少，再加近期大事（如 IPO 认购的几个节点）。
/// 用 ImageRenderer 离屏画成 390pt 宽、3 倍的 PNG，固定浅色，和手机当前深浅色无关。
/// 离屏渲染不触发 onAppear、TimelineView 不出帧、画不了 TabView / ScrollView / Picker，这里只用纯绘制的东西。
struct FamilyReport: View {
    var snap: Snapshot
    var now: Date

    static let width: CGFloat = 390

    private var family: Certificate? { snap.certificates.first { $0.key == "family" } ?? snap.certificates.first }
    /// 两类持仓的今日涨跌（用户 11-12 要的：不按人分，按「Charles Schwab 持仓」和「Apple 持仓」）。
    /// Schwab 的股票组 key 形如「Schwab-123:股票」（现金组 key 是 cash，不算持仓）；AAPL 股票计划 key 是 aapl。
    private var holdings: [(label: String, group: HoldGroup)] {
        let schwab = snap.holdings.filter { $0.key.hasPrefix("Schwab") }
        var out: [(String, HoldGroup)] = schwab.map { g in
            let kind = g.key.split(separator: ":").last.map(String.init) ?? ""
            return (schwab.count > 1 && !kind.isEmpty ? "Charles Schwab \(kind)" : "Charles Schwab 持仓", g)
        }
        if let a = snap.holdings.first(where: { $0.key == "aapl" }) { out.append(("Apple 持仓", a)) }
        return out
    }
    /// 近期大事：还有关键节点没过去的资金计划（比如 IPO 认购）。
    private var events: [Plan] {
        snap.plans.filter { p in p.milestones.contains { ($0.endDate ?? .distantFuture) > now } }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 10) {
                Logo().frame(width: 30, height: 30)
                Text("家庭资产").font(Type_.song(18)).tracking(1)
                if snap.isDemo {     // 展示模式下分享出去的图要标明，免得被当成真实数据
                    Text("示例数据").font(Type_.sans(10, weight: .semibold)).foregroundStyle(Ink.text2).fixedSize()
                        .padding(.horizontal, 5).padding(.vertical, 2)
                        .overlay(RoundedRectangle(cornerRadius: 4).stroke(Ink.rule))
                }
                Spacer(minLength: 4)
                Text(snap.asOfLabel).font(Type_.song(14, bold: false)).foregroundStyle(Ink.text2)
                    .lineLimit(1).minimumScaleFactor(0.8)
            }

            if let f = family { note(f).padding(.top, 16) }

            ForEach(events) { p in event(p).padding(.top, 22) }

            Text("按 USD/CNY \(snap.fx) 折算。只列事实，不构成投资建议。")
                .font(.system(size: 10)).foregroundStyle(Ink.muted)
                .padding(.top, 18)
        }
        .padding(.horizontal, 20).padding(.top, 24).padding(.bottom, 22)
        .frame(width: Self.width, alignment: .leading)
        .background { ReportPaper() }
        .environment(\.colorScheme, .light)
    }

    /// 主票：家族净资产、今日涨跌、Schwab 和 Apple 两类持仓的今日涨跌。
    private func note(_ f: Certificate) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("家族净资产").font(.footnote.weight(.medium)).foregroundStyle(Ink.text2)
            faceValue(f.net.usd, size: 40, color: Ink.text).lineLimit(1).minimumScaleFactor(0.6).padding(.top, 6)
            Text(f.net.cny).font(Type_.sans(14)).foregroundStyle(Ink.muted).padding(.top, 4)

            Rectangle().fill(Ink.rule).frame(height: 1).padding(.vertical, 16)

            HStack(alignment: .firstTextBaseline) {
                Text("今日涨跌").font(.footnote.weight(.medium)).foregroundStyle(Ink.text2)
                Spacer()
                if !f.dayPct.isEmpty { Chip(text: f.dayPct, sign: f.day.sign) }
            }
            faceValue(f.day.usd, size: 32, color: Ink.signed(f.day.sign)).lineLimit(1).minimumScaleFactor(0.6)
                .padding(.top, 4)
            Text(f.day.cny).font(Type_.sans(14)).foregroundStyle(Ink.signed(f.day.sign)).padding(.top, 2)

            if !holdings.isEmpty {
                VStack(spacing: 10) {
                    ForEach(holdings, id: \.group.key) { h in
                        VStack(spacing: 2) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(h.label).font(.subheadline.weight(.semibold))
                                Spacer()
                                Text(h.group.day?.usd ?? "—").font(Type_.sans(16, weight: .semibold))
                                    .foregroundStyle(Ink.signed(h.group.day?.sign ?? 0))
                            }
                            HStack(alignment: .firstTextBaseline) {
                                Text("市值 \(h.group.total.usd)").foregroundStyle(Ink.muted)
                                Spacer()
                                Text(h.group.day?.cny ?? "").foregroundStyle(Ink.signed(h.group.day?.sign ?? 0))
                            }
                            .font(Type_.sans(12))
                        }
                    }
                }
                .padding(.top, 18)
            }
        }
        .padding(.horizontal, 30).padding(.top, 30).padding(.bottom, 28)
        .background {
            ZStack {
                Ink.couponBg
                ZStack {
                    GuillocheField(spacing: 6, wavelength: 90).foregroundStyle(Ink.accent).opacity(0.1)
                    GeometryReader { g in
                        Rosette(lineWidth: 0.7).foregroundStyle(Ink.accent).opacity(0.2)
                            .frame(width: 260, height: 260)
                            .position(x: g.size.width * 0.78, y: g.size.height * 0.26)
                    }
                    Fibers(colors: Engrave(scheme: .light).fiberColors, count: 40).opacity(0.45)
                }
                .clipped()
                .padding(14)
            }
        }
        .overlay { EngravedFrame(medalFill: Ink.couponBg) }
    }

    /// 近期大事：计划名和金额，备注的第一句，关键节点，离下一个节点还有多久。
    private func event(_ p: Plan) -> some View {
        let next = p.upcoming(at: now)
        return VStack(alignment: .leading, spacing: 0) {
            Text("近期大事").font(.footnote.weight(.medium)).foregroundStyle(Ink.text2)
            VStack(spacing: 3) {
                Rectangle().fill(Ink.ruleStrong).frame(height: 1)
                WaveBand(period: 12).stroke(Ink.accent, lineWidth: 0.6).frame(height: 5).opacity(0.6)
            }
            .padding(.top, 6)

            HStack(alignment: .firstTextBaseline) {
                Text(p.label).font(Type_.song(18))
                Spacer()
                faceValue(p.amount.usd, size: 22, color: Ink.accent)
            }
            .padding(.top, 12)
            if let s = Self.firstSentence(p.note) {
                Text(s).font(.caption).foregroundStyle(Ink.text2).padding(.top, 4)
            }

            VStack(alignment: .leading, spacing: 6) {
                ForEach(p.milestones) { m in
                    let done = (m.endDate ?? .distantFuture) <= now
                    let isNext = m.id == next?.id
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Circle().fill(isNext || done ? Ink.accent : .clear)
                            .overlay(Circle().stroke(Ink.accent, lineWidth: 1.2))
                            .frame(width: 8, height: 8)
                            .opacity(done ? 0.4 : 1)
                        Text(m.label).font(Type_.sans(13, weight: .semibold))
                        Text(m.text).font(.caption)
                    }
                    .foregroundStyle(done ? Ink.muted : Ink.text)
                }
            }
            .padding(.top, 12)

            if let n = next, let at = n.atDate {
                Text("距\(n.short)还有 \(Self.left(from: now, to: at, withTime: n.hasTime))")
                    .font(.footnote.weight(.semibold)).foregroundStyle(Ink.accent)
                    .padding(.top, 10)
            }
        }
    }

    /// 备注的第一句（到第一个句号为止）。
    static func firstSentence(_ note: String) -> String? {
        let t = note.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return nil }
        if let i = t.firstIndex(of: "。") { return String(t[...i]) }
        return t
    }

    /// 「5 天 13 小时」；没有具体时刻的节点只说天数。
    static func left(from now: Date, to at: Date, withTime: Bool) -> String {
        if !withTime {
            let cal = Calendar.current
            let d = cal.dateComponents([.day], from: cal.startOfDay(for: now), to: cal.startOfDay(for: at)).day ?? 0
            return d <= 0 ? "不到 1 天" : "\(d) 天"
        }
        let s = max(0, Int(at.timeIntervalSince(now)))
        let d = s / 86400, h = s % 86400 / 3600, m = s % 3600 / 60
        if d > 0 { return "\(d) 天 \(h) 小时" }
        if h > 0 { return "\(h) 小时 \(m) 分" }
        return "\(m) 分钟"
    }

    /// 离屏画成图片（3 倍）。
    @MainActor static func render(_ snap: Snapshot, now: Date = .now) -> UIImage? {
        let r = ImageRenderer(content: FamilyReport(snap: snap, now: now))
        r.scale = 3
        r.proposedSize = ProposedViewSize(width: width, height: nil)
        return r.uiImage
    }
}

/// 图的纸底：淡车花网、右上一枚大玫瑰水印、少量纤维。不用 ignoresSafeArea / drawingGroup（离屏渲染不稳）。
private struct ReportPaper: View {
    var body: some View {
        let e = Engrave(scheme: .light)
        GeometryReader { g in
            ZStack {
                Ink.bg
                GuillocheField().foregroundStyle(Ink.accent).opacity(e.field * 0.6)
                Rosette(lineWidth: 0.6).foregroundStyle(Ink.accent).opacity(e.watermark)
                    .frame(width: 380, height: 380)
                    .position(x: g.size.width - 10, y: 20)
                Fibers(colors: e.fiberColors, count: 24).opacity(e.fibers * 0.5)
            }
        }
        .accessibilityHidden(true)
    }
}

struct ReportShot: Identifiable {
    let id = UUID()
    let image: UIImage
}

/// 右上角：生成这张图，直接打开系统分享面板（选「微信」）。只分享图片本身，不带标题文字，
/// 免得微信里多出一句「家庭资产日报 ……」。
struct ShareReportButton: View {
    var snap: Snapshot
    @State private var shot: ReportShot?

    var body: some View {
        Button {
            if let img = FamilyReport.render(snap) { shot = ReportShot(image: img) }
        } label: {
            Label("分享给家人", systemImage: "square.and.arrow.up")
        }
        .sheet(item: $shot) { s in
            ActivityView(items: [s.image]) { shot = nil }.presentationDetents([.medium, .large]).ignoresSafeArea()
        }
    }
}

/// 系统分享面板。
struct ActivityView: UIViewControllerRepresentable {
    var items: [Any]
    var onDone: () -> Void = {}
    func makeUIViewController(context: Context) -> UIActivityViewController {
        let vc = UIActivityViewController(activityItems: items, applicationActivities: nil)
        vc.completionWithItemsHandler = { _, _, _, _ in onDone() }
        return vc
    }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}
