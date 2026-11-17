"""资金日历：报税截止日（带出处、周末顺延）、只写到月份的计划、归属、EB-5、转长期。"""
from datetime import date

from core.agenda import build_events, next_business_day, tax_deadlines
from core.money import D
from core.portfolio import Quote

TODAY = date(2026, 10, 16)


def test_weekend_and_emancipation_day_roll_forward():
    # 和 IRS 实际公布的截止日对照：2022、2023 年都是 4 月 18 日
    assert next_business_day(date(2022, 4, 15)) == date(2022, 4, 18)      # 4/15 周五是解放日的放假日
    assert next_business_day(date(2023, 4, 15)) == date(2023, 4, 18)      # 周六 → 周一 4/17 放假 → 周二
    assert next_business_day(date(2028, 4, 15)) == date(2028, 4, 18)      # 同 2023
    assert next_business_day(date(2027, 4, 15)) == date(2027, 4, 15)      # 周四不变
    assert next_business_day(date(2024, 4, 15)) == date(2024, 4, 15)      # 解放日在 4/16 周二，不影响


def test_tax_deadlines_follow_status_and_carry_sources():
    nra = tax_deadlines(2026, "nonresident", has_wages=False, father_has_overseas=True)
    us = [e for e in nra if e.member == "我"]
    assert [(e.start, "8843" in e.title) for e in us] == [(date(2027, 6, 15), True)]
    assert all(e.source and e.source.startswith("https://") for e in nra)
    cn = next(e for e in nra if e.member == "爸爸")
    assert (cn.start, cn.end) == (date(2027, 3, 1), date(2027, 6, 30)) and "律所解读" in cn.note
    res = tax_deadlines(2027, "resident", has_wages=False, father_has_overseas=False)
    f1040 = next(e for e in res if "1040" in e.title)
    fbar = next(e for e in res if "FBAR" in e.title)
    assert f1040.start == date(2028, 4, 18) and fbar.end == date(2028, 10, 16)   # 2028-04-15 周六、10-15 周日
    wages = tax_deadlines(2026, "nonresident", has_wages=True, father_has_overseas=False)
    assert wages[0].start == date(2027, 4, 15)


def test_build_events_collects_sources(conn):
    with conn:
        conn.execute("INSERT INTO cash_plans(label, amount, expected, note) VALUES('参与某公司 IPO','50000','2026-11','')")
        conn.execute("INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note)"
                     " VALUES('Schwab-123','2026-10-15','2026-10-16','2026-10-22','20000','从支票账户转入')")
        conn.execute("INSERT INTO aapl_holdings(id, espp_shares, rs_vested_shares, rs_unvested_shares) VALUES(1,'1','2','3')")
        conn.execute("INSERT INTO aapl_vest_schedule(expected_date, quantity) VALUES('2027-03-15','3')")
        conn.execute("INSERT INTO eb5(id, principal, expected_return_date) VALUES(1,'800000','2029-06-30')")
        conn.execute("INSERT INTO lots(account, ticker, open_date, quantity, cost_per_share, cost_basis) "
                     "VALUES('Schwab-123','ACME','2025-12-10','100','180','18000')")
        conn.execute("INSERT INTO calendar_events(event_date, title, member, amount, note) VALUES('2026-12-01','见 CPA','我',NULL,'')")
    quotes = {"AAPL": Quote("AAPL", D(300), D(299), "2026-10-16")}
    ev = build_events(conn, TODAY, quotes, {2026: "nonresident"}, {2026: False})
    titles = [e.title for e in ev]
    assert ev == sorted(ev, key=lambda e: e.sort_key)
    plan = next(e for e in ev if e.title == "参与某公司 IPO")
    assert plan.month_only and plan.start == date(2026, 11, 1) and plan.usd == D(50000)
    assert any("从支票账户转入" in t for t in titles)
    vest = next(e for e in ev if e.kind == "vest")
    assert vest.member == "爸爸" and vest.usd == D(900)
    assert any(e.title == "EB-5 预计回款" for e in ev)                     # 超出 18 个月也显示
    lt = next(e for e in ev if e.kind == "lot")
    assert lt.start == date(2026, 12, 11) and "ACME 1 批" in lt.title and "12-11 ACME 100 股" in lt.note
    assert any(e.title == "见 CPA" and e.custom_id for e in ev)
    assert any(e.kind == "tax" and e.member == "爸爸" for e in ev)


