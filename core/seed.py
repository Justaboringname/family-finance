"""首次运行时把 ./data/ 里的初始数据写进数据库。代码里不含任何个人数字。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .db import DATA_DIR, get_setting, one, set_setting
from .importer import load_simple_csv


def seed_if_empty(conn: sqlite3.Connection, data_dir: Path = DATA_DIR) -> list[str]:
    if get_setting(conn, "seeded") == "1":
        return []
    msgs = []
    lots_csv = data_dir / "initial_lots.csv"
    init_json = data_dir / "initial_data.json"
    init = json.loads(init_json.read_text(encoding="utf-8")) if init_json.exists() else {}

    with conn:
        if lots_csv.exists() and not one(conn, "SELECT id FROM lots LIMIT 1"):
            as_of = init.get("lots_as_of")
            for r in load_simple_csv(lots_csv):
                conn.execute(
                    "INSERT INTO lots(account, ticker, open_date, quantity, cost_per_share, cost_basis, source, as_of)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (r["account"], r["ticker"], r["open_date"], str(r["quantity"]), str(r["cost_per_share"]),
                     str(r["cost_basis"]), "initial", as_of),
                )
            msgs.append("已写入初始批次")
        for s in init.get("cash_snapshots", []):
            conn.execute("INSERT INTO cash_snapshots(account, as_of, amount, note) VALUES(?,?,?,?)",
                         (s["account"], s["as_of"], s["amount"], s.get("note")))
        for t in init.get("cash_transfers", []):
            conn.execute(
                "INSERT INTO cash_transfers(account, initiated, settle_date, available_date, amount, note)"
                " VALUES(?,?,?,?,?,?)",
                (t["account"], t["initiated"], t["settle_date"], t.get("available_date"), t["amount"], t.get("note")),
            )
        for p in init.get("cash_plans", []):
            conn.execute("INSERT INTO cash_plans(label, amount, expected, note) VALUES(?,?,?,?)",
                         (p["label"], p["amount"], p.get("expected"), p.get("note")))
        if "eb5" in init:
            e = init["eb5"]
            conn.execute(
                "INSERT OR REPLACE INTO eb5(id, principal, expected_return_date, returned, note) VALUES(1,?,?,0,?)",
                (e["principal"], e["expected_return_date"], e.get("note")),
            )
        if "aapl_holdings" in init:
            a = init["aapl_holdings"]
            conn.execute(
                "INSERT OR REPLACE INTO aapl_holdings(id, espp_shares, rs_vested_shares, rs_unvested_shares,"
                " ref_price, ref_date, ref_total_value, note) VALUES(1,?,?,?,?,?,?,?)",
                (a["espp_shares"], a["rs_vested_shares"], a["rs_unvested_shares"], a.get("ref_price"),
                 a.get("ref_date"), a.get("ref_total_value"), a.get("note")),
            )
        for t in init.get("travel", []):
            conn.execute(
                "INSERT INTO travel(event_date, direction, status, planned, note) VALUES(?,?,?,?,?)",
                (t["event_date"], t["direction"], t.get("status"), int(t.get("planned", 0)), t.get("note")),
            )
    defaults = {
        "offline": "0",
        "cn_loss_rule": "annual_netting",
        "cn_equity_filed": "1",
        "cn_listed_ownership_ok": "1",
        "wash_equivalents": "GOOG=GOOGL",
    }
    for k, v in defaults.items():
        if get_setting(conn, k) is None:
            set_setting(conn, k, v)
    set_setting(conn, "seeded", "1")
    return msgs
