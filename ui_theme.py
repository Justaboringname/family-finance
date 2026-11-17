"""界面样式：「私人账簿」主题的令牌、页头、数字格、资产构成、持仓表、通用表格。

浅色 = 暖纸 + 墨色，深色 = 暖炭灰；内容区不用卡片和阴影，靠细线、字号和字重分层。
资产类别的分类色已用 dataviz 的 validate_palette.js 验证（相邻两色在浅/深两种底色下色盲可分辨、对比度 ≥ 3:1），
顺序按类别固定：AAPL 股票计划、EB-5、基金、Schwab 股票、现金。
"""
from __future__ import annotations

import html
import math
import random
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st

from core.money import ZERO, D, fmt_cny, fmt_pct, fmt_price, fmt_qty, fmt_usd, shares_bp
from ui_html import _PAIR, disp, money_html, pair, sign_class, table_html

FONT = ('-apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro", "PingFang SC", "Hiragino Sans GB", "Segoe UI", '
        '"Microsoft YaHei", sans-serif')
FONT_DISPLAY = ('-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro", "PingFang SC", "Hiragino Sans GB", '
                '"Microsoft YaHei", sans-serif')
FONT_MONO = 'ui-monospace, "SF Mono", Menlo, monospace'
# 标题用衬线（私人银行的气质）：中文宋体，西文 New York；数字一律用上面的无衬线等宽数字
FONT_SERIF = '"Songti SC", "STSong", "New York", ui-serif, Georgia, serif'
# 凭证上的面额数字（净资产、今日盈亏、息票票面）用 New York 衬线，像钞票上的凹版数字
FONT_NUM = 'ui-serif, "New York", "Songti SC", Georgia, serif'

# 资产类别 → 分类色槽位（固定，不按大小排）
# 分类色槽位（--ff-s1…s5 = 墨蓝、赭黄、青绿、赤陶、梅紫）。每种分组的顺序都用 validate_palette.js 验证过（浅 / 深色）
from core.wealth import CLASS_SLOTS  # noqa: E402  资产类别 → 分类色槽位（手机 app 共用）
MEMBER_SLOTS_ORDER = [1, 2, 3, 4, 5]                                           # 我 = 墨蓝，爸爸 = 赭黄，其余依次
ACCOUNT_SLOTS_ORDER = [1, 2, 3, 4, 5]

LIGHT = {
    "bg": "#f2f2f2", "sidebar": "#eaeaea", "surface": "#f9f9f9", "sunken": "#e3e3e3",   # 背景用中性灰（10-17 起，原暖纸色）
    "text": "#1c1a16", "text2": "#4d4940", "muted": "#6b665c",
    "rule": "#dcd6c9", "rule_soft": "#e6e1d5", "rule_strong": "#1c1a16",
    "accent": "#234b7c", "on_accent": "#fbf9f4", "accent_soft": "rgba(35,75,124,0.09)", "focus": "rgba(35,75,124,0.28)",
    "hover": "rgba(28,26,22,0.03)", "shadow": "0 1px 0 rgba(28,26,22,0.03), 0 1px 3px rgba(28,26,22,0.08)",
    "red": "#b3261e", "green": "#17703f", "red_soft": "rgba(179,38,30,0.09)", "green_soft": "rgba(23,112,63,0.09)",
    "series": ["#2e5794", "#ad7a17", "#129390", "#bf623d", "#884783"],
    # 钞版装饰（只是透明度和派生色，不新增色相）：框线、波纹带、玫瑰纹、底纹、安全纤维；
    # field = 整页车花网，note_field = 凭证和息票里的车花网，sunburst = 玫瑰纹外圈的放射线，wmark = 整页玫瑰水印，pfib = 整页纤维
    "frame_line": "rgba(35,75,124,0.5)", "bd_op": "0.62", "engr": "0.2", "under": "0.06", "fib_op": "0.45",
    "fib_glow": "none", "fibers": "var(--ff-fibers-light)", "coupon_bg": "#f8f8f8", "perf": "rgba(28,26,22,0.42)",
    "field": "0.11", "note_field": "0.1", "sunburst": "0.075", "wmark": "0.11", "pfib_op": "0.25",
    "pfibers": "var(--ff-pfibers-light)",
}
DARK = {
    "bg": "#1d1d1d", "sidebar": "#171717", "surface": "#252525", "sunken": "#131313",   # 背景用中性灰（原暖炭灰）
    "text": "#ede9e0", "text2": "#bfb9ac", "muted": "#958f83",
    "rule": "#36332d", "rule_soft": "#2c2a26", "rule_strong": "#a29c90",
    "accent": "#8fb1e0", "on_accent": "#1e1d1a", "accent_soft": "rgba(143,177,224,0.12)", "focus": "rgba(143,177,224,0.34)",
    "hover": "rgba(237,233,224,0.035)", "shadow": "0 1px 0 rgba(0,0,0,0.25), 0 1px 3px rgba(0,0,0,0.35)",
    "red": "#f0786c", "green": "#5cbf86", "red_soft": "rgba(240,120,108,0.13)", "green_soft": "rgba(92,191,134,0.13)",
    "series": ["#5581c1", "#b48226", "#129390", "#c66843", "#9b5896"],
    # 深色 = 紫外灯下的钞票：雕版线更亮，纸里的安全纤维发荧光
    "frame_line": "rgba(143,177,224,0.45)", "bd_op": "0.72", "engr": "0.28", "under": "0.07", "fib_op": "0.48",
    "fib_glow": "drop-shadow(0 0 2px rgba(88,222,204,0.5))", "fibers": "var(--ff-fibers-dark)", "coupon_bg": "#232323",
    "perf": "rgba(237,233,224,0.4)",
    "field": "0.14", "note_field": "0.12", "sunburst": "0.08", "wmark": "0.15", "pfib_op": "0.27",
    "pfibers": "var(--ff-pfibers-dark)",
}


def _pnl_scheme() -> str:
    return "green_up"              # 绿涨红跌（2026-10-17 起固定，不再提供切换）


# ---------- 全局样式 ----------

def _vars(t: dict) -> str:
    out = [f"--ff-{k.replace('_', '-')}: {v};" for k, v in t.items() if k != "series"]
    out += [f"--ff-s{i + 1}: {c};" for i, c in enumerate(t["series"])]
    return " ".join(out)


# 跟随 Streamlit 实际使用的主题：读 .stApp 背景色的明暗，标在 <html data-ff>。
# 系统外观在页面打开期间切换时，颜色立刻跟着变，不必等下一次重新运行。
_THEME_JS = """<script>
(function () {
  if (window.__ffThemeWatch) return;
  window.__ffThemeWatch = true;
  function apply() {
    var app = document.querySelector('.stApp');
    if (!app) return;
    var m = getComputedStyle(app).backgroundColor.match(/[0-9.]+/g);
    if (!m || m.length < 3) return;
    var lum = (0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]) / 255;
    var mode = lum < 0.5 ? 'dark' : 'light';
    if (document.documentElement.getAttribute('data-ff') !== mode) {
      document.documentElement.setAttribute('data-ff', mode);
    }
  }
  apply();
  setInterval(apply, 500);
})();
</script>"""

