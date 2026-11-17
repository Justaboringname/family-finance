"""补充测试：参数来源、持有期边界、wash sale、非居民亏损、NIIT、FYC 窗口、CSV 解析等。"""
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from core.importer import apply_import, clean_number, parse_lot_details
from core.optimizer import allocate, zero_band_headroom
from core.params import PARAMS_DIR, available_years, load_params, validate
from core.portfolio import Lot, first_long_term_date, is_long_term, load_lots, load_realized, record_sale
from core.residency import TravelEvent, compute_residency
from core.us_tax import GainItem, IncomeItem, TaxProfile, capital_loss_carryover, compute_tax, ny_state_tax
from core.wash_sale import Acquisition, LossLeg, detect, parse_equivalents

D = Decimal
P26 = load_params(2026)
FIX = Path(__file__).parent / "fixtures"


def g(amount, lt, on=date(2026, 11, 20)):
    return GainItem(on, "X", D(1), D(amount), D(0), lt)


@pytest.mark.parametrize("year", available_years())
def test_every_param_has_source(year):
    data = json.loads((PARAMS_DIR / f"{year}.json").read_text(encoding="utf-8"))
    assert validate(data) == []


def test_validate_catches_missing_source():
    assert validate({"a": {"value": 1}}) == ["a 缺少 source 链接"]
    assert validate({"a": 3}) == ["a 不是 {value, source} 参数"]


def test_2026_brackets_match_rev_proc():
    br = P26.brackets("us_federal.ordinary_brackets_single")
    assert [b[0] for b in br] == [D(12400), D(50400), D(105700), D(201775), D(256225), D(640600), None]


@pytest.mark.parametrize("bought,first_lt", [
    (date(2025, 12, 2), date(2026, 12, 3)),
    (date(2027, 2, 28), date(2028, 3, 1)),
    (date(2028, 2, 29), date(2029, 3, 1)),
    (date(2026, 1, 15), date(2027, 1, 16)),
])
def test_long_term_date(bought, first_lt):
    assert first_long_term_date(bought) == first_lt
    assert not is_long_term(bought, first_lt - timedelta(days=1))
    assert is_long_term(bought, first_lt)


def test_nonresident_losses_do_not_carry_or_offset_ordinary():
    prof = TaxProfile(2026, "nonresident", us_days=250)
    r = compute_tax(prof, [g(10000, True), g(-25000, False)], [], P26)
    assert r.federal == 0
    assert r.carry_out_st == 0 and r.carry_out_lt == 0


def test_nonresident_under_183_days_is_zero():
    prof = TaxProfile(2026, "nonresident", us_days=150)
    assert compute_tax(prof, [g(50000, False)], [], P26).federal == 0


def test_dual_status_nonresident_portion_taxed_30pct_when_full_year_183():
    prof = TaxProfile(2026, "dual_status", residency_start=date(2026, 9, 15), us_days=292)
    r = compute_tax(prof, [g(10000, True, date(2026, 5, 1)), g(30000, True)], [], P26)
    assert r.nra_tax == D("3000")
    assert r.regular_tax == 0


def test_resident_loss_carryover_keeps_character_and_3000_limit():
    prof = TaxProfile(2026, "resident")
    wages = [IncomeItem("wages", D(80000))]
    r = compute_tax(prof, [g(-10000, True), g(2000, False)], wages, P26)
    assert r.loss_deduction == D(3000)
    assert r.carry_out_lt == D(5000) and r.carry_out_st == 0


def test_carryover_worksheet_no_income_keeps_whole_loss():
    st, lt = capital_loss_carryover(D(0), D(-10000), D(3000), D(-19100))
    assert (st, lt) == (D(0), D(10000))


def test_niit_dual_status_only_resident_portion():
    prof = TaxProfile(2026, "dual_status", residency_start=date(2026, 9, 15), us_days=292)
    r = compute_tax(prof, [g(300000, True)], [], P26)
    assert r.niit == D("3800.000")  # (300,000 − 200,000) × 3.8%
    r2 = compute_tax(prof, [g(300000, True, date(2026, 3, 1))], [], P26)
    assert r2.niit == 0 and r2.nra_tax == D(90000)


def test_qualified_div_and_wages_stack_ltcg():
    prof = TaxProfile(2027, "resident")
    p27 = load_params(2027)
    r = compute_tax(prof, [g(20000, True)], [IncomeItem("wages", D(60000))], p27)
    # 应税 63,900；普通 43,900；长期 20,000 中 5,550 在 0% 档，14,450 在 15%
    assert r.taxable_income == D(63900)
    assert r.ltcg_bands == [(D("0.0"), D(5550)), (D("0.15"), D(14450))]


