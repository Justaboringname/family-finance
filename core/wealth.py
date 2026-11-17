"""家族资产负债表：把 Schwab 持仓、现金、爸爸的 AAPL 股票计划、EB-5 和负债合成一张表。

同一份明细可以按「资产类别」「家庭成员」「账户」三种方式分组，三种分组的合计永远相同。
另外给出流动性阶梯（什么时候能用到这笔钱）、集中度（单一持仓占总资产多少），
以及每日净资产快照（走势和月度对账单用）。这里不依赖 Streamlit。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal

from .db import get_setting, one, rows
from .money import ZERO, D, fmt_usd
from .portfolio import (Quote, aapl_holdings, cash_status, default_account, eb5_status, load_lots,
                        parse_fund_tickers, ticker_view)

# 资产类别（私人银行的口径），顺序固定
CASH, LISTED, RESTRICTED, ALTERNATIVE = "现金及类现金", "上市股票", "受限股票", "另类投资"
CLASSES = [CASH, LISTED, RESTRICTED, ALTERNATIVE]
# 资产类别 → 分类色槽位（--ff-s1…s5 = 墨蓝、赭黄、青绿、赤陶、梅紫），固定不按大小排；网页和手机 app 共用
CLASS_SLOTS = {CASH: 2, LISTED: 1, RESTRICTED: 4, ALTERNATIVE: 3}
# 家庭成员的称呼：默认「我」「爸爸」，真实名字在「数据管理 → 设置」里改（存在本机数据库，不写进代码）。
# 爸爸的 AAPL 股票计划默认归爸爸，其余（Schwab 账户、现金、EB-5）默认归本人
DEFAULT_SELF, DEFAULT_FATHER = "我", "爸爸"
DEFAULT_CASH_LIKE = "SGOV"                  # 短期国债 ETF：按「现金及类现金」统计
SINGLE_STOCK_FLAG = Decimal("0.10")         # 单一股票超过总资产 10% 时标出来（只给数字，不给建议）
INTERNAL_TRANSFER_NOTES = ("EB-5 本金回款",)  # 资产之间的转换，不算外部资金进出

# 流动性阶梯
NOW, T1, PENDING, VESTING, LOCKED = "今天可用", "1 个工作日内", "在途", "受限", "锁定"
LIQUIDITY = [NOW, T1, PENDING, VESTING, LOCKED]
# 每一级的说明和流动性台阶的填充方式（实色 = 随时能用或 T+1，点 = 在途，斜线 = 受限，网格 = 锁定）；网页和手机 app 共用
LIQUIDITY_TIMING = {NOW: "已到账的现金", T1: "卖出后下一个工作日到账", PENDING: "转账在途，按可用日期",
                    VESTING: "按归属日期；归属后才能卖", LOCKED: "到期前拿不回来"}
LIQUIDITY_FILL = {NOW: "solid", T1: "solid", PENDING: "dots", VESTING: "hatch", LOCKED: "cross"}


def account_label(account: str) -> str:
    return account.replace("Schwab-", "Schwab 尾号 ")


@dataclass
class Item:
    """资产负债表里的一行。usd 为市值（现金为余额）；day_pnl 为 None 表示不计当日盈亏（现金）。"""
    key: str
    name: str
    account: str
    asset_class: str
    member: str
    usd: Decimal
    day_pnl: Decimal | None
    liquidity: str
    unlock: str | None = None          # ISO 日期：在途现金到账日、归属日、EB-5 回款日
    note: str = ""
    underlying: str | None = None      # 集中度按「同一标的」合并，例如 AAPL 已归属 + 未归属


@dataclass
class Wealth:
    items: list[Item]
    liabilities: list[dict]
    fx: Decimal | None
    missing: list[str] = field(default_factory=list)   # 缺行情的代码
    quote_date: str | None = None                       # 持仓行情的最新日期（不看汇率）
    intraday: bool = False                              # 持仓里有盘中价（不是收盘价）
    today: date | None = None
    transfers: list[dict] = field(default_factory=list)  # 计入现金的转账（最新余额之后入账的）
    funds: frozenset[str] = frozenset()                  # 设置里按「基金」统计的代码：集中度不按单一股票标注

    @property
    def assets(self) -> Decimal:
        return sum((i.usd for i in self.items), ZERO)

    @property
    def liabilities_total(self) -> Decimal:
        return sum((D(x["amount"]) for x in self.liabilities), ZERO)

    @property
    def net_worth(self) -> Decimal:
        return self.assets - self.liabilities_total

    @property
    def cash(self) -> Decimal:
        """真正的现金余额（不含 SGOV 这类类现金证券）。"""
        return sum((i.usd for i in self.items if i.key.startswith("cash:")), ZERO)

    @property
    def invested(self) -> Decimal:
        """投资资产 = 总资产 − 现金余额（即原来的「合计（不含现金）」）。"""
        return self.assets - self.cash

    @property
    def day_pnl(self) -> Decimal:
        return sum((i.day_pnl for i in self.items if i.day_pnl is not None), ZERO)

    def group(self, by: str) -> list[dict]:
        """按 asset_class / member / account 分组：[{name, usd, day_pnl, items}]，类别按固定顺序。"""
        out: dict[str, dict] = {}
        for i in self.items:
            k = getattr(i, by)
            g = out.setdefault(k, {"name": k, "usd": ZERO, "day_pnl": None, "items": []})
            g["usd"] += i.usd
            if i.day_pnl is not None:
                g["day_pnl"] = (g["day_pnl"] or ZERO) + i.day_pnl
            g["items"].append(i)
        order = CLASSES if by == "asset_class" else list(out)
        return [out[k] for k in sorted(out, key=lambda k: order.index(k) if k in order else len(order))]

    def liquidity(self) -> list[dict]:
        """流动性阶梯：[{bucket, usd, rows:[{name, member, usd, unlock, note}]}]，按能用到钱的先后排序。"""
        out = []
        for b in LIQUIDITY:
            its = [i for i in self.items if i.liquidity == b]
            if its:
                its.sort(key=lambda i: (i.unlock is None, i.unlock or "", -i.usd))   # 没有日期的排最后
                out.append({"bucket": b, "usd": sum((i.usd for i in its), ZERO), "rows": its})
        return out

    def concentration(self, top: int = 5) -> list[dict]:
        """单一持仓占总资产的比例（现金和类现金不算），从大到小。"""
        agg: dict[str, dict] = {}
        for i in self.items:
            if i.asset_class == CASH or not i.underlying:
                continue
            g = agg.setdefault(i.underlying, {"name": i.underlying, "usd": ZERO, "members": set(), "kinds": set()})
            g["usd"] += i.usd
            g["members"].add(i.member)
            g["kinds"].add(i.asset_class)
        total = self.assets
        out = []
        for g in sorted(agg.values(), key=lambda g: -g["usd"])[:top]:
            share = g["usd"] / total if total else ZERO
            single_stock = g["kinds"] <= {LISTED, RESTRICTED} and g["name"] not in self.funds
            out.append({**g, "share": share, "flag": single_stock and share > SINGLE_STOCK_FLAG})
        return out


def member_names(conn: sqlite3.Connection) -> tuple[str, str]:
    """（本人, 爸爸）的称呼：设置里的 member_self / member_father，没设时用「我」「爸爸」。"""
    return ((get_setting(conn, "member_self", "") or "").strip() or DEFAULT_SELF,
            (get_setting(conn, "member_father", "") or "").strip() or DEFAULT_FATHER)


def default_owners(conn: sqlite3.Connection) -> dict[str, str]:
    """没有单独指定时的归属："*" 是其余所有来源的默认成员。"""
    me, father = member_names(conn)
    return {"*": me, "aapl": father}


def owners(conn: sqlite3.Connection) -> dict[str, str]:
    """资产来源 → 家庭成员。设置里存 JSON（例如 {"schwab:Schwab-123": "妈妈"}），只存和默认不同的；没设的用默认。"""
    try:
        saved = json.loads(get_setting(conn, "owners", "") or "{}")
    except ValueError:
        saved = {}
    if not isinstance(saved, dict):     # 手改数据库或恢复旧数据时可能不是对象；不能让所有页面都打不开
        saved = {}
    return {**default_owners(conn), **{k: v.strip() for k, v in saved.items() if isinstance(v, str) and v.strip()}}


def member_of(own: dict[str, str], key: str) -> str:
    return own.get(key) or own.get("*") or DEFAULT_SELF


def cash_like_tickers(conn: sqlite3.Connection) -> frozenset[str]:
    return parse_fund_tickers(get_setting(conn, "cash_like_tickers", DEFAULT_CASH_LIKE))


def vest_allocation(conn: sqlite3.Connection, today: date, unvested: Decimal) -> tuple[list[tuple[dict, Decimal]], Decimal, bool]:
    """把未归属股数按归属时间表从早到晚分配，资产负债表和资金日历共用。

    已过预计归属日的行不再占用股数（那部分应该已经转为已归属；「爸爸的中国个税」页同样跳过）；
    时间表合计超过未归属股数时，后面的行按剩下的股数截断。
    返回 ([(时间表行, 股数)], 没排上日期的股数, 时间表里是否有已过期的行)。
    """
    left, out, past = unvested, [], False
    for v in rows(conn, "SELECT * FROM aapl_vest_schedule ORDER BY expected_date, id"):
        if v["expected_date"] < today.isoformat():
            past = True
            continue
        q = min(D(v["quantity"]), left)
        if q <= 0:
            continue
        left -= q
        out.append((v, q))
    return out, left, past


def build_wealth(conn: sqlite3.Connection, quotes: dict[str, Quote], fx: Decimal | None, today: date) -> Wealth:
    own = owners(conn)
    cash_like = cash_like_tickers(conn)
    items: list[Item] = []
    missing: list[str] = []
    held_dates: list[str] = []
    intraday = False
    transfers: list[dict] = []

    # Schwab 持仓：按账户、按代码
    lots = load_lots(conn)
    for acct in sorted({l.account for l in lots}):
        for r in ticker_view([l for l in lots if l.account == acct], quotes):
            if r["market_value"] is None:
                missing.append(r["ticker"])
                continue
            held_dates.append(r["quote_date"])
            intraday = intraday or r["intraday"]
            is_cash_like = r["ticker"] in cash_like
            items.append(Item(
                key=f"lot:{acct}:{r['ticker']}", name=r["ticker"], account=account_label(acct),
                asset_class=CASH if is_cash_like else LISTED, member=member_of(own, f"schwab:{acct}"),
                usd=r["market_value"], day_pnl=r["day_pnl"], liquidity=T1,
                note="卖出后 T+1 到账", underlying=r["ticker"]))

    # 现金：每个有现金记录的账户
    cash_accounts = sorted({r["account"] for r in rows(conn, "SELECT DISTINCT account FROM cash_snapshots")}
                           | {r["account"] for r in rows(conn, "SELECT DISTINCT account FROM cash_transfers")})
    for acct in cash_accounts or [default_account(conn)]:
        cs = cash_status(conn, today, acct)
        if not cs["snapshot"] and not cs["transfers"]:
            continue
        member = member_of(own, f"cash:{acct}")
        transfers.extend(cs["transfers"])
        # 还没完全可用的转入单列为「在途」；待转出的钱现在就不能再用，直接从可用余额里扣掉，不单列
        pending = [t for t in cs["transfers"]
                   if t["amount"] > 0 and today < date.fromisoformat(t["available_date"] or t["settle_date"])]
        pending_total = sum((t["amount"] for t in pending), ZERO)
        outgoing = sum((t["amount"] for t in cs["transfers"]
                        if t["amount"] < 0 and today < date.fromisoformat(t["settle_date"])), ZERO)
        items.append(Item(key=f"cash:{acct}", name="现金", account=account_label(acct), asset_class=CASH,
                          member=member, usd=cs["total"] - pending_total, day_pnl=None, liquidity=NOW,
                          note="已到账、可以使用" + (f"（已扣除待转出的 {fmt_usd(-outgoing)}）" if outgoing else "")))
        for t in pending:
            items.append(Item(key=f"cash:{acct}:transfer:{t['id']}", name=t["note"] or "转入", account=account_label(acct),
                              asset_class=CASH, member=member, usd=t["amount"], day_pnl=None, liquidity=PENDING,
                              unlock=t["available_date"] or t["settle_date"], note=t["status"]))

    # 爸爸的 AAPL 股票计划：已归属（含 ESPP）算上市股票；未归属算受限股票，按归属时间表拆开
    a = aapl_holdings(conn)
    if a and a["total"] > 0:
        aq = quotes.get("AAPL")
        member = member_of(own, "aapl")
        if aq is None:
            missing.append("AAPL")
        else:
            held_dates.append(aq.quote_date)
            intraday = intraday or aq.intraday
            per_share_pnl = aq.close - aq.prev_close
            items.append(Item(key="aapl:vested", name="AAPL（已归属，含 ESPP）", account="AAPL 股票计划",
                              asset_class=LISTED, member=member, usd=a["vested_incl_espp"] * aq.close,
                              day_pnl=a["vested_incl_espp"] * per_share_pnl, liquidity=T1,
                              note="卖出后 T+1 到账", underlying="AAPL"))
            alloc, left, past = vest_allocation(conn, today, a["rs_unvested"])
            for v, q in alloc:
                items.append(Item(key=f"aapl:vest:{v['id']}", name=f"AAPL 未归属 {q.normalize():f} 股",
                                  account="AAPL 股票计划", asset_class=RESTRICTED, member=member, usd=q * aq.close,
                                  day_pnl=q * per_share_pnl, liquidity=VESTING, unlock=v["expected_date"],
                                  note="预计归属", underlying="AAPL"))
            if left > 0:
                items.append(Item(key="aapl:unvested", name=f"AAPL 未归属 {left.normalize():f} 股",
                                  account="AAPL 股票计划", asset_class=RESTRICTED, member=member, usd=left * aq.close,
                                  day_pnl=left * per_share_pnl, liquidity=VESTING,
                                  note="归属日期未录入" + ("，或已过预计归属日但持仓还没更新" if past else ""),
                                  underlying="AAPL"))

    # EB-5：按本金计，锁定到预计回款日
    e = eb5_status(conn, today)
    if e and not e["returned"]:
        items.append(Item(key="eb5", name="EB-5 投资本金", account="EB-5", asset_class=ALTERNATIVE,
                          member=member_of(own, "eb5"), usd=e["principal"], day_pnl=ZERO, liquidity=LOCKED,
                          unlock=e["expected_return_date"].isoformat(), note="按本金，不随市场变动",
                          underlying="EB-5"))

    liabilities = rows(conn, "SELECT * FROM liabilities ORDER BY id")
    funds = parse_fund_tickers(get_setting(conn, "fund_tickers", ""))
    return Wealth(items, liabilities, fx, sorted(set(missing)), max(held_dates) if held_dates else None,
                  intraday=intraday, today=today, transfers=transfers, funds=funds)


# ---------- 每日快照 ----------

def _jsonable(x):
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, (set, frozenset)):
        return sorted(x)
    return x


def in_transit(w: Wealth) -> list[dict]:
    """已计入现金、但入账日在快照日期之后的外部转账（例如周末记下、下周一才入账的转入）。

    快照的净资产包含这些钱；对账单算「已记录的净转入」时据此调整，避免把它们算成市值变动。
    """
    return [{"account": t["account"], "settle_date": t["settle_date"], "amount": str(t["amount"]), "note": t["note"] or ""}
            for t in w.transfers
            if w.quote_date and t["settle_date"] > w.quote_date and (t["note"] or "") not in INTERNAL_TRANSFER_NOTES]


def snapshot_detail(w: Wealth) -> dict:
    return {
        "by_class": {g["name"]: str(g["usd"]) for g in w.group("asset_class")},
        "by_member": {g["name"]: str(g["usd"]) for g in w.group("member")},
        "by_account": {g["name"]: str(g["usd"]) for g in w.group("account")},
        "items": [{k: _jsonable(v) for k, v in asdict(i).items()} for i in w.items],
        "liabilities": [{"name": x["name"], "member": x.get("member"), "amount": str(D(x["amount"]))}
                        for x in w.liabilities],
        "in_transit": in_transit(w),
    }


def snapshot_skip_reason(conn: sqlite3.Connection, w: Wealth, now: datetime | None = None) -> str | None:
    """record_snapshot 这次不写的原因（一句话，写进日志用）；可以写时返回 None。"""
    today = (w.today or (now or datetime.now()).date()).isoformat()
    if w.missing:
        return "缺行情：" + "、".join(w.missing)
    if w.fx is None:
        return "缺 USD/CNY 汇率"
    if not w.quote_date:
        return "没有持仓行情"
    if w.intraday:
        return "持仓里有盘中价，不是收盘价"
    if w.quote_date > today:
        return f"行情日期 {w.quote_date} 在今天之后"
    newest = one(conn, "SELECT MAX(snap_date) AS d FROM nw_snapshots WHERE snap_date <= ?", (today,))
    if newest and newest["d"] and w.quote_date < newest["d"]:
        return f"行情日期 {w.quote_date} 比已有的最新快照 {newest['d']} 还旧"
    return None


def record_snapshot(conn: sqlite3.Connection, w: Wealth, now: datetime | None = None) -> str | None:
    """以持仓行情日期为键记一条快照（同一天重复运行就覆盖成最新值）。返回写入的日期；不写时返回 None。

    不写的情况：
    - 缺任何一只持仓的行情，或者没有汇率；
    - 持仓里有盘中价：对账单把快照当收盘价用，宁可这天没有记录（收盘后刷新行情就会记上）；
    - 行情日期在今天之后（手动行情的日期填错了）；
    - 行情日期比已有的最新快照还旧（离线或缓存过期时，不能用旧价格加新持仓去覆盖之前的记录）。
    """
    if snapshot_skip_reason(conn, w, now):
        return None
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    with conn:
        conn.execute(
            "INSERT INTO nw_snapshots(snap_date, net_worth, assets, liabilities, invested, cash, day_pnl, fx, detail,"
            " updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(snap_date) DO UPDATE SET net_worth=excluded.net_worth,"
            " assets=excluded.assets, liabilities=excluded.liabilities, invested=excluded.invested, cash=excluded.cash,"
            " day_pnl=excluded.day_pnl, fx=excluded.fx, detail=excluded.detail, updated_at=excluded.updated_at",
            (w.quote_date, str(w.net_worth), str(w.assets), str(w.liabilities_total), str(w.invested), str(w.cash),
             str(w.day_pnl), str(w.fx), json.dumps(snapshot_detail(w), ensure_ascii=False), stamp))
    return w.quote_date


def load_snapshots(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM nw_snapshots WHERE 1=1", []
    if start:
        sql += " AND snap_date >= ?"
        args.append(start)
    if end:
        sql += " AND snap_date <= ?"
        args.append(end)
    out = []
    for r in rows(conn, sql + " ORDER BY snap_date", tuple(args)):
        out.append({**r, **{k: D(r[k]) for k in ("net_worth", "assets", "liabilities", "invested", "cash", "day_pnl")},
                    "fx": D(r["fx"]) if r["fx"] else None, "detail": json.loads(r["detail"])})
    return out
