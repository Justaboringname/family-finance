import SwiftUI

// 雕版纹样：和网页 ui_theme._ornament_css 同一套数学（几圈绕圆起伏的正弦线，每圈几条错开相位），
// 在这里直接画成矢量路径，颜色跟着令牌走。

enum Guilloche {
    /// 一圈网纹：strands 条线，半径按 waves 个周期起伏。坐标是 1000×1000 的画布，圆心在中间。
    static func ring(_ radius: Double, _ amp: Double, _ waves: Int, _ strands: Int, _ amp2: Double = 0,
                     steps: Int = 9) -> Path {
        var p = Path()
        let n = waves * steps
        for c in 0..<strands {
            let ph = 2 * Double.pi * Double(c) / Double(strands)
            for s in 0...n {
                let t = 2 * Double.pi * Double(s) / Double(n)
                let r = radius + amp * sin(Double(waves) * t + ph) + amp2 * sin(Double(2 * waves) * t - ph)
                let pt = CGPoint(x: 500 + r * cos(t), y: 500 + r * sin(t))
                if s == 0 { p.move(to: pt) } else { p.addLine(to: pt) }
            }
            p.closeSubpath()
        }
        return p
    }

    static let rosette: Path = {
        var p = Path()
        for r in [ring(440, 42, 36, 6, 10), ring(352, 26, 36, 4), ring(268, 34, 24, 5, 8), ring(178, 24, 18, 4),
                  ring(104, 30, 10, 3, 6), ring(46, 14, 6, 3)] { p.addPath(r) }
        return p
    }()

    static let medal: Path = {
        var p = Path(ellipseIn: CGRect(x: 34, y: 34, width: 932, height: 932))
        p.addPath(ring(318, 74, 10, 3))
        p.addPath(ring(140, 44, 6, 2))
        return p
    }()

    /// 把 1000×1000 的路径缩放进一个方框。
    static func fit(_ path: Path, in rect: CGRect) -> Path {
        let s = min(rect.width, rect.height) / 1000
        return path.applying(CGAffineTransform(translationX: rect.midX - 500 * s, y: rect.midY - 500 * s).scaledBy(x: s, y: s))
    }
}

/// 大玫瑰纹。
struct Rosette: View {
    var lineWidth: CGFloat = 0.8
    var body: some View {
        Canvas { ctx, size in
            ctx.stroke(Guilloche.fit(Guilloche.rosette, in: CGRect(origin: .zero, size: size)), with: .foreground,
                       lineWidth: lineWidth)
        }
        .aspectRatio(1, contentMode: .fit)
    }
}

/// 四角的小纹章：一块底色圆盖住波纹带的交叉处，上面一枚小玫瑰纹。
struct Medal: View {
    var fill: Color
    var body: some View {
        Canvas { ctx, size in
            let r = CGRect(origin: .zero, size: size)
            ctx.fill(Path(ellipseIn: r), with: .color(fill))
            ctx.stroke(Guilloche.fit(Guilloche.medal, in: r), with: .color(Ink.accent), lineWidth: max(0.7, size.width / 46))
        }
    }
}

/// 波纹带：三条错相正弦线，沿着长边重复（period 是一个周期的长度）。
struct WaveBand: Shape {
    var vertical = false
    var period: CGFloat = 18
    var strands = 3

    func path(in rect: CGRect) -> Path {
        var p = Path()
        let len = vertical ? rect.height : rect.width
        let thick = vertical ? rect.width : rect.height
        let amp = thick * 0.38
        for c in 0..<strands {
            let ph = 2 * CGFloat.pi * CGFloat(c) / CGFloat(strands)
            var t: CGFloat = 0
            var first = true
            while t <= len {
                let w = thick / 2 + amp * sin(2 * .pi * t / period + ph)
                let pt = vertical ? CGPoint(x: rect.minX + w, y: rect.minY + t) : CGPoint(x: rect.minX + t, y: rect.minY + w)
                if first { p.move(to: pt); first = false } else { p.addLine(to: pt) }
                t += period / 24
            }
        }
        return p
    }
}