def test_fyc_grace_days():
    events = [
        TravelEvent(date(2022, 8, 25), "entry", "F-1"),
        TravelEvent(date(2026, 6, 28), "exit", "F-1"),
        TravelEvent(date(2026, 11, 1), "entry", "parole"),
        TravelEvent(date(2026, 12, 20), "exit", "parole"),
        TravelEvent(date(2027, 1, 10), "entry", "parole"),
    ]
    res = compute_residency(events, [2026, 2027], today=date(2026, 10, 15))
    f = res[2026].fyc
    # 测试期 11/1–12/31 共 61 天，在美 50 天，缓冲 5 天 → 55/61 = 90%
    assert f.eligible and f.start == date(2026, 11, 1)
    assert res[2026].us_days == 179 + 50


def test_fyc_fails_without_31_days():
    events = [
        TravelEvent(date(2022, 8, 25), "entry", "F-1"),
        TravelEvent(date(2026, 6, 28), "exit", "F-1"),
        TravelEvent(date(2026, 12, 10), "entry", "parole"),
    ]
    res = compute_residency(events, [2026, 2027], today=date(2026, 10, 15))
    assert not res[2026].fyc.eligible


def test_fyc_elected_makes_dual_status_and_2027_full_year():
    events = [
        TravelEvent(date(2022, 8, 25), "entry", "F-1"),
        TravelEvent(date(2026, 6, 28), "exit", "F-1"),
        TravelEvent(date(2026, 9, 15), "entry", "parole"),
        TravelEvent(date(2026, 12, 17), "exit", "parole"),
        TravelEvent(date(2027, 1, 16), "entry", "parole"),
    ]
    no = compute_residency(events, [2026, 2027], today=date(2026, 10, 15))
    assert no[2027].status == "dual_status" and no[2027].residency_start == date(2027, 1, 16)
    yes = compute_residency(events, [2026, 2027], today=date(2026, 10, 15), fyc_elect={2026: True})
    assert yes[2026].status == "dual_status" and yes[2026].residency_start == date(2026, 9, 15)
    assert yes[2027].status == "resident"


def test_sixth_f1_year_counts():
    events = [TravelEvent(date(2020, 9, 1), "entry", "F-1")]
    res = compute_residency(events, [2024, 2025], today=date(2026, 10, 15))
    # 2020–2024 用满 5 个豁免年，2025 年的 F-1 天数开始计入
    assert res[2024].exempt_year and res[2024].counted_days == 0
    assert not res[2025].exempt_year and res[2025].exempt_years_used == 5
    assert res[2025].counted_days == 365 and res[2025].spt_met


def test_wash_sale_detects_purchase_within_window():
    acq = [Acquisition(date(2026, 7, 22), "ACME", D(100), ("A", "ACME", date(2026, 7, 22)))]
    leg = LossLeg(date(2026, 8, 10), "ACME", D(50), D(1000), ("A", "ACME", date(2026, 6, 25)))
    hits = detect([leg], acq, 30)
    assert len(hits) == 1 and hits[0].disallowed == D(1000)
    far = LossLeg(date(2026, 9, 10), "ACME", D(50), D(1000), ("A", "ACME", date(2026, 6, 25)))
    assert detect([far], acq, 30) == []


def test_wash_sale_equivalent_tickers_and_own_lot_excluded():
    acq = [Acquisition(date(2026, 3, 1), "GOOGL", D(10), ("A", "GOOGL", date(2026, 3, 1))),
           Acquisition(date(2026, 3, 5), "GOOG", D(10), ("A", "GOOG", date(2026, 3, 5)))]
    leg = LossLeg(date(2026, 3, 19), "GOOG", D(10), D(500), ("A", "GOOG", date(2026, 3, 5)))
    hits = detect([leg], acq, 30, parse_equivalents("GOOG=GOOGL"))
    assert [h.replacement.ticker for h in hits] == ["GOOGL"]


def test_clean_number():
    assert clean_number("$1,234.56") == D("1234.56")
    assert clean_number("-$45.00") == D("-45.00")
    assert clean_number("($12.00)") == D("-12.00")
    assert clean_number("--") is None


