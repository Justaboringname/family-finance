import SwiftUI

/// 持仓：雕版账页。点一行展开股数、收盘、成本、浮盈。
/// （原来顶上有按成员筛选，2026-11-12 用户说按人分不重要，去掉了。）
struct HoldingsView: View {
    var snap: Snapshot
    @State private var open: Set<String> = []

    private var shown: [HoldGroup] { snap.holdings }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if let f = snap.family {
                        VStack(alignment: .leading, spacing: 4) {
                            faceValue(f.strip.first?.usd ?? f.net.usd, size: 30).foregroundStyle(Ink.text)
                            HStack(spacing: 8) {
                                Text(f.strip.first?.cny ?? f.net.cny).font(Type_.sans(13)).foregroundStyle(Ink.muted)
                                Text("今日 \(f.day.usd)").font(Type_.sans(13, weight: .medium)).foregroundStyle(Ink.signed(f.day.sign))
                                if !f.dayPct.isEmpty { Chip(text: f.dayPct, sign: f.day.sign) }
                            }
                        }
                    }

                    ForEach(shown) { g in
                        GroupHeader(g: g).padding(.top, 26)
                        ForEach(g.rows) { r in
                            HoldRowView(r: r, expanded: open.contains(g.key + r.id))
                                .contentShape(Rectangle())
                                .onTapGesture {
                                    withAnimation(.snappy) {
                                        if open.contains(g.key + r.id) { open.remove(g.key + r.id) } else { open.insert(g.key + r.id) }
                                    }
                                }
                        }
                    }
                    ProvenanceFooter(snap: snap)
                }
                .padding(.horizontal, 20).padding(.bottom, 40)
            }
            .background { PaperBackground() }
            .navigationTitle("持仓")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { DemoToggle() }
                ToolbarItem(placement: .topBarTrailing) { ShareReportButton(snap: snap) }
            }
        }
    }
}

struct GroupHeader: View {
    var g: HoldGroup
    var body: some View {
        VStack(spacing: 8) {
            Rectangle().fill(Ink.ruleStrong).frame(height: 1)
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(g.title).font(Type_.song(17))
                    Text(g.sub).font(.caption).foregroundStyle(Ink.muted)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    MoneyStack(m: g.total, size: 15)
                    if let d = g.day { Text("今日 \(d.usd)").font(Type_.sans(12)).foregroundStyle(Ink.signed(d.sign)) }
                }
            }
            Rectangle().fill(Ink.rule).frame(height: 1)
        }
    }
}

struct HoldRowView: View {
    var r: HoldRow
    var expanded: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(r.name).font(.subheadline.weight(.semibold)).tracking(0.3)
                    Text(r.meta).font(.caption).foregroundStyle(Ink.muted).lineLimit(expanded ? nil : 1)
                }
                Spacer(minLength: 8)
                VStack(alignment: .trailing, spacing: 3) {
                    if let mv = r.mv { Text(mv.usd).font(Type_.sans(15, weight: .semibold)) }
                    HStack(spacing: 6) {
                        if let d = r.day { Text(d.usd).font(Type_.sans(12)).foregroundStyle(Ink.signed(d.sign)) }
                        if !r.chg.isEmpty { Chip(text: r.chg, sign: r.chgSign) }
                    }
                }
            }
            if expanded {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
                    if !r.shares.isEmpty { detail("股数", r.shares) }
                    if !r.close.isEmpty { detail("收盘", "\(r.close)，\(r.prev)") }
                    if let mv = r.mv { detail("市值", "\(mv.usd) / \(mv.cny)") }
                    if let d = r.day { detail("今日盈亏", "\(d.usd) / \(d.cny)", sign: d.sign) }
                    if let c = r.cost { detail("成本", "\(c.usd) / \(c.cny)") }
                    if let u = r.unr { detail("浮盈", "\(u.usd) / \(u.cny)" + (r.unrPct.isEmpty ? "" : "，\(r.unrPct)"), sign: u.sign) }
                }
                .font(Type_.sans(12))
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .padding(.vertical, 12)
        .overlay(alignment: .bottom) { Rectangle().fill(Ink.ruleSoft).frame(height: 1) }
        .accessibilityElement(children: .combine)
        .accessibilityHint(expanded ? "轻点收起" : "轻点展开明细")
    }

    @ViewBuilder private func detail(_ k: String, _ v: String, sign: Int = 0) -> some View {
        GridRow {
            Text(k).foregroundStyle(Ink.muted)
            Text(v).foregroundStyle(sign == 0 ? Ink.text2 : Ink.signed(sign))
        }
    }
}
