"""家族资产负债表：三种分组合计一致、流动性阶梯、集中度、每日快照的写入规则。"""
from datetime import date, datetime

from core.money import D
from core.portfolio import Quote
from core.wealth import (ALTERNATIVE, CASH, LISTED, LOCKED, NOW, PENDING, RESTRICTED, T1, VESTING, build_wealth,
                         load_snapshots, record_snapshot, snapshot_skip_reason)

TODAY = date(2026, 10, 16)


def _seed(conn, vest=True):
    with conn:
        conn.executemany(
            "INSERT INTO lots(account, ticker, open_date, quantity, cost_per_share, cost_basis, source) VALUES(?,?,?,?,?,?,?)",
            [("Schwab-123", "ACME", "2026-01-05", "10", "100", "1000", "initial"),
             ("Schwab-123", "SGOV", "2026-10-16", "100", "100.50", "10050", "manual")])
        conn.execute("INSERT INTO cash_snapshots(account, as_of, amount, note) VALUES('Schwab-123','2026-10-14','500','')")
        conn.execute("INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note)"
                     " VALUES('Schwab-123','2026-10-15','2026-10-16','2026-10-22','2000','从支票账户转入')")
        conn.execute("INSERT INTO aapl_holdings(id, espp_shares, rs_vested_shares, rs_unvested_shares)"
                     " VALUES(1,'10','20','30')")
        if vest:
            conn.execute("INSERT INTO aapl_vest_schedule(expected_date, quantity) VALUES('2027-03-15','12')")
        conn.execute("INSERT INTO eb5(id, principal, expected_return_date) VALUES(1,'800','2029-06-30')")


QUOTES = {"ACME": Quote("ACME", D(110), D(120), "2026-10-16"), "SGOV": Quote("SGOV", D("100.60"), D("100.55"), "2026-10-16"),
          "AAPL": Quote("AAPL", D(300), D(290), "2026-10-16"), "CNY=X": Quote("CNY=X", D("7.1"), D("7.2"), "2026-10-16")}


def test_three_groupings_and_balance_sheet_agree(conn):
    _seed(conn)
    with conn:
        conn.execute("INSERT INTO liabilities(name, member, amount) VALUES('信用额度','我','300')")
    w = build_wealth(conn, QUOTES, D("7.1"), TODAY)
    assets = D(1100) + D(10060) + D(2500) + D(60) * 300 + D(800)          # ACME + SGOV + 现金 + AAPL + EB-5
    assert w.assets == assets and w.liabilities_total == D(300) and w.net_worth == assets - 300
    for by in ("asset_class", "member", "account"):
        assert sum((g["usd"] for g in w.group(by)), D(0)) == w.assets
    cls = {g["name"]: g["usd"] for g in w.group("asset_class")}
    assert cls[CASH] == D(10060) + D(2500)                                  # SGOV 按类现金
    assert cls[LISTED] == D(1100) + D(30) * 300 and cls[RESTRICTED] == D(30) * 300 and cls[ALTERNATIVE] == D(800)
    mem = {g["name"]: g["usd"] for g in w.group("member")}
    assert mem["爸爸"] == D(60) * 300 and mem["我"] + mem["爸爸"] == w.assets
    assert w.cash == D(2500) and w.invested == w.assets - D(2500)
    # 当日盈亏：ACME −100、SGOV 当天买入从买入价算 +10、AAPL 60×10；现金不计
    assert w.day_pnl == D(-100) + D(10) + D(600)


def test_liquidity_ladder_and_unscheduled_vesting(conn):
    _seed(conn)
    w = build_wealth(conn, QUOTES, D("7.1"), TODAY)
    lad = {b["bucket"]: b for b in w.liquidity()}
    assert lad[NOW]["usd"] == D(500) and lad[PENDING]["usd"] == D(2000)
    assert lad[PENDING]["rows"][0].unlock == "2026-10-22"
    assert lad[T1]["usd"] == D(1100) + D(10060) + D(30) * 300
    vest = lad[VESTING]["rows"]
    assert [(r.unlock, r.usd) for r in vest] == [("2027-03-15", D(12) * 300), (None, D(18) * 300)]
    assert vest[1].note == "归属日期未录入"
    assert lad[LOCKED]["rows"][0].unlock == "2029-06-30"
    assert sum((b["usd"] for b in lad.values()), D(0)) == w.assets


