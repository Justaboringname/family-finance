"""E. 优化提示。"""
from dataclasses import replace
from datetime import date

import pandas as pd
import streamlit as st

from core.money import ZERO, fmt_qty, fmt_usd
from core.optimizer import harvest_plan, order_lots, unrealized_losses, upcoming_long_term, zero_band_headroom
from core.params import available_years, load_params
from core.portfolio import load_lots
from core.us_tax import STATUS_LABELS
from ui_theme import page_header, section, table
from ui_common import (build_profile, current_market, get_conn, incomes_for, money, realized_items, residency_all,
                       stat, today_et)

page_header("优化提示", "0% 长期资本利得档额度、即将转长期的批次、收益收割", eyebrow="美国税")
conn = get_conn()
today = today_et()
quotes, fx, _ = current_market(conn)
res = residency_all(conn, today)
lots = load_lots(conn)

years = available_years()
c1, c2 = st.columns(2)
year = c1.selectbox("年份", years, index=years.index(today.year) if today.year in years else 0)
prof, _ = build_profile(conn, year, res)
alt = None
if year == 2026:
    y26 = res.get(2026)
    alt = c2.radio("2026 年按哪种身份看", ["按「美国联邦税」页的设置", "假设做 First-Year Choice"], horizontal=True)
    if alt.startswith("假设") and prof.status == "nonresident":
        start = (y26.fyc.start if y26 and y26.fyc.start else None) or date(2026, 9, 15)
        prof = replace(prof, status="dual_status", residency_start=start)
p = load_params(year)
gains, _ = realized_items(conn, year)
incomes = incomes_for(conn, year)
sale_on = max(today, date(year, 1, 1)) if year >= today.year else date(year, 12, 31)
if prof.status == "dual_status" and prof.residency_start and sale_on < prof.residency_start:
    sale_on = prof.residency_start
st.caption(f"身份：{STATUS_LABELS[prof.status]}" + (f"，居民起始日 {prof.residency_start}" if prof.residency_start else ""))

# ---------- 0% 档 ----------
section("0% 长期资本利得档还剩多少额度")
head = zero_band_headroom(prof, gains, incomes, p, sale_on)
if head is None:
    st.info("本年是全年非居民：没有 0% 档。在美满 183 天时净收益统一按 30%，不满 183 天则为 0。")
else:
    stat(st, "还能实现的长期收益（联邦税为 0）", head, fx)
    st.caption("已扣除本年已实现收益和其他收入。" + ("双重身份年不能用标准扣除。" if prof.status == "dual_status" else
                                                  "全年居民的标准扣除也会先吸收一部分。") + "只针对联邦税；州/市税另计。")

# ---------- 60 天内转长期 ----------
section("未来 60 天内从短期变成长期的批次")
up = upcoming_long_term(lots, today, 60)
if up:
    table(pd.DataFrame([{
        "股票": x["lot"].ticker, "买入日": x["lot"].open_date, "股数": fmt_qty(x["lot"].quantity),
        "每股成本": f"${x['lot'].cost_per_share:,.2f}", "变成长期日期": x["long_term_date"], "还差天数": str(x["days"]),
        "当前浮盈": fmt_usd(x["lot"].quantity * quotes[x["lot"].ticker].close - x["lot"].cost_basis, sign=True)
        if x["lot"].ticker in quotes else "",
    } for x in up]), hide_index=True, width="stretch")
    st.caption("在居民期间，等它变成长期再卖，收益按 0/15/20% 而不是普通税率。")
else:
    st.caption("没有。")

# ---------- 0% 档年份不要实现亏损 ----------
if head is not None and head > 0:
    losses = unrealized_losses(lots, quotes)
    if losses:
        section("本年还在 0% 档：先别实现亏损")
        st.warning("今年长期收益本来就是 0% 税率，在今年实现亏损等于浪费：亏损会先抵掉本来免税的收益。"
                   "建议留到税率高的年份再实现。")
        table(pd.DataFrame([{
            "股票": x["lot"].ticker, "买入日": x["lot"].open_date, "股数": fmt_qty(x["lot"].quantity),
            "浮亏": fmt_usd(x["unrealized"]),
        } for x in losses]), hide_index=True, width="stretch")

# ---------- 收益收割 ----------
section("收益收割（0% 档内卖出后立即买回）")
if head is None or head <= 0:
    st.caption("本年没有 0% 档额度，不适用。")
else:
    plan = harvest_plan(lots, quotes, head, sale_on)
    if not plan:
        st.caption("按 " + str(sale_on) + " 卖出时没有满足条件的长期浮盈批次。")
    else:
        total = sum((x["gain"] for x in plan), ZERO)
        table(pd.DataFrame([{
            "股票": x["lot"].ticker, "买入日": x["lot"].open_date, "卖出并买回股数": fmt_qty(x["shares"]),
            "现价": f"${x['price']:,.2f}", "实现收益（0% 税）": fmt_usd(x["gain"]),
            "成本从": fmt_usd(x["old_basis"]), "抬高到": fmt_usd(x["new_basis"]),
        } for x in plan]), hide_index=True, width="stretch")
        st.caption(f"合计实现 {fmt_usd(total)} 长期收益，联邦税仍为 0；成本抬高同样金额，以后卖出少交税。"
                   "卖赚钱的股票不受 wash sale 限制，可以立即买回；但买回的新批次持有期重新计算（重新变成短期）。"
                   f"按 {sale_on} 卖出、按当前价格估算。")

# ---------- 默认选批次 ----------
section("卖出时默认推荐：成本最高的批次")
t = st.selectbox("看哪只股票的推荐顺序", sorted({l.ticker for l in lots}))
if t:
    px = quotes[t].close if t in quotes else ZERO
    table(pd.DataFrame([{
        "顺序": i + 1, "买入日": l.open_date, "股数": fmt_qty(l.quantity), "每股成本": f"${l.cost_per_share:,.2f}",
        "每股收益": fmt_usd(px - l.cost_per_share, sign=True) if px else "",
    } for i, l in enumerate(order_lots([l for l in lots if l.ticker == t], "hifo", px, sale_on))]),
        hide_index=True, width="stretch")
    st.caption("成本最高的批次收益最小。全年非居民（统一 30%）时这就是最优；居民期间如果最高成本批次是短期，"
               "可以在「卖出模拟」里对比「税优先」等选法。下单时在 Schwab 选 Specific Lots。")
