import SwiftUI
import UIKit

// 和网页同一套令牌（ui_theme.LIGHT / DARK）：中性灰底、墨色文字、墨蓝强调、固定的分类色、绿涨红跌。
// 深色 = 「紫外灯下的钞票」：雕版线更亮，安全纤维发荧光。

extension Color {
    init(hex: UInt32, alpha: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 0xff) / 255, green: Double((hex >> 8) & 0xff) / 255,
                  blue: Double(hex & 0xff) / 255, opacity: alpha)
    }

    /// 跟随深浅色自动切换的颜色。
    init(light: UInt32, dark: UInt32, lightAlpha: Double = 1, darkAlpha: Double = 1) {
        self.init(uiColor: UIColor { t in
            let (h, a) = t.userInterfaceStyle == .dark ? (dark, darkAlpha) : (light, lightAlpha)
            return UIColor(red: CGFloat((h >> 16) & 0xff) / 255, green: CGFloat((h >> 8) & 0xff) / 255,
                           blue: CGFloat(h & 0xff) / 255, alpha: a)
        })
    }
}

enum Ink {
    static let bg = Color(light: 0xf2f2f2, dark: 0x1d1d1d)
    static let surface = Color(light: 0xf9f9f9, dark: 0x252525)
    static let sunken = Color(light: 0xe3e3e3, dark: 0x131313)
    static let text = Color(light: 0x1c1a16, dark: 0xede9e0)
    static let text2 = Color(light: 0x4d4940, dark: 0xbfb9ac)
    static let muted = Color(light: 0x6b665c, dark: 0x958f83)
    static let rule = Color(light: 0xdcd6c9, dark: 0x36332d)
    static let ruleSoft = Color(light: 0xe6e1d5, dark: 0x2c2a26)
    static let ruleStrong = Color(light: 0x1c1a16, dark: 0xa29c90)
    static let accent = Color(light: 0x234b7c, dark: 0x8fb1e0)
    static let accentSoft = Color(light: 0x234b7c, dark: 0x8fb1e0, lightAlpha: 0.09, darkAlpha: 0.12)
    static let up = Color(light: 0x17703f, dark: 0x5cbf86)
    static let down = Color(light: 0xb3261e, dark: 0xf0786c)
    static let upSoft = Color(light: 0x17703f, dark: 0x5cbf86, lightAlpha: 0.1, darkAlpha: 0.14)
    static let downSoft = Color(light: 0xb3261e, dark: 0xf0786c, lightAlpha: 0.1, darkAlpha: 0.14)
    static let couponBg = Color(light: 0xf8f8f8, dark: 0x232323)
    static let frameLine = Color(light: 0x234b7c, dark: 0x8fb1e0, lightAlpha: 0.5, darkAlpha: 0.45)
    static let teal = Color(hex: 0x129390)

    /// 分类色槽位 1…5：墨蓝、赭黄、青绿、赤陶、梅紫（和网页 --ff-s1…s5 一样，资产类别的槽位由 Mac 端给出）。
    static func slot(_ n: Int) -> Color {
        let light: [UInt32] = [0x2e5794, 0xad7a17, 0x129390, 0xbf623d, 0x884783]
        let dark: [UInt32] = [0x5581c1, 0xb48226, 0x129390, 0xc66843, 0x9b5896]
        let i = max(0, min(4, n - 1))
        return Color(light: light[i], dark: dark[i])
    }

    static func signed(_ s: Int) -> Color { s > 0 ? up : s < 0 ? down : text }
    static func signedSoft(_ s: Int) -> Color { s > 0 ? upSoft : s < 0 ? downSoft : sunken }
}

/// 装饰的浓淡（深色更亮，像紫外灯下的雕版线）。
struct Engrave {
    var scheme: ColorScheme
    var band: Double { scheme == .dark ? 0.72 : 0.62 }
    var rosette: Double { scheme == .dark ? 0.28 : 0.2 }
    var field: Double { scheme == .dark ? 0.14 : 0.11 }        // 整页满版底纹
    var noteField: Double { scheme == .dark ? 0.12 : 0.1 }    // 凭证、息票里的底纹
    var sunburst: Double { scheme == .dark ? 0.08 : 0.075 }
    var watermark: Double { scheme == .dark ? 0.15 : 0.11 }
    var underprint: Double { scheme == .dark ? 0.07 : 0.06 }
    var fibers: Double { scheme == .dark ? 0.5 : 0.45 }
    var fiberColors: [Color] {
        scheme == .dark
            ? [Color(hex: 0x58decc, alpha: 0.9), Color(hex: 0xa0c0f0, alpha: 0.85), Color(hex: 0xe8c470, alpha: 0.8)]
            : [Color(hex: 0xbf623d, alpha: 0.6), Color(hex: 0x2e5794, alpha: 0.55), Color(hex: 0x129390, alpha: 0.55)]
    }
}

enum Type_ {
    /// 宋体（标题）：系统有就用，没有就退到衬线体。
    static func song(_ size: CGFloat, bold: Bool = true) -> Font {
        let name = bold ? "STSongti-SC-Bold" : "STSongti-SC-Regular"
        if UIFont(name: name, size: size) != nil { return .custom(name, size: size) }
        return .system(size: size, weight: bold ? .bold : .regular, design: .serif)
    }

    /// 面额数字：New York 衬线、等宽数字。
    static func num(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .serif).monospacedDigit()
    }

    static func sans(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight).monospacedDigit()
    }
}