/// 框内很淡的底纹：两条交叉的长波，一行一行铺满。
struct Underprint: View {
    var body: some View {
        Canvas { ctx, size in
            var p = Path()
            var y: CGFloat = 0
            while y < size.height + 18 {
                for c in 0..<2 {
                    var x: CGFloat = 0
                    var first = true
                    while x <= size.width {
                        let yy = y + 9 + 6 * sin(2 * .pi * x / 96 + .pi * CGFloat(c))
                        if first { p.move(to: CGPoint(x: x, y: yy)); first = false } else { p.addLine(to: CGPoint(x: x, y: yy)) }
                        x += 4
                    }
                }
                y += 18
            }
            ctx.stroke(p, with: .foreground, lineWidth: 0.6)
        }
    }
}

/// 钞纸里的安全纤维：随机散落的短弧线，三种颜色轮流；种子固定，每次都一样。
struct Fibers: View {
    var colors: [Color]
    var count = 60

    var body: some View {
        Canvas { ctx, size in
            var rng = SeededRandom(seed: 20261111)
            for i in 0..<count {
                let x = rng.next() * size.width, y = rng.next() * size.height
                let a = rng.next() * 2 * .pi, len = 6 + rng.next() * 8, bend = (rng.next() - 0.5) * 8
                let x2 = x + len * cos(a), y2 = y + len * sin(a)
                let cx = (x + x2) / 2 - bend * sin(a), cy = (y + y2) / 2 + bend * cos(a)
                var p = Path()
                p.move(to: CGPoint(x: x, y: y))
                p.addQuadCurve(to: CGPoint(x: x2, y: y2), control: CGPoint(x: cx, y: cy))
                ctx.stroke(p, with: .color(colors[i % colors.count]), style: StrokeStyle(lineWidth: 0.9, lineCap: .round))
            }
        }
    }
}

struct SeededRandom {
    var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next() -> CGFloat {
        state = state &* 6364136223846793005 &+ 1442695040888963407
        return CGFloat(Double(state >> 11) / Double(1 << 53))
    }
}

