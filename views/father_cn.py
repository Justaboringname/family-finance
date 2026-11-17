"""F. 爸爸的中国个税（AAPL 股票计划）。爸爸是中国税务居民、美国非居民。"""
import uuid
from datetime import date
from decimal import ROUND_CEILING

import pandas as pd
import streamlit as st

from core.cn_tax import LOSS_RULES, IncentiveItem, TransferItem, dividend_tax, equity_incentive_tax, property_transfer_tax
from core.db import get_setting, one, rows, set_setting
from core.market import cn_fx_rate
from core.money import ZERO, D, fmt_cny, fmt_qty, fmt_usd
from core.params import available_years, load_params
from core.portfolio import aapl_holdings
from ui_theme import page_header, reset_sections, section, table
from ui_common import (card, current_market, dec_or_none, flash, get_conn, is_read_only, money, render_trace, stat, to_date,
                       today_et, txt)

page_header("爸爸的中国个税", "AAPL 股票计划：股权激励、卖出、分红（中国税务居民、美国非居民）", eyebrow="家人")
conn = get_conn()
today = today_et()
quotes, fx, _ = current_market(conn)
aq = quotes.get("AAPL")
years = available_years()
P = load_params(today.year if today.year in years else years[-1])


def rate_for(on: date) -> tuple:
    """默认汇率：手动录入的中间价优先，否则用 CNY=X 近似（估算）。"""
    r, src = cn_fx_rate(conn, on)
    if r:
        return r, src
    return (fx, "CNY=X 市场汇率近似（估算，非中间价）") if fx else (None, "缺汇率")


tabs = st.tabs(["持仓与批次", "股权激励个税", "卖出模拟", "分红", "汇率与提醒"])