_CSS_FILE = Path(__file__).resolve().parent / "assets" / "theme.css"


# ---------- 雕版纹样（钞票、股票凭证上的 guilloché） ----------
# 纹样只当遮罩用（CSS mask），颜色由 theme.css 用 --ff-accent 填，所以深浅色切换时跟着变；不新增任何颜色。
# 图形用代码算出来，不放图片文件：几圈「绕圆起伏的正弦线」，每圈几条错开相位，交织成网纹。

def _poly(points: list[tuple[float, float]]) -> str:
    """闭合折线 → SVG 路径。坐标先取整再求相对增量，误差不累积，路径也短。"""
    pts = [(round(x), round(y)) for x, y in points]
    out, (px, py) = [f"M{pts[0][0]},{pts[0][1]}"], pts[0]
    for x, y in pts[1:]:
        if (x, y) != (px, py):
            out.append(f"l{x - px},{y - py}")
        px, py = x, y
    return "".join(out) + "z"


def _ring(radius: float, amp: float, waves: int, strands: int, amp2: float = 0.0, steps: int = 9) -> str:
    """一圈网纹：strands 条线，半径按 waves 个周期起伏，相位均匀错开。画布 1000×1000，圆心在中间。"""
    n, out = waves * steps, []
    for c in range(strands):
        ph = 2 * math.pi * c / strands
        pts = []
        for s in range(n):
            t = 2 * math.pi * s / n
            r = radius + amp * math.sin(waves * t + ph) + amp2 * math.sin(2 * waves * t - ph)
            pts.append((500 + r * math.cos(t), 500 + r * math.sin(t)))
        out.append(_poly(pts))
    return "".join(out)


def _svg_url(svg: str) -> str:
    return 'url("data:image/svg+xml,' + quote(svg, safe=" ,=:/'-.") + '")'


def _waves(length: int, height: int, strands: int, amp: float, vertical: bool = False, step: float = 0.5) -> str:
    """一个周期的几条错相正弦线（波纹带的一格）。vertical=True 时沿竖直方向走。"""
    out = []
    for c in range(strands):
        pts = []
        for k in range(int(length / step) + 1):
            t = k * step
            w = height / 2 + amp * math.sin(2 * math.pi * t / length + 2 * math.pi * c / strands)
            pts.append(f"{w:.2f},{t:g}" if vertical else f"{t:g},{w:.2f}")
        out.append("M" + "L".join(pts))
    return "".join(out)


def _svg(view: str, body: str, stretch: bool = False) -> str:
    par = " preserveAspectRatio='none'" if stretch else ""
    return f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='{view}'{par} fill='none'>{body}</svg>"


def _open(points: list[tuple[float, float]]) -> str:
    """开放折线 → SVG 路径（坐标取整后求相对增量）。"""
    pts = [(round(x), round(y)) for x, y in points]
    out, (px, py) = [f"M{pts[0][0]},{pts[0][1]}"], pts[0]
    for x, y in pts[1:]:
        out.append(f"l{x - px},{y - py}")
        px, py = x, y
    return "".join(out)


def _field(w: int = 120, sp: float = 7, rows: int = 26) -> str:
    """满版「车花」网的一格（可上下左右平铺）：一行行正弦线，振幅随行号缓慢涨落，两组反向错相交叉成网，像钞票的底纹。

    横向一个周期 = 格宽；行号每过 rows 行，振幅和相位都回到原样，所以格高 = sp × rows。
    格子上下各多画 3 行，伸进相邻格子的部分由这一格自己补上，平铺时没有接缝。坐标放大 10 倍后取整。
    """
    h, k10 = sp * rows, 10
    d = []
    for fam in (1, -1):
        for k in range(-3, rows + 3):
            amp = sp * (1.15 + 0.85 * math.sin(2 * math.pi * k / rows))
            ph = fam * 2 * math.pi * k / rows
            pts = [(x * k10, (k * sp + amp * math.sin(2 * math.pi * x / w + ph)) * k10) for x in range(-3, w + 4, 3)]
            d.append(_open(pts))
    return _svg(f"0 0 {w * k10} {round(h * k10)}", f"<path stroke='black' stroke-width='5.5' d='{''.join(d)}'/>")


def _sunburst(n: int = 220) -> str:
    """玫瑰纹外圈的放射细线。画布 4000×4000（玫瑰纹的 4 倍大，圆心对齐），线从玫瑰纹外缘一点开始，长短交替。"""
    d = []
    for i in range(n):
        a = 2 * math.pi * i / n
        r0 = 520 if i % 2 == 0 else 560
        d.append(f"M{2000 + r0 * math.cos(a):.0f},{2000 + r0 * math.sin(a):.0f}"
                 f"L{2000 + 2900 * math.cos(a):.0f},{2000 + 2900 * math.sin(a):.0f}")
    return _svg("0 0 4000 4000", f"<path stroke='black' stroke-width='2.4' d='{''.join(d)}'/>")


def _fibers(colors: tuple[str, str, str], n: int = 72, w: int = 520, h: int = 320) -> str:
    """钞纸里的安全纤维：随机散落的短弧线，三种颜色轮流；用固定种子，每次生成都一样。"""
    rnd = random.Random(20261111)
    paths = {c: [] for c in colors}
    for i in range(n):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        a = rnd.uniform(0, 2 * math.pi)
        ln = rnd.uniform(6, 14)
        x2, y2 = x + ln * math.cos(a), y + ln * math.sin(a)
        bend = rnd.uniform(-4, 4)
        cx, cy = (x + x2) / 2 - bend * math.sin(a), (y + y2) / 2 + bend * math.cos(a)
        paths[colors[i % 3]].append(f"M{x:.1f},{y:.1f}Q{cx:.1f},{cy:.1f} {x2:.1f},{y2:.1f}")
    body = "".join(f"<path stroke='{c}' d='{''.join(d)}'/>" for c, d in paths.items())
    return _svg(f"0 0 {w} {h}", f"<g stroke-width='0.9' stroke-linecap='round'>{body}</g>")


