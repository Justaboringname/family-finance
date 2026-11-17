"""C. 美国联邦税：按自然年，每年单独配置身份。"""
from datetime import date

import pandas as pd
import streamlit as st

from core.db import one, rows
from core.money import D, fmt_usd
from core.params import available_years, load_params
from core.us_tax import INCOME_KINDS, STATUS_LABELS, compute_tax
from ui_theme import page_header, section, table
from ui_common import (txt, STATUS_OPTIONS, build_profile, current_market, dec_or_none, get_conn, incomes_for, money,
                       flash, card, realized_items, render_trace, residency_all, save_tax_year, stat, stop, tax_years_settings,
                       today_et)

page_header("联邦税", "按自然年计算；每年的身份默认来自「税务身份」，也可以手动覆盖", eyebrow="美国税")
conn = get_conn()
today = today_et()
_, fx, _ = current_market(conn)
years = available_years()
year = st.selectbox("年份", years, index=years.index(today.year) if today.year in years else 0)
p = load_params(year)
if p.provisional:
    st.warning(f"{year} 年参数是临时值：{p.note}")

res = residency_all(conn, today)
yr = res.get(year)
s = tax_years_settings(conn, year)

# ---------- 计算（按已保存的设置） ----------
prof, notes = build_profile(conn, year, res)
gains, hits = realized_items(conn, year)
incomes = incomes_for(conn, year)
r = compute_tax(prof, gains, incomes, p)
m1, m2, m3, m4 = st.columns(4)
stat(m1, "联邦税", r.federal, fx)
stat(m2, "其中 NIIT", r.niit, fx)
stat(m3, "其中非居民 30%", r.nra_tax, fx)
if prof.ny_status != "off":
    stat(m4, "纽约州/市（估算）", r.state_local, fx)
else:
    card(m4, "纽约州 / 市（估算）", "未计算", "在下方「设置 → 身份」里开启")
st.caption(f"身份：{STATUS_LABELS[prof.status]}"
           + (f"，居民起始日 {prof.residency_start}" if prof.status == "dual_status" else "")
           + f"，在美 {prof.us_days} 天" + ("；" + "、".join(notes) if notes else ""))
if r.carry_out_st or r.carry_out_lt:
    st.info(f"结转到 {year + 1} 年：短期亏损 {fmt_usd(r.carry_out_st)}，长期亏损 {fmt_usd(r.carry_out_lt)}")
render_trace(r.trace, fx, expanded=True)

section(f"{year} 年已实现卖出")
if gains:
    table(pd.DataFrame([{
        "卖出日": g.sale_date, "批次": g.label, "股数": f"{g.quantity:,f}".rstrip("0").rstrip("."),
        "卖出所得": fmt_usd(g.proceeds), "成本": fmt_usd(g.cost_basis), "收益": fmt_usd(g.gain, sign=True),
        "短/长期": "长期" if g.long_term else "短期",
        "期间": ("居民" if prof.status == "resident" or (prof.status == "dual_status" and g.sale_date >= prof.residency_start)
                 else "非居民"),
        "wash sale 加回": fmt_usd(g.wash_disallowed) if g.wash_disallowed else "",
    } for g in gains]), hide_index=True, width="stretch")
else:
    st.caption("本年还没有记录已实现卖出（在「数据管理」录入）。")
for h in hits:
    st.warning(f"wash sale：{h.leg.sale_date} 卖出 {h.leg.ticker} 亏损，{h.replacement.acq_date} 买入 "
               f"{h.matched_qty:,f} 股 {h.replacement.ticker} → 禁止扣除 {fmt_usd(h.disallowed)}，加到替代批次成本上"
               "（Schwab 导出的成本通常已含此调整）")

