"""示例数据（core/demo.py）：虚构家庭，给手机 app 的展示模式和 ./run.sh --demo 用。不联网，不读 ./data。"""
from datetime import date, datetime

import pytest

from core.demo import ACCOUNT, build_demo_db, demo_quotes, demo_snapshot
from core.market import ET, FX_TICKER
from core.wealth import build_wealth, load_snapshots

TODAY = date(2026, 11, 17)
NOW = datetime(2026, 11, 17, 18, 0, tzinfo=ET)


def test_demo_snapshot_is_complete_and_marked(tmp_path):
    s = demo_snapshot(tmp_path / "demo.db", NOW)
    assert s["demo"] is True and s["version"] == 1 and s["as_of"] == "2026-11-17"
    fam = s["certificates"][0]
    assert fam["key"] == "family" and fam["net"]["v"] > 1_000_000
    assert sum(g["bp"] for g in s["composition"]["按资产类别"]) == 10000
    assert len(s["trend"]) > 100 and s["trend"][-1]["date"] == "2026-11-17"
    assert abs(s["trend"][-1]["v"] - fam["net"]["v"]) < 0.01          # 走势最后一点就是今天的净资产
    assert {g["key"] for g in s["holdings"]} >= {f"{ACCOUNT}:股票", f"{ACCOUNT}:基金", "aapl", "eb5", "cash"}
    plan = s["plans"][0]                                              # 关键节点都在今天之后，展示时倒计时在走
    assert [m["state"] for m in plan["milestones"]] == ["now", "", ""] and plan["cash_ok"]
    assert s["events"]


def test_demo_is_deterministic_and_follows_the_date(tmp_path):
    a = demo_snapshot(tmp_path / "a.db", NOW)
    b = demo_snapshot(tmp_path / "b.db", NOW)
    assert a == b
    later = demo_snapshot(tmp_path / "c.db", datetime(2026, 11, 24, 18, 0, tzinfo=ET))
    assert later["as_of"] == "2026-11-24" and later["certificates"][0]["net"]["v"] != a["certificates"][0]["net"]["v"]
    assert later["plans"][0]["milestones"][0]["state"] == "now"       # 一周后再生成，节点还是在后面


def test_demo_quotes_weekend_uses_friday():
    q = demo_quotes(date(2026, 11, 15))                               # 周日
    assert q["NVDA"].quote_date == "2026-11-13" and q[FX_TICKER].close > 0
    assert q["NVDA"] == demo_quotes(date(2026, 11, 14))["NVDA"]


def test_demo_db_history_matches_wealth(tmp_path):
    conn = build_demo_db(tmp_path / "demo.db", TODAY)
    q = demo_quotes(TODAY)
    w = build_wealth(conn, q, q[FX_TICKER].close, TODAY)
    snaps = load_snapshots(conn)
    assert not w.missing and snaps[-1]["net_worth"] == w.net_worth
    assert all(a["snap_date"] < b["snap_date"] for a, b in zip(snaps, snaps[1:]))
    conn.close()


@pytest.mark.parametrize("page", ["views/overview.py", "views/agenda.py", "views/statement.py", "views/residency.py",
                                  "views/federal.py", "views/simulator.py", "views/optimize.py", "views/father_cn.py",
                                  "views/data_admin.py"])
def test_every_page_runs_on_demo_db(tmp_path, monkeypatch, page):
    """./run.sh --demo：每个页面都能在示例数据库上打开（离线，不联网）。"""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    from tests.test_ui_smoke import ROOT

    db = tmp_path / "demo.db"
    build_demo_db(db).close()
    monkeypatch.setenv("FAMILY_FINANCE_DB", str(db))
    st.cache_resource.clear()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    if page != "views/overview.py":
        at.switch_page(page)
        at.run()
    st.cache_resource.clear()
    assert not at.exception, [e.value for e in at.exception]
