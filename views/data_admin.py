"""数据管理：导入 Schwab CSV、录入卖出、修改现金 / AAPL / EB-5、手动行情。"""
import json
from datetime import date

import pandas as pd
import streamlit as st

from core.agenda import parse_milestones, parse_when
from core.db import get_setting, one, rows, set_setting
from core.importer import ImportError_, apply_import, parse_lot_details
from core.market import FX_TICKER, clear_override, set_override
from core.money import ZERO, D, fmt_qty, fmt_usd
from core.portfolio import (default_account, delete_sale, first_long_term_date, is_long_term, load_lots, mark_eb5_returned,
                            parse_fund_tickers, record_sale)
from core.wealth import DEFAULT_CASH_LIKE, account_label, default_owners, member_names, member_of, owners
from ui_theme import page_header, reset_sections, section, table
from ui_common import dec_or_none, flash, get_conn, parse_logins, to_date, today_et, txt

page_header("数据管理", "导入 Schwab CSV、记录卖出、修改现金 / AAPL / EB-5、手动行情", eyebrow="设置")
conn = get_conn()
today = today_et()
st.caption(f"数据库：./data/finance.db（已加入 .gitignore，只在本机）。")

tabs = st.tabs(["导入 Schwab CSV", "已实现卖出", "手动批次", "现金", "EB-5", "AAPL 股数", "手动行情", "负债", "设置"])

# ---------- 导入 ----------
with tabs[0]:
    reset_sections()
    st.caption("Schwab → Positions → 某只股票 → Lot Details → 导出 CSV。每只股票一个文件，可一次选多个。"
               "同一账户同一只股票会整体覆盖；Total 行跳过。")
    files = st.file_uploader("选择 Lot Details CSV", type=["csv"], accept_multiple_files=True)
    parsed, errors = [], []
    for f in files or []:
        try:
            parsed.append(parse_lot_details(f.getvalue(), f.name, default_account(conn)))
        except ImportError_ as e:
            errors.append(str(e))
        except Exception as e:  # 编码、格式等意外错误：只跳过这个文件
            errors.append(f"{f.name}：无法解析（{type(e).__name__}: {e}）")
    for e in errors:
        st.error(e)
    if parsed:
        table(pd.DataFrame([{
            "文件": pf.filename, "股票": pf.ticker, "账户": pf.account, "日期": pf.as_of, "批次数": len(pf.lots),
            "股数": fmt_qty(pf.total_quantity), "成本": fmt_usd(pf.total_cost),
            "提示": "；".join(pf.warnings),
        } for pf in parsed]), hide_index=True, width="stretch")
        mismatch = []
        for pf in parsed:
            for lot in pf.lots:
                ref = pf.as_of or today
                mine = "Long" if is_long_term(lot.open_date, ref) else "Short"
                if lot.holding_period and mine.lower() not in lot.holding_period.lower():
                    mismatch.append(f"{pf.ticker} {lot.open_date}：Schwab「{lot.holding_period}」，本工具算出 {mine} Term")
        for m in mismatch:
            st.caption("⚠️ 持有期与 Schwab 不一致：" + m)
        if st.button("确认导入（覆盖这些股票的现有批次）", type="primary"):
            for m in apply_import(conn, parsed):
                st.success(m)

