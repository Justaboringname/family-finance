"""持仓、持有期、市值和每日盈亏；现金、EB-5、AAPL 股票计划。"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta

from .db import get_setting, one, rows
from .money import ZERO, D

FALLBACK_ACCOUNT = "Schwab-000"            # 库里还没有任何账户记录时用的占位账户
BROKER_SOURCES = ("schwab_csv", "initial")   # 成本来自券商导出（已含券商做过的 wash sale 调整）


# ---------- 持有期 ----------

def first_long_term_date(open_date: date) -> date:
    """持有期从买入次日起算，持有超过 1 年为长期：买入日 + 1 年 + 1 天 当天或之后卖出（Pub 550）。

    月末买入按 Rev. Rul. 66-7：从下个月 1 日起算 1 年。例如 2027-02-28 → 2028-03-01，
    2028-02-29 → 2029-03-01；而闰年的 2028-02-28 不是月末 → 2029-03-01。
    """
    next_day = open_date + timedelta(days=1)
    if next_day.month != open_date.month:          # 月末买入
        return next_day + relativedelta(years=1)
    return open_date + relativedelta(years=1) + timedelta(days=1)


def is_long_term(open_date: date, sale_date: date) -> bool:
    return sale_date >= first_long_term_date(open_date)


def days_to_long_term(open_date: date, today: date) -> int:
    return max(0, (first_long_term_date(open_date) - today).days)


# ---------- 批次 ----------

@dataclass
class Lot:
    id: int | None
    account: str
    ticker: str
    open_date: date
    quantity: Decimal
    cost_per_share: Decimal
    cost_basis: Decimal
    source: str | None = None
    as_of: str | None = None

    @property
    def key(self) -> tuple:
        return (self.account, self.ticker, self.open_date)


def load_lots(conn: sqlite3.Connection, account: str | None = None, ticker: str | None = None) -> list[Lot]:
    sql = "SELECT * FROM lots WHERE 1=1"
    args: list = []
    if account:
        sql += " AND account = ?"
        args.append(account)
    if ticker:
        sql += " AND ticker = ?"
        args.append(ticker)
    sql += " ORDER BY ticker, open_date, id"
    return [
        Lot(r["id"], r["account"], r["ticker"], date.fromisoformat(r["open_date"]), D(r["quantity"]),
            D(r["cost_per_share"]), D(r["cost_basis"]), r["source"], r["as_of"])
        for r in rows(conn, sql, args)
    ]


def totals_by_ticker(lots: list[Lot]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for lot in lots:
        t = out.setdefault(lot.ticker, {"quantity": ZERO, "cost_basis": ZERO})
        t["quantity"] += lot.quantity
        t["cost_basis"] += lot.cost_basis
    return out


@dataclass
class Quote:
    ticker: str
    close: Decimal
    prev_close: Decimal
    quote_date: str = ""
    intraday: bool = False
    source: str = ""


def lot_view(lots: list[Lot], quotes: dict[str, Quote], today: date) -> list[dict]:
    out = []
    for lot in lots:
        q = quotes.get(lot.ticker)
        mv = lot.quantity * q.close if q else None
        lt_date = first_long_term_date(lot.open_date)
        out.append(
            {
                "lot_id": lot.id,
                "account": lot.account,
                "ticker": lot.ticker,
                "open_date": lot.open_date,
                "quantity": lot.quantity,
                "cost_per_share": lot.cost_per_share,
                "cost_basis": lot.cost_basis,
                "market_value": mv,
                "unrealized": (mv - lot.cost_basis) if mv is not None else None,
                "unrealized_pct": ((mv - lot.cost_basis) / lot.cost_basis) if mv is not None and lot.cost_basis else None,
                "term": "长期" if today >= lt_date else "短期",
                "long_term_date": lt_date,
                "days_to_long_term": days_to_long_term(lot.open_date, today),
            }
        )
    return out


def lot_day_pnl(lot: Lot, q: Quote) -> Decimal:
    """行情当天（或之后）买入的批次，当日盈亏从买入价算起；其余从前收盘价算起。"""
    bought_today = bool(q.quote_date) and lot.open_date >= date.fromisoformat(q.quote_date[:10])
    return lot.quantity * (q.close - (lot.cost_per_share if bought_today else q.prev_close))


def ticker_view(lots: list[Lot], quotes: dict[str, Quote]) -> list[dict]:
    out = []
    for ticker, t in totals_by_ticker(lots).items():
        q = quotes.get(ticker)
        row = {"ticker": ticker, "quantity": t["quantity"], "cost_basis": t["cost_basis"], "close": None,
               "prev_close": None, "change_pct": None, "market_value": None, "day_pnl": None, "unrealized": None,
               "quote_date": "", "intraday": False, "source": ""}
        if q:
            mv = t["quantity"] * q.close
            row.update(close=q.close, prev_close=q.prev_close,
                       change_pct=(q.close - q.prev_close) / q.prev_close if q.prev_close else None,
                       market_value=mv, day_pnl=sum((lot_day_pnl(l, q) for l in lots if l.ticker == ticker), ZERO),
                       unrealized=mv - t["cost_basis"], quote_date=q.quote_date, intraday=q.intraday,
                       source=q.source)
        out.append(row)
    return out


# ---------- 已实现卖出 ----------

@dataclass
class RealizedLeg:
    sale_id: int | None
    account: str
    ticker: str
    sale_date: date
    open_date: date
    quantity: Decimal
    proceeds: Decimal      # 已扣除分摊的手续费
    cost_basis: Decimal
    lot_id: int | None = None
    basis_source: str | None = None   # 卖出时该批次成本的来源（schwab_csv / initial / manual …）
    basis_as_of: str | None = None    # 该成本对应的券商数据日期

    @property
    def gain(self) -> Decimal:
        return self.proceeds - self.cost_basis

    @property
    def long_term(self) -> bool:
        return is_long_term(self.open_date, self.sale_date)

    @property
    def lot_key(self) -> tuple:
        return (self.account, self.ticker, self.open_date)


def load_realized(conn: sqlite3.Connection, year: int | None = None) -> list[RealizedLeg]:
    sql = ("SELECT s.id AS sale_id, s.account, s.ticker, s.sale_date, s.price, s.fee, sl.open_date, sl.quantity,"
           " sl.cost_basis, sl.lot_id, sl.lot_source, sl.lot_as_of FROM sales s JOIN sale_lots sl ON sl.sale_id = s.id")
    args: list = []
    if year:
        sql += " WHERE s.sale_date BETWEEN ? AND ?"
        args = [f"{year}-01-01", f"{year}-12-31"]
    sql += " ORDER BY s.sale_date, s.id, sl.id"
    data = rows(conn, sql, args)
    qty_by_sale: dict[int, Decimal] = {}
    for r in data:
        qty_by_sale[r["sale_id"]] = qty_by_sale.get(r["sale_id"], ZERO) + D(r["quantity"])
    out = []
    for r in data:
        qty = D(r["quantity"])
        fee_share = D(r["fee"]) * qty / qty_by_sale[r["sale_id"]] if qty_by_sale[r["sale_id"]] else ZERO
        out.append(RealizedLeg(r["sale_id"], r["account"], r["ticker"], date.fromisoformat(r["sale_date"]),
                               date.fromisoformat(r["open_date"]), qty, qty * D(r["price"]) - fee_share,
                               D(r["cost_basis"]), r["lot_id"], r["lot_source"], r["lot_as_of"]))
    return out


def record_sale(conn: sqlite3.Connection, account: str, ticker: str, sale_date: date, price, fee,
                allocations: list[tuple[int, Decimal]], note: str = "") -> int:
    """记录一笔已实现卖出：allocations = [(lot_id, 股数)]。会同步扣减批次。"""
    if not allocations:
        raise ValueError("没有选择批次")
    with conn:
        cur = conn.execute("INSERT INTO sales(account, ticker, sale_date, price, fee, note) VALUES(?,?,?,?,?,?)",
                           (account, ticker, sale_date.isoformat(), str(D(price)), str(D(fee)), note))
        sale_id = cur.lastrowid
        for lot_id, qty in allocations:
            qty = D(qty)
            lot = one(conn, "SELECT * FROM lots WHERE id = ?", (lot_id,))
            if not lot or lot["ticker"] != ticker:
                raise ValueError(f"批次 {lot_id} 不存在或不是 {ticker}")
            have = D(lot["quantity"])
            if qty <= 0 or qty > have:
                raise ValueError(f"批次 {lot['open_date']} 只有 {have} 股，不能卖 {qty} 股")
            basis = D(lot["cost_basis"]) * qty / have
            conn.execute(
                "INSERT INTO sale_lots(sale_id, open_date, quantity, cost_basis, lot_id, cost_per_share, lot_source,"
                " lot_as_of) VALUES(?,?,?,?,?,?,?,?)",
                (sale_id, lot["open_date"], str(qty), str(basis), lot_id, lot["cost_per_share"], lot["source"],
                 lot["as_of"]))
            if qty == have:
                conn.execute("DELETE FROM lots WHERE id = ?", (lot_id,))
            else:
                conn.execute("UPDATE lots SET quantity = ?, cost_basis = ? WHERE id = ?",
                             (str(have - qty), str(D(lot["cost_basis"]) - basis), lot_id))
    return sale_id


def delete_sale(conn: sqlite3.Connection, sale_id: int, restore_lots: bool = True) -> None:
    """删除一笔卖出记录；可选把股数还回批次（按买入日合并）。"""
    sale = one(conn, "SELECT * FROM sales WHERE id = ?", (sale_id,))
    if not sale:
        return
    with conn:
        if restore_lots:
            for sl in rows(conn, "SELECT * FROM sale_lots WHERE sale_id = ?", (sale_id,)):
                q, b = D(sl["quantity"]), D(sl["cost_basis"])
                # 优先还回原来那一批；同一天买的不同成本批次不能混在一起
                lot = one(conn, "SELECT * FROM lots WHERE id = ? AND ticker = ?", (sl["lot_id"], sale["ticker"])) \
                    if sl["lot_id"] else None
                if lot:
                    nq = D(lot["quantity"]) + q
                    nb = D(lot["cost_basis"]) + b
                    conn.execute("UPDATE lots SET quantity=?, cost_basis=? WHERE id=?", (str(nq), str(nb), lot["id"]))
                else:
                    cps = D(sl["cost_per_share"]) if sl["cost_per_share"] else b / q
                    conn.execute(
                        "INSERT INTO lots(account, ticker, open_date, quantity, cost_per_share, cost_basis, source, as_of)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (sale["account"], sale["ticker"], sl["open_date"], str(q), str(cps), str(b),
                         sl["lot_source"] or "restored", sl["lot_as_of"]))
        conn.execute("DELETE FROM sales WHERE id = ?", (sale_id,))


# ---------- 默认账户 ----------

def default_account(conn: sqlite3.Connection) -> str:
    """默认的券商账户（手动加批次、记现金、没指定账户时用）。

    账户尾号是个人数据，只存在数据库里，不写进代码：先看设置 default_account；
    没设就用库里记录最多的那个账户；库是空的才用占位账户。
    """
    s = (get_setting(conn, "default_account", "") or "").strip()
    if s:
        return s
    r = one(conn, "SELECT account, COUNT(*) AS n FROM (SELECT account FROM lots UNION ALL SELECT account FROM"
                  " cash_snapshots UNION ALL SELECT account FROM cash_transfers) GROUP BY account"
                  " ORDER BY n DESC, account LIMIT 1")
    return r["account"] if r else FALLBACK_ACCOUNT


# ---------- 现金 ----------

def cash_status(conn: sqlite3.Connection, today: date, account: str | None = None) -> dict:
    account = account or default_account(conn)
    snap = one(conn, "SELECT * FROM cash_snapshots WHERE account=? ORDER BY as_of DESC, id DESC LIMIT 1", (account,))
    base = D(snap["amount"]) if snap else ZERO
    snap_date = snap["as_of"] if snap else "0000-00-00"
    transfers = []
    pending = ZERO
    for t in rows(conn, "SELECT * FROM cash_transfers WHERE account=? AND settle_date > ? ORDER BY settle_date",
                  (account, snap_date)):
        settle = date.fromisoformat(t["settle_date"])
        avail = date.fromisoformat(t["available_date"]) if t["available_date"] else settle
        if today < settle:
            status = f"在途，{settle:%m/%d} 入账，{avail:%m/%d} 完全可用"
        elif today < avail:
            status = f"已入账，{avail:%m/%d} 完全可用"
        else:
            status = "已可用"
        amt = D(t["amount"])
        if today < avail:
            pending += amt
        transfers.append({**t, "amount": amt, "status": status})
    total = base + sum((t["amount"] for t in transfers), ZERO)
    plans = [{**p, "amount": D(p["amount"])} for p in rows(conn, "SELECT * FROM cash_plans ORDER BY id")]
    return {"snapshot": snap, "base": base, "transfers": transfers, "total": total, "not_yet_available": pending,
            "plans": plans}


# ---------- EB-5 ----------

def eb5_status(conn: sqlite3.Connection, today: date) -> dict | None:
    e = one(conn, "SELECT * FROM eb5 WHERE id = 1")
    if not e:
        return None
    ret = date.fromisoformat(e["expected_return_date"])
    return {
        "principal": D(e["principal"]),
        "expected_return_date": ret,
        "days_left": (ret - today).days,
        "returned": bool(e["returned"]),
        "returned_date": e["returned_date"],
        "note": e["note"],
    }


def mark_eb5_returned(conn: sqlite3.Connection, returned_date: date, account: str | None = None) -> None:
    """回款后转为现金：记一笔转入，EB-5 不再计入资产。"""
    account = account or default_account(conn)
    e = one(conn, "SELECT * FROM eb5 WHERE id = 1")
    if not e or e["returned"]:
        return
    with conn:
        conn.execute("UPDATE eb5 SET returned = 1, returned_date = ? WHERE id = 1", (returned_date.isoformat(),))
        conn.execute(
            "INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note) VALUES(?,?,?,?,?,?)",
            (account, returned_date.isoformat(), returned_date.isoformat(), returned_date.isoformat(), e["principal"],
             "EB-5 本金回款"))


# ---------- AAPL 股票计划 ----------

def aapl_holdings(conn: sqlite3.Connection) -> dict | None:
    a = one(conn, "SELECT * FROM aapl_holdings WHERE id = 1")
    if not a:
        return None
    espp, vested, unvested = D(a["espp_shares"]), D(a["rs_vested_shares"]), D(a["rs_unvested_shares"])
    return {
        "espp": espp,
        "rs_vested": vested,
        "rs_unvested": unvested,
        "rs_total": vested + unvested,
        "vested_incl_espp": espp + vested,
        "total": espp + vested + unvested,
        "ref_price": D(a["ref_price"]) if a["ref_price"] else None,
        "ref_date": a["ref_date"],
        "ref_total_value": D(a["ref_total_value"]) if a["ref_total_value"] else None,
    }


# ---------- 汇总 ----------

def parse_fund_tickers(text: str | None) -> frozenset[str]:
    """设置里「按基金计算」的代码，用逗号、顿号、分号或空格分隔，例如 "SGOV, SNSXX" 或 "SGOV、SNSXX"。"""
    return frozenset(t.upper() for t in re.split(r"[\s,，、;；]+", text or "") if t)


def _holding_row(item: str, rows_: list[dict]) -> dict:
    missing = [r["ticker"] for r in rows_ if r["market_value"] is None]
    return {"item": item,
            "market_value": sum((r["market_value"] for r in rows_ if r["market_value"] is not None), ZERO),
            "day_pnl": sum((r["day_pnl"] for r in rows_ if r["day_pnl"] is not None), ZERO),
            "note": ("缺行情：" + ", ".join(missing)) if missing else ""}


def summary_rows(schwab: list[dict], aapl: dict | None, aapl_quote: Quote | None, eb5: dict | None,
                 funds: frozenset[str] = frozenset()) -> list[dict]:
    """汇总表：Schwab 股票、基金、AAPL 股票计划、EB-5、合计（不含现金）。

    funds 里的代码（如 SGOV）单独算作「基金」，同样计入合计和当日盈亏；税务上仍按普通持仓处理。
    """
    out = [_holding_row("Schwab 股票", [r for r in schwab if r["ticker"] not in funds])]
    fund_rows = [r for r in schwab if r["ticker"] in funds]
    if fund_rows:
        out.append(_holding_row("基金", fund_rows))
    if aapl:
        if aapl_quote:
            out.append({"item": "AAPL 股票计划（含未归属）", "market_value": aapl["total"] * aapl_quote.close,
                        "day_pnl": aapl["total"] * (aapl_quote.close - aapl_quote.prev_close), "note": ""})
        else:
            out.append({"item": "AAPL 股票计划（含未归属）", "market_value": ZERO, "day_pnl": ZERO, "note": "缺 AAPL 行情"})
    if eb5 and not eb5["returned"]:
        out.append({"item": "EB-5", "market_value": eb5["principal"], "day_pnl": ZERO, "note": "按本金，不随市场变动"})
    out.append({"item": "合计（不含现金）", "market_value": sum((r["market_value"] for r in out), ZERO),
                "day_pnl": sum((r["day_pnl"] for r in out), ZERO), "note": ""})
    return out
