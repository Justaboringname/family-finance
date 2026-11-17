"""D. 卖出模拟器：选批次放进篮子，对比三种情景下的联邦税。"""
from dataclasses import replace
from datetime import date
from decimal import ROUND_CEILING

import pandas as pd
import streamlit as st

from core.money import ZERO, D, fmt_pct, fmt_qty, fmt_usd
from core.optimizer import STRATEGIES, allocate, best_strategy
from core.params import load_params
from core.portfolio import is_long_term, load_lots
from core.us_tax import STATUS_LABELS, GainItem, IncomeItem, basket_effect
from ui_theme import page_header, scenario_bars, section, table
from ui_common import (build_profile, current_market, dec_or_none, get_conn, incomes_for, lot_meta, money, render_trace,
                       residency_all, stop, to_date, today_et, txt, wash_run)

page_header("卖出模拟", "把计划卖出的批次放进篮子，对比三种情景下的联邦税", eyebrow="美国税")
conn = get_conn()
today = today_et()
quotes, fx, _ = current_market(conn)
res = residency_all(conn, today)
lots = load_lots(conn)
by_id = {l.id: l for l in lots}
basket = st.session_state.setdefault("basket", [])
planned = st.session_state.setdefault("planned_buys", [])

# ---------- 情景设置 ----------
y26 = res.get(2026)
fyc_start = (y26.fyc.start if y26 and y26.fyc.start else None) or (y26.residency_start if y26 else None)
with st.expander("情景设置", expanded=True):
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**① 2026 全年非居民**")
        days26 = st.number_input("2026 年在美总天数", 0, 366, value=y26.us_days if y26 else 0,
                                 help="含 F-1 天数；≥183 天则净收益按 30%")
    with c2:
        st.markdown("**② 2026 First-Year Choice**")
        s2_start = st.date_input("居民起始日（AP 入境日）", value=fyc_start or date(2026, 9, 15),
                                 min_value=date(2026, 1, 1), max_value=date(2026, 12, 31), format="YYYY-MM-DD")
        if not (y26 and y26.fyc.eligible):
            st.caption("⚠️ 按现有出入境记录还不满足 First-Year Choice 条件，这里只是假设")
    with c3:
        st.markdown("**③ 2027 有工资**")
        wages_default = float(sum((i.amount for i in incomes_for(conn, 2027) if i.kind == "wages"), ZERO))
        wages27 = st.number_input("2027 年工资（美元）", min_value=0.0, value=wages_default, step=1000.0)
        sale27 = st.date_input("2027 情景的卖出日期", value=date(2027, 1, 4), min_value=date(2027, 1, 1),
                               max_value=date(2027, 12, 31), format="YYYY-MM-DD")
        st.caption("按 2027 年全年居民计算；批次的长短期按这个日期重新判断")

# ---------- 加入篮子 ----------
section("加入篮子")
tickers = sorted({l.ticker for l in lots})
if not tickers:
    st.info("没有持仓批次。")
    stop()
c1, c2, c3, c4 = st.columns(4)
ticker = c1.selectbox("股票", tickers)
q = quotes.get(ticker)
sale_date = c2.date_input("预计卖出日期", value=today, format="YYYY-MM-DD")
price = D(c3.number_input("预计卖价（美元）", min_value=0.01, value=float(q.close) if q else 100.0, step=1.0,
                          format="%.2f"))
fee = D(c4.number_input("手续费（美元）", min_value=0.0, value=0.0, step=1.0))

used = {}
for b in basket:
    used[b["lot_id"]] = used.get(b["lot_id"], ZERO) + D(b["qty"])
t_lots = [replace(l, quantity=l.quantity - used.get(l.id, ZERO)) for l in lots if l.ticker == ticker]
t_lots = [l for l in t_lots if l.quantity > 0]
avail = sum((l.quantity for l in t_lots), ZERO)

