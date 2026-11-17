"""手机 app 用的只读快照。

总览页上的每个数字都在这里按网页同样的函数格式化成文字（fmt_usd / fmt_cny / fmt_pct / shares_bp），
手机 app 只负责排版、画图，所以两边逐分一致；原始数值只留给手机画条形和走势用。
不联网、不依赖 Streamlit。生成的 JSON 只放在本机（ios-app/Generated/，已 gitignore），装机时打包进 app，
或者用户自己隔空投送到手机上导入。
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal

from .agenda import KINDS, next_milestone, parse_milestones, parse_when
from .db import rows
from .money import ZERO, D, fmt_cny, fmt_pct, fmt_price, fmt_qty, fmt_usd, shares_bp
from .portfolio import (Quote, aapl_holdings, cash_status, default_account, eb5_status, load_lots, lot_view,
                        ticker_view)
from .wealth import (CLASS_SLOTS, LIQUIDITY_FILL, LIQUIDITY_TIMING, Wealth, load_snapshots, member_names,
                     account_label)

SCHEMA_VERSION = 1
WEEK = "一二三四五六日"


class IncompleteData(ValueError):
    """数据不完整（缺行情、缺汇率），不导出，免得手机上显示一个看起来精确的错数。"""


def _neg(s: str) -> str:
    """开头的减号换成真正的负号（−），和网页的 disp() 一样。"""
    return "−" + s[1:] if s.startswith("-") else s


def _sign(v) -> int:
    v = D(v)
    return 1 if v > 0 else -1 if v < 0 else 0


def _usd(v, sign: bool = False) -> str:
    return _neg(fmt_usd(v, sign))


def _cny(v, fx, sign: bool = False) -> str:
    return _neg(fmt_cny(D(v) * D(fx), sign)) if fx is not None else ""


def _money(v, fx, sign: bool = False) -> dict:
    """一笔钱：原始值（画图用）+ 美元、人民币文字 + 正负。"""
    return {"v": float(D(v)), "usd": _usd(v, sign), "cny": _cny(v, fx, sign), "sign": _sign(v) if sign else 0}


def _pct(x, sign: bool = False) -> str:
    return _neg(fmt_pct(x, sign=sign))


def _day_label(d: date, today: date) -> str:
    left = (d - today).days
    return "今天" if left == 0 else f"{left} 天后" if left > 0 else f"已过 {-left} 天"


def _certificate(key: str, title: str, items: list, liabilities, fx, unr: dict | None = None) -> dict:
    """一张凭证：净资产面额、今日盈亏（含按类别的来源）、底部的数字带。"""
    assets = sum((i.usd for i in items), ZERO)
    liab = sum((D(x["amount"]) for x in liabilities), ZERO)
    cash = sum((i.usd for i in items if i.key.startswith("cash:")), ZERO)
    invested = assets - cash
    day = sum((i.day_pnl for i in items if i.day_pnl is not None), ZERO)
    base = invested - day
    by_class: dict[str, Decimal] = {}
    for i in items:
        if i.day_pnl:
            by_class[i.asset_class] = by_class.get(i.asset_class, ZERO) + i.day_pnl
    strip = [{"label": "总资产", "qual": "", **_money(assets, fx)},
             {"label": "负债", "qual": "", **_money(liab, fx)},
             {"label": "投资资产", "qual": "不含现金", **_money(invested, fx)}]
    if unr is not None:
        strip.append({"label": "累计浮盈", "qual": "Schwab 股票 + 基金", **unr})
    return {"key": key, "title": title, "net": _money(assets - liab, fx), "day": _money(day, fx, True),
            "day_pct": _pct(day / base, True) if base else "",
            "contrib": [{"name": k, **_money(v, fx, True)} for k, v in by_class.items()], "strip": strip}


def _composition(w: Wealth, by: str, fx) -> list[dict]:
    groups = w.group(by)
    bp = shares_bp([g["usd"] for g in groups])
    out = []
    for n, (g, b) in enumerate(zip(groups, bp)):
        names = list(dict.fromkeys(i.name if by != "member" else i.account for i in g["items"]))
        desc = " · ".join(names[:4]) + (f" 等 {len(names)} 项" if len(names) > 4 else "")
        slot = CLASS_SLOTS.get(g["name"], 5) if by == "asset_class" else min(n, 4) + 1
        out.append({"name": g["name"], "slot": slot, "bp": b, "share": f"{b / 100:.2f}%", "desc": desc,
                    **_money(g["usd"], fx), "day": _money(g["day_pnl"], fx, True) if g["day_pnl"] is not None else None})
    return out


def _liquidity(w: Wealth, fx) -> list[dict]:
    out, cum, total = [], ZERO, w.assets
    for b in w.liquidity():
        share = b["usd"] / total if total else ZERO
        c0, cum = cum, cum + share
        merged: dict[tuple, dict] = {}
        for i in b["rows"]:
            g = merged.setdefault((i.member, i.account, i.unlock, i.note), {"names": [], "usd": ZERO, "i": i})
            g["names"].append(i.name)
            g["usd"] += i.usd
        lines = []
        for g in merged.values():
            i, names = g["i"], g["names"]
            nm = "、".join(names[:4]) + (f" 等 {len(names)} 只" if len(names) > 4 else "")
            lines.append({"name": nm, "who": f"{i.member}，{i.account}" + (f"，{i.unlock}" if i.unlock else ""),
                          **_money(g["usd"], fx)})
        out.append({"bucket": b["bucket"], "timing": LIQUIDITY_TIMING.get(b["bucket"], ""),
                    "fill": LIQUIDITY_FILL.get(b["bucket"], "solid"), "c0": float(c0), "c1": float(cum),
                    "share": fmt_pct(share), "cum": fmt_pct(cum), **_money(b["usd"], fx), "rows": lines})
    return out


def _holdings(conn: sqlite3.Connection, w: Wealth, quotes: dict[str, Quote], today: date, fx) -> list[dict]:
    lots = load_lots(conn)
    lv = lot_view(lots, quotes, today)
    funds = w.funds
    groups = []
    for acct in sorted({l.account for l in lots}):
        tick = ticker_view([l for l in lots if l.account == acct], quotes)
        member = next((i.member for i in w.items if i.key.startswith(f"lot:{acct}:")), "")
        for title_kind, rs in (("股票", [r for r in tick if r["ticker"] not in funds]),
                               ("基金", [r for r in tick if r["ticker"] in funds])):
            if not rs:
                continue
            rs.sort(key=lambda r: -abs(r["day_pnl"] or ZERO))
            rows_ = []
            for r in rs:
                mine = [x for x in lv if x["ticker"] == r["ticker"] and x["account"] == acct]
                n_lt = sum(1 for x in mine if x["term"] == "长期")
                term = ("均为长期" if n_lt == len(mine) else "均为短期" if n_lt == 0 else f"{n_lt} 批长期") if mine else ""
                has_q = r["close"] is not None
                rows_.append({
                    "name": r["ticker"], "meta": f"{len(mine)} 批，{term}", "shares": fmt_qty(r["quantity"]),
                    "close": fmt_price(r["close"]) if has_q else "缺行情",
                    "prev": f"前收 {fmt_price(r['prev_close'])}" if has_q else "",
                    "chg": _pct(r["change_pct"], True) if r["change_pct"] is not None else "",
                    "chg_sign": _sign(r["change_pct"] or 0),
                    "mv": _money(r["market_value"], fx) if has_q else None,
                    "day": _money(r["day_pnl"], fx, True) if r["day_pnl"] is not None else None,
                    "cost": _money(r["cost_basis"], fx),
                    "unr": _money(r["unrealized"], fx, True) if r["unrealized"] is not None else None,
                    "unr_pct": _pct(r["unrealized"] / r["cost_basis"], True) if r["unrealized"] is not None and r["cost_basis"] else "",
                })
            mv = sum((r["market_value"] for r in rs if r["market_value"] is not None), ZERO)
            dp = sum((r["day_pnl"] for r in rs if r["day_pnl"] is not None), ZERO)
            groups.append({"key": f"{acct}:{title_kind}", "title": account_label(acct), "member": member,
                           "sub": f"{title_kind} {len(rs)} 只", "total": _money(mv, fx), "day": _money(dp, fx, True),
                           "rows": rows_})
    a, aq = aapl_holdings(conn), quotes.get("AAPL")
    if a and a["total"] > 0 and aq:
        member = next((i.member for i in w.items if i.key.startswith("aapl:")), "")
        per = aq.close - aq.prev_close
        chg = _pct(per / aq.prev_close, True) if aq.prev_close else ""
        vest_items = [i for i in w.items if i.key.startswith("aapl:") and i.key != "aapl:vested"]
        unvested_note = vest_items[0].note if vest_items else ""
        rows_ = [{"name": "已归属（含 ESPP）", "meta": f"RS {fmt_qty(a['rs_vested'])} 股 + ESPP {fmt_qty(a['espp'])} 股",
                  "shares": fmt_qty(a["vested_incl_espp"]), "close": fmt_price(aq.close), "prev": f"前收 {fmt_price(aq.prev_close)}",
                  "chg": chg, "chg_sign": _sign(per), "mv": _money(a["vested_incl_espp"] * aq.close, fx),
                  "day": _money(a["vested_incl_espp"] * per, fx, True), "cost": None, "unr": None, "unr_pct": ""},
                 {"name": "未归属", "meta": unvested_note, "shares": fmt_qty(a["rs_unvested"]), "close": fmt_price(aq.close),
                  "prev": f"前收 {fmt_price(aq.prev_close)}", "chg": chg, "chg_sign": _sign(per),
                  "mv": _money(a["rs_unvested"] * aq.close, fx), "day": _money(a["rs_unvested"] * per, fx, True),
                  "cost": None, "unr": None, "unr_pct": ""}]
        groups.append({"key": "aapl", "title": "AAPL 股票计划", "member": member, "sub": f"收盘 {fmt_price(aq.close)}",
                       "total": _money(a["total"] * aq.close, fx), "day": _money(a["total"] * per, fx, True), "rows": rows_})
    e = eb5_status(conn, today)
    if e and not e["returned"]:
        member = next((i.member for i in w.items if i.key == "eb5"), "")
        groups.append({"key": "eb5", "title": "EB-5", "member": member, "sub": "按本金，不随市场变动",
                       "total": _money(e["principal"], fx), "day": None,
                       "rows": [{"name": "投资本金", "meta": f"预计 {e['expected_return_date']} 回款，还有 {e['days_left']} 天",
                                 "shares": "", "close": "", "prev": "", "chg": "", "chg_sign": 0,
                                 "mv": _money(e["principal"], fx), "day": None, "cost": None, "unr": None, "unr_pct": ""}]})
    cs = cash_status(conn, today)
    if cs["snapshot"] or cs["transfers"]:
        member = next((i.member for i in w.items if i.key.startswith("cash:")), "")
        lines = []
        if cs["snapshot"]:
            lines.append({"name": f"{cs['snapshot']['as_of']} 余额", "meta": cs["snapshot"]["note"] or "", "shares": "",
                          "close": "", "prev": "", "chg": "", "chg_sign": 0, "mv": _money(cs["base"], fx), "day": None,
                          "cost": None, "unr": None, "unr_pct": ""})
        for t in cs["transfers"]:
            lines.append({"name": t["note"] or "转入", "meta": t["status"], "shares": "", "close": "", "prev": "", "chg": "",
                          "chg_sign": 0, "mv": _money(t["amount"], fx, True), "day": None, "cost": None, "unr": None,
                          "unr_pct": ""})
        groups.append({"key": "cash", "title": "现金", "member": member, "sub": account_label(default_account(conn)),
                       "total": _money(cs["total"], fx), "day": None, "rows": lines})
    return groups


def _plans(conn: sqlite3.Connection, today: date, now: datetime, fx, cash, member: str) -> list[dict]:
    out = []
    for pl in rows(conn, "SELECT * FROM cash_plans ORDER BY id"):
        try:
            ms = parse_milestones(pl.get("milestones"))
        except ValueError:
            continue
        nxt = next_milestone(ms, now)
        if not ms or not nxt:
            continue
        when = parse_when(pl.get("expected"))
        amt = D(pl["amount"])
        exp = None
        if when:
            d = when[0]
            exp = {"date": d.isoformat(), "month_only": when[1], "year": d.year, "month": d.month, "day": d.day,
                   "weekday": f"周{WEEK[d.weekday()]}", "left": _day_label(d, today)}
        gap = (D(cash) - amt) if cash is not None else None
        out.append({
            "label": pl["label"], "member": member, "note": pl.get("note") or "", "amount": _money(amt, fx),
            "expected": exp,
            "milestones": [{"at": m.when.isoformat(), "end": m.end.isoformat(), "has_time": m.has_time, "text": m.text,
                            "short": m.short,
                            "label": f"{m.when.month}/{m.when.day:02d}" + (f" {m.when:%H:%M} ET" if m.has_time else ""),
                            "state": "now" if m is nxt else "done" if m.end <= now else ""} for m in ms],
            "next": {"short": nxt.short, "at": nxt.when.isoformat(), "has_time": nxt.has_time},
            "cash": _usd(cash) if cash is not None else "",
            "cash_ok": gap is not None and gap >= 0,
            "cash_gap": (f"够付这笔，余 {_usd(gap)}" if gap >= 0 else f"还差 {_usd(-gap)}") if gap is not None else "",
        })
    return out


def build_mobile_snapshot(conn: sqlite3.Connection, w: Wealth, quotes: dict[str, Quote], today: date, now: datetime,
                          events: list) -> dict:
    """整理成一个可以直接 json.dump 的字典。数据不完整时抛 IncompleteData。events 是资金日历的事项（core.agenda.Event）。"""
    if w.missing:
        raise IncompleteData("缺行情：" + "、".join(w.missing))
    if w.fx is None:
        raise IncompleteData("缺 USD/CNY 汇率")
    if not w.quote_date:
        raise IncompleteData("没有持仓行情")
    fx = w.fx
    me, father = member_names(conn)
    tick = ticker_view(load_lots(conn), quotes)
    quoted = [r for r in tick if r["unrealized"] is not None]
    s_cost = sum((r["cost_basis"] for r in quoted), ZERO)
    s_unr = sum((r["unrealized"] for r in quoted), ZERO) if quoted else None
    unr = None
    if s_unr is not None:
        unr = {**_money(s_unr, fx, True), "pct": _pct(s_unr / s_cost, True) if s_cost else ""}
    certs = [_certificate("family", "家族净资产", w.items, w.liabilities, fx, unr)]
    for m in [g["name"] for g in w.group("member")]:
        items = [i for i in w.items if i.member == m]
        certs.append(_certificate(m, f"{m} 名下", items, [x for x in w.liabilities if (x.get("member") or "") == m], fx))
    d = date.fromisoformat(w.quote_date)
    cash = cash_status(conn, today)
    plans = _plans(conn, today, now, fx, cash["total"] if cash["snapshot"] else None, me)
    shown = {p["label"] for p in plans}
    evs = []
    for e in events:
        if e.done or (e.kind == "cash" and e.title in shown) or e.last_day < today:
            continue
        evs.append({"date": e.start.isoformat(), "month_only": e.month_only, "end": e.end.isoformat() if e.end else None,
                    "kind": KINDS.get(e.kind, e.kind), "title": e.title, "member": e.member, "note": e.note,
                    "money": _money(e.usd, fx) if e.usd is not None else None,
                    "weekday": f"周{WEEK[e.start.weekday()]}", "left": _day_label(e.start, today) if not e.month_only else "当月"})
    trend = [{"date": s["snap_date"], **_money(s["net_worth"], fx)} for s in load_snapshots(conn)]
    conc = [{"name": c["name"], "who": "、".join(sorted(c["members"])), **_money(c["usd"], fx), "share": fmt_pct(c["share"]),
             "share_v": float(c["share"]), "flag": bool(c["flag"])} for c in w.concentration()]
    return {
        "version": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "as_of": w.quote_date,
        "as_of_label": f"{d.year} 年 {d.month} 月 {d.day} 日" + ("盘中" if w.intraday else "收盘"),
        "intraday": w.intraday,
        "fx": f"{D(fx):.4f}",
        "members": {"self": me, "father": father},
        "certificates": certs,
        "composition": {"按资产类别": _composition(w, "asset_class", fx), "按家庭成员": _composition(w, "member", fx),
                        "按账户": _composition(w, "account", fx)},
        "liquidity": _liquidity(w, fx),
        "concentration": conc,
        "trend": trend,
        "holdings": _holdings(conn, w, quotes, today, fx),
        "plans": plans,
        "events": evs,
    }
