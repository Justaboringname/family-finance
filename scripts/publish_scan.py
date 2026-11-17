"""发布前的第二道检查：拿本机真实数据库里的值，反查要发布的文件里有没有照搬进去的真实记录。

    python3 scripts/publish_scan.py <导出目录> [--db data/finance.db] [--ignore data/publish_scan_ignore.txt]

scripts/publish.sh 会调用它。data/private_terms.txt 那份清单靠人想到才写得进去；这里直接从库里取，
新记的交易、行程不用再手工登记。查这几类（只读，不改任何东西）：
- 出入境日期、EB-5 预计回款日：出现在任何地方都算，`2026-01-02`、`date(2026, 1, 2)`、`01/02/2026` 三种写法都查；
- 持仓批次、卖出：同一行里有它的代码，又有它的买卖日期或成本价（导入日期 as_of 不算）；
- 转账、资金计划：同一行里有同一条记录的两个不同日期；
- 金额、股数、成本、每天的净资产快照（含明细）：带小数且有效数字 4 位以上、或整数有效数字 5 位以上的，出现在任何地方都算，
  带千分位逗号的写法也算，十万以上的只比整数部分（行情价、汇率不查，那不是个人数据）；
- 备注、名称、标题这类文字（6 个字符以上）。
碰巧撞上的（比如备注正好是界面上的一个通用标签），把那个值写进 --ignore 文件，一行一个。
退出码：0 没查到（或本机没有数据库），2 查到了。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
NUM = re.compile(r"^-?\d+(\.\d+)?$")
SKIP_SUFFIX = (".svg", ".png", ".jpg", ".icns", ".pdf", ".db")
TEXT_COLS = {"note", "label", "title", "name", "account"}
ANYWHERE_DATES = {("travel", "event_date"), ("eb5", "expected_return_date")}
NOT_PERSONAL = {("aapl_holdings", "ref_price")}                # 行情价
SKIP_COLS = {"id", "year", "as_of", "created_at", "updated_at", "fx"}   # 编号、年份、导入和记录的时间、汇率
ROW_TABLES = {"lots": "ticker", "sales": "ticker", "sale_lots": "ticker", "aapl_sales": None}


def date_patterns(iso: str) -> list[re.Pattern]:
    y, m, d = ISO.match(iso).groups()
    return [re.compile(re.escape(f"{y}-{m}-{d}")),
            re.compile(rf"\(\s*{y}\s*,\s*0?{int(m)}\s*,\s*0?{int(d)}\s*[,)]"),
            re.compile(rf"(?<!\d)0?{int(m)}/0?{int(d)}/{y}")]


def number_pattern(s: str) -> re.Pattern | None:
    """带小数的要有效数字 4 位以上、整数要 5 位以上才查（100、20000、2026 这种到处都是）。
    带不带千分位逗号都算；十万以上的数只要整数部分对上就算（改掉几分钱不算脱敏）。"""
    z = s.lstrip("-")
    z = z.rstrip("0").rstrip(".") if "." in z else z
    if len(z.replace(".", "").strip("0")) < (4 if "." in z else 5):
        return None
    whole, _, frac = z.partition(".")
    head = len(whole) % 3 or 3
    groups = [whole[:head]] + [whole[i:i + 3] for i in range(head, len(whole), 3)]
    body = ",?".join(groups)
    if len(whole) >= 6:
        return re.compile(r"(?<![\d.,])" + body + r"(?![\d])(?!,\d)")
    return re.compile(r"(?<![\d.,])" + body + (r"\." + frac if frac else "") + r"0*(?![\d])(?!\.\d)")


def load_checks(db: Path, ignore: set[str]):
    """→ (任何地方都算的 [(来源, 正则)]，同一行要凑齐的 [(来源, [必须有的正则], [至少有一个的正则], 至少几个)])。"""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    anywhere, combos = [], []
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for t in tables:
        if t in ("settings", "sqlite_sequence") or "cache" in t:
            continue
        for row in conn.execute(f'SELECT * FROM "{t}"'):
            vals = {k: str(row[k]).strip() for k in row.keys() if row[k] is not None and str(row[k]).strip()}
            dates, extra = [], []
            for k, v in vals.items():
                if v in ignore or (t, k) in NOT_PERSONAL or k in SKIP_COLS:
                    continue
                if t.startswith("nw_") and not NUM.match(v):
                    # 净资产快照：日期只是哪天记的，不查；明细里的每个数都查
                    for tok in dict.fromkeys(re.findall(r"-?\d+\.\d+|\d{6,}", v)) if k == "detail" else ():
                        p = number_pattern(tok)
                        if p:
                            anywhere.append((f"{t}.{k}", p))
                    continue
                found = ["-".join(m) for m in ISO.findall(v)]
                if found and (ISO.fullmatch(v[:10]) or k == "milestones"):
                    for iso in dict.fromkeys(found):
                        pats = date_patterns(iso)
                        dates.append((iso, pats))
                        if (t, k) in ANYWHERE_DATES:
                            anywhere += [(f"{t}.{k}", p) for p in pats]
                elif NUM.match(v):
                    p = number_pattern(v)
                    if p:
                        anywhere.append((f"{t}.{k}", p))
                        extra.append(p)
                elif k in TEXT_COLS and len(v) >= 6:
                    anywhere.append((f"{t}.{k}", re.compile(re.escape(v))))
            if t in ROW_TABLES and vals.get(ROW_TABLES[t] or ""):
                tick = re.compile(r"(?<![A-Za-z])" + re.escape(vals[ROW_TABLES[t]]) + r"(?![A-Za-z])")
                combos.append((t, [tick], [p for _, ps in dates for p in ps] + extra, 1))
            uniq = {iso: ps for iso, ps in dates}
            if t in ("cash_transfers", "cash_plans") and len(uniq) >= 2:
                combos.append((t, [], [re.compile("|".join(p.pattern for p in ps)) for ps in uniq.values()], 2))
    conn.close()
    return anywhere, combos


def scan(root: Path, db: Path, ignore: set[str]) -> list[str]:
    anywhere, combos = load_checks(db, ignore)
    hits = []
    for f in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts):
        if f.suffix.lower() in SKIP_SUFFIX:
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(lines, 1):
            why = next((src for src, p in anywhere if p.search(line)), None)
            if not why:
                why = next((src for src, must, some, n in combos
                            if all(p.search(line) for p in must) and sum(bool(p.search(line)) for p in some) >= n), None)
            if why:
                hits.append(f"{f.relative_to(root)}:{i} [{why}] {line.strip()[:110]}")
    return hits


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="拿真实数据库反查要发布的文件")
    ap.add_argument("root")
    ap.add_argument("--db", default=str(here / "data" / "finance.db"))
    ap.add_argument("--ignore", default=str(here / "data" / "publish_scan_ignore.txt"))
    a = ap.parse_args(argv)
    if not Path(a.db).exists():
        print(f"没有 {a.db}，跳过和真实数据的比对。")
        return 0
    ig = Path(a.ignore)
    ignore = {s.strip() for s in ig.read_text(encoding="utf-8").splitlines() if s.strip() and not s.startswith("#")} if ig.exists() else set()
    hits = scan(Path(a.root), Path(a.db), ignore)
    if hits:
        print("要发布的文件里有和真实数据库对得上的内容：", file=sys.stderr)
        print("\n".join(hits), file=sys.stderr)
        return 2
    print("和真实数据库比对：没有对得上的。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
