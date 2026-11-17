"""个人理财工具（只在本机运行）。启动：./run.sh"""
import os
import sqlite3
from pathlib import Path

import streamlit as st

from core.db import get_setting
from core.wealth import Wealth, build_wealth, record_snapshot
from ui_common import DISCLAIMER, SNAPSHOT_ENV, market_state, refresh_access, system_conn, today_et
from ui_theme import appearance_js, footer_html, inject_css

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="家庭资产与税务", page_icon=str(ROOT / "assets" / "icon.svg"), layout="wide")
st.logo(str(ROOT / "assets" / "logo.svg"), size="large", icon_image=str(ROOT / "assets" / "icon.svg"))

pages = {
    "资产": [
        st.Page("views/overview.py", title="家族资产总览", icon=":material/space_dashboard:", default=True),
        st.Page("views/agenda.py", title="资金日历", icon=":material/calendar_month:"),
        st.Page("views/statement.py", title="月度对账单", icon=":material/description:"),
    ],
    "美国税": [
        st.Page("views/residency.py", title="税务身份", icon=":material/flight_land:"),
        st.Page("views/federal.py", title="联邦税", icon=":material/receipt_long:"),
        st.Page("views/simulator.py", title="卖出模拟", icon=":material/calculate:"),
        st.Page("views/optimize.py", title="优化提示", icon=":material/tips_and_updates:"),
    ],
    "家人": [
        st.Page("views/father_cn.py", title="爸爸的中国个税", icon=":material/family_restroom:"),
    ],
}
# 只读会话（爸妈等经 Tailscale 访问、且不在可修改名单里的人）看不到「数据管理」
read_only = refresh_access()
if not read_only:
    pages["设置"] = [st.Page("views/data_admin.py", title="数据管理", icon=":material/database:")]
nav = st.navigation(pages)

conn = system_conn()            # 行情缓存和净资产快照由程序自己维护，只读会话也照常更新
inject_css()                    # 涨跌颜色固定为绿涨红跌

with st.sidebar:
    st.divider()
    if read_only:
        st.caption(f":material/visibility: 只读模式 · {st.session_state.get('_viewer', '访客')}：只能查看，不能修改")
    # 外观一律跟随系统（macOS 日落自动切深色时一起变）；也会清掉以前在这台浏览器里手动选过的深浅色
    page_paths = ["/"] + [f"/{Path(f).stem}" for f in sorted(ROOT.glob("views/*.py"))]   # 页面地址 = 文件名
    st.html(appearance_js("auto", page_paths), unsafe_allow_javascript=True)
    offline = get_setting(conn, "offline", "0") == "1"
    snapshot = os.environ.get(SNAPSHOT_ENV)     # 给家人的只读副本：固定离线，不显示刷新按钮和离线提示
    if not snapshot and st.button("刷新行情", width="stretch", disabled=offline, icon=":material/refresh:",
                                  readonly_ok=True):
        st.session_state["_refresh_quotes"] = True
    quotes, fx, msgs = market_state(conn)
    if snapshot:
        msgs = [m for m in msgs if not m.startswith("离线模式")]
    st.session_state["_market"] = (quotes, fx, msgs)
    # 家族资产负债表：每次运行算一遍；行情齐全时按行情日期记一条净资产快照（走势和月度对账单用）
    try:
        wealth = build_wealth(conn, quotes, fx, today_et())
    except Exception as e:              # 一条坏数据不能让所有页面都打不开（包括用来修它的「数据管理」）
        wealth = Wealth([], [], fx, today=today_et())
        msgs = [*msgs, f"家族资产负债表没算出来：{type(e).__name__}: {e}"]
    st.session_state["_wealth"] = wealth
    try:
        record_snapshot(conn, wealth)
    except sqlite3.Error as e:          # 写快照失败不影响看页面
        msgs = [*msgs, f"净资产快照没有写入：{e}"]
    if fx:
        q = quotes["CNY=X"]
        st.caption(f"USD/CNY **{fx:.4f}** · {q.quote_date} · {q.source}")
    for m in msgs:
        st.caption(f"⚠️ {m}")
    if snapshot:
        st.caption(f":material/inventory_2: 这是 {snapshot} 的数据副本，存在这台电脑上，不联网。")
    else:
        st.caption(":material/lock: 所有数据只存在本机。联网只向 Yahoo 请求股票代码的行情。")

nav.run()
footer_html(DISCLAIMER)
