"""基金因子的精确读取、十进制精度、分段完整性与未知转换资格。"""

from datetime import datetime
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from data_provider.tushare_fetcher import TushareFetcher
from data_provider.tushare_fund_adj import FIELDS, fetch_tushare_fund_adj
from data_provider.tushare_history_window import fund_history_windows


def source_frame(day="20250102", **changes):
    return pd.DataFrame([{"ts_code": "159516.SZ", "trade_date": day, "adj_factor": "1.01234567890123456789", **changes}])


def fetch(api, **changes):
    args = dict(symbol="159516.SZ", start="2025-01-01", end="2025-01-03", before_call=Mock())
    args.update(changes)
    return fetch_tushare_fund_adj(api, **args)


def test_exact_factor_endpoint_retains_precision_and_explicit_unknown_basis():
    api = Mock()
    api.fund_adj.return_value = source_frame()
    result = fetch(api)
    api.fund_adj.assert_called_once_with(
        ts_code="159516.SZ", start_date="20250101", end_date="20250103", fields=FIELDS, limit=2000,
    )
    assert result.iloc[0]["adj_factor"] == "1.01234567890123456789"
    evidence = result.attrs["fundAdjustmentRetrieval"]
    assert evidence["conversionAvailable"] is False
    assert evidence["anchor"] is None
    assert evidence["upstreamDataRevision"] is None
    assert evidence["adjustmentAlgorithmRevision"] is None
    assert evidence["tradingCalendarVerified"] is False
    assert datetime.fromisoformat(evidence["observedAt"]).utcoffset().total_seconds() == 0
    assert evidence["contentFingerprint"] == fetch(api).attrs["fundAdjustmentRetrieval"]["contentFingerprint"]


def test_partitions_are_nonoverlapping_and_each_charged_once():
    api, before = Mock(), Mock()
    api.fund_adj.side_effect = lambda **kwargs: source_frame(kwargs["start_date"])
    result = fetch(api, start="20240101", end="20250102", before_call=before)
    assert before.call_count == api.fund_adj.call_count == 2
    assert result["trade_date"].tolist() == ["20240101", "20250101"]
    assert [(row["start"], row["end"]) for row in result.attrs["fundAdjustmentRetrieval"]["partitions"]] == [
        ("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-01-02"),
    ]


@pytest.mark.parametrize("changes", [
    {"ts_code": "510300.SH"}, {"trade_date": "20241231"}, {"trade_date": "20250104"},
    {"trade_date": "20250230"}, {"adj_factor": 0}, {"adj_factor": -1},
    {"adj_factor": "NaN"}, {"adj_factor": "Infinity"}, {"adj_factor": True}, {"adj_factor": None},
])
def test_bad_factors_are_rejected_without_retry(changes):
    api = Mock()
    api.fund_adj.return_value = source_frame(**changes)
    with pytest.raises(ValueError):
        fetch(api)
    api.fund_adj.assert_called_once()
    api.adj_factor.assert_not_called()


@pytest.mark.parametrize("shape", ["duplicate", "missing", "not-frame", "duplicate-column"])
def test_malformed_pages_are_not_treated_as_missing_history(shape):
    api = Mock()
    data = source_frame()
    if shape == "duplicate":
        data = pd.concat([data, data])
    elif shape == "missing":
        data = data.drop(columns=["adj_factor"])
    elif shape == "not-frame":
        data = []
    else:
        data = pd.concat([data, data[["adj_factor"]]], axis=1)
    api.fund_adj.return_value = data
    with pytest.raises(ValueError):
        fetch(api)


def test_empty_page_never_grants_conversion_or_historical_coverage():
    api = Mock()
    api.fund_adj.return_value = source_frame().iloc[0:0]
    result = fetch(api)
    assert result.empty
    assert result.attrs["fundAdjustmentRetrieval"]["partitionComplete"] is True
    assert result.attrs["fundAdjustmentRetrieval"]["conversionAvailable"] is False
    assert result.attrs["fundAdjustmentRetrieval"]["tradingCalendarVerified"] is False


def test_positive_decreasing_factor_is_not_reinterpreted_as_a_split_event():
    api = Mock()
    api.fund_adj.return_value = pd.concat([
        source_frame("20250102", adj_factor="2"), source_frame("20250103", adj_factor="1"),
    ])
    assert fetch(api)["adj_factor"].tolist() == ["2", "1"]


def test_budget_rejected_before_network_and_late_page_failure_never_returns_partial_result():
    api, before = Mock(), Mock()
    with pytest.raises(ValueError, match="budget"):
        fetch(api, start="20240101", end="20250102", before_call=before, max_calls=1)
    before.assert_not_called()
    api.fund_adj.assert_not_called()
    api.fund_adj.side_effect = [source_frame("20240101"), TimeoutError("fixture timeout")]
    with pytest.raises(TimeoutError):
        fetch(api, start="20240101", end="20250102")
    assert api.fund_adj.call_count == 2


def test_http_factor_json_is_parsed_as_decimal_before_dataframe_conversion():
    fetcher = TushareFetcher(token="fixture-token", http_url="https://fixture.example/api")
    value = Mock(status_code=200, text='{"code":0,"data":{"fields":["ts_code","trade_date","adj_factor"],'
                 '"items":[["159516.SZ","20250102",1.01234567890123456789]]}}')
    with patch("data_provider.tushare_fetcher.requests.post", return_value=value) as post:
        result = fetcher.get_fund_adjustment_factors_for_source(
            "159516.SZ", "tushare", start_date="20250101", end_date="20250103",
        )
    assert result.iloc[0]["adj_factor"] == "1.01234567890123456789"
    assert post.call_args.kwargs["json"]["api_name"] == "fund_adj"
    assert fetcher._call_count == 1


def test_window_partition_does_not_overflow_maximum_date():
    windows = fund_history_windows("159516.SZ", "9999-12-30", "9999-12-31")
    assert len(windows) == 1
    assert windows[0][1].isoformat() == "9999-12-31"


@pytest.mark.parametrize("changes", [
    {"stock_code": "600519.SH"}, {"upstream_source": "eastmoney"},
    {"start_date": "2025-01-04"}, {"timeout_seconds": 0},
])
def test_exact_factor_entry_rejects_invalid_scope_without_http(changes):
    fetcher = TushareFetcher(token="fixture-token", http_url="https://fixture.example/api")
    arguments = dict(stock_code="159516.SZ", upstream_source="tushare", start_date="20250101", end_date="20250103")
    arguments.update(changes)
    with patch("data_provider.tushare_fetcher.requests.post") as post, pytest.raises(ValueError):
        fetcher.get_fund_adjustment_factors_for_source(**arguments)
    post.assert_not_called()
