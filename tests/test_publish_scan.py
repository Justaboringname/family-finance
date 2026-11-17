"""发布前拿真实数据库反查（scripts/publish_scan.py）：照搬进文件的真实记录要查得出来，编的不能误报。"""
import importlib.util
from pathlib import Path

from core.db import connect

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("publish_scan", ROOT / "scripts" / "publish_scan.py")
publish_scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish_scan)


def _db(tmp_path):
    db = tmp_path / "real.db"
    if db.exists():
        return db
    c = connect(db)
    with c:
        c.execute("INSERT INTO lots(account, ticker, open_date, quantity, cost_per_share, cost_basis, source, as_of)"
                  " VALUES('Schwab-123','QQQQ','2031-02-03','70','41.37','2895.9','manual','2031-05-05')")
        c.execute("INSERT INTO travel(event_date, direction, status, planned, note) VALUES('2031-04-09','exit','F-1',0,'')")
        c.execute("INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note)"
                  " VALUES('Schwab-123','2031-06-01','2031-06-03','2031-06-08','20000','从某某银行转入')")
        c.execute("INSERT INTO cash_snapshots(account, as_of, amount, note) VALUES('Schwab-123','2031-05-05','500','通用的标签文字')")
        c.execute("INSERT INTO nw_snapshots(snap_date, net_worth, assets, liabilities, invested, cash, day_pnl, fx, detail, updated_at)"
                  " VALUES('2031-05-05','3456789.12','3456789.12','0','3400000','0','0','7.1234',?,'x')", ('{"a": "987654.32"}',))
    c.close()
    return db


def _hits(tmp_path, text, ignore=frozenset()):
    tree = tmp_path / "tree"
    tree.mkdir(exist_ok=True)
    (tree / "t.py").write_text(text, encoding="utf-8")
    return publish_scan.scan(tree, _db(tmp_path), set(ignore))


def test_real_rows_are_found_in_any_spelling(tmp_path):
    hits = _hits(tmp_path, "\n".join([
        'TravelEvent(date(2031, 4, 9), "exit", "F-1")',                 # 出入境日期，Python 写法
        '"04/09/2031","1"',                                             # 同一天，斜杠写法
        'Lot(1, "A", "QQQQ", date(2031, 2, 3), D(70), D(50), D(3500))',  # 代码 + 买入日
        'x = D("41.37")',                                                # 成本价单独出现
        "VALUES('2031-06-01','2031-06-03','2031-06-09')",                # 同一笔转账的两个日期
        "note = '从某某银行转入'",
        '"""$3,456,789.55 → 大号数字"""',                                # 净资产：带千分位、改了几分钱
        "x = 987654.32",                                                 # 快照明细里的数
    ]))
    assert [h.split(" ")[0] for h in hits] == [f"t.py:{i}" for i in range(1, 9)]


def test_made_up_values_and_generic_dates_pass(tmp_path):
    clean = "\n".join([
        'Lot(1, "A", "QQQQ", date(2031, 2, 4), D(70), D(50), D(3500))',  # 同一个代码，日期和成本都是编的
        'Lot(1, "A", "ABC", date(2031, 2, 3), D(70), D(50), D(3500))',   # 同一天，别的代码
        'TODAY = date(2031, 5, 5)',                                      # 导入日期、快照日期不算个人信息
        "VALUES('2031-06-01','2031-06-02','2031-06-09')",                # 只撞上一个日期
        "amount = 20000; year = 2031; fx = 7.1234; snap = '2031-05-05'",
        "x = 13456789.12 + 3456.78",                                     # 只是长得像，不是那个数
    ])
    assert _hits(tmp_path, clean) == []
    assert len(_hits(tmp_path, 'label = "通用的标签文字"')) == 1
    assert _hits(tmp_path, 'label = "通用的标签文字"', {"通用的标签文字"}) == []
