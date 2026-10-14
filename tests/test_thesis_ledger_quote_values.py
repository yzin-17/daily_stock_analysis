"""个人研究 Quote 非必要字段的确定性降级。"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api.thesis_ledger_quote_fields import quote_public_fields
from src.services.thesis_ledger_hithink_quote import HiThinkSnapshotAdapter, HiThinkQuoteError
from src.services.thesis_ledger_quote_values import optional_quote_number


@pytest.mark.parametrize("value", [None, "", "--", True, False, float("nan"), float("inf"), -1, {}, 10 ** 1000])
def test_unknown_or_invalid_unsigned_value_is_null(value):
    assert optional_quote_number(value) is None


@pytest.mark.parametrize("value,expected", [(0, 0), ("0", 0), ("123.45", 123.45), (12, 12)])
def test_known_zero_and_numbers_are_preserved(value, expected):
    assert optional_quote_number(value) == expected


def test_signed_change_can_be_negative_without_inventing_a_missing_change():
    assert optional_quote_number(-1.5, signed=True) == -1.5
    assert optional_quote_number(None, signed=True) is None


def test_public_projection_does_not_invent_optional_facts():
    quote = SimpleNamespace(open_price=10, high=11, low=9, price=10, pre_close=9.5)
    result = quote_public_fields(quote, lambda value, _field: float(value))
    assert result == {"open": 10, "high": 11, "low": 9, "price": 10,
                      "previousClose": 9.5, "volume": None, "amount": None}


@pytest.mark.parametrize("asset,symbol", [("ETF", "510300.SH"), ("STOCK", "600519.SH")])
def test_real_adapter_missing_optional_fields_keeps_price_and_time(asset, symbol):
    item = {"thscode": symbol, "last_price": 10, "open_price": 10,
            "high_price": 11, "low_price": 9, "prev_price": 9.5}
    response = SimpleNamespace(status_code=200, json=lambda: {
        "code": 0, "data": {"timestamp": None, "item": [item]},
    })
    adapter = HiThinkSnapshotAdapter(
        api_key="synthetic-key", http_get=lambda *_a, **_k: response,
        clock=lambda: datetime(2026, 10, 6, 8, tzinfo=timezone.utc),
    )
    result = adapter.fetch_quote(symbol, asset)
    assert result["price"] == 10
    assert result["provider_timestamp"] is None
    assert result["fetched_at"] == "2026-10-06T08:00:00+00:00"
    for field in ["volume", "amount", "change_amount", "change_pct"]:
        assert result[field] is None
    item["last_price"] = 0
    with pytest.raises(HiThinkQuoteError):
        adapter.fetch_quote(symbol, asset)