def test_concentration_merges_aapl_and_flags_single_stock(conn):
    _seed(conn, vest=False)
    w = build_wealth(conn, QUOTES, D("7.1"), TODAY)
    top = w.concentration()
    assert top[0]["name"] == "AAPL" and top[0]["usd"] == D(60) * 300 and top[0]["flag"]
    assert all(t["name"] != "SGOV" for t in top)                             # 类现金不算集中度
    eb5 = next(t for t in top if t["name"] == "EB-5")
    assert not eb5["flag"]                                                   # 只标单一股票


def test_snapshot_written_once_per_quote_day_and_never_backwards(conn):
    _seed(conn)
    w = build_wealth(conn, QUOTES, D("7.1"), TODAY)
    assert record_snapshot(conn, w, datetime(2026, 10, 16, 20)) == "2026-10-16"
    assert record_snapshot(conn, w, datetime(2026, 10, 16, 21)) == "2026-10-16"   # 同一天覆盖
    assert len(load_snapshots(conn)) == 1
    old = {k: Quote(q.ticker, q.close, q.prev_close, "2026-10-15") for k, q in QUOTES.items()}
    assert record_snapshot(conn, build_wealth(conn, old, D("7.1"), TODAY)) is None   # 比最新快照旧：不写
    no_aapl = {k: v for k, v in QUOTES.items() if k != "AAPL"}
    assert record_snapshot(conn, build_wealth(conn, no_aapl, D("7.1"), TODAY)) is None   # 缺行情：不写
    assert record_snapshot(conn, build_wealth(conn, QUOTES, None, TODAY)) is None       # 没汇率：不写
    snap = load_snapshots(conn)[0]
    assert snap["net_worth"] == w.net_worth and snap["detail"]["by_member"]["爸爸"] == str(D(60) * 300)


def test_owner_settings_override_defaults(conn):
    import json

    from core.db import set_setting
    _seed(conn)
    set_setting(conn, "owners", json.dumps({"eb5": "妈妈"}, ensure_ascii=False))
    w = build_wealth(conn, QUOTES, D("7.1"), TODAY)
    mem = {g["name"]: g["usd"] for g in w.group("member")}
    assert mem["妈妈"] == D(800) and mem["爸爸"] == D(60) * 300


def test_snapshot_skips_intraday_and_future_dated_quotes(conn):
    _seed(conn)
    live = {**QUOTES, "ACME": Quote("ACME", D(90), D(100), "2026-10-16", intraday=True)}
    assert record_snapshot(conn, build_wealth(conn, live, D("7.1"), TODAY)) is None          # 盘中价不当收盘价记
    typo = {**QUOTES, "ACME": Quote("ACME", D(110), D(120), "2027-11-20")}                   # 手动行情日期填错
    assert record_snapshot(conn, build_wealth(conn, typo, D("7.1"), TODAY)) is None
    with conn:                                                                               # 以前留下的未来日期快照不再挡住记录
        conn.execute("INSERT INTO nw_snapshots(snap_date, net_worth, assets, liabilities, invested, cash, day_pnl, fx,"
                     " detail, updated_at) VALUES('2027-11-20','1','1','0','1','0','0','7','{}','x')")
    assert record_snapshot(conn, build_wealth(conn, QUOTES, D("7.1"), TODAY)) == "2026-10-16"


def test_past_vest_rows_do_not_hold_unvested_shares(conn):
    _seed(conn, vest=False)
    with conn:
        conn.executemany("INSERT INTO aapl_vest_schedule(expected_date, quantity) VALUES(?,?)",
                         [("2026-09-15", "12"), ("2027-03-15", "12"), ("2027-09-15", "6")])
        conn.execute("UPDATE aapl_holdings SET rs_unvested_shares = '18'")
    vest = {b["bucket"]: b for b in build_wealth(conn, QUOTES, D("7.1"), TODAY).liquidity()}[VESTING]["rows"]
    assert [(r.unlock, r.usd) for r in vest] == [("2027-03-15", D(12) * 300), ("2027-09-15", D(6) * 300)]


