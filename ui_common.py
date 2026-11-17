"""各页面共用：数据库连接、行情、身份/税务设置的组装、明细表渲染、页脚。"""
from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import streamlit as st

from core.db import connect, db_path, get_setting, one, rows
from core.market import ET, FX_TICKER, get_quotes
from core.money import ZERO, D, fmt_cny, fmt_pair, fmt_usd
from core.params import ParamError, available_years, load_params
from core.portfolio import BROKER_SOURCES, Quote, is_long_term, load_lots, load_realized
from core.residency import compute_residency, load_travel
from core.seed import seed_if_empty
from core.trace import Trace
from core.us_tax import GainItem, IncomeItem, TaxProfile, compute_tax
from core.wash_sale import Acquisition, apply_wash, parse_equivalents

DISCLAIMER = "仅供估算，不构成税务建议，以 CPA 意见为准。"


# Streamlit 的 markdown 会把一对 $…$ 当成 LaTeX 公式，金额里的美元符号会被吃掉。
# 这里统一把文字类元素里的 $ 转义（幂等），表格不受影响。
def _escape_dollar(x):
    return x.replace("\\$", "$").replace("$", "\\$") if isinstance(x, str) else x


def _escape_args(args, kwargs, all_args):
    if all_args:
        args = tuple(_escape_dollar(a) for a in args)
    elif args:
        args = (_escape_dollar(args[0]), *args[1:])
    if "body" in kwargs:
        kwargs["body"] = _escape_dollar(kwargs["body"])
    return args, kwargs


def _patch_markdown_dollars() -> None:
    from functools import wraps

    from streamlit.delta_generator import DeltaGenerator

    for name in ("markdown", "caption", "info", "warning", "success", "error", "write"):
        all_args = name == "write"
        meth = getattr(DeltaGenerator, name)
        if not getattr(meth, "_dollar_safe", False):
            def method(self, *a, _f=meth, _all=all_args, **k):
                a, k = _escape_args(a, k, _all)
                return _f(self, *a, **k)
            method = wraps(meth)(method)
            method._dollar_safe = True
            setattr(DeltaGenerator, name, method)
        func = getattr(st, name)
        if not getattr(func, "_dollar_safe", False):
            def module_fn(*a, _f=func, _all=all_args, **k):
                a, k = _escape_args(a, k, _all)
                return _f(*a, **k)
            module_fn = wraps(func)(module_fn)
            module_fn._dollar_safe = True
            setattr(st, name, module_fn)


_patch_markdown_dollars()
STATUS_OPTIONS = {"resident": "全年居民", "nonresident": "全年非居民", "dual_status": "双重身份年"}


# ---------- 访问权限：只读模式 ----------
# 本机浏览器直连可以修改；经 Tailscale Serve 来的访问（手机、爸妈）按 Tailscale 账号判断：
# 在设置「可以修改数据的 Tailscale 账号」（editor_logins）里的能改，其余一律只读。
# 只读会话拿到的是 SQLite 只读连接，即使哪个按钮漏了，写入也会被数据库拒绝。
READ_ONLY_ENV = "FAMILY_FINANCE_READ_ONLY"      # 设为 1 时强制只读（只会更严格，测试用）
SNAPSHOT_ENV = "FAMILY_FINANCE_SNAPSHOT"        # 打包给家人的只读副本（packaging/）：值是数据日期，副本不联网
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_SERVE_HEADERS = ("tailscale-headers-info", "tailscale-user-login", "x-forwarded-for", "x-forwarded-host")
READ_ONLY_HELP = "只读模式：只能查看，不能修改"


def _hostname(host: str) -> str:
    host = host.strip()
    return host[1:host.find("]")] if host.startswith("[") else host.split(":")[0]