# ======================= 持仓与批次 =======================
with tabs[0]:
    reset_sections()
    h = aapl_holdings(conn)
    if h:
        c1, c2, c3 = st.columns(3)
        if aq:
            stat(c1, "合计（含未归属）", h["total"] * aq.close, fx, note=f"{fmt_qty(h['total'])} 股 × ${aq.close:,.2f}")
            stat(c2, "已归属（含 ESPP）", h["vested_incl_espp"] * aq.close, fx,
                 note=f"ESPP {fmt_qty(h['espp'])} + RS {fmt_qty(h['rs_vested'])} 股")
            stat(c3, "未归属 RS", h["rs_unvested"] * aq.close, fx, note=f"{fmt_qty(h['rs_unvested'])} 股，计入合计")
        else:
            card(c1, "合计股数", fmt_qty(h["total"]), "缺 AAPL 行情")
        st.caption("股数在「数据管理 → AAPL 股数」修改；记录卖出时可以自动扣减。")

    section("每一批的归属 / 购买记录")
    st.caption("RS 填归属日和当日收盘价；ESPP 填购买日、当日收盘价和实际每股购买价（算折扣用）。"
               "「已代扣个税」填单位在归属时代扣的人民币（可选，用来核对）。汇率填当次扣缴用的中间价（可选）。")
    lots = rows(conn, "SELECT * FROM aapl_lots ORDER BY event_date, id")
    ldf = pd.DataFrame([{
        "id": r["id"], "类型": r["lot_type"], "日期": date.fromisoformat(r["event_date"]), "股数": float(D(r["quantity"])),
        "当日收盘价（美元）": float(D(r["fmv"])),
        "ESPP 实际购买价（美元）": float(D(r["purchase_price"])) if r["purchase_price"] else None,
        "已代扣个税（元）": float(D(r["withheld_cny"])) if r["withheld_cny"] else None,
        "汇率": float(D(r["fx_rate"])) if r["fx_rate"] else None, "备注": r["note"] or "",
    } for r in lots], columns=["id", "类型", "日期", "股数", "当日收盘价（美元）", "ESPP 实际购买价（美元）", "已代扣个税（元）", "汇率", "备注"])
    led = st.data_editor(ldf, num_rows="dynamic", hide_index=True, width="stretch", key="aapl_lots_editor",
                         column_config={
                             "id": None,
                             "类型": st.column_config.SelectboxColumn(options=["RS", "ESPP"], required=True),
                             "日期": st.column_config.DateColumn(format="YYYY-MM-DD", required=True),
                             "股数": st.column_config.NumberColumn(min_value=0.0, format="%.4f", required=True),
                             "当日收盘价（美元）": st.column_config.NumberColumn(min_value=0.0, format="%.4f", required=True),
                             "ESPP 实际购买价（美元）": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
                             "已代扣个税（元）": st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
                             "汇率": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
                         })
    if st.button("保存批次", type="primary"):
        keep, bad, ops = set(), [], []
        for i, r in led.iterrows():
            d = to_date(r["日期"])
            qty, fmv = dec_or_none(r["股数"]), dec_or_none(r["当日收盘价（美元）"])
            if not d or r["类型"] not in ("RS", "ESPP") or qty is None or fmv is None:
                if any(txt(v) for k, v in r.items() if k != "id"):
                    bad.append(f"第 {i + 1} 行缺少类型、日期、股数或收盘价")
                continue
            pp, wh, fxr = (dec_or_none(r[k]) for k in ("ESPP 实际购买价（美元）", "已代扣个税（元）", "汇率"))
            vals = (r["类型"], d.isoformat(), str(qty), str(fmv), str(pp) if pp is not None else None,
                    str(wh) if wh is not None else None, str(fxr) if fxr is not None else None, txt(r["备注"]))
            rid = r["id"]
            ops.append((None if rid is None or pd.isna(rid) else int(rid), vals))
            if rid is not None and not pd.isna(rid):
                keep.add(int(rid))
        has_sales = {x["lot_id"] for x in rows(conn, "SELECT DISTINCT lot_id FROM aapl_sales")}
        blocked = [r for r in lots if r["id"] not in keep and r["id"] in has_sales]
        if blocked:
            bad.append("这些批次有卖出记录，不能删除（先在「卖出模拟」里删掉对应的卖出记录）："
                       + "、".join(f"{r['lot_type']} {r['event_date']}" for r in blocked))
        if bad:
            st.error("没有保存：" + "；".join(bad))
        else:
            with conn:
                for rid, vals in ops:
                    if rid is None:
                        conn.execute("INSERT INTO aapl_lots(lot_type, event_date, quantity, fmv, purchase_price,"
                                     " withheld_cny, fx_rate, note) VALUES(?,?,?,?,?,?,?,?)", vals)
                    else:
                        conn.execute("UPDATE aapl_lots SET lot_type=?, event_date=?, quantity=?, fmv=?, purchase_price=?,"
                                     " withheld_cny=?, fx_rate=?, note=? WHERE id=?", (*vals, rid))
                for r in lots:
                    if r["id"] not in keep:
                        conn.execute("DELETE FROM aapl_lots WHERE id = ?", (r["id"],))
            flash("已保存批次", "cn_lots")
            st.rerun()
    flash(key="cn_lots")
    if h and lots:
        espp = sum((D(r["quantity"]) for r in lots if r["lot_type"] == "ESPP"), ZERO)
        rs = sum((D(r["quantity"]) for r in lots if r["lot_type"] == "RS"), ZERO)
        st.caption(f"已录批次合计：ESPP {fmt_qty(espp)} 股（账户 {fmt_qty(h['espp'])}），"
                   f"RS {fmt_qty(rs)} 股（账户已归属 {fmt_qty(h['rs_vested'])}）。卖出过的股数不会从批次里扣，见「卖出模拟」。")

    section("未归属部分的预计归属时间表")
    vs = rows(conn, "SELECT * FROM aapl_vest_schedule ORDER BY expected_date, id")
    vdf = pd.DataFrame([{"预计归属日": date.fromisoformat(r["expected_date"]), "股数": float(D(r["quantity"])),
                         "预计收盘价（美元，可空）": float(D(r["expected_fmv"])) if r["expected_fmv"] else None,
                         "备注": r["note"] or ""} for r in vs],
                       columns=["预计归属日", "股数", "预计收盘价（美元，可空）", "备注"])
    ved = st.data_editor(vdf, num_rows="dynamic", hide_index=True, width="stretch", key="vest_editor",
                         column_config={"预计归属日": st.column_config.DateColumn(format="YYYY-MM-DD", required=True)})
    if st.button("保存归属时间表"):
        vrows, bad = [], []
        for i, r in ved.iterrows():
            d, q = to_date(r["预计归属日"]), dec_or_none(r["股数"])
            if d and q:
                fm = dec_or_none(r["预计收盘价（美元，可空）"])
                vrows.append((d.isoformat(), str(q), str(fm) if fm is not None else None, txt(r["备注"])))
            elif any(txt(v) for v in r.values):
                bad.append(f"第 {i + 1} 行缺少日期或股数")
        if bad:
            st.error("没有保存：" + "；".join(bad))
        else:
            with conn:
                conn.execute("DELETE FROM aapl_vest_schedule")
                conn.executemany("INSERT INTO aapl_vest_schedule(expected_date, quantity, expected_fmv, note) VALUES(?,?,?,?)",
                                 vrows)
            st.rerun()
    if h:
        sched = sum((D(r["quantity"]) for r in vs), ZERO)
        if vs and sched != h["rs_unvested"]:
            st.caption(f"时间表合计 {fmt_qty(sched)} 股，账户未归属 {fmt_qty(h['rs_unvested'])} 股。")

