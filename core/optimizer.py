"""优化提示与选批次。"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal

from .money import ZERO, D
from .params import TaxParams
from .portfolio import Lot, Quote, first_long_term_date, is_long_term
from .us_tax import GainItem, IncomeItem, TaxProfile, compute_tax

# ---------- 选批次 ----------

STRATEGIES = {
    "hifo": "成本最高优先（默认）",
    "tax_order": "税优先：先亏损、再长期小收益、最后短期",
    "lt_hifo": "长期批次优先（各自按成本从高到低）",
    "fifo": "先进先出",
    "lifo": "后进先出",
}


def order_lots(lots: list[Lot], strategy: str, price: Decimal, sale_date: date) -> list[Lot]:
    if strategy == "hifo":
        return sorted(lots, key=lambda l: (-l.cost_per_share, l.open_date))
    if strategy == "fifo":
        return sorted(lots, key=lambda l: l.open_date)
    if strategy == "lifo":
        return sorted(lots, key=lambda l: l.open_date, reverse=True)
    if strategy == "lt_hifo":
        return sorted(lots, key=lambda l: (not is_long_term(l.open_date, sale_date), -l.cost_per_share))
    if strategy == "tax_order":
        def k(l: Lot):
            g = price - l.cost_per_share
            lt = is_long_term(l.open_date, sale_date)
            if g < 0:
                return (0, g)
            return (1 if lt else 2, g)
        return sorted(lots, key=k)
    raise ValueError(strategy)


def allocate(lots: list[Lot], qty: Decimal, strategy: str, price: Decimal, sale_date: date) -> list[tuple[Lot, Decimal]]:
    need = D(qty)
    out = []
    for lot in order_lots(lots, strategy, price, sale_date):
        if need <= 0:
            break
        take = min(need, lot.quantity)
        out.append((lot, take))
        need -= take
    if need > 0:
        raise ValueError(f"股数不够：还差 {need} 股")
    return out


def to_gain_items(allocs: list[tuple[Lot, Decimal]], price: Decimal, sale_date: date, fee: Decimal = ZERO) -> list[GainItem]:
    total_q = sum((q for _, q in allocs), ZERO)
    items = []
    for lot, q in allocs:
        fee_share = fee * q / total_q if total_q else ZERO
        basis = lot.cost_basis * q / lot.quantity
        items.append(GainItem(sale_date, lot.ticker, q, q * price - fee_share, basis,
                              is_long_term(lot.open_date, sale_date), label=f"{lot.ticker} {lot.open_date}"))
    return items


def best_strategy(lots: list[Lot], qty: Decimal, price: Decimal, sale_date: date, profile: TaxProfile,
                  base_gains: list[GainItem], incomes: list[IncomeItem], p: TaxParams, fee: Decimal = ZERO) -> dict:
    """在候选选法里找该情景下税最少的（不是全局最优，是候选中最优）。"""
    results = []
    for key in STRATEGIES:
        allocs = allocate(lots, qty, key, price, sale_date)
        items = to_gain_items(allocs, price, sale_date, fee)
        tax = compute_tax(profile, base_gains + items, incomes, p).total
        results.append({"strategy": key, "allocs": allocs, "items": items, "tax": tax})
    results.sort(key=lambda r: (r["tax"], r["strategy"] != "hifo"))
    return {"best": results[0], "all": results}


# ---------- 0% 档额度 ----------

def zero_band_headroom(profile: TaxProfile, base_gains: list[GainItem], incomes: list[IncomeItem], p: TaxParams,
                       sale_date: date | None = None) -> Decimal | None:
    """再实现多少长期收益，联邦税仍不增加。非居民年不适用（返回 None）。"""
    if profile.status == "nonresident":
        return None
    prof = replace(profile, ny_status="off", nyc=False)
    if sale_date is None:
        sale_date = date(profile.year, 12, 31)
    if profile.status == "dual_status" and profile.residency_start and sale_date < profile.residency_start:
        sale_date = profile.residency_start
    base = compute_tax(prof, base_gains, incomes, p).federal_exact

    def extra(g: Decimal) -> Decimal:
        item = GainItem(sale_date, "_probe", Decimal(1), g, ZERO, True)
        return compute_tax(prof, base_gains + [item], incomes, p).federal_exact - base

    lo, hi = ZERO, Decimal(2_000_000)
    if extra(Decimal("0.01")) > 0:
        return ZERO
    for _ in range(60):
        mid = (lo + hi) / 2
        if extra(mid) <= 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < Decimal("0.01"):
            break
    c = lo.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    while extra(c + Decimal("0.01")) <= 0:
        c += Decimal("0.01")
    return c


# ---------- 提示 ----------

def upcoming_long_term(lots: list[Lot], today: date, within_days: int = 60) -> list[dict]:
    out = []
    for lot in lots:
        d = first_long_term_date(lot.open_date)
        if today < d <= today + timedelta(days=within_days):
            out.append({"lot": lot, "long_term_date": d, "days": (d - today).days})
    return sorted(out, key=lambda x: x["long_term_date"])


def unrealized_losses(lots: list[Lot], quotes: dict[str, Quote]) -> list[dict]:
    out = []
    for lot in lots:
        q = quotes.get(lot.ticker)
        if not q:
            continue
        u = lot.quantity * q.close - lot.cost_basis
        if u < 0:
            out.append({"lot": lot, "unrealized": u})
    return sorted(out, key=lambda x: x["unrealized"])


def harvest_plan(lots: list[Lot], quotes: dict[str, Quote], headroom: Decimal, sale_date: date) -> list[dict]:
    """0% 档内的收益收割：长期且有浮盈的批次，按每股收益从高到低，用整数股填满额度。"""
    cands = []
    for lot in lots:
        q = quotes.get(lot.ticker)
        if not q or not is_long_term(lot.open_date, sale_date):
            continue
        per = q.close - lot.cost_per_share
        if per > 0:
            cands.append((lot, per, q.close))
    cands.sort(key=lambda x: -x[1])
    left = D(headroom)
    plan = []
    for lot, per, price in cands:
        if left <= 0:
            break
        shares = min(lot.quantity, (left / per).to_integral_value(rounding=ROUND_FLOOR))
        if shares <= 0:
            continue
        gain = shares * per
        left -= gain
        plan.append({"lot": lot, "shares": shares, "price": price, "gain": gain,
                     "new_basis": shares * price, "old_basis": shares * lot.cost_per_share})
    return plan
