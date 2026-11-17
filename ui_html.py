"""自绘的只读表格和税务明细（HTML）。

Streamlit 自带的 st.dataframe 是画布表格，样式改不了、窄屏会截断，所以只读表格都改用这里的 HTML 渲染。
所有单元格文字都先 html.escape，数据库里的备注等内容不会被当成 HTML。
"""
from __future__ import annotations

import html
import math
import re
from datetime import date, datetime
from decimal import Decimal

import pandas as pd

from core.money import fmt_cny, fmt_usd

# fmt_pair 的输出："$1,234.56 / ¥8,240.00"（可带 +/- 号）
_PAIR = re.compile(r"^([+\-]?\$[\d,]+\.\d+) / ([+\-]?¥[\d,]+\.\d+)$")
_NUM = re.compile(r"^[+\-−]?[$¥]?[\d,]+(\.\d+)?%?$")
_EMPTY = {"", "—", "-", "–"}
SIGNED_HINTS = ("盈亏", "收益", "浮盈", "浮亏", "涨跌")
_TOTAL_PREFIX = ("合计", "含现金总计", "总计")


def esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    if v is pd.NaT:
        return ""
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v)


def sign_class(s: str) -> str:
    s = s.strip()
    if s.startswith("+"):
        return "ff-up"
    if s.startswith("-") or s.startswith("−"):
        return "ff-down" if any(ch.isdigit() and ch != "0" for ch in s) else ""
    return ""


def disp(s: str) -> str:
    """显示用：开头的减号换成真正的负号（−），和 + 号等宽、更好读。只改显示，不改数据。"""
    return "−" + s[1:] if isinstance(s, str) and s.startswith("-") else s


def money_html(usd: str, cny: str | None, cls: str = "") -> str:
    """金额单元格：美元一行，人民币一行（小一号）。cls 为 ff-up/ff-down 时两行都上色。"""
    second = f'<span class="ff-m2">{esc(disp(cny))}</span>' if cny else ""
    return f'<span class="ff-m{" " + cls if cls else ""}"><span>{esc(disp(usd))}</span>{second}</span>'


def pair(usd, fx, sign: bool = False, colored: bool = False) -> str:
    """美元在前、人民币在后的两行金额 HTML；fx 为 None 时只显示美元。"""
    u = fmt_usd(usd, sign)
    cls = sign_class(u) if colored else ""
    return money_html(u, fmt_cny(Decimal(str(usd)) * Decimal(str(fx)), sign) if fx is not None else None, cls)


def _cell(s: str, signed: bool) -> str:
    m = _PAIR.match(s)
    cls = sign_class(s) if signed else ""
    if m:
        return money_html(m.group(1), m.group(2), cls)
    shown = disp(s) if _NUM.match(s.strip()) else s
    return f'<span class="{cls}">{esc(shown)}</span>' if cls else esc(shown)


def _is_numeric_col(values: list[str]) -> bool:
    filled = [v for v in values if v.strip() not in _EMPTY]
    return bool(filled) and all(_PAIR.match(v) or _NUM.match(v.strip()) for v in filled)


def table_html(df: pd.DataFrame, signed: list[str] | None = None, dense: bool = False) -> str:
    """把 DataFrame（多数单元格已是格式化好的文字）画成 HTML 表格。

    - 数字列右对齐、等宽数字；"美元 / 人民币" 拆成两行。
    - 列名带 盈亏/收益/浮盈/涨跌 的列（或 signed 指定的列）按 +/- 上色。
    - 第一列是行名，始终左对齐；以「合计」开头的行加粗并加上分隔线。
    """
    if df is None or df.empty:
        return '<div class="ff-empty">暂无记录</div>'
    cols = [str(c) for c in df.columns]
    signed_cols = set(signed if signed is not None else [c for c in cols if any(h in c for h in SIGNED_HINTS)])
    data = [[_text(v) for v in row] for row in df.itertuples(index=False, name=None)]
    numeric = [i > 0 and _is_numeric_col([r[i] for r in data]) for i in range(len(cols))]
    long_text = [not numeric[i] and max((len(r[i]) for r in data), default=0) > 28 for i in range(len(cols))]

    def cls(i: int) -> str:
        return " ".join(c for c in ("num" if numeric[i] else "", "wrap" if long_text[i] else "") if c)

    head = "".join(f'<th class="{cls(i)}">{esc(c)}</th>' for i, c in enumerate(cols))
    body = []
    for r in data:
        total = r[0].startswith(_TOTAL_PREFIX)
        tds = "".join(f'<td class="{cls(i)}">{_cell(v, cols[i] in signed_cols)}</td>' for i, v in enumerate(r))
        body.append(f'<tr class="ff-total">{tds}</tr>' if total else f"<tr>{tds}</tr>")
    return (f'<div class="ff-table-wrap"><table class="ff-table{" dense" if dense else ""}">'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def trace_html(lines, fx=None, currency: str = "usd") -> str:
    """税额明细的账本：每行 = 项目 + 金额 + 规则 + 参数（带出处链接）。"""
    rows = []
    for ln in lines:
        if ln.kind == "warning":
            continue
        if ln.amount is None:
            amt = ""
        elif currency == "cny":
            amt = money_html(fmt_cny(ln.amount), None)
        elif currency == "plain":
            amt = money_html(f"{ln.amount:,.2f}", None)
        else:
            amt = pair(ln.amount, fx)
        chips = []
        for p in ln.params:
            src = str(p.source or "")
            link = (f'<a href="{esc(src)}" target="_blank" rel="noopener noreferrer">出处</a>'
                    if src.startswith(("http://", "https://")) else "")
            note = f'<span class="nt">{esc(p.note)}</span>' if p.note else ""
            chips.append(f'<span class="ff-param"><code>{esc(p.path)}</code> = {esc(p.value)}{note}{link}</span>')
        kind = f" k-{ln.kind}" if ln.kind in ("tax", "income") else ""
        rule = f'<div class="rule">{esc(ln.rule)}</div>' if ln.rule else ""
        params = f'<div class="params">{"".join(chips)}</div>' if chips else ""
        rows.append(f'<div class="ff-ledger-row{kind}"><div class="lbl">{esc(ln.label)}{rule}{params}</div>'
                    f'<div class="amt">{amt}</div></div>')
    return f'<div class="ff-ledger">{"".join(rows)}</div>' if rows else '<div class="ff-empty">暂无明细</div>'
