"""ETF raw 日线分段、身份/日期与量额单位边界。"""

from unittest.mock import Mock

import pandas as pd
import pytest

from data_provider.tushare_fund_daily import FIELDS, fetch_tushare_fund_daily
from data_provider.tushare_fetcher import TushareFetcher


def frame(day="20250618", **changes):
    return pd.DataFrame([{
        "ts_code": "510300.SH", "trade_date": day, "open": "4.008", "high": "4.024",
        "low": "3.996", "close": "4.017", "vol": "382896.00", "amount": "153574.446", **changes,
    }])


def test_exact_endpoint_and_existing_unit_conversion_preserve_retrieval_evidence():
    api, before = Mock(), Mock()
    api.fund_daily.return_value = frame()
    raw = fetch_tushare_fund_daily(api, "510300.SH", "2025-06-01", "2025-06-30", before_call=before)
    api.fund_daily.assert_called_once_with(
        ts_code="510300.SH", start_date="20250601", end_date="20250630", fields=FIELDS,
    )
    before.assert_called_once()
    normalized = object.__new__(TushareFetcher)._normalize_data(raw, "510300.SH")
    assert normalized.iloc[0]["volume"] == 38289600
    assert normalized.iloc[0]["amount"] == pytest.approx(153574446)
    assert normalized.iloc[0]["close"] == 4.017
    evidence = normalized.attrs["fundDailyRetrieval"]
    assert evidence["adjustment"] == "none"
    assert evidence["partitionComplete"] is True
    assert evidence["tradingCalendarVerified"] is False
    assert len(evidence["revision"]) == 64


def test_nonoverlapping_date_partitions_and_rate_budget():
    api, before = Mock(), Mock()
    api.fund_daily.side_effect = lambda **kwargs: frame(kwargs["start_date"])
    result = fetch_tushare_fund_daily(api, "510300.SH", "20240101", "20250102", before_call=before)
    calls = [entry.kwargs for entry in api.fund_daily.call_args_list]
    assert [(call["start_date"], call["end_date"]) for call in calls] == [
        ("20240101", "20241231"), ("20250101", "20250102"),
    ]
    assert before.call_count == 2
    assert len(result) == 2


@pytest.mark.parametrize("change", [
    {"ts_code": "159516.SZ"}, {"trade_date": "20250531"}, {"trade_date": "20250631"},
    {"open": "NaN"}, {"amount": "Infinity"}, {"vol": -1}, {"close": 0},
    {"high": "3.9"}, {"low": "4.1"}, {"open": True},
])
def test_invalid_response_rejects_without_retry(change):
    api = Mock()
    api.fund_daily.return_value = frame(**change)
    with pytest.raises(ValueError):
        fetch_tushare_fund_daily(api, "510300.SH", "20250601", "20250630", before_call=Mock())
    api.fund_daily.assert_called_once()


@pytest.mark.parametrize("mode", ["duplicate", "missing-column", "wrong-type"])
def test_duplicate_missing_or_malformed_response_is_not_silently_normalized(mode):
    api = Mock()
    value = frame()
    if mode == "duplicate":
        value = pd.concat([value, value])
    elif mode == "missing-column":
        value = value.drop(columns=["ts_code"])
    else:
        value = None
    api.fund_daily.return_value = value
    with pytest.raises(ValueError):
        fetch_tushare_fund_daily(api, "510300.SH", "20250601", "20250630", before_call=Mock())


def test_budget_exceeded_is_rejected_before_any_request():
    api, before = Mock(), Mock()
    with pytest.raises(ValueError, match="budget"):
        fetch_tushare_fund_daily(api, "510300.SH", "20240101", "20250102", before_call=before, max_calls=1)
    api.fund_daily.assert_not_called()
    before.assert_not_called()


def test_empty_data_retains_unverified_calendar_and_transport_failure_never_switches_api():
    api = Mock()
    api.fund_daily.return_value = frame().iloc[0:0]
    result = fetch_tushare_fund_daily(api, "510300.SH", "20250601", "20250630", before_call=Mock())
    assert result.empty
    assert result.attrs["fundDailyRetrieval"]["tradingCalendarVerified"] is False
    api.fund_daily.side_effect = TimeoutError("fixture timeout")
    with pytest.raises(TimeoutError):
        fetch_tushare_fund_daily(api, "510300.SH", "20250601", "20250630", before_call=Mock())
    assert api.fund_daily.call_count == 2
    api.daily.assert_not_called()
