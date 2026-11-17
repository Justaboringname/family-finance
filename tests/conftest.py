import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """测试一律不联网：任何 yfinance 调用都会失败。"""
    import core.market as market

    def boom(*a, **k):
        raise RuntimeError("测试中禁止联网")

    monkeypatch.setattr(market, "fetch_yfinance", boom)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from core.db import connect

    monkeypatch.setenv("FAMILY_FINANCE_DB", str(tmp_path / "t.db"))
    c = connect(tmp_path / "t.db")
    yield c
    c.close()
