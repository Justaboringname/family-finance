#!/usr/bin/env python3
"""生成 app 图标「打孔息票」的 Icon Composer 文档：Resources/AppIcon.icon（icon.json + 单色 SVG 图层）。

用户 2026-11-11 从 6 个方向里选的「打孔息票」：藏蓝底上一张略斜的近白证券息票，两侧半圆缺口、一列打孔，
票面上一道雕版内框和两条镜像波纹带，票根上一枚青绿小章里是品牌四方风车。几何来自设计稿
（~/Downloads/family-finance-icon-directions-2026-11-11/d-coupon/_gen/gen.py），在这里加粗了内框、放大了小章里的风车。

每个图层是一个白色的单色 SVG，颜色和深色版本都写在 icon.json 里，iOS 自己渲染 Liquid Glass、深色和着色外观。
    python3 ios-app/scripts/make_app_icon.py          # 重新生成 Resources/AppIcon.icon
"""
import math
import os
import sys

VARIANT = "A"

# ---------------------------------------------------------------- geometry
CX, CY = 512.0, 512.0
ANG = math.radians(-8.0)          # negative = counter-clockwise on screen (rises to the right)
HW, HH = 372.0, 234.0             # ticket half width / half height
RC = 48.0                         # ticket corner radius
NOTCH = 60.0                      # side notch radius, centred on (+-HW, 0)
XP = -132.0                       # perforation x (stub on the left)
HOLE = 21.0                       # perforation hole radius
HOLES_Y = [-156.0, -78.0, 0.0, 78.0, 156.0]

# face inset frame (engraved border), concentric with the right notch
BD = 34.0                         # inset distance from outer edge
BSW = 18.0                        # frame stroke width（12 在 58px 时消失，加粗到 18）
BXL = XP + 44.0                   # frame left x (clear of the holes)
BRC = 14.0                        # frame corner radius

# stub seal (pinwheel badge)
SEAL_X, SEAL_Y = -232.0, 0.0
SEAL_R = 66.0
PIN_S = 84.0                      # 风车在小章里占约 64%（原 76，小尺寸认不出）


def rot(x, y):
    c, s = math.cos(ANG), math.sin(ANG)
    return CX + x * c - y * s, CY + x * s + y * c


def P(x, y):
    X, Y = rot(x, y)
    return f"{X:.2f} {Y:.2f}"


def arc(r, large, sweep, x, y):
    return f"A{r:.2f} {r:.2f} 0 {large} {sweep} {P(x, y)}"


# ---------------------------------------------------------------- paper
def ticket_outer():
    r, n = RC, NOTCH
    return " ".join([
        f"M{P(-HW + r, -HH)}",
        f"L{P(HW - r, -HH)}",
        arc(r, 0, 1, HW, -HH + r),
        f"L{P(HW, -n)}",
        arc(n, 0, 0, HW, n),
        f"L{P(HW, HH - r)}",
        arc(r, 0, 1, HW - r, HH),
        f"L{P(-HW + r, HH)}",
        arc(r, 0, 1, -HW, HH - r),
        f"L{P(-HW, n)}",
        arc(n, 0, 0, -HW, -n),
        f"L{P(-HW, -HH + r)}",
        arc(r, 0, 1, -HW + r, -HH),
        "Z",
    ])


def circle_sub(cx, cy, r, ccw=True):
    sw = 0 if ccw else 1
    return (f"M{P(cx + r, cy)} " + arc(r, 1, sw, cx - r, cy) + " " +
            arc(r, 1, sw, cx + r, cy) + " Z")


def ticket_path():
    d = ticket_outer()
    for y in HOLES_Y:
        d += " " + circle_sub(XP, y, HOLE, ccw=True)   # opposite winding + evenodd
    return d


def in_paper(x, y, m):
    """True if a disk of radius m centred at local (x,y) lies on solid paper."""
    if abs(x) > HW - m or abs(y) > HH - m:
        return False
    cxr, cyr = HW - RC, HH - RC
    if abs(x) > cxr and abs(y) > cyr and math.hypot(abs(x) - cxr, abs(y) - cyr) > RC - m:
        return False
    for sx in (-HW, HW):
        if math.hypot(x - sx, y) < NOTCH + m:
            return False
    for hy in HOLES_Y:
        if math.hypot(x - XP, y - hy) < HOLE + m:
            return False
    return True


