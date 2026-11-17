"""示例数据：一个虚构家庭的资产，全部是编的。

两处用：
- 手机 app 的「展示模式」：scripts/make_demo.py --snapshot 导出成快照，打包进 app；
- 没有自己数据的人试用网站：./run.sh --demo。

代码是真实公司的，股数、成本、余额都不是谁的真实持仓。价格由下面的基准价按日期做确定性的随机游走
（同一天、同一只股票，谁算都一样），不联网。所有日期都相对「今天」生成，哪天看都像刚更新过。
不依赖 Streamlit。
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from .agenda import build_events
from .db import connect, set_setting
from .market import ET, FX_TICKER, save_cache
from .mobile import build_mobile_snapshot
from .money import D
from .params import available_years
from .portfolio import Quote
from .seed import seed_if_empty
from .wealth import build_wealth, record_snapshot

ACCOUNT = "Schwab-123"
EPOCH = date(2026, 1, 2)          # 随机游走的起点；改它会改掉所有示例价格
# 代码 → (起点价格, 日波动)。只是让数量级看着合理，不是任何一天的真实行情
BASE = {
    "NVDA": (142.0, 0.022), "MSFT": (488.0, 0.012), "GOOG": (251.0, 0.015), "AMZN": (236.0, 0.016),
    "TSM": (318.0, 0.019), "COST": (952.0, 0.010), "VOO": (641.0, 0.008), "SGOV": (100.40, 0.00008),
    "AAPL": (318.0, 0.013), FX_TICKER: (6.82, 0.0012),
}
DRIFT = 0.0004                    # 趋势线每个交易日的涨幅
FLAT = {"SGOV", FX_TICKER}       # 这两个没有趋势，只在原地小幅波动
# 持仓批次：代码、买入日（今天往前多少天）、股数、每股成本
LOTS = [
    ("NVDA", 900, "120", "48.50"), ("NVDA", 420, "60", "118.20"), ("NVDA", 200, "40", "128.75"),
    ("MSFT", 1000, "80", "318.40"), ("MSFT", 310, "30", "441.10"),
    ("GOOG", 760, "150", "131.25"), ("GOOG", 330, "50", "176.60"),
    ("AMZN", 820, "90", "127.80"),
    ("TSM", 880, "110", "96.30"), ("TSM", 150, "40", "241.50"),
    ("COST", 700, "25", "612.00"),
    ("VOO", 950, "260", "412.35"), ("VOO", 380, "120", "518.90"),
    ("SGOV", 75, "600", "100.31"),
]
HISTORY_DAYS = 180                # 净资产走势往回算多少天


def _weekday(d: date) -> date:
    """周末挪到之前的周五（示例数据不管节假日）。"""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _prices(ticker: str, last: date) -> dict[date, Decimal]:
    """从 EPOCH 到 last 每个工作日的示例收盘价：沿一条缓慢上行的趋势线上下波动（偏离会慢慢拉回，不会越走越离谱）。"""
    base, vol = BASE[ticker]
    drift = 0.0 if ticker in FLAT else DRIFT
    out, d, n, x = {}, EPOCH, 0, 0.0
    places = Decimal("0.0001") if ticker == FX_TICKER else Decimal("0.01")
    while d <= last:
        if d.weekday() < 5:
            if d > EPOCH:
                n += 1
                x = 0.97 * x + random.Random(f"{ticker}:{d.isoformat()}").gauss(0, vol)
            out[d] = Decimal(str(base * math.exp(drift * n + x))).quantize(places)
        d += timedelta(days=1)
    return out


def demo_quotes(today: date, series: dict[str, dict[date, Decimal]] | None = None) -> dict[str, Quote]:
    """today 当天（或之前最近一个工作日）的示例行情，含 USD/CNY。"""
    qd = max(_weekday(today), EPOCH + timedelta(days=3))
    series = series or {t: _prices(t, qd) for t in BASE}
    out = {}
    for t, s in series.items():
        days = [d for d in s if d <= qd]
        out[t] = Quote(t, s[days[-1]], s[days[-2]], days[-1].isoformat(), False, "示例")
    return out


def build_demo_db(path: str | Path, today: date | None = None):
    """在 path 新建一个示例数据库（已有就覆盖），返回连接。"""
    today = today or datetime.now(ET).date()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for f in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        f.unlink(missing_ok=True)
    conn = connect(path)
    seed_if_empty(conn, path.parent / "no-initial-data")       # 只写默认设置
    for k, v in {"offline": "1", "fund_tickers": "VOO", "demo": "1"}.items():
        set_setting(conn, k, v)

    def day(n: int) -> str:
        return (today + timedelta(days=n)).isoformat()

    with conn:
        for ticker, ago, qty, cps in LOTS:
            conn.execute(
                "INSERT INTO lots(account, ticker, open_date, quantity, cost_per_share, cost_basis, source) VALUES(?,?,?,?,?,?,?)",
                (ACCOUNT, ticker, _weekday(today - timedelta(days=ago)).isoformat(), qty, cps, str(D(qty) * D(cps)), "manual"))
        conn.execute("INSERT INTO cash_snapshots(account, as_of, amount, note) VALUES(?,?,?,?)",
                     (ACCOUNT, day(-20), "86420.55", "现金及现金类资产"))
        conn.execute("INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note) VALUES(?,?,?,?,?,?)",
                     (ACCOUNT, day(-1), day(1), day(4), "25000", "从支票账户转入"))
        conn.execute("INSERT INTO aapl_holdings(id, espp_shares, rs_vested_shares, rs_unvested_shares, note) VALUES(1,?,?,?,?)",
                     ("186.4210", "1450", "320", "示例数据"))
        for n in (70, 250, 435, 615):
            conn.execute("INSERT INTO aapl_vest_schedule(expected_date, quantity, note) VALUES(?,?,?)", (day(n), "80", ""))
        conn.execute("INSERT INTO eb5(id, principal, expected_return_date, returned, note) VALUES(1,?,?,0,?)",
                     ("800000", date(today.year + 2, 6, 30).isoformat(), "示例数据"))
        conn.execute(
            "INSERT INTO cash_plans(label, amount, expected, note, milestones) VALUES(?,?,?,?,?)",
            ("参与某公司 IPO", "60000", day(9), "示例：按发行区间上限预估，结算前账户里要有足够现金",
             f"{day(8)} 16:00 ET 改单截止，之前可增可减\n{day(9)} 定价\n{day(10)} 07:00 ET 确认截止"))
        conn.execute("INSERT INTO liabilities(name, member, amount, note) VALUES(?,?,?,?)",
                     ("信用卡账单", "我", "4218.37", "本期未出账单"))
        conn.execute("INSERT INTO calendar_events(event_date, title, member, amount, note) VALUES(?,?,?,?,?)",
                     (day(21), "见 CPA，核对全年预估税", "我", None, ""))
        conn.execute("INSERT INTO calendar_events(event_date, title, member, amount, note) VALUES(?,?,?,?,?)",
                     (day(48), "家庭保险年度续费", "我", "4800", ""))
        # 一条很早的入境记录：税务身份页按美国税务居民算
        conn.execute("INSERT INTO travel(event_date, direction, status, planned, note) VALUES(?,?,?,0,?)",
                     ("2015-08-18", "entry", "其他", "示例数据"))

    qd = date.fromisoformat(demo_quotes(today)[FX_TICKER].quote_date)
    series = {t: _prices(t, qd) for t in BASE}
    # 净资产走势：过去每个工作日按当天的示例行情各算一次
    d = max(qd - timedelta(days=HISTORY_DAYS), EPOCH + timedelta(days=3))
    while d <= qd:
        if d.weekday() < 5:
            q = demo_quotes(d, series)
            record_snapshot(conn, build_wealth(conn, q, q[FX_TICKER].close, d), datetime.combine(d, time(16, 10)))
        d += timedelta(days=1)
    save_cache(conn, demo_quotes(today, series))
    return conn


def demo_events(conn, today: date, quotes: dict[str, Quote]) -> list:
    """示例家庭的资金日历：两个纳税年度都按美国税务居民。"""
    years = [y for y in (today.year - 1, today.year) if y in available_years()]
    return build_events(conn, today, quotes, {y: "resident" for y in years}, {})


def demo_snapshot(db_path: str | Path, now: datetime | None = None) -> dict:
    """手机 app「展示模式」用的快照（和真快照同一个格式，多一个 demo 标记）。"""
    now = now or datetime.now(ET)
    today = now.date()
    conn = build_demo_db(db_path, today)
    try:
        quotes = demo_quotes(today)
        w = build_wealth(conn, quotes, quotes[FX_TICKER].close, today)
        snap = build_mobile_snapshot(conn, w, quotes, today, now, demo_events(conn, today, quotes))
    finally:
        conn.close()
    return {**snap, "demo": True}