# ---------- 已实现卖出 ----------
with tabs[1]:
    reset_sections()
    st.caption("录入已经成交的卖出：日期、股票、具体卖哪几批、价格、手续费。录入后会从批次里扣掉。")
    flash(key="sale")
    lots = load_lots(conn)
    tickers = sorted({l.ticker for l in lots})
    if tickers:
        c1, c2, c3, c4 = st.columns(4)
        t = c1.selectbox("股票", tickers, key="sale_t")
        sd = c2.date_input("成交日期", value=today, format="YYYY-MM-DD", key="sale_d")
        px = c3.number_input("成交价（美元）", min_value=0.0, value=0.0, format="%.4f", key="sale_p")
        fee = c4.number_input("手续费（美元）", min_value=0.0, value=0.0, key="sale_f")
        tl = [l for l in lots if l.ticker == t]
        df = pd.DataFrame([{"lot_id": l.id, "买入日": l.open_date, "持有股数": float(l.quantity),
                            "每股成本": float(l.cost_per_share), "卖出股数": 0.0} for l in tl])
        nonce = st.session_state.get("sale_nonce", 0)
        ed = st.data_editor(df, hide_index=True, width="stretch", key=f"sale_editor_{t}_{nonce}",
                            disabled=["lot_id", "买入日", "持有股数", "每股成本"], column_config={"lot_id": None})
        allocs = [(int(r["lot_id"]), dec_or_none(r["卖出股数"])) for _, r in ed.iterrows()
                  if (dec_or_none(r["卖出股数"]) or ZERO) > 0]
        note = st.text_input("备注", key="sale_note")
        if st.button("记录卖出", type="primary", disabled=not allocs or px <= 0):
            acct = next(l.account for l in tl)
            try:
                record_sale(conn, acct, t, sd, D(str(px)), D(str(fee)), allocs, note)
                st.session_state["sale_nonce"] = nonce + 1   # 清空编辑器，防止重复记录
                flash(f"已记录 {t} 卖出 {fmt_qty(sum((q for _, q in allocs), ZERO))} 股", "sale")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    sales = rows(conn, "SELECT * FROM sales ORDER BY sale_date DESC, id DESC")
    if sales:
        section("已记录的卖出")
        for s in sales:
            legs = rows(conn, "SELECT * FROM sale_lots WHERE sale_id = ?", (s["id"],))
            qty = sum((D(x["quantity"]) for x in legs), ZERO)
            basis = sum((D(x["cost_basis"]) for x in legs), ZERO)
            gain = qty * D(s["price"]) - D(s["fee"]) - basis
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"{s['sale_date']} **{s['ticker']}** {fmt_qty(qty)} 股 @ ${D(s['price']):,.4f}，手续费 "
                        f"{fmt_usd(s['fee'])}，收益 {fmt_usd(gain, sign=True)}　批次："
                        + "、".join(f"{x['open_date']}×{fmt_qty(D(x['quantity']))}" for x in legs))
            if c2.button("删除并还回批次", key=f"del_sale_{s['id']}"):
                delete_sale(conn, s["id"], restore_lots=True)
                st.rerun()

# ---------- 手动批次 ----------
with tabs[2]:
    reset_sections()
    st.caption("例如参与 IPO 买入后、还没导出 Schwab CSV 时，先在这里手动加一批。")
    c1, c2, c3, c4 = st.columns(4)
    mt = c1.text_input("股票代码").upper().strip()
    md = c2.date_input("买入日", value=today, format="YYYY-MM-DD", key="ml_d")
    mq = c3.number_input("股数", min_value=0.0, value=0.0, format="%.4f")
    mc = c4.number_input("每股成本（美元）", min_value=0.0, value=0.0, format="%.4f")
    if st.button("添加批次", disabled=not mt or mq <= 0):
        q, c = D(str(mq)), D(str(mc))
        with conn:
            conn.execute("INSERT INTO lots(account, ticker, open_date, quantity, cost_per_share, cost_basis, source)"
                         " VALUES(?,?,?,?,?,?,?)", (default_account(conn), mt, md.isoformat(), str(q), str(c), str(q * c), "manual"))
        st.success(f"已添加 {mt}，{first_long_term_date(md)} 起变成长期")
        st.rerun()
    manual = rows(conn, "SELECT * FROM lots WHERE source IN ('manual', 'restored') ORDER BY ticker, open_date")
    for r in manual:
        c1, c2 = st.columns([5, 1])
        c1.write(f"{r['ticker']} {r['open_date']} {fmt_qty(D(r['quantity']))} 股，成本 {fmt_usd(r['cost_basis'])}（{r['source']}）")
        if c2.button("删除", key=f"del_lot_{r['id']}"):
            with conn:
                conn.execute("DELETE FROM lots WHERE id = ?", (r["id"],))
            st.rerun()

