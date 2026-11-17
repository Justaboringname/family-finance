"""美国联邦税（单身），按自然年；纽约州 / 纽约市税为可选估算。

身份：
- resident：全年居民。短期收益按普通收入累进；长期收益按 0/15/20% 叠在普通收入之上；
  可用标准扣除；MAGI 超过门槛的部分计 3.8% NIIT；净亏损抵 $3,000，剩余按长短期性质结转。
- nonresident：全年非居民。当年在美 ≥183 天：美国来源净资本利得统一 30%，不分长短期、无扣除；
  不满 183 天为 0。亏损只抵同期收益，不结转。
- dual_status：以居民起始日为界拆成两段，居民段按居民规则但不能用标准扣除；非居民段按上一条。
所有税率、档位从 tax_params/<年>.json 读取。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .money import ZERO, D, fmt_usd, q2
from .params import TaxParams
from .trace import Trace

INCOME_KINDS = {
    "wages": "工资",
    "interest": "利息（含货基分红中的利息部分）",
    "nonqualified_dividends": "非合格股息",
    "qualified_dividends": "合格股息",
    "other_ordinary": "其他普通收入",
}
STATUS_LABELS = {"resident": "全年居民", "nonresident": "全年非居民", "dual_status": "双重身份年"}


@dataclass
class GainItem:
    sale_date: date
    ticker: str
    quantity: Decimal
    proceeds: Decimal          # 扣除手续费后的卖出所得
    cost_basis: Decimal
    long_term: bool
    wash_disallowed: Decimal = ZERO   # 被 wash sale 禁止的亏损（正数），从亏损里加回
    label: str = ""

    @property
    def gain(self) -> Decimal:
        return self.proceeds - self.cost_basis + self.wash_disallowed


@dataclass
class IncomeItem:
    kind: str
    amount: Decimal
    period: str = "resident"   # resident / nonresident（只对双重身份年有意义）
    note: str = ""


@dataclass
class TaxProfile:
    year: int
    status: str                        # resident / nonresident / dual_status
    residency_start: date | None = None
    us_days: int | None = None         # 当年在美总天数（30% 规则用）
    itemized: Decimal = ZERO
    carry_st: Decimal = ZERO           # 从上一年结转来的短期亏损（正数）
    carry_lt: Decimal = ZERO
    ny_status: str = "off"             # off / resident / part_year / nonresident
    ny_start: date | None = None
    nyc: bool = False


@dataclass
class TaxResult:
    profile: TaxProfile
    regular_tax: Decimal = ZERO        # 居民（及非居民 ECI）部分的累进税 + 资本利得税
    niit: Decimal = ZERO
    nra_tax: Decimal = ZERO            # 非居民 30% 部分
    ny_tax: Decimal = ZERO
    nyc_tax: Decimal = ZERO
    agi: Decimal = ZERO
    deduction: Decimal = ZERO
    taxable_income: Decimal = ZERO
    ordinary_ti: Decimal = ZERO
    preferential: Decimal = ZERO
    ltcg_bands: list = field(default_factory=list)   # [(税率, 金额)]
    net_st: Decimal = ZERO
    net_lt: Decimal = ZERO
    loss_deduction: Decimal = ZERO
    carry_out_st: Decimal = ZERO
    carry_out_lt: Decimal = ZERO
    nr_net_gain: Decimal = ZERO
    resident_gain: Decimal = ZERO
    trace: Trace = field(default_factory=Trace)

    @property
    def federal_exact(self) -> Decimal:
        return self.regular_tax + self.niit + self.nra_tax

    @property
    def federal(self) -> Decimal:
        return q2(self.federal_exact)

    @property
    def state_local(self) -> Decimal:
        return q2(self.ny_tax + self.nyc_tax)

    @property
    def total(self) -> Decimal:
        return self.federal + self.state_local


# ---------- 基础计算 ----------

def bracket_tax(amount: Decimal, brackets: list[tuple]) -> Decimal:
    """累进税：brackets = [(上限或 None, 税率), ...]。"""
    tax, lo = ZERO, ZERO
    for cap, rate, *_ in brackets:
        if amount <= lo:
            break
        top = amount if cap is None else min(amount, cap)
        tax += (top - lo) * rate
        if cap is None:
            break
        lo = cap
    return tax


def table_tax(amount: Decimal, rows: list[tuple]) -> Decimal:
    """按官方税表：rows = [(上限或 None, 税率, 本档起点的税额)]，税 = 起点税额 + 税率 × 超出部分。"""
    if amount <= 0:
        return ZERO
    lo = ZERO
    for cap, rate, base in rows:
        if cap is None or amount <= cap:
            return base + (amount - lo) * rate
        lo = cap
    raise AssertionError("税表缺最后一档")


def band_overlap(lo: Decimal, hi: Decimal, brackets: list[tuple]) -> list[tuple[Decimal, Decimal]]:
    """[lo, hi] 区间落在各档的金额：返回 [(税率, 金额)]。"""
    out, b_lo = [], ZERO
    for cap, rate, *_ in brackets:
        b_hi = cap
        top = hi if b_hi is None else min(hi, b_hi)
        bottom = max(lo, b_lo)
        if top > bottom:
            out.append((rate, top - bottom))
        if b_hi is None:
            break
        b_lo = b_hi
    return out


def split_by_period(profile: TaxProfile, gains: list[GainItem], incomes: list[IncomeItem]):
    if profile.status == "resident":
        return gains, [], incomes, []
    if profile.status == "nonresident":
        return [], gains, [], incomes
    start = profile.residency_start
    if start is None:
        raise ValueError("双重身份年需要居民起始日")
    res_g = [g for g in gains if g.sale_date >= start]
    nr_g = [g for g in gains if g.sale_date < start]
    res_i = [i for i in incomes if i.period == "resident"]
    nr_i = [i for i in incomes if i.period != "resident"]
    return res_g, nr_g, res_i, nr_i


def capital_loss_carryover(net_st: Decimal, net_lt: Decimal, loss_ded: Decimal, ti_unfloored: Decimal):
    """Schedule D「Capital Loss Carryover Worksheet」第 1–13 行。"""
    line3 = max(ZERO, ti_unfloored + loss_ded)
    line4 = min(loss_ded, line3)
    st_co = lt_co = ZERO
    line5 = -net_st if net_st < 0 else ZERO
    if net_st < 0:
        line6 = max(ZERO, net_lt)
        st_co = max(ZERO, line5 - (line4 + line6))
    if net_lt < 0:
        line9 = -net_lt
        line10 = max(ZERO, net_st)
        line11 = max(ZERO, line4 - line5)
        lt_co = max(ZERO, line9 - (line10 + line11))
    return st_co, lt_co


# ---------- 主函数 ----------

def compute_tax(profile: TaxProfile, gains: list[GainItem], incomes: list[IncomeItem], p: TaxParams) -> TaxResult:
    r = TaxResult(profile=profile)
    t = r.trace
    status = profile.status
    if p.provisional:
        t.warn(f"{p.year} 年参数为临时值（{p.status}）", p.note)
    t.add(f"身份：{STATUS_LABELS.get(status, status)}", None,
          f"居民起始日 {profile.residency_start}" if status == "dual_status" else "")

    res_g, nr_g, res_i, nr_i = split_by_period(profile, gains, incomes)

    # ===== 居民部分（含非居民期间的工资 ECI）=====
    st_g = sum((g.gain for g in res_g if not g.long_term), ZERO)
    lt_g = sum((g.gain for g in res_g if g.long_term), ZERO)
    wash = sum((g.wash_disallowed for g in gains), ZERO)
    if wash:
        t.add("wash sale：被禁止的亏损已加回", wash, "IRC §1091：卖出前后 30 天内买入实质相同的股票，亏损不能扣除，"
              "加到替代批次的成本上", [p.ref("us_federal.wash_sale_window_days")])
    resident_like = status in ("resident", "dual_status")
    carry_st = profile.carry_st if resident_like else ZERO
    carry_lt = profile.carry_lt if resident_like else ZERO
    net_st = st_g - carry_st
    net_lt = lt_g - carry_lt
    r.net_st, r.net_lt = net_st, net_lt
    r.resident_gain = st_g + lt_g
    if res_g or carry_st or carry_lt:
        t.add("居民期间：短期收益合计", st_g, "持有 ≤1 年")
        t.add("居民期间：长期收益合计", lt_g, "持有 >1 年", [p.ref("us_federal.long_term_holding_rule")])
        if carry_st or carry_lt:
            t.add("上年结转亏损（短期 / 长期）", -(carry_st + carry_lt), f"短期 {fmt_usd(carry_st)}，长期 {fmt_usd(carry_lt)}")

    total_cap = net_st + net_lt
    limit = p.dec("us_federal.capital_loss_ordinary_offset_limit")
    if total_cap < 0:
        r.loss_deduction = min(limit, -total_cap)
        cap_in_agi = -r.loss_deduction
        t.add("净资本亏损抵普通收入", cap_in_agi, "先同类相抵，再跨类相抵；每年最多抵 $3,000",
              [p.ref("us_federal.capital_loss_ordinary_offset_limit")])
    else:
        cap_in_agi = total_cap

    def amt(items, kind):
        return sum((i.amount for i in items if i.kind == kind), ZERO)

    wages = amt(res_i, "wages")
    # 非居民期间的工资和其他普通收入视为 ECI，并入累进税（没有标准扣除）
    eci_wages = amt(nr_i, "wages") + amt(nr_i, "other_ordinary")
    interest = amt(res_i, "interest")
    nq_div = amt(res_i, "nonqualified_dividends")
    q_div = amt(res_i, "qualified_dividends")
    other = amt(res_i, "other_ordinary")
    ordinary = wages + eci_wages + interest + nq_div + other
    for label, v in (("工资", wages), ("非居民期间工资/其他收入（ECI）", eci_wages), ("利息", interest), ("非合格股息", nq_div),
                     ("合格股息", q_div), ("其他普通收入", other)):
        if v:
            t.add(f"其他收入：{label}", v, "", kind="income")

    r.agi = ordinary + q_div + cap_in_agi
    std = p.dec("us_federal.standard_deduction_single")
    if status == "resident":
        r.deduction = max(std, profile.itemized)
        t.add("扣除额", -r.deduction,
              "标准扣除与列举扣除取大" if profile.itemized else "全年居民可用标准扣除",
              [p.ref("us_federal.standard_deduction_single")])
    else:
        r.deduction = profile.itemized
        rule = "双重身份年不能用标准扣除，只能列举扣除" if status == "dual_status" else "非居民没有标准扣除"
        if r.agi or profile.itemized:
            t.add("扣除额", -r.deduction, rule, [p.ref("us_federal.dual_status_standard_deduction_allowed")])
    ti_unfloored = r.agi - r.deduction
    r.taxable_income = max(ZERO, ti_unfloored)

    if net_lt > 0 and total_cap > 0:
        ncg = net_lt + min(ZERO, net_st)
    else:
        ncg = ZERO
    r.preferential = min(r.taxable_income, max(ZERO, ncg) + q_div)
    r.ordinary_ti = r.taxable_income - r.preferential

    ord_br = p.brackets("us_federal.ordinary_brackets_single")
    lt_br = p.brackets("us_federal.ltcg_brackets_single")
    if r.taxable_income > 0:
        t.add("应税所得", r.taxable_income, "AGI − 扣除额", kind="income")
        tax_ord = bracket_tax(r.ordinary_ti, ord_br)
        t.add("普通收入部分的税", tax_ord,
              f"普通应税所得 {fmt_usd(r.ordinary_ti)} 按累进税率（短期收益在这里）",
              [p.ref("us_federal.ordinary_brackets_single")], kind="tax")
        r.ltcg_bands = band_overlap(r.ordinary_ti, r.taxable_income, lt_br)
        tax_pref = ZERO
        for rate, a in r.ltcg_bands:
            tax_pref += a * rate
            t.add(f"长期收益/合格股息 {rate * 100:.0f}% 档", a * rate,
                  f"{fmt_usd(a)} × {rate * 100:.0f}%（叠在普通收入之上）",
                  [p.ref("us_federal.ltcg_brackets_single")], kind="tax")
        regular = tax_ord + tax_pref
        alt = bracket_tax(r.taxable_income, ord_br)
        if alt < regular:
            regular = alt
            t.add("取较低者：全部按普通税率", alt, "Qualified Dividends and Capital Gain Tax Worksheet")
        r.regular_tax = regular

    # NIIT：全年居民或双重身份年的居民期间
    if resident_like:
        nii = max(ZERO, interest + nq_div + q_div + cap_in_agi)
        magi = r.agi - eci_wages
        thr = p.dec("us_federal.niit_threshold_single")
        rate = p.dec("us_federal.niit_rate")
        base = max(ZERO, min(nii, magi - thr))
        r.niit = base * rate
        refs = [p.ref("us_federal.niit_rate"), p.ref("us_federal.niit_threshold_single")]
        if status == "dual_status":
            refs.append(p.ref("us_federal.niit_dual_status_resident_portion_only"))
        if r.niit or magi > thr:
            t.add("NIIT 3.8%", r.niit, f"min(投资净收入 {fmt_usd(nii)}, MAGI {fmt_usd(magi)} − {fmt_usd(thr)}) × 3.8%",
                  refs, kind="tax")
        else:
            t.add("NIIT", ZERO, f"MAGI {fmt_usd(magi)} 未超过 {fmt_usd(thr)}", refs)

    if total_cap < 0 and resident_like:
        r.carry_out_st, r.carry_out_lt = capital_loss_carryover(net_st, net_lt, r.loss_deduction, ti_unfloored)
        t.add("结转到下一年的亏损", -(r.carry_out_st + r.carry_out_lt),
              f"短期 {fmt_usd(r.carry_out_st)}，长期 {fmt_usd(r.carry_out_lt)}（Capital Loss Carryover Worksheet）",
              [p.ref("us_federal.capital_loss_ordinary_offset_limit")])

    # ===== 非居民部分：30% 规则 =====
    if nr_g or status == "nonresident":
        r.nr_net_gain = sum((g.gain for g in nr_g), ZERO)
        days = profile.us_days
        thr_days = p.v("us_federal.nra_183_day_threshold")
        rate30 = p.dec("us_federal.nra_capital_gains_rate")
        refs = [p.ref("us_federal.nra_capital_gains_rate"), p.ref("us_federal.nra_183_day_threshold"),
                p.ref("us_federal.nra_183_count_exempt_days")]
        if status == "dual_status":
            refs.append(p.ref("us_federal.nra_183_dual_status_counts_full_year"))
        label = "非居民期间" if status == "dual_status" else "非居民"
        if nr_g:
            t.add(f"{label}：净资本利得（不分长短期）", r.nr_net_gain, "美国来源收益减亏损", kind="income")
        if days is None:
            t.warn("缺少当年在美总天数，无法判断 30% 规则", "请在「税务身份」录入出入境记录，或在本页手动填写")
        elif days >= thr_days:
            r.nra_tax = max(ZERO, r.nr_net_gain) * rate30
            if nr_g:
                t.add(f"{label}：30% 资本利得税", r.nra_tax,
                      f"当年在美 {days} 天 ≥ {thr_days} 天（含 F-1 天数）→ 净收益 × 30%，无扣除", refs, kind="tax")
        elif nr_g:
            t.add(f"{label}：资本利得税", ZERO, f"当年在美 {days} 天 < {thr_days} 天 → 不征税", refs, kind="tax")
        if r.nr_net_gain < 0:
            t.add(f"{label}亏损作废", r.nr_net_gain, "只能抵同期收益，不能抵 $3,000，也不能结转",
                  [p.ref("us_federal.nra_capital_loss_carryover_allowed")])

    nr_div = [i for i in nr_i if i.kind in ("interest", "nonqualified_dividends", "qualified_dividends")]
    if nr_div:
        t.warn(f"非居民期间的利息/股息 {fmt_usd(sum((i.amount for i in nr_div), ZERO))} 未计入",
               "由券商按协定或 30% 预扣，此处不计算")

    # ===== 纽约州 / 纽约市（可选估算）=====
    if profile.ny_status != "off":
        _compute_ny(r, profile, gains, incomes, p)

    t.add("联邦税合计", r.federal, "", kind="tax")
    return r


# ---------- 纽约州 / 纽约市（估算） ----------

def ny_state_tax(nyagi: Decimal, ti: Decimal, p: TaxParams) -> tuple[Decimal, str]:
    """IT-2105-I (2026) 单身：税表 + Tax computation worksheets 7–11（高收入回收低档优惠）。"""
    br = p.brackets("ny_state.brackets_single")
    sched = table_tax(ti, br)
    phase = p.dec("ny_state.recapture_phase_in")

    def frac(excess: Decimal) -> Decimal:
        return (min(max(excess, ZERO), phase) / phase).quantize(Decimal("0.0001"))

    if nyagi > p.dec("ny_state.top_flat_agi"):
        top_rate = br[-1][1]
        return ti * top_rate, "Worksheet 11：全部按最高税率"
    start = p.dec("ny_state.recapture_flat_rate_start_agi")
    if nyagi <= start:
        return sched, "税率表"
    if ti <= p.dec("ny_state.recapture_ws7_taxable_income_cap"):
        flat = ti * p.dec("ny_state.recapture_flat_rate")
        return sched + (flat - sched) * frac(nyagi - start), "Worksheet 7：回收到统一 5.90%"
    for tier in reversed(p.v("ny_state.recapture_tiers")):
        if ti > D(tier["ti_floor"]) and nyagi > D(tier["agi_floor"]):
            add = D(tier["base"]) + D(tier["increment"]) * frac(nyagi - D(tier["agi_floor"]))
            return sched + add, f"Recapture：基数 {tier['base']} + 增量 {tier['increment']}"
    return sched, "税率表"


def _compute_ny(r: TaxResult, profile: TaxProfile, gains: list[GainItem], incomes: list[IncomeItem], p: TaxParams):
    t = r.trace
    # 纽约从总收入起算：全年所有已实现收益（亏损最多抵 $3,000）+ 全部其他收入
    total_gain = sum((g.gain for g in gains), ZERO) - profile.carry_st - profile.carry_lt
    limit = p.dec("us_federal.capital_loss_ordinary_offset_limit")
    cap = total_gain if total_gain >= 0 else -min(limit, -total_gain)
    other = sum((i.amount for i in incomes), ZERO)
    nyagi = cap + other
    ti = max(ZERO, nyagi - p.dec("ny_state.standard_deduction_single"))
    full, how = ny_state_tax(nyagi, ti, p)
    if profile.ny_status == "resident":
        pct = Decimal(1)
        why = "纽约全年居民"
    elif profile.ny_status == "part_year":
        start = profile.ny_start or date(profile.year, 1, 1)
        ny_part = sum((g.gain for g in gains if g.sale_date >= start), ZERO) + sum(
            (i.amount for i in incomes if i.period == "resident"), ZERO)
        pct = min(Decimal(1), max(ZERO, ny_part / nyagi)) if nyagi > 0 else ZERO
        why = f"纽约部分年居民（{start} 起），按收入比例 {pct:.2%}（IT-203 方法）"
    else:
        ny_src = sum((i.amount for i in incomes if i.kind == "wages"), ZERO)
        pct = min(Decimal(1), max(ZERO, ny_src / nyagi)) if nyagi > 0 else ZERO
        why = f"纽约非居民：只有纽约来源收入（工资）计税，比例 {pct:.2%}；股票收益不是纽约来源"
    r.ny_tax = full * pct
    t.add("纽约州税（估算）", r.ny_tax, f"NYAGI {fmt_usd(nyagi)}，应税 {fmt_usd(ti)}；{how}；{why}",
          [p.ref("ny_state.brackets_single"), p.ref("ny_state.standard_deduction_single"),
           p.ref("ny_state.capital_gains_as_ordinary")], kind="tax")
    if profile.nyc and profile.ny_status in ("resident", "part_year"):
        nyc_full = table_tax(ti, p.brackets("nyc.brackets_single"))
        r.nyc_tax = nyc_full * pct
        t.add("纽约市税（估算）", r.nyc_tax, f"纽约市居民税率表 × {pct:.2%}", [p.ref("nyc.brackets_single")], kind="tax")


# ---------- 篮子：实际税率与边际税率 ----------

def basket_effect(profile: TaxProfile, base_gains: list[GainItem], basket: list[GainItem], incomes: list[IncomeItem],
                  p: TaxParams, probe: Decimal = Decimal(100), base_with_basket: list[GainItem] | None = None) -> dict:
    """base_gains：不含篮子时的已实现卖出；base_with_basket：加入篮子后重新做过 wash sale 的已实现卖出
    （篮子里的亏损可能让已卖出的替代股成本变化），不传则与 base_gains 相同。"""
    with_base = base_with_basket if base_with_basket is not None else base_gains
    base = compute_tax(profile, base_gains, incomes, p)
    with_b = compute_tax(profile, with_base + basket, incomes, p)
    gain = sum((g.gain for g in basket), ZERO)
    inc = with_b.total - base.total
    last_date = max((g.sale_date for g in basket), default=date(profile.year, 12, 31))

    def probe_rate(long_term: bool) -> Decimal:
        extra = GainItem(last_date, "_probe", Decimal(1), probe, ZERO, long_term)
        after = compute_tax(profile, with_base + basket + [extra], incomes, p)
        return (after.total - with_b.total) / probe

    return {
        "base": base,
        "with_basket": with_b,
        "basket_gain": gain,
        "incremental_tax": inc,
        "effective_rate": (inc / gain) if gain > 0 else None,
        "marginal_st": probe_rate(False),
        "marginal_lt": probe_rate(True),
    }
