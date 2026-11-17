import SwiftUI

struct OverviewView: View {
    var snap: Snapshot
    @State private var view = Snapshot.compositionViews[0]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text("\(snap.asOfLabel)，USD/CNY \(snap.fx)")
                        .font(.subheadline).foregroundStyle(Ink.text2)
                        .padding(.horizontal, 20)

                    // 只放家族这一张凭证（2026-11-12 用户说按人分的「名下」不重要，各成员的凭证去掉了）
                    if let fam = snap.family {
                        CertificateCard(cert: fam, asOf: snap.asOfLabel, fx: snap.fx)
                            .padding(.horizontal, 16).padding(.top, 16)
                    }

                    VStack(alignment: .leading, spacing: 0) {
                        SectionHeader(no: 1, title: "资产构成", hint: "100% = \(snap.family?.strip.first?.usd ?? "")")
                        Picker("构成视角", selection: $view) {
                            ForEach(Snapshot.compositionViews, id: \.self) { Text($0) }
                        }
                        .pickerStyle(.segmented).padding(.top, 14)
                        CompositionList(groups: snap.composition[view] ?? [])
                            .id(view).transition(.opacity)

                        SectionHeader(no: 2, title: "净资产走势", hint: "按住拖动看每一天")
                        TrendChart(points: snap.trend).padding(.top, 14)

                        SectionHeader(no: 3, title: "流动性", hint: "需要用钱时，多久能拿到")
                        LiquidityList(buckets: snap.liquidity)

                        SectionHeader(no: 4, title: "集中度", hint: "单一持仓占总资产")
                        ConcentrationList(items: snap.concentration)

                        ProvenanceFooter(snap: snap)
                    }
                    .padding(.horizontal, 20)
                    .padding(.bottom, 40)
                }
            }
            .background { PaperBackground() }
            .navigationTitle("总览")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { DemoToggle() }
                ToolbarItem(placement: .topBarTrailing) { ShareReportButton(snap: snap) }
            }
        }
    }
}

// MARK: - 凭证

struct CertificateCard: View {
    var cert: Certificate
    var asOf: String
    var fx: String
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var rolled = false
    @State private var lathe = false