# ======================= 股权激励个税 =======================
with tabs[1]:
    reset_sections()
    st.caption("RS 归属、ESPP 买入折扣按「工资、薪金所得」。默认按上市公司股权激励单独计税（财政部 税务总局公告2023年第25号，"
               "执行至 2027-12-31）：全年股权激励收入 × 税率 − 速算扣除数，一年内多次合并计算。")
    c1, c2, c3 = st.columns(3)
    year = c1.selectbox("年份", years, index=years.index(today.year) if today.year in years else 0, key="cn_year")
    ro = is_read_only()
    filed = c2.toggle("境内机构已备案", value=get_setting(conn, "cn_equity_filed", "1") == "1", disabled=ro,
                      help="国税函〔2009〕461号：境外上市公司的境内机构须报送股权激励计划备案；未备案改为并入综合所得")
    own = c3.toggle("上市公司对境内雇主持股 ≥30%", value=get_setting(conn, "cn_listed_ownership_ok", "1") == "1",
                    disabled=ro)
    if filed != (get_setting(conn, "cn_equity_filed", "1") == "1") and not ro:
        set_setting(conn, "cn_equity_filed", "1" if filed else "0")
    if own != (get_setting(conn, "cn_listed_ownership_ok", "1") == "1") and not ro:
        set_setting(conn, "cn_listed_ownership_ok", "1" if own else "0")
    py = load_params(year)
    with_proj = st.checkbox("包含预计归属（时间表里本年的部分）", value=True)
    yr_end_rate, yr_src = rate_for(date(year, 12, 31) if date(year, 12, 31) <= today else today)
    default_rate = D(st.number_input("没填汇率的批次用这个汇率", min_value=0.0,
                                     value=float(yr_end_rate) if yr_end_rate else 7.0, format="%.4f",
                                     help=f"默认：{yr_src}。单位扣缴按申报月上一月末中间价（实施条例第三十二条）"))
    items, withheld = [], ZERO
    for r in rows(conn, "SELECT * FROM aapl_lots WHERE event_date BETWEEN ? AND ?", (f"{year}-01-01", f"{year}-12-31")):
        items.append(IncentiveItem(r["lot_type"], date.fromisoformat(r["event_date"]), D(r["quantity"]), D(r["fmv"]),
                                   D(r["purchase_price"]) if r["purchase_price"] else None,
                                   D(r["fx_rate"]) if r["fx_rate"] else default_rate))
        withheld += D(r["withheld_cny"] or 0)
    if with_proj:
        for r in rows(conn, "SELECT * FROM aapl_vest_schedule WHERE expected_date BETWEEN ? AND ?",
                      (f"{year}-01-01", f"{year}-12-31")):
            if date.fromisoformat(r["expected_date"]) < today:
                continue
            fm = D(r["expected_fmv"]) if r["expected_fmv"] else (aq.close if aq else None)
            if fm is not None:
                items.append(IncentiveItem("RS", date.fromisoformat(r["expected_date"]), D(r["quantity"]), fm, None,
                                           default_rate, projected=True))
    combined_inputs = not (filed and own and year <= 2027)
    o1, o2 = st.columns(2)
    other = D(o1.number_input("其他综合所得（元，并入综合所得时用）", min_value=0.0, value=0.0, step=10000.0,
                              disabled=not combined_inputs))
    ded = D(o2.number_input("专项扣除 + 专项附加扣除等（元）", min_value=0.0, value=0.0, step=1000.0,
                            disabled=not combined_inputs))
    if not items:
        st.info(f"{year} 年还没有归属 / 购买记录。请在「持仓与批次」补录。")
    else:
        rr = equity_incentive_tax(items, year, py, filed, own, other, ded)
        m1, m2, m3 = st.columns(3)
        def both(cny):
            return f"约 {fmt_usd(cny / default_rate)} / {fmt_cny(cny)}" if default_rate else fmt_cny(cny)
        card(m1, "全年股权激励收入", both(rr["income"]))
        card(m2, f"应纳税额（{rr['method']}）", both(rr["tax"]))
        card(m3, "已代扣合计", both(withheld), f"还需补 {both(rr['tax'] - withheld)}" if withheld else None)
        st.caption(f"人民币是计税依据；美元按 {default_rate} 折算，仅供参考。")
        render_trace(rr["trace"], currency="cny", expanded=True)