/// 凭证和息票共用的雕版边框：外线、内线，四边波纹带，四角纹章。
struct EngravedFrame: View {
    var inset: CGFloat = 14
    var band: CGFloat = 9
    var bandInset: CGFloat = 3
    var medal: CGFloat = 26
    var period: CGFloat = 14
    var medalFill: Color = Ink.bg
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width, h = geo.size.height
            let e = Engrave(scheme: scheme)
            let c = bandInset + band / 2
            ZStack(alignment: .topLeading) {
                Rectangle().stroke(Ink.frameLine, lineWidth: 1)
                Rectangle().stroke(Ink.frameLine, lineWidth: 1)
                    .frame(width: w - 2 * inset, height: h - 2 * inset).offset(x: inset, y: inset)
                Group {
                    WaveBand(period: period).stroke(Ink.accent, lineWidth: 0.7)
                        .frame(width: w - 2 * inset, height: band).offset(x: inset, y: bandInset)
                    WaveBand(period: period).stroke(Ink.accent, lineWidth: 0.7)
                        .frame(width: w - 2 * inset, height: band).offset(x: inset, y: h - bandInset - band)
                    WaveBand(vertical: true, period: period).stroke(Ink.accent, lineWidth: 0.7)
                        .frame(width: band, height: h - 2 * inset).offset(x: bandInset, y: inset)
                    WaveBand(vertical: true, period: period).stroke(Ink.accent, lineWidth: 0.7)
                        .frame(width: band, height: h - 2 * inset).offset(x: w - bandInset - band, y: inset)
                }
                .opacity(e.band)
                ForEach(0..<4, id: \.self) { k in
                    Medal(fill: medalFill)
                        .frame(width: medal, height: medal)
                        .offset(x: (k % 2 == 0 ? c : w - c) - medal / 2, y: (k < 2 ? c : h - c) - medal / 2)
                }
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// 满版「车花」底纹：一行行正弦线，振幅随行号缓慢涨落，两组反向错相交叉成网，像钞票的底纹。
struct GuillocheField: View {
    var spacing: CGFloat = 7
    var wavelength: CGFloat = 120
    var lineWidth: CGFloat = 0.5

    var body: some View {
        Canvas { ctx, size in
            var p = Path()
            let rows = Int(size.height / spacing) + 4
            for fam in 0..<2 {
                let dir: CGFloat = fam == 0 ? 1 : -1
                for k in -3..<rows {
                    let kk = CGFloat(k)
                    let y0 = kk * spacing
                    let amp = spacing * (1.15 + 0.85 * sin(kk * 2 * .pi / 26))
                    var x: CGFloat = -4
                    p.move(to: CGPoint(x: x, y: y0 + amp * sin(2 * .pi * x / wavelength + dir * kk * 0.3)))
                    while x <= size.width + 4 {
                        x += 4
                        p.addLine(to: CGPoint(x: x, y: y0 + amp * sin(2 * .pi * x / wavelength + dir * kk * 0.3)))
                    }
                }
            }
            ctx.stroke(p, with: .foreground, lineWidth: lineWidth)
        }
        .accessibilityHidden(true)
    }
}

/// 从一点放射出去的细线（玫瑰纹外圈的「光芒」）。
struct Sunburst: View {
    var center: UnitPoint
    var inner: CGFloat = 150
    var count = 220

    var body: some View {
        Canvas { ctx, size in
            let c = CGPoint(x: size.width * center.x, y: size.height * center.y)
            let outer = hypot(max(c.x, size.width - c.x), max(c.y, size.height - c.y))
            var p = Path()
            for i in 0..<count {
                let a = 2 * CGFloat.pi * CGFloat(i) / CGFloat(count)
                let r0 = inner + (i % 2 == 0 ? 0 : 14)
                p.move(to: CGPoint(x: c.x + r0 * cos(a), y: c.y + r0 * sin(a)))
                p.addLine(to: CGPoint(x: c.x + outer * cos(a), y: c.y + outer * sin(a)))
            }
            ctx.stroke(p, with: .foreground, lineWidth: 0.5)
        }
        .accessibilityHidden(true)
    }
}

/// 整页的钞纸底：中性灰底，满版车花底纹（页头浓、往下淡），右上角大玫瑰水印、左下角一枚青绿小玫瑰，散落安全纤维。
/// 静态绘制、挂在 ScrollView 背后（不随滚动重画），颜色只用令牌。
struct PaperBackground: View {
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let e = Engrave(scheme: scheme)
        GeometryReader { g in
            ZStack {
                Ink.bg
                GuillocheField().foregroundStyle(Ink.accent).opacity(e.field)
                    .mask(LinearGradient(stops: [.init(color: .black, location: 0), .init(color: .black.opacity(0.75), location: 0.12),
                                                 .init(color: .black.opacity(0.42), location: 0.3), .init(color: .black.opacity(0.42), location: 1)],
                                         startPoint: .top, endPoint: .bottom))
                Rosette(lineWidth: 0.6).foregroundStyle(Ink.accent).opacity(e.watermark)
                    .frame(width: 440, height: 440)
                    .position(x: g.size.width - 30, y: 90)
                Rosette(lineWidth: 0.6).foregroundStyle(Ink.teal).opacity(e.watermark * 0.8)
                    .frame(width: 300, height: 300)
                    .position(x: 20, y: g.size.height - 150)
                Fibers(colors: e.fiberColors, count: 60).opacity(e.fibers * 0.55)
            }
            .drawingGroup()
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}
