"""当前图表使用的纯指标计算合同回归。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from api.thesis_ledger import (
    _bar_series_fingerprint,
    calculate_indicators,
)

IDENTITY = {"symbol": "510300.SH", "assetType": "ETF", "timeframe": "1d", "adjustment": "qfq"}


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


def test_indicator_calculation_is_batch_and_provider_free():
    points = _points()
    assert _bar_series_fingerprint(
        [{**points[0], "open": 1, "high": 1.1, "low": 0.9, "close": 1, "volume": 100, "amount": 1000, "timestamp": "2025-01-02T07:00:00.000Z", "availableAt": "2025-01-02T08:00:00.000Z"}],
        IDENTITY,
    ) == "9f35a1b9f4dba8483e6acb2b6214ec2fbaf5d450b91096ce591ada057623cfc5"
    result = calculate_indicators(
        {
            "contractVersion": 3,
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
    assert result["contractVersion"] == 3
    assert [item["name"] for item in result["results"]] == ["MA", "MACD", "RSI"]
    assert all(item["inputFingerprint"] == result["inputFingerprint"] for item in result["results"])


def test_fingerprint_keeps_exponent_numbers_cross_runtime_stable():
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


def test_indicator_rejects_mismatched_fingerprint():
    points = _points()
    try:
        calculate_indicators(
            {
                "contractVersion": 3,
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


def test_indicator_rejects_old_contract_before_calculation():
    try:
        calculate_indicators({"contractVersion": 2})
    except Exception as error:
        assert "Contract V3" in str(error.detail["message"])
    else:
        raise AssertionError("old indicator contract must fail")
