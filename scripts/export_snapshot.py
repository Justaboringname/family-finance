"""导出手机 app 用的只读快照（JSON）。不联网：用网站已经缓存的行情。

    .venv/bin/python scripts/export_snapshot.py                 # 写到 ios-app/Generated/snapshot.json（装机时打包进 app）
    .venv/bin/python scripts/export_snapshot.py --downloads     # 另存一份到 ~/Downloads，用隔空投送发到手机后在 app 里导入

数据不完整（缺行情、缺汇率）时不导出，退出码 2。生成的文件含个人数据，只放在本机，已 gitignore。
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from core.db import connect  # noqa: E402
from core.market import ET, FX_TICKER, load_cache  # noqa: E402
from core.mobile import IncompleteData, build_mobile_snapshot  # noqa: E402
from core.seed import seed_if_empty  # noqa: E402
from core.wealth import build_wealth  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="导出手机 app 用的只读快照")
    p.add_argument("--out", default=str(ROOT / "ios-app" / "Generated" / "snapshot.json"))
    p.add_argument("--downloads", action="store_true", help="另存一份到 ~/Downloads/家庭资产快照-日期.json")
    a = p.parse_args(argv)
    import ui_common   # 资金日历的事项要按各年报税身份算，那部分代码在网站这边；只借用函数，不起网站

    conn = connect()
    seed_if_empty(conn)
    now = datetime.now(ET)
    quotes = load_cache(conn)
    fxq = quotes.get(FX_TICKER)
    w = build_wealth(conn, quotes, fxq.close if fxq else None, now.date())
    try:
        snap = build_mobile_snapshot(conn, w, quotes, now.date(), now, ui_common.family_events(conn, now.date(), quotes))
    except IncompleteData as e:
        print(f"没有导出：{e}。先在网站上刷新行情再试。", file=sys.stderr)
        return 2
    text = json.dumps(snap, ensure_ascii=False, indent=1)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"已导出 {snap['as_of_label']} 的快照：{out}")
    if a.downloads:
        dl = Path.home() / "Downloads" / f"家庭资产快照-{snap['as_of']}.json"
        dl.write_text(text, encoding="utf-8")
        print(f"另存一份：{dl}（隔空投送到手机，在 app 里点「导入快照」）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