def access_from_headers(headers: Mapping[str, str], editors: set[str]) -> tuple[bool, str]:
    """按请求头判断这个会话能不能修改数据，返回 (能修改, 访问者)。

    Tailscale Serve 会给每个请求加上 Tailscale-User-Login、X-Forwarded-For 等头，并删掉客户端自己带的
    同名身份头（https://tailscale.com/s/serve-headers），所以经它来的请求不可能冒充本机或别人的账号。
    完全没有请求头只发生在测试环境（AppTest），按本机处理。
    """
    h = {str(k).lower(): str(v) for k, v in headers.items()}
    if not h:
        return True, "本机"
    via_serve = any(k in h for k in _SERVE_HEADERS)
    if not via_serve and _hostname(h.get("host", "")).lower() in _LOCAL_HOSTS:
        return True, "本机"
    login = h.get("tailscale-user-login", "").strip().lower()
    if via_serve and login and login in editors:
        return True, login
    return False, login or "访客"


def parse_logins(text: str | None) -> set[str]:
    """「a@x.com, b@y.com」→ {"a@x.com", "b@y.com"}（不分大小写）。"""
    return {x.strip().lower() for x in re.split(r"[,，;\s]+", text or "") if x.strip()}


def editor_logins(conn) -> set[str]:
    return parse_logins(get_setting(conn, "editor_logins", ""))


def refresh_access() -> bool:
    """每次运行时（app.py）按当前请求头重新判断，结果存进 session_state；返回是否只读。"""
    if os.environ.get(READ_ONLY_ENV) == "1":
        ok, who = False, "家人" if os.environ.get(SNAPSHOT_ENV) else "测试"
    else:
        try:
            headers = dict(st.context.headers or {})
        except Exception:          # 不在 Streamlit 运行环境里
            headers = {}
        ok, who = access_from_headers(headers, editor_logins(_rw_conn()))
    st.session_state["_read_only"], st.session_state["_viewer"] = not ok, who
    return not ok


def is_read_only() -> bool:
    if "_read_only" not in st.session_state:
        return refresh_access()
    return bool(st.session_state["_read_only"]) or os.environ.get(READ_ONLY_ENV) == "1"


@st.cache_resource
def _rw_conn() -> sqlite3.Connection:
    conn = connect()
    seed_if_empty(conn)
    return conn