@lru_cache(maxsize=1)
def _ornament_css() -> str:
    rosette = (_ring(440, 42, 36, 6, 10) + _ring(352, 26, 36, 4) + _ring(268, 34, 24, 5, 8) + _ring(178, 24, 18, 4)
               + _ring(104, 30, 10, 3, 6) + _ring(46, 14, 6, 3))
    rosette_svg = _svg("0 0 1000 1000", f"<path stroke='black' stroke-width='1.7' d='{rosette}'/>")
    # 波纹带的一格（横、竖各一份），24 宽 12 高，三条错相正弦线
    wave_h = _svg("0 0 24 12", f"<path stroke='black' stroke-width='0.75' d='{_waves(24, 12, 3, 4.6)}'/>", stretch=True)
    wave_v = _svg("0 0 12 24", f"<path stroke='black' stroke-width='0.75' d='{_waves(24, 12, 3, 4.6, vertical=True)}'/>",
                  stretch=True)
    # 四角纹章：一圈外环 + 两圈小网纹
    medal = _svg("0 0 1000 1000", "<circle cx='500' cy='500' r='466' stroke='black' stroke-width='30'/>"
                 f"<path stroke='black' stroke-width='22' d='{_ring(318, 74, 10, 3) + _ring(140, 44, 6, 2)}'/>")
    # 框内的青色底纹：一格 96×18，两条交叉的长波
    under = _svg("0 0 96 18", f"<path stroke='black' stroke-width='0.6' d='{_waves(96, 18, 2, 6, step=2)}'/>", stretch=True)
    light_c = ("rgba(191,98,61,0.6)", "rgba(46,87,148,0.55)", "rgba(18,147,144,0.55)")
    dark_c = ("rgba(88,222,204,0.9)", "rgba(160,192,240,0.85)", "rgba(232,196,112,0.8)")
    # 整页的纤维比凭证里稀得多：一格 900×700 只有 36 根
    return (f":root {{ --ff-rosette: {_svg_url(rosette_svg)}; --ff-wave-h: {_svg_url(wave_h)}; "
            f"--ff-wave-v: {_svg_url(wave_v)}; --ff-medal: {_svg_url(medal)}; --ff-underprint: {_svg_url(under)}; "
            f"--ff-field-tile: {_svg_url(_field())}; --ff-sunburst: {_svg_url(_sunburst())}; "
            f"--ff-fibers-light: {_svg_url(_fibers(light_c))}; --ff-fibers-dark: {_svg_url(_fibers(dark_c))}; "
            f"--ff-pfibers-light: {_svg_url(_fibers(light_c, 36, 900, 700))}; "
            f"--ff-pfibers-dark: {_svg_url(_fibers(dark_c, 36, 900, 700))}; }}")


def inject_css() -> None:
    up, down = ("red", "green") if _pnl_scheme() == "red_up" else ("green", "red")
    fonts = (f"--ff-font: {FONT}; --ff-font-display: {FONT_DISPLAY}; --ff-font-mono: {FONT_MONO}; "
             f"--ff-font-serif: {FONT_SERIF}; --ff-font-num: {FONT_NUM};")
    tokens_css = f"""
{_ornament_css()}
:root {{ {_vars(LIGHT)} {fonts} color-scheme: light; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-ff="light"]) {{ {_vars(DARK)} color-scheme: dark; }} }}
:root[data-ff="dark"] {{ {_vars(DARK)} color-scheme: dark; }}
@media print {{ :root, :root[data-ff="dark"], :root:not([data-ff="light"]) {{ {_vars(LIGHT)} color-scheme: light; }} }}
:root {{ --ff-up: var(--ff-{up}); --ff-up-soft: var(--ff-{up}-soft);
        --ff-down: var(--ff-{down}); --ff-down-soft: var(--ff-{down}-soft); }}
"""
    st.html(f"<style>{tokens_css}\n{_CSS_FILE.read_text(encoding='utf-8')}</style>")
    st.html(_THEME_JS, unsafe_allow_javascript=True)


def _e(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def chip(text: str, v=None) -> str:
    return f'<span class="ff-chip {sign_class(text) or "ff-flat"}">{_e(disp(text))}</span>'


# ---------- 页头、区块标题、注、页脚 ----------

def page_header(title: str, sub: str | None = None, eyebrow: str | None = None,
                meta: list[tuple[str, str]] | None = None) -> None:
    """页头：大标题 + 副标题，右侧一组「键 / 值」；下方一粗一细两条墨线。区块编号从这里重新开始。"""
    st.session_state["_ff_sec_no"] = 0
    dl = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in (meta or []))
    st.html(f"""<div class="ff-blk"><header class="ff-mast"><div>
{f'<div class="eyebrow">{_e(eyebrow)}</div>' if eyebrow else ''}<h1>{_e(title)}</h1>
{f'<div class="sub">{_e(sub)}</div>' if sub else ''}</div>{f'<dl>{dl}</dl>' if dl else ''}</header></div>""")


def section(title: str, hint: str | None = None, numbered: bool = True) -> None:
    """区块标题：两位编号 + 标题 + 右侧说明，下方一条墨线。"""
    no = ""
    if numbered:
        n = st.session_state.get("_ff_sec_no", 0) + 1
        st.session_state["_ff_sec_no"] = n
        no = f'<span class="no">{n:02d}</span>'
    st.html(f'<div class="ff-blk"><header class="ff-sec">{no}<h2>{_e(title)}</h2>'
            f'{f"<p class=hint>{_e(hint)}</p>" if hint else ""}</header></div>')


def reset_sections() -> None:
    """在每个 st.tabs 标签页开头调用：标签页里的区块从 01 重新编号。"""
    st.session_state["_ff_sec_no"] = 0


def note(text: str, mark: str = "注") -> None:
    st.html(f'<p class="ff-note"><span class="mk">{_e(mark)}</span><span>{_e(text)}</span></p>')


def footer_html(disclaimer: str) -> None:
    st.html(f'<div class="ff-blk"><footer class="ff-foot"><span>{_e(disclaimer)}</span>'
            f'<span>数据只保存在本机 <code>./data/</code></span></footer></div>')


# ---------- 数字格（代替卡片） ----------

def money_card(col, label: str, usd, fx, sign: bool = False, note: str | None = None, pct=None,
               colored: bool = False, qual: str | None = None) -> None:
    """一个数字格：上方一条细线，标签、美元（大字）、人民币、可选涨跌幅和备注。col 可以是列，也可以是 st 本身。"""
    u = fmt_usd(usd, sign)
    cls = sign_class(u) if colored else ""
    cny = f'<div class="c {cls}">{_e(disp(fmt_cny(D(usd) * D(fx), sign)))}</div>' if fx is not None else ""
    p = chip(fmt_pct(pct, sign=True)) if pct is not None else ""
    q = f'<span class="qual">{_e(qual)}</span>' if qual else ""
    col.html(f'<div class="ff-fig"><div class="k">{_e(label)}{q}</div><div class="v {cls}">{_e(disp(u))}</div>{cny}{p}'
             f'{f"<div class=n>{_e(note)}</div>" if note else ""}</div>')


def text_card(col, label: str, text: str, note: str | None = None, color: str | None = None) -> None:
    """文字数字格；text 是 "美元 / 人民币" 时拆成两行。"""
    m = _PAIR.match(str(text))
    if m:
        cls = sign_class(m.group(1))
        body = f'<div class="v {cls}">{_e(disp(m.group(1)))}</div><div class="c {cls}">{_e(disp(m.group(2)))}</div>'
    else:
        style = f' style="color:{_e(color)}"' if color else ""
        body = f'<div class="v"{style}>{_e(text)}</div>'
    col.html(f'<div class="ff-fig"><div class="k">{_e(label)}</div>{body}'
             f'{f"<div class=n>{_e(note)}</div>" if note else ""}</div>')