mode = st.radio("选批次方式", ["按股数自动选", "按金额自动选", "手动选批次"], horizontal=True)
allocs = []
if mode in ("按股数自动选", "按金额自动选"):
    a1, a2 = st.columns(2)
    if mode == "按股数自动选":
        qty = D(a1.number_input(f"卖出股数（可卖 {fmt_qty(avail)}）", min_value=0.0, max_value=float(avail),
                                value=0.0, step=1.0))
    else:
        amt = D(a1.number_input("想卖出的金额（美元）", min_value=0.0, value=0.0, step=1000.0))
        qty = min(avail, (amt / price).to_integral_value(rounding=ROUND_CEILING)) if price else ZERO
        a1.caption(f"约 {fmt_qty(qty)} 股（向上取整）")
    strategy = a2.selectbox("选法", list(STRATEGIES), format_func=lambda k: STRATEGIES[k])
    if qty > 0:
        allocs = allocate(t_lots, qty, strategy, price, sale_date)
else:
    df = pd.DataFrame([{"lot_id": l.id, "买入日": l.open_date, "可卖股数": float(l.quantity),
                        "每股成本": float(l.cost_per_share), "长短期": "长期" if is_long_term(l.open_date, sale_date) else "短期",
                        "每股收益": float(price - l.cost_per_share), "卖出股数": 0.0} for l in t_lots])
    ed = st.data_editor(df, hide_index=True, width="stretch", readonly_ok=True, disabled=["lot_id", "买入日", "可卖股数", "每股成本", "长短期", "每股收益"],
                        column_config={"lot_id": None, "卖出股数": st.column_config.NumberColumn(min_value=0.0)})
    for _, r in ed.iterrows():
        n = dec_or_none(r["卖出股数"]) or ZERO
        if n > 0:
            lot = next(l for l in t_lots if l.id == int(r["lot_id"]))
            allocs.append((lot, min(n, lot.quantity)))

if allocs:
    prev = pd.DataFrame([{"买入日": l.open_date, "股数": fmt_qty(n), "每股成本": f"${l.cost_per_share:,.2f}",
                          "收益": fmt_usd(n * (price - l.cost_per_share), sign=True),
                          "长短期": "长期" if is_long_term(l.open_date, sale_date) else "短期"} for l, n in allocs])
    table(prev, hide_index=True, width="stretch")
    if st.button("加入篮子", type="primary", readonly_ok=True):            # 篮子只在当前会话里，不写数据库
        group = len(basket) and max(b["group"] for b in basket) + 1
        for l, n in allocs:
            basket.append({"group": group, "lot_id": l.id, "account": l.account, "ticker": l.ticker,
                           "open_date": l.open_date, "qty": str(n), "price": str(price), "sale_date": sale_date,
                           "fee": str(fee * n / sum((x[1] for x in allocs), ZERO))})
        st.rerun()

# ---------- 计划买入（用于 wash sale 检测） ----------
with st.expander("计划买入（用于 wash sale 检测）"):
    st.caption("卖出亏损前后 30 天内买入同一只股票会触发 wash sale。其他账户（如 IRA）的买入也请填在这里。")
    pdf = pd.DataFrame([{"股票": b["ticker"], "日期": b["date"], "股数": float(b["qty"])} for b in planned],
                       columns=["股票", "日期", "股数"])
    ped = st.data_editor(pdf, num_rows="dynamic", hide_index=True, key="planned_editor", readonly_ok=True,
                         column_config={"日期": st.column_config.DateColumn(format="YYYY-MM-DD")})
    if st.button("保存计划买入", readonly_ok=True):                         # 计划买入也只在当前会话里
        good, bad = [], []
        for i, r in ped.iterrows():
            t_, d_, q_ = txt(r["股票"]), to_date(r["日期"]), dec_or_none(r["股数"])
            if t_ and d_ and q_ and q_ > 0:
                good.append({"ticker": t_.upper(), "date": d_, "qty": str(q_)})
            elif any(txt(v) for v in r.values):
                bad.append(f"第 {i + 1} 行缺少股票、日期或股数")
        if bad:
            st.error("没有保存：" + "；".join(bad))
        else:
            st.session_state["planned_buys"] = good
            st.rerun()

# ---------- 篮子 ----------
section("篮子")
if not basket:
    st.info("篮子是空的。")
    stop()

