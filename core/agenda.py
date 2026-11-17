"""资金日历：把散在各处的日期汇总到一个时间轴上。

来源：
- 在途转账：哪天到账、哪天可用
- 资金计划，例如参与 IPO。只写到月份时按「当月、具体日期未定」显示，不编造日期
- 爸爸的 AAPL 归属时间表
- EB-5 预计回款日
- 未来一年内从短期变成长期的批次
- 报税截止日：取 tax_params 里的 filing_deadlines，每条都带出处
- 自己加的提醒
"""
from __future__ import annotations

import re
import sqlite3
import calendar as _cal
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from .db import rows
from .market import ET
from .money import D
from .optimizer import upcoming_long_term
from .params import ParamError, load_params
from .portfolio import Quote, aapl_holdings, eb5_status, load_lots
from .wealth import DEFAULT_FATHER, DEFAULT_SELF, account_label, member_names, member_of, owners, vest_allocation

KINDS = {"cash": "资金", "vest": "归属", "invest": "投资", "tax": "税务", "lot": "批次", "custom": "提醒"}


@dataclass
class Event:
    start: date                 # 只知道月份时为当月 1 日
    title: str
    kind: str
    member: str = ""
    usd: Decimal | None = None
    note: str = ""
    source: str | None = None   # 规则类事项的出处链接
    end: date | None = None     # 有截止窗口的，例如境外所得申报 3/1–6/30
    month_only: bool = False
    custom_id: int | None = None
    done: bool = False

    @property
    def sort_key(self) -> tuple:
        return (self.start, 0 if self.month_only else 1, self.title)

    @property
    def last_day(self) -> date:
        """事项最后一天：有截止窗口的取窗口结束；只知道月份的算到当月最后一天。"""
        if self.end:
            return self.end
        if self.month_only:
            return self.start.replace(day=_cal.monthrange(self.start.year, self.start.month)[1])
        return self.start


def emancipation_day(year: int) -> date:
    """华盛顿特区解放日（4 月 16 日）的放假日：周六提前到周五，周日顺延到周一。"""
    d = date(year, 4, 16)
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def next_business_day(d: date) -> date:
    """IRS / FinCEN 截止日遇周末或法定假日顺延到下一个工作日。

    只处理会碰到这些截止日（4/15、6/15、10/15）的假日：华盛顿特区解放日。
    例如 2022 年（4/15 是解放日的放假日）和 2023 年（4/17 放假）的截止日都是 4 月 18 日。
    """
    while d.weekday() >= 5 or d == emancipation_day(d.year):
        d += timedelta(days=1)
    return d


def parse_when(text: str | None) -> tuple[date, bool] | None:
    """「2026-11」→ (2026-11-01, True)；「2026-11-05」→ (2026-11-05, False)；其他写法返回 None。"""
    t = (text or "").strip()
    try:
        if len(t) == 7:
            y, m = t.split("-")
            return date(int(y), int(m), 1), True
        return date.fromisoformat(t[:10]), False
    except ValueError:
        return None


# ---------- 资金计划的关键节点（息票上的时间线和倒计时） ----------

@dataclass
class Milestone:
    when: datetime              # 美东时间；只写日期的按当天 0 点
    has_time: bool
    text: str

    @property
    def short(self) -> str:
        """说明里第一个逗号前的部分，用在「距改单截止」这种短语里。"""
        return re.split(r"[，,]", self.text, maxsplit=1)[0].strip()

    @property
    def end(self) -> datetime:
        """这个节点什么时候算过去：带时间的到那一刻，只写日期的到当天结束。"""
        return self.when if self.has_time else self.when + timedelta(days=1)


_MILESTONE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}):(\d{2})(?:\s*ET)?)?\s+(\S.*?)\s*$")


def parse_milestones(text: str | None) -> list[Milestone]:
    """资金计划的关键节点：每行一个（也可以用「；」隔开），写成「日期 [时:分 ET] 说明」，时间按美东。
    例如「2026-12-08 16:00 ET 改单截止，之前可增可减；2026-12-09 定价」。看不懂的写法抛 ValueError，按时间排序。"""
    out = []
    for raw in re.split(r"[\n；;]+", text or ""):
        if not raw.strip():
            continue
        m = _MILESTONE.match(raw)
        if not m:
            raise ValueError(f"看不懂「{raw.strip()}」，写成「2026-12-08 16:00 ET 改单截止」或「2026-12-09 定价」")
        d = date.fromisoformat(m.group(1))
        has_time = m.group(2) is not None
        t = time(int(m.group(2)), int(m.group(3))) if has_time else time(0, 0)
        out.append(Milestone(datetime.combine(d, t, tzinfo=ET), has_time, m.group(4)))
    return sorted(out, key=lambda x: x.when)


