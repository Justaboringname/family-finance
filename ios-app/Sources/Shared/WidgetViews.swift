import SwiftUI
import UIKit
import WidgetKit

// 小组件的界面（app 里也编进来，用 -FFWidgetPreview 1 在 app 里预览、截图）。

struct Entry: TimelineEntry {
    let date: Date
    let snap: Snapshot?
}

struct FamilyWidgetView: View {
    var entry: Entry
    var family: WidgetFamily
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        if let s = entry.snap, let f = s.family {
            switch family {
            case .systemMedium: medium(s, f)
            default: small(s, f)
            }
        } else {
            VStack(alignment: .leading, spacing: 6) {
                Text("家庭资产").font(Type_.song(15))
                Text("打开 app 导入快照").font(.caption).foregroundStyle(Ink.muted)
            }
        }
    }

    private func head(_ s: Snapshot) -> some View {
        HStack(spacing: 6) {
            Logo().frame(width: 16, height: 16)
            Text("家族净资产").font(Type_.song(12))
            Spacer(minLength: 0)
            if s.isDemo { Text("示例").font(.system(size: 9, weight: .semibold)).foregroundStyle(Ink.muted) }
        }
    }

    private func small(_ s: Snapshot, _ f: Certificate) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            head(s)
            Spacer(minLength: 4)
            faceValue(f.net.usd, size: 23).foregroundStyle(Ink.text)
                .lineLimit(1).minimumScaleFactor(0.55)
            Text(f.day.usd).font(Type_.sans(12, weight: .semibold)).foregroundStyle(Ink.signed(f.day.sign)).padding(.top, 4)
            Text(f.dayPct).font(Type_.sans(11)).foregroundStyle(Ink.signed(f.day.sign))
            Spacer(minLength: 2)
            Text(s.asOfLabel).font(.system(size: 9)).foregroundStyle(Ink.muted)
        }
    }

    private func medium(_ s: Snapshot, _ f: Certificate) -> some View {
        HStack(spacing: 14) {
            small(s, f)
            VStack(alignment: .leading, spacing: 6) {
                Sparkline(values: s.trend.map(\.v)).frame(maxHeight: .infinity)
                CompositionBar(groups: s.composition["按资产类别"] ?? [], height: 6)
                if let p = s.plans.first, let n = p.upcoming(at: entry.date), let at = n.atDate {
                    HStack(spacing: 4) {
                        Text("距\(n.short)").foregroundStyle(Ink.muted)
                        Text(at, style: .relative).foregroundStyle(Ink.accent)
                    }
                    .font(.system(size: 10, weight: .medium)).lineLimit(1)
                }
            }
        }
    }
}

/// 扁平「四方风车」logo（和网页一样，无渐变无光泽）。
/// app 的标志：和 app 图标同一张「紫色打孔息票」（scripts/make_app_icon.py 用 Icon Composer 渲染出 LogoMark.png / LogoMarkDark.png）。
struct Logo: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        if let img = UIImage(named: scheme == .dark ? "LogoMarkDark" : "LogoMark") {
            Image(uiImage: img).resizable().interpolation(.high).aspectRatio(1, contentMode: .fit)
        } else {
            LegacyPinwheel()
        }
    }
}

/// 旧标志（藏蓝底白色四方风车），只在找不到图片时用。
private struct LegacyPinwheel: View {
    var body: some View {
        Canvas { ctx, size in
            let k = size.width / 100
            ctx.fill(Path(roundedRect: CGRect(origin: .zero, size: size), cornerRadius: 21 * k), with: .color(Color(hex: 0x1f4270)))
            for (x, y, w, h) in [(22.0, 22.0, 33.6, 18.0), (60, 22, 18, 33.6), (44.4, 60, 33.6, 18), (22, 44.4, 18, 33.6)] {
                ctx.fill(Path(roundedRect: CGRect(x: x * k, y: y * k, width: w * k, height: h * k), cornerRadius: 2.6 * k),
                         with: .color(.white))
            }
        }
    }
}

/// 小组件的底：页面底色 + 右下角一枚淡淡的玫瑰纹。
struct WidgetBackground: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        let e = Engrave(scheme: scheme)
        ZStack(alignment: .bottomTrailing) {
            Ink.bg
            GuillocheField(spacing: 6, wavelength: 80).foregroundStyle(Ink.accent).opacity(e.noteField)
            Rosette(lineWidth: 0.6).foregroundStyle(Ink.accent).opacity(e.rosette * 0.7)
                .frame(width: 190, height: 190).offset(x: 70, y: 70)
            Fibers(colors: e.fiberColors, count: 22).opacity(e.fibers)
        }
    }
}
