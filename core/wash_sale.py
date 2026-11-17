"""Wash sale 检测：亏损卖出前后 30 天内买入实质相同的股票（IRC §1091）。

配对规则（Treas. Reg. §1.1091-1(c)–(e)）：亏损卖出按日期顺序，每笔与窗口内「最早的」买入配对，
每次买入只能用一次。被禁止的亏损加到替代股票的成本上，替代股票的持有期并入原股票的持有期。

只看得到本工具里的数据（批次、已记录的卖出、模拟篮子里计划的买入）；
其他账户（如 IRA）的买入需要手动录入成批次或计划买入。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from .money import ZERO


@dataclass
class Acquisition:
    acq_date: date
    ticker: str
    quantity: Decimal
    key: tuple            # (账户, ticker, 买入日) —— 同一次买入
    label: str = ""


@dataclass
class LossLeg:
    sale_date: date
    ticker: str
    quantity: Decimal
    loss: Decimal         # 正数
    own_key: tuple        # 这笔卖出所对应批次的买入 key（自己的买入不算替代）
    open_date: date | None = None
    ref: object = None


@dataclass
class WashHit:
    leg: LossLeg
    replacement: Acquisition
    matched_qty: Decimal
    disallowed: Decimal

    @property
    def tack_days(self) -> int:
        """原股票的持有天数，并入替代股票的持有期。"""
        return (self.leg.sale_date - self.leg.open_date).days if self.leg.open_date else 0


def parse_equivalents(s: str | None) -> list[set[str]]:
    """'GOOG=GOOGL; BRK.A=BRK.B' → [{'GOOG','GOOGL'}, ...]"""
    groups = []
    for part in (s or "").replace(",", ";").split(";"):
        names = {x.strip().upper() for x in part.split("=") if x.strip()}
        if len(names) > 1:
            groups.append(names)
    return groups


def same_security(a: str, b: str, groups: list[set[str]]) -> bool:
    a, b = a.upper(), b.upper()
    if a == b:
        return True
    return any(a in g and b in g for g in groups)


def detect(legs: list[LossLeg], acquisitions: list[Acquisition], window_days: int,
           equivalents: list[set[str]] | None = None) -> list[WashHit]:
    groups = equivalents or []
    remaining = {id(a): a.quantity for a in acquisitions}
    hits: list[WashHit] = []
    for leg in sorted(legs, key=lambda x: x.sale_date):
        if leg.loss <= 0:
            continue
        need = leg.quantity
        lo, hi = leg.sale_date - timedelta(days=window_days), leg.sale_date + timedelta(days=window_days)
        cands = [a for a in acquisitions
                 if same_security(a.ticker, leg.ticker, groups) and lo <= a.acq_date <= hi
                 and a.key != leg.own_key and remaining[id(a)] > 0]
        cands.sort(key=lambda a: a.acq_date)   # 最早的买入优先
        for a in cands:
            if need <= 0:
                break
            take = min(need, remaining[id(a)])
            remaining[id(a)] -= take
            need -= take
            hits.append(WashHit(leg, a, take, leg.loss * take / leg.quantity))
    return hits


def total_disallowed(hits: list[WashHit]) -> Decimal:
    return sum((h.disallowed for h in hits), ZERO)


@dataclass(eq=False)
class _Part:
    """一笔卖出（或被拆开的一部分）。eq=False：按对象身份比较。"""
    idx: int                # 原下标
    item: object            # GainItem
    meta: dict
    tack: int = 0           # 已并入的持有天数
    replacement: bool = False   # 这些股票已经当过一次替代股（每股只能当一次）


def _split(part: _Part, take: Decimal) -> tuple[_Part, _Part | None]:
    it = part.item
    if take >= it.quantity:
        return part, None
    f = take / it.quantity
    g = (it.quantity - take) / it.quantity
    a = _Part(part.idx, replace(it, quantity=take, proceeds=it.proceeds * f, cost_basis=it.cost_basis * f,
                                wash_disallowed=it.wash_disallowed * f), part.meta, part.tack, part.replacement)
    b = _Part(part.idx, replace(it, quantity=it.quantity - take, proceeds=it.proceeds * g, cost_basis=it.cost_basis * g,
                                wash_disallowed=it.wash_disallowed * g), part.meta, part.tack, part.replacement)
    return a, b


def _broker_already_adjusted(loss: _Part, repl: _Part) -> bool:
    """券商只对同一账户、同一证券、已经发生的卖出调整成本（Treas. Reg. §1.6045-1(d)(6)(iv)）。
    满足这些条件且替代批次的成本数据日期不早于亏损卖出日，才认为成本里已经含有这次调整。"""
    adj_on = repl.meta.get("broker_adjusted_on")
    lk, rk = loss.meta["key"], repl.meta["key"]
    return bool(loss.meta.get("real") and adj_on and str(adj_on) >= loss.item.sale_date.isoformat()
                and lk[0] == rk[0] and lk[1] == rk[1])


def apply_wash(items: list, meta: list[dict], acquisitions: list[Acquisition], window_days: int,
               equivalents: list[set[str]] | None = None, is_long_term=None) -> tuple[list[tuple[int, object]], list[WashHit]]:
    """对一组卖出（GainItem）做 wash sale，按卖出日期逐笔处理：
    1. 亏损卖出（用已调整后的成本判断）：与窗口内最早的买入配对，被禁止的亏损记到 wash_disallowed；
    2. 替代股票如果也在这组卖出里：把被禁止的亏损加到它的成本上，持有期并入（按股数拆分）。
       替代股之后再亏损卖出又买回时，递延的亏损会继续传给下一批替代股（连环 wash sale）。
    meta[i] = {"key": 买入 key, "open_date": 买入日, "broker_adjusted_on": 券商成本数据日期或 None,
               "real": 是否已实际成交}
    返回 [(原下标, 调整后的 GainItem)]（替代股可能被拆成多条）和配对结果。
    """
    groups = equivalents or []
    remaining = {id(a): a.quantity for a in acquisitions}
    parts = sorted((_Part(i, it, m) for i, (it, m) in enumerate(zip(items, meta))), key=lambda p: p.item.sale_date)
    hits: list[WashHit] = []
    k = 0
    while k < len(parts):
        p = parts[k]
        it = p.item
        loss = it.cost_basis - it.proceeds
        if loss > 0:
            od = p.meta.get("open_date")
            leg = LossLeg(it.sale_date, it.ticker, it.quantity, loss, p.meta["key"],
                          od - timedelta(days=p.tack) if od else None, p.idx)
            lo, hi = it.sale_date - timedelta(days=window_days), it.sale_date + timedelta(days=window_days)
            cands = sorted((a for a in acquisitions
                            if same_security(a.ticker, it.ticker, groups) and lo <= a.acq_date <= hi
                            and a.key != leg.own_key and remaining[id(a)] > 0), key=lambda a: a.acq_date)
            need, total = it.quantity, ZERO
            for a in cands:
                if need <= 0:
                    break
                take = min(need, remaining[id(a)])
                remaining[id(a)] -= take
                need -= take
                hit = WashHit(leg, a, take, loss * take / it.quantity)
                hits.append(hit)
                total += hit.disallowed
                _assign(parts, p, hit, is_long_term)
            p.item = replace(p.item, wash_disallowed=total)
            k = parts.index(p)
        k += 1
    return [(x.idx, x.item) for x in parts], hits


def _assign(parts: list[_Part], loss: _Part, hit: WashHit, is_long_term) -> None:
    """把一次配对的调整分给这组卖出里卖掉的替代股（按卖出日期，先到先得）。"""
    left = hit.matched_qty
    per_share = hit.disallowed / hit.matched_qty
    for q in sorted((x for x in parts if x is not loss and not x.replacement and x.meta["key"] == hit.replacement.key),
                    key=lambda x: x.item.sale_date):
        if left <= 0:
            break
        take = min(q.item.quantity, left)
        matched, rest = _split(q, take)
        add = ZERO if _broker_already_adjusted(loss, q) else per_share * take
        matched.tack = q.tack + hit.tack_days
        od = q.meta.get("open_date")
        lt = matched.item.long_term
        if od and is_long_term and matched.tack:
            lt = is_long_term(od - timedelta(days=matched.tack), matched.item.sale_date)
        matched.item = replace(matched.item, cost_basis=matched.item.cost_basis + add, long_term=lt,
                               label=f"{matched.item.label}（wash sale 替代股，成本 +{add:,.2f}，持有期并入 {hit.tack_days} 天）")
        matched.replacement = True
        pos = parts.index(q)
        parts[pos:pos + 1] = [matched] + ([rest] if rest else [])
        left -= take
