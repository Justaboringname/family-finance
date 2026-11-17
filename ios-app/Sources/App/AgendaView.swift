import SwiftUI

/// 日程：有关键节点的资金计划（IPO）做成打孔息票置顶，带实时倒计时；其余事项是一条连续的撕口票据，按月分组。
struct AgendaView: View {
    var snap: Snapshot

    private var months: [(String, [EventItem])] {
        var out: [(String, [EventItem])] = []
        for e in snap.events {
            let p = e.date.split(separator: "-")
            let key = p.count >= 2 ? "\(p[0]) 年 \(Int(p[1]) ?? 0) 月" : e.date
            if out.last?.0 == key { out[out.count - 1].1.append(e) } else { out.append((key, [e])) }
        }
        return out
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text("资金事项和批次变化").font(.subheadline).foregroundStyle(Ink.text2)
                    ForEach(snap.plans) { p in CouponView(plan: p).padding(.top, 18) }
                    ForEach(months, id: \.0) { m, evs in
                        VStack(alignment: .leading, spacing: 0) {
                            Text(m).font(Type_.song(16)).padding(.top, 28).padding(.bottom, 8)
                            Rectangle().fill(Ink.ruleStrong).frame(height: 1)
                            ForEach(evs) { e in EventStub(e: e) }
                        }
                    }
                    if snap.plans.isEmpty && snap.events.isEmpty {
                        Text("暂无事项").foregroundStyle(Ink.muted).padding(.top, 30)
                    }
                    ProvenanceFooter(snap: snap)
                }
                .padding(.horizontal, 20).padding(.bottom, 40)
            }
            .background { PaperBackground() }
            .navigationTitle("日程")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { DemoToggle() }
                ToolbarItem(placement: .topBarTrailing) { ShareReportButton(snap: snap) }
            }
        }
    }
}

/// 息票外形：圆角矩形，在打孔线两端各打一个半圆缺口。
struct CouponShape: Shape {
    var perfY: CGFloat
    var notch: CGFloat = 10
    func path(in rect: CGRect) -> Path {
        var p = Path(rect)
        p.addEllipse(in: CGRect(x: rect.minX - notch, y: perfY - notch, width: 2 * notch, height: 2 * notch))
        p.addEllipse(in: CGRect(x: rect.maxX - notch, y: perfY - notch, width: 2 * notch, height: 2 * notch))
        return p
    }
}

struct CouponView: View {
    var plan: Plan
    private let stubH: CGFloat = 92
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let e = Engrave(scheme: scheme)
        VStack(alignment: .leading, spacing: 0) {
            // 存根：日期
            HStack(alignment: .center, spacing: 14) {
                if let e = plan.expected {
                    Text(verbatim: e.monthOnly ? "\(e.month)" : "\(e.day)").font(Type_.num(46)).foregroundStyle(Ink.accent)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(verbatim: e.monthOnly ? "\(e.year) 年" : "\(e.year) 年 \(e.month) 月").font(Type_.song(13, bold: false))
                            .foregroundStyle(Ink.text2)
                        Text(e.monthOnly ? "月内" : e.weekday).font(.caption).foregroundStyle(Ink.text2)
                    }
                    Spacer()
                    Text(e.monthOnly ? "日期未定" : e.left).font(.footnote.weight(.semibold)).foregroundStyle(Ink.accent)
                } else {
                    Text("日期未定").font(Type_.song(15))
                    Spacer()
                }
            }
            .frame(height: stubH - 20)
            .padding(.horizontal, 26).padding(.top, 20)

            // 打孔线
            Line().stroke(Ink.text.opacity(0.42), style: StrokeStyle(lineWidth: 1.5, lineCap: .round, dash: [0.1, 5]))
                .frame(height: 1).padding(.horizontal, 18)

            // 票面
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("资金").font(.caption2.weight(.semibold)).foregroundStyle(Ink.text2)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .overlay(RoundedRectangle(cornerRadius: 4).stroke(Ink.rule))
                    Text(plan.label).font(Type_.song(19))
                    Spacer()
                    Text(plan.member).font(.caption).foregroundStyle(Ink.muted)
                }
                if !plan.note.isEmpty {
                    Text(plan.note).font(.caption).foregroundStyle(Ink.text2).lineSpacing(3).padding(.top, 8)
                }
                faceValue(plan.amount.usd, size: 40, color: Ink.accent).padding(.top, 14)
                Text(plan.amount.cny).font(Type_.sans(14)).foregroundStyle(Ink.text2).padding(.top, 4)

                TimelineView(.periodic(from: .now, by: 1)) { ctx in timeline(at: ctx.date) }
                if !plan.cash.isEmpty {
                    HStack {
                        Text("现金 \(plan.cash)").foregroundStyle(Ink.text2)
                        Spacer()
                        Text(plan.cashGap).foregroundStyle(plan.cashOk ? Ink.text2 : Ink.down)
                    }
                    .font(Type_.sans(12)).padding(.top, 12)
                }
            }
            .padding(.horizontal, 26).padding(.top, 18).padding(.bottom, 26)
        }
        .background {
            ZStack {
                Ink.couponBg
                ZStack {
                    GuillocheField(spacing: 6, wavelength: 90).foregroundStyle(Ink.accent).opacity(e.noteField)
                    Underprint().foregroundStyle(Ink.teal).opacity(e.underprint)
                    GeometryReader { g in
                        Rosette(lineWidth: 0.6).foregroundStyle(Ink.accent).opacity(e.rosette)
                            .frame(width: 280, height: 280)
                            .position(x: g.size.width - 40, y: stubH + 150)
                    }
                    Fibers(colors: e.fiberColors, count: 50).opacity(e.fibers * 0.8)
                }
                .clipped()
                .padding(12)
            }
        }
        .overlay { EngravedFrame(inset: 12, band: 8, bandInset: 2, medal: 24, period: 12, medalFill: Ink.couponBg) }
        .clipShape(CouponShape(perfY: stubH), style: FillStyle(eoFill: true))
        .accessibilityElement(children: .contain)
    }

    private func timeline(at t: Date) -> some View {
        let next = plan.upcoming(at: t)
        return VStack(alignment: .leading, spacing: 0) {
            MilestoneList(items: plan.milestones, next: next, now: t).padding(.top, 18)
            Rectangle().fill(Ink.rule).frame(height: 1).padding(.vertical, 14)
            Countdown(next: next, now: t)
        }
    }
}

