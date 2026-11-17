"""金额工具：全程 Decimal，只在展示或最终结果时四舍五入到分。"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, getcontext

getcontext().prec = 34

ZERO = Decimal("0")
CENT = Decimal("0.01")


def D(x) -> Decimal:
    """把任意数值安全地转成 Decimal（float 先转 str，避免二进制误差）。"""
    if x is None:
        return ZERO
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x))
    s = str(x).strip()
    return Decimal(s) if s else ZERO


def q2(x) -> Decimal:
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_usd(x, sign: bool = False) -> str:
    v = q2(x)
    s = f"{abs(v):,.2f}"
    if v < 0:
        return f"-${s}"
    return f"+${s}" if sign and v > 0 else f"${s}"


def fmt_cny(x, sign: bool = False) -> str:
    v = q2(x)
    s = f"{abs(v):,.2f}"
    if v < 0:
        return f"-¥{s}"
    return f"+¥{s}" if sign and v > 0 else f"¥{s}"


def fmt_pair(usd, fx, sign: bool = False) -> str:
    """美元在前、人民币在后。fx 为 None 时只显示美元。"""
    if fx is None:
        return fmt_usd(usd, sign)
    return f"{fmt_usd(usd, sign)} / {fmt_cny(D(usd) * D(fx), sign)}"


def fmt_price(x) -> str:
    """单价：至少两位小数，多余的有效小数（最多 4 位）保留。"""
    v = D(x)
    if v == v.quantize(CENT):
        return f"${v:,.2f}"
    s = f"{v.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP):,.4f}"
    while s.endswith("0") and len(s.split(".")[1]) > 2:
        s = s[:-1]
    return f"${s}"


def fmt_pct(x, digits: int = 2, sign: bool = False) -> str:
    v = D(x) * 100
    s = f"{v:.{digits}f}%"
    return f"+{s}" if sign and v > 0 else s


def fmt_qty(x) -> str:
    v = D(x).normalize()
    if v == v.to_integral():
        return f"{v:,.0f}"
    return f"{v:,f}"


def shares_bp(values: list) -> list[int]:
    """按金额算占比（单位：万分之一），用最大余数法让正数项的占比加起来正好 10000（= 100.00%）。负数和 0 记 0。
    网页的资产构成和手机 app 都用它，两边显示的占比逐位一致。"""
    pos = [D(v) if D(v) > 0 else ZERO for v in values]
    total = sum(pos, ZERO)
    if total <= 0:
        return [0] * len(values)
    raw = [v / total * 10000 for v in pos]
    out = [int(r) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)[:10000 - sum(out)]:
        out[i] += 1
    return out
