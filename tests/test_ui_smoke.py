"""界面冒烟测试：在临时数据库上把每个页面跑一遍，不能抛异常，也不能联网。"""
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["views/overview.py", "views/agenda.py", "views/statement.py", "views/residency.py", "views/federal.py", "views/simulator.py", "views/optimize.py",
         "views/father_cn.py", "views/data_admin.py"]


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    import streamlit as st

    from core.db import connect, set_setting
    from core.market import save_cache
    from core.portfolio import Quote
    from core.money import D

    # 用合成数据而不是 ./data 里的个人数据
    data = tmp_path / "data"
    data.mkdir()
    (data / "initial_lots.csv").write_text(
        "account,ticker,open_date,quantity,cost_per_share,cost_basis\n"
        "Schwab-123,ABC,2024-01-10,10,100,1000\nSchwab-123,ABC,2026-05-01,5,150,750\n"
        "Schwab-123,XYZ,2025-12-02,20,50,1000\n", encoding="utf-8")
    (data / "initial_data.json").write_text(
        '{"eb5": {"principal": "1000", "expected_return_date": "2029-06-30"},'
        ' "aapl_holdings": {"espp_shares": "1", "rs_vested_shares": "2", "rs_unvested_shares": "3"},'
        ' "travel": [{"event_date": "2022-08-25", "direction": "entry", "status": "F-1"},'
        ' {"event_date": "2026-06-28", "direction": "exit", "status": "F-1"},'
        ' {"event_date": "2026-09-15", "direction": "entry", "status": "parole"}],'
        ' "cash_snapshots": [{"account": "Schwab-123", "as_of": "2026-10-14", "amount": "100"}]}', encoding="utf-8")
    db = tmp_path / "t.db"
    monkeypatch.setenv("FAMILY_FINANCE_DB", str(db))
    from core.seed import seed_if_empty
    c = connect(db)
    seed_if_empty(c, data)
    set_setting(c, "offline", "1")
    save_cache(c, {t: Quote(t, D(p), D(p) - 1) for t, p in
                   {"ABC": "120", "XYZ": "60", "AAPL": "300", "CNY=X": "7.1"}.items()})
    c.close()
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


@pytest.mark.parametrize("page", PAGES)
def test_page_runs(app_env, page):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    if page != PAGES[0]:
        at.switch_page(page)
        at.run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.parametrize("page", ["views/overview.py", "views/optimize.py", "views/federal.py"])
def test_pages_run_resident_year_and_edge_data(app_env, page):
    """居民年份（优化提示会显示 0% 档额度）、设了基金代码、现金为负、有一只股票没有行情。"""
    import os

    from streamlit.testing.v1 import AppTest

    from core.db import connect, set_setting
    c = connect(os.environ["FAMILY_FINANCE_DB"])
    set_setting(c, "green_card_date", "2024-06-01")
    set_setting(c, "fund_tickers", "XYZ、QQQ")
    with c:
        c.execute("INSERT INTO lots(account, ticker, open_date, quantity, cost_per_share, cost_basis, source)"
                  " VALUES('Schwab-123','NOQ','2026-01-05','3','10','30','manual')")
        c.execute("INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note)"
                  " VALUES('Schwab-123','2026-10-15','2026-10-16','2026-10-16','-500','转出')")
    c.close()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    if page != "views/overview.py":
        at.switch_page(page)
        at.run()
    assert not at.exception, [e.value for e in at.exception]
    if page == "views/optimize.py":
        assert any("还能实现的长期收益" in str(h.proto) for h in at.get("html"))