# ---------- 资产总览：首屏 ----------

_REEL = "\n".join("0123456789" * 2 + "012")   # 滚轮上的数字：转两圈多一点，弹簧冲过头时下面还有字


def _odo(text: str, start: int = 0) -> tuple[str, int]:
    """数字滚轮：每个数字一格，页面打开时像点钞机一样从 0 滚到位，左边的先停。

    滚轮的静止位置就是最终数字，不支持动画（或系统开了「减少动态效果」）时直接显示结果。
    返回 (HTML, 下一格的序号)；序号决定每一格晚多久开始。
    """
    out, i = [], start
    for ch in text:
        if ch.isdigit():
            out.append(f'<span class="dg" style="--d:{ch};--i:{i}"><span class="ph">{ch}</span>'
                       f'<span class="rl">{_REEL}</span></span>')
            i += 1
        else:
            out.append(_e(ch))
    return "".join(out), i


def _odo_figure(s: str, start: int = 0) -> str:
    """整串金额做成滚轮；读屏软件读隐藏的那份原文。"""
    reel, _ = _odo(disp(s), start)
    return f'<span class="ff-sr">{_e(disp(s))}</span><span class="odo" aria-hidden="true">{reel}</span>'


def _big_usd(v) -> str:
    """$1,234,567.89 → 小号 $、正常整数、小号小数；数字是滚轮。"""
    s = fmt_usd(v)
    neg = s.startswith("-")
    whole, dec = s.lstrip("-").lstrip("$").split(".")
    w_html, n = _odo(whole)
    d_html, _ = _odo(dec, n)
    return (f'<span class="ff-sr">{_e(disp(s))}</span><span class="odo" aria-hidden="true">'
            f'<span class="cur">{"−" if neg else ""}$</span>{w_html}<span class="dec">.{d_html}</span></span>')


_MICRO = "家庭资产与税务　仅在本机运行　" * 40      # 凭证上的微缩文字线（装饰，读屏跳过）
_FRAME_LINES = ('<i class="ln" aria-hidden="true"></i><i class="ln i" aria-hidden="true"></i>'
                + "".join(f'<i class="bd {s}" aria-hidden="true"></i>' for s in "tblr")
                + "".join(f'<i class="md {s}" aria-hidden="true"></i>' for s in ("tl", "tr", "bl", "br")))
_COUPON_FRAME = _FRAME_LINES
_CERT_FRAME = ('<i class="ln" aria-hidden="true"></i><i class="ln i" aria-hidden="true"></i>'
               + "".join(f'<i class="bd {s}" aria-hidden="true"></i>' for s in "tblr")
               + "".join(f'<i class="md {s}" aria-hidden="true"></i>' for s in ("tl", "tr", "bl", "br")))


def overview_hero(label: str, total, day_pnl, day_pct, contrib: list[tuple[str, Decimal]], fx,
                  strip: list[dict], issued: str = "") -> None:
    """首屏凭证：雕版边框里，左边面额（家族净资产），右边今日盈亏和按类别的来源；微缩文字线下面是一条数字带。

    strip = [{label, qual?, usd (None = 缺行情), sign?, colored?, pct?, note?}]；issued 写在凭证右上角，如「2026 年 11 月 11 日收盘」。
    """
    d = fmt_usd(day_pnl, True)
    dcls = sign_class(d)
    cny_total = _e(disp(fmt_cny(D(total) * D(fx)))) if fx is not None else ""
    day_cny = f'<span class="{dcls}">{_e(disp(fmt_cny(D(day_pnl) * D(fx), True)))}</span>' if fx is not None else ""
    rows = "".join(
        f'<dt>{_e(n)}</dt><dd class="{sign_class(fmt_usd(v, True))}">{_e(disp(fmt_usd(v, True)))}</dd>'
        f'<dd class="cny {sign_class(fmt_usd(v, True))}">'
        f'{_e(disp(fmt_cny(D(v) * D(fx), True))) if fx is not None else ""}</dd>'
        for n, v in contrib)
    cells = []
    for c in strip:
        if c["usd"] is None:                       # 没有行情时不显示一个看起来精确的 0
            u, cls, cny, pct = "—", "", "缺行情", ""
        else:
            u = fmt_usd(c["usd"], c.get("sign", False))
            cls = sign_class(u) if c.get("colored") else ""
            cny = disp(fmt_cny(D(c["usd"]) * D(fx), c.get("sign", False))) if fx is not None else ""
            pct = f' · {disp(fmt_pct(c["pct"], sign=True))}' if c.get("pct") is not None else ""
        q = f'<span class="qual">{_e(c["qual"])}</span>' if c.get("qual") else ""
        n = f'<div class="n">{_e(c["note"])}</div>' if c.get("note") else ""
        cells.append(f'<div><div class="k">{_e(c["label"])}{q}</div><div class="v {cls}">{_e(disp(u))}</div>'
                     f'<div class="c {cls}">{_e(cny + pct)}</div>{n}</div>')
    day_chip = chip(fmt_pct(day_pct, sign=True)) if day_pct is not None else ""
    rate = f"人民币，按当日汇率 {D(fx):.4f} 折算" if fx is not None else ""
    st.html(f"""<div class="ff-blk"><section class="ff-cert ff-fr" aria-label="{_e(label)}">{_CERT_FRAME}
<i class="clip" aria-hidden="true"><i class="fld"></i><i class="under"></i><i class="sun"></i><i class="ros"></i><i class="ros2"></i><i class="fib"></i></i>
<div class="cert-top"><div class="lbl">{_e(label)}</div><div class="iss">{_e(issued)}</div></div>
<div class="cert-grid"><div class="main"><div class="fig">{_big_usd(total)}</div>
<div class="fig-cny"><span>{cny_total}</span><small>{_e(rate)}</small></div></div>
<div class="day"><div class="k">今日盈亏 <span class="qual">较前一交易日收盘</span></div>
<div class="day-fig {dcls}">{_odo_figure(d, 3)}</div>
<div class="day-sub">{day_cny}{day_chip}</div>
{f'<dl class="contrib">{rows}</dl>' if rows else ''}</div></div>
<div class="micro" aria-hidden="true">{_MICRO}</div>
<div class="ff-strip" style="--cols:{len(strip)}">{''.join(cells)}</div></section></div>""")


# ---------- 构成（按资产类别 / 家庭成员 / 账户） ----------

_shares = shares_bp


