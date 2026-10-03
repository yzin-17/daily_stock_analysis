"""腾讯原生日线的字段、窗口分段与失败关闭测试。"""

from hashlib import sha256
import json
from unittest.mock import patch

import pytest

from data_provider.base import DataFetchError
from data_provider.tencent_fetcher import TencentFetcher
from data_provider.tencent_native_daily import (
    TENCENT_NATIVE_DAILY_PROTOCOL, TENCENT_NATIVE_DAILY_URL,
    fetch_tencent_native_daily,
)
from src.services.thesis_ledger_market_v3_pagination import (
    MarketPaginationError,
    market_pagination_proof_v3,
)


def _row(day, *, amount="23.45", close="2.10"):
    return [day, "2.00", close, "2.20", "1.90", "123", "0", "0", amount, "0"]


class _Response:
    def __init__(self, variable, symbol, rows, *, code=0, key="day"):
        self.text = f"{variable}=" + json.dumps({
            "code": code, "data": {symbol: {key: rows}},
        })

    def raise_for_status(self):
        pass


def test_native_amount_and_volume_are_read_from_upstream_fields(monkeypatch):
    requests = []

    def fake_get(url, *, params, timeout, **kwargs):
        requests.append((url, params, timeout))
        return _Response(params["_var"], "sh510300", [_row("2025-06-13")])

    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", fake_get)
    frame, retrieval = fetch_tencent_native_daily(
        "sh510300", "2025-06-13", "2025-06-30", "none", 4.5,
    )
    assert requests[0][0] == TENCENT_NATIVE_DAILY_URL
    assert requests[0][1]["param"] == "sh510300,day,2025-06-13,2025-06-30,640,"
    assert 0 < requests[0][2] <= 4.5
    assert frame.iloc[0]["volume"] == "12300"
    assert frame.iloc[0]["amount"] == "234500.00"
    assert frame.iloc[0]["amount"] != str(2.1 * 12300)
    assert retrieval["partitions"][0]["rows"] == 1
    assert retrieval["partitions"][0]["sha256"] == sha256(
        _Response(requests[0][1]["_var"], "sh510300", [_row("2025-06-13")]).text.encode()
    ).hexdigest()


def test_year_partitions_and_qfq_coordinate_share_one_total_budget(monkeypatch):
    calls = []

    def fake_get(url, *, params, timeout, **kwargs):
        calls.append(params["param"])
        day = "2024-12-31" if len(calls) == 1 else "2025-01-02"
        return _Response(params["_var"], "sz159516", [_row(day)], key="qfqday")

    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", fake_get)
    frame, retrieval = fetch_tencent_native_daily(
        "sz159516", "2024-12-31", "2025-01-02", "qfq", 4.5,
    )
    assert calls == [
        "sz159516,day,2024-12-31,2024-12-31,640,qfq",
        "sz159516,day,2025-01-01,2025-01-02,640,qfq",
    ]
    assert frame["date"].tolist() == ["2024-12-31", "2025-01-02"]
    assert [part["rows"] for part in retrieval["partitions"]] == [1, 1]


def test_exact_etf_hfq_uses_native_key_and_matching_pagination_proof(monkeypatch):
    requests = []
    rows = [
        ["2026-07-09", "3.610", "3.890", "3.890", "3.554", "51677053.00", "0", "0", "957065.64", "0"],
        ["2026-07-10", "3.892", "3.620", "4.016", "3.612", "123385264.00", "0", "0", "1233856.07", "0"],
    ]

    def fake_get(url, *, params, timeout, **kwargs):
        requests.append((url, params, timeout))
        return _Response(params["_var"], "sz159516", rows, key="hfqday")

    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", fake_get)
    frame = TencentFetcher().get_daily_data_for_source(
        "159516", "tencent", start_date="2026-07-09", end_date="2026-07-10",
        adjustment="hfq", asset_type="ETF", timeout_seconds=4.5,
    )

    assert requests[0][0] == TENCENT_NATIVE_DAILY_URL
    assert requests[0][1] == {
        "_var": "kline_dayhfq2026",
        "param": "sz159516,day,2026-07-09,2026-07-10,640,hfq",
    }
    assert 0 < requests[0][2] <= 4.5
    assert frame["close"].tolist() == [3.89, 3.62]
    assert frame["volume"].tolist() == [5167705300.0, 12338526400.0]
    assert frame["amount"].tolist() == [9570656400.0, 12338560700.0]
    assert frame.attrs["tencentDailyRetrieval"]["adjustment"] == "hfq"

    frame.attrs["thesis_ledger_v3_pagination"] = {
        "status": "complete", "pagesFetched": 1, "continuationPending": False,
        "protocol": TENCENT_NATIVE_DAILY_PROTOCOL, "maximumRows": 800,
        "requestedStart": "2026-07-09", "requestedEnd": "2026-07-10",
    }
    assert market_pagination_proof_v3(
        frame, asset_type="ETF", provider="tencent", upstream_source="tencent",
        expected_session_count=2, requested_start="2026-07-09",
        requested_end="2026-07-10", requested_adjustment="hfq",
    )["pagesFetched"] == 1
    with pytest.raises(MarketPaginationError, match="invalid_response"):
        market_pagination_proof_v3(
            frame, asset_type="ETF", provider="tencent", upstream_source="tencent",
            expected_session_count=2, requested_start="2026-07-09",
            requested_end="2026-07-10", requested_adjustment="qfq",
        )


