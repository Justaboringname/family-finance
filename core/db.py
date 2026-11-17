"""SQLite 存储。所有金额、股数以 TEXT 存，读出后转 Decimal。

数据库默认在 ./data/finance.db；环境变量 FAMILY_FINANCE_DB 可覆盖（测试用）。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS lots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL,
  ticker TEXT NOT NULL,
  open_date TEXT NOT NULL,
  quantity TEXT NOT NULL,
  cost_per_share TEXT NOT NULL,
  cost_basis TEXT NOT NULL,
  source TEXT,
  as_of TEXT
);

CREATE TABLE IF NOT EXISTS sales(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL,
  ticker TEXT NOT NULL,
  sale_date TEXT NOT NULL,
  price TEXT NOT NULL,
  fee TEXT NOT NULL DEFAULT '0',
  note TEXT
);

CREATE TABLE IF NOT EXISTS sale_lots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
  open_date TEXT NOT NULL,
  quantity TEXT NOT NULL,
  cost_basis TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL,
  as_of TEXT NOT NULL,
  amount TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS cash_transfers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL,
  initiated TEXT NOT NULL,
  settle_date TEXT NOT NULL,
  available_date TEXT,
  amount TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS cash_plans(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  amount TEXT NOT NULL,
  expected TEXT,
  note TEXT,
  milestones TEXT
);

CREATE TABLE IF NOT EXISTS eb5(
  id INTEGER PRIMARY KEY CHECK (id = 1),
  principal TEXT NOT NULL,
  expected_return_date TEXT NOT NULL,
  returned INTEGER NOT NULL DEFAULT 0,
  returned_date TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS aapl_holdings(
  id INTEGER PRIMARY KEY CHECK (id = 1),
  espp_shares TEXT NOT NULL,
  rs_vested_shares TEXT NOT NULL,
  rs_unvested_shares TEXT NOT NULL,
  ref_price TEXT,
  ref_date TEXT,
  ref_total_value TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS aapl_lots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lot_type TEXT NOT NULL CHECK (lot_type IN ('ESPP', 'RS')),
  event_date TEXT NOT NULL,
  quantity TEXT NOT NULL,
  fmv TEXT NOT NULL,
  purchase_price TEXT,
  withheld_cny TEXT,
  fx_rate TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS aapl_vest_schedule(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  expected_date TEXT NOT NULL,
  quantity TEXT NOT NULL,
  expected_fmv TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS aapl_sales(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_date TEXT NOT NULL,
  lot_id INTEGER REFERENCES aapl_lots(id),
  quantity TEXT NOT NULL,
  price TEXT NOT NULL,
  fee TEXT NOT NULL DEFAULT '0',
  fx_rate TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS aapl_dividends(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pay_date TEXT NOT NULL,
  gross_usd TEXT NOT NULL,
  us_withheld_usd TEXT NOT NULL DEFAULT '0',
  fx_rate TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS travel(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_date TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('entry', 'exit')),
  status TEXT,
  planned INTEGER NOT NULL DEFAULT 0,
  note TEXT
);

CREATE TABLE IF NOT EXISTS tax_years(
  year INTEGER PRIMARY KEY,
  status_override TEXT,
  residency_start_override TEXT,
  us_days_override INTEGER,
  fyc_elect INTEGER NOT NULL DEFAULT 0,
  itemized TEXT,
  ny_status TEXT NOT NULL DEFAULT 'off',
  ny_start TEXT,
  nyc INTEGER NOT NULL DEFAULT 0,
  note TEXT
);

CREATE TABLE IF NOT EXISTS other_income(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  year INTEGER NOT NULL,
  kind TEXT NOT NULL,
  amount TEXT NOT NULL,
  period TEXT NOT NULL DEFAULT 'resident',
  note TEXT
);

CREATE TABLE IF NOT EXISTS carryover_in(
  year INTEGER PRIMARY KEY,
  st TEXT NOT NULL DEFAULT '0',
  lt TEXT NOT NULL DEFAULT '0',
  note TEXT
);

CREATE TABLE IF NOT EXISTS price_cache(
  ticker TEXT PRIMARY KEY,
  quote_date TEXT,
  close TEXT,
  prev_close TEXT,
  intraday INTEGER NOT NULL DEFAULT 0,
  fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS price_override(
  ticker TEXT PRIMARY KEY,
  close TEXT NOT NULL,
  prev_close TEXT,
  as_of TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  note TEXT
);

CREATE TABLE IF NOT EXISTS cn_fx_rates(
  rate_date TEXT PRIMARY KEY,
  rate TEXT NOT NULL,
  note TEXT
);

-- 负债（房贷、保证金借款、信用额度等）；没有时为空，首页照样显示「负债 $0.00」
CREATE TABLE IF NOT EXISTS liabilities(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  member TEXT,
  amount TEXT NOT NULL,
  note TEXT
);

-- 每个交易日一条净资产快照（以持仓行情日期为准），给走势和月度对账单用
CREATE TABLE IF NOT EXISTS nw_snapshots(
  snap_date TEXT PRIMARY KEY,
  net_worth TEXT NOT NULL,
  assets TEXT NOT NULL,
  liabilities TEXT NOT NULL,
  invested TEXT NOT NULL,
  cash TEXT NOT NULL,
  day_pnl TEXT NOT NULL,
  fx TEXT,
  detail TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- 资金日历里自己加的提醒
CREATE TABLE IF NOT EXISTS calendar_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_date TEXT NOT NULL,
  title TEXT NOT NULL,
  member TEXT,
  amount TEXT,
  note TEXT,
  done INTEGER NOT NULL DEFAULT 0
);
"""


def db_path() -> Path:
    env = os.environ.get("FAMILY_FINANCE_DB")
    return Path(env) if env else DATA_DIR / "finance.db"


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


# 旧库补列：(表, 列, 类型)
MIGRATIONS = [
    ("sale_lots", "lot_id", "INTEGER"),
    ("sale_lots", "cost_per_share", "TEXT"),
    ("sale_lots", "lot_source", "TEXT"),
    ("sale_lots", "lot_as_of", "TEXT"),
    ("aapl_sales", "order_no", "TEXT"),
    ("aapl_sales", "holdings_deducted", "TEXT"),   # 记录时实际从 AAPL 股数里扣掉的股数
    ("cash_plans", "milestones", "TEXT"),          # 关键节点，见 core.agenda.parse_milestones（2026-11-11）
]


def migrate(conn: sqlite3.Connection) -> None:
    with conn:
        for table, col, typ in MIGRATIONS:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params=()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    r = one(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    return r["value"] if r else default


def set_setting(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    with conn:
        if value is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