def test_pending_outflow_is_not_available_today(conn):
    _seed(conn)
    with conn:
        conn.execute("INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note)"
                     " VALUES('Schwab-123','2026-10-15','2026-10-19','2026-10-19','-400','转到支票账户')")
    w = build_wealth(conn, QUOTES, D("7.1"), TODAY)
    lad = {b["bucket"]: b for b in w.liquidity()}
    assert lad[NOW]["usd"] == D(100) and "待转出" in lad[NOW]["rows"][0].note            # 500 − 400
    assert lad[PENDING]["usd"] == D(2000)                                                    # 在途只列转入
    assert sum((b["usd"] for b in lad.values()), D(0)) == w.assets


def test_funds_are_not_flagged_as_single_stocks(conn):
    from core.db import set_setting
    _seed(conn, vest=False)
    set_setting(conn, "fund_tickers", "ACME")
    top = {t["name"]: t for t in build_wealth(conn, {**QUOTES, "ACME": Quote("ACME", D(5000), D(5000), "2026-10-16")},
                                              D("7.1"), TODAY).concentration()}
    assert top["ACME"]["share"] > D("0.10") and not top["ACME"]["flag"] and top["AAPL"]["flag"]


def test_owner_setting_that_is_not_an_object_falls_back_to_defaults(conn):
    from core.db import set_setting
    from core.wealth import owners
    for bad in ("null", "[1, 2]", '"爸爸"', "{not json"):
        set_setting(conn, "owners", bad)
        assert owners(conn) == {"*": "我", "aapl": "爸爸"}


def test_member_names_come_from_settings(conn):
    """称呼存在设置里：改了以后资产负债表、资金日历都用新称呼；单独指定的归属不受影响。"""
    import json

    from core.agenda import build_events
    from core.db import set_setting
    _seed(conn)
    set_setting(conn, "member_self", "Alex")
    set_setting(conn, "member_father", "David")
    set_setting(conn, "owners", json.dumps({"eb5": "妈妈"}, ensure_ascii=False))
    mem = {g["name"]: g["usd"] for g in build_wealth(conn, QUOTES, D("7.1"), TODAY).group("member")}
    assert set(mem) == {"Alex", "David", "妈妈"} and mem["David"] == D(60) * 300 and mem["妈妈"] == D(800)
    ev = build_events(conn, TODAY, QUOTES, {2026: "nonresident"}, {2026: False})
    tax = {e.member for e in ev if e.kind == "tax"}
    assert tax == {"Alex", "David"} and any(e.title.startswith("David 2026 年境外所得") for e in ev)
    assert "我" not in {e.member for e in ev} and "爸爸" not in {e.member for e in ev}


def test_snapshot_skip_reason_names_why_nothing_was_written(conn):
    _seed(conn)
    reason = lambda q, fx=D("7.1"): snapshot_skip_reason(conn, build_wealth(conn, q, fx, TODAY))  # noqa: E731
    assert reason(QUOTES) is None
    assert reason({k: v for k, v in QUOTES.items() if k != "AAPL"}) == "缺行情：AAPL"
    assert reason(QUOTES, None) == "缺 USD/CNY 汇率"
    assert reason({**QUOTES, "ACME": Quote("ACME", D(90), D(100), "2026-10-16", intraday=True)}) == "持仓里有盘中价，不是收盘价"
    assert reason({**QUOTES, "ACME": Quote("ACME", D(110), D(120), "2027-11-20")}) == "行情日期 2027-11-20 在今天之后"
    assert record_snapshot(conn, build_wealth(conn, QUOTES, D("7.1"), TODAY)) == "2026-10-16"
    old = {k: Quote(q.ticker, q.close, q.prev_close, "2026-10-15") for k, q in QUOTES.items()}
    assert reason(old) == "行情日期 2026-10-15 比已有的最新快照 2026-10-16 还旧"