def in_frame(x, y, m):
    """True if a disk of radius m at (x,y) is inside the face frame's inner edge."""
    t, b = -HH + BD + BSW / 2, HH - BD - BSW / 2
    xl, xr = BXL + BSW / 2, HW - BD - BSW / 2
    if x - m < xl or x + m > xr or y - m < t or y + m > b:
        return False
    if math.hypot(x - HW, y) < NOTCH + BD + BSW / 2 + m:
        return False
    return True


# ---------------------------------------------------------------- print
def frame_path():
    t, b = -HH + BD, HH - BD
    xr = HW - BD
    rn = NOTCH + BD
    yn = math.sqrt(rn * rn - BD * BD)
    rc = BRC
    return " ".join([
        f"M{P(BXL + rc, t)}",
        f"L{P(xr - rc, t)}",
        arc(rc, 0, 1, xr, t + rc),
        f"L{P(xr, -yn)}",
        arc(rn, 0, 0, xr, yn),
        f"L{P(xr, b - rc)}",
        arc(rc, 0, 1, xr - rc, b),
        f"L{P(BXL + rc, b)}",
        arc(rc, 0, 1, BXL, b - rc),
        f"L{P(BXL, t + rc)}",
        arc(rc, 0, 1, BXL + rc, t),
        "Z",
    ])


def frame_samples():
    t, b = -HH + BD, HH - BD
    xr = HW - BD
    rn = NOTCH + BD
    yn = math.sqrt(rn * rn - BD * BD)
    pts = []
    a0 = math.atan2(-yn, -BD)
    a1 = math.atan2(yn, -BD) - 2 * math.pi
    for i in range(101):
        u = i / 100
        x = BXL + (xr - BXL) * u
        pts += [(x, t), (x, b)]
        y = t + (b - t) * u
        pts.append((BXL, y))
        if abs(y) >= yn:
            pts.append((xr, y))
        a = a0 + (a1 - a0) * u
        pts.append((HW + rn * math.cos(a), rn * math.sin(a)))
    return pts


def sine(x0, x1, y0, amp, periods, phase=0.0, n=200):
    pts = []
    for i in range(n + 1):
        u = i / n
        pts.append((x0 + (x1 - x0) * u, y0 - amp * math.sin(2 * math.pi * periods * u + phase)))
    return pts


def poly_path(pts):
    return "M" + " L".join(P(x, y) for x, y in pts)


def rrect(x, y, w, h, r):
    return " ".join([
        f"M{P(x + r, y)}",
        f"L{P(x + w - r, y)}",
        arc(r, 0, 1, x + w, y + r),
        f"L{P(x + w, y + h - r)}",
        arc(r, 0, 1, x + w - r, y + h),
        f"L{P(x + r, y + h)}",
        arc(r, 0, 1, x, y + h - r),
        f"L{P(x, y + r)}",
        arc(r, 0, 1, x + r, y),
        "Z",
    ])


def pinwheel_paths(cx, cy, size):
    """Brand pinwheel (current/icon-light.svg: 100-unit box, glyph bbox 22..78)."""
    k = size / 56.0
    rects = [(22, 22, 33.6, 18), (60, 22, 18, 33.6), (44.4, 60, 33.6, 18), (22, 44.4, 18, 33.6)]
    return [rrect(cx + (x - 50) * k, cy + (y - 50) * k, w * k, h * k, 2.6 * k) for (x, y, w, h) in rects]


# Face decoration variants: list of (points, stroke_width, palette_key)
def waves_for(v):
    if v == "A":   # twin waves top & bottom, mirrored
        return [
            (sine(-46, 272, -112, 15, 2.5), 42, "wave"),
            (sine(-46, 272, 112, 15, 2.5, math.pi), 42, "wave"),
        ]
    if v == "B":   # braid: two antiphase waves, centred, ends before the notch frame
        return [
            (sine(-50, 196, 0, 40, 2.0), 30, "wave"),
            (sine(-50, 196, 0, 40, 2.0, math.pi), 30, "wave2"),
        ]
    if v == "C":   # three stacked waves filling the face's lower band
        return [
            (sine(-48, 256, 66, 10, 2.6), 24, "wave"),
            (sine(-48, 300, 110, 10, 3.0), 24, "wave"),
            (sine(-48, 300, 154, 10, 3.0), 24, "wave"),
        ]
    raise SystemExit(f"unknown variant {v}")


