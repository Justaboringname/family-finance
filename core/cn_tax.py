"""爸爸的中国个税（AAPL 股票计划）。所有函数只做数值计算、不关心币种：
传入人民币就得到人民币，传入美元就得到美元（测试用例即按美元验证）。参数来自 tax_params/<年>.json 的 cn_iit。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .money import ZERO, D, fmt_qty
from .params import TaxParams
from .trace import Trace

LOSS_RULES = {"annual_netting": "同一年内境外股票盈亏互抵（不跨年）", "per_sale": "按笔计算，亏损那笔不交税、不抵其他"}


def comprehensive_bracket(amount: Decimal, p: TaxParams) -> tuple[Decimal, Decimal, Decimal]:
    """返回 (税率, 速算扣除数, 应纳税额)。"""
    if amount <= 0:
        return ZERO, ZERO, ZERO
    for cap, rate, quick in p.brackets("cn_iit.comprehensive_brackets"):
        if cap is None or amount <= cap:
            return rate, quick, amount * rate - quick
    raise AssertionError("税率表缺最后一档")


# ---------- 股权激励（工资薪金） ----------

@dataclass
class IncentiveItem:
    lot_type: str            # RS / ESPP
    event_date: date
    quantity: Decimal
    fmv: Decimal             # 归属日 / 购买日收盘价
    purchase_price: Decimal | None = None   # ESPP 实际每股购买价
    fx_rate: Decimal = Decimal(1)           # 折算成人民币的汇率（数值计算用；传 1 即不折算）
    projected: bool = False

    def income(self) -> Decimal | None:
        if self.lot_type == "RS":
            per = self.fmv
        else:
            if self.purchase_price is None:
                return None
            per = self.fmv - self.purchase_price
        return per * self.quantity * self.fx_rate


def equity_incentive_tax(items: list[IncentiveItem], year: int, p: TaxParams, filed: bool = True,
                         ownership_ok: bool = True, other_comprehensive: Decimal = ZERO,
                         deductions: Decimal = ZERO) -> dict:
    """全年股权激励收入合并计税。符合条件时单独计税；否则并入综合所得（返回增量税额）。"""
    t = Trace()
    total = ZERO
    missing = []
    for it in items:
        inc = it.income()
        if inc is None:
            missing.append(it)
            continue
        total += inc
        what = "归属日收盘价 × 股数" if it.lot_type == "RS" else "(购买日收盘价 − 实际购买价) × 股数"
        if it.fx_rate != 1:
            what += f" × 汇率 {it.fx_rate}"
        t.add(f"{it.event_date} {it.lot_type} {fmt_qty(it.quantity)} 股{'（预计）' if it.projected else ''}", inc, what,
              [p.ref("cn_iit.equity_incentive_separate_valid_until"), p.ref("cn_iit.fx_rule_withholding")],
              kind="income")
    for it in missing:
        t.warn(f"{it.event_date} ESPP 缺少实际购买价，未计入", "请在批次里补录每股实际购买价")

    valid_until = date.fromisoformat(p.v("cn_iit.equity_incentive_separate_valid_until"))
    separate_ok = filed and ownership_ok and year <= valid_until.year
    reasons = []
    if not filed:
        reasons.append("境内机构未备案（国税函〔2009〕461号 第六条、第七条）")
    if not ownership_ok:
        reasons.append("上市公司对境内雇主持股低于 30%（461号 第七条）")
    if year > valid_until.year:
        reasons.append(f"单独计税政策执行至 {valid_until}，{year} 年已过期")
        t.warn(f"25 号公告执行至 {valid_until}，{year} 年按并入综合所得估算", "政策是否延续待确认",
               [p.ref("cn_iit.equity_incentive_separate_valid_until")])

    if separate_ok:
        rate, quick, tax = comprehensive_bracket(total, p)
        t.add("单独计税", tax, f"全年股权激励收入 {total:,.2f} × {rate * 100:.0f}% − 速算扣除数 {quick:,.0f}",
              [p.ref("cn_iit.equity_incentive_separate_valid_until"), p.ref("cn_iit.comprehensive_brackets")], kind="tax")
        method = "单独计税"
    else:
        basic = p.dec("cn_iit.comprehensive_basic_deduction")
        base_wo = max(ZERO, other_comprehensive - basic - deductions)
        base_w = max(ZERO, other_comprehensive + total - basic - deductions)
        _, _, tax_wo = comprehensive_bracket(base_wo, p)
        rate, quick, tax_w = comprehensive_bracket(base_w, p)
        tax = tax_w - tax_wo
        t.add("并入综合所得（增量）", tax,
              f"（其他综合所得 + 股权激励 − 60,000 − 扣除）按综合所得税率表，减去不含股权激励时的税；原因：{'；'.join(reasons)}",
              [p.ref("cn_iit.comprehensive_basic_deduction"), p.ref("cn_iit.comprehensive_brackets")], kind="tax")
        method = "并入综合所得"
    return {"income": total, "tax": tax, "method": method, "reasons": reasons, "trace": t}


# ---------- 卖出：财产转让所得 ----------

@dataclass
class TransferItem:
    sale_date: date
    quantity: Decimal
    price: Decimal
    cost_per_share: Decimal   # 归属日（RS）/ 购买日（ESPP）收盘价
    fee: Decimal = ZERO
    fx_rate: Decimal = Decimal(1)
    label: str = ""
    order: str = ""           # 同一笔卖出订单（跨多个批次）共用一个编号

    @property
    def proceeds(self) -> Decimal:
        return self.quantity * self.price * self.fx_rate

    @property
    def cost(self) -> Decimal:
        return self.quantity * self.cost_per_share * self.fx_rate

    @property
    def gain(self) -> Decimal:
        return self.proceeds - self.cost - self.fee * self.fx_rate


def property_transfer_tax(items: list[TransferItem], p: TaxParams, rule: str = "annual_netting") -> dict:
    t = Trace()
    rate = p.dec("cn_iit.property_transfer_rate")
    refs = [p.ref("cn_iit.property_transfer_rate"), p.ref("cn_iit.property_transfer_cost_basis_source")]
    fx_ref = [p.ref("cn_iit.fx_rule_annual_filing")]
    for it in items:
        fx_txt = f"，汇率 {it.fx_rate}" if it.fx_rate != 1 else ""
        t.add(f"{it.sale_date} {it.label}卖出 {fmt_qty(it.quantity)} 股 @ {it.price}", it.gain,
              f"收入 {it.proceeds:,.2f} − 原值 {it.cost:,.2f} − 手续费 {it.fee * it.fx_rate:,.2f}{fx_txt}",
              refs + (fx_ref if fx_txt else []), kind="income")
    if rule == "per_sale":
        orders: dict[str, Decimal] = {}
        for k, it in enumerate(items):
            key = it.order or f"_{k}"
            orders[key] = orders.get(key, ZERO) + it.gain
        tax = sum((max(ZERO, g) * rate for g in orders.values()), ZERO)
        t.add("财产转让所得税（按笔）", tax, "每笔订单收益 × 20%（同一笔订单的多个批次先相抵），亏损那笔不交税",
              refs + [p.ref("cn_iit.property_transfer_per_sale_rule")], kind="tax")
    else:
        net = sum((it.gain for it in items), ZERO)
        tax = max(ZERO, net) * rate
        t.add("财产转让所得税（年度互抵）", tax, f"全年盈亏相抵后 {net:,.2f} × 20%；亏损不结转到下一年",
              refs + [p.ref("cn_iit.property_transfer_annual_netting_rule")], kind="tax")
    return {"tax": tax, "net_gain": sum((it.gain for it in items), ZERO), "trace": t}


def sell_by_amount(amount: Decimal, price: Decimal, cost_per_share: Decimal, p: TaxParams, fee: Decimal = ZERO) -> dict:
    """按卖出金额模拟：股数 = 金额 ÷ 卖价（不取整）。"""
    amount, price, cost_per_share = D(amount), D(price), D(cost_per_share)
    qty = amount / price
    item = TransferItem(date.today(), qty, price, cost_per_share, D(fee))
    res = property_transfer_tax([item], p, "per_sale")
    res["quantity"] = qty
    return res


# ---------- 股息 ----------

def dividend_tax(gross: Decimal, us_withheld: Decimal, p: TaxParams) -> dict:
    gross, us_withheld = D(gross), D(us_withheld)
    rate = p.dec("cn_iit.dividend_rate")
    treaty = p.dec("cn_iit.us_dividend_treaty_rate")
    cn = gross * rate
    credit = min(us_withheld, gross * treaty, cn)
    t = Trace()
    t.add("中国应纳税额", cn, "股息毛额 × 20%", [p.ref("cn_iit.dividend_rate")], kind="tax")
    t.add("可抵免美国预扣", -credit, "min(美国实扣, 毛额 × 10% 协定税率, 中国应纳税额)；需境外纳税凭证",
          [p.ref("cn_iit.us_dividend_treaty_rate"), p.ref("cn_iit.foreign_tax_credit_rule")])
    if us_withheld > gross * treaty:
        t.warn("美国多扣的部分不能在中国抵免", "超过协定 10% 的部分只能向美国申请退税（检查 W-8BEN）")
    return {"cn_tax": cn, "credit": credit, "payable": cn - credit, "trace": t}