# ---------- 现金 ----------
with tabs[3]:
    reset_sections()
    st.caption("现金单独显示，不计入当日盈亏。录一个新的余额后，此前已入账的转账不再重复计入。")
    c1, c2, c3 = st.columns(3)
    cd = c1.date_input("余额日期", value=today, format="YYYY-MM-DD", key="cash_d")
    ca = c2.number_input("现金及现金类资产（美元）", min_value=0.0, value=0.0, step=1000.0)
    cn = c3.text_input("备注", key="cash_n")
    if st.button("记录新余额", disabled=ca <= 0):
        with conn:
            conn.execute("INSERT INTO cash_snapshots(account, as_of, amount, note) VALUES(?,?,?,?)",
                         (default_account(conn), cd.isoformat(), str(D(str(ca))), cn or None))
        st.rerun()
    st.markdown("**转账（入账日在最新余额日期之后的才计入）**")
    tr = rows(conn, "SELECT * FROM cash_transfers ORDER BY initiated, id")
    tdf = pd.DataFrame([{"发起日": date.fromisoformat(r["initiated"]), "入账日": date.fromisoformat(r["settle_date"]),
                         "完全可用日": date.fromisoformat(r["available_date"]) if r["available_date"] else None,
                         "金额（美元，转出填负数）": float(D(r["amount"])), "备注": r["note"] or ""} for r in tr],
                       columns=["发起日", "入账日", "完全可用日", "金额（美元，转出填负数）", "备注"])
    ted = st.data_editor(tdf, num_rows="dynamic", hide_index=True, width="stretch", key="transfer_editor",
                         column_config={k: st.column_config.DateColumn(format="YYYY-MM-DD") for k in ["发起日", "入账日", "完全可用日"]})
    st.markdown("**计划用途**")
    pl = rows(conn, "SELECT * FROM cash_plans ORDER BY id")
    pdf = pd.DataFrame([{"用途": r["label"], "金额（美元）": float(D(r["amount"])), "预计时间": r["expected"] or "",
                         "备注": r["note"] or "", "关键节点": "；".join((r.get("milestones") or "").split("\n"))}
                        for r in pl], columns=["用途", "金额（美元）", "预计时间", "备注", "关键节点"])
    ped = st.data_editor(pdf, num_rows="dynamic", hide_index=True, width="stretch", key="plan_editor",
                         column_config={"预计时间": st.column_config.TextColumn(
                             help="写成 2026-11-20，或只写月份 2026-11；资金日历按这个日期显示"),
                             "关键节点": st.column_config.TextColumn(
                             help="可不填。用「；」隔开，每个写成「日期 [时:分 ET] 说明」，例如 2026-12-08 16:00 ET 改单截止；"
                                  "2026-12-09 定价。填了的计划在总览的「近期事项」里显示成息票，带时间线和倒计时")})
    if st.button("保存转账和计划"):
        t_rows, p_rows, bad = [], [], []
        for i, r in ted.iterrows():
            a, b = to_date(r["发起日"]), to_date(r["入账日"])
            amt = dec_or_none(r["金额（美元，转出填负数）"])
            if a and b and amt is not None:
                c = to_date(r["完全可用日"])
                t_rows.append((default_account(conn), a.isoformat(), b.isoformat(), c.isoformat() if c else None, str(amt),
                               txt(r["备注"])))
            elif any(txt(v) for v in r.values):
                bad.append(f"转账第 {i + 1} 行缺少发起日、入账日或金额")
        for i, r in ped.iterrows():
            when = txt(r["预计时间"])
            if txt(r["用途"]) and dec_or_none(r["金额（美元）"]) is not None:
                if when and not parse_when(when):
                    bad.append(f"计划第 {i + 1} 行的预计时间「{when}」看不懂，写成 2026-11-20 或 2026-11")
                    continue
                ms_text = txt(r.get("关键节点"))
                try:
                    ms = parse_milestones(ms_text)
                except ValueError as e:
                    bad.append(f"计划第 {i + 1} 行的关键节点：{e}")
                    continue
                p_rows.append((txt(r["用途"]), str(dec_or_none(r["金额（美元）"])), when, txt(r["备注"]),
                               "\n".join(f"{m.when:%Y-%m-%d}" + (f" {m.when:%H:%M} ET" if m.has_time else "") + f" {m.text}"
                                         for m in ms) or None))
            elif any(txt(v) for v in r.values):
                bad.append(f"计划第 {i + 1} 行缺少用途或金额")
        if bad:
            st.error("没有保存：" + "；".join(bad))
        else:
            with conn:
                conn.execute("DELETE FROM cash_transfers")
                conn.executemany("INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note)"
                                 " VALUES(?,?,?,?,?,?)", t_rows)
                conn.execute("DELETE FROM cash_plans")
                conn.executemany("INSERT INTO cash_plans(label, amount, expected, note, milestones) VALUES(?,?,?,?,?)",
                                 p_rows)
            flash("已保存转账和计划", "cash")
            st.rerun()
    flash(key="cash")

