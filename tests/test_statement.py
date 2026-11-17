"""月度对账单：期初 / 净转入 / 变动 / 期末，内部转换不算转入，没有更早记录时标明起点。"""
import json
from datetime import date

from core.money import D
from core.statement import available_months, month_statement


def _snap(conn, d, nw):
    conn.execute("INSERT INTO nw_snapshots(snap_date, net_worth, assets, liabilities, invested, cash, day_pnl, fx, detail,"
                 " updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (d, str(nw), str(nw), "0", str(nw), "0", "0", "7", json.dumps({"items": []}), "x"))


def test_month_statement_activity_summary(conn):
    with conn:
        _snap(conn, "2026-09-30", 100)
        _snap(conn, "2026-10-09", 130)
        _snap(conn, "2026-10-16", 150)
        conn.execute("INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note)"
                     " VALUES('Schwab-123','2026-10-03','2026-10-05','2026-10-08','20','从支票账户转入')")
        conn.execute("INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note)"
                     " VALUES('Schwab-123','2026-10-06','2026-10-06','2026-10-06','800','EB-5 本金回款')")
    st = month_statement(conn, "2026-10", date(2026, 10, 16))
    m = st["month"]
    assert m.opening["snap_date"] == "2026-09-30" and m.closing["snap_date"] == "2026-10-16"
    assert m.opening_label == "9 月 30 日收盘"
    assert m.net_in == D(20) and m.change == D(30)                         # 150 − 100 − 20；EB-5 回款不算转入
    y = st["year"]
    assert y.opening["snap_date"] == "2026-09-30" and "之前还没有记录" in y.opening_label
    assert y.change == D(30)
    assert available_months(conn, date(2026, 10, 16)) == ["2026-10", "2026-09"]


def test_first_month_without_earlier_snapshot(conn):
    with conn:
        _snap(conn, "2026-10-16", 150)
    m = month_statement(conn, "2026-10", date(2026, 10, 16))["month"]
    assert m.ok and m.opening is m.closing or m.opening["snap_date"] == m.closing["snap_date"]
    assert m.change == D(0) and "自 10-16 起" in m.opening_label
    empty = month_statement(conn, "2026-08", date(2026, 10, 16))["month"]
    assert not empty.ok and empty.change is None


def test_deposit_recorded_before_it_settles_is_not_market_change(conn):
    """周六记下周一才入账的转入：周五的快照已经含这笔钱。10 月和 11 月的「市值及其他变动」都应为 0。"""
    from core.portfolio import Quote
    from core.wealth import build_wealth, record_snapshot
    with conn:
        conn.execute("INSERT INTO lots(account, ticker, open_date, quantity, cost_per_share, cost_basis, source)"
                     " VALUES('Schwab-123','ACME','2026-01-05','100','100','10000','initial')")
        conn.execute("INSERT INTO cash_snapshots(account, as_of, amount, note) VALUES('Schwab-123','2026-10-28','1000','')")

    def run(quote_day, today):
        q = {"ACME": Quote("ACME", D(100), D(100), quote_day), "CNY=X": Quote("CNY=X", D(7), D(7), quote_day)}
        return record_snapshot(conn, build_wealth(conn, q, D(7), today))

    assert run("2026-10-29", date(2026, 10, 29)) and run("2026-10-30", date(2026, 10, 30))
    with conn:
        conn.execute("INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note)"
                     " VALUES('Schwab-123','2026-10-30','2026-11-02','2026-11-02','50000','从支票账户转入')")
    assert run("2026-10-30", date(2026, 10, 31)) == "2026-10-30"          # 周五的快照被覆盖成 61,000
    assert run("2026-11-02", date(2026, 11, 2)) == "2026-11-02"
    oct_ = month_statement(conn, "2026-10", date(2026, 11, 2))["month"]
    nov = month_statement(conn, "2026-11", date(2026, 11, 2))["month"]
    assert oct_.closing["net_worth"] == D(61000) and oct_.transit_close == D(50000)
    assert (oct_.net_in, oct_.change) == (D(50000), D(0))
    assert (nov.net_in, nov.change, nov.transit_open) == (D(0), D(0), D(50000))
    year = month_statement(conn, "2026-11", date(2026, 11, 2))["year"]
    assert (year.net_in, year.change) == (D(50000), D(0))


def test_future_snapshot_month_is_not_offered(conn):
    with conn:
        _snap(conn, "2026-10-16", 150)
        _snap(conn, "2027-11-20", 999)
    assert available_months(conn, date(2026, 10, 16)) == ["2026-10"]
