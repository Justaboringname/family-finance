"""生成示例数据（一个虚构家庭，见 core/demo.py）。不联网，也不读 ./data 里的真实数据。

    .venv/bin/python scripts/make_demo.py --db data/demo/finance.db            # 试用网站：./run.sh --demo 会自动跑这一步
    .venv/bin/python scripts/make_demo.py --snapshot ios-app/Generated/demo-snapshot.json   # 手机 app 的「展示模式」
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.demo import build_demo_db, demo_snapshot  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="生成示例数据（虚构家庭）")
    p.add_argument("--db", help="在这里新建示例数据库（已有就覆盖）")
    p.add_argument("--snapshot", help="把手机 app 展示模式用的快照写到这里")
    a = p.parse_args(argv)
    if not a.db and not a.snapshot:
        p.error("至少给 --db 或 --snapshot 之一")
    if a.db:
        db = Path(a.db).resolve()
        if db == (ROOT / "data" / "finance.db").resolve():
            p.error("不能把示例数据写到 data/finance.db，那是真实数据")
        build_demo_db(db).close()
        print(f"示例数据库：{db}")
    if a.snapshot:
        with tempfile.TemporaryDirectory() as tmp:
            snap = demo_snapshot(Path(tmp) / "demo.db")
        out = Path(a.snapshot)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"示例快照（{snap['as_of_label']}，净资产 {snap['certificates'][0]['net']['usd']}）：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