# ======================= 卖出模拟 =======================
with tabs[2]:
    reset_sections()
    st.caption("财产转让所得 20%。成本 = 归属日收盘价（ESPP 用购买日收盘价），手续费可扣。默认推荐归属价最高的批次，"
               "也可以手动选批次。")
    flash(key="cn_sale")
    sold = {}
    for x in rows(conn, "SELECT lot_id, quantity FROM aapl_sales"):
        sold[x["lot_id"]] = sold.get(x["lot_id"], ZERO) + D(x["quantity"])
    c1, c2, c3, c4 = st.columns(4)
    s_date = c1.date_input("卖出日期", value=today, format="YYYY-MM-DD", key="cn_sdate")
    s_price = D(c2.number_input("卖价（美元）", min_value=0.01, value=float(aq.close) if aq else 300.0, format="%.2f"))
    s_fee = D(c3.number_input("手续费（美元）", min_value=0.0, value=0.0))
    rule = c4.selectbox("亏损口径", list(LOSS_RULES), format_func=lambda k: LOSS_RULES[k],
                        index=list(LOSS_RULES).index(get_setting(conn, "cn_loss_rule", "annual_netting")),
                        disabled=is_read_only())
    if rule != get_setting(conn, "cn_loss_rule", "annual_netting") and not is_read_only():
        set_setting(conn, "cn_loss_rule", rule)
    r_def, r_src = rate_for(date(s_date.year, 12, 31) if date(s_date.year, 12, 31) <= today else today)
    if not r_def:
        st.warning("没有汇率：请在「汇率与提醒」录入中间价，或在下面手动填写。")
    s_rate = D(st.number_input("折算汇率", min_value=0.0001, value=float(r_def) if r_def else 7.0, format="%.4f",
                               help=f"默认按所得年度最后一日中间价（实施条例第三十二条第二句）；年中先用：{r_src}"))
    # 只能卖卖出日当天已经归属/购买的批次
    lots_av = rows(conn, "SELECT * FROM aapl_lots WHERE event_date <= ?", (s_date.isoformat(),))
    lots_av.sort(key=lambda r: (-D(r["fmv"]), r["event_date"]))  # 归属价最高的优先（数值排序）
    avail = [(r, D(r["quantity"]) - sold.get(r["id"], ZERO)) for r in lots_av]
    avail = [(r, q) for r, q in avail if q > 0]
    mode = st.radio("卖出方式", ["按股数（自动选批次）", "按金额（美元，自动选批次）", "手动选批次"], horizontal=True)
    picks = []
    if not avail:
        st.info("卖出日期之前还没有录入已归属的批次。可以先用下面的临时成本估算（不能记录）：")
        tmp_cost = D(st.number_input("每股成本（归属日收盘价，美元）", min_value=0.0, value=200.0))
        if mode.startswith("按金额"):
            qty = D(st.number_input("卖出金额（美元）", min_value=0.0, value=0.0)) / s_price
        else:
            qty = D(st.number_input("股数", min_value=0.0, value=0.0))
        if qty > 0:
            picks = [({"id": None, "event_date": "临时", "lot_type": "", "fmv": str(tmp_cost)}, qty)]
    elif mode == "手动选批次":
        nonce = st.session_state.get("cn_pick_nonce", 0)
        pdf = pd.DataFrame([{"lot_id": r["id"], "批次": f"{r['lot_type']} {r['event_date']}", "可卖股数": float(q),
                             "成本价（美元）": float(D(r["fmv"])), "卖出股数": 0.0} for r, q in avail])
        ped = st.data_editor(pdf, hide_index=True, width="stretch", key=f"cn_pick_{nonce}",
                             disabled=["lot_id", "批次", "可卖股数", "成本价（美元）"],
                             column_config={"lot_id": None, "卖出股数": st.column_config.NumberColumn(min_value=0.0)})
        for _, r in ped.iterrows():
            n = dec_or_none(r["卖出股数"]) or ZERO
            if n > 0:
                lot, q = next((x, q) for x, q in avail if x["id"] == int(r["lot_id"]))
                picks.append((lot, min(n, q)))
    else:
        total_av = sum((q for _, q in avail), ZERO)
        if mode.startswith("按股数"):
            qty = D(str(st.number_input(f"股数（可卖 {fmt_qty(total_av)}）", min_value=0.0, max_value=float(total_av),
                                        value=0.0)))
        else:
            amt = D(str(st.number_input("卖出金额（美元）", min_value=0.0, value=0.0, step=10000.0)))
            qty = min(total_av, (amt / s_price).quantize(D("0.0001"), rounding=ROUND_CEILING))
            st.caption(f"约 {fmt_qty(qty)} 股")
        need = qty
        for r, q in avail:  # 已按归属价从高到低
            if need <= 0:
                break
            take = min(q, need)
            picks.append((r, take))
            need -= take
    if picks:
        tq = sum((x[1] for x in picks), ZERO)
        items = [TransferItem(s_date, q, s_price, D(r["fmv"]), s_fee * q / tq, s_rate,
                              f"{r['lot_type']} {r['event_date']} ", order="new") for r, q in picks]
        prior = []
        for x in rows(conn, "SELECT s.*, l.fmv, l.lot_type, l.event_date FROM aapl_sales s JOIN aapl_lots l ON l.id = s.lot_id"
                            " WHERE s.sale_date BETWEEN ? AND ?", (f"{s_date.year}-01-01", f"{s_date.year}-12-31")):
            prior.append(TransferItem(date.fromisoformat(x["sale_date"]), D(x["quantity"]), D(x["price"]), D(x["fmv"]),
                                      D(x["fee"]), D(x["fx_rate"]) if x["fx_rate"] else s_rate,
                                      f"已卖 {x['lot_type']} {x['event_date']} ", order=x["order_no"] or f"s{x['id']}"))
        py = load_params(s_date.year if s_date.year in years else years[-1])
        base = property_transfer_tax(prior, py, rule)["tax"] if prior else ZERO
        rr = property_transfer_tax(prior + items, py, rule)
        inc = rr["tax"] - base
        gain = sum((it.gain for it in items), ZERO)
        m1, m2, m3 = st.columns(3)
        card(m1, "卖出股数", fmt_qty(tq))
        card(m2, "这次卖出的收益", f"{fmt_usd(gain / s_rate, sign=True)} / {fmt_cny(gain, sign=True)}")
        card(m3, "这次卖出带来的税", f"{fmt_usd(inc / s_rate)} / {fmt_cny(inc)}",
             "已和本年已记录的卖出一起算（年度互抵）" if prior and rule == "annual_netting" else None)
        table(pd.DataFrame([{"批次": f"{r['lot_type']} {r['event_date']}", "股数": fmt_qty(q),
                                    "成本价": f"${D(r['fmv']):,.2f}",
                                    "收益": f"{fmt_usd(q * (s_price - D(r['fmv'])), sign=True)} / "
                                            f"{fmt_cny(q * (s_price - D(r['fmv'])) * s_rate, sign=True)}"}
                                   for r, q in picks]), hide_index=True, width="stretch")
        render_trace(rr["trace"], currency="cny")
        if avail:
            future = s_date > today
            dec_h = st.checkbox("同时从「AAPL 股数」里扣减（ESPP 扣 ESPP、RS 扣已归属）", value=True)
            if st.button("记录为已实现卖出", disabled=future, help="卖出日期在未来时只能模拟" if future else None):
                order_no = f"{s_date.isoformat()}-{uuid.uuid4().hex[:8]}"   # 不会重复的订单号
                with conn:
                    for (r, q), it in zip(picks, items):
                        deducted = ZERO
                        if dec_h:
                            col = "espp_shares" if r["lot_type"] == "ESPP" else "rs_vested_shares"
                            cur = one(conn, f"SELECT {col} AS v FROM aapl_holdings WHERE id = 1")
                            if cur:
                                deducted = min(q, D(cur["v"]))
                                conn.execute(f"UPDATE aapl_holdings SET {col} = ? WHERE id = 1",
                                             (str(D(cur["v"]) - deducted),))
                        conn.execute("INSERT INTO aapl_sales(sale_date, lot_id, quantity, price, fee, fx_rate, order_no,"
                                     " holdings_deducted) VALUES(?,?,?,?,?,?,?,?)",
                                     (s_date.isoformat(), r["id"], str(q), str(s_price), str(it.fee), str(s_rate), order_no,
                                      str(deducted)))
                st.session_state["cn_pick_nonce"] = st.session_state.get("cn_pick_nonce", 0) + 1
                flash(f"已记录卖出 {fmt_qty(tq)} 股", "cn_sale")
                st.rerun()

    section("已记录的卖出")
    recs = rows(conn, "SELECT s.*, l.lot_type, l.event_date FROM aapl_sales s JOIN aapl_lots l ON l.id = s.lot_id"
                      " ORDER BY s.sale_date, s.id")
    if not recs:
        st.caption("还没有。")
    else:
        rdf = pd.DataFrame([{"id": x["id"], "卖出日": x["sale_date"], "批次": f"{x['lot_type']} {x['event_date']}",
                             "股数": fmt_qty(D(x["quantity"])), "卖价（美元）": float(D(x["price"])),
                             "手续费（美元）": float(D(x["fee"])), "汇率": float(D(x["fx_rate"])) if x["fx_rate"] else None,
                             "删除": False} for x in recs])
        red = st.data_editor(rdf, hide_index=True, width="stretch", key="cn_sales_editor",
                             disabled=["id", "卖出日", "批次", "股数"], column_config={"id": None})
        add_back = st.checkbox("删除时把记录时扣掉的股数加回「AAPL 股数」", value=True,
                               help="只加回当初记录这笔卖出时实际扣减的股数；当初没扣就不加")
        if st.button("保存卖出记录的修改"):
            by = {x["id"]: x for x in recs}
            with conn:
                for _, r in red.iterrows():
                    x = by[int(r["id"])]
                    if r["删除"] == True:  # noqa: E712
                        conn.execute("DELETE FROM aapl_sales WHERE id = ?", (x["id"],))
                        back = D(x["holdings_deducted"]) if x["holdings_deducted"] else ZERO
                        if add_back and back > 0:
                            col = "espp_shares" if x["lot_type"] == "ESPP" else "rs_vested_shares"
                            cur = one(conn, f"SELECT {col} AS v FROM aapl_holdings WHERE id = 1")
                            if cur:
                                conn.execute(f"UPDATE aapl_holdings SET {col} = ? WHERE id = 1",
                                             (str(D(cur["v"]) + back),))
                        continue
                    pr, fe, fr = dec_or_none(r["卖价（美元）"]), dec_or_none(r["手续费（美元）"]), dec_or_none(r["汇率"])
                    conn.execute("UPDATE aapl_sales SET price = ?, fee = ?, fx_rate = ? WHERE id = ?",
                                 (str(pr if pr is not None else D(x["price"])), str(fe or ZERO),
                                  str(fr) if fr is not None else None, x["id"]))
            flash("已保存卖出记录", "cn_sale")
            st.rerun()
        st.caption("汇率可以改成所得年度 12-31 的中间价（次年申报时用）；留空则按上面的折算汇率。")
    st.caption("爸爸是美国非居民、不在美国居住，卖 AAPL 的资本利得在美国不征税；中国按次年申报。")