def test_sidebar_has_no_appearance_or_color_switches(app_env):
    """外观一律跟随系统、涨跌固定绿涨红跌：侧边栏没有这些开关；以前存过的手动深色和红涨绿跌都不再生效。"""
    import os

    from streamlit.testing.v1 import AppTest

    from core.db import connect, set_setting
    c = connect(os.environ["FAMILY_FINANCE_DB"])
    set_setting(c, "appearance", "dark")
    set_setting(c, "pnl_colors", "red_up")
    c.close()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.sidebar.toggle and not at.sidebar.get("button_group")
    bodies = [h.proto.body for h in at.get("html")] + [h.proto.body for h in at.sidebar.get("html")]
    assert any('var want = "System"' in b for b in bodies)
    assert any("--ff-up: var(--ff-green)" in b for b in bodies)


def _user_data(db: str) -> dict:
    """用户自己录入的数据（不含程序维护的行情缓存和净资产快照）。"""
    from core.db import connect
    c = connect(db)
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
              if r[0] not in ("price_cache", "nw_snapshots")]
    out = {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}
    c.close()
    return out


SESSION_ONLY_BUTTONS = {"刷新行情", "加入篮子", "保存计划买入", "移除所选", "清空篮子"}   # 不写数据库，只读时照常可用


@pytest.mark.parametrize("page", [p for p in PAGES if p != "views/data_admin.py"])
def test_pages_in_read_only_mode(app_env, monkeypatch, page):
    """只读会话：每个页面都能打开；写数据的按钮、表格、开关都被禁用；跑完后用户数据一点没变。"""
    import os

    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("FAMILY_FINANCE_READ_ONLY", "1")
    db = os.environ["FAMILY_FINANCE_DB"]
    before = _user_data(db)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    if page != PAGES[0]:
        at.switch_page(page)
        at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("只读模式" in c.value for c in at.sidebar.caption)
    for b in at.button:
        assert b.disabled or b.label in SESSION_ONLY_BUTTONS, b.label
    if page != "views/simulator.py":                       # 卖出模拟的表格只改当前会话
        for d in at.dataframe:
            assert d.proto.disabled or d.proto.editing_mode == d.proto.EditingMode.READ_ONLY
    assert all(t.disabled for t in at.toggle)
    assert _user_data(db) == before