def test_parse_and_import_schwab_csv(conn):
    pf = parse_lot_details((FIX / "ABC_lot_details.csv").read_bytes(), "ABC.csv")
    assert pf.ticker == "ABC" and pf.account == "Schwab-123" and pf.as_of == date(2026, 10, 15)
    assert len(pf.lots) == 2 and pf.total_quantity == D(1020) and pf.total_cost == D("5745.00")
    apply_import(conn, [pf])
    apply_import(conn, [pf])  # 重复导入整体覆盖，不会翻倍
    lots = load_lots(conn, ticker="ABC")
    assert len(lots) == 2 and sum(l.quantity for l in lots) == D(1020)


def test_record_sale_reduces_lot_and_realized(conn):
    pf = parse_lot_details((FIX / "ABC_lot_details.csv").read_bytes(), "ABC.csv")
    apply_import(conn, [pf])
    lot = [l for l in load_lots(conn, ticker="ABC") if l.open_date == date(2023, 3, 9)][0]
    record_sale(conn, "Schwab-123", "ABC", date(2026, 10, 20), D(11), D(5), [(lot.id, D(400))])
    left = [l for l in load_lots(conn, ticker="ABC") if l.open_date == date(2023, 3, 9)][0]
    assert left.quantity == D(600) and left.cost_basis == D("3300.00")
    legs = load_realized(conn, 2026)
    assert len(legs) == 1 and legs[0].gain == D(4400) - D(5) - D(2200) and legs[0].long_term


def test_hifo_allocation():
    lots = [Lot(1, "A", "ACME", date(2025, 12, 10), D(100), D(180), D(18000)),
            Lot(2, "A", "ACME", date(2026, 5, 20), D(50), D("175.50"), D("8775")),
            Lot(3, "A", "ACME", date(2026, 7, 22), D(100), D(120), D(12000))]
    a = allocate(lots, D(120), "hifo", D(150), date(2026, 11, 20))
    assert [(l.id, q) for l, q in a] == [(1, D(100)), (2, D(20))]


def test_zero_band_headroom_resident_and_dual():
    res = zero_band_headroom(TaxProfile(2026, "resident"), [], [], P26)
    assert res == D("65550.00")  # 16,100 标准扣除 + 49,450
    dual = zero_band_headroom(TaxProfile(2026, "dual_status", residency_start=date(2026, 9, 15), us_days=292), [], [], P26)
    assert dual == D("49450.00")
    assert zero_band_headroom(TaxProfile(2026, "nonresident", us_days=250), [], [], P26) is None


def test_ny_state_tax_schedule_and_recapture():
    tax, how = ny_state_tax(D(50000), D(42000), P26)
    assert tax == D(586) + (D(42000) - D(13900)) * D("0.054")  # 税表原值：586 + 5.40% × 超出
    mid, how = ny_state_tax(D(132650), D(124650), P26)       # NYAGI 超 107,650 一半 → 回收一半
    sched = D(4191) + (D(124650) - D(80650)) * D("0.059")
    assert mid == sched + (D(124650) * D("0.059") - sched) * D("0.5")
    flat, how = ny_state_tax(D(200000), D(192000), P26)
    assert flat == D(192000) * D("0.059")


def test_nra_183_param_documented():
    ref = P26.ref("us_federal.nra_183_count_exempt_days")
    assert ref.value is True and ref.source.startswith("https://")


def _add_lot(conn, ticker, d, qty, cps, source="manual", as_of=None, account="Schwab-123"):
    q, c = D(qty), D(cps)
    return conn.execute("INSERT INTO lots(account, ticker, open_date, quantity, cost_per_share, cost_basis, source, as_of)"
                        " VALUES(?,?,?,?,?,?,?,?)", (account, ticker, d.isoformat(), str(q), str(c), str(q * c), source,
                                                     as_of)).lastrowid


def test_wash_sale_matches_earliest_acquisition_first(conn):
    from ui_common import realized_items
    x = _add_lot(conn, "ACME", date(2025, 1, 2), 100, 200)
    y = _add_lot(conn, "ACME", date(2025, 2, 3), 100, 200)
    _add_lot(conn, "ACME", date(2026, 3, 5), 100, 190)
    _add_lot(conn, "ACME", date(2026, 4, 3), 100, 180)
    conn.commit()
    record_sale(conn, "Schwab-123", "ACME", date(2026, 3, 31), D(190), D(0), [(x, D(100))])
    record_sale(conn, "Schwab-123", "ACME", date(2026, 5, 1), D(180), D(0), [(y, D(100))])
    items, hits = realized_items(conn, 2026)
    assert [(h.leg.sale_date, h.replacement.acq_date) for h in hits] == [
        (date(2026, 3, 31), date(2026, 3, 5)), (date(2026, 5, 1), date(2026, 4, 3))]
    assert sum(i.wash_disallowed for i in items) == D(3000)