def allocation(parts: list[dict], fx, slots: dict[str, int], footers: list[dict], head: str = "类别",
               show_pnl: bool = True) -> None:
    """构成：一条 100% 横条（色段上方标注占比和名称）+ 一张账簿表（美元、人民币、占比、今日盈亏）。

    parts = [{name, usd, day_pnl (None = 不计盈亏), desc}]，按给定顺序画；slots 把名称映射到分类色槽位（颜色跟着实体走）。
    footers = [{label, usd, share (文字), day_pnl (None = —)}]，画在表尾（例如 总资产 / 负债 / 净资产）。
    占比按正数项的合计算；负数项占比显示 —。
    """
    bp = _shares([p["usd"] for p in parts])
    if not any(bp):
        return
    labels, segs, rows = [], [], []
    for p, b in zip(parts, bp):
        color = f"var(--ff-s{slots.get(p['name'], 5)})"
        pct = f"{b / 100:.2f}%"
        if b >= 50:                                 # ≥ 0.5% 才画色段；标签格和色段等宽，放不下就自动隐藏
            cny = f" / {fmt_cny(D(p['usd']) * D(fx))}" if fx is not None else ""
            tip = f"{p['name']} · {fmt_usd(p['usd'])}{cny} · {pct}"
            segs.append(f'<span class="s" style="flex:{b} 1 0;background:{color}" data-tip="{_e(tip)}"></span>')
            labels.append(f'<span class="lab" style="flex:{b} 1 0"><span class="al"><b>{pct}</b>'
                          f'<span class="nm">{_e(p["name"])}</span></span></span>')
        share_txt = "—" if p["usd"] < 0 else ("<0.01%" if b == 0 and p["usd"] > 0 else pct)
        pnl = pair(p["day_pnl"], fx, True, True) if p.get("day_pnl") is not None else '<span class="muted">—</span>'
        cny = _e(disp(fmt_cny(D(p["usd"]) * D(fx)))) if fx is not None else ""
        rows.append(f'<tr><td><div class="cls"><span class="sw" style="background:{color}"></span>'
                    f'<span class="nm">{_e(p["name"])}</span><span class="ds">{_e(p.get("desc") or "")}</span></div></td>'
                    f'<td class="c-usd">{_e(disp(fmt_usd(p["usd"])))}</td><td class="c-cny muted">{cny}</td>'
                    f'<td class="c-pct">{_e(share_txt)}</td>' + (f'<td class="c-pnl">{pnl}</td>' if show_pnl else '') + '</tr>')
    foot = []
    for f in footers:
        cny = _e(disp(fmt_cny(D(f["usd"]) * D(fx)))) if fx is not None else ""
        pnl = pair(f["day_pnl"], fx, True, True) if f.get("day_pnl") is not None else '<span class="muted">—</span>'
        foot.append(f'<tr><td class="lbl">{_e(f["label"])}</td><td class="c-usd">{_e(disp(fmt_usd(f["usd"])))}</td>'
                    f'<td class="c-cny">{cny}</td><td class="c-pct">{_e(f.get("share") or "—")}</td>'
                    + (f'<td class="c-pnl">{pnl}</td>' if show_pnl else '') + '</tr>')
    st.html(f"""<div class="ff-blk"><figure class="ff-alloc" style="margin-left:0;margin-right:0;margin-bottom:0">
<div class="labels" aria-hidden="true">{''.join(labels)}</div><div class="bar">{''.join(segs)}</div></figure>
<table class="ff-ledger-t"><thead><tr><th>{_e(head)}</th><th>美元</th><th>人民币</th><th>占比</th>{"<th>今日盈亏</th>" if show_pnl else ""}</tr></thead>
<tbody>{''.join(rows)}</tbody><tfoot>{''.join(foot)}</tfoot></table></div>""")


# ---------- 流动性阶梯 ----------

from core.wealth import LIQUIDITY_FILL as _STAIR_FILL  # noqa: E402


def liquidity_ladder(buckets: list[dict], total, fx, timing: dict[str, str]) -> None:
    """流动性台阶：越往右越难拿到钱。每一级一根柱子，柱底到柱顶 = 这一级之前 / 之后的累计占比（淡色是之前的累计），
    填充方式表示性质：实色 = 随时能用或 T+1，点 = 在途，斜线 = 受限，网格 = 锁定。柱子下面写金额、占比和明细。
    窄屏时每一级改成一行，柱子横过来。"""
    out, cum = [], ZERO
    for b in buckets:
        share = b["usd"] / total if total else ZERO
        c0, cum = cum, cum + share
        merged: dict[tuple, dict] = {}                 # 同一成员、同一账户、同一时间的合并成一行（例如 Schwab 的几只股票）
        for i in b["rows"]:
            g = merged.setdefault((i.member, i.account, i.unlock, i.note), {"names": [], "usd": ZERO, "i": i})
            g["names"].append(i.name)
            g["usd"] += i.usd
        lines = []
        for g in merged.values():
            i, names = g["i"], g["names"]
            nm = "、".join(names[:4]) + (f" 等 {len(names)} 只" if len(names) > 4 else "")
            when = f"，{i.unlock}" if i.unlock else ""
            lines.append(f'<li><span class="nm">{_e(nm)}</span><span class="amt">{_e(disp(fmt_usd(g["usd"])))}</span>'
                         f'<span class="who">{_e(i.member)}，{_e(i.account)}{_e(when)}</span></li>')
        fill = _STAIR_FILL.get(b["bucket"], "solid")
        cny = f'<span class="cn">{_e(disp(fmt_cny(D(b["usd"]) * D(fx))))}</span>' if fx is not None else ""
        out.append(
            f'<div class="st" style="--c0:{float(c0) * 100:.3f};--c1:{float(cum) * 100:.3f}">'
            f'<div class="col" aria-hidden="true"><i class="prev"></i><i class="cur {fill}"></i></div>'
            f'<div class="cap"><span class="b">{_e(b["bucket"])}</span><span class="t">{_e(timing.get(b["bucket"], ""))}</span>'
            f'<span class="amt">{_e(disp(fmt_usd(b["usd"])))}</span>{cny}'
            f'<span class="pc">占 {_e(fmt_pct(share))}<i>累计 {_e(fmt_pct(cum))}</i></span><ul>{"".join(lines)}</ul></div></div>')
    st.html(f'<div class="ff-blk"><div class="ff-stairs" style="--n:{max(len(out), 1)}">{"".join(out)}</div></div>')


# ---------- 集中度 ----------

def concentration(rows_: list[dict], fx, threshold) -> None:
    out = []
    for r in rows_:
        flag = (f'<span class="flag">单一股票超过总资产 {_e(fmt_pct(threshold, 0))}</span>' if r["flag"] else "")
        out.append(f'<tr><td><span class="nm">{_e(r["name"])}</span>{flag}'
                   f'<span class="who">{_e("、".join(sorted(r["members"])))}</span></td>'
                   f'<td class="c-usd">{pair(r["usd"], fx)}</td>'
                   f'<td class="c-pct"><span class="pbar"><i style="width:{float(r["share"]) * 100:.2f}%"></i></span>'
                   f'{_e(fmt_pct(r["share"]))}</td></tr>')
    st.html(f'<div class="ff-blk"><table class="ff-conc"><thead><tr><th>持仓</th><th>市值</th><th>占总资产</th></tr></thead>'
            f'<tbody>{"".join(out)}</tbody></table></div>')


