"""月度对账单：按私人银行月结单的格式汇总一个月。

变动摘要分「本期」「本年」两栏：期初净资产、已记录的净转入、市值及其他变动、期末净资产。
- 期初取上一期末最后一条快照；那之前还没有快照时，取本期第一条，并标明「自某日起」。不回填历史。
- 「已记录的净转入」只算记在「现金转账」里的外部资金进出，不含 EB-5 本金回款（那是资产之间的转换）。
  按入账日落在两次快照之间统计；快照里已经计入、但还没入账的在途转账（见 wealth.in_transit），
  期末的加上、期初的扣掉——口径和快照的净资产一致，在途或跨月的转账不会被算成市值变动。
- 现金是按余额快照记的：没记成转账的资金进出会落在「市值及其他变动」里，所以这一行的名字写明了这一点。
"""
from __future__ import annotations

import calendar as _cal
import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .db import rows
from .money import ZERO, D
from .portfolio import load_realized
from .wealth import INTERNAL_TRANSFER_NOTES, load_snapshots


@dataclass
class Period:
    label: str
    start: date
    end: date
    opening: dict | None          # 快照
    closing: dict | None
    opening_label: str            # 「9 月 30 日收盘」或「自 10-16 起」
    net_in: Decimal
    change: Decimal | None
    transfers: list[dict]         # 两次快照之间入账的外部转账
    transit_open: Decimal = ZERO  # 期初快照已计入、本期才入账的在途转账
    transit_close: Decimal = ZERO  # 期末快照已计入、下期才入账的在途转账

    @property
    def ok(self) -> bool:
        return self.opening is not None and self.closing is not None


def month_bounds(ym: str, today: date) -> tuple[date, date]:
    y, m = (int(x) for x in ym.split("-"))
    start = date(y, m, 1)
    end = date(y, m, _cal.monthrange(y, m)[1])
    return start, min(end, today)


def transit_total(snap: dict | None) -> Decimal:
    return sum((D(x["amount"]) for x in (snap or {}).get("detail", {}).get("in_transit", [])), ZERO)


def _period(conn: sqlite3.Connection, label: str, start: date, end: date, snaps: list[dict]) -> Period:
    before = [s for s in snaps if s["snap_date"] < start.isoformat()]
    inside = [s for s in snaps if start.isoformat() <= s["snap_date"] <= end.isoformat()]
    closing = inside[-1] if inside else None
    if before:
        opening = before[-1]
        d = date.fromisoformat(opening["snap_date"])
        opening_label = f"{d.month} 月 {d.day} 日收盘"
    elif inside:
        opening = inside[0]
        d = date.fromisoformat(opening["snap_date"])
        opening_label = f"自 {d:%m-%d} 起（之前还没有记录）"
    else:
        opening, opening_label = None, "没有记录"
    transfers = []
    if opening and closing:
        transfers = [
            {**t, "amount": D(t["amount"])}
            for t in rows(conn, "SELECT * FROM cash_transfers WHERE settle_date > ? AND settle_date <= ? ORDER BY settle_date",
                          (opening["snap_date"], closing["snap_date"]))
            if (t["note"] or "") not in INTERNAL_TRANSFER_NOTES]
    t_open, t_close = (transit_total(opening), transit_total(closing)) if opening and closing else (ZERO, ZERO)
    net_in = sum((t["amount"] for t in transfers), ZERO) + t_close - t_open
    change = closing["net_worth"] - opening["net_worth"] - net_in if opening and closing else None
    return Period(label, start, end, opening, closing, opening_label, net_in, change, transfers, t_open, t_close)


def month_statement(conn: sqlite3.Connection, ym: str, today: date) -> dict:
    start, end = month_bounds(ym, today)
    snaps = load_snapshots(conn)
    month = _period(conn, "本期", start, end, snaps)
    year = _period(conn, "本年", date(start.year, 1, 1), end, snaps)
    realized = [leg for leg in load_realized(conn, start.year) if start <= leg.sale_date <= end]
    return {
        "ym": ym, "start": start, "end": end, "month": month, "year": year,
        "realized": realized, "realized_gain": sum((x.gain for x in realized), ZERO),
        "series": [s for s in snaps if s["snap_date"] <= end.isoformat()],
    }


def available_months(conn: sqlite3.Connection, today: date) -> list[str]:
    """有快照的月份，加上本月；新的在前。今天之后的不算（手动行情日期填错时可能留下）。"""
    months = {r["snap_date"][:7] for r in rows(conn, "SELECT snap_date FROM nw_snapshots WHERE snap_date <= ?",
                                               (today.isoformat(),))}
    months.add(f"{today:%Y-%m}")
    return sorted(months, reverse=True)