def test_wash_sale_replacement_basis_and_holding_period(conn):
    from ui_common import realized_items
    a = _add_lot(conn, "ACME", date(2025, 1, 2), 100, 200)
    b = _add_lot(conn, "ACME", date(2026, 3, 5), 100, 150)
    conn.commit()
    record_sale(conn, "Schwab-123", "ACME", date(2026, 3, 19), D(150), D(0), [(a, D(100))])
    record_sale(conn, "Schwab-123", "ACME", date(2026, 6, 1), D(160), D(0), [(b, D(100))])
    items, _ = realized_items(conn, 2026)
    r = compute_tax(TaxProfile(2026, "resident"), items, [IncomeItem("wages", D(80000))], P26)
    assert r.federal == D("8110.00")
    assert r.carry_out_lt == D(1000) and r.carry_out_st == 0


def test_wash_sale_broker_adjusted_basis_not_double_counted(conn):
    from ui_common import realized_items
    a = _add_lot(conn, "ACME", date(2025, 1, 2), 100, 200)
    conn.commit()
    record_sale(conn, "Schwab-123", "ACME", date(2026, 3, 19), D(150), D(0), [(a, D(100))])
    # 之后重新导入了 Schwab CSV：替代批次的成本已含 +5,000 调整
    b = _add_lot(conn, "ACME", date(2026, 3, 5), 100, 200, source="schwab_csv", as_of="2026-04-01")
    conn.commit()
    record_sale(conn, "Schwab-123", "ACME", date(2026, 6, 1), D(160), D(0), [(b, D(100))])
    items, _ = realized_items(conn, 2026)
    assert sum(i.gain for i in items) == D(-4000)   # A 的亏损被禁止（0），B 的亏损 −4,000，不重复加成本


def test_delete_sale_restores_original_lot(conn):
    l1 = _add_lot(conn, "ACME", date(2026, 3, 3), 10, 100)
    l2 = _add_lot(conn, "ACME", date(2026, 3, 3), 10, 200)
    conn.commit()
    from core.portfolio import delete_sale
    sid = record_sale(conn, "Schwab-123", "ACME", date(2026, 6, 1), D(180), D(0), [(l2, D(5))])
    delete_sale(conn, sid)
    got = sorted((l.cost_per_share, l.quantity) for l in load_lots(conn, ticker="ACME"))
    assert got == [(D(100), D(10)), (D(200), D(10))]
    sid = record_sale(conn, "Schwab-123", "ACME", date(2026, 6, 1), D(180), D(0), [(l2, D(10))])
    delete_sale(conn, sid)
    got = sorted((l.cost_per_share, l.quantity) for l in load_lots(conn, ticker="ACME"))
    assert got == [(D(100), D(10)), (D(200), D(10))]


def test_leap_year_feb28_not_month_end():
    assert first_long_term_date(date(2028, 2, 28)) == date(2029, 3, 1)
    assert first_long_term_date(date(2024, 2, 28)) == date(2025, 3, 1)
    assert first_long_term_date(date(2026, 1, 31)) == date(2027, 2, 1)


def test_cn_per_sale_nets_lots_within_one_order():
    from core.cn_tax import TransferItem, property_transfer_tax
    a = TransferItem(date(2026, 5, 1), D(100), D(250), D(300), D(10), D(7), order="o1")
    b = TransferItem(date(2026, 5, 1), D(100), D(250), D(200), D(10), D(7), order="o1")
    assert property_transfer_tax([a, b], P26, "per_sale")["tax"] == 0
    c = TransferItem(date(2026, 6, 1), D(10), D(250), D(200), D(0), D(7), order="o2")
    assert property_transfer_tax([a, b, c], P26, "per_sale")["tax"] == D(10) * D(50) * D(7) * D("0.2")


def test_dual_status_nonresident_other_income_is_eci():
    prof = TaxProfile(2026, "dual_status", residency_start=date(2026, 9, 15), us_days=292)
    r = compute_tax(prof, [], [IncomeItem("other_ordinary", D(20000), "nonresident")], P26)
    assert r.federal == D("2152.00")
    r2 = compute_tax(prof, [], [IncomeItem("interest", D(500), "nonresident")], P26)
    assert r2.federal == 0 and any("未计入" in w.label for w in r2.trace.warnings)


