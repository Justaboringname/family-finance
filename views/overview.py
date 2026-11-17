"""A. 家族资产总览：资产负债表、构成、流动性、集中度、走势、近期事项、持仓明细。"""
from datetime import datetime

import pandas as pd
import streamlit as st

from core.agenda import KINDS, next_milestone, parse_milestones
from core.db import get_setting, rows
from core.market import ET
from core.money import ZERO, fmt_pct, fmt_price, fmt_qty, fmt_usd
from core.portfolio import (aapl_holdings, cash_status, eb5_status, load_lots, lot_view, parse_fund_tickers,
                            ticker_view)
from core.wealth import (LIQUIDITY_TIMING, SINGLE_STOCK_FLAG, build_wealth, load_snapshots,
                         member_names)
from ui_common import card, current_market, family_events, get_conn, is_read_only, money, stat, today_et
from ui_theme import (ACCOUNT_SLOTS_ORDER, CLASS_SLOTS, agenda_list, allocation, concentration,
                      holdings_table, liquidity_ladder, note, nw_trend, overview_hero, page_header, section, table,
                      upcoming)

conn = get_conn()
today = today_et()
quotes, fx, _ = current_market(conn)
fxq = quotes.get("CNY=X")
w = st.session_state.get("_wealth") or build_wealth(conn, quotes, fx, today)

lots = load_lots(conn)
tick = ticker_view(lots, quotes)
funds = parse_fund_tickers(get_setting(conn, "fund_tickers", ""))
aapl = aapl_holdings(conn)
aq = quotes.get("AAPL")
eb5 = eb5_status(conn, today)
cash = cash_status(conn, today)

# 行情日期只看持有的股票（和 AAPL），不看汇率：工作日开盘前汇率的日线已经是今天，股票还是昨天
held = [quotes[t] for t in {r["ticker"] for r in tick} | ({"AAPL"} if aapl else set()) if t in quotes]
dates = sorted({q.quote_date for q in held if q.quote_date})
live = any(q.intraday for q in held)
hour = datetime.now(ET).hour
greet = ("早上好" if 5 <= hour < 11 else "中午好" if 11 <= hour < 13 else "下午好" if 13 <= hour < 18
         else "晚上好")                                   # 凌晨 0–5 点也算晚上
name = (get_setting(conn, "display_name", "") or "").strip()
meta = [("日期", f"{today:%Y-%m-%d} 周{'一二三四五六日'[today.weekday()]}"),
        ("行情", (dates[-1] + (" 盘中" if live else " 收盘")) if dates else "暂无")]
if fx:
    meta.append(("汇率", f"USD/CNY {fx:.4f}"))
page_header("家族资产总览", "家族资产负债表 · 美元与人民币对照", eyebrow=greet + (f"，{name}" if name else ""), meta=meta)

if fx is None:
    st.warning("缺少 USD/CNY 汇率：人民币金额暂不显示。可在侧边栏刷新行情，或在「数据管理」手动填写 CNY=X。")
intraday = [r["ticker"] for r in tick if r["intraday"]] + (["AAPL"] if aq and aq.intraday else [])
if intraday:
    st.info("盘中数据，非收盘价：" + "、".join(intraday))
if w.missing:
    st.warning("缺少行情，没算进总数：" + "、".join(w.missing) + "（可在侧边栏刷新，或在「数据管理」手动填写）")

# ---------- 首屏：家族净资产 ----------
quoted = [r for r in tick if r["unrealized"] is not None]
s_cost = sum((r["cost_basis"] for r in quoted), ZERO)          # 浮盈率的分母只含有行情的批次
s_unr = sum((r["unrealized"] for r in quoted), ZERO) if quoted else None
base = w.invested - w.day_pnl                                   # 昨天收盘时的投资资产（现金不计当日盈亏）
classes = w.group("asset_class")
overview_hero(
    "家族净资产", w.net_worth, w.day_pnl, w.day_pnl / base if base else None,
    [(g["name"], g["day_pnl"]) for g in classes if g["day_pnl"]], fx,
    [{"label": "总资产", "usd": w.assets},
     {"label": "负债", "usd": w.liabilities_total, "note": f"{len(w.liabilities)} 笔" if w.liabilities else "暂无"},
     {"label": "投资资产", "qual": "不含现金", "usd": w.invested},
     {"label": "累计浮盈", "qual": "Schwab 股票 + 基金", "usd": s_unr, "sign": True, "colored": True,
      "pct": s_unr / s_cost if s_unr is not None and s_cost else None}],
    issued=(f"{int(dates[-1][:4])} 年 {int(dates[-1][5:7])} 月 {int(dates[-1][8:10])} 日" + ("盘中" if live else "收盘"))
    if dates else "",
)

