"""Synthetic contract tests for the HiThink stock historical adapter."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import requests

from src.services.thesis_ledger_hithink_stock import (
    HITHINK_STOCK_HISTORICAL_URL,
    HiThinkStockError,
    HiThinkStockHistoricalAdapter,
)

API_KEY = "synthetic-secret-never-print"
START = "2025-01-06"
END = "2025-01-07"
SESSIONS = (START, END)


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload


def _date_ms(day_text: str) -> int:
    local_midnight = datetime.combine(
        date.fromisoformat(day_text), datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai")
    )
    return int(local_midnight.timestamp() * 1000)


def _item(day_text: str, *, adjustment_offset: str = "0") -> dict[str, Any]:
    offset = Decimal(adjustment_offset)
    return {
        "date_ms": _date_ms(day_text),
        "open_price": str(Decimal("10.00") + offset),
        "high_price": str(Decimal("10.50") + offset),
        "low_price": str(Decimal("9.50") + offset),
        "close_price": str(Decimal("10.25") + offset),
        "volume": 120000,
        "turnover": 1234500.50,
    }


def _payload(
    adjustment: str | None = "none",
    *,
    items: list[dict[str, Any]] | None = None,
    code: int = 0,
    request_id: str = "fixture-request-1",
    message: str = "fixture response",
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "request_id": request_id,
        "data": {
            "timestamp": _date_ms(END),
            "adjust": adjustment,
            "item": items if items is not None else [_item(START), _item(END)],
        },
    }


def _adapter(payload: Any, *, status_code: int = 200, key: str = API_KEY):
    seen: list[dict[str, Any]] = []

    def http_get(url: str, **kwargs: Any) -> FakeResponse:
        seen.append({"url": url, **kwargs})
        return FakeResponse(payload, status_code=status_code)

    adapter = HiThinkStockHistoricalAdapter(
        key,
        http_get=http_get,
        clock=lambda: datetime(2025, 1, 8, 12, tzinfo=timezone.utc),
    )
    return adapter, seen


@pytest.mark.parametrize("adjustment,echo", [("none", "none"), ("qfq", "forward"), ("hfq", "backward")])
@pytest.mark.parametrize("location", ["envelope", "data"])
@pytest.mark.parametrize("marker,value", [("hasMore", True), ("next_page", 2), ("pagination", {"cursor": "next"})])
def test_stock_rejects_pagination_even_when_current_page_covers_sessions(adjustment, echo, location, marker, value):
    payload = _payload(echo)
    container = payload if location == "envelope" else payload["data"]
    container[marker] = value
    adapter, calls = _adapter(payload)
    with pytest.raises(HiThinkStockError) as failure:
        _fetch(adapter, adjustment=adjustment)
    assert failure.value.code == "incomplete_coverage"
    assert len(calls) == 1


def _fetch(adapter: HiThinkStockHistoricalAdapter, **overrides: Any):
    values = {
        "symbol": "000001.SZ",
        "start": START,
        "end": END,
        "adjustment": "none",
        "expected_sessions": SESSIONS,
        "calendar_revision": "XSHE-fixture-2025.01",
    }
    values.update(overrides)
    return adapter.fetch_daily_bars(**values)


@pytest.mark.parametrize(
    ("adjustment", "upstream", "offset"),
    [
        ("none", "none", "0"),
        ("qfq", "forward", "1"),
        ("hfq", "backward", "2"),
    ],
)
def test_stock_adjustments_map_explicitly_and_keep_independent_series(
    adjustment: str, upstream: str, offset: str
):
    response = _payload(
        upstream,
        items=[_item(START, adjustment_offset=offset), _item(END, adjustment_offset=offset)],
    )
    adapter, calls = _adapter(response)

    frame = _fetch(adapter, adjustment=adjustment)

    assert len(calls) == 1
    assert calls[0]["url"] == HITHINK_STOCK_HISTORICAL_URL
    assert calls[0]["params"]["thscode"] == "000001.SZ"
    assert calls[0]["params"]["interval"] == "1d"
    assert calls[0]["params"]["adjust"] == upstream
    assert calls[0]["params"]["offset"] == 0
    assert set(frame["date"]) == set(SESSIONS)
    assert frame.attrs["thesis_ledger_source"]["adjustment"] == adjustment
    assert frame.attrs["thesis_ledger_source"]["source_price_basis"]["adjustment"] == adjustment
    assert frame.attrs["thesis_ledger_source"]["source_price_basis"]["anchor"] is None
    assert frame.attrs["thesis_ledger_source"]["source_price_basis"]["basis_scope"] == (
        "provider-defined"
    )
    assert frame.attrs["thesis_ledger_source"]["source_price_basis"]["method_version"] == (
        "provider-defined-unversioned"
    )


def test_shanghai_inclusive_boundaries_and_units_are_preserved_without_scaling():
    adapter, calls = _adapter(_payload())

    frame = _fetch(adapter)

    params = calls[0]["params"]
    assert datetime.fromtimestamp(params["start"] / 1000, tz=timezone.utc).isoformat() == (
        "2025-01-05T16:00:00+00:00"
    )
    assert datetime.fromtimestamp(params["end"] / 1000, tz=timezone.utc).isoformat() == (
        "2025-01-07T15:59:59.999000+00:00"
    )
    assert frame.loc[0, "volume"] == Decimal("120000")
    assert frame.loc[0, "amount"] == Decimal("1234500.5")
    metadata = frame.attrs["thesis_ledger_source"]
    assert metadata["units"] == {
        "price_currency": "CNY",
        "volume": "share",
        "turnover_currency": "CNY",
        "turnover_source_field": "turnover",
        "volume_adjustment_basis": "unknown",
        "turnover_adjustment_basis": "unknown",
    }
    assert metadata["source_price_basis"]["volume_basis"] == "unknown"
    assert metadata["source_price_basis"]["dividend_meaning"] == "provider-defined"
    assert metadata["source_price_basis"]["conversion_available"] is False


def test_target_calendar_proof_is_required_and_covered_exactly():
    adapter, _ = _adapter(_payload(items=[_item(START)]))

    with pytest.raises(HiThinkStockError) as missing:
        _fetch(adapter)

    assert missing.value.code == "incomplete_coverage"
    assert str(missing.value) == "HiThink 股票行情缺少目标市场交易日"

    adapter, _ = _adapter(_payload())
    with pytest.raises(HiThinkStockError) as no_calendar:
        _fetch(adapter, calendar_revision="")
    assert no_calendar.value.code == "invalid_request"


@pytest.mark.parametrize(
    ("items", "expected_code"),
    [
        ([_item(START), _item(START)], "duplicate_data"),
        ([_item(START), _item(END), _item("2025-01-08")], "invalid_response"),
        ([_item(START), {**_item(END), "high_price": "9.75"}], "invalid_response"),
    ],
)
def test_duplicate_out_of_window_and_invalid_bars_fail_closed(
    items: list[dict[str, Any]], expected_code: str
):
    adapter, _ = _adapter(_payload(items=items))

    with pytest.raises(HiThinkStockError) as error:
        _fetch(adapter)

    assert error.value.code == expected_code


def test_unknown_request_and_response_adjustment_labels_are_rejected():
    adapter, calls = _adapter(_payload())
    with pytest.raises(HiThinkStockError) as unknown_request:
        _fetch(adapter, adjustment="forward")
    assert unknown_request.value.code == "unsupported_adjustment"
    assert calls == []

    adapter, _ = _adapter(_payload("split-adjusted"))
    with pytest.raises(HiThinkStockError) as unknown_echo:
        _fetch(adapter, adjustment="qfq")
    assert unknown_echo.value.code == "invalid_response"

    adapter, _ = _adapter(_payload(None))
    with pytest.raises(HiThinkStockError) as missing_echo:
        _fetch(adapter)
    assert missing_echo.value.code == "invalid_response"

    adapter, calls = _adapter(_payload())
    with pytest.raises(HiThinkStockError) as unknown_request:
        _fetch(adapter, adjustment="split-adjusted")
    assert unknown_request.value.code == "unsupported_adjustment"
    assert calls == []


@pytest.mark.parametrize(
    ("business_code", "expected_code", "retryable"),
    [
        (2001, "authentication_failed", False),
        (2003, "permission_denied", False),
        (3004, "unsupported_capability", False),
        (4001, "rate_limited", True),
        (5002, "upstream_failure", True),
    ],
)
def test_business_errors_are_classified_without_echoing_provider_body(
    business_code: int, expected_code: str, retryable: bool
):
    adapter, _ = _adapter(
        _payload(code=business_code, message=f"contains {API_KEY}"),
    )

    with pytest.raises(HiThinkStockError) as error:
        _fetch(adapter)

    assert error.value.code == expected_code
    assert error.value.retryable is retryable
    assert error.value.request_id == "fixture-request-1"
    assert API_KEY not in str(error.value)
    assert API_KEY not in repr(error.value)


@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (401, "authentication_failed", False),
        (403, "permission_denied", False),
        (429, "rate_limited", True),
        (503, "upstream_failure", True),
    ],
)
def test_http_errors_are_classified(status_code: int, expected_code: str, retryable: bool):
    adapter, _ = _adapter(_payload(), status_code=status_code)

    with pytest.raises(HiThinkStockError) as error:
        _fetch(adapter)

    assert error.value.code == expected_code
    assert error.value.retryable is retryable


def test_network_failure_and_bad_envelope_are_safe_and_fail_closed():
    def failed_get(_url: str, **_kwargs: Any) -> FakeResponse:
        raise requests.Timeout(f"transport detail {API_KEY}")

    adapter = HiThinkStockHistoricalAdapter(API_KEY, http_get=failed_get)
    with pytest.raises(HiThinkStockError) as network:
        _fetch(adapter)
    assert network.value.code == "network_failure"
    assert network.value.retryable is True
    assert API_KEY not in str(network.value)

    adapter, _ = _adapter({"code": 0, "request_id": "fixture-request-2"})
    with pytest.raises(HiThinkStockError) as malformed:
        _fetch(adapter)
    assert malformed.value.code == "invalid_response"


def test_local_calendar_dates_symbol_and_range_are_validated_before_request():
    adapter, calls = _adapter(_payload())

    invalid_cases = [
        {"symbol": "000001"},
        {"start": "2025-1-6"},
        {"end": "2025-01-05"},
        {"start": "2020-01-06", "end": "2030-01-06", "expected_sessions": ()},
    ]
    for overrides in invalid_cases:
        with pytest.raises(HiThinkStockError):
            _fetch(adapter, **overrides)
    assert calls == []


def test_credentials_are_injected_only_as_header_and_fingerprint_excludes_them():
    adapter, calls = _adapter(_payload())

    frame = _fetch(adapter)

    assert calls[0]["headers"]["X-api-key"] == API_KEY
    assert API_KEY not in str(calls[0]["params"])
    metadata = frame.attrs["thesis_ledger_source"]
    assert len(metadata["content_fingerprint"]) == 64
    assert API_KEY not in metadata["content_fingerprint"]
    assert metadata["source_price_basis"]["revision"]["content_hash"] == metadata[
        "content_fingerprint"
    ]