# ======================= 分红 =======================
with tabs[3]:
    reset_sections()
    st.caption("分红按 20% 计税；美国按中美税收协定预扣 10% 可以抵免，国内补缴约 10%。需要境外纳税凭证。")
    dv = rows(conn, "SELECT * FROM aapl_dividends ORDER BY pay_date, id")
    ddf = pd.DataFrame([{"发放日": date.fromisoformat(r["pay_date"]), "分红毛额（美元）": float(D(r["gross_usd"])),
                         "美国预扣（美元）": float(D(r["us_withheld_usd"])),
                         "汇率": float(D(r["fx_rate"])) if r["fx_rate"] else None, "备注": r["note"] or ""} for r in dv],
                       columns=["发放日", "分红毛额（美元）", "美国预扣（美元）", "汇率", "备注"])
    ded_ = st.data_editor(ddf, num_rows="dynamic", hide_index=True, width="stretch", key="div_editor",
                          column_config={"发放日": st.column_config.DateColumn(format="YYYY-MM-DD", required=True)})
    if st.button("保存分红"):
        drows, bad = [], []
        for i, r in ded_.iterrows():
            d, g = to_date(r["发放日"]), dec_or_none(r["分红毛额（美元）"])
            if d and g:
                rt = dec_or_none(r["汇率"])
                drows.append((d.isoformat(), str(g), str(dec_or_none(r["美国预扣（美元）"]) or ZERO),
                              str(rt) if rt is not None else None, txt(r["备注"])))
            elif any(txt(v) for v in r.values):
                bad.append(f"第 {i + 1} 行缺少发放日或分红毛额")
        if bad:
            st.error("没有保存：" + "；".join(bad))
        else:
            with conn:
                conn.execute("DELETE FROM aapl_dividends")
                conn.executemany("INSERT INTO aapl_dividends(pay_date, gross_usd, us_withheld_usd, fx_rate, note)"
                                 " VALUES(?,?,?,?,?)", drows)
            st.rerun()
    for y in sorted({date.fromisoformat(r["pay_date"]).year for r in dv}):
        ys = [r for r in dv if r["pay_date"].startswith(str(y))]
        rate_y, src_y = rate_for(date(y, 12, 31) if date(y, 12, 31) <= today else today)
        if any(not r["fx_rate"] for r in ys) and not rate_y:
            st.warning(f"{y} 年：缺汇率，没有计算。请在「汇率与提醒」录入中间价，或给每笔分红填写汇率。")
            continue
        gross_usd = sum((D(r["gross_usd"]) for r in ys), ZERO)
        gross = sum((D(r["gross_usd"]) * (D(r["fx_rate"]) if r["fx_rate"] else rate_y) for r in ys), ZERO)
        wh = sum((D(r["us_withheld_usd"]) * (D(r["fx_rate"]) if r["fx_rate"] else rate_y) for r in ys), ZERO)
        dr = dividend_tax(gross, wh, load_params(y if y in years else years[-1]))
        avg = gross / gross_usd if gross_usd else None
        def both(cny):
            return f"{fmt_usd(cny / avg)} / {fmt_cny(cny)}" if avg else fmt_cny(cny)
        st.markdown(f"**{y} 年**：分红 {both(gross)}，中国应纳 {both(dr['cn_tax'])}，可抵免 {both(dr['credit'])}，"
                    f"需补缴 **{both(dr['payable'])}**")
        if any(not r["fx_rate"] for r in ys):
            st.caption(f"没填汇率的分红按 {rate_y}（{src_y}）折算。")
        render_trace(dr["trace"], currency="cny")

