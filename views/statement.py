"""月度对账单：按私人银行月结单的格式，一个月一张，可以打印或存成 PDF。"""
import pandas as pd
import streamlit as st

from core.agenda import KINDS
from core.db import rows
from core.money import ZERO, D, fmt_cny, fmt_qty, fmt_usd
from core.portfolio import is_long_term
from core.statement import available_months, month_statement
from core.wealth import CLASSES, INTERNAL_TRANSFER_NOTES, account_label
from ui_common import current_market, family_events, get_conn, stop, today_et
from ui_html import esc, pair
from ui_theme import CLASS_SLOTS, agenda_list, allocation, note, nw_trend, page_header, section, table

conn = get_conn()
today = today_et()
quotes, fx_now, _ = current_market(conn)

months = available_months(conn, today)
ym = st.session_state.get("stmt_month") or months[0]
if ym not in months:
    ym = months[0]
stt = month_statement(conn, ym, today)
m, y = stt["month"], stt["year"]
closing = m.closing
fx = closing["fx"] if closing and closing["fx"] else fx_now
y_, mo = int(ym[:4]), int(ym[5:])
page_header(f"{y_} 年 {mo} 月对账单", "家族资产月结单 · 美元与人民币对照", eyebrow="资产",
            meta=[("期间", f"{stt['start']} 至 {stt['end']}"), ("报告日", f"{today}"),
                  ("汇率", f"USD/CNY {fx:.4f}（期末）" if fx else "—")])
c1, c2 = st.columns([2, 1])
c1.selectbox("月份", months, index=months.index(ym), format_func=lambda v: f"{v[:4]} 年 {int(v[5:])} 月",
             key="stmt_month")
with c2:
    # st.html 会去掉 onclick 这类属性，所以用同一段里的脚本接管点击；只装一次，页面重跑时不会叠加
    st.html('<div class="ff-noprint" style="display:flex;justify-content:flex-end;padding-top:26px">'
            '<button type="button" class="ff-print-btn">打印 / 存为 PDF</button></div>'
            '<script>if (!window.__ffPrint) { window.__ffPrint = true; document.addEventListener("click", function (e) {'
            ' if (e.target.closest && e.target.closest(".ff-print-btn")) window.print(); }); }</script>',
            unsafe_allow_javascript=True)

if not closing:
    st.info("这个月还没有净资产快照。快照在每次打开 app、行情齐全时自动记录，从 2026-10-16 开始，不回填更早的历史。")
    stop()


def cell(v, sign: bool = False) -> str:
    return pair(v, fx, sign, sign) if v is not None else '<span class="muted">—</span>'


# ---------- 01 资产变动摘要 ----------
section("资产变动摘要", "本期 / 本年")
lines = [
    ("期初净资产", f"本期：{m.opening_label}；本年：{y.opening_label}", m.opening["net_worth"] if m.opening else None,
     y.opening["net_worth"] if y.opening else None, False, ""),
    ("已记录的净转入", "「现金」里记的外部转入转出，按入账日；含期末在途、扣除期初在途；不含 EB-5 本金回款",
     m.net_in, y.net_in, True, ""),
    ("市值及其他变动", "含没记成转账的资金进出", m.change, y.change, True, ""),
    ("期末净资产", f"{closing['snap_date']} 收盘", closing["net_worth"], y.closing["net_worth"] if y.closing else None,
     False, "total"),
]
trs = "".join(f'<tr class="{cls}"><td>{esc(a)}<small>{esc(b)}</small></td><td>{cell(v1, s)}</td><td>{cell(v2, s)}</td></tr>'
              for a, b, v1, v2, s, cls in lines)
st.html(f'<div class="ff-blk"><table class="ff-activity"><thead><tr><th>项目</th><th>本期</th><th>本年</th></tr></thead>'
        f'<tbody>{trs}</tbody></table></div>')
note("人民币按期末汇率折算。现金按余额快照记账：如果有资金进出没有记成「现金转账」，会落在「市值及其他变动」里。")
if len(stt["series"]) >= 2:
    nw_trend([(s["snap_date"], s["net_worth"]) for s in stt["series"] if s["snap_date"] >= stt["start"].isoformat()]
             or [(s["snap_date"], s["net_worth"]) for s in stt["series"]], fx)