bdf = pd.DataFrame([{
    "#": i, "股票": b["ticker"], "买入日": b["open_date"], "股数": fmt_qty(D(b["qty"])), "卖价": f"${D(b['price']):,.2f}",
    "卖出日": b["sale_date"],
    "成本": fmt_usd(by_id[b["lot_id"]].cost_per_share * D(b["qty"])) if b["lot_id"] in by_id else "批次已变",
    "收益": fmt_usd(D(b["qty"]) * D(b["price"]) - D(b["fee"]) - by_id[b["lot_id"]].cost_per_share * D(b["qty"]), sign=True)
    if b["lot_id"] in by_id else "",
} for i, b in enumerate(basket)])
table(bdf, hide_index=True, width="stretch")
r1, r2 = st.columns([3, 1])
drop = r1.multiselect("移除", list(range(len(basket))), format_func=lambda i: f"#{i} {basket[i]['ticker']} {basket[i]['open_date']}")
if r2.button("移除所选", readonly_ok=True) and drop:
    st.session_state["basket"] = [b for i, b in enumerate(basket) if i not in drop]
    st.rerun()
if r2.button("清空篮子", readonly_ok=True):
    st.session_state["basket"] = []
    st.rerun()

missing = [b for b in basket if b["lot_id"] not in by_id]
need: dict = {}
for b in basket:
    need[b["lot_id"]] = need.get(b["lot_id"], ZERO) + D(b["qty"])
short = [k for k, q in need.items() if k in by_id and q > by_id[k].quantity]
if missing or short:
    st.error("篮子里有批次已不存在或股数已减少（可能刚记录了卖出或重新导入过），请清空篮子重新添加。")
    stop()


def basket_pairs(on: date | None = None) -> list[tuple[GainItem, dict]]:
    out = []
    for b in basket:
        lot = by_id[b["lot_id"]]
        qty, pr, fe = D(b["qty"]), D(b["price"]), D(b["fee"])
        d = on or b["sale_date"]
        out.append((GainItem(d, lot.ticker, qty, qty * pr - fe, lot.cost_basis * qty / lot.quantity,
                             is_long_term(lot.open_date, d), label=f"{lot.ticker} {lot.open_date}"),
                    lot_meta(lot.account, lot.ticker, lot.open_date, lot.source, lot.as_of)))
    return out


# wash sale：篮子 + 所有已实现卖出一起算，对照所有已知买入（含计划买入）
real_only, _, _, _ = wash_run(conn, planned=planned)                 # 不含篮子
real26, items_2026, hits, n_real = wash_run(conn, basket_pairs(), planned)
real27, items_2027, _, _ = wash_run(conn, basket_pairs(sale27), planned)
for h in hits:
    if h.leg.ref >= n_real:
        st.warning(f"wash sale：{h.leg.ticker} {h.leg.sale_date} 卖出亏损，{h.replacement.acq_date} 有买入"
                   f"（{h.replacement.label or '已有批次'}，{fmt_qty(h.matched_qty)} 股）→ 禁止扣除 {fmt_usd(h.disallowed)}，"
                   "加到替代批次成本上，替代股的持有期并入原股票。")
not_2026 = [b for b in basket if b["sale_date"].year != 2026]
if not_2026:
    st.caption("注意：情景 ①② 按 2026 年计算，篮子里卖出日期不在 2026 年的条目也按其日期参与。")

# ---------- 三种情景 ----------
p26, p27 = load_params(2026), load_params(2027)
base26 = [it for it in real_only if it.sale_date.year == 2026]
base27 = [it for it in real_only if it.sale_date.year == 2027]
with26 = [it for it in real26 if it.sale_date.year == 2026]   # 加入篮子后重新做过 wash sale
with27 = [it for it in real27 if it.sale_date.year == 2027]
inc26 = incomes_for(conn, 2026)
inc27 = [i for i in incomes_for(conn, 2027) if i.kind != "wages"]
if wages27:
    inc27.append(IncomeItem("wages", D(str(wages27))))