# ---------- 01 构成 ----------
section("资产构成", f"按市值，含现金 · 100% = {money(w.assets, fx)}")
# 2026-11-12 用户说按家庭成员分「不重要」，去掉了这个视角（网站和手机 app 都去掉）
VIEWS = ["按资产类别", "按账户"]
if st.session_state.get("alloc_view") not in (None, *VIEWS):   # 旧会话里可能还记着「按家庭成员」
    del st.session_state["alloc_view"]
view = st.segmented_control("构成视角", VIEWS, default="按资产类别", key="alloc_view",
                            label_visibility="collapsed") or "按资产类别"


def describe(g: dict) -> str:
    """一组里有什么：前几项的名称，例如「现金 · SGOV」或「GOOG · MSFT · NVDA」。"""
    names = list(dict.fromkeys(i.name for i in g["items"]))
    return " · ".join(names[:4]) + (f" 等 {len(names)} 项" if len(names) > 4 else "")


if view == "按资产类别":
    groups, slots, head = classes, CLASS_SLOTS, "资产类别"
    extra = {g["name"]: describe(g) for g in groups}
else:
    groups = w.group("account")
    slots = {g["name"]: ACCOUNT_SLOTS_ORDER[min(i, 4)] for i, g in enumerate(groups)}
    head = "账户"
    extra = {g["name"]: describe(g) for g in groups}
parts = [{"name": g["name"], "usd": g["usd"], "day_pnl": g["day_pnl"], "desc": extra[g["name"]]} for g in groups]
foot = [{"label": "总资产", "usd": w.assets, "share": "100.00%", "day_pnl": w.day_pnl},
        {"label": "负债", "usd": -w.liabilities_total if w.liabilities_total else ZERO, "share": "—", "day_pnl": None},
        {"label": "家族净资产", "usd": w.net_worth, "share": "—", "day_pnl": w.day_pnl}]
allocation(parts, fx, slots, foot, head=head)
if fxq and fxq.prev_close and fx:
    impact = (w.invested - w.day_pnl) * (fxq.close - fxq.prev_close)   # 昨天的美元投资资产 × 汇率变化
    note(f"人民币今日盈亏不含汇率波动。汇率变动影响（另计）：¥{impact:+,.2f}（{fxq.prev_close:.4f} → {fxq.close:.4f}）")
father = member_names(conn)[1]
father_txt = f" {father} " if father.isascii() else father         # 英文名前后留空格
note("现金及类现金含 SGOV 这类短期国债基金（在「数据管理 → 设置」里改）；"
     f"受限股票是{father_txt}的 AAPL 计划里还没归属的部分，照常计入总资产。")

# ---------- 02 流动性 ----------
section("流动性", "需要用钱时，多久能拿到")
liquidity_ladder(w.liquidity(), w.assets, fx, LIQUIDITY_TIMING)
if any(i.note.startswith("归属日期未录入") for i in w.items) and not is_read_only():
    st.page_link("views/father_cn.py", label="未归属部分还没录归属日期，去补上",
                 icon=":material/event:")

# ---------- 03 集中度 ----------
section("集中度", "单一持仓占总资产（现金和类现金不算）")
concentration(w.concentration(), fx, SINGLE_STOCK_FLAG)
note("只列事实，不构成投资建议。")

