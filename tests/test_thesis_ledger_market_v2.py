"""DSA BarSeries 与纯指标计算 V2 回归。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from api.thesis_ledger import (
    _bar_series_fingerprint,
    market_bars_v2,
    calculate_indicators_v2,
)
from starlette.requests import Request

IDENTITY = {"symbol": "510300.SH", "assetType": "ETF", "timeframe": "1d", "adjustment": "qfq"}


def _request():
    return Request({"type": "http", "headers": []})


def _points(count: int = 30):
    start = datetime(2025, 1, 1, 7, tzinfo=timezone.utc)
    return [
        {
            "timestamp": (start + timedelta(days=index)).isoformat(),
            "open": 10 + index,
            "high": 11 + index,
            "low": 9 + index,
            "close": 10.5 + index,
            "volume": 100 + index,
            "amount": 1000 + index,
            "completionStatus": "complete",
            "availableAt": (start + timedelta(days=index, hours=1)).isoformat(),
        }
        for index in range(count)
    ]


def test_v2_indicator_calculation_is_batch_and_provider_free():
    points = _points()
    assert _bar_series_fingerprint(
        [{**points[0], "open": 1, "high": 1.1, "low": 0.9, "close": 1, "volume": 100, "amount": 1000, "timestamp": "2025-01-02T07:00:00.000Z", "availableAt": "2025-01-02T08:00:00.000Z"}],
        IDENTITY,
    ) == "9f35a1b9f4dba8483e6acb2b6214ec2fbaf5d450b91096ce591ada057623cfc5"
    result = calculate_indicators_v2(
        {
            "contractVersion": 2,
            "identity": IDENTITY,
            "inputFingerprint": _bar_series_fingerprint(points, IDENTITY),
            "points": points,
            "requests": [
                {"name": "MA", "parameters": {"period": 5}},
                {"name": "MACD", "parameters": {"fast": 3, "slow": 6, "signal": 3}},
                {"name": "RSI", "parameters": {"short": 3, "mid": 5, "long": 8}},
            ],
        }
    )
    assert result["contractVersion"] == 2
    assert [item["name"] for item in result["results"]] == ["MA", "MACD", "RSI"]
    assert all(item["inputFingerprint"] == result["inputFingerprint"] for item in result["results"])


def test_v2_fingerprint_keeps_exponent_numbers_cross_runtime_stable():
    point = {
        "timestamp": "2025-01-02T07:00:00.000Z",
        "open": 1e20,
        "high": 1e20,
        "low": 1e-7,
        "close": 0.1,
        "volume": 1e20,
        "amount": 1e20,
        "completionStatus": "complete",
        "availableAt": "2025-01-02T08:00:00.000Z",
    }
    assert _bar_series_fingerprint([point], IDENTITY) == (
        "bb4a7c6018e1627dbb74cd75ccfece58e85cd8e16c6688d8820756b34bb595d8"
    )


def test_v2_indicator_rejects_mismatched_fingerprint():
    points = _points()
    try:
        calculate_indicators_v2(
            {
                "contractVersion": 2,
                "identity": IDENTITY,
                "inputFingerprint": "wrong",
                "points": points,
                "requests": [{"name": "MA", "parameters": {"period": 5}}],
            }
        )
    except Exception as error:
        assert "inputFingerprint" in str(error.detail["message"])
    else:
        raise AssertionError("mismatched fingerprint must fail")


def test_v2_bars_preserve_adjustment_and_session_completion(monkeypatch):
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "1")
    result = market_bars_v2(_request(), symbol="510300.SH", assetType="ETF", timeframe="1d", adjustment="qfq", start=None, end=None, limit=2)
    assert result["identity"]["adjustment"] == "qfq"
    assert all(point["completionStatus"] == "complete" for point in result["points"])
    assert all(point["availableAt"] != result["provenance"]["fetchedAt"] for point in result["points"])
    assert result["inputFingerprint"] == _bar_series_fingerprint(result["points"], result["identity"])


def test_v2_bars_preserve_supported_hfq_adjustment(monkeypatch):
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "1")
    result = market_bars_v2(_request(), symbol="510300.SH", assetType="ETF", timeframe="1d", adjustment="hfq", start=None, end=None, limit=2)
    assert result["identity"]["adjustment"] == "hfq"
    assert result["provenance"]["providerId"] == "akshare"


@pytest.mark.parametrize(
    ("start", "end", "expected_start", "expected_end"),
    [
        (
            "2023-11-09T00:00:00.000Z",
            "2024-03-29T23:59:59.999Z",
            "2023-11-09",
            "2024-03-29",
        ),
        ("2023-11-09", "2024-03-29", "2023-11-09", "2024-03-29"),
    ],
)
def test_v2_bars_normalize_provider_window_dates(
    monkeypatch, start, end, expected_start, expected_end
):
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "0")
    captured = {}

    class Gateway:
        def bars(self, symbol, **kwargs):
            captured.update(kwargs)
            frame = pd.DataFrame(
                [
                    {
                        "date": "2024-01-02",
                        "open": 10,
                        "high": 11,
                        "low": 9,
                        "close": 10.5,
                        "volume": 100,
                        "amount": 1000,
                    }
                ]
            )
            frame.attrs["upstream_source"] = "tencent"
            return SimpleNamespace(
                data=frame,
                provider="akshare",
                fallback_used=False,
                route_index=0,
                effective_revision=23,
                provider_revision="test-provider-v1",
            )

    monkeypatch.setattr(
        "src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway",
        lambda: Gateway(),
    )
    result = market_bars_v2(
        _request(),
        symbol="600519.SH",
        assetType="STOCK",
        timeframe="1d",
        adjustment="none",
        start=start,
        end=end,
        limit=10,
    )
    assert captured["start"] == expected_start
    assert captured["end"] == expected_end
    assert result["identity"]["symbol"] == "600519.SH"


def test_v2_bars_reject_invalid_provider_window_date(monkeypatch):
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "0")
    with pytest.raises(Exception) as raised:
        market_bars_v2(
            _request(),
            symbol="600519.SH",
            assetType="STOCK",
            timeframe="1d",
            adjustment="none",
            start="not-a-date",
            end="2024-03-29",
            limit=10,
        )
    assert raised.value.status_code == 422
    assert raised.value.detail["code"] == "invalid_request"