def test_data_admin_is_hidden_in_read_only_mode(app_env, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("FAMILY_FINANCE_READ_ONLY", "1")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    try:
        at.switch_page("views/data_admin.py")
        at.run()
    except Exception:
        return
    bodies = " ".join(h.proto.body for h in at.get("html"))
    assert "导入 Schwab CSV" not in bodies


def test_access_rules_from_request_headers():
    from ui_common import access_from_headers, parse_logins

    ed = {"me@x.com"}
    serve = {"Host": "mac-studio.tail.ts.net", "Tailscale-Headers-Info": "https://tailscale.com/s/serve-headers",
             "X-Forwarded-For": "100.64.0.2", "X-Forwarded-Host": "mac-studio.tail.ts.net"}
    assert access_from_headers({"Host": "127.0.0.1:8501"}, ed) == (True, "本机")
    assert access_from_headers({"host": "localhost:8501"}, ed)[0] and access_from_headers({"Host": "[::1]:8501"}, ed)[0]
    assert access_from_headers({**serve, "Tailscale-User-Login": "Me@X.com"}, ed) == (True, "me@x.com")
    assert access_from_headers({**serve, "tailscale-user-login": "dad@y.com"}, ed) == (False, "dad@y.com")
    assert access_from_headers(serve, ed) == (False, "访客")                    # 没有身份头（例如带标签的设备）
    assert access_from_headers({**serve, "Host": "127.0.0.1:8501"}, ed)[0] is False   # 经 Serve 来的不算本机
    assert access_from_headers({"Host": "192.168.1.5:8501"}, ed)[0] is False
    assert access_from_headers({}, ed)[0]                                        # 测试环境（没有请求头）
    assert parse_logins("A@x.com， b@y.com;c@z.com  d@w.com") == {"a@x.com", "b@y.com", "c@z.com", "d@w.com"}


@pytest.mark.parametrize("page", ["views/overview.py", "views/statement.py", "views/agenda.py"])
def test_family_office_pages_with_snapshot(app_env, page):
    """行情带日期时 app 会记一条净资产快照；对账单、日历、总览都要能完整渲染（含负债、提醒、只写到月份的计划）。"""
    import os
    from datetime import date

    from streamlit.testing.v1 import AppTest

    from core.db import connect, set_setting
    from core.market import save_cache
    from core.money import D
    from core.portfolio import Quote
    c = connect(os.environ["FAMILY_FINANCE_DB"])
    d = date.today().isoformat()
    save_cache(c, {t: Quote(t, D(p), D(p) - 1, d) for t, p in {"ABC": "120", "XYZ": "60", "AAPL": "300", "CNY=X": "7.1"}.items()})
    set_setting(c, "display_name", "<b>测试</b>")
    with c:
        c.execute("INSERT INTO liabilities(name, member, amount, note) VALUES('<script>x</script>','我','50','')")
        c.execute("INSERT INTO calendar_events(event_date, title, member) VALUES(?, '见 CPA <i>', '我')", (d[:7],))
        c.execute("INSERT INTO cash_plans(label, amount, expected) VALUES('IPO', '300', ?)", (d[:7],))
    c.close()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    if page != "views/overview.py":
        at.switch_page(page)
        at.run()
    assert not at.exception, [e.value for e in at.exception]
    bodies = " ".join(h.proto.body for h in at.get("html"))
    assert "<script>x</script>" not in bodies and "<b>测试</b>" not in bodies and "见 CPA <i>" not in bodies
    if page == "views/statement.py":
        assert "资产变动摘要" in bodies and "期末净资产" in bodies


@pytest.mark.parametrize("page", ["views/overview.py", "views/statement.py", "views/agenda.py"])
def test_html_bodies_survive_the_sanitizer(app_env, page):
    """st.html 在浏览器里会经过 DOMPurify（只保留 HTML，外加 script / style）：<svg> 整个被去掉、onclick 这类属性被去掉。
    AppTest 看到的是过滤前的内容，所以这里直接检查：不能靠这些东西。走势图要有至少两条快照才会画，这里先补上。"""
    import json
    import os
    import re

    from streamlit.testing.v1 import AppTest

    from core.db import connect
    c = connect(os.environ["FAMILY_FINANCE_DB"])
    with c:
        for d, nw in (("2026-09-29", "1000"), ("2026-09-30", "1100")):
            c.execute("INSERT INTO nw_snapshots(snap_date, net_worth, assets, liabilities, invested, cash, day_pnl, fx,"
                      " detail, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (d, nw, nw, "0", nw, "0", "0", "7.1", json.dumps({"items": [], "by_class": {}, "by_member": {}}), "x"))
    c.close()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    if page != "views/overview.py":
        at.switch_page(page)
        at.run()
    if page == "views/statement.py":
        at.selectbox(key="stmt_month").set_value("2026-09").run()
    assert not at.exception, [e.value for e in at.exception]
    for h in [*at.get("html"), *at.sidebar.get("html")]:
        assert "<svg" not in h.proto.body.lower(), h.proto.body[:200]
        assert not re.search(r"\son[a-z]+\s*=", h.proto.body, re.I), h.proto.body[:200]
    if page in ("views/overview.py", "views/statement.py"):
        trend = [m.value for m in at.markdown if "ff-trend" in m.value]
        assert trend and "<svg" in trend[0] and "$" not in trend[0] and "&#36;" in trend[0]


def test_theme_style_block_has_no_tag_like_text():
    """注入的样式里不能有「小于号 + 字母或斜杠」：st.html 的过滤器见到就把整个 style 标签删掉，整站失去样式。
    （2026-11-11 @property 的 syntax: "<angle>" 就这样把样式弄没了，现在写成 CSS 转义。）"""
    import re

    import ui_theme

    css = ui_theme._ornament_css() + (ROOT / "assets" / "theme.css").read_text(encoding="utf-8")
    assert not re.search(r"<[/\w!?]", css), re.search(r".{40}<[/\w!?].{20}", css, re.S)


def test_face_value_is_solid_and_paper_is_patterned():
    """2026-11-11 用户嫌首屏数字太花、背景不够花：数字是实心墨色（不再用背景裁成文字的凹版条纹、描边和压印高光），
    整页、凭证、息票、区块标题都有纹样，而且打印时这些纹样都隐藏。"""
    import re

    import ui_theme

    css = (ROOT / "assets" / "theme.css").read_text(encoding="utf-8")
    assert "background-clip: text" not in css and "text-stroke" not in css and "--ff-emboss" not in css
    assert "intaglio" not in css
    orn = ui_theme._ornament_css()
    for var in ("--ff-field-tile", "--ff-sunburst", "--ff-pfibers-light", "--ff-pfibers-dark"):
        assert var + ": url(" in orn
    for sel in (".stApp::before", ".stApp::after", '[data-testid="stAppViewContainer"]::before', ".ff-sec::after",
                ".ff-fr .fld", ".ff-cert .sun", ".ff-cert .ros2", ".ff-coupon .wm"):
        assert sel in css, sel
    # 凭证是不透明的钞纸，四角纹章的底色跟着它
    assert re.search(r"\.ff-cert \{[^}]*background: var\(--ff-coupon-bg\)[^}]*--fbg: var\(--ff-coupon-bg\)", css)
    # 打印时整页底纹和区块标题的波纹带都不印
    pr = css[css.index("@media print"):]
    pr = pr[:pr.index("@media (prefers-reduced-motion")]
    for sel in (".stApp::before", ".stApp::after", '[data-testid="stAppViewContainer"]::before', ".ff-sec::after"):
        assert sel in pr, sel
    for t in (ui_theme.LIGHT, ui_theme.DARK):
        assert "hatch_gap" not in t and "emboss" not in t
        for k in ("field", "note_field", "sunburst", "wmark", "pfib_op", "pfibers"):
            assert k in t, k


def test_field_tile_repeats_without_seams():
    """车花网一格：横向一个周期、纵向 rows 行一个周期，平铺时上下左右都接得上。"""
    import math

    w, sp, rows = 120, 7, 26
    for fam in (1, -1):
        for k in range(rows):
            amp = lambda kk: sp * (1.15 + 0.85 * math.sin(2 * math.pi * kk / rows))
            ph = lambda kk: fam * 2 * math.pi * kk / rows
            y = lambda kk, x: kk * sp + amp(kk) * math.sin(2 * math.pi * x / w + ph(kk))
            for x in (0, 17, 60):
                assert abs(y(k, x) - y(k, x + w)) < 1e-9
                assert abs(y(k + rows, x) - (y(k, x) + sp * rows)) < 1e-9


def test_odometer_rests_on_the_real_digits():
    """首屏数字滚轮：读屏文字是原金额；每一格的静止数字（--d）连起来就是原来的数字。"""
    import re
    from decimal import Decimal

    import ui_theme

    h = ui_theme._big_usd(Decimal("-1234567.89"))
    assert '<span class="ff-sr">−$1,234,567.89</span>' in h
    assert "".join(re.findall(r"--d:(\d)", h)) == "123456789"
    assert [int(i) for i in re.findall(r"--i:(\d+)", h)] == list(range(9))


def test_overview_coupon_for_plan_with_milestones(app_env):
    """有关键节点的资金计划在总览里画成息票：倒计时有服务器端的初值和 data-deadline，脚本里没有会被过滤的尖括号。"""
    import os
    import re
    from datetime import datetime, timedelta

    from streamlit.testing.v1 import AppTest

    from core.db import connect
    from core.market import ET

    soon = datetime.now(ET) + timedelta(days=3)
    c = connect(os.environ["FAMILY_FINANCE_DB"])
    with c:
        c.execute("INSERT INTO cash_plans(label, amount, expected, note, milestones) VALUES(?,?,?,?,?)",
                  ("参与测试 IPO", "80", f"{soon + timedelta(days=1):%Y-%m-%d}", "测试",
                   f"{soon:%Y-%m-%d} 16:00 ET 改单截止，之前可增可减\n{soon + timedelta(days=1):%Y-%m-%d} 定价"))
    c.close()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    body = next(h.proto.body for h in at.get("html") if "ff-coupon" in h.proto.body)
    assert "data-deadline=" in body and "距改单截止" in body and "够付这笔" in body
    script = body.split("<script>", 1)[1]
    assert not re.search(r"<[/\w!](?!/script>)", script.replace("</script>", ""))


def test_saving_plans_in_data_admin_keeps_milestones(app_env):
    """数据管理里点「保存转账和计划」会整表重写 cash_plans：关键节点必须原样保留，不能被清掉。"""
    import os

    from streamlit.testing.v1 import AppTest

    from core.db import connect, rows

    ms = "2026-12-08 16:00 ET 改单截止，之前可增可减\n2026-12-09 定价"
    c = connect(os.environ["FAMILY_FINANCE_DB"])
    with c:
        c.execute("INSERT INTO cash_plans(label, amount, expected, note, milestones) VALUES('测试计划','80','2026-12-09','n',?)", (ms,))
    c.close()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.switch_page("views/data_admin.py")
    at.run()
    next(b for b in at.button if b.label == "保存转账和计划").click().run()
    assert not at.exception, [e.value for e in at.exception]
    c = connect(os.environ["FAMILY_FINANCE_DB"])
    got = rows(c, "SELECT label, milestones FROM cash_plans")
    c.close()
    assert got == [{"label": "测试计划", "milestones": ms}]


def test_mobile_snapshot_matches_the_web(app_env):
    """手机 app 的快照：数字用网页同一套格式化，占比和网页的资产构成逐位一致；有关键节点的计划单独成息票，不在事项里重复。"""
    import json
    import os
    from datetime import datetime, timedelta

    from core.agenda import build_events
    from core.db import connect
    from core.market import ET, load_cache
    from core.mobile import build_mobile_snapshot
    from core.money import fmt_usd
    from core.wealth import build_wealth
    from ui_theme import _shares

    c = connect(os.environ["FAMILY_FINANCE_DB"])
    now = datetime.now(ET)
    soon = now + timedelta(days=3)
    with c:
        c.execute("INSERT INTO cash_plans(label, amount, expected, milestones) VALUES(?,?,?,?)",
                  ("测试 IPO", "80", f"{soon:%Y-%m-%d}", f"{soon:%Y-%m-%d} 16:00 ET 改单截止"))
    from dataclasses import replace
    quotes = {t: replace(q, quote_date=now.date().isoformat()) for t, q in load_cache(c).items()}   # 合成行情补上日期
    w = build_wealth(c, quotes, quotes["CNY=X"].close, now.date())
    snap = build_mobile_snapshot(c, w, quotes, now.date(), now, build_events(c, now.date(), quotes, {}, {}))
    c.close()
    json.dumps(snap, ensure_ascii=False)                      # 能直接写成 JSON
    fam = snap["certificates"][0]
    assert fam["net"]["usd"] == fmt_usd(w.net_worth) and fam["key"] == "family"
    comp = snap["composition"]["按资产类别"]
    assert [g["bp"] for g in comp] == _shares([g["usd"] for g in w.group("asset_class")]) and sum(g["bp"] for g in comp) == 10000
    assert [p["label"] for p in snap["plans"]] == ["测试 IPO"]
    assert snap["plans"][0]["milestones"][0]["at"].endswith("-05:00") or snap["plans"][0]["milestones"][0]["at"].endswith("-04:00")
    assert not any(e["title"] == "测试 IPO" for e in snap["events"])
    assert {g["key"] for g in snap["holdings"]} >= {"aapl", "eb5", "cash"}