# ---------- 04 走势 ----------
snaps = load_snapshots(conn)
section("净资产走势", f"每个交易日记一次 · 共 {len(snaps)} 条" if snaps else "每个交易日记一次")
if len(snaps) >= 2:
    nw_trend([(s["snap_date"], s["net_worth"]) for s in snaps], fx)
    first, last = snaps[0], snaps[-1]
    note(f"自 {first['snap_date']} 起变化 {money(last['net_worth'] - first['net_worth'], fx, sign=True)}"
         "（含资金进出；扣除资金进出的变化见「月度对账单」）。")
else:
    since = snaps[0]["snap_date"] if snaps else f"{today}"
    note(f"从 {since} 开始记录，之后每个交易日自动记一条，攒够两天就能看到走势。历史不回填。")

# ---------- 05 近期事项 ----------
section("近期事项", "未来 90 天")
events = [e for e in family_events(conn, today, quotes) if (e.start - today).days <= 90 and not e.done]
# 有关键节点、而且还有节点没过去的资金计划画成息票（例如 IPO 申购），列表里就不再重复列它
now_et = datetime.now(ET)
coupons = []
for pl in rows(conn, "SELECT * FROM cash_plans ORDER BY id"):
    try:
        ms = parse_milestones(pl.get("milestones"))
    except ValueError:
        ms = []
    if ms and next_milestone(ms, now_et):
        coupons.append((pl, ms))
shown = {pl["label"] for pl, _ in coupons}
events = [e for e in events if not (e.kind == "cash" and e.title in shown)]
upcoming(coupons, events[:6 if coupons else 8], fx, KINDS, today, now_et, cash["total"] if cash["snapshot"] else None,
         member_names(conn)[0])
st.page_link("views/agenda.py", label="查看完整的资金日历", icon=":material/calendar_month:")

accounts = sorted({l.account for l in lots})
acct_txt = "、".join(a.replace("Schwab-", "尾号 ") for a in accounts)
stock_rows = [r for r in tick if r["ticker"] not in funds]
fund_rows = [r for r in tick if r["ticker"] in funds]

# ---------- 明细 ----------
section("持仓明细", f"Schwab {acct_txt}" + (f" · {'盘中价' if live else '收盘价'} {dates[-1]}" if dates else ""))
tabs = st.tabs(["每只股票", "按批次", "AAPL 股票计划", "EB-5", "现金"])

with tabs[0]:
    if len(accounts) > 1:
        acc_rows = []
        for a in accounts:
            tv = ticker_view([l for l in lots if l.account == a], quotes)
            mv = sum((r["market_value"] for r in tv if r["market_value"] is not None), ZERO)
            dp = sum((r["day_pnl"] for r in tv if r["day_pnl"] is not None), ZERO)
            acc_rows.append({"账户": a, "市值": money(mv, fx), "今日盈亏": money(dp, fx, sign=True)})
        table(pd.DataFrame(acc_rows))
    lv_all = lot_view(lots, quotes, today)
    for r in tick:
        mine = [x for x in lv_all if x["ticker"] == r["ticker"]]
        n_lt = sum(1 for x in mine if x["term"] == "长期")
        term = ("长期" if n_lt == len(mine) else "短期" if n_lt == 0 else f"{n_lt} 批长期") if mine else ""
        r["meta_html"] = f'{len(mine)} 批 · ' + (f'<span class="lt">{term}</span>' if n_lt else term)

    def by_move(rs):
        return sorted(rs, key=lambda r: -abs(r["day_pnl"] or ZERO))
    groups = [{"title": "股票", "desc": f"{len(stock_rows)} 只 · 按今日盈亏绝对值排序", "rows": by_move(stock_rows)}]
    if fund_rows:
        groups.append({"title": "基金", "desc": f"{len(fund_rows)} 只 · 单独成类，照常计入合计",
                       "rows": by_move(fund_rows)})
    holdings_table(groups, fx, total_label="Schwab 合计")
    note("当天买入的批次，今日盈亏从买入价算起。")

