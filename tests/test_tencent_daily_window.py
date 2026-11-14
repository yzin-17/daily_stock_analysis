"""腾讯超额历史候选、精确日期投影与独立错误类别回归。"""

from datetime import date, timedelta
import json

import pytest
import requests

from data_provider.tencent_native_daily import (
    MAX_RESPONSE_BYTES, MAX_ROWS_PER_RESPONSE, REQUEST_ROW_LIMIT,
    TencentDailyResponseError, fetch_tencent_native_daily,
)
from src.services.thesis_ledger_provider_errors import classify_provider_failure


def _row(day):
    return [day, "1445.00", "1402.30", "1445.62", "1402.00", "39848", {}, "0.32", "563387", "", "0.32"]


def _history(count):
    first = date(2024, 3, 20)
    return [_row((first + timedelta(days=i)).isoformat()) for i in range(count)]


class Response:
    def __init__(self, variable, rows):
        self.text = variable + "=" + json.dumps({"code": 0, "data": {"sh600519": {"day": rows}}})

    def raise_for_status(self):
        pass


@pytest.mark.parametrize("start", ["2026-11-11", "2026-11-10"])
def test_excess_history_is_projected_to_exact_inclusive_window(monkeypatch, start):
    rows = _history(639) + [_row("2026-11-10"), _row("2026-11-11")]
    calls = []

    def fake_get(url, *, params, **kwargs):
        calls.append(params)
        assert "headers" not in kwargs
        return Response(params["_var"], rows)

    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", fake_get)
    frame, retrieval = fetch_tencent_native_daily("sh600519", start, "2026-11-11", "none", 4.5)
    expected = ["2026-11-10", "2026-11-11"] if start == "2026-11-10" else ["2026-11-11"]
    assert frame["date"].tolist() == expected
    assert frame.iloc[-1]["close"] == "1402.30"
    assert frame.iloc[-1]["volume"] == "3984800"
    assert frame.iloc[-1]["amount"] == "5633870000"
    assert calls == [{"_var": "kline_day2026",
                      "param": f"sh600519,day,{start},2026-11-11,{REQUEST_ROW_LIMIT},",
                      "r": "0.8205512681390605"}]
    assert retrieval["partitions"][0]["rows"] == len(expected)


@pytest.mark.parametrize("count", [640, 641, 642])
def test_request_count_and_raw_response_budget_are_independent(monkeypatch, count):
    rows = _history(count - 1) + [_row("2026-11-11")]
    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get",
                        lambda url, *, params, **kwargs: Response(params["_var"], rows))
    assert REQUEST_ROW_LIMIT == 640
    assert MAX_ROWS_PER_RESPONSE == 641
    if count > MAX_ROWS_PER_RESPONSE:
        with pytest.raises(TencentDailyResponseError, match="单段行数超限") as caught:
            fetch_tencent_native_daily("sh600519", "2026-11-11", "2026-11-11", "none", 4.5)
        assert classify_provider_failure(caught.value) == "invalid_response"
    else:
        frame, _ = fetch_tencent_native_daily("sh600519", "2026-11-11", "2026-11-11", "none", 4.5)
        assert frame["date"].tolist() == ["2026-11-11"]


def test_duplicate_outside_requested_window_is_still_invalid(monkeypatch):
    rows = [_row("2024-03-20"), _row("2024-03-20"), _row("2026-11-11")]
    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get",
                        lambda url, *, params, **kwargs: Response(params["_var"], rows))
    with pytest.raises(TencentDailyResponseError, match="日期重复"):
        fetch_tencent_native_daily("sh600519", "2026-11-11", "2026-11-11", "none", 4.5)


def test_response_byte_budget_is_not_removed(monkeypatch):
    response = Response("kline_day2026", [])
    response.text += " " * MAX_RESPONSE_BYTES
    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", lambda *a, **kw: response)
    with pytest.raises(TencentDailyResponseError, match="大小非法"):
        fetch_tencent_native_daily("sh600519", "2026-11-11", "2026-11-11", "none", 4.5)


def test_empty_candidate_window_does_not_fabricate_target_day(monkeypatch):
    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get",
                        lambda url, *, params, **kwargs: Response(params["_var"], _history(640)))
    frame, retrieval = fetch_tencent_native_daily("sh600519", "2026-11-11", "2026-11-11", "none", 4.5)
    assert frame.empty
    assert retrieval["partitions"][0]["rows"] == 0


@pytest.mark.parametrize("status,expected", [(429, "rate_limited"), (401, "authentication_failed"),
                                           (403, "permission_denied"), (503, "upstream_failure")])
def test_http_failure_keeps_status_without_retry(monkeypatch, status, expected):
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(1)
        response = requests.Response()
        response.status_code = status
        raise requests.HTTPError(response=response)

    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", fake_get)
    with pytest.raises(Exception) as caught:
        fetch_tencent_native_daily("sh600519", "2026-11-11", "2026-11-11", "none", 4.5)
    assert classify_provider_failure(caught.value) == expected
    assert calls == [1]


@pytest.mark.parametrize("error", [requests.Timeout, requests.ConnectionError])
def test_network_failure_is_separate_from_invalid_payload(monkeypatch, error):
    def fake_get(*args, **kwargs):
        raise error()

    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", fake_get)
    with pytest.raises((ConnectionError, TimeoutError)) as caught:
        fetch_tencent_native_daily("sh600519", "2026-11-11", "2026-11-11", "none", 4.5)
    assert classify_provider_failure(caught.value) == "network_failure"