    var body: some View {
        let e = Engrave(scheme: scheme)
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text(cert.title).font(Type_.song(17)).tracking(1)
                Spacer()
                Text(asOf).font(Type_.song(12, bold: false)).foregroundStyle(Ink.text2)
            }
            faceValue(rolled ? cert.net.usd : zeroed(cert.net.usd), size: 44, color: Ink.text)
                .contentTransition(.numericText())
                .lineLimit(1).minimumScaleFactor(0.6)
                .padding(.top, 16)
                .accessibilityLabel(cert.net.usd)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(cert.net.cny).font(Type_.sans(16)).foregroundStyle(Ink.text2)
                Text("按 \(fx) 折算").font(.caption).foregroundStyle(Ink.muted)
            }
            .padding(.top, 8)

            Rectangle().fill(Ink.rule).frame(height: 1).padding(.vertical, 16)

            HStack(alignment: .firstTextBaseline) {
                Text("今日盈亏").font(.footnote.weight(.medium)).foregroundStyle(Ink.text2)
                Text("较前一交易日收盘").font(.caption).foregroundStyle(Ink.muted)
            }
            faceValue(rolled ? cert.day.usd : zeroed(cert.day.usd), size: 28, color: Ink.signed(cert.day.sign))
                .contentTransition(.numericText())
                .lineLimit(1).minimumScaleFactor(0.7)
                .padding(.top, 8)
                .accessibilityLabel(cert.day.usd)
            HStack(spacing: 8) {
                Text(cert.day.cny).font(Type_.sans(14)).foregroundStyle(Ink.signed(cert.day.sign))
                if !cert.dayPct.isEmpty { Chip(text: cert.dayPct, sign: cert.day.sign) }
            }
            .padding(.top, 6)
            if !cert.contrib.isEmpty {
                VStack(spacing: 3) {
                    ForEach(cert.contrib, id: \.name) { c in
                        HStack {
                            Text(c.name).foregroundStyle(Ink.muted)
                            Spacer()
                            Text(c.usd).foregroundStyle(Ink.signed(c.sign))
                            Text(c.cny).font(Type_.sans(11)).foregroundStyle(Ink.signed(c.sign)).frame(minWidth: 96, alignment: .trailing)
                        }
                        .font(Type_.sans(12, weight: .medium))
                    }
                }
                .padding(.top, 12)
            }

            // 微缩文字线
            Text(String(repeating: "家庭资产与税务　仅在本机运行　", count: 8))
                .font(.system(size: 4.6)).foregroundStyle(Ink.accent.opacity(0.85))
                .lineLimit(1).padding(.top, 16).accessibilityHidden(true)

            LazyVGrid(columns: [GridItem(.flexible(), alignment: .topLeading), GridItem(.flexible(), alignment: .topLeading)],
                      alignment: .leading, spacing: 12) {
                ForEach(cert.strip, id: \.label) { s in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(s.label).font(.caption.weight(.medium)).foregroundStyle(Ink.text2)
                        if !s.qual.isEmpty { Text(s.qual).font(.caption2).foregroundStyle(Ink.muted) }
                        Text(s.usd).font(Type_.sans(17)).foregroundStyle(s.sign != 0 ? Ink.signed(s.sign) : Ink.text)
                            .lineLimit(1).minimumScaleFactor(0.7)
                        Text(s.cny + (s.pct.map { "，\($0)" } ?? "")).font(Type_.sans(11))
                            .foregroundStyle(s.sign != 0 ? Ink.signed(s.sign) : Ink.muted)
                    }
                }
            }
            .padding(.top, 10)
        }
        .padding(.horizontal, 30).padding(.top, 32).padding(.bottom, 26)
        .background {
            // 不透明的钞纸底（页面底纹不透进来），框内再叠：车花网、波纹底纹、玫瑰纹外圈的放射线、大玫瑰纹、左下青绿小玫瑰、安全纤维
            ZStack {
                Ink.couponBg
                ZStack {
                    GuillocheField(spacing: 6, wavelength: 90).foregroundStyle(Ink.accent).opacity(e.noteField)
                    Underprint().foregroundStyle(Ink.teal).opacity(e.underprint)
                    Sunburst(center: UnitPoint(x: 0.72, y: 0.3)).foregroundStyle(Ink.accent).opacity(e.sunburst)
                    GeometryReader { g in
                        Rosette(lineWidth: 0.7).foregroundStyle(Ink.accent).opacity(e.rosette)
                            .frame(width: 300, height: 300)
                            .rotationEffect(.degrees(lathe ? 0 : -50))
                            .opacity(lathe ? 1 : 0)
                            .position(x: g.size.width * 0.72, y: g.size.height * 0.3)
                        Rosette(lineWidth: 0.6).foregroundStyle(Ink.teal).opacity(e.rosette * 0.9)
                            .frame(width: 170, height: 170)
                            .position(x: 18, y: g.size.height - 30)
                    }
                    Fibers(colors: e.fiberColors, count: 90).opacity(e.fibers)
                        .shadow(color: scheme == .dark ? Color(hex: 0x58decc, alpha: 0.5) : .clear, radius: 2)
                }
                .clipped()
                .padding(14)
            }
        }
        .overlay { EngravedFrame(medalFill: Ink.couponBg) }
        .onAppear {
            guard !rolled else { return }
            if reduceMotion { rolled = true; lathe = true; return }
            withAnimation(.spring(duration: 1.4, bounce: 0.15).delay(0.15)) { rolled = true }
            withAnimation(.easeOut(duration: 2.2)) { lathe = true }
        }
    }
}

// MARK: - 构成、走势、流动性、集中度

struct CompositionList: View {
    var groups: [CompGroup]
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            CompositionBar(groups: groups, height: 12).padding(.top, 16)
            ForEach(groups) { g in
                HStack(alignment: .top, spacing: 10) {
                    RoundedRectangle(cornerRadius: 2).fill(Ink.slot(g.slot)).frame(width: 10, height: 10).padding(.top, 4)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(g.name).font(.subheadline.weight(.semibold))
                        Text(g.desc).font(.caption).foregroundStyle(Ink.muted).lineLimit(2)
                        Text("占 \(g.share)").font(Type_.sans(12)).foregroundStyle(Ink.text2).padding(.top, 2)
                    }
                    Spacer(minLength: 8)
                    VStack(alignment: .trailing, spacing: 4) {
                        MoneyStack(m: Money(v: g.v, usd: g.usd, cny: g.cny, sign: 0), size: 15)
                        if let d = g.day { Text(d.usd).font(Type_.sans(12)).foregroundStyle(Ink.signed(d.sign)) }
                    }
                }
                .padding(.vertical, 12)
                .overlay(alignment: .bottom) { Rectangle().fill(Ink.ruleSoft).frame(height: 1) }
            }
        }
    }
}

/// 净资产走势：线 + 线下竖向排线 + 起点虚线 + 取整网格；按住拖动看每一天。
struct TrendChart: View {
    var points: [TrendPoint]
    @State private var picked: Int?