def test_first_year_choice_year_files_extension_first():
    """靠 First-Year Choice 成为双重身份的年份：4/15 交 Form 4868，满足次年 SPT 后才交 1040（Pub 519）。"""
    ev = tax_deadlines(2026, "dual_status", has_wages=False, father_has_overseas=False,
                       first_year_choice=True, next_spt_met=date(2027, 7, 2))
    ext = next(e for e in ev if "4868" in e.title)
    assert ext.start == date(2027, 4, 15) and "p519" in ext.source
    file_ = next(e for e in ev if "First-Year Choice 声明" in e.title)
    assert (file_.start, file_.end) == (date(2027, 7, 2), date(2027, 10, 15))
    assert not any(e.title.endswith("（1040）") for e in ev)
    late = tax_deadlines(2026, "dual_status", False, False, first_year_choice=True, next_spt_met=date(2027, 11, 1))
    assert any(e.start == date(2027, 10, 15) and "CPA" in e.note for e in late)
    plain = tax_deadlines(2026, "dual_status", False, False)                # 真正的双重身份年：照常 4/15 交 1040
    assert any(e.title.endswith("（1040）") and e.start == date(2027, 4, 15) for e in plain)


def test_month_only_events_stay_for_the_whole_month(conn):
    with conn:
        conn.execute("INSERT INTO cash_plans(label, amount, expected, note) VALUES('参与某公司 IPO','50000','2026-11','')")
        conn.execute("INSERT INTO calendar_events(event_date, title) VALUES('2026-11', '交学费')")
    for today, shown in ((date(2026, 11, 1), True), (date(2026, 11, 15), True), (date(2026, 11, 30), True),
                         (date(2026, 12, 1), False)):
        titles = [e.title for e in build_events(conn, today, {}, {}, {})]
        assert ("参与某公司 IPO" in titles and "交学费" in titles) is shown, today


def test_vest_events_match_balance_sheet_and_outflows_read_as_outflows(conn):
    with conn:
        conn.execute("INSERT INTO aapl_holdings(id, espp_shares, rs_vested_shares, rs_unvested_shares) VALUES(1,'0','12','5')")
        conn.executemany("INSERT INTO aapl_vest_schedule(expected_date, quantity) VALUES(?,?)",
                         [("2026-09-15", "12"), ("2027-03-15", "12"), ("2027-09-15", "12")])
        conn.execute("INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note)"
                     " VALUES('Schwab-123','2026-10-15','2026-10-19','2026-10-19','-4000','转到支票账户')")
    ev = build_events(conn, TODAY, {"AAPL": Quote("AAPL", D(300), D(299), "2026-10-16")}, {}, {})
    vest = [(e.start, e.usd) for e in ev if e.kind == "vest"]
    assert vest == [(date(2027, 3, 15), D(1500))]                           # 过期的行不占用，合计按未归属 5 股截断
    out = next(e for e in ev if e.kind == "cash")
    assert out.start == date(2026, 10, 19) and "转出" in out.title and "可以使用" not in out.title


def test_father_window_follows_that_years_income_not_current_holdings(conn):
    with conn:
        conn.execute("INSERT INTO aapl_holdings(id, espp_shares, rs_vested_shares, rs_unvested_shares) VALUES(1,'0','0','0')")
        conn.execute("INSERT INTO aapl_sales(sale_date, quantity, price) VALUES('2026-05-01','10','200')")
    ev = build_events(conn, TODAY, {}, {2025: "nonresident", 2026: "nonresident"}, {})
    cn = [e for e in ev if e.member == "爸爸" and e.kind == "tax"]
    assert [(e.start, e.end) for e in cn] == [(date(2027, 3, 1), date(2027, 6, 30))]   # 2026 年卖光了也要报


def test_plan_milestones_parse_and_next():
    """资金计划的关键节点：美东时间、只写日期的算到当天结束、按时间排序；看不懂的写法报错。"""
    from datetime import datetime

    import pytest

    from core.agenda import next_milestone, parse_milestones
    from core.market import ET

    ms = parse_milestones("2026-12-09 定价；2026-12-08 16:00 ET 改单截止，之前可增可减\n2026-12-10 7:00 ET 确认截止")
    assert [(m.when.strftime("%m-%d %H:%M"), m.has_time, m.short) for m in ms] == [
        ("12-08 16:00", True, "改单截止"), ("12-09 00:00", False, "定价"), ("12-10 07:00", True, "确认截止")]
    assert ms[0].when.utcoffset().total_seconds() == -5 * 3600        # 12 月是标准时间
    at = lambda *a: datetime(*a, tzinfo=ET)
    assert next_milestone(ms, at(2026, 12, 2, 16)).short == "改单截止"
    assert next_milestone(ms, at(2026, 12, 8, 16, 0, 1)).short == "定价"      # 截止那一刻之后
    assert next_milestone(ms, at(2026, 12, 9, 23, 59)).short == "定价"        # 只写日期的，当天都算没过
    assert next_milestone(ms, at(2026, 12, 10, 7, 1)) is None
    assert parse_milestones(None) == [] and parse_milestones("  \n ") == []
    with pytest.raises(ValueError):
        parse_milestones("下周三 定价")
