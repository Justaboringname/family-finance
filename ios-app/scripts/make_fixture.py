"""生成 iOS 测试用的合成快照（不含任何真实数据）：ios-app/Tests/Fixtures/snapshot-synthetic.json。
用法：.venv/bin/python ios-app/scripts/make_fixture.py   （改了 core/mobile.py 的格式后重跑）"""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

tmp = Path(tempfile.mkdtemp())
os.environ["FAMILY_FINANCE_DB"] = str(tmp / "t.db")
data = tmp / "data"
data.mkdir()
(data / "initial_lots.csv").write_text(
    "account,ticker,open_date,quantity,cost_per_share,cost_basis\n"
    "Schwab-123,ABC,2024-01-10,10,100,1000\nSchwab-123,ABC,2026-05-01,5,150,750\n"
    "Schwab-123,XYZ,2025-12-02,20,50,1000\n", encoding="utf-8")
(data / "initial_data.json").write_text(
    '{"eb5": {"principal": "1000", "expected_return_date": "2029-06-30"},'
    ' "aapl_holdings": {"espp_shares": "1", "rs_vested_shares": "2", "rs_unvested_shares": "3"},'
    ' "cash_snapshots": [{"account": "Schwab-123", "as_of": "2026-10-14", "amount": "100"}]}', encoding="utf-8")

from core.agenda import build_events  # noqa: E402
from core.db import connect  # noqa: E402
from core.market import ET, save_cache  # noqa: E402
from core.mobile import build_mobile_snapshot  # noqa: E402
from core.money import D  # noqa: E402
from core.portfolio import Quote  # noqa: E402
from core.seed import seed_if_empty  # noqa: E402
from core.wealth import build_wealth  # noqa: E402

c = connect(tmp / "t.db")
seed_if_empty(c, data)
now = datetime(2026, 11, 11, 18, 0, tzinfo=ET)
quotes = {t: Quote(t, D(p), D(p) - 1, "2026-11-11") for t, p in {"ABC": "120", "XYZ": "60", "AAPL": "300", "CNY=X": "7.1"}.items()}
save_cache(c, quotes)
with c:
    c.execute("INSERT INTO cash_plans(label, amount, expected, note, milestones) VALUES(?,?,?,?,?)",
              ("参与测试 IPO", "80", "2026-12-09", "合成数据",
               "2026-12-08 16:00 ET 改单截止，之前可增可减\n2026-12-09 定价\n2026-12-10 07:00 ET 确认截止"))
    for d, v in (("2026-11-09", "9000"), ("2026-11-10", "9100"), ("2026-11-11", "9050")):
        c.execute("INSERT INTO nw_snapshots(snap_date, net_worth, assets, liabilities, invested, cash, day_pnl, fx, detail, updated_at)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?)", (d, v, v, "0", v, "0", "0", "7.1", "{}", "x"))
w = build_wealth(c, quotes, D("7.1"), now.date())
snap = build_mobile_snapshot(c, w, quotes, now.date(), now, build_events(c, now.date(), quotes, {}, {}))
out = ROOT / "ios-app" / "Tests" / "Fixtures" / "snapshot-synthetic.json"
out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
print(out, snap["certificates"][0]["net"]["usd"])