def next_milestone(milestones: list[Milestone], now: datetime) -> Milestone | None:
    """还没过去的第一个节点；全部过去了返回 None。"""
    return next((m for m in milestones if m.end > now), None)


def tax_deadlines(year: int, status: str, has_wages: bool, father_has_overseas: bool,
                  first_year_choice: bool = False, next_spt_met: date | None = None,
                  me: str = DEFAULT_SELF, father: str = DEFAULT_FATHER) -> list[Event]:
    """year 纳税年度的申报截止日（落在 year + 1 年）。status 是该年度在「联邦税」页实际使用的身份。

    first_year_choice：这一年是靠 First-Year Choice 才成为双重身份年。这时要等满足次年的 SPT 才能交 1040，
    4 月 15 日先交 Form 4868 延期（Pub 519）；next_spt_met 是按出入境记录推算的次年满足 SPT 的日期。
    """
    try:
        p = load_params(year)
    except ParamError:
        return []
    if "filing_deadlines" not in p.data:
        return []

    def at(key: str) -> tuple[date, object]:
        ref = p.ref(f"filing_deadlines.{key}")
        m, d = (int(x) for x in str(ref.value).split("-"))
        return date(year + 1, m, d), ref

    out = []
    labels = {"resident": "全年居民", "nonresident": "全年非居民", "dual_status": "双重身份年"}
    who = f"{year} 年按{labels.get(status, status)}"
    if status == "dual_status" and first_year_choice and "fyc_extension_4868" in p.data["filing_deadlines"]:
        d, ref = at("fyc_extension_4868")
        d2, ref2 = at("fyc_extended")
        ext = next_business_day(d2)
        out.append(Event(next_business_day(d), f"{year} 年申请延期（Form 4868），按全年非居民估税缴款", "tax", me,
                         note=f"{year} 年按双重身份（First-Year Choice）；{ref.note}", source=ref.source))
        if next_spt_met and next_spt_met <= ext:
            out.append(Event(next_spt_met, f"可以提交 {year} 年 1040 和 First-Year Choice 声明", "tax", me,
                             note=f"按出入境记录推算 {next_spt_met} 满足 {year + 1} 年 SPT；{ref2.note}",
                             source=ref2.source, end=ext))
        else:
            when = f"要到 {next_spt_met} 才满足" if next_spt_met else "目前推算不满足"
            out.append(Event(ext, f"{year} 年延期申报截止（1040 + First-Year Choice 声明）", "tax", me,
                             note=f"按出入境记录，{year + 1} 年 SPT {when}，晚于延期截止日，请和 CPA 确认；{ref2.note}",
                             source=ref2.source))
        d, ref = at("fbar")
        d2, ref2 = at("fbar_extended")
        out.append(Event(next_business_day(d), f"{year} 年 FBAR 截止（境外账户合计超过 $10,000 时）", "tax", me,
                         note=f"{who}；{ref.note}", source=ref.source, end=next_business_day(d2)))
    elif status in ("resident", "dual_status"):
        d, ref = at("us_resident_1040")
        out.append(Event(next_business_day(d), f"{year} 年联邦税申报截止（1040）", "tax", me, note=f"{who}；{ref.note}",
                         source=ref.source))
        d, ref = at("fbar")
        d2, ref2 = at("fbar_extended")
        out.append(Event(next_business_day(d), f"{year} 年 FBAR 截止（境外账户合计超过 $10,000 时）", "tax", me,
                         note=f"{who}；{ref.note}", source=ref.source, end=next_business_day(d2)))
    else:
        d, ref = at("us_nonresident_wages" if has_wages else "us_nonresident_no_wages")
        form = "1040-NR + Form 8843" if has_wages else "1040-NR 或只交 Form 8843"
        out.append(Event(next_business_day(d), f"{year} 年联邦税申报截止（{form}）", "tax", me,
                         note=f"{who}；{ref.note}", source=ref.source))
    if father_has_overseas:
        d1, ref1 = at("cn_overseas_income_start")
        d2, _ = at("cn_overseas_income_end")
        out.append(Event(d1, f"{father} {year} 年境外所得（AAPL）个税申报窗口", "tax", father, note=ref1.note,
                         source=ref1.source, end=d2))
    return out