with tabs[1]:
    tickers = sorted({l.ticker for l in lots})
    pick = st.multiselect("股票", tickers, default=tickers, label_visibility="collapsed", placeholder="选择股票")
    lv = [r for r in lot_view(lots, quotes, today) if r["ticker"] in pick]
    table(pd.DataFrame([{
        "股票": r["ticker"],
        "账户": r["account"].replace("Schwab-", "尾号 "),
        "买入日": r["open_date"],
        "股数": fmt_qty(r["quantity"]),
        "每股成本": fmt_price(r["cost_per_share"]),
        "成本": money(r["cost_basis"], fx),
        "市值": money(r["market_value"], fx) if r["market_value"] is not None else "",
        "浮盈": money(r["unrealized"], fx, sign=True) if r["unrealized"] is not None else "",
        "浮盈%": fmt_pct(r["unrealized_pct"], sign=True) if r["unrealized_pct"] is not None else "",
        "长短期": r["term"],
        "变成长期日期": r["long_term_date"],
        "还差天数": str(r["days_to_long_term"]) if r["days_to_long_term"] else "",
    } for r in lv]), signed=["浮盈", "浮盈%"])
    note("长期 = 在（买入日 + 1 天）+ 1 年当天或之后卖出（Pub 550）。")

with tabs[2]:
    if aapl:
        price = aq.close if aq else None
        c1, c2, c3 = st.columns(3)
        if price is not None:
            stat(c1, "合计（含未归属）", aapl["total"] * price, fx, note=f"{fmt_qty(aapl['total'])} 股 × ${price:,.2f}")
            stat(c2, "已归属（含 ESPP）", aapl["vested_incl_espp"] * price, fx, note=f"{fmt_qty(aapl['vested_incl_espp'])} 股")
            stat(c3, "今日盈亏", aapl["total"] * (aq.close - aq.prev_close), fx, sign=True, colored=True,
                 pct=(aq.close - aq.prev_close) / aq.prev_close if aq.prev_close else None)
        else:
            st.warning("缺 AAPL 行情")

        def val(n):
            return money(n * price, fx) if price is not None else "缺 AAPL 行情"
        table(pd.DataFrame([
            {"类别": "ESPP", "股数": fmt_qty(aapl["espp"]), "市值": val(aapl["espp"])},
            {"类别": "RS 已归属", "股数": fmt_qty(aapl["rs_vested"]), "市值": val(aapl["rs_vested"])},
            {"类别": "RS 未归属（计入合计）", "股数": fmt_qty(aapl["rs_unvested"]), "市值": val(aapl["rs_unvested"])},
            {"类别": "合计", "股数": fmt_qty(aapl["total"]), "市值": val(aapl["total"])},
        ]))
        if aq:
            note(f"AAPL 收盘 ${aq.close:,.2f}，前收 ${aq.prev_close:,.2f}（{aq.quote_date} {aq.source}）")

with tabs[3]:
    if eb5:
        if eb5["returned"]:
            st.success(f"已于 {eb5['returned_date']} 回款，已转为现金。")
        else:
            d = eb5["days_left"]
            c1, c2, c3 = st.columns(3)
            stat(c1, "本金", eb5["principal"], fx)
            card(c2, "预计回款", f"{eb5['expected_return_date']:%Y 年 %m 月 %d 日}")
            card(c3, "倒计时", f"{d} 天" if d >= 0 else f"已过 {-d} 天", f"约 {d / 365.25:.1f} 年" if d > 0 else None)
            note("不随市场变动，每日盈亏为 0，只随汇率换算人民币。回款日期在「数据管理」修改；回款后点「标记已回款」转为现金。")

with tabs[4]:
    snap = cash["snapshot"]
    lines = []
    if snap:
        lines.append({"项目": f"{snap['as_of']} 余额", "金额": money(cash["base"], fx), "状态": "",
                      "备注": snap["note"] or ""})
    for t in cash["transfers"]:
        lines.append({"项目": f"{t['initiated']} {t['note'] or '转入'}", "金额": money(t["amount"], fx, sign=True),
                      "状态": t["status"], "备注": ""})
    lines.append({"项目": "合计", "金额": money(cash["total"], fx), "状态": "", "备注": ""})
    table(pd.DataFrame(lines))
    for p in cash["plans"]:
        note(f"计划用途：{p['label']} 约 {fmt_usd(p['amount'])}（{p['expected'] or ''}）{p['note'] or ''}")