# ---------- 净资产走势 ----------

def nw_trend(series: list[tuple[str, Decimal]], fx, height: int = 160) -> None:
    """净资产走势：一条线 + 线下雕版式的竖向排线，取整的横向网格（左边标金额）和日期，一条虚线标出起点的水平；
    首尾直接标注，最新一天的点常亮。
    悬停任意一天：竖向准线、圆点和一张小标签（日期、美元 / 人民币）。少于 2 个点时不画。

    st.html 会把 <svg> 整个过滤掉，所以这张图走 st.markdown（不过滤 SVG）。金额里的 $ 写成 &#36;，
    免得被 Markdown 当成公式、也不会被 ui_common 的 $ 转义改掉。线和面积用拉伸的 SVG 画；
    每一天是一条全高的透明竖条（悬停区域），圆点和标签用 HTML 按百分比定位，宽窄屏都是圆的。
    """
    if len(series) < 2:
        return
    vals = [float(v) for _, v in series]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or abs(hi) * 0.01 or 1
    lo, hi = lo - pad, hi + pad
    n = len(series)
    xs = [i * 100 / (n - 1) for i in range(n)]
    ys = [(1 - (v - lo) / (hi - lo)) * 100 for v in vals]

    def label(d, v) -> str:
        cny = f" / {fmt_cny(D(v) * D(fx))}" if fx is not None else ""
        return _e(f"{d} · {fmt_usd(v)}{cny}").replace("$", "&#36;")

    path = " ".join(f"{'M' if i == 0 else 'L'}{x * 10:.1f},{y:.2f}" for i, (x, y) in enumerate(zip(xs, ys)))
    area = f"{path} L1000,100 L0,100 Z"
    # 面积是雕版式的竖向细排线：一层排线背景，用「线下面积」的形状当遮罩（遮罩是 data URI，写在行内样式里，所以外层用单引号）
    area_svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1000 100' preserveAspectRatio='none'>"
                f"<path fill='black' d='{area}'/></svg>")
    area_url = "url('data:image/svg+xml," + quote(area_svg, safe=" ,=:/.-") + "')"
    # 纵轴：取整的几条横线（约 4 条），金额大时按「万」标
    span = hi - lo
    raw = span / 4 if span > 0 else 1
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    wan = hi >= 1e5
    grid, t = [], math.ceil(lo / step) * step
    while t <= hi:
        gy = (1 - (t - lo) / (hi - lo)) * 100
        txt_ = (f"{t / 1e4:,.{0 if step >= 1e4 else 1}f} 万" if wan else f"{t:,.0f}")
        grid.append(f'<i class="gl" style="top:{gy:.3f}%"><span>&#36;{txt_}</span></i>')
        t += step
    # 横轴：日期，最多 7 个；最后一个总是最新一天，离它太近的那个省掉；首尾两个贴边对齐，不出画框
    k = max(1, math.ceil(n / 7))
    idx = list(range(0, n, k))
    if len(idx) > 1 and n - 1 - idx[-1] < k:
        idx.pop()
    if idx[-1] != n - 1:
        idx.append(n - 1)
    xl = "".join(f'<span class="{"a0" if i == 0 else "a1" if i == n - 1 else "o" if j % 2 else ""}" style="left:{xs[i]:.3f}%">'
                 f'{_e(series[i][0][5:])}</span>' for j, i in enumerate(idx))      # o = 窄屏上隔一个省掉
    col = 100 / (n - 1)
    cols = []
    for i, ((d, v), x, y) in enumerate(zip(series, xs, ys)):
        side = " l" if x < 18 else " r" if x > 82 else ""          # 靠边的标签往里收，不出画框
        half = " lh" if x < 50 else " rh"                           # 窄屏：标签一律朝画面中间展开
        last = " last" if i == n - 1 else ""
        cols.append(f'<i class="pt{side}{half}{last}" style="left:{x:.3f}%;width:{col:.3f}%">'
                    f'<b class="dot" style="top:{y:.3f}%"></b><span class="tip" style="top:{y:.3f}%">{label(d, v)}</span></i>')
    first, last = series[0], series[-1]
    st.markdown(
        f'<div class="ff-blk"><figure class="ff-trend" style="margin:0"><div class="plot" style="height:{height}px">'
        f'{"".join(grid)}<i class="hatch" aria-hidden="true" style="-webkit-mask-image:{area_url};mask-image:{area_url}"></i>'
        f'<i class="base" style="top:{ys[0]:.3f}%"></i>'
        f'<svg class="line" viewBox="0 0 1000 100" preserveAspectRatio="none" role="img" aria-label="净资产走势">'
        f'<path d="{path}" vector-effect="non-scaling-stroke"/></svg>{"".join(cols)}</div>'
        f'<div class="xl" aria-hidden="true">{xl}</div>'
        f'<figcaption><span>{label(*first)}</span><span>{label(*last)}</span></figcaption></figure></div>',
        unsafe_allow_html=True)


# ---------- 事项列表 ----------

def _agenda_html(events: list, fx, kinds: dict[str, str], today) -> str:
    out, month = [], None
    week = "一二三四五六日"
    for e in events:
        m = f"{e.start:%Y 年 %-m 月}"
        if m != month:
            month = m
            out.append(f'<li class="mon serif">{_e(m)}</li>')
        if e.month_only:
            day = "当月"
            dsub = "日期未定"
        else:
            day = f"{e.start.day}"
            dsub = f"周{week[e.start.weekday()]}"
        rng = f"至 {e.end:%Y-%m-%d}" if e.end and e.end != e.start else ""
        src = (f' <a href="{_e(e.source)}" target="_blank" rel="noopener noreferrer">出处</a>'
               if e.source and str(e.source).startswith(("http://", "https://")) else "")
        amt = pair(e.usd, fx) if e.usd is not None else ""
        soon = (e.start - today).days if not e.month_only else None
        badge = ('<span class="donetag">已完成</span>' if e.done else
                 f'<span class="soon">{soon} 天后</span>' if soon is not None and 0 <= soon <= 14 else
                 '<span class="soon">进行中</span>' if e.end and e.start <= today <= e.end else "")
        done = " done" if e.done else ""
        out.append(
            f'<li class="ev{done}"><div class="d"><b>{_e(day)}</b><span>{_e(dsub)}</span></div>'
            f'<div class="body"><div class="t"><span class="kind k-{_e(e.kind)}">{_e(kinds.get(e.kind, e.kind))}</span>'
            f'<span class="tt">{_e(e.title)}</span>{badge}</div>'
            f'<div class="n">{_e(" · ".join(x for x in (e.member, rng, e.note) if x))}{src}</div></div>'
            f'<div class="amt">{amt}</div></li>')
    return f'<ul class="ff-agenda">{"".join(out)}</ul>' if out else ""