def build_events(conn: sqlite3.Connection, today: date, quotes: dict[str, Quote],
                 statuses: dict[int, str], wages: dict[int, bool], horizon_days: int = 548,
                 fyc: dict[int, date | None] | None = None) -> list[Event]:
    """未来约 18 个月内的事项（截止窗口还没结束的也算），按日期排序。statuses / wages 按纳税年度给出；
    fyc 列出靠 First-Year Choice 成为双重身份的年份，值为次年满足 SPT 的日期（推算不出时为 None）。"""
    fyc = fyc or {}
    own = owners(conn)
    me, father = member_names(conn)
    until = today + timedelta(days=horizon_days)
    ev: list[Event] = []

    for t in rows(conn, "SELECT * FROM cash_transfers ORDER BY settle_date"):
        amt, who = D(t["amount"]), member_of(own, f"cash:{t['account']}")
        if amt < 0:                     # 转出：按出账日列出
            settle = date.fromisoformat(t["settle_date"])
            if settle >= today:
                ev.append(Event(settle, f"{t['note'] or '转出'}：从账户转出", "cash", who, amt,
                                note=f"{account_label(t['account'])}，{t['settle_date']} 出账"))
            continue
        avail = date.fromisoformat(t["available_date"] or t["settle_date"])
        if avail >= today:
            ev.append(Event(avail, f"{t['note'] or '转入'}可以使用", "cash", who, amt,
                            note=f"{account_label(t['account'])}，{t['settle_date']} 入账"))

    for pl in rows(conn, "SELECT * FROM cash_plans ORDER BY id"):
        md = parse_when(pl["expected"])
        if md and (md[0] >= today.replace(day=1)):
            ev.append(Event(md[0], pl["label"], "cash", me, D(pl["amount"]), note=pl["note"] or "", month_only=md[1]))

    a = aapl_holdings(conn)
    aq = quotes.get("AAPL")
    # 和资产负债表用同一套分配：只看今天及以后的行，合计不超过未归属股数
    for v, q in (vest_allocation(conn, today, a["rs_unvested"])[0] if a else []):
        ev.append(Event(date.fromisoformat(v["expected_date"]), f"AAPL 归属 {q.normalize():f} 股", "vest",
                        member_of(own, "aapl"), q * aq.close if aq else None,
                        note="按当前股价估算市值" if aq else "缺 AAPL 行情"))

    e = eb5_status(conn, today)
    if e and not e["returned"]:
        ev.append(Event(e["expected_return_date"], "EB-5 预计回款", "invest", member_of(own, "eb5"), e["principal"],
                        note="日期可在「数据管理」修改；回款后标记已回款，会转成现金"))

    # 批次转长期：同一个月合并成一条（逐批列出太多），备注里按日期写明哪些批次
    by_month: dict[tuple, list] = {}
    for x in upcoming_long_term(load_lots(conn), today, 365):
        lot = x["lot"]
        by_month.setdefault((x["long_term_date"].replace(day=1), member_of(own, f"schwab:{lot.account}")), []).append(x)
    for (m, member), xs in sorted(by_month.items()):
        xs.sort(key=lambda x: (x["long_term_date"], x["lot"].ticker))
        per_ticker: dict[str, int] = {}
        for x in xs:
            per_ticker[x["lot"].ticker] = per_ticker.get(x["lot"].ticker, 0) + 1
        detail = "；".join(f"{x['long_term_date']:%m-%d} {x['lot'].ticker} {x['lot'].quantity.normalize():f} 股" for x in xs)
        ev.append(Event(xs[0]["long_term_date"], f"{len(xs)} 批转为长期：" + "、".join(f"{t} {n} 批" for t, n in per_ticker.items()),
                        "lot", member, end=xs[-1]["long_term_date"] if len(xs) > 1 else None,
                        note=f"{detail}。这些日期及以后卖出按长期资本利得计税（买入日 + 1 天再加 1 年，Pub 550）",
                        source="https://www.irs.gov/publications/p550"))

    # 爸爸的境外所得：当年还持有 AAPL，或者当年卖过、收过股息（全部卖掉的那一年同样要申报）
    holds_aapl = bool(a and a["total"] > 0)
    for y in sorted(statuses):
        income_y = any(rows(conn, sql, (str(y),)) for sql in (
            "SELECT 1 FROM aapl_sales WHERE substr(sale_date, 1, 4) = ? LIMIT 1",
            "SELECT 1 FROM aapl_dividends WHERE substr(pay_date, 1, 4) = ? LIMIT 1"))
        ev.extend(tax_deadlines(y, statuses[y], wages.get(y, False), holds_aapl or income_y,
                                first_year_choice=y in fyc, next_spt_met=fyc.get(y), me=me, father=father))

    for c in rows(conn, "SELECT * FROM calendar_events ORDER BY event_date, id"):
        md = parse_when(c["event_date"])
        if md:
            ev.append(Event(md[0], c["title"], "custom", c["member"] or "", D(c["amount"]) if c["amount"] else None,
                            note=c["note"] or "", month_only=md[1], custom_id=c["id"], done=bool(c["done"])))

    # 只写到月份的事项整个月都保留；投资回款（EB-5）这类大额事项不受 18 个月的范围限制
    keep = [x for x in ev if x.last_day >= today and (x.start <= until or x.kind == "invest")]
    return sorted(keep, key=lambda x: x.sort_key)

