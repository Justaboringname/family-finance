"""需求里列出的 8 个测试用例。"""
import json
from datetime import date
from decimal import Decimal

import pytest

from core.cn_tax import comprehensive_bracket, equity_incentive_tax, IncentiveItem, sell_by_amount
from core.db import DATA_DIR
from core.money import q2
from core.params import load_params
from core.residency import TravelEvent, compute_residency
from core.us_tax import GainItem, TaxProfile, compute_tax

P26 = load_params(2026)
D = Decimal


def gain(amount, long_term, on=date(2026, 11, 20)):
    return GainItem(on, "X", D(1), D(amount), D(0), long_term)


def test_1_dual_status_long_term_30k_is_zero():
    prof = TaxProfile(2026, "dual_status", residency_start=date(2026, 9, 15), us_days=292)
    r = compute_tax(prof, [gain(30000, True)], [], P26)
    assert r.deduction == 0
    assert r.federal == D("0.00")


def test_2_dual_status_short_term_30k():
    prof = TaxProfile(2026, "dual_status", residency_start=date(2026, 9, 15), us_days=292)
    r = compute_tax(prof, [gain(30000, False)], [], P26)
    assert r.federal == D("3352.00")


def test_3_nonresident_183_days_30pct():
    prof = TaxProfile(2026, "nonresident", us_days=200)
    r = compute_tax(prof, [gain(30000, True)], [], P26)
    assert r.federal == D("9000.00")


def test_4_resident_long_term_100k():
    prof = TaxProfile(2026, "resident")
    r = compute_tax(prof, [gain(100000, True)], [], P26)
    assert r.taxable_income == D("83900")
    assert r.federal == D("5167.50")


def test_5_residency_f1_then_parole():
    events = [
        TravelEvent(date(2022, 8, 25), "entry", "F-1"),
        TravelEvent(date(2026, 6, 28), "exit", "F-1"),
        TravelEvent(date(2026, 9, 15), "entry", "parole"),
    ]
    res = compute_residency(events, [2022, 2023, 2024, 2025, 2026, 2027], today=date(2026, 10, 15))
    y26 = res[2026]
    assert [y for y in range(2022, 2027) if res[y].exempt_year] == [2022, 2023, 2024, 2025, 2026]
    assert y26.counted_days == 108
    assert y26.default_status == "nonresident"
    assert res[2027].spt_met
    assert y26.fyc.eligible
    assert y26.fyc.start == date(2026, 9, 15)


def test_6_cn_separate_equity_incentive():
    _, _, tax = comprehensive_bracket(D(6696000), P26)
    assert q2(tax) == D("2831280.00")
    # 走完整函数：一笔 RS 归属，收入正好 6,696,000
    r = equity_incentive_tax([IncentiveItem("RS", date(2026, 3, 1), D(6696), D(1000))], 2026, P26)
    assert q2(r["tax"]) == D("2831280.00")
    assert r["method"] == "单独计税"


def test_7_cn_sale():
    r = sell_by_amount(D(1000000), D("342.96"), D(200), P26)
    assert q2(r["tax"]) == D("83368.32")


def test_8_initial_import_totals(tmp_path):
    expected_path = DATA_DIR / "expected_totals.json"
    if not expected_path.exists() or not (DATA_DIR / "initial_lots.csv").exists():
        pytest.skip("没有 ./data 初始数据")
    from core.db import connect
    from core.portfolio import load_lots, totals_by_ticker
    from core.seed import seed_if_empty

    exp = json.loads(expected_path.read_text(encoding="utf-8"))
    c = connect(tmp_path / "seed.db")
    seed_if_empty(c, DATA_DIR)
    tot = totals_by_ticker(load_lots(c))
    assert set(tot) == set(exp["tickers"])
    for t, e in exp["tickers"].items():
        assert tot[t]["quantity"] == D(e["quantity"]), t
        assert q2(tot[t]["cost_basis"]) == D(e["cost_basis"]), t
    assert q2(sum(x["cost_basis"] for x in tot.values())) == D(exp["total_cost_basis"])