    var body: some View {
        let sel = picked ?? (points.count - 1)
        VStack(alignment: .leading, spacing: 8) {
            if points.indices.contains(sel) {
                let p = points[sel]
                HStack(alignment: .firstTextBaseline) {
                    Text(p.date).font(Type_.sans(12)).foregroundStyle(Ink.muted)
                    Spacer()
                    Text(p.usd).font(Type_.num(20)).foregroundStyle(Ink.text)
                }
                Text(p.cny).font(Type_.sans(12)).foregroundStyle(Ink.muted).frame(maxWidth: .infinity, alignment: .trailing)
            }
            GeometryReader { geo in
                let vals = points.map(\.v)
                let lo0 = vals.min() ?? 0, hi0 = vals.max() ?? 1
                let pad = max((hi0 - lo0) * 0.12, abs(hi0) * 0.002, 1)
                let lo = lo0 - pad, hi = hi0 + pad
                let w = geo.size.width, h = geo.size.height
                let x = { (i: Int) in points.count > 1 ? w * CGFloat(i) / CGFloat(points.count - 1) : 0 }
                let y = { (v: Double) in h * (1 - (v - lo) / (hi - lo)) }
                Canvas { ctx, _ in
                    // 网格：取整的横线，左上标金额（大额按「万」）
                    let span = hi - lo
                    let raw = span / 4
                    let mag = pow(10, floor(log10(max(raw, 1e-9))))
                    let step = [1.0, 2, 5, 10].map { $0 * mag }.first { $0 >= raw } ?? mag * 10
                    var t = ceil(lo / step) * step
                    while t <= hi {
                        let yy = y(t)
                        var g = Path(); g.move(to: CGPoint(x: 0, y: yy)); g.addLine(to: CGPoint(x: w, y: yy))
                        ctx.stroke(g, with: .color(Ink.ruleSoft), lineWidth: 1)
                        let label = hi >= 1e5 ? String(format: "$%.0f 万", t / 1e4) : String(format: "$%.0f", t)
                        ctx.draw(Text(label).font(.system(size: 10)).foregroundStyle(Ink.muted), at: CGPoint(x: 2, y: yy - 7), anchor: .leading)
                        t += step
                    }
                    guard points.count > 1 else { return }
                    var line = Path()
                    for (i, p) in points.enumerated() {
                        let pt = CGPoint(x: x(i), y: y(p.v))
                        if i == 0 { line.move(to: pt) } else { line.addLine(to: pt) }
                    }
                    var area = line
                    area.addLine(to: CGPoint(x: w, y: h)); area.addLine(to: CGPoint(x: 0, y: h)); area.closeSubpath()
                    ctx.drawLayer { l in
                        l.clip(to: area)
                        var hatch = Path(), xx: CGFloat = 0
                        while xx <= w { hatch.move(to: CGPoint(x: xx, y: 0)); hatch.addLine(to: CGPoint(x: xx, y: h)); xx += 3.5 }
                        l.stroke(hatch, with: .color(Ink.accent.opacity(0.5)), lineWidth: 0.7)
                    }
                    var base = Path()
                    base.move(to: CGPoint(x: 0, y: y(points[0].v))); base.addLine(to: CGPoint(x: w, y: y(points[0].v)))
                    ctx.stroke(base, with: .color(Ink.rule), style: StrokeStyle(lineWidth: 1, dash: [3, 4]))
                    ctx.stroke(line, with: .color(Ink.accent), style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                    let s = CGPoint(x: x(sel), y: y(points[sel].v))
                    if picked != nil {
                        var cross = Path(); cross.move(to: CGPoint(x: s.x, y: 0)); cross.addLine(to: CGPoint(x: s.x, y: h))
                        ctx.stroke(cross, with: .color(Ink.text.opacity(0.25)), lineWidth: 1)
                    }
                    ctx.fill(Path(ellipseIn: CGRect(x: s.x - 4.5, y: s.y - 4.5, width: 9, height: 9)), with: .color(Ink.accent))
                }
                .contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 0)
                    .onChanged { v in
                        guard points.count > 1 else { return }
                        let i = Int((v.location.x / w * CGFloat(points.count - 1)).rounded())
                        let n = max(0, min(points.count - 1, i))
                        if n != picked { picked = n; UISelectionFeedbackGenerator().selectionChanged() }
                    }
                    .onEnded { _ in picked = nil })
            }
            .frame(height: 170)
            if let f = points.first, let l = points.last {
                HStack { Text(f.date); Spacer(); Text(l.date) }.font(Type_.sans(11)).foregroundStyle(Ink.muted)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("净资产走势，\(points.first?.date ?? "") \(points.first?.usd ?? "") 到 \(points.last?.date ?? "") \(points.last?.usd ?? "")")
    }
}

/// 流动性：每一级一行，横条从之前的累计画到之后的累计（淡色是之前的累计），填充表示性质。
struct LiquidityList: View {
    var buckets: [LiqBucket]
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(buckets) { b in
                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(b.bucket).font(Type_.song(16))
                            Text(b.timing).font(.caption).foregroundStyle(Ink.muted)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 1) {
                            Text(b.usd).font(Type_.sans(15, weight: .semibold))
                            Text(b.cny).font(Type_.sans(11)).foregroundStyle(Ink.muted)
                        }
                    }
                    StairBar(c0: b.c0, c1: b.c1, fill: b.fill).frame(height: 8)
                    Text("占 \(b.share)　累计 \(b.cum)").font(Type_.sans(12)).foregroundStyle(Ink.text2)
                    ForEach(b.rows, id: \.name) { r in
                        HStack(alignment: .firstTextBaseline) {
                            VStack(alignment: .leading, spacing: 1) {
                                Text(r.name).font(.caption).foregroundStyle(Ink.text)
                                Text(r.who).font(.caption2).foregroundStyle(Ink.muted)
                            }
                            Spacer()
                            Text(r.usd).font(Type_.sans(12)).foregroundStyle(Ink.text2)
                        }
                    }
                }
                .padding(.vertical, 14)
                .overlay(alignment: .bottom) { Rectangle().fill(Ink.ruleSoft).frame(height: 1) }
            }
        }
    }
}

