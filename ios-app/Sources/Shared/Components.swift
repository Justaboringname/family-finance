import SwiftUI
import UIKit

/// 金额文字拆成「$ + 整数 + 小数」三段：$ 和小数小一号，像钞票面额。
func faceValue(_ s: String, size: CGFloat, color: Color? = nil) -> Text {
    var sign = "", rest = s
    if let f = rest.first, f == "+" || f == "−" || f == "-" { sign = String(f); rest.removeFirst() }
    var cur = ""
    if let f = rest.first, f == "$" || f == "¥" { cur = String(f); rest.removeFirst() }
    let parts = rest.split(separator: ".", maxSplits: 1).map(String.init)
    let whole = parts.first ?? rest, dec = parts.count > 1 ? "." + parts[1] : ""
    let c = Text(sign + cur).font(Type_.num(size * 0.42)).baselineOffset(size * 0.42)
    let w = Text(whole).font(Type_.num(size))
    let d = Text(dec).font(Type_.num(size * 0.44))
    var t = Text("\(c)\(w)\(d)")
    if let color { t = t.foregroundStyle(color) }
    return t
}

/// 「数字从 0 滚到位」：先显示把数字都换成 0 的字串，出现后带动画换成真值（系统的 numericText 过渡）。
func zeroed(_ s: String) -> String { String(s.map { $0.isNumber ? "0" : $0 }) }

/// 迷你走势：线 + 线下竖向排线。
struct Sparkline: View {
    var values: [Double]
    var hatch = true
    var body: some View {
        Canvas { ctx, size in
            guard values.count > 1, let lo = values.min(), let hi = values.max() else { return }
            let pad = (hi - lo) * 0.12 + 1e-9
            let y = { (v: Double) in size.height * (1 - (v - (lo - pad)) / ((hi + pad) - (lo - pad))) }
            let x = { (i: Int) in size.width * CGFloat(i) / CGFloat(values.count - 1) }
            var line = Path()
            for (i, v) in values.enumerated() {
                let p = CGPoint(x: x(i), y: y(v))
                if i == 0 { line.move(to: p) } else { line.addLine(to: p) }
            }
            if hatch {
                var area = line
                area.addLine(to: CGPoint(x: size.width, y: size.height))
                area.addLine(to: CGPoint(x: 0, y: size.height))
                area.closeSubpath()
                ctx.drawLayer { l in
                    l.clip(to: area)
                    var xx: CGFloat = 0
                    var lines = Path()
                    while xx <= size.width { lines.move(to: CGPoint(x: xx, y: 0)); lines.addLine(to: CGPoint(x: xx, y: size.height)); xx += 3 }
                    l.stroke(lines, with: .color(Ink.accent.opacity(0.45)), lineWidth: 0.7)
                }
            }
            ctx.stroke(line, with: .color(Ink.accent), style: StrokeStyle(lineWidth: 1.6, lineCap: .round, lineJoin: .round))
            if let last = values.last {
                let p = CGPoint(x: size.width, y: y(last))
                ctx.fill(Path(ellipseIn: CGRect(x: p.x - 3, y: p.y - 3, width: 6, height: 6)), with: .color(Ink.accent))
            }
        }
    }
}

/// 100% 构成色条。
struct CompositionBar: View {
    var groups: [CompGroup]
    var height: CGFloat = 10
    var body: some View {
        GeometryReader { geo in
            let shown = groups.filter { $0.bp > 0 }
            let gaps = CGFloat(max(0, shown.count - 1)) * 2
            HStack(spacing: 2) {
                ForEach(shown) { g in
                    Rectangle().fill(Ink.slot(g.slot))
                        .frame(width: max(2, (geo.size.width - gaps) * CGFloat(g.bp) / 10000))
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 2))
        }
        .frame(height: height)
    }
}
