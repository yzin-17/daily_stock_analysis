from datetime import datetime, timezone
from email.utils import formatdate
from hashlib import sha256

import pytest

from src.services.thesis_ledger_hithink_quote import (
    HITHINK_ETF_SNAPSHOT_SOURCE,
    HITHINK_ETF_SNAPSHOT_URL,
    HITHINK_STOCK_SNAPSHOT_SOURCE,
    HITHINK_STOCK_SNAPSHOT_URL,
    HiThinkQuoteError,
    HiThinkSnapshotAdapter,
)
from src.services import thesis_ledger_hithink_quote_budget as budget_module


class Response:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers

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
        budget=budget_module.HiThinkQuoteBudget(clock=lambda: 0),
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
        (200, {"code": 4001, "data": None}, "rate_limited", False),
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


@pytest.mark.parametrize("limited", ["fractional-quota", "seconds", "http-date", "business"])
def test_stock_and_etf_share_observed_quota_and_retry_hint(monkeypatch, limited):
    now = [0.0]
    wall = 1_800_000_000
    budget = budget_module.HiThinkQuoteBudget(clock=lambda: now[0], wall_clock=lambda: wall + now[0])
    key = "shared-test-key"
    monkeypatch.setattr(budget_module, "_budgets", {sha256(key.encode()).digest(): budget})
    calls = []

    def quote(symbol):
        return {"code": 0, "data": {"timestamp": None, "item": [item(symbol)]}}

    def get(url, *, params, **_kwargs):
        calls.append(url)
        if url == HITHINK_ETF_SNAPSHOT_URL:
            return Response(quote(params["thscode"]), headers={"X-RateLimit-Remaining": "14.81"})
        if limited == "fractional-quota":
            return Response(quote(params["thscodes"]), headers={
                "X-RateLimit-Limit": "15", "x-ratelimit-remaining": "0.81",
            })
        if limited == "business":
            return Response({"code": 4001}, headers={"Retry-After": "7"})
        retry = "7" if limited == "seconds" else formatdate(wall + 7, usegmt=True)
        return Response("<html>", 429, {"retry-after": retry})

    stock = HiThinkSnapshotAdapter(api_key=key, http_get=get)
    etf = HiThinkSnapshotAdapter(api_key=key, http_get=get)
    other = HiThinkSnapshotAdapter(api_key="independent-test-key", http_get=get)
    if limited == "fractional-quota":
        assert stock.fetch_quote("600519.SH", "STOCK")["price"] == 10.5
    else:
        with pytest.raises(HiThinkQuoteError) as first:
            stock.fetch_quote("600519.SH", "STOCK")
        assert (first.value.code, first.value.retryable) == ("rate_limited", False)
    resume_at = 1 if limited == "fractional-quota" else 7
    for before in (0, resume_at - 0.1):
        now[0] = before
        with pytest.raises(HiThinkQuoteError) as blocked:
            etf.fetch_quote("510300.SH", "ETF")
        assert (blocked.value.code, blocked.value.retryable) == ("rate_limited", False)
    assert calls == [HITHINK_STOCK_SNAPSHOT_URL]
    assert other.fetch_quote("510300.SH", "ETF")["price"] == 10.5
    now[0] = resume_at
    assert etf.fetch_quote("510300.SH", "ETF")["price"] == 10.5
    assert etf.fetch_quote("510300.SH", "ETF")["price"] == 10.5
    assert calls == [HITHINK_STOCK_SNAPSHOT_URL] + [HITHINK_ETF_SNAPSHOT_URL] * 3


def test_malformed_retry_hint_backs_off_each_unsuccessful_probe():
    now = [0.0]
    calls = []

    def get(*_args, **_kwargs):
        calls.append(now[0])
        return Response({"code": 4001}, headers={"Retry-After": "unknown"})

    snapshot = HiThinkSnapshotAdapter(
        api_key="synthetic-key", http_get=get, budget=budget_module.HiThinkQuoteBudget(clock=lambda: now[0]),
    )
    for t in (0, 0.5, 1, 2, 3, 6, 7):
        now[0] = t
        with pytest.raises(HiThinkQuoteError) as raised:
            snapshot.fetch_quote("600519.SH", "STOCK")
        assert raised.value.code == "rate_limited"
    assert calls == [0, 1, 3, 7]