def agenda_list(events: list, fx, kinds: dict[str, str], today) -> None:
    """按月分组的事项：日期、类型、标题、成员、金额；规则类事项带出处链接。"""
    h = _agenda_html(events, fx, kinds, today)
    st.html(f'<div class="ff-blk">{h}</div>' if h else '<div class="ff-empty">暂无事项</div>')


# ---------- 息票：有关键节点的资金计划 ----------

_COUNTDOWN_JS = """<script>
(function () {
  function fmt(el) {
    var t = Number(el.getAttribute('data-deadline')) - Date.now();
    if (!(t > 0)) { el.textContent = '已过'; return; }
    var m = Math.floor(t / 60000), d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60), mi = m % 60;
    // 尖括号写成 \\x3c：st.html 的过滤器见到脚本里有「小于号 + 字母」会把整段脚本删掉
    var B = '\\x3cb>', E = '\\x3c/b>';
    el.innerHTML = (d ? B + d + E + '天' : '') + B + h + E + '时' + B + mi + E + '分';
  }
  function tick() { document.querySelectorAll('.ff-coupon [data-deadline]').forEach(fmt); }
  tick();
  if (window.__ffCountdown) return;
  window.__ffCountdown = setInterval(tick, 20000);
})();
</script>"""


def _countdown_html(delta_s: float) -> str:
    """服务器端先算好的倒计时（不开 JS 也对）；页面上由脚本每 20 秒刷新。"""
    m = max(0, int(delta_s // 60))
    d, h, mi = m // 1440, m % 1440 // 60, m % 60
    return (f"<b>{d}</b>天" if d else "") + f"<b>{h}</b>时<b>{mi}</b>分"


def _coupon_html(plan: dict, milestones: list, now, fx, cash, member: str) -> str:
    """一张息票。plan 是 cash_plans 的一行；milestones 来自 core.agenda.parse_milestones；cash 是现在账上的现金（None = 不比）。"""
    from core.agenda import next_milestone, parse_when

    when = parse_when(plan.get("expected"))
    today = now.date()
    if when and not when[1]:
        d = when[0]
        stub = (f'<span class="mo">{d.year} 年 {d.month} 月</span><b class="dd">{d.day}</b>'
                f'<span class="wk">周{"一二三四五六日"[d.weekday()]}</span>')
        left = (d - today).days
        stub += f'<span class="soon">{"今天" if left == 0 else f"{left} 天后" if left > 0 else f"已过 {-left} 天"}</span>'
    elif when:
        stub = f'<span class="mo">{when[0].year} 年</span><b class="dd">{when[0].month}</b><span class="wk">月内</span>'
    else:
        stub = '<span class="mo">日期未定</span>'
    amt = D(plan["amount"])
    usd = fmt_usd(amt)
    whole, dec = usd.lstrip("$").split(".")
    cny = f'<span class="fc">{_e(fmt_cny(amt * D(fx)))}</span>' if fx is not None else ""
    nxt = next_milestone(milestones, now)
    items = []
    for m in milestones:
        cls = "now" if m is nxt else "done" if m.end <= now else ""
        t = f"{m.when:%m/%d}".lstrip("0") + (f" {m.when:%H:%M} ET" if m.has_time else "")
        items.append(f'<li class="{cls}"><b>{_e(t)}</b><span>{_e(m.text)}</span></li>')
    miles = f'<ol class="miles" style="--n:{len(items)}">{"".join(items)}</ol>' if items else ""
    if nxt and nxt.has_time:
        cd = (f'<div class="cdw"><span>距{_e(nxt.short)}</span><span class="cdv" data-deadline="{int(nxt.when.timestamp() * 1000)}">'
              f'{_countdown_html((nxt.when - now).total_seconds())}</span></div>')
    elif nxt:
        days = (nxt.when.date() - today).days
        cd = (f'<div class="cdw"><span>距{_e(nxt.short)}</span><span class="cdv">'
              f'{"今天" if days <= 0 else f"<b>{days}</b>天"}</span></div>')
    else:
        cd = '<div class="cdw"><span>关键节点都已过去</span></div>'
    if cash is not None:
        gap = D(cash) - amt
        cash_html = (f'<div class="cash">现金 {_e(fmt_usd(cash))}<br>'
                     + (f'够付这笔，余 {_e(fmt_usd(gap))}' if gap >= 0 else f'<span class="short">还差 {_e(fmt_usd(-gap))}</span>')
                     + '</div>')
    else:
        cash_html = ""
    return (f'<article class="ff-coupon ff-fr" aria-label="{_e(plan["label"])}">{_COUPON_FRAME}'
            f'<i class="clip" aria-hidden="true"><i class="fld"></i><i class="under"></i><i class="wm"></i><i class="fib"></i></i>'
            f'<div class="stub">{stub}</div><div class="cface">'
            f'<div class="c-top"><span class="kind">资金</span><span class="tt">{_e(plan["label"])}</span>'
            f'<span class="who">{_e(member)}</span></div>'
            + (f'<p class="c-sub">{_e(plan.get("note") or "")}</p>' if plan.get("note") else "")
            + f'<div class="c-val"><span class="fv"><span class="cur">$</span>{_e(whole)}<span class="dec">.{_e(dec)}</span></span>{cny}</div>'
            f'{miles}<div class="c-foot">{cd}{cash_html}</div></div></article>')


def upcoming(coupons: list[tuple[dict, list]], events: list, fx, kinds: dict[str, str], today, now, cash,
             member: str) -> None:
    """近期事项：有关键节点的资金计划画成息票放在左边，其余事项按月列在右边；没有息票时就是原来的列表。"""
    if not coupons:
        agenda_list(events, fx, kinds, today)
        return
    tickets = "".join(_coupon_html(p, ms, now, fx, cash, member) for p, ms in coupons)
    rest = _agenda_html(events, fx, kinds, today)
    st.html(f'<div class="ff-blk"><div class="ff-upcoming{"" if rest else " solo"}"><div>{tickets}</div>'
            f'{f"<div>{rest}</div>" if rest else ""}</div></div>{_COUNTDOWN_JS}', unsafe_allow_javascript=True)


# ---------- 持仓表 ----------

def holdings_table(groups: list[dict], fx, total_label: str = "合计") -> None:
    """持仓：按组（股票 / 基金）分段，组头带小计；今日盈亏带一条以 0 为中线的小横条；表尾双线下给出合计。

    groups = [{title, desc, rows}]；row 来自 ticker_view，另加 meta_html（如「14 批 · 短期」，已转义）。
    """
    allrows = [r for g in groups for r in g["rows"]]
    peak = max((abs(r["day_pnl"]) for r in allrows if r["day_pnl"] is not None), default=ZERO) or Decimal(1)

    def sums(rs):
        quoted = [r for r in rs if r["unrealized"] is not None]
        return (sum((r["market_value"] for r in rs if r["market_value"] is not None), ZERO),
                sum((r["day_pnl"] for r in rs if r["day_pnl"] is not None), ZERO),
                sum((r["cost_basis"] for r in rs), ZERO),
                sum((r["unrealized"] for r in quoted), ZERO) if quoted else None,
                sum((r["cost_basis"] for r in quoted), ZERO))

    def unr_cell(unr, cost) -> str:
        """浮盈：美元一行；人民币 · 收益率一行。cost 只含有行情的批次。"""
        if unr is None:
            return '<span class="muted">—</span>'
        u = fmt_usd(unr, True)
        pct = fmt_pct(unr / cost, sign=True) if cost else ""
        second = disp(fmt_cny(D(unr) * D(fx), True)) + (" · " + disp(pct) if pct else "") if fx is not None else disp(pct)
        return money_html(u, second, sign_class(u))

    body = []
    for gi, g in enumerate(groups):
        rs = g["rows"]
        if not rs:
            continue
        mv, dp, cost, unr, qcost = sums(rs)
        if gi:
            body.append('<tr class="spacer"><td colspan="8"></td></tr>')
        body.append(
            f'<tr class="grp"><td class="c-gname" colspan="4"><span class="gname">{_e(g["title"])}'
            f'<small>{_e(g.get("desc") or "")}</small></span></td>'
            f'<td class="c-mv" data-l="市值">{pair(mv, fx)}</td>'
            f'<td class="c-pnl" data-l="今日盈亏">{pair(dp, fx, True, True)}</td>'
            f'<td class="c-cost">{pair(cost, fx)}</td><td class="c-unr">{unr_cell(unr, qcost)}</td></tr>')
        for r in rs:
            has_q = r["close"] is not None
            chg = fmt_pct(r["change_pct"], sign=True) if r["change_pct"] is not None else ""
            d = r["day_pnl"]
            pnl = ""
            if d is not None:
                w = float(abs(d) / peak) * 100
                dcls = sign_class(fmt_usd(d, True)) or "ff-flat"
                bar = (f'<span class="dbar {"pos" if d >= 0 else "neg"} {dcls}" aria-hidden="true">'
                       f'<i style="--w:{w:.2f}"></i></span>')
                pnl = f'<span class="pnlc">{bar}<span class="st">{pair(d, fx, True, True)}</span></span>'
            px = money_html(fmt_price(r["close"]), f"前收 {fmt_price(r['prev_close'])}") if has_q else "缺行情"
            tag = ' · <span class="tag">盘中</span>' if r.get("intraday") else ""
            unr = unr_cell(r["unrealized"], r["cost_basis"])
            body.append(
                f'<tr class="r"><td class="c-tk"><span class="sym">{_e(r["ticker"])}</span>'
                f'<span class="meta">{r.get("meta_html", "")}{tag}</span></td>'
                f'<td class="c-qty" data-l="股数">{_e(fmt_qty(r["quantity"]))}</td>'
                f'<td class="c-px" data-l="收盘">{px}</td>'
                f'<td class="c-chg {sign_class(chg)}" data-l="涨跌">{_e(disp(chg))}</td>'
                f'<td class="c-mv">{pair(r["market_value"], fx) if has_q else ""}</td>'
                f'<td class="c-pnl" data-l="今日盈亏">{pnl}</td>'
                f'<td class="c-cost" data-l="成本">{pair(r["cost_basis"], fx)}</td>'
                f'<td class="c-unr" data-l="浮盈">{unr}</td></tr>')
    mv, dp, cost, unr, qcost = sums(allrows)
    tfoot = (f'<tr><td class="lbl" colspan="4">{_e(total_label)}<small>{len(allrows)} 只</small></td>'
             f'<td class="c-mv" data-l="市值">{pair(mv, fx)}</td><td class="c-pnl" data-l="今日盈亏">{pair(dp, fx, True, True)}</td>'
             f'<td class="c-cost" data-l="总成本">{pair(cost, fx)}</td>'
             f'<td class="c-unr" data-l="累计浮盈">{unr_cell(unr, qcost)}</td></tr>')
    head = ('<tr><th>股票</th><th>股数</th><th>收盘</th><th>涨跌</th><th>市值<small>美元 / 人民币</small></th>'
            '<th>今日盈亏<small>美元 / 人民币</small></th><th>成本<small>美元 / 人民币</small></th>'
            '<th>浮盈<small>美元 / 人民币 · 收益率</small></th></tr>')
    st.html(f'<div class="ff-blk"><table class="ff-hold"><thead>{head}</thead><tbody>{"".join(body)}</tbody>'
            f'<tfoot>{tfoot}</tfoot></table></div>')


# ---------- 通用只读表 ----------

def table(df: pd.DataFrame, signed: list[str] | None = None, dense: bool = False, **_ignored) -> None:
    """只读表格：HTML 渲染（可换行、可上色、窄屏横向滚动）。列名带 盈亏/收益/浮盈/涨跌 的列按涨跌上色。"""
    st.html(table_html(df, signed, dense))


# ---------- 横条（只比大小） ----------

def hbars(rows: list[tuple[str, Decimal]], fx) -> None:
    """一组横条：单一颜色，只比大小；条尾标美元，下一行人民币。"""
    top = max((v for _, v in rows), default=ZERO)
    out = []
    for name, v in rows:
        w = float(v / top) * 100 if top > 0 and v > 0 else 0
        out.append(f'<div class="row"><span class="nm">{_e(name)}</span><span class="track"><i style="width:{w:.2f}%">'
                   f'</i></span><span class="val">{pair(v, fx)}</span></div>')
    st.html(f'<div class="ff-blk"><div class="ff-hbars">{"".join(out)}</div></div>')


scenario_bars = hbars


# ---------- 外观：跟随系统 / 浅色 / 深色 ----------

APPEARANCE = {"auto": "System", "light": "Light", "dark": "Dark"}


def appearance_js(mode: str, page_paths: list[str]) -> str:
    """把外观选择同步到 Streamlit 自己的主题记录（浏览器 localStorage，按页面路径分别存），有变化就刷新一次页面。

    Streamlit 1.66 在页面加载时读 localStorage["stActiveTheme-<路径>-v2"]（"System" / "Light" / "Dark"），
    没有这条记录时等同于 "System"。只在本机浏览器里改，不联网。
    """
    want = APPEARANCE.get(mode, "System")
    paths = ",".join(f'"{p}"' for p in page_paths)
    return f"""<script>
(function () {{
  var want = "{want}";
  if (window.__ffAppearance === want) return;
  window.__ffAppearance = want;
  var paths = [{paths}];
  if (paths.indexOf(location.pathname) < 0) paths.push(location.pathname);
  var changed = false;
  try {{
    paths.forEach(function (p) {{
      var k = "stActiveTheme-" + p + "-v2";
      var cur = localStorage.getItem(k);
      var curVal = cur ? JSON.parse(cur) : "System";
      if (curVal !== want) {{ localStorage.setItem(k, JSON.stringify(want)); changed = true; }}
    }});
  }} catch (e) {{ return; }}
  if (changed) location.reload();
}})();
</script>"""
