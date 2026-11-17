"""资金日历：未来约 18 个月要用钱、会来钱、要报税的日子，以及自己加的提醒。"""
import pandas as pd
import streamlit as st

from core.agenda import KINDS, parse_when
from core.db import rows
from core.money import D
from ui_common import current_market, dec_or_none, family_events, flash, get_conn, today_et, txt
from ui_theme import agenda_list, note, page_header, section

conn = get_conn()
today = today_et()
quotes, fx, _ = current_market(conn)
page_header("资金日历", "转账到账、资金计划、归属、回款、批次转长期、报税截止日，按时间排好", eyebrow="资产")

events = family_events(conn, today, quotes)
kinds = st.segmented_control("类型", ["全部", *KINDS.values()], default="全部", key="agenda_kind",
                             label_visibility="collapsed") or "全部"
shown = [e for e in events if kinds == "全部" or KINDS.get(e.kind) == kinds]
section("时间轴", f"共 {len(shown)} 项")
agenda_list(shown, fx, KINDS, today)
note("报税截止日按每个纳税年度在「联邦税」页实际使用的身份推算（含手动覆盖）；遇周末或华盛顿特区解放日（4 月 16 日）"
     "顺延到下一个工作日，其他假日没有计入；具体以 IRS / 税务局和 CPA 的意见为准。资金计划只写到月份的，"
     "显示为「当月、日期未定」，整个月都留在日历上。")

# ---------- 自己加的提醒 ----------
section("我的提醒", "例如约 CPA、交学费、续签证件；日期可以只写到月份，如 2026-12")
own = rows(conn, "SELECT * FROM calendar_events ORDER BY event_date, id")
df = pd.DataFrame([{"日期": r["event_date"], "事项": r["title"], "成员": r["member"] or "",
                    "金额（美元）": float(D(r["amount"])) if r["amount"] else None, "备注": r["note"] or "",
                    "已完成": bool(r["done"])} for r in own],
                  columns=["日期", "事项", "成员", "金额（美元）", "备注", "已完成"])
ed = st.data_editor(df, num_rows="dynamic", hide_index=True, width="stretch", key="agenda_editor",
                    column_config={
                        "日期": st.column_config.TextColumn(required=True, help="YYYY-MM-DD，或只写月份 YYYY-MM"),
                        "事项": st.column_config.TextColumn(required=True),
                        "金额（美元）": st.column_config.NumberColumn(format="%.2f"),
                        "已完成": st.column_config.CheckboxColumn(),
                    })
if st.button("保存提醒", type="primary"):
    bad, keep = [], []
    for _, r in ed.iterrows():
        d, title = txt(r["日期"]), txt(r["事项"])
        if not d and not title:
            continue
        when = parse_when(d)
        if not when or not title:
            bad.append(d or "（空日期）")
            continue
        d = f"{when[0]:%Y-%m}" if when[1] else when[0].isoformat()
        amt = dec_or_none(r["金额（美元）"])
        keep.append((d, title, txt(r["成员"]), str(amt) if amt is not None else None, txt(r["备注"]),
                     int(bool(r["已完成"]) and r["已完成"] == True)))  # noqa: E712 可能是 numpy bool 或 None
    if bad:
        st.error("这些行的日期或事项不对，没有保存：" + "、".join(bad) + "。日期写成 2026-12-01 或 2026-12。")
    else:
        with conn:
            conn.execute("DELETE FROM calendar_events")
            conn.executemany("INSERT INTO calendar_events(event_date, title, member, amount, note, done)"
                             " VALUES(?,?,?,?,?,?)", keep)
        flash("已保存提醒", "agenda")
        st.rerun()
flash(key="agenda")