# ---------- EB-5 ----------
with tabs[4]:
    reset_sections()
    e = one(conn, "SELECT * FROM eb5 WHERE id = 1")
    if e:
        c1, c2 = st.columns(2)
        ep = c1.number_input("本金（美元）", min_value=0.0, value=float(D(e["principal"])), step=10000.0)
        ed_ = c2.date_input("预计回款日期", value=date.fromisoformat(e["expected_return_date"]), format="YYYY-MM-DD")
        if st.button("保存 EB-5"):
            with conn:
                conn.execute("UPDATE eb5 SET principal = ?, expected_return_date = ? WHERE id = 1",
                             (str(D(str(ep))), ed_.isoformat()))
            st.rerun()
        if e["returned"]:
            st.success(f"已于 {e['returned_date']} 回款并转为现金。")
        else:
            rd = st.date_input("实际回款日期", value=today, format="YYYY-MM-DD", key="eb5_rd")
            if st.button("标记已回款（转为现金）"):
                mark_eb5_returned(conn, rd)
                st.rerun()

# ---------- AAPL 股数 ----------
with tabs[5]:
    reset_sections()
    a = one(conn, "SELECT * FROM aapl_holdings WHERE id = 1")
    if a:
        c1, c2, c3 = st.columns(3)
        espp = c1.number_input("ESPP 股数", min_value=0.0, value=float(D(a["espp_shares"])), format="%.4f")
        rv = c2.number_input("RS 已归属股数", min_value=0.0, value=float(D(a["rs_vested_shares"])), format="%.4f")
        ru = c3.number_input("RS 未归属股数", min_value=0.0, value=float(D(a["rs_unvested_shares"])), format="%.4f")
        st.caption(f"合计 {fmt_qty(D(str(espp)) + D(str(rv)) + D(str(ru)))} 股（未归属也计入合计）。")
        if st.button("保存 AAPL 股数"):
            with conn:
                conn.execute("UPDATE aapl_holdings SET espp_shares=?, rs_vested_shares=?, rs_unvested_shares=? WHERE id=1",
                             (str(D(str(espp))), str(D(str(rv))), str(D(str(ru)))))
            st.rerun()

# ---------- 手动行情 ----------
with tabs[6]:
    reset_sections()
    st.caption(f"手动值优先于 yfinance。汇率填 {FX_TICKER}（1 美元兑人民币）。")
    ov = rows(conn, "SELECT * FROM price_override WHERE active = 1 ORDER BY ticker")
    for r in ov:
        c1, c2 = st.columns([5, 1])
        c1.write(f"{r['ticker']}：收盘 {r['close']}，前收盘 {r['prev_close'] or '同收盘'}（{r['as_of'] or ''}）")
        if c2.button("取消", key=f"clr_{r['ticker']}"):
            clear_override(conn, r["ticker"])
            st.rerun()
    c1, c2, c3, c4 = st.columns(4)
    ot = c1.text_input("代码（如 TSLA、AAPL、CNY=X）").upper().strip()
    oc = c2.number_input("收盘价", min_value=0.0, value=0.0, format="%.4f")
    op = c3.number_input("前收盘价（可 0 = 同收盘）", min_value=0.0, value=0.0, format="%.4f")
    od = c4.date_input("日期", value=today, max_value=today, format="YYYY-MM-DD", key="ov_d")
    if st.button("保存手动行情", disabled=not ot or oc <= 0):
        set_override(conn, ot, D(str(oc)), D(str(op)) if op > 0 else None, od.isoformat())
        st.rerun()

# ---------- 负债 ----------
with tabs[7]:
    reset_sections()
    st.caption("房贷、保证金借款、信用额度等。首页「家族净资产」= 总资产 − 负债；没有负债时留空即可。")
    li = rows(conn, "SELECT * FROM liabilities ORDER BY id")
    me = member_names(conn)[0]
    ldf = pd.DataFrame([{"名称": r["name"], "成员": r["member"] or me, "金额（美元）": float(D(r["amount"])),
                         "备注": r["note"] or ""} for r in li], columns=["名称", "成员", "金额（美元）", "备注"])
    led = st.data_editor(ldf, num_rows="dynamic", hide_index=True, width="stretch", key="liab_editor",
                         column_config={"名称": st.column_config.TextColumn(required=True),
                                        "金额（美元）": st.column_config.NumberColumn(min_value=0.0, format="%.2f",
                                                                                 required=True)})
    if st.button("保存负债"):
        keep = [(txt(r["名称"]), txt(r["成员"]) or me, str(dec_or_none(r["金额（美元）"])), txt(r["备注"]))
                for _, r in led.iterrows() if txt(r["名称"]) and dec_or_none(r["金额（美元）"]) is not None]
        with conn:
            conn.execute("DELETE FROM liabilities")
            conn.executemany("INSERT INTO liabilities(name, member, amount, note) VALUES(?,?,?,?)", keep)
        flash(f"已保存 {len(keep)} 笔负债", "liab")
        st.rerun()
    flash(key="liab")