struct Line: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path(); p.move(to: CGPoint(x: rect.minX, y: rect.midY)); p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY)); return p
    }
}

struct MilestoneList: View {
    var items: [Milestone]
    var next: Milestone?
    var now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.element.id) { i, m in
                let done = (m.endDate ?? .distantFuture) <= now
                let isNext = m.id == next?.id
                HStack(alignment: .top, spacing: 12) {
                    VStack(spacing: 0) {
                        Circle()
                            .fill(isNext || done ? Ink.accent : Ink.couponBg)
                            .overlay(Circle().stroke(Ink.accent, lineWidth: 1.5))
                            .frame(width: 11, height: 11)
                            .opacity(done ? 0.45 : 1)
                            .background(Circle().fill(isNext ? Ink.accentSoft : .clear).frame(width: 20, height: 20))
                        if i < items.count - 1 {
                            Rectangle().fill(Ink.accent.opacity(0.5)).frame(width: 1.5).frame(maxHeight: .infinity)
                        }
                    }
                    .frame(width: 20)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(m.label).font(Type_.sans(13, weight: .semibold)).foregroundStyle(done ? Ink.muted : Ink.text)
                        Text(m.text).font(.caption).foregroundStyle(done ? Ink.muted : Ink.text2)
                    }
                    .padding(.bottom, i < items.count - 1 ? 12 : 0)
                }
            }
        }
    }
}

struct Countdown: View {
    var next: Milestone?
    var now: Date

    var body: some View {
        if let n = next, let target = n.hasTime ? n.atDate : n.atDate {
            let left = max(0, Int(target.timeIntervalSince(now)))
            VStack(alignment: .leading, spacing: 4) {
                Text("距\(n.short)").font(.caption).foregroundStyle(Ink.muted)
                if n.hasTime {
                    HStack(alignment: .firstTextBaseline, spacing: 3) {
                        unit(left / 86400, "天", show: left >= 86400)
                        unit(left % 86400 / 3600, "时")
                        unit(left % 3600 / 60, "分")
                        unit(left % 60, "秒")
                    }
                    .contentTransition(.numericText(countsDown: true))
                    .animation(.snappy, value: left)
                } else {
                    let days = Calendar.current.dateComponents([.day], from: Calendar.current.startOfDay(for: now),
                                                               to: Calendar.current.startOfDay(for: target)).day ?? 0
                    Text(verbatim: days <= 0 ? "今天" : "\(days) 天").font(Type_.num(26)).foregroundStyle(Ink.accent)
                }
            }
            .accessibilityElement(children: .combine)
        } else {
            Text("关键节点都已过去").font(.caption).foregroundStyle(Ink.muted)
        }
    }

    @ViewBuilder private func unit(_ v: Int, _ u: String, show: Bool = true) -> some View {
        if show {
            Text(verbatim: "\(v)").font(Type_.num(26)).foregroundStyle(Ink.accent)
            Text(u).font(.footnote).foregroundStyle(Ink.text).padding(.trailing, 4)
        }
    }
}

/// 其余事项：一条连续的撕口票据，每件事之间是虚线撕口。
struct EventStub: View {
    var e: EventItem
    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            VStack(alignment: .leading, spacing: 2) {
                Text(verbatim: e.monthOnly ? "当月" : "\(Int(e.date.suffix(2)) ?? 0)").font(Type_.num(22))
                Text(e.monthOnly ? "日期未定" : e.weekday).font(.caption2).foregroundStyle(Ink.muted)
            }
            .frame(width: 46, alignment: .leading)
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(e.kind).font(.caption2.weight(.semibold)).foregroundStyle(e.kind == "税务" ? Ink.accent : Ink.text2)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(e.kind == "税务" ? Ink.accentSoft : .clear, in: RoundedRectangle(cornerRadius: 4))
                        .overlay(RoundedRectangle(cornerRadius: 4).stroke(Ink.rule))
                    Text(e.left).font(.caption2.weight(.semibold)).foregroundStyle(Ink.accent)
                }
                Text(e.title).font(.subheadline.weight(.semibold))
                let meta = [e.member, e.end.map { "至 \($0)" } ?? "", e.note].filter { !$0.isEmpty }.joined(separator: "，")
                if !meta.isEmpty { Text(meta).font(.caption).foregroundStyle(Ink.muted).lineLimit(3) }
                if let m = e.money {
                    Text("\(m.usd) / \(m.cny)").font(Type_.sans(12, weight: .medium)).foregroundStyle(Ink.text2)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 12)
        .overlay(alignment: .bottom) {
            Line().stroke(Ink.rule, style: StrokeStyle(lineWidth: 1, dash: [4, 3])).frame(height: 1)
        }
    }
}