@st.cache_resource
def _ro_conn() -> sqlite3.Connection:
    _rw_conn()                     # 先确保库和表都已建好
    conn = sqlite3.connect(f"{db_path().resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_conn() -> sqlite3.Connection:
    """页面用的数据库连接；只读会话拿到只读连接。"""
    return _ro_conn() if is_read_only() else _rw_conn()


def system_conn() -> sqlite3.Connection:
    """行情缓存、净资产快照这类程序自己维护的数据：谁打开 app 都照常更新。"""
    return _rw_conn()


def _read_only_kwargs(kind: str, readonly_ok: bool, kwargs: dict) -> dict:
    if readonly_ok or not is_read_only():
        return kwargs
    kwargs = {**kwargs, "disabled": True}
    if kind == "button" and not kwargs.get("help"):
        kwargs["help"] = READ_ONLY_HELP
    return kwargs


def _patch_read_only_widgets() -> None:
    """只读会话里按钮和可编辑表格一律禁用；只改当前会话、不写数据库的地方传 readonly_ok=True 放行。"""
    from functools import wraps

    from streamlit.delta_generator import DeltaGenerator

    for name in ("button", "data_editor"):
        meth = getattr(DeltaGenerator, name)
        if not getattr(meth, "_ro_safe", False):
            def method(self, *a, _f=meth, _kind=name, readonly_ok=False, **k):
                return _f(self, *a, **_read_only_kwargs(_kind, readonly_ok, k))
            method = wraps(meth)(method)
            method._ro_safe = True
            setattr(DeltaGenerator, name, method)
        func = getattr(st, name)
        if not getattr(func, "_ro_safe", False):
            def module_fn(*a, _f=func, _kind=name, readonly_ok=False, **k):
                return _f(*a, **_read_only_kwargs(_kind, readonly_ok, k))
            module_fn = wraps(func)(module_fn)
            module_fn._ro_safe = True
            setattr(st, name, module_fn)


_patch_read_only_widgets()


def today_et() -> date:
    return datetime.now(ET).date()


def to_date(x) -> date | None:
    if x is None or (isinstance(x, float) and pd.isna(x)) or x is pd.NaT:
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if hasattr(x, "date"):
        return x.date()
    s = str(x).strip()
    return date.fromisoformat(s[:10]) if s else None


def txt(x) -> str | None:
    """表格编辑器里的文字单元格：None / NaN / 空串 → None。"""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip()
    return s or None


def dec_or_none(x) -> Decimal | None:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip()
    return D(s.replace(",", "").replace("$", "").replace("¥", "")) if s else None


# ---------- 行情 ----------

def market_state(conn) -> tuple[dict[str, Quote], Decimal | None, list[str]]:
    """每个浏览器会话首次打开时联网一次（离线模式除外）；之后用侧边栏「刷新行情」。"""
    tickers = sorted({r["ticker"] for r in rows(conn, "SELECT DISTINCT ticker FROM lots")} | {"AAPL"})
    refresh = st.session_state.pop("_refresh_quotes", False) or not st.session_state.get("_quotes_loaded")
    quotes, msgs = get_quotes(conn, tickers, refresh=refresh)
    st.session_state["_quotes_loaded"] = True
    fxq = quotes.get(FX_TICKER)
    return quotes, (fxq.close if fxq else None), msgs


def current_market(conn) -> tuple[dict[str, Quote], Decimal | None, list[str]]:
    """app.py 每次运行都会先取行情放进 session_state；页面直接读。行情缓存总是用可写连接更新。"""
    if "_market" not in st.session_state:
        st.session_state["_market"] = market_state(system_conn())
    return st.session_state["_market"]


def stat(col, label: str, usd, fx, sign: bool = False, note: str | None = None, pct=None,
         colored: bool = False) -> None:
    """金额卡片：美元一行（大字）、人民币一行，窄屏也不会被截断。"""
    from ui_theme import money_card

    money_card(col, label, usd, fx, sign=sign, note=note, pct=pct, colored=colored)


def card(col, label: str, text: str, note: str | None = None, color: str | None = None) -> None:
    from ui_theme import text_card

    text_card(col, label, text, note, color)


def money(usd, fx, sign: bool = False) -> str:
    return fmt_pair(usd, fx, sign)


# ---------- 明细表 ----------

def render_trace(trace: Trace, fx: Decimal | None = None, currency: str = "usd", expanded: bool = False,
                 title: str = "用到的规则和参数") -> None:
    for w in trace.warnings:
        st.warning(f"{w.label}　{w.rule}")
    from ui_html import trace_html

    with st.expander(title, expanded=expanded):
        st.html(trace_html(trace.lines, fx, currency))


def stop() -> None:
    """提前结束页面前先补上免责声明（st.stop() 会跳过 app.py 末尾的页脚）。"""
    from ui_theme import footer_html

    footer_html(DISCLAIMER)
    st.stop()


def flash(msg: str | None = None, key: str = "main") -> None:
    """st.rerun() 之后仍能看到的提示：带 msg 时先存起来；不带时在当前位置显示并清掉。"""
    k = f"_flash_{key}"
    if msg:
        st.session_state[k] = msg
    elif k in st.session_state:
        st.success(st.session_state.pop(k))


# ---------- 身份与税务设置 ----------

def tax_years_settings(conn, year: int) -> dict:
    r = one(conn, "SELECT * FROM tax_years WHERE year = ?", (year,))
    return r or {"year": year, "status_override": None, "residency_start_override": None, "us_days_override": None,
                 "fyc_elect": 0, "itemized": None, "ny_status": "off", "ny_start": None, "nyc": 0, "note": None}


def save_tax_year(conn, year: int, **fields) -> None:
    cur = tax_years_settings(conn, year)
    cur.update(fields)
    with conn:
        conn.execute(
            "INSERT INTO tax_years(year, status_override, residency_start_override, us_days_override, fyc_elect,"
            " itemized, ny_status, ny_start, nyc, note) VALUES(?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(year) DO UPDATE SET status_override=excluded.status_override,"
            " residency_start_override=excluded.residency_start_override, us_days_override=excluded.us_days_override,"
            " fyc_elect=excluded.fyc_elect, itemized=excluded.itemized, ny_status=excluded.ny_status,"
            " ny_start=excluded.ny_start, nyc=excluded.nyc, note=excluded.note",
            (year, cur["status_override"], cur["residency_start_override"], cur["us_days_override"],
             int(cur["fyc_elect"] or 0), cur["itemized"], cur["ny_status"] or "off", cur["ny_start"],
             int(cur["nyc"] or 0), cur["note"]),
        )


def green_card_date(conn) -> date | None:
    v = get_setting(conn, "green_card_date")
    return date.fromisoformat(v) if v else None


def residency_years(today: date) -> list[int]:
    return list(range(2022, max(today.year + 1, max(available_years())) + 1))


def residency_all(conn, today: date):
    years = residency_years(today)
    fyc = {r["year"]: bool(r["fyc_elect"]) for r in rows(conn, "SELECT year, fyc_elect FROM tax_years")}
    return compute_residency(load_travel(conn), years, today, green_card_date(conn), fyc)


def build_profile(conn, year: int, res: dict, status_override: str | None = None,
                  residency_start: date | None = None, us_days: int | None = None) -> tuple[TaxProfile, list[str]]:
    """身份默认取「税务身份」页的计算结果；本年设置里的手动覆盖优先；参数显式传入时再优先。"""
    s = tax_years_settings(conn, year)
    notes = []
    yr = res.get(year)
    status = (yr.status if yr else "nonresident")
    start = yr.residency_start if yr else None
    days = yr.us_days if yr else None
    if s["status_override"]:
        status = s["status_override"]
        notes.append("身份：手动覆盖")
    if s["residency_start_override"]:
        start = date.fromisoformat(s["residency_start_override"])
    if s["us_days_override"] is not None:
        days = int(s["us_days_override"])
        notes.append("在美天数：手动覆盖")
    if status_override:
        status = status_override
    if residency_start:
        start = residency_start
    if us_days is not None:
        days = us_days
    if status == "dual_status" and start is None:
        start = (yr.fyc.start if yr and yr.fyc.start else None) or (yr.residency_start if yr else None)
        if start is None:
            status = "nonresident"
            notes.append("设成了双重身份年但没有居民起始日，暂按全年非居民计算")
    carry_st, carry_lt = carry_in(conn, year, res)
    prof = TaxProfile(year, status, start, days, D(s["itemized"] or 0), carry_st, carry_lt,
                      s["ny_status"] or "off", date.fromisoformat(s["ny_start"]) if s["ny_start"] else None,
                      bool(s["nyc"]))
    return prof, notes


def incomes_for(conn, year: int) -> list[IncomeItem]:
    return [IncomeItem(r["kind"], D(r["amount"]), r["period"], r["note"] or "")
            for r in rows(conn, "SELECT * FROM other_income WHERE year = ? ORDER BY id", (year,))]


def acquisitions(conn, planned: list[dict] | None = None) -> list[Acquisition]:
    """所有已知买入：现有批次 + 已卖出批次 + 计划买入。同一账户、同一 ticker、同一天的算一次买入。"""
    agg: dict[tuple, Decimal] = {}
    for r in rows(conn, "SELECT account, ticker, open_date, quantity FROM lots"):
        k = (r["account"], r["ticker"], date.fromisoformat(r["open_date"]))
        agg[k] = agg.get(k, ZERO) + D(r["quantity"])
    for r in rows(conn, "SELECT s.account, s.ticker, sl.open_date, sl.quantity FROM sale_lots sl"
                        " JOIN sales s ON s.id = sl.sale_id"):
        k = (r["account"], r["ticker"], date.fromisoformat(r["open_date"]))
        agg[k] = agg.get(k, ZERO) + D(r["quantity"])
    out = [Acquisition(k[2], k[1], q, k) for k, q in agg.items()]
    for i, b in enumerate(planned or []):
        out.append(Acquisition(b["date"], b["ticker"], D(b["qty"]), ("planned", b["ticker"], b["date"], i), "计划买入"))
    return out


def equivalents(conn):
    return parse_equivalents(get_setting(conn, "wash_equivalents", ""))


def lot_meta(account: str, ticker: str, open_date: date, source: str | None, as_of: str | None,
             real: bool = False) -> dict:
    """wash sale 用的批次信息；real=True 表示已实际成交的卖出（券商可能已经调整过替代股成本）。"""
    return {"key": (account, ticker, open_date), "open_date": open_date,
            "broker_adjusted_on": as_of if source in BROKER_SOURCES else None, "real": real}


def wash_run(conn, extra: list[tuple[GainItem, dict]] | None = None, planned: list[dict] | None = None):
    """把所有已实现卖出（各年）和额外的模拟卖出放在一起做 wash sale。
    返回 (已实现 GainItem 列表, 模拟 GainItem 列表, 配对结果, 已实现条数)。"""
    legs = load_realized(conn)
    items = [GainItem(l.sale_date, l.ticker, l.quantity, l.proceeds, l.cost_basis, l.long_term,
                      label=f"{l.ticker} {l.open_date}") for l in legs]
    meta = [lot_meta(l.account, l.ticker, l.open_date, l.basis_source, l.basis_as_of, real=True) for l in legs]
    n_real = len(items)
    for it, m in extra or []:
        items.append(it)
        meta.append(m)
    window = load_params_safe(date.today().year).v("us_federal.wash_sale_window_days")
    pairs, hits = apply_wash(items, meta, acquisitions(conn, planned), window, equivalents(conn), is_long_term)
    return [it for i, it in pairs if i < n_real], [it for i, it in pairs if i >= n_real], hits, n_real


def realized_items(conn, year: int, planned: list[dict] | None = None) -> tuple[list[GainItem], list]:
    """某年已实现卖出 → GainItem（已按 wash sale 加回被禁止的亏损、调整替代股成本）。"""
    realized, _, hits, n = wash_run(conn, planned=planned)
    return ([it for it in realized if it.sale_date.year == year],
            [h for h in hits if h.leg.ref < n and h.leg.sale_date.year == year])


def load_params_safe(year: int):
    try:
        return load_params(year)
    except ParamError:
        ys = available_years()
        return load_params(min(ys, key=lambda y: abs(y - year)))


def carry_in(conn, year: int, res: dict | None = None, _depth: int = 0) -> tuple[Decimal, Decimal]:
    """上年结转亏损：手动录入优先；否则用上一年计算出的结转额。"""
    man = one(conn, "SELECT * FROM carryover_in WHERE year = ?", (year,))
    if man:
        return D(man["st"]), D(man["lt"])
    prev = year - 1
    if prev not in available_years() or _depth > 5:
        return ZERO, ZERO
    res = res or {}
    prof, _ = build_profile(conn, prev, res) if prev in res else (None, None)
    if prof is None:
        return ZERO, ZERO
    gains, _ = realized_items(conn, prev)
    if not gains and not prof.carry_st and not prof.carry_lt:
        return ZERO, ZERO
    r = compute_tax(prof, gains, incomes_for(conn, prev), load_params(prev))
    return r.carry_out_st, r.carry_out_lt


# ---------- 资金日历 ----------

def family_events(conn, today: date, quotes: dict[str, Quote]) -> list:
    """资金日历的全部事项。报税截止日按每个纳税年度在「联邦税」页实际使用的身份（含手动覆盖）来定。"""
    from core.agenda import build_events

    res = residency_all(conn, today)
    statuses, wages, fyc = {}, {}, {}
    for y in (today.year - 1, today.year):
        if y not in available_years():
            continue
        prof, _ = build_profile(conn, y, res)
        statuses[y] = prof.status
        wages[y] = any(i.kind == "wages" and i.amount > 0 for i in incomes_for(conn, y))
        # 本来是非居民、靠 First-Year Choice 才成为双重身份的年份：要等次年满足 SPT 才能交 1040
        r, nxt = res.get(y), res.get(y + 1)
        if prof.status == "dual_status" and r and r.fyc_elected and r.default_status == "nonresident":
            fyc[y] = nxt.spt_met_date if nxt else None
    return build_events(conn, today, quotes, statuses, wages, fyc=fyc)
