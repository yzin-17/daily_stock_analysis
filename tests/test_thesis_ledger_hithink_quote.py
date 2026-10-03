from datetime import datetime, timezone

import pytest

from src.services.thesis_ledger_hithink_quote import (
    HITHINK_ETF_SNAPSHOT_SOURCE,
    HITHINK_ETF_SNAPSHOT_URL,
    HITHINK_STOCK_SNAPSHOT_SOURCE,
    HITHINK_STOCK_SNAPSHOT_URL,
    HiThinkQuoteError,
    HiThinkSnapshotAdapter,
)


class Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def item(symbol):
    return {
        "thscode": symbol,
        "ticker": symbol[:6],
        "last_price": 10.5,
        "price_change": -0.2,
        "price_change_ratio_pct": -1.87,
        "open_price": 10.6,
        "high_price": 10.8,
        "low_price": 10.4,
        "prev_price": 10.7,
        "volume": 12345,
        "turnover": 987654.32,
    }


def adapter(http_get):
    return HiThinkSnapshotAdapter(
        api_key="synthetic-key",
        http_get=http_get,
        clock=lambda: datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc),
    )


def test_stock_snapshot_uses_exact_single_symbol_and_official_units():
    calls = []

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return Response({"code": 0, "data": {"timestamp": None, "item": [item("600519.SH")]}})

    result = adapter(get).fetch_quote("600519.SH", "STOCK")

    assert calls == [
        (
            (HITHINK_STOCK_SNAPSHOT_URL,),
            {
                "params": {"thscodes": "600519.SH"},
                "headers": {"X-api-key": "synthetic-key", "Accept": "application/json"},
                "timeout": 4.5,
                "allow_redirects": False,
            },
        )
    ]
    assert result["source"] == f"hithink/{HITHINK_STOCK_SNAPSHOT_SOURCE}"
    assert result["provider_timestamp"] is None
    assert result["fetched_at"] == "2026-09-28T10:00:00+00:00"
    assert result["units"] == {
        "price_currency": "CNY",
        "volume": "share",
        "turnover_currency": "CNY",
    }
    assert result["historical_visibility_verified"] is False


def test_etf_snapshot_uses_independent_endpoint_and_keeps_unproven_units_unknown():
    calls = []

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return Response({
            "code": 0,
            "data": {"timestamp": 1_800_000_000_000, "item": [item("510300.SH")]},
        })

    result = adapter(get).fetch_quote("510300.SH", "ETF")

    assert calls[0][0] == (HITHINK_ETF_SNAPSHOT_URL,)
    assert calls[0][1]["params"] == {"thscode": "510300.SH"}
    assert result["source"] == f"hithink/{HITHINK_ETF_SNAPSHOT_SOURCE}"
    assert result["provider_timestamp"] == datetime.fromtimestamp(
        1_800_000_000, tz=timezone.utc
    ).isoformat()
    assert result["units"] == {
        "price_currency": "CNY",
        "volume": "unknown",
        "turnover_currency": "unknown",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 0, "data": {"timestamp": None, "item": []}},
        {"code": 0, "data": {"timestamp": None, "item": [item("600519.SH"), item("600519.SH")]}},
        {"code": 0, "data": {"timestamp": None, "item": [item("000001.SZ")]}},
    ],
)
def test_single_symbol_snapshot_rejects_missing_duplicate_or_wrong_identity(payload):
    with pytest.raises(HiThinkQuoteError, match="快照"):
        adapter(lambda *_args, **_kwargs: Response(payload)).fetch_quote("600519.SH", "STOCK")


def test_stock_and_etf_symbol_contracts_do_not_bleed():
    a = adapter(lambda *_args, **_kwargs: Response({}))
    with pytest.raises(HiThinkQuoteError, match="股票快照代码"):
        a.fetch_quote("510300.OF", "STOCK")
    with pytest.raises(HiThinkQuoteError, match="ETF 快照代码"):
        a.fetch_quote("430001.BJ", "ETF")


@pytest.mark.parametrize(
    "status,payload,code,retryable",
    [
        (200, {"code": 2001, "data": None}, "authentication_failed", False),
        (200, {"code": 2003, "data": None}, "permission_denied", False),
        (200, {"code": 4001, "data": None}, "rate_limited", True),
        (503, {"code": 5001}, "upstream_failure", True),
    ],
)
def test_snapshot_errors_are_stable_and_do_not_surface_provider_body(
    status, payload, code, retryable
):
    with pytest.raises(HiThinkQuoteError) as raised:
        adapter(lambda *_args, **_kwargs: Response(payload, status)).fetch_quote(
            "600519.SH", "STOCK"
        )
    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert "synthetic-key" not in str(raised.value)


def test_nonfinite_or_invalid_ohlc_is_rejected():
    bad = item("600519.SH")
    bad["last_price"] = float("inf")
    with pytest.raises(HiThinkQuoteError, match="last_price"):
        adapter(lambda *_args, **_kwargs: Response(
            {"code": 0, "data": {"timestamp": None, "item": [bad]}}
        )).fetch_quote("600519.SH", "STOCK")
