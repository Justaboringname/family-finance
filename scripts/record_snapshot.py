"""收盘后不经过浏览器记一条净资产快照：和打开网站时一样取行情、算家族资产负债表、写快照。

由 scripts/open_after_close.sh 在工作日收盘后调用，每次尝试打印一行结果（写进那边的日志）。
以前只靠 Safari 刷新网站来记：Mac 重启后 Safari 停在「此连接不安全」页，网站没打开，就漏记了（2026-10-21、10-22）。
用法：.venv/bin/python scripts/record_snapshot.py [--tries N] [--wait 秒]
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.db import connect, rows  # noqa: E402
from core.market import ET, FX_TICKER, get_quotes  # noqa: E402
from core.money import fmt_usd  # noqa: E402
from core.seed import seed_if_empty  # noqa: E402
from core.wealth import build_wealth, record_snapshot, snapshot_skip_reason  # noqa: E402


def record_once(conn, today: date) -> tuple[str | None, bool, str]:
    """取一次行情（和网站一样：手动覆盖 > 联网 > 缓存）、记一次快照。

    返回 (写入的日期, 是否已是今天的完整收盘快照, 给日志的一句话)。快照日期取持仓里最新的行情日期，
    所以只要有一只拿到今天的价就会记成今天；还有持仓停在旧收盘价时不算完成，要重试（Yahoo 有时逐只晚出收盘价）。
    """
    tickers = sorted({r["ticker"] for r in rows(conn, "SELECT DISTINCT ticker FROM lots")} | {"AAPL"})
    quotes, msgs = get_quotes(conn, tickers, refresh=True)
    fxq = quotes.get(FX_TICKER)
    w = build_wealth(conn, quotes, fxq.close if fxq else None, today)
    written = record_snapshot(conn, w)
    held = sorted({i.underlying for i in w.items if i.key.startswith(("lot:", "aapl:")) and i.underlying in quotes})
    stale = [f"{t}（{quotes[t].quote_date}）" for t in held if quotes[t].quote_date < today.isoformat()]
    done = written == today.isoformat() and not stale
    if written:
        line = f"已记 {written} 的净资产快照：{fmt_usd(w.net_worth)}，当日盈亏 {fmt_usd(w.day_pnl, sign=True)}"
        if written != today.isoformat():
            line += f"（不是今天 {today}：今天休市，或 Yahoo 还没出今天的收盘价）"
        elif stale:
            line += "（还不完整，这些还是旧收盘价：" + "、".join(stale) + "）"
    else:
        line = f"没记快照：{snapshot_skip_reason(conn, w)}"
    if msgs:
        line += "；行情提示：" + "；".join(msgs)
    return written, done, line


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="收盘后记一条净资产快照")
    p.add_argument("--tries", type=int, default=3, help="没记到今天完整的收盘快照时最多试几次（默认 3）")
    p.add_argument("--wait", type=float, default=300, help="两次之间等几秒（默认 300）")
    a = p.parse_args(argv)
    conn = connect()
    seed_if_empty(conn)
    for n in range(1, max(a.tries, 1) + 1):
        today = datetime.now(ET).date()
        try:
            _, done, line = record_once(conn, today)
        except Exception as e:  # 一次失败不影响下一次重试
            done, line = False, f"出错：{type(e).__name__}: {e}"
        print(f"[第 {n} 次] {line}", flush=True)
        if done:
            return 0
        if n < a.tries:
            time.sleep(a.wait)
    return 1


if __name__ == "__main__":
    sys.exit(main())