def test_dual_status_without_start_falls_back(conn):
    from ui_common import build_profile, save_tax_year
    save_tax_year(conn, 2026, status_override="dual_status")
    prof, notes = build_profile(conn, 2026, {})
    assert prof.status == "nonresident" and notes


def _resident_tax(items):
    return compute_tax(TaxProfile(2026, "resident"), items, [IncomeItem("wages", D(80000))], P26)


@pytest.mark.parametrize("b_price,c_date", [(160, date(2026, 6, 10)), (140, date(2026, 6, 15))])
def test_chained_wash_sale_defers_into_next_replacement(conn, b_price, c_date):
    from ui_common import realized_items
    a = _add_lot(conn, "ACME", date(2025, 1, 2), 100, 200)
    b = _add_lot(conn, "ACME", date(2026, 3, 5), 100, 150)
    _add_lot(conn, "ACME", c_date, 100, 160)   # C：B 卖出后 30 天内又买回，一直持有
    conn.commit()
    record_sale(conn, "Schwab-123", "ACME", date(2026, 3, 19), D(150), D(0), [(a, D(100))])
    record_sale(conn, "Schwab-123", "ACME", date(2026, 6, 1), D(b_price), D(0), [(b, D(100))])
    items, hits = realized_items(conn, 2026)
    assert len(hits) == 2
    r = _resident_tax(items)
    assert r.federal == D("8770.00") and r.carry_out_lt == 0 and r.carry_out_st == 0


def test_equivalent_ticker_replacement_not_treated_as_broker_adjusted(conn):
    from core.db import set_setting
    from ui_common import realized_items
    set_setting(conn, "wash_equivalents", "GOOG=GOOGL")
    a = _add_lot(conn, "GOOG", date(2025, 1, 2), 100, 200)
    conn.commit()
    record_sale(conn, "Schwab-123", "GOOG", date(2026, 3, 19), D(150), D(0), [(a, D(100))])
    # GOOGL 是另一个证券：券商不会把 GOOG 的亏损加进 GOOGL 的成本
    b = _add_lot(conn, "GOOGL", date(2026, 3, 25), 100, 150, source="schwab_csv", as_of="2026-04-01")
    conn.commit()
    record_sale(conn, "Schwab-123", "GOOGL", date(2026, 6, 1), D(160), D(0), [(b, D(100))])
    items, _ = realized_items(conn, 2026)
    r = _resident_tax(items)
    assert r.federal == D("8110.00") and r.carry_out_lt == D(1000)


def test_hypothetical_loss_always_adjusts_replacement():
    from core.wash_sale import Acquisition, apply_wash
    k_a, k_b = ("Schwab-123", "TSM", date(2025, 6, 2)), ("Schwab-123", "TSM", date(2026, 10, 1))
    items = [GainItem(date(2026, 10, 15), "TSM", D(30), D(9000), D(10050), False),
             GainItem(date(2026, 10, 15), "TSM", D(10), D(3000), D(2900), False)]
    meta = [{"key": k_a, "open_date": k_a[2], "broker_adjusted_on": "2026-10-15", "real": False},
            {"key": k_b, "open_date": k_b[2], "broker_adjusted_on": "2026-10-15", "real": False}]
    acq = [Acquisition(k_b[2], "TSM", D(10), k_b)]
    out, hits = apply_wash(items, meta, acq, 30, [], is_long_term)
    assert len(hits) == 1 and hits[0].disallowed == D(350)
    b = [it for i, it in out if i == 1]
    assert len(b) == 1 and b[0].cost_basis == D(3250)      # 替代股成本 2,900 + 350
    assert sum(it.gain for _, it in out) == D(-950)        # A −1,050 与 B +100 的合计不变，只是递延到替代股上


@pytest.mark.parametrize("extra", ["", " "])
def test_cp1252_csv_any_length(extra):
    raw = (FIX / "ABC_lot_details.csv").read_text(encoding="utf-8").replace("Long Term", "Long Term – note" + extra)
    for data in (raw.encode("cp1252"), raw.encode("utf-16"), raw.encode("utf-8-sig")):
        pf = parse_lot_details(data, "x.csv")
        assert pf.ticker == "ABC" and pf.total_quantity == D(1020)