def check(v):
    errs = []
    for (x, y) in frame_samples():
        if not in_paper(x, y, BSW / 2 + 6):
            errs.append(("frame", round(x, 1), round(y, 1)))
    for (pts, sw, _) in waves_for(v):
        for (x, y) in pts:
            if not in_paper(x, y, sw / 2 + 8):
                errs.append(("wave-paper", round(x, 1), round(y, 1)))
            if not in_frame(x, y, sw / 2 + 10):
                errs.append(("wave-frame", round(x, 1), round(y, 1)))
    for a in range(0, 360, 5):
        x = SEAL_X + SEAL_R * math.cos(math.radians(a))
        y = SEAL_Y + SEAL_R * math.sin(math.radians(a))
        if not in_paper(x, y, 12):
            errs.append(("seal", round(x, 1), round(y, 1)))
    # safe area for the paper silhouette (corner arcs are the extreme points)
    pts = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            ccx, ccy = sx * (HW - RC), sy * (HH - RC)
            for k in range(360):
                pts.append((ccx + RC * math.cos(math.radians(k)), ccy + RC * math.sin(math.radians(k))))
    R = [rot(x, y) for x, y in pts]
    xs, ys = [p[0] for p in R], [p[1] for p in R]
    if min(xs) < 102 or max(xs) > 922 or min(ys) < 102 or max(ys) > 922:
        errs.append(("safe-area", min(xs), max(xs), min(ys), max(ys)))
    print(f"[{v}] paper bbox x {min(xs):.1f}..{max(xs):.1f}  y {min(ys):.1f}..{max(ys):.1f}")
    return errs


