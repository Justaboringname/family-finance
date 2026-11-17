"""美国税务居民身份：由出入境记录逐日推算。

规则（参数见 tax_params/<年>.json 的 us_residency）：
- 入境日、出境日都算在美（当天任何时间在美即算一天）。
- F-1 学生是「豁免个人」：前 5 个日历年（有任何一天以 F-1 在美就算一年）的 F-1 天数不计入实际居住测试。
- 实际居住测试（SPT）：今年计入天数 ≥31，且 今年 + 去年×1/3 + 前年×1/6 ≥183。
- 30% 资本利得规则看的是当年在美总天数（含 F-1 天数）。
- First-Year Choice：今年不满足 SPT、去年非居民、明年满足 SPT；今年有连续 31 天计入天数；
  从这 31 天第一天到年底，计入天数（最多 5 天离境视为在美）≥75%。居民起始日 = 最早满足条件的那段的第一天。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction

from .db import rows
from .params import TaxParams, load_params

EXEMPT_STUDENT_STATUSES = {"F-1"}
STATUS_CHOICES = ["F-1", "parole", "其他"]


@dataclass
class TravelEvent:
    event_date: date
    direction: str          # entry / exit
    status: str | None = None
    planned: bool = False
    note: str = ""
    id: int | None = None


def load_travel(conn: sqlite3.Connection) -> list[TravelEvent]:
    return [
        TravelEvent(date.fromisoformat(r["event_date"]), r["direction"], r["status"], bool(r["planned"]),
                    r["note"] or "", r["id"])
        for r in rows(conn, "SELECT * FROM travel ORDER BY event_date, id")
    ]


def daily_presence(events: list[TravelEvent], start: date, end: date) -> dict[date, str]:
    """返回 {日期: 当天身份}，只包含在美的日子。最后一次入境后若无出境，视为一直在美到 end。"""
    evs = sorted(events, key=lambda e: e.event_date)
    by_day: dict[date, list[TravelEvent]] = {}
    for e in evs:
        by_day.setdefault(e.event_date, []).append(e)

    in_us = False
    status: str | None = None
    if evs and evs[0].direction == "exit":
        # 第一条是出境：之前视为在美，身份用出境记录上的身份
        in_us, status = True, evs[0].status
    out: dict[date, str] = {}
    first_day = min(start, evs[0].event_date) if evs else start
    d = first_day
    while d <= end:
        todays = by_day.get(d, [])
        if todays:
            entries = [e for e in todays if e.direction == "entry"]
            exits = [e for e in todays if e.direction == "exit"]
            if entries:
                status = entries[-1].status or status
            present_today = in_us or bool(entries) or bool(exits)
            if entries and exits:
                end_in_us = in_us  # 同一天进出：之前在美则当天往返，仍在美；之前不在美则当天入境又离开
            elif entries:
                end_in_us = True
            else:
                end_in_us = False
            if present_today and d >= start:
                out[d] = status or "其他"
            in_us = end_in_us
        elif in_us and d >= start:
            out[d] = status or "其他"
        d += timedelta(days=1)
    return out


@dataclass
class FycResult:
    eligible: bool = False
    start: date | None = None
    checks: list[tuple[str, bool, str]] = field(default_factory=list)  # (条件, 是否满足, 说明)


@dataclass
class YearResidency:
    year: int
    us_days: int = 0                 # 在美总天数（含 F-1）
    counted_days: int = 0            # 计入实际居住测试的天数
    exempt_days: int = 0
    f1_days: int = 0
    exempt_year: bool = False        # 本年是否为 F-1 豁免年
    exempt_years_used: int = 0       # 截至本年已用掉的豁免年数
    weighted: Fraction = Fraction(0)
    spt_met: bool = False
    green_card_date: date | None = None
    default_status: str = "nonresident"
    residency_start: date | None = None
    status: str = "nonresident"      # 考虑 First-Year Choice 选择后的身份
    fyc: FycResult = field(default_factory=FycResult)
    fyc_elected: bool = False
    projected: bool = False          # 是否包含今天之后（预计/计划）的天数
    spt_met_date: date | None = None  # 本年哪天满足 SPT（计入天数累计）
    notes: list[str] = field(default_factory=list)


def compute_residency(events: list[TravelEvent], years: list[int], today: date,
                      green_card_date: date | None = None, fyc_elect: dict[int, bool] | None = None,
                      params_for=load_params) -> dict[int, YearResidency]:
    fyc_elect = fyc_elect or {}
    if not events:
        return {y: YearResidency(y, notes=["没有出入境记录"]) for y in years}
    first_year = min(e.event_date.year for e in events)
    y_lo = min(first_year, min(years) - 2)
    y_hi = max(years) + 1
    pres = daily_presence(events, date(y_lo, 1, 1), date(y_hi, 12, 31))

    # 先确定每年是否为 F-1 豁免年（按年份顺序累计）
    res: dict[int, YearResidency] = {}
    used = 0
    p0 = _safe_params(params_for, max(years)) if years else None
    limit = p0.v("us_residency.student_exempt_calendar_years") if p0 else 5
    counted_by_day: dict[date, bool] = {}
    for y in range(y_lo, y_hi + 1):
        r = YearResidency(y)
        days = [d for d in pres if d.year == y]
        f1 = [d for d in days if pres[d] in EXEMPT_STUDENT_STATUSES]
        r.us_days = len(days)
        r.f1_days = len(f1)
        if f1 and used < limit:
            r.exempt_year = True
            used += 1
        elif f1:
            r.notes.append(f"F-1 豁免已用满 {limit} 个日历年，本年 F-1 天数计入")
        r.exempt_years_used = used
        for d in days:
            exempt = r.exempt_year and pres[d] in EXEMPT_STUDENT_STATUSES
            counted_by_day[d] = not exempt
        r.exempt_days = sum(1 for d in days if not counted_by_day[d])
        r.counted_days = r.us_days - r.exempt_days
        r.projected = any(d > today for d in days)
        res[y] = r

    def counted(y: int) -> int:
        return res[y].counted_days if y in res else 0

    for y in range(y_lo, y_hi + 1):
        p = _safe_params(params_for, y)
        w = p.fractions("us_residency.spt_weights")
        r = res[y]
        r.weighted = w[0] * counted(y) + w[1] * counted(y - 1) + w[2] * counted(y - 2)
        min_days = p.v("us_residency.spt_min_current_year_days")
        thr = p.v("us_residency.spt_threshold")
        r.spt_met = counted(y) >= min_days and r.weighted >= thr
        if r.spt_met:
            base = w[1] * counted(y - 1) + w[2] * counted(y - 2)
            n = 0
            for d in sorted(x for x in counted_by_day if x.year == y and counted_by_day[x]):
                n += 1
                if n >= min_days and base + n >= thr:
                    r.spt_met_date = d
                    break
        if green_card_date and green_card_date.year == y:
            r.green_card_date = green_card_date

    prior_resident = False
    for y in range(y_lo, y_hi + 1):
        r = res[y]
        p = _safe_params(params_for, y)
        gc_active = green_card_date is not None and green_card_date <= date(y, 12, 31)
        starts = []
        if r.spt_met:
            first_counted = min((d for d in counted_by_day if d.year == y and counted_by_day[d]), default=None)
            if first_counted:
                starts.append(first_counted)
        if gc_active:
            gc_start = max(green_card_date, date(y, 1, 1))
            first_gc = min((d for d in pres if d.year == y and d >= gc_start), default=None)
            if first_gc:
                starts.append(first_gc)
        if starts:
            start = date(y, 1, 1) if prior_resident else min(starts)
            r.residency_start = start
            r.default_status = "resident" if start == date(y, 1, 1) else "dual_status"
            if not prior_resident and start != date(y, 1, 1):
                r.notes.append(f"首个居民年：居民起始日 {start}（§7701(b)(2)(A)）")
        else:
            r.default_status = "nonresident"
            r.fyc = check_fyc(y, counted_by_day, res, prior_resident, p)
        r.status = r.default_status
        r.fyc_elected = bool(fyc_elect.get(y))
        if r.fyc_elected and r.default_status == "nonresident":
            if r.fyc.eligible:
                r.status = "dual_status"
                r.residency_start = r.fyc.start
            else:
                r.notes.append("勾选了 First-Year Choice，但条件不满足，仍按非居民")
        prior_resident = r.status in ("resident", "dual_status")
    return {y: res[y] for y in years if y in res}


def _safe_params(params_for, y: int) -> TaxParams:
    from .params import ParamError, available_years

    try:
        return params_for(y)
    except ParamError:
        ys = available_years()
        nearest = min(ys, key=lambda x: abs(x - y))
        return params_for(nearest)


def check_fyc(y: int, counted_by_day: dict[date, bool], res: dict[int, YearResidency], prior_resident: bool,
              p: TaxParams) -> FycResult:
    out = FycResult()
    need_run = p.v("us_residency.fyc_consecutive_days")
    ratio = p.dec("us_residency.fyc_presence_ratio")
    grace = p.v("us_residency.fyc_absence_grace_days")

    out.checks.append(("当年不满足实际居住测试", True, f"计入 {res[y].counted_days} 天，加权 {float(res[y].weighted):.2f}"))
    out.checks.append(("上一年不是居民", not prior_resident, ""))
    nxt = res.get(y + 1)
    next_ok = bool(nxt and nxt.spt_met)
    detail = ""
    if nxt:
        detail = f"{y + 1} 年计入 {nxt.counted_days} 天，加权 {float(nxt.weighted):.2f}"
        if nxt.projected:
            detail += "（含预计天数）"
        if nxt.spt_met_date:
            detail += f"，预计 {nxt.spt_met_date} 满足"
    out.checks.append((f"{y + 1} 年满足实际居住测试", next_ok, detail))

    days = [date(y, 1, 1) + timedelta(days=i) for i in range((date(y, 12, 31) - date(y, 1, 1)).days + 1)]
    is_c = [counted_by_day.get(d, False) for d in days]
    start = None
    run_found = False
    for i in range(len(days) - need_run + 1):
        if all(is_c[i:i + need_run]):
            run_found = True
            period = is_c[i:]
            present = sum(period)
            absent = len(period) - present
            deemed = present + min(grace, absent)
            if deemed >= ratio * len(period):
                start = days[i]
                out.checks.append((f"连续 {need_run} 天计入且 75% 测试", True,
                                   f"窗口从 {start} 起；测试期 {len(period)} 天，在美 {present} 天"
                                   f"（+{min(grace, absent)} 天缓冲）= {deemed / len(period):.1%}"))
                break
    if start is None:
        out.checks.append((f"连续 {need_run} 天计入且 75% 测试", False,
                           "没有连续 31 天的计入天数" if not run_found else "有连续 31 天，但没有一段能满足 75%"))
    out.start = start
    out.eligible = (not prior_resident) and next_ok and start is not None
    return out
