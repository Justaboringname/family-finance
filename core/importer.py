"""Schwab「Lot Details」CSV 导入。

文件格式（每只股票一个文件）：
  第 1 行：标题，含 ticker、账户尾号和日期，例如
           "ABC Lot Details for ...123 as of 04:10 PM ET, 10/15/2026"
  第 3 行：表头 Open Date, Quantity, Price, Cost/Share, Market Value, Cost Basis,
           Gain/Loss ($), Gain/Loss (%), Holding Period
  之后：每个批次一行；Total 行跳过。金额里的 $ 和逗号会被清洗。
重复导入时按 (账户, ticker) 整体覆盖。
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .db import rows
from .money import D, q2
from .portfolio import FALLBACK_ACCOUNT

TITLE_RE = re.compile(r"^\s*([A-Z][A-Z0-9.\-]*)\s+Lot Details", re.I)
ACCOUNT_RE = re.compile(r"\.\.\.\s*(\d{3,4})")
DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


@dataclass
class ParsedLot:
    open_date: date
    quantity: Decimal
    cost_per_share: Decimal
    cost_basis: Decimal
    holding_period: str = ""


@dataclass
class ParsedFile:
    filename: str
    ticker: str
    account: str
    as_of: date | None
    lots: list[ParsedLot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_quantity(self) -> Decimal:
        return sum((x.quantity for x in self.lots), Decimal(0))

    @property
    def total_cost(self) -> Decimal:
        return sum((x.cost_basis for x in self.lots), Decimal(0))


class ImportError_(Exception):
    pass


def clean_number(s: str) -> Decimal | None:
    """'$1,234.56' → 1234.56；'($12.00)' / '-$12.00' → -12；'--' / 空 → None。"""
    if s is None:
        return None
    t = str(s).strip()
    if t in ("", "--", "N/A", "n/a"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace("$", "").replace(",", "").replace("%", "").strip()
    if t.startswith("+"):
        t = t[1:]
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return -v if neg else v


def parse_date(s: str) -> date | None:
    m = DATE_RE.search(s or "")
    if not m:
        try:
            return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
        except ValueError:
            return None
    mo, dd, yy = map(int, m.groups())
    return date(yy, mo, dd)


def parse_lot_details(content: bytes | str, filename: str = "", default_account: str = FALLBACK_ACCOUNT) -> ParsedFile:
    """default_account：标题里没有账户尾号时归到哪个账户（界面传 core.portfolio.default_account(conn)）。"""
    if isinstance(content, bytes):
        # 只有带 BOM 时才按 UTF-16 解；否则先 UTF-8，再 Windows 的 cp1252
        encs = ("utf-16",) if content[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252")
        for enc in encs:
            try:
                text = content.decode(enc)
                break
            except UnicodeError:
                continue
        else:
            raise ImportError_(f"{filename}：文件编码无法识别")
    else:
        text = content.lstrip("\ufeff")
    table = list(csv.reader(io.StringIO(text)))
    if not table or not table[0]:
        raise ImportError_(f"{filename}：文件为空")

    title = " ".join(c for c in table[0] if c).strip()
    m = TITLE_RE.search(title)
    if not m:
        raise ImportError_(f"{filename}：第 1 行不是 Lot Details 标题：{title[:80]}")
    ticker = m.group(1).upper()
    acct = ACCOUNT_RE.search(title)
    account = f"Schwab-{acct.group(1)}" if acct else default_account
    as_of = None
    dates = DATE_RE.findall(title)
    if dates:
        mo, dd, yy = map(int, dates[-1])
        as_of = date(yy, mo, dd)

    header_idx = None
    for i, row in enumerate(table[:10]):
        cells = [c.strip().lower() for c in row]
        if "open date" in cells and "quantity" in cells:
            header_idx = i
            break
    if header_idx is None:
        raise ImportError_(f"{filename}：前 10 行里找不到表头（Open Date, Quantity, ...）")
    header = [c.strip().lower() for c in table[header_idx]]

    def col(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_date = col("open date")
    i_qty = col("quantity")
    i_cps = col("cost/share", "cost per share")
    i_basis = col("cost basis")
    i_hp = col("holding period")
    if i_basis is None and i_cps is None:
        raise ImportError_(f"{filename}：缺少 Cost Basis / Cost/Share 列")

    pf = ParsedFile(filename=filename, ticker=ticker, account=account, as_of=as_of)
    if not acct:
        pf.warnings.append(f"标题里没有账户尾号，按 {default_account} 导入")
    for row in table[header_idx + 1:]:
        if not row or not any(c.strip() for c in row):
            continue
        first = row[i_date].strip() if i_date is not None and i_date < len(row) else ""
        if first.lower().startswith("total") or any(c.strip().lower() == "total" for c in row[:2]):
            continue
        od = parse_date(first)
        if od is None:
            pf.warnings.append(f"跳过无法识别日期的行：{row[:4]}")
            continue

        def cell(i):
            return row[i] if i is not None and i < len(row) else ""

        qty = clean_number(cell(i_qty))
        cps = clean_number(cell(i_cps))
        basis = clean_number(cell(i_basis))
        if qty is None or qty <= 0:
            pf.warnings.append(f"{od} 股数无效，已跳过")
            continue
        if basis is None and cps is not None:
            basis = q2(cps * qty)
        if cps is None and basis is not None:
            cps = basis / qty
        if basis is None:
            pf.warnings.append(f"{od} 缺少成本，已跳过")
            continue
        pf.lots.append(ParsedLot(od, qty, cps, basis, cell(i_hp).strip()))
    if not pf.lots:
        pf.warnings.append("没有读到任何批次")
    return pf


def apply_import(conn: sqlite3.Connection, files: list[ParsedFile], source: str = "schwab_csv") -> list[str]:
    """按 (账户, ticker) 整体覆盖。返回提示信息。"""
    msgs = []
    seen = {}
    for pf in files:
        key = (pf.account, pf.ticker)
        if key in seen:
            msgs.append(f"{pf.ticker} 有多个文件，只用最后一个（{pf.filename}）")
        seen[key] = pf
    with conn:
        for (account, ticker), pf in seen.items():
            if pf.as_of:
                later = rows(
                    conn,
                    "SELECT sale_date FROM sales WHERE account=? AND ticker=? AND sale_date > ?",
                    (account, ticker, pf.as_of.isoformat()),
                )
                if later:
                    msgs.append(
                        f"注意：{ticker} 的 CSV 日期 {pf.as_of} 早于已记录的卖出 {later[0]['sale_date']}，"
                        "导入后的批次可能包含已卖出的股票"
                    )
            conn.execute("DELETE FROM lots WHERE account=? AND ticker=?", (account, ticker))
            for lot in pf.lots:
                conn.execute(
                    "INSERT INTO lots(account, ticker, open_date, quantity, cost_per_share, cost_basis, source, as_of)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (
                        account,
                        ticker,
                        lot.open_date.isoformat(),
                        str(lot.quantity),
                        str(lot.cost_per_share),
                        str(lot.cost_basis),
                        source,
                        pf.as_of.isoformat() if pf.as_of else None,
                    ),
                )
            msgs.append(f"{ticker}（{account}）：导入 {len(pf.lots)} 批，{pf.total_quantity} 股，成本 ${q2(pf.total_cost):,}")
    return msgs


def load_simple_csv(path) -> list[dict]:
    """读取 ./data/initial_lots.csv 这类简单表（account,ticker,open_date,quantity,cost_per_share,cost_basis）。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        out = []
        for r in csv.DictReader(f):
            out.append(
                {
                    "account": r["account"].strip(),
                    "ticker": r["ticker"].strip().upper(),
                    "open_date": r["open_date"].strip(),
                    "quantity": D(r["quantity"]),
                    "cost_per_share": D(r["cost_per_share"]),
                    "cost_basis": D(r["cost_basis"]),
                }
            )
        return out