# ======================= 汇率与提醒 =======================
with tabs[4]:
    reset_sections()
    section("提醒")
    st.markdown(
        f"- 境外所得（卖出 AAPL、分红）在取得所得的**{P.v('cn_iit.foreign_income_filing_window')}**自行申报"
        f"（[个人所得税法第十三条]({P.ref('cn_iit.foreign_income_filing_window').source})）。\n"
        f"- 单独计税政策执行至 {P.v('cn_iit.equity_incentive_separate_valid_until')}，之后是否延续待确认"
        f"（[2023年第25号]({P.ref('cn_iit.equity_incentive_separate_valid_until').source})）。\n"
        f"- 适用单独计税需要：境内机构已备案、上市公司对境内雇主持股 ≥30%"
        f"（[国税函〔2009〕461号]({P.ref('cn_iit.equity_incentive_listed_min_ownership').source})）。\n"
        "- 抵免美国预扣税要有境外纳税凭证。"
    )
    section("汇率规则")
    st.markdown(f"- 单位扣缴（归属 / 购买）：{P.v('cn_iit.fx_rule_withholding')}\n"
                f"- 次年自行申报补税（卖出、分红）：{P.v('cn_iit.fx_rule_annual_filing')}\n"
                f"- {P.ref('cn_iit.fx_rule_annual_filing').note}（[实施条例第三十二条]({P.ref('cn_iit.fx_rule_annual_filing').source})）")
    section("人民币汇率中间价（手动录入）")
    st.caption("yfinance 的 CNY=X 是市场汇率，不是央行中间价。报税用的中间价请在这里录入（例如每年 12-31、每次扣缴的上月末）。")
    fr = rows(conn, "SELECT * FROM cn_fx_rates ORDER BY rate_date")
    fdf = pd.DataFrame([{"日期": date.fromisoformat(r["rate_date"]), "中间价": float(D(r["rate"])), "备注": r["note"] or ""}
                        for r in fr], columns=["日期", "中间价", "备注"])
    fed = st.data_editor(fdf, num_rows="dynamic", hide_index=True, key="fx_editor",
                         column_config={"日期": st.column_config.DateColumn(format="YYYY-MM-DD", required=True),
                                        "中间价": st.column_config.NumberColumn(format="%.4f", required=True)})
    if st.button("保存中间价"):
        with conn:
            conn.execute("DELETE FROM cn_fx_rates")
            for _, r in fed.iterrows():
                d = to_date(r["日期"])
                if d and dec_or_none(r["中间价"]):
                    conn.execute("INSERT OR REPLACE INTO cn_fx_rates(rate_date, rate, note) VALUES(?,?,?)",
                                 (d.isoformat(), str(dec_or_none(r["中间价"])), txt(r["备注"])))
        st.rerun()
