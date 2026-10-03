"""HiThink 基金分红字段、经济日期及不完整覆盖的离线合同。"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from data_provider.hithink_fund_dividends import normalize_hithink_fund_dividends


def ms(day):
    instant = datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return int(instant.timestamp() * 1000)


ROW = {
    "per_ten_cash_before_tax": 0.88,
    "per_ten_cash_after_tax": 0.88,
    "progress": "实施",
    "publish_date_ms": ms("2025-06-11"),
    "registration_date_ms": ms("2025-06-17"),
    "ex_dividend_date_ms": ms("2025-06-18"),
    "payment_date_ms": ms("2025-06-27"),
    "reinvestment_date_ms": ms("2025-06-27"),
    "profit_base_date_ms": ms("2025-06-10"),
    "in_dividend_date_ms": ms("2025-06-27"),
}


def normalize(rows, **overrides):
    options = {
        "instrument_type": "ETF", "currency": "CNY", "start": "2025-06-01", "end": "2025-06-30",
        "observed_at": datetime(2026, 9, 28, tzinfo=timezone.utc), "provider_revision": "fixture-v1",
    }
    return normalize_hithink_fund_dividends(rows, "510300.SH", **{**options, **overrides})


def test_implemented_row_maps_per_ten_cash_and_distinct_dates():
    result = normalize([ROW, ROW])
    assert len(result["facts"]) == len(result["observations"]) == 1
    fact = result["facts"][0]
    assert fact["cashAmount"] == "0.088"
    assert fact["effectiveDate"] == "2025-06-18"
    assert fact["recordDate"] == "2025-06-17"
    assert fact["paymentDate"] == "2025-06-27"
    assert fact["occurredAt"] == "2025-06-18T00:00:00+08:00"
    assert fact["availableAt"] == "2026-09-28T00:00:00Z"
    assert "strategyVisibility" not in fact
    observation = result["observations"][0]
    assert observation["announcementDate"] == "2025-06-11"
    assert observation["reinvestmentDate"] == "2025-06-27"
    assert observation["profitBaseDate"] == "2025-06-10"
    assert observation["inDividendDate"] == "2025-06-27"
    assert observation["cashUnit"] == "per-fund-unit"
    assert observation["cashAfterTaxAmount"] == "0.088"
    assert result["coverage"]["complete"] is False


def test_non_implemented_and_empty_rows_do_not_create_facts_or_complete_coverage():
    pending = {**ROW, "progress": "预案", "ex_dividend_date_ms": None, "per_ten_cash_before_tax": None}
    result = normalize([pending])
    assert result["facts"] == []
    assert result["observations"][0]["effectiveDate"] is None
    assert result["coverage"]["complete"] is False
    assert normalize([])["coverage"]["complete"] is False


def test_live_numeric_progress_code_is_not_silently_treated_as_no_event():
    with pytest.raises(ValueError, match="进度口径未经核验"):
        normalize([{**ROW, "progress": "2"}])


@pytest.mark.parametrize("change", [
    {"per_ten_cash_before_tax": None}, {"per_ten_cash_before_tax": 0},
    {"per_ten_cash_before_tax": -1}, {"per_ten_cash_before_tax": "NaN"},
    {"per_ten_cash_before_tax": True}, {"ex_dividend_date_ms": None},
    {"ex_dividend_date_ms": -1},
    {"registration_date_ms": ms("2025-06-19")},
    {"payment_date_ms": ms("2025-06-17")},
    {"publish_date_ms": ms("2025-06-19")},
])
def test_invalid_amount_or_dates_fail_closed(change):
    with pytest.raises(ValueError):
        normalize([{**ROW, **change}])


def test_conflicting_duplicates_and_progress_require_revision_evidence():
    with pytest.raises(ValueError, match="冲突"):
        normalize([ROW, {**ROW, "per_ten_cash_before_tax": 0.89}])
    with pytest.raises(ValueError, match="冲突"):
        normalize([ROW, {**ROW, "publish_date_ms": ms("2025-06-12")}])
    with pytest.raises(ValueError, match="进度冲突"):
        normalize([ROW, {**ROW, "progress": "预案"}])


def test_window_filter_and_verified_currency_are_explicit():
    assert normalize([ROW], start="2025-07-01", end="2025-07-31")["facts"] == []
    with pytest.raises(ValueError, match="币种"):
        normalize([ROW], currency="UNKNOWN")


def test_after_tax_zero_is_observed_without_changing_gross_economic_fact():
    result = normalize([{**ROW, "per_ten_cash_after_tax": 0}])
    assert result["observations"][0]["cashAfterTaxAmount"] == "0"
    assert result["facts"][0]["cashAmount"] == "0.088"