# ---------- 设置 ----------
section("设置", "改完点保存，上面的结果会重新计算")
with st.expander("身份", expanded=bool(s["status_override"] or s["residency_start_override"] or s["us_days_override"] is not None)):
    auto = f"自动：{STATUS_OPTIONS[yr.status]}" if yr else "自动：无记录"
    if yr and yr.status == "dual_status":
        auto += f"（居民起始日 {yr.residency_start}）"
    opts = ["auto", "resident", "nonresident", "dual_status"]
    labels = {"auto": auto, **STATUS_OPTIONS}
    cur = s["status_override"] or "auto"
    c1, c2, c3 = st.columns(3)
    choice = c1.selectbox("身份", opts, index=opts.index(cur), format_func=lambda k: labels[k])
    auto_start = yr.residency_start if yr else None
    start_default = date.fromisoformat(s["residency_start_override"]) if s["residency_start_override"] else auto_start
    start = c2.date_input("居民起始日（双重身份年）", value=start_default, min_value=date(year, 1, 1),
                          max_value=date(year, 12, 31), format="YYYY-MM-DD")
    days_auto = yr.us_days if yr else 0
    days = c3.number_input(f"当年在美总天数（自动 {days_auto}）", min_value=0, max_value=366,
                           value=int(s["us_days_override"]) if s["us_days_override"] is not None else days_auto)
    c4, c5, c6, c7 = st.columns(4)
    itemized = c4.number_input("列举扣除（美元）", min_value=0.0, value=float(D(s["itemized"] or 0)), step=100.0,
                               help="双重身份年不能用标准扣除，只能用列举扣除")
    ny_opts = {"off": "不计算", "resident": "纽约全年居民", "part_year": "纽约部分年居民", "nonresident": "纽约非居民"}
    ny = c5.selectbox("纽约州税（估算）", list(ny_opts), index=list(ny_opts).index(s["ny_status"] or "off"),
                      format_func=lambda k: ny_opts[k])
    ny_start = c6.date_input("纽约居民起始日", value=date.fromisoformat(s["ny_start"]) if s["ny_start"] else None,
                             format="YYYY-MM-DD", disabled=ny != "part_year")
    nyc = c7.checkbox("计算纽约市税（估算）", value=bool(s["nyc"]), disabled=ny not in ("resident", "part_year"))
    if st.button("保存身份设置"):
        if choice == "dual_status" and not start:
            st.error("双重身份年需要填写居民起始日。")
            stop()
        save_tax_year(
            conn, year,
            status_override=None if choice == "auto" else choice,
            residency_start_override=start.isoformat() if start and start != auto_start else None,
            us_days_override=int(days) if int(days) != days_auto else None,
            itemized=str(itemized) if itemized else None,
            ny_status=ny, ny_start=ny_start.isoformat() if ny_start else None, nyc=int(nyc),
        )
        flash("已保存身份设置")
        st.rerun()
    flash()
    st.caption("身份默认来自「税务身份」页的计算（含 First-Year Choice 勾选）；在这里选具体身份即为手动覆盖。"
               "纽约州身份不跟联邦身份走，需要单独选。")

with st.expander("其他收入（工资、利息、分红）", expanded=False):
    st.caption("例如 2027 年的工资、现金利息、股票分红。居民期间的这些收入会占用 0% 长期资本利得档，也计入 NIIT。"
               "双重身份年请标明属于居民期间还是非居民期间。")
    inc_rows = rows(conn, "SELECT * FROM other_income WHERE year = ? ORDER BY id", (year,))
    kinds = list(INCOME_KINDS)
    periods = {"resident": "居民期间", "nonresident": "非居民期间"}
    inc_df = pd.DataFrame([{"类型": INCOME_KINDS[r["kind"]], "金额（美元）": float(D(r["amount"])),
                            "期间": periods.get(r["period"], "居民期间"), "备注": r["note"] or ""} for r in inc_rows],
                          columns=["类型", "金额（美元）", "期间", "备注"])
    inc_ed = st.data_editor(inc_df, num_rows="dynamic", hide_index=True, width="stretch", key=f"inc_{year}",
                            column_config={
                                "类型": st.column_config.SelectboxColumn(options=list(INCOME_KINDS.values()), required=True),
                                "金额（美元）": st.column_config.NumberColumn(min_value=0.0, format="%.2f", required=True),
                                "期间": st.column_config.SelectboxColumn(options=list(periods.values()), required=True),
                            })
    if st.button("保存其他收入"):
        rk = {v: k for k, v in INCOME_KINDS.items()}
        rp = {v: k for k, v in periods.items()}
        with conn:
            conn.execute("DELETE FROM other_income WHERE year = ?", (year,))
            for _, r in inc_ed.iterrows():
                if r["类型"] in rk and dec_or_none(r["金额（美元）"]) is not None:
                    conn.execute("INSERT INTO other_income(year, kind, amount, period, note) VALUES(?,?,?,?,?)",
                                 (year, rk[r["类型"]], str(dec_or_none(r["金额（美元）"])), rp.get(r["期间"], "resident"),
                                  txt(r["备注"])))
        st.success("已保存")
        st.rerun()

man = one(conn, "SELECT * FROM carryover_in WHERE year = ?", (year,))
with st.expander("上年结转的资本亏损"):
    st.caption("默认用上一年计算出的结转额（非居民年份的亏损不能结转）。需要时可以手动填写。")
    a1, a2, a3 = st.columns(3)
    mst = a1.number_input("短期亏损结转", min_value=0.0, value=float(D(man["st"])) if man else 0.0)
    mlt = a2.number_input("长期亏损结转", min_value=0.0, value=float(D(man["lt"])) if man else 0.0)
    if a3.button("保存手动结转"):
        with conn:
            conn.execute("INSERT OR REPLACE INTO carryover_in(year, st, lt) VALUES(?,?,?)", (year, str(mst), str(mlt)))
        st.rerun()
    if man and a3.button("改回自动"):
        with conn:
            conn.execute("DELETE FROM carryover_in WHERE year = ?", (year,))
        st.rerun()
