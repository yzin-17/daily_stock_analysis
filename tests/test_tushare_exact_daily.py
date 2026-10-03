"""精确 ETF raw 入口的端点隔离、分段超时与限流边界。"""

import json as json_module
import time
from unittest.mock import Mock, patch

import pytest

from data_provider.base import DataFetchError, RateLimitError
from data_provider.tushare_fetcher import TushareFetcher


@pytest.fixture
def fetcher():
    with patch.dict("os.environ", {"TUSHARE_HTTP_URL": ""}):
        return TushareFetcher(token="fixture-token")


def read(fetcher, **overrides):
    arguments = dict(
        stock_code="159516.SZ", upstream_source="tushare", start_date="2024-01-01",
        end_date="2025-01-02", adjustment="none", timeout_seconds=10,
    )
    arguments.update(overrides)
    return fetcher.get_daily_data_for_source(**arguments)


def response_for_request(url, *, json, timeout, allow_redirects):
    params = json["params"]
    assert json["api_name"] == "fund_daily"
    assert 0 < timeout <= 10
    assert allow_redirects is False
    return Mock(status_code=200, text=json_module.dumps({
        "code": 0, "data": {
            "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
            "items": [[params["ts_code"], params["start_date"], 4, 5, 3, 4, 12, 23]],
        },
    }))


def test_http_exact_raw_partitions_preserve_evidence_and_cached_client(fetcher):
    cached = fetcher._api
    with patch("data_provider.tushare_fetcher.requests.post", side_effect=response_for_request) as post:
        result = read(fetcher)
    assert post.call_count == 2
    assert fetcher._call_count == 2
    assert fetcher._api is cached and cached._timeout == 30
    assert result["volume"].tolist() == [1200, 1200]
    assert result["amount"].tolist() == [23000, 23000]
    assert result.attrs["upstream_source"] == "tushare"
    assert len(result.attrs["fundDailyRetrieval"]["partitions"]) == 2
    assert result.attrs["fundDailyRetrieval"]["tradingCalendarVerified"] is False
    assert result.attrs["thesis_ledger_v3_pagination"] == {
        "status": "complete", "pagesFetched": 2, "continuationPending": False,
        "protocol": "tushare-fund-daily-date-partitions-366-v1", "maximumRows": None,
        "requestedStart": "2024-01-01", "requestedEnd": "2025-01-02",
    }


@pytest.mark.parametrize("change", [
    {"upstream_source": "eastmoney"}, {"adjustment": "qfq"}, {"stock_code": "600519.SH"},
    {"stock_code": "159516.SH"}, {"start_date": None}, {"end_date": None},
    {"timeout_seconds": True}, {"timeout_seconds": float("nan")}, {"timeout_seconds": 0},
])
def test_invalid_exact_requests_make_no_http_calls(fetcher, change):
    with patch("data_provider.tushare_fetcher.requests.post") as post, pytest.raises(ValueError):
        read(fetcher, **change)
    post.assert_not_called()


def test_exhausted_rate_quota_fails_without_sleep_or_network(fetcher):
    fetcher._minute_start = time.time()
    fetcher._call_count = fetcher.rate_limit_per_minute
    with patch("time.sleep") as sleep, patch("data_provider.tushare_fetcher.requests.post") as post:
        with pytest.raises(RateLimitError):
            read(fetcher)
    sleep.assert_not_called()
    post.assert_not_called()


def test_deadline_prevents_second_partition(fetcher):
    with patch("data_provider.tushare_history_request.time.monotonic", side_effect=[0, 0, 1, 11]), \
            patch("data_provider.tushare_fetcher.requests.post", side_effect=response_for_request) as post:
        with pytest.raises(TimeoutError):
            read(fetcher)
    assert post.call_count == 1


def test_endpoint_permission_failure_does_not_retry_or_fallback(fetcher):
    denied = Mock(status_code=200, text=json_module.dumps({"code": -2002, "msg": "fixture permission denied"}))
    with patch("data_provider.tushare_fetcher.requests.post", return_value=denied) as post:
        with pytest.raises(Exception, match="fixture permission denied"):
            read(fetcher)
    assert post.call_count == 1


def test_missing_credential_never_uses_environment_fallback(fetcher):
    fetcher._token = ""
    with patch("data_provider.tushare_fetcher.requests.post") as post, pytest.raises(DataFetchError):
        read(fetcher)
    post.assert_not_called()


def test_late_final_response_is_not_returned_as_success(fetcher):
    with patch("data_provider.tushare_history_request.time.monotonic", side_effect=[0, 0, 1, 11]), \
            patch("data_provider.tushare_fetcher.requests.post", side_effect=response_for_request) as post:
        with pytest.raises(TimeoutError):
            read(fetcher, end_date="2024-01-02")
    assert post.call_count == 1


def test_later_partition_failure_does_not_return_partial_data(fetcher):
    count = 0

    def failure_after_first(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise TimeoutError("fixture transport timeout")
        return response_for_request(*args, **kwargs)

    with patch("data_provider.tushare_fetcher.requests.post", side_effect=failure_after_first) as post:
        with pytest.raises(TimeoutError, match="fixture transport timeout"):
            read(fetcher)
    assert post.call_count == 2
