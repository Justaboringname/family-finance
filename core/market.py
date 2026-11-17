"""行情：yfinance 的收盘价和前收盘价；汇率 USD/CNY 用 CNY=X。都可以手动覆盖。

隐私：唯一的外网请求就是这里——只把股票代码和 CNY=X 发给 Yahoo，不带持仓数量或金额。
「离线模式」打开后完全不联网，只用缓存和手动值。
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .db import get_setting, one, rows
from .money import D
from .portfolio import Quote

FX_TICKER = "CNY=X"
ET = ZoneInfo("America/New_York")


def fetch_yfinance(tickers: list[str], now: datetime | None = None) -> tuple[dict[str, Quote], list[str]]:
    """从 yfinance 取最近两个交易日的收盘价。返回 (报价, 错误信息)。"""
    import yfinance as yf

    now = now or datetime.now(ET)
    quotes, errors = {}, []
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(period="10d", interval="1d", auto_adjust=False)
            hist = hist.dropna(subset=["Close"])
            if len(hist) < 2:
                errors.append(f"{t}：数据不足")
                continue
            last, prev = hist.iloc[-1], hist.iloc[-2]
            qd = hist.index[-1].date()
            intraday = t != FX_TICKER and qd == now.date() and (now.hour, now.minute) < (16, 0)
            quotes[t] = Quote(t, D(float(last["Close"])), D(float(prev["Close"])), qd.isoformat(), intraday, "yfinance")
        except Exception as e:  # 网络或数据源错误不应让页面崩溃
            errors.append(f"{t}：{type(e).__name__}: {e}")
    return quotes, errors


def save_cache(conn: sqlite3.Connection, quotes: dict[str, Quote]) -> None:
    stamp = datetime.now(ET).isoformat(timespec="seconds")
    with conn:
        for q in quotes.values():
            conn.execute(
                "INSERT INTO price_cache(ticker, quote_date, close, prev_close, intraday, fetched_at) VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(ticker) DO UPDATE SET quote_date=excluded.quote_date, close=excluded.close,"
                " prev_close=excluded.prev_close, intraday=excluded.intraday, fetched_at=excluded.fetched_at",
                (q.ticker, q.quote_date, str(q.close), str(q.prev_close), int(q.intraday), stamp),
            )


def load_cache(conn: sqlite3.Connection) -> dict[str, Quote]:
    out = {}
    for r in rows(conn, "SELECT * FROM price_cache"):
        out[r["ticker"]] = Quote(r["ticker"], D(r["close"]), D(r["prev_close"]), r["quote_date"] or "",
                                 bool(r["intraday"]), f"缓存（{r['fetched_at']}）")
    return out


def load_overrides(conn: sqlite3.Connection) -> dict[str, Quote]:
    out = {}
    for r in rows(conn, "SELECT * FROM price_override WHERE active = 1"):
        close = D(r["close"])
        prev = D(r["prev_close"]) if r["prev_close"] else close
        out[r["ticker"]] = Quote(r["ticker"], close, prev, r["as_of"] or "", False, "手动")
    return out


def set_override(conn: sqlite3.Connection, ticker: str, close, prev_close=None, as_of: str | None = None,
                 note: str = "") -> None:
    with conn:
        conn.execute(
            "INSERT INTO price_override(ticker, close, prev_close, as_of, active, note) VALUES(?,?,?,?,1,?)"
            " ON CONFLICT(ticker) DO UPDATE SET close=excluded.close, prev_close=excluded.prev_close,"
            " as_of=excluded.as_of, active=1, note=excluded.note",
            (ticker.upper(), str(D(close)), str(D(prev_close)) if prev_close not in (None, "") else None, as_of, note),
        )


def clear_override(conn: sqlite3.Connection, ticker: str) -> None:
    with conn:
        conn.execute("UPDATE price_override SET active = 0 WHERE ticker = ?", (ticker.upper(),))


def get_quotes(conn: sqlite3.Connection, tickers: list[str], refresh: bool, fetcher=None) -> tuple[dict[str, Quote], list[str]]:
    """手动覆盖 > 本次联网结果 > 缓存。refresh=False 或离线模式时不联网。"""
    msgs = []
    want = sorted(set(tickers) | {FX_TICKER})
    offline = get_setting(conn, "offline", "0") == "1"
    fresh: dict[str, Quote] = {}
    if refresh and not offline:
        fetcher = fetcher or fetch_yfinance  # 运行时查找，测试里可以替换
        try:
            fresh, errors = fetcher(want)
        except Exception as e:  # 网络不可用时退回缓存
            fresh, errors = {}, [f"联网取行情失败：{type(e).__name__}: {e}"]
        # Yahoo 偶尔在收盘后把当天的日线收盘价给成空值，这时拿到的是前一天的数据；不要用旧数据覆盖缓存里更新的行情
        cached = load_cache(conn)
        stale = [t for t, q in fresh.items()
                 if t in cached and q.quote_date and cached[t].quote_date and q.quote_date < cached[t].quote_date]
        for t in stale:
            del fresh[t]
        if stale:
            msgs.append("Yahoo 返回的行情比缓存旧，沿用缓存：" + ", ".join(stale))
        save_cache(conn, fresh)
        msgs.extend(errors)
    elif refresh and offline:
        msgs.append("离线模式：未联网，使用缓存和手动值")
    quotes = load_cache(conn)
    quotes.update(fresh)
    quotes.update(load_overrides(conn))
    missing = [t for t in want if t not in quotes]
    if missing:
        msgs.append("缺少行情：" + ", ".join(missing) + "（可在「数据管理」手动填写）")
    return quotes, msgs


def fx_rate(quotes: dict[str, Quote]) -> Decimal | None:
    q = quotes.get(FX_TICKER)
    return q.close if q else None


def cn_fx_rate(conn: sqlite3.Connection, on: date) -> tuple[Decimal | None, str]:
    """人民币汇率中间价（手动录入表）：取不晚于 on 的最近一条。"""
    r = one(conn, "SELECT * FROM cn_fx_rates WHERE rate_date <= ? ORDER BY rate_date DESC LIMIT 1", (on.isoformat(),))
    if r:
        return D(r["rate"]), f"中间价 {r['rate_date']}（手动）"
    return None, ""
