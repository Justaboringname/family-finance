"""B. 税务身份计算器：由出入境记录推出每年的身份。"""
from datetime import date

import pandas as pd
import streamlit as st

from core.db import rows, set_setting
from core.params import load_params
from core.residency import STATUS_CHOICES
from ui_theme import page_header, section, table
from ui_common import (txt, STATUS_OPTIONS, get_conn, is_read_only, green_card_date, load_params_safe, residency_all, save_tax_year,
                       tax_years_settings, to_date, today_et)

page_header("税务身份", "由出入境记录推算每年的实际居住测试、30% 规则天数和 First-Year Choice", eyebrow="美国税")
conn = get_conn()
today = today_et()

# ---------- 出入境记录 ----------
section("出入境记录")
st.caption("入境日、出境日都算在美。每次入境填当时的入境身份（F-1 / parole / 其他）。"
           "还没发生的行程（比如寒假）勾选「计划」，会计入预测。")
trav = rows(conn, "SELECT * FROM travel ORDER BY event_date, id")
DIR = {"entry": "入境", "exit": "出境"}
df = pd.DataFrame([{
    "日期": date.fromisoformat(r["event_date"]),
    "方向": DIR[r["direction"]],
    "身份": r["status"] or "",
    "计划": bool(r["planned"]),
    "备注": r["note"] or "",
} for r in trav], columns=["日期", "方向", "身份", "计划", "备注"])
edited = st.data_editor(
    df, num_rows="dynamic", width="stretch", hide_index=True, key="travel_editor",
    column_config={
        "日期": st.column_config.DateColumn(required=True, format="YYYY-MM-DD"),
        "方向": st.column_config.SelectboxColumn(options=["入境", "出境"], required=True),
        "身份": st.column_config.SelectboxColumn(options=STATUS_CHOICES),
        "计划": st.column_config.CheckboxColumn(),
    },
)
if st.button("保存出入境记录", type="primary"):
    rev = {v: k for k, v in DIR.items()}
    with conn:
        conn.execute("DELETE FROM travel")
        for _, r in edited.iterrows():
            d = to_date(r["日期"])
            if not d or r["方向"] not in rev:
                continue
            conn.execute("INSERT INTO travel(event_date, direction, status, planned, note) VALUES(?,?,?,?,?)",
                         (d.isoformat(), rev[r["方向"]], txt(r["身份"]), int(r["计划"] == True), txt(r["备注"])))  # noqa: E712 计划列可能是 numpy bool 或 None
    st.success("已保存")
    st.rerun()

has_parole = any(r["status"] == "parole" and r["direction"] == "entry" for r in trav)
if not has_parole:
    st.warning("还没有录入 AP（parole）入境记录。录入之前，工具会把 2026 年全部当作 F-1 豁免天数，计入天数为 0。"
               "出境和 AP 入境的具体日期都要补上。")

gc = green_card_date(conn)
c1, c2 = st.columns([1, 2])
new_gc = c1.date_input("绿卡批准日期（还没批就留空）", value=gc, min_value=date(2020, 1, 1), format="YYYY-MM-DD",
                       disabled=is_read_only())
if new_gc != gc and not is_read_only():
    set_setting(conn, "green_card_date", new_gc.isoformat() if new_gc else None)
    st.rerun()
c2.caption("combo 卡（EAD/AP）不是绿卡，不适用绿卡测试；I-485 批准后从批准日起适用绿卡测试。")

# ---------- 每年结果 ----------
res = residency_all(conn, today)
section("每年结果")
year_rows = []
for y, r in sorted(res.items()):
    year_rows.append({
        "年份": str(y),
        "在美总天数": r.us_days,
        "其中 F-1": r.f1_days,
        "豁免天数": r.exempt_days,
        "计入天数": r.counted_days,
        "加权（今+去/3+前/6）": f"{float(r.weighted):.2f}",
        "满足实际居住测试": "是" if r.spt_met else "否",
        "F-1 豁免年": "是" if r.exempt_year else "",
        "已用豁免年": r.exempt_years_used,
        "身份": STATUS_OPTIONS[r.status] + ("（FYC）" if r.fyc_elected and r.status == "dual_status" else ""),
        "居民起始日": str(r.residency_start or ""),
        "含预测": "是" if r.projected else "",
    })