def test_day_pnl_for_lot_bought_on_quote_day_uses_cost():
    from core.portfolio import Quote, ticker_view
    old = Lot(1, "Schwab-123", "SGOV", date(2026, 9, 1), D(100), D("100.40"), D(10040))
    new = Lot(2, "Schwab-123", "SGOV", date(2026, 10, 16), D(500), D("100.57"), D("50285"))
    q = Quote("SGOV", D("100.59"), D("100.55"), "2026-10-16")
    row = ticker_view([old, new], {"SGOV": q})[0]
    assert row["day_pnl"] == D(100) * D("0.04") + D(500) * D("0.02")    # 新批次从买入价 100.57 算起
    q_next = Quote("SGOV", D("100.60"), D("100.59"), "2026-10-19")
    assert ticker_view([new], {"SGOV": q_next})[0]["day_pnl"] == D(500) * D("0.01")  # 下个交易日从前收盘价算起
    q_manual = Quote("SGOV", D("100.60"), D("100.59"), "")                # 手动行情没填日期
    assert ticker_view([new], {"SGOV": q_manual})[0]["day_pnl"] == D(500) * D("0.01")


def test_fund_tickers_split_out_of_stocks_but_stay_in_total():
    from core.portfolio import parse_fund_tickers, summary_rows
    rows_ = [{"ticker": "ACME", "market_value": D(1000), "day_pnl": D(-50)},
             {"ticker": "SGOV", "market_value": D(500), "day_pnl": D(1)}]
    assert parse_fund_tickers(" sgov，snsxx ") == {"SGOV", "SNSXX"}
    assert parse_fund_tickers("SGOV、SNSXX;bil") == {"SGOV", "SNSXX", "BIL"}
    plain = summary_rows(rows_, None, None, None)
    split = summary_rows(rows_, None, None, None, funds=parse_fund_tickers("SGOV"))
    assert [r["item"] for r in split] == ["Schwab 股票", "基金", "合计（不含现金）"]
    assert split[0]["market_value"] == D(1000) and split[1]["market_value"] == D(500)
    assert split[-1]["market_value"] == plain[-1]["market_value"] == D(1500)
    assert split[-1]["day_pnl"] == plain[-1]["day_pnl"] == D(-49)


def test_table_html_escapes_and_formats():
    import pandas as pd
    from ui_html import table_html
    df = pd.DataFrame([{"项目": "<script>x</script>", "金额": "$1,234.50 / ¥8,240.00", "今日盈亏": "-$10.00 / -¥66.77",
                        "备注": "a&b"},
                       {"项目": "合计", "金额": "$2.00 / ¥13.35", "今日盈亏": "+$3.00 / +¥20.03", "备注": None}])
    h = table_html(df)
    assert "<script>" not in h and "&lt;script&gt;" in h and "a&amp;b" in h
    assert '<span class="ff-m2">¥8,240.00</span>' in h                 # 人民币单独一行
    assert '<span class="ff-m ff-down"><span>−$10.00' in h and '<span class="ff-m ff-up"><span>+$3.00' in h
    assert '<tr class="ff-total">' in h and 'class="num"' in h
    assert table_html(pd.DataFrame()) == '<div class="ff-empty">暂无记录</div>'


def test_trace_html_links_only_http_sources():
    from core.params import ParamRef
    from core.trace import Trace
    from ui_html import trace_html
    t = Trace()
    t.add("联邦税", D(100), "按税率表", [ParamRef("brackets.single", "10%", "https://www.irs.gov/x"),
                                       ParamRef("x", "<b>", "javascript:alert(1)")], kind="tax")
    h = trace_html(t.lines, fx=D("7"))
    assert 'href="https://www.irs.gov/x"' in h and "javascript:" not in h and "&lt;b&gt;" in h


def test_refresh_never_overwrites_newer_cached_quote(conn):
    from core.market import get_quotes, save_cache
    from core.portfolio import Quote
    save_cache(conn, {"ACME": Quote("ACME", D("128.20"), D("134.50"), "2026-10-16")})

    def fetcher(tickers):   # Yahoo 收盘后把 10-16 的收盘价给成空值 → 只拿到 10-15
        return {"ACME": Quote("ACME", D("134.50"), D("130.50"), "2026-10-15"),
                "CNY=X": Quote("CNY=X", D("6.6768"), D("6.6926"), "2026-10-16")}, []
    quotes, msgs = get_quotes(conn, ["ACME"], refresh=True, fetcher=fetcher)
    assert quotes["ACME"].quote_date == "2026-10-16" and quotes["ACME"].close == D("128.20")
    assert any("比缓存旧" in m for m in msgs)
    quotes2, _ = get_quotes(conn, ["ACME"], refresh=False)
    assert quotes2["ACME"].close == D("128.20")     # 缓存也没被旧数据覆盖