struct StairBar: View {
    var c0: Double
    var c1: Double
    var fill: String
    var body: some View {
        GeometryReader { g in
            let w = g.size.width
            ZStack(alignment: .leading) {
                Rectangle().fill(Ink.sunken)
                Rectangle().fill(Ink.accent.opacity(0.14)).frame(width: w * c0)
                Canvas { ctx, size in
                    let r = CGRect(origin: .zero, size: size)
                    switch fill {
                    case "hatch", "cross":
                        var p = Path(), xx: CGFloat = -size.height
                        while xx < size.width + size.height {
                            p.move(to: CGPoint(x: xx, y: size.height)); p.addLine(to: CGPoint(x: xx + size.height, y: 0))
                            if fill == "cross" { p.move(to: CGPoint(x: xx, y: 0)); p.addLine(to: CGPoint(x: xx + size.height, y: size.height)) }
                            xx += 4
                        }
                        ctx.clip(to: Path(r))
                        ctx.stroke(p, with: .color(Ink.accent.opacity(0.8)), lineWidth: 1.1)
                    case "dots":
                        var yy: CGFloat = 1.5
                        while yy < size.height { var xx: CGFloat = 1.5
                            while xx < size.width { ctx.fill(Path(ellipseIn: CGRect(x: xx - 1, y: yy - 1, width: 2, height: 2)), with: .color(Ink.accent)); xx += 4 }
                            yy += 4 }
                    default:
                        ctx.fill(Path(r), with: .color(Ink.accent.opacity(0.6)))
                    }
                    var edge = Path(); edge.move(to: .zero); edge.addLine(to: CGPoint(x: 0, y: size.height))
                    ctx.stroke(edge, with: .color(Ink.accent), lineWidth: 1.5)
                }
                .frame(width: max(3, w * (c1 - c0)))
                .offset(x: w * c0)
            }
        }
        .accessibilityHidden(true)
    }
}

struct ConcentrationList: View {
    var items: [Concentration]
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(items) { c in
                HStack(alignment: .center) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(c.name).font(.subheadline.weight(.semibold))
                            if c.flag {
                                Text("超过总资产 10%").font(.caption2.weight(.semibold)).foregroundStyle(Ink.text2)
                                    .padding(.horizontal, 5).padding(.vertical, 1)
                                    .overlay(RoundedRectangle(cornerRadius: 4).stroke(Ink.rule))
                            }
                        }
                        Text(c.who).font(.caption).foregroundStyle(Ink.muted)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 3) {
                        Text(c.share).font(Type_.sans(15, weight: .semibold))
                        GeometryReader { g in
                            ZStack(alignment: .leading) {
                                Capsule().fill(Ink.sunken)
                                Capsule().fill(Ink.text2).frame(width: g.size.width * c.shareV)
                            }
                        }
                        .frame(width: 90, height: 5)
                        Text(c.usd).font(Type_.sans(11)).foregroundStyle(Ink.muted)
                    }
                }
                .padding(.vertical, 11)
                .overlay(alignment: .bottom) { Rectangle().fill(Ink.ruleSoft).frame(height: 1) }
            }
        }
    }
}