table(pd.DataFrame(year_rows), hide_index=True, width="stretch")
p = load_params(2026)
st.caption(
    f"实际居住测试：今年计入 ≥{p.v('us_residency.spt_min_current_year_days')} 天，且加权 ≥{p.v('us_residency.spt_threshold')}。"
    f"F-1 学生前 {p.v('us_residency.student_exempt_calendar_years')} 个日历年的 F-1 天数不计入（有一天是 F-1 就算一年）。"
    "非居民 30% 规则看「在美总天数」（含 F-1 天数）。"
)
for y, r in sorted(res.items()):
    for n in r.notes:
        st.caption(f"{y}：{n}")
    if r.spt_met_date and r.default_status != "nonresident" and r.year >= today.year:
        st.caption(f"{y}：按现有记录预计 {r.spt_met_date} 满足实际居住测试")

# ---------- First-Year Choice ----------
section("First-Year Choice")
for y, r in sorted(res.items()):
    # 一直显示到这一年延期申报截止日（次年 10-15），申报和延期提醒正好落在这段时间
    if r.default_status != "nonresident" or y < today.year - 1 or today > date(y + 1, 10, 15):
        continue
    # 去年的区块只在还能做（或已勾选）First-Year Choice 时显示，否则提醒没有意义
    if y < today.year and not (r.fyc.eligible or r.fyc_elected):
        continue
    with st.container(border=True):
        st.markdown(f"**{y} 年**：默认全年非居民（计入 {r.counted_days} 天，在美总共 {r.us_days} 天）")
        for name, ok, detail in r.fyc.checks:
            st.markdown(f"- {'✅' if ok else '❌'} {name}　{detail}")
        if r.fyc.eligible:
            st.success(f"可以做 First-Year Choice：居民起始日 {r.fyc.start}，{y} 年变为双重身份年。")
        else:
            st.info("目前不满足 First-Year Choice 条件（或依赖的下一年天数还不够）。")
        s = tax_years_settings(conn, y)
        elect = st.checkbox(f"{y} 年做 First-Year Choice（用于「美国联邦税」的默认身份）", value=bool(s["fyc_elect"]),
                            key=f"fyc_{y}", disabled=is_read_only() or (not r.fyc.eligible and not s["fyc_elect"]))
        if elect != bool(s["fyc_elect"]) and not is_read_only():
            save_tax_year(conn, y, fyc_elect=int(elect))
            st.rerun()
        nxt = res.get(y + 1)
        pn = load_params_safe(y)
        st.markdown(
            f"- 提醒：要等 {y + 1} 年满足实际居住测试后才能报 {y} 年的税"
            + (f"（按现有记录预计 {nxt.spt_met_date}）" if nxt and nxt.spt_met_date else "")
            + f"。{y + 1}-04-15 前用 Form 4868 申请延期（最晚到 {y + 1}-10-15）。\n"
            f"- 延期时要先按「{y} 年全年非居民」算税并缴纳：在美满 183 天时就是净收益的 30%；"
            "做完 First-Year Choice 正式申报后，多交的部分退回。（"
            f"[Reg. §301.7701(b)-4(c)(3)(v)]({pn.ref('us_residency.fyc_extension_pay_as_nonresident').source})）"
        )

# ---------- 成为居民后的申报提醒 ----------
section("成为居民后的申报提醒")
pr = load_params(2026)
st.markdown(
    f"- **FBAR（FinCEN 114）**：境外金融账户合计任一时点超过 ${pr.v('us_reporting.fbar_aggregate_threshold'):,}"
    f" 就要报（[来源]({pr.ref('us_reporting.fbar_aggregate_threshold').source})）。\n"
    f"- **Form 8938**：单身住在美国，境外金融资产年末超过 ${pr.v('us_reporting.form8938_single_us_year_end'):,}"
    f" 或年内任一时点超过 ${pr.v('us_reporting.form8938_single_us_any_time'):,}；双重身份年只看居民期间"
    f"（[来源]({pr.ref('us_reporting.form8938_single_us_year_end').source})）。\n"
    f"- **Form 5471**：持有境外公司 {pr.v('us_reporting.form5471_ownership_threshold'):.0%} 以上股份要报；"
    f"成为美国税务居民的当天就可能触发 Category 3（[来源]({pr.ref('us_reporting.form5471_ownership_threshold').source})）。"
)

section("手动覆盖")
st.caption("上面的结果会自动作为「美国联邦税」的身份设置。需要手动覆盖身份、居民起始日或在美天数，请到「美国联邦税」页面修改。")