def _record_script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "record_snapshot.py"
    spec = importlib.util.spec_from_file_location("record_snapshot_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_after_close_script_records_without_browser(conn, monkeypatch):
    import core.market as market

    _seed(conn)
    monkeypatch.setattr(market, "fetch_yfinance", lambda tickers: ({t: QUOTES[t] for t in tickers if t in QUOTES}, []))
    written, done, line = _record_script().record_once(conn, TODAY)
    assert written == "2026-10-16" and done and line.startswith("已记 2026-10-16 的净资产快照")
    assert load_snapshots(conn)[0]["net_worth"] == build_wealth(conn, QUOTES, D("7.1"), TODAY).net_worth
    # 第二天 Yahoo 还没出收盘价：记的是前一天，日志要说清楚不是今天，也不算完成
    written, done, line = _record_script().record_once(conn, date(2026, 10, 19))
    assert written == "2026-10-16" and not done and "不是今天 2026-10-19" in line


def test_after_close_script_reports_why_and_retries(conn, monkeypatch, capsys):
    from core.db import set_setting

    _seed(conn)                                   # 测试不联网（conftest）：没有缓存就缺行情，记不上
    set_setting(conn, "seeded", "1")              # 脚本会调 seed_if_empty，别把 ./data 里的真实数据灌进测试库
    mod = _record_script()
    monkeypatch.setattr(mod, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
    assert mod.main(["--tries", "2", "--wait", "0"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and out[0].startswith("[第 1 次] 没记快照：缺行情：") and "联网取行情失败" in out[0]
    assert load_snapshots(conn) == []


def _dated(day: str, stale: tuple = ()) -> dict:
    """QUOTES 改成某一天的行情；stale 里的代码停在前一个交易日（Yahoo 逐只晚出收盘价）。"""
    return {k: Quote(q.ticker, q.close, q.prev_close, "2000-01-03" if k in stale else day) for k, q in QUOTES.items()}


def test_after_close_script_stops_once_today_is_complete(conn, monkeypatch, capsys):
    import core.market as market
    from core.db import set_setting
    from core.market import ET

    _seed(conn)
    set_setting(conn, "seeded", "1")
    today = datetime.now(ET).date().isoformat()
    calls, sleeps = [], []
    monkeypatch.setattr(market, "fetch_yfinance", lambda tickers: (calls.append(1) or _dated(today), []))
    mod = _record_script()
    monkeypatch.setattr(mod, "time", type("T", (), {"sleep": staticmethod(sleeps.append)}))
    assert mod.main(["--tries", "3", "--wait", "7"]) == 0
    assert len(calls) == 1 and sleeps == []                       # 第一次就记到今天：不再等、不再取
    assert capsys.readouterr().out.startswith(f"[第 1 次] 已记 {today} 的净资产快照")
    assert [s["snap_date"] for s in load_snapshots(conn)] == [today]


def test_after_close_script_retries_while_some_closes_are_stale(conn, monkeypatch, capsys):
    import core.market as market
    from core.db import set_setting
    from core.market import ET

    _seed(conn)
    set_setting(conn, "seeded", "1")
    today = datetime.now(ET).date().isoformat()
    replies = [_dated(today, stale=("AAPL",)), _dated(today)]   # 第一次 AAPL 还是旧收盘价，第二次齐了
    sleeps = []
    monkeypatch.setattr(market, "fetch_yfinance", lambda tickers: (replies.pop(0), []))
    mod = _record_script()
    monkeypatch.setattr(mod, "time", type("T", (), {"sleep": staticmethod(sleeps.append)}))
    assert mod.main(["--tries", "3", "--wait", "7"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and sleeps == [7]
    assert "还不完整" in out[0] and "AAPL（2000-01-03）" in out[0] and "还不完整" not in out[1]
    snap = load_snapshots(conn)[-1]
    assert snap["snap_date"] == today and snap["net_worth"] == build_wealth(conn, _dated(today), D("7.1"), TODAY).net_worth