# ---------------------------------------------------------------- background
def rosette_paths(rmin=150, rmax=760, step=26, lobes=14, amp=9.0, npts=480):
    """Guilloche rosette: wavy concentric rings with drifting phase."""
    paths = []
    r, i = rmin, 0
    while r <= rmax:
        ph = i * 0.42
        pts = []
        for k in range(npts):
            t = 2 * math.pi * k / npts
            rr = r + amp * math.sin(lobes * t + ph) + 0.5 * amp * math.sin((lobes * 2 + 1) * t - ph)
            pts.append((CX + rr * math.cos(t), CY + rr * math.sin(t)))
        paths.append("M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + " Z")
        r += step
        i += 1
    return paths



# ---------------------------------------------------------------- Icon Composer
import json

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Resources", "AppIcon.icon")
HEAD = '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">'
W = "#FFFFFF"


def svg(body):
    return HEAD + "\n" + body + "\n</svg>\n"


def layer_svgs(v):
    rosette = [f'<g fill="none" stroke="{W}" stroke-width="3">'] + [f'<path d="{d}"/>' for d in rosette_paths()] + ["</g>"]
    prints = [f'<path d="{frame_path()}" fill="none" stroke="{W}" stroke-width="{BSW}" stroke-linejoin="round"/>']
    for (pts, sw, _) in waves_for(v):
        prints.append(f'<path d="{poly_path(pts)}" fill="none" stroke="{W}" stroke-width="{sw}" '
                      f'stroke-linecap="round" stroke-linejoin="round"/>')
    return {
        "rosette.svg": svg("\n".join(rosette)),
        "paper.svg": svg(f'<path d="{ticket_path()}" fill="{W}" fill-rule="evenodd"/>'),
        "print.svg": svg("\n".join(prints)),
        "seal.svg": svg(f'<path d="{circle_sub(SEAL_X, SEAL_Y, SEAL_R, ccw=False)}" fill="{W}"/>'),
        "pinwheel.svg": svg(f'<g fill="{W}">' + "".join(f'<path d="{d}"/>' for d in pinwheel_paths(SEAL_X, SEAL_Y, PIN_S)) + "</g>"),
    }


def srgb(hexstr, a=1.0):
    h = hexstr.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return f"srgb:{r:.4f},{g:.4f},{b:.4f},{a:.1f}"


def fill(light, dark):
    return [{"value": {"solid": srgb(light)}}, {"appearance": "dark", "value": {"solid": srgb(dark)}}]


def layer(name, light, dark, glass, opacity=None):
    d = {"image-name": f"{name}.svg", "name": name, "glass": glass, "fill-specializations": fill(light, dark)}
    if opacity:
        d["opacity-specializations"] = [{"value": opacity[0]}, {"appearance": "dark", "value": opacity[1]}]
    return d


# 2026-11-12 用户看了 Icon Composer 的渲染图，说「紫色的打孔息票比较好看」，所以改成紫色（取自 ictool 默认紫色着色效果）：
# 浅色 = 紫底、白票、浅紫印纹和小章（像「浅色着色」那张）；深色 = 近黑底、紫票、深紫印纹（像「深色着色」那张）。
# 这是 app 图标的单独决定，不影响 app 里的配色令牌。
ICON = {
    "fill-specializations": fill("#6650C4", "#131313"),
    "groups": [
        {   # 票面上的印纹和小章：不做玻璃，保持印刷感
            "layers": [
                layer("pinwheel", "#FFFFFF", "#6143B6", False),
                layer("seal", "#AFA2E8", "#3F2E67", False),
                layer("print", "#8C7BD6", "#2A2140", False),
            ],
        },
        {   # 票纸：Liquid Glass 的一张「纸片」
            "layers": [layer("paper", "#FFFFFF", "#6143B6", True)],
            "shadow": {"kind": "neutral", "opacity": 0.5},
            "translucency": {"enabled": True, "value": 0.0},
        },
        {   # 底上的玫瑰纹底纹：很淡，缩小后只是一层色调
            "layers": [layer("rosette", "#7A66D0", "#FFFFFF", False, opacity=(1.0, 0.06))],
        },
    ],
    "supported-platforms": {"squares": "shared"},
}


def main():
    fail_if(check(VARIANT))
    assets = os.path.join(OUT, "Assets")
    os.makedirs(assets, exist_ok=True)
    for name, text in layer_svgs(VARIANT).items():
        with open(os.path.join(assets, name), "w") as f:
            f.write(text)
    with open(os.path.join(OUT, "icon.json"), "w") as f:
        json.dump(ICON, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("wrote", OUT)
    # app 里的标志（长图页头、小组件）用同一个图标：Icon Composer 的 ictool 渲染成 PNG，浅色、深色各一张
    import shutil, subprocess
    ictool = "/Applications/Xcode.app/Contents/Applications/Icon Composer.app/Contents/Executables/ictool"
    res = os.path.dirname(OUT)
    if not os.path.exists(ictool):
        print("没找到 Icon Composer 的 ictool，标志 PNG 没更新")
        return
    for rendition, name in (("Default", "LogoMark.png"), ("Dark", "LogoMarkDark.png")):
        subprocess.run([ictool, OUT, "--export-image", "--output-file", os.path.join(res, name), "--platform", "iOS",
                        "--rendition", rendition, "--width", "192", "--height", "192", "--scale", "1"],
                       check=True, capture_output=True)
    # ictool 出的是 16 位 PNG，Xcode 会警告；有项目的 .venv（带 Pillow）就转成 8 位
    venv_py = os.path.join(os.path.dirname(os.path.dirname(res)), ".venv", "bin", "python")
    if os.path.exists(venv_py):
        subprocess.run([venv_py, "-I", "-c",
                        "import sys\nfrom PIL import Image\nfor p in sys.argv[1:]: Image.open(p).convert('RGBA').save(p)",
                        os.path.join(res, "LogoMark.png"), os.path.join(res, "LogoMarkDark.png")], check=True)
    print("wrote LogoMark.png, LogoMarkDark.png")


def fail_if(errs):
    if errs:
        print("CONTAINMENT ERRORS (first 20):")
        for e in errs[:20]:
            print("  ", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