# ---------- 设置 ----------
with tabs[8]:
    reset_sections()
    eq = st.text_input("wash sale 视为实质相同的股票（用 = 连接，多组用 ; 分隔）",
                       value=get_setting(conn, "wash_equivalents", "GOOG=GOOGL"))
    ft = st.text_input("按「基金」单独统计的代码（逗号分隔）", value=get_setting(conn, "fund_tickers", ""),
                       help="例如 SGOV。持仓明细里单独列为「基金」一组，照样计入合计和当日盈亏；税务计算不受影响。")
    cl = st.text_input("按「现金及类现金」统计的代码（逗号分隔）",
                       value=get_setting(conn, "cash_like_tickers", DEFAULT_CASH_LIKE),
                       help="短期国债基金这类几乎等同现金的持仓。家族资产总览按资产类别看时归入「现金及类现金」。")
    dn = st.text_input("首页问候语里的称呼（可留空）", value=get_setting(conn, "display_name", "") or "",
                       help="例如「晚上好，某某」。只存在本机数据库里。")
    eds = st.text_input("可以修改数据的 Tailscale 账号（逗号分隔）", value=get_setting(conn, "editor_logins", "") or "",
                        help="经 Tailscale 从手机等设备打开时，只有这些账号能修改；其他人（例如共享给爸妈）只能查看。"
                             "这台 Mac 本机打开时总是可以修改。")
    section("家庭成员", "称呼和每个账户属于谁；家族资产总览可以按成员看", numbered=False)
    me, father = member_names(conn)
    n1, n2 = st.columns(2)
    me_new = n1.text_input("本人的称呼", value=me, help="家族资产里按成员分组时显示的名字，例如英文名。只存在本机数据库里。")
    father_new = n2.text_input("爸爸的称呼", value=father, help="爸爸的 AAPL 股票计划默认归在这个名字下。")
    own = owners(conn)
    keys = ([f"schwab:{a}" for a in sorted({l.account for l in load_lots(conn)})]
            + [f"cash:{r['account']}" for r in rows(conn, "SELECT account FROM cash_snapshots UNION"
                                                        " SELECT account FROM cash_transfers ORDER BY account")]
            + ["aapl", "eb5"])
    labels = {"aapl": "AAPL 股票计划", "eb5": "EB-5"}
    cols = st.columns(min(4, len(keys)))
    new_own = {}
    for i, k in enumerate(keys):
        lbl = labels.get(k) or (("Schwab 持仓 " if k.startswith("schwab:") else "现金 ") + account_label(k.split(":", 1)[1]))
        new_own[k] = cols[i % len(cols)].text_input(lbl, value=member_of(own, k), key=f"owner_{k}").strip()
    if st.button("保存设置"):
        set_setting(conn, "wash_equivalents", eq)
        set_setting(conn, "fund_tickers", ",".join(sorted(parse_fund_tickers(ft))))
        set_setting(conn, "cash_like_tickers", ",".join(sorted(parse_fund_tickers(cl))))
        set_setting(conn, "display_name", dn.strip())
        set_setting(conn, "editor_logins", ",".join(sorted(parse_logins(eds))))
        set_setting(conn, "member_self", me_new.strip())
        set_setting(conn, "member_father", father_new.strip())
        # 只存和默认不同的归属，没单独指定的账户以后会跟着称呼走；指定给本人或爸爸的，改名后换成新称呼
        before, after = {"*": me, "aapl": father}, default_owners(conn)
        rename = {me: after["*"], father: after["aapl"]}
        custom = {k: rename.get(v, v) for k, v in new_own.items()
                  if v and v not in (before[k if k == "aapl" else "*"], after[k if k == "aapl" else "*"])}
        set_setting(conn, "owners", json.dumps(custom, ensure_ascii=False))
        st.success("已保存")
