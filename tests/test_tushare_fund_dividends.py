"""Tushare 分红的金额分母、独立日期、计划进度与真实可见时间。"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from data_provider.tushare_fund_dividends import normalize_tushare_fund_dividends

OBSERVED = datetime(2026, 9, 27, tzinfo=timezone.utc)


def row(**changes):
    return {
        "ts_code": "159516.SZ", "ann_date": "20260601", "imp_anndate": "20260602",
        "base_date": "20260531", "div_proc": "实施", "record_date": "20260619", "ex_date": "20260622",
        "pay_date": "20260624", "earpay_date": "20260625", "net_ex_date": "20260623",
        "account_date": "20260626", "div_cash": "0.0150", "base_unit": "41287.3653", **changes,
    }


def normalize(rows=None, **changes):
    args = dict(
        symbol="159516.SZ", instrument_type="ETF", currency="CNY", start="2026-06-20", end="2026-06-30",
        observed_at=OBSERVED, provider_revision="fixture-revision",
    )
    args.update(changes)
    return normalize_tushare_fund_dividends(pd.DataFrame([row()] if rows is None else rows), **args)


def test_cash_is_per_unit_and_window_is_by_ex_date_not_announcement():
    result = normalize()
    fact = result["facts"][0]
    assert fact["cashAmount"] == "0.015"
    assert fact["effectiveDate"] == "2026-06-22"
    assert fact["recordDate"] == "2026-06-19"
    assert fact["paymentDate"] == "2026-06-24"
    assert fact["occurredAt"] == "2026-06-22T00:00:00+08:00"
    assert fact["availableAt"] == "2026-09-27T00:00:00Z"
    assert fact["strategyVisibility"] == {"kind": "conservative-day", "visibleDate": "2026-06-02"}
    assert fact["provider"] == "tushare"
    assert result["coverage"]["complete"] is False
    observation = result["observations"][0]
    assert observation["incomeBaseDate"] == "2026-05-31"
    assert observation["incomePaymentDate"] == "2026-06-25"
    assert observation["navExDate"] == "2026-06-23"
    assert observation["reinvestmentAccountDate"] == "2026-06-26"


def test_later_announcement_date_is_not_moved_back_to_implementation_date():
    fact = normalize([row(ann_date="20260624")])["facts"][0]
    assert fact["strategyVisibility"]["visibleDate"] == "2026-06-24"
    assert fact["availableAt"] == "2026-09-27T00:00:00Z"


def test_missing_implementation_notice_does_not_make_the_initial_plan_visible_as_final():
    result = normalize([row(imp_anndate=None, record_date=None, pay_date=None)])
    fact = result["facts"][0]
    assert "strategyVisibility" not in fact
    assert "recordDate" not in fact and "paymentDate" not in fact
    assert result["observations"][0]["announcementDate"] == "2026-06-01"


@pytest.mark.parametrize("progress", ["预案", "取消", "未知状态"])
def test_unimplemented_plans_remain_observations_without_economic_facts(progress):
    result = normalize([row(div_proc=progress, ex_date=None, div_cash="0.02")])
    assert result["facts"] == []
    assert result["observations"][0]["cashAmount"] == "0.02"
    assert result["observations"][0]["planProgress"] == progress
    assert result["coverage"]["complete"] is False


def test_identical_duplicates_deduplicate_but_conflicting_final_revisions_reject():
    result = normalize([row(), row()])
    assert len(result["facts"]) == len(result["observations"]) == 1
    with pytest.raises(ValueError, match="conflicting implemented"):
        normalize([row(), row(div_cash="0.02")])
    with pytest.raises(ValueError, match="conflicting plan progress"):
        normalize([row(), row(div_proc="取消")])


@pytest.mark.parametrize("changes", [
    {"ts_code": "159516.OF"}, {"ex_date": None}, {"ex_date": "20260631"},
    {"record_date": "20260623"}, {"pay_date": "20260621"}, {"ann_date": "invalid"},
    {"div_cash": "NaN"}, {"div_cash": "Infinity"}, {"div_cash": 0}, {"div_cash": -1},
    {"div_cash": True}, {"div_proc": None},
])
def test_invalid_identity_dates_amount_or_progress_rejects(changes):
    with pytest.raises(ValueError):
        normalize([row(**changes)])


@pytest.mark.parametrize("changes", [
    {"observed_at": datetime(2026, 9, 27)}, {"currency": None}, {"currency": "EUR"},
    {"provider_revision": ""}, {"start": "2026-07-01"}, {"instrument_type": "STOCK"},
])
def test_observation_context_must_be_explicit(changes):
    with pytest.raises(ValueError):
        normalize(**changes)


def test_nav_fund_uses_existing_domain_type_and_explicit_currency_without_symbol_aliasing():
    result = normalize([row(ts_code="005485.OF")], symbol="005485.OF", instrument_type="NAV_FUND", currency="USD")
    assert result["facts"][0]["instrumentType"] == "NAV_FUND"
    assert result["facts"][0]["currency"] == "USD"
    with pytest.raises(ValueError):
        normalize([row(ts_code="005485.OF")], symbol="005485.OF")


def test_outside_window_and_empty_input_never_prove_complete_history():
    assert normalize([row(ex_date="20260701")])["facts"] == []
    result = normalize_tushare_fund_dividends(
        pd.DataFrame([row()]).iloc[0:0], "159516.SZ", instrument_type="ETF", currency="CNY",
        start="2026-06-20", end="2026-06-30", observed_at=OBSERVED, provider_revision="fixture-revision",
    )
    assert result["facts"] == []
    assert result["coverage"]["complete"] is False