@pytest.mark.parametrize("response_key", ["day", "qfqday"])
def test_native_hfq_rejects_other_adjustment_response_keys(monkeypatch, response_key):
    def fake_get(url, *, params, timeout, **kwargs):
        return _Response(params["_var"], "sz159516", [_row("2026-07-09")], key=response_key)

    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", fake_get)
    with pytest.raises(DataFetchError, match="口径"):
        fetch_tencent_native_daily(
            "sz159516", "2026-07-09", "2026-07-10", "hfq", 4.5,
        )


def test_native_hfq_rejects_missing_amount(monkeypatch):
    def fake_get(url, *, params, timeout, **kwargs):
        return _Response(
            params["_var"], "sz159516", [_row("2026-07-09")[:8]], key="hfqday",
        )

    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", fake_get)
    with pytest.raises(DataFetchError, match="原生成交额"):
        fetch_tencent_native_daily(
            "sz159516", "2026-07-09", "2026-07-10", "hfq", 4.5,
        )


@pytest.mark.parametrize("asset_type", [None, "STOCK", "MUTUAL_FUND"])
def test_exact_tencent_hfq_is_restricted_to_etf_routes(monkeypatch, asset_type):
    with patch("data_provider.tencent_native_daily.requests.get") as request:
        with pytest.raises(DataFetchError, match="only for ETF"):
            TencentFetcher().get_daily_data_for_source(
                "159516", "tencent", start_date="2026-07-09", end_date="2026-07-10",
                adjustment="hfq", asset_type=asset_type,
            )
        request.assert_not_called()


def test_exact_fetcher_preserves_native_retrieval_for_v3_proof(monkeypatch):
    calls = []

    def fake_get(url, *, params, **kwargs):
        calls.append(params["param"])
        day = "2024-12-31" if len(calls) == 1 else "2025-01-02"
        return _Response(params["_var"], "sz159516", [_row(day)])

    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", fake_get)
    frame = TencentFetcher().get_daily_data_for_source(
        "159516", "tencent", start_date="2024-12-31", end_date="2025-01-02",
        adjustment="none", timeout_seconds=4.5,
    )
    assert frame["amount"].tolist() == [234500.0, 234500.0]
    frame.attrs["thesis_ledger_v3_pagination"] = {
        "status": "complete", "pagesFetched": 2, "continuationPending": False,
        "protocol": TENCENT_NATIVE_DAILY_PROTOCOL, "maximumRows": 800,
        "requestedStart": "2024-12-31", "requestedEnd": "2025-01-02",
    }
    assert market_pagination_proof_v3(
        frame, asset_type="ETF", provider="tencent", upstream_source="tencent",
        expected_session_count=2, requested_start="2024-12-31",
        requested_end="2025-01-02", requested_adjustment="none",
    )["pagesFetched"] == 2


def test_total_deadline_rejects_response_that_arrived_late(monkeypatch):
    def fake_get(url, *, params, **kwargs):
        return _Response(params["_var"], "sh510300", [_row("2025-06-13")])

    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", fake_get)
    with patch("data_provider.tencent_native_daily.time.monotonic", side_effect=[0, 0, 10]):
        with pytest.raises(DataFetchError, match="总期限"):
            fetch_tencent_native_daily("sh510300", "2025-06-13", "2025-06-30", "none", 4.5)


@pytest.mark.parametrize("rows,code,symbol,key", [
    ([_row("2025-06-13")[:8]], 0, "sh510300", "day"),
    ([_row("2025-06-13", amount="NaN")], 0, "sh510300", "day"),
    ([_row("2025-06-13", amount="-1")], 0, "sh510300", "day"),
    ([_row("2025-06-13"), _row("2025-06-13")], 0, "sh510300", "day"),
    ([_row("2025-06-13")], True, "sh510300", "day"),
    ([_row("2025-06-13")], 0, "sz159516", "day"),
    ([_row("2025-06-13")], 0, "sh510300", "qfqday"),
])
def test_missing_native_fact_or_wrong_identity_fails_closed(monkeypatch, rows, code, symbol, key):
    def fake_get(url, *, params, **kwargs):
        return _Response(params["_var"], symbol, rows, code=code, key=key)

    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", fake_get)
    with pytest.raises(DataFetchError):
        fetch_tencent_native_daily("sh510300", "2025-06-13", "2025-06-30", "none", 4.5)


@pytest.mark.parametrize("symbol,start,end,adjustment,timeout", [
    ("bj920748", "2025-01-01", "2025-01-02", "none", 4.5),
    ("sh510300", "2025-01-01", "2025-01-02", "backward", 4.5),
    ("sh510300", "2025-01-03", "2025-01-02", "none", 4.5),
    ("sh510300", "2025-01-01", "2033-01-02", "none", 4.5),
    ("sh510300", "2025-01-01", "2025-01-02", "none", float("nan")),
])
def test_invalid_request_is_rejected_before_transport(symbol, start, end, adjustment, timeout):
    with patch("data_provider.tencent_native_daily.requests.get") as request:
        with pytest.raises(DataFetchError):
            fetch_tencent_native_daily(symbol, start, end, adjustment, timeout)
        request.assert_not_called()