prof_base26, _ = build_profile(conn, 2026, res)
prof_base27, _ = build_profile(conn, 2027, res)
scen = [
    ("① 2026 全年非居民", replace(prof_base26, status="nonresident", residency_start=None, us_days=int(days26)),
     base26, items_2026, inc26, p26, with26),
    ("② 2026 First-Year Choice", replace(prof_base26, status="dual_status", residency_start=s2_start, us_days=int(days26)),
     base26, items_2026, inc26, p26, with26),
    ("③ 2027 有工资", replace(prof_base27, status="resident", residency_start=None), base27, items_2027, inc27, p27,
     with27),
]

section("情景对比（联邦税，州/市税按各年设置）")
summary = []
effects = []
for name, prof, base, items, inc, p, with_base in scen:
    e = basket_effect(prof, base, items, inc, p, base_with_basket=with_base)
    effects.append((name, prof, e, p, items))
    summary.append({
        "情景": name,
        "身份": STATUS_LABELS[prof.status],
        "篮子收益": fmt_usd(e["basket_gain"], sign=True),
        "篮子带来的税": money(e["incremental_tax"], fx),
        "实际税率": fmt_pct(e["effective_rate"]) if e["effective_rate"] is not None else "—",
        "边际税率（再多 $100 短期）": fmt_pct(e["marginal_st"]),
        "边际税率（再多 $100 长期）": fmt_pct(e["marginal_lt"]),
        "全年税合计": money(e["with_basket"].total, fx),
    })
scenario_bars([(name, e["incremental_tax"]) for name, _, e, _, _ in effects], fx)
table(pd.DataFrame(summary), hide_index=True, width="stretch")
if p27.provisional:
    st.caption("情景 ③ 用的是 2027 年临时参数（沿用 2026 年档位），IRS 发布后会更新。")
if any(e["with_basket"].nra_tax for _, _, e, _, _ in effects[1:2]):
    st.caption("情景 ② 里 AP 入境前卖出的部分仍按非居民 30% 计税（全年在美满 183 天）。")
st.caption("做 First-Year Choice 时，延期报税要先按情景 ① 的税额缴纳，正式申报后多交的部分退回。")

for name, prof, e, p, items in effects:
    with st.container(border=True):
        st.markdown(f"**{name}**：篮子带来的税 {money(e['incremental_tax'], fx)}")
        lt_n = sum(1 for it in items if it.long_term)
        st.caption(f"篮子 {len(items)} 条，其中长期 {lt_n} 条；不含篮子时全年税 {money(e['base'].total, fx)}")
        render_trace(e["with_basket"].trace, fx, title="含篮子的全年税：规则和参数")

# ---------- 选批次对比 ----------
section("选批次对比")
st.caption("默认推荐成本最高的批次。下表在所选情景里比较几种候选选法（不是穷举的全局最优）。")
k1, k2 = st.columns(2)
cmp_ticker = k1.selectbox("股票", sorted({b["ticker"] for b in basket}), key="cmp_t")
cmp_scen = k2.selectbox("情景", [s[0] for s in scen], key="cmp_s")
sc = next(s for s in scen if s[0] == cmp_scen)
mine = [b for b in basket if b["ticker"] == cmp_ticker]
qty_t = sum((D(b["qty"]) for b in mine), ZERO)
px = D(mine[0]["price"])
d_t = sale27 if sc[0].startswith("③") else mine[0]["sale_date"]
others = [it for it in sc[3] if it.ticker != cmp_ticker]
try:
    best = best_strategy([l for l in lots if l.ticker == cmp_ticker], qty_t, px, d_t, sc[1], sc[2] + others, sc[4], sc[5])
    table(pd.DataFrame([{
        "选法": STRATEGIES[x["strategy"]],
        "全年税": money(x["tax"], fx),
        "批次": "；".join(f"{l.open_date} × {fmt_qty(n)}" for l, n in x["allocs"]),
    } for x in best["all"]]), hide_index=True, width="stretch")
except ValueError as e:
    st.warning(f"无法比较：{e}")
st.caption(f"{cmp_ticker} 共 {fmt_qty(qty_t)} 股 @ ${px:,.2f}。下单时要在 Schwab 选 Specific Lots 指定批次，"
           "否则券商按默认方法配批次。")