# ---------- 02 资产配置（期末） ----------
detail = closing["detail"]
section("资产配置", f"{closing['snap_date']} 收盘")
by_class = detail.get("by_class", {})
assets, liab = closing["assets"], closing["liabilities"]
foot = [{"label": "总资产", "usd": assets, "share": "100.00%"},
        {"label": "负债", "usd": -liab if liab else ZERO, "share": "—"},
        {"label": "家族净资产", "usd": closing["net_worth"], "share": "—"}]
allocation([{"name": k, "usd": D(by_class[k]), "day_pnl": None, "desc": ""} for k in CLASSES if k in by_class],
           fx, CLASS_SLOTS, foot, head="资产类别", show_pnl=False)

# ---------- 03 持仓明细（期末） ----------
section("持仓明细", f"{closing['snap_date']} 收盘 · 共 {len(detail.get('items', []))} 项")
table(pd.DataFrame([{"资产": i["name"], "账户": i["account"], "成员": i["member"], "类别": i["asset_class"],
                     "市值": f"{fmt_usd(D(i['usd']))} / {fmt_cny(D(i['usd']) * D(fx))}" if fx else fmt_usd(D(i["usd"])),
                     "备注": i.get("unlock") or i.get("note") or ""}
                    for i in sorted(detail.get("items", []), key=lambda i: -D(i["usd"]))]))

# ---------- 04 已实现收益 ----------
section("本期已实现收益", "Schwab 卖出")
if stt["realized"]:
    table(pd.DataFrame([{"卖出日": x.sale_date, "股票": x.ticker, "股数": fmt_qty(x.quantity),
                         "卖出所得": fmt_usd(x.proceeds), "成本": fmt_usd(x.cost_basis),
                         "收益": fmt_usd(x.gain, sign=True), "长短期": "长期" if is_long_term(x.open_date, x.sale_date) else "短期"}
                        for x in stt["realized"]] +
                       [{"卖出日": "合计", "股票": "", "股数": "", "卖出所得": "", "成本": "",
                         "收益": fmt_usd(stt["realized_gain"], sign=True), "长短期": ""}]))
    note("这里是账面收益，没有做 wash sale 调整；报税口径看「联邦税」页。")
else:
    note("本期没有已实现的卖出。")

# ---------- 05 资金进出 ----------
section("资金进出", "本期入账的现金转账")


def amt(v) -> str:
    return f"{fmt_usd(D(v), True)} / {fmt_cny(D(v) * D(fx), True)}" if fx else fmt_usd(D(v), True)


tr = rows(conn, "SELECT * FROM cash_transfers WHERE settle_date BETWEEN ? AND ? ORDER BY settle_date",
          (stt["start"].isoformat(), stt["end"].isoformat()))
lines_tr = [{"入账日": t["settle_date"], "可用日": t["available_date"] or "", "账户": account_label(t["account"]),
             "说明": (t["note"] or "") + ("（资产之间的转换，不算转入）" if (t["note"] or "") in INTERNAL_TRANSFER_NOTES else ""),
             "金额": amt(t["amount"])} for t in tr]
# 快照的净资产按「已记下的转账」算：还没入账的在途转账也在里面，这里单列出来，和摘要里的净转入对得上
if m.transit_close:
    lines_tr.append({"入账日": "期末在途", "可用日": "", "账户": "", "说明": "已计入期末净资产、下期才入账",
                     "金额": amt(m.transit_close)})
if m.transit_open:
    lines_tr.append({"入账日": "期初在途", "可用日": "", "账户": "", "说明": "期初净资产里已经含有、本期入账（上面已列出）",
                     "金额": amt(-m.transit_open)})
if lines_tr:
    table(pd.DataFrame(lines_tr), signed=["金额"])
    if m.opening and m.opening["snap_date"] >= stt["start"].isoformat():
        note(f"期初快照是 {m.opening['snap_date']}：那天及之前入账的转账已经含在期初净资产里，不计入本期净转入。")
else:
    note("本期没有记录的转账。")

# ---------- 06 接下来 60 天 ----------
section("接下来 60 天", f"从 {today} 起")
agenda_list([e for e in family_events(conn, today, quotes) if (e.start - today).days <= 60 and not e.done], fx, KINDS, today)
note("本对账单由本机数据生成，不是券商或银行的正式对账单。")
