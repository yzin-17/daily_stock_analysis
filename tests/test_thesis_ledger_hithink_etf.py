from __future__ import annotations

import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from src.services.thesis_ledger_hithink_etf import (
    HITHINK_ETF_HISTORICAL_URL,
    HiThinkETFAdapterError,
    HiThinkETFCalendarEvidence,
    fetch_hithink_etf_daily_bars,
    parse_hithink_etf_daily_response,
)


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SYMBOL = "159516.SZ"
_API_KEY = "synthetic-fixture-key"


def _date_ms(value: str) -> int:
    local_midnight = datetime.combine(date.fromisoformat(value), time.min, tzinfo=_SHANGHAI)
    return int(local_midnight.timestamp() * 1000)


def _item(value: str, offset: float = 0.0) -> dict[str, int | float]:
    return {
        "date_ms": _date_ms(value),
        "volume": 120000 + int(offset),
        "turnover": 156000 + int(offset),
        "open_price": 1.29 + offset,
        "high_price": 1.32 + offset,
        "low_price": 1.28 + offset,
        "close_price": 1.31 + offset,
    }


@pytest.fixture
def calendar_evidence() -> HiThinkETFCalendarEvidence:
    return HiThinkETFCalendarEvidence(
        symbol=_SYMBOL,
        calendar_name="XSHG",
        calendar_version="exchange_calendars 4.13.2",
        market_sessions=("2026-05-18", "2026-05-19", "2026-05-20"),
        listing_date="2023-07-27",
        listing_source="SZSE listing notice t20230724_602100",
        listing_source_version="2023-07-24",
    )


@pytest.fixture
def successful_payload() -> dict:
    items = [_item("2026-05-18"), _item("2026-05-19", 0.01), _item("2026-05-20", 0.02)]
    return {
        "code": 0,
        "message": "success",
        "request_id": "synthetic-request-id",
        "data": {
            "timestamp": _date_ms("2026-05-20"),
            "thscode": _SYMBOL,
            "interval": "1d",
            "adjust": None,
            "item": items,
        },
    }


class _Response:
    def __init__(self, payload: object, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.content = json.dumps(payload, sort_keys=True).encode("utf-8")

    def json(self) -> object:
        return self.payload


def test_fetch_maps_documented_etf_adjust_null_to_qfq_and_uses_shanghai_date_bounds(
    successful_payload: dict, calendar_evidence: HiThinkETFCalendarEvidence
):
    calls: list[dict] = []

    def fake_get(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Response(successful_payload)

    result = fetch_hithink_etf_daily_bars(
        symbol=_SYMBOL,
        start="2026-05-16",
        end="2026-05-20",
        adjustment="qfq",
        api_key=_API_KEY,
        calendar_evidence=calendar_evidence,
        http_get=fake_get,
        timeout_seconds=4.5,
    )

    assert len(calls) == 1
    assert calls[0]["url"] == HITHINK_ETF_HISTORICAL_URL
    assert calls[0]["params"] == {
        "thscode": _SYMBOL,
        "interval": "1d",
        "start": _date_ms("2026-05-16"),
        "end": _date_ms("2026-05-20"),
    }
    assert calls[0]["headers"] == {"X-api-key": _API_KEY}
    assert calls[0]["timeout"] == 4.5
    assert calls[0]["allow_redirects"] is False
    assert result.adjustment == "qfq"
    assert result.adjustment_method == "provider-native"
    assert result.adjustment_method_version is None
    assert result.basis_scope == "provider-defined"
    assert result.adjustment_anchor is None
    assert result.volume_unit == "unknown"
    assert result.turnover_unit == "unknown"
    assert result.bars[0].date == "2026-05-18"
    assert result.bars[0].amount == 156000
    assert result.coverage["coverageStatus"] == "complete"
    assert result.coverage["expectedSessionCount"] == 3
    assert result.coverage["prelistingSessionCount"] == 0
    assert result.coverage["windowSemantics"] == "single_request_only"
    assert result.coverage["maximumWindowYears"] == 5
    assert len(result.response_fingerprint) == 64
    assert _API_KEY not in repr(result)


@pytest.mark.parametrize("adjustment", ["none", "hfq"])
def test_raw_and_backward_adjusted_etf_requests_are_rejected_before_http(
    adjustment: str, calendar_evidence: HiThinkETFCalendarEvidence
):
    called = False

    def fake_get(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("HTTP must not be called")

    with pytest.raises(HiThinkETFAdapterError) as caught:
        fetch_hithink_etf_daily_bars(
            symbol=_SYMBOL,
            start="2026-05-16",
            end="2026-05-20",
            adjustment=adjustment,
            api_key=_API_KEY,
            calendar_evidence=calendar_evidence,
            http_get=fake_get,
        )

    assert caught.value.code == "unsupported_adjustment"
    assert called is False


def test_market_calendar_excludes_weekends_and_listing_evidence_excludes_prelisting_sessions():
    evidence = HiThinkETFCalendarEvidence(
        symbol=_SYMBOL,
        calendar_name="XSHG",
        calendar_version="exchange_calendars 4.13.2",
        market_sessions=(
            "2023-07-24",
            "2023-07-25",
            "2023-07-26",
            "2023-07-27",
            "2023-07-28",
        ),
        listing_date="2023-07-27",
        listing_source="SZSE listing notice t20230724_602100",
        listing_source_version="2023-07-24",
    )
    payload = {
        "code": 0,
        "data": {
            "timestamp": _date_ms("2023-07-28"),
            "thscode": _SYMBOL,
            "interval": "1d",
            "adjust": None,
            "item": [_item("2023-07-27"), _item("2023-07-28", 0.01)],
        },
    }

    result = parse_hithink_etf_daily_response(
        payload,
        symbol=_SYMBOL,
        start="2023-07-22",
        end="2023-07-30",
        calendar_evidence=evidence,
    )

    assert [bar.date for bar in result.bars] == ["2023-07-27", "2023-07-28"]
    assert result.coverage["prelistingSessionCount"] == 3
    assert result.coverage["expectedSessionCount"] == 2
    assert result.coverage["missingSessionCount"] == 0


def test_weekend_only_window_is_distinct_from_a_missing_trading_day():
    evidence = HiThinkETFCalendarEvidence(
        symbol=_SYMBOL,
        calendar_name="XSHG",
        calendar_version="exchange_calendars 4.13.2",
        market_sessions=(),
        listing_date="2023-07-27",
        listing_source="SZSE listing notice t20230724_602100",
        listing_source_version="2023-07-24",
    )
    payload = {
        "code": 0,
        "data": {
            "timestamp": None,
            "thscode": _SYMBOL,
            "interval": "1d",
            "adjust": None,
            "item": [],
        },
    }

    result = parse_hithink_etf_daily_response(
        payload,
        symbol=_SYMBOL,
        start="2026-05-16",
        end="2026-05-17",
        calendar_evidence=evidence,
    )

    assert result.bars == ()
    assert result.coverage["coverageStatus"] == "no_expected_sessions"
    assert result.coverage["missingSessionCount"] == 0


def test_missing_expected_session_fails_with_machine_readable_calendar_summary(
    calendar_evidence: HiThinkETFCalendarEvidence,
):
    payload = {
        "code": 0,
        "data": {
            "timestamp": _date_ms("2026-05-18"),
            "thscode": _SYMBOL,
            "interval": "1d",
            "adjust": None,
            "item": [_item("2026-05-18")],
        },
    }

    with pytest.raises(HiThinkETFAdapterError) as caught:
        parse_hithink_etf_daily_response(
            payload,
            symbol=_SYMBOL,
            start="2026-05-18",
            end="2026-05-20",
            calendar_evidence=calendar_evidence,
        )

    assert caught.value.code == "incomplete_coverage"
    assert caught.value.diagnostics["coverageStatus"] == "missing_expected_sessions"
    assert caught.value.diagnostics["missingSessions"] == ["2026-05-19", "2026-05-20"]
    assert caught.value.diagnostics["calendarVersion"] == "exchange_calendars 4.13.2"
    assert caught.value.diagnostics["listingSourceVersion"] == "2023-07-24"
    assert len(caught.value.diagnostics["expectedSessionsFingerprint"]) == 64
    assert len(caught.value.response_fingerprint) == 64


def test_sparse_etf_source_read_preserves_missing_session_without_relaxing_price_read():
    symbol = "159515.SZ"
    evidence = HiThinkETFCalendarEvidence(
        symbol=symbol, calendar_name="SZSE", calendar_version="calendar-fixture-v1",
        market_sessions=("2026-07-29", "2026-07-30", "2026-07-31"),
        listing_date="2023-01-01", listing_source="listing-fixture",
        listing_source_version="listing-fixture-v1",
    )
    payload = {
        "code": 0,
        "data": {
            "timestamp": _date_ms("2026-07-31"), "thscode": symbol,
            "interval": "1d", "adjust": None,
            "item": [_item("2026-07-29"), _item("2026-07-31")],
        },
    }
    with pytest.raises(HiThinkETFAdapterError) as strict:
        parse_hithink_etf_daily_response(
            payload, symbol=symbol, start="2026-07-29", end="2026-07-31",
            calendar_evidence=evidence,
        )
    assert strict.value.code == "incomplete_coverage"

    series = fetch_hithink_etf_daily_bars(
        symbol=symbol, start="2026-07-29", end="2026-07-31",
        adjustment="qfq", api_key=_API_KEY, calendar_evidence=evidence,
        http_get=lambda *_args, **_kwargs: _Response(payload),
        allow_missing_sessions=True,
    )
    assert [bar.date for bar in series.bars] == ["2026-07-29", "2026-07-31"]
    assert series.missing_sessions == ("2026-07-30",)
    assert series.coverage["coverageStatus"] == "missing_expected_sessions"
    assert series.coverage["missingSessionCount"] == 1
    assert len(series.coverage["missingSessionsFingerprint"]) == 64
    assert len(series.response_fingerprint) == 64

    payload["data"]["hasMore"] = True
    with pytest.raises(HiThinkETFAdapterError) as paginated:
        fetch_hithink_etf_daily_bars(
            symbol=symbol, start="2026-07-29", end="2026-07-31",
            adjustment="qfq", api_key=_API_KEY, calendar_evidence=evidence,
            http_get=lambda *_args, **_kwargs: _Response(payload),
            allow_missing_sessions=True,
        )
    assert paginated.value.code == "pagination_unverified"

    del payload["data"]["hasMore"]
    payload["data"]["item"] = [_item("2026-07-29"), _item("2026-07-29")]
    with pytest.raises(HiThinkETFAdapterError) as duplicate:
        parse_hithink_etf_daily_response(
            payload, symbol=symbol, start="2026-07-29", end="2026-07-31",
            calendar_evidence=evidence, allow_missing_sessions=True,
        )
    assert duplicate.value.code == "duplicate_bar_date"

    payload["data"]["item"] = [_item("2026-07-29"), {
        **_item("2026-07-31"), "high_price": 0.1,
    }]
    with pytest.raises(HiThinkETFAdapterError) as malformed:
        parse_hithink_etf_daily_response(
            payload, symbol=symbol, start="2026-07-29", end="2026-07-31",
            calendar_evidence=evidence, allow_missing_sessions=True,
        )
    assert malformed.value.code == "invalid_response"


def test_sparse_fetch_requires_original_response_bytes(successful_payload, calendar_evidence):
    response = _Response(successful_payload)
    del response.content
    with pytest.raises(HiThinkETFAdapterError) as rejected:
        fetch_hithink_etf_daily_bars(
            symbol=_SYMBOL, start="2026-05-18", end="2026-05-20", adjustment="qfq",
            api_key=_API_KEY, calendar_evidence=calendar_evidence,
            http_get=lambda *_args, **_kwargs: response, allow_missing_sessions=True,
        )
    assert rejected.value.code == "invalid_response"


@pytest.mark.parametrize(
    "changed_field",
    ["missing_adjust", "non_null_adjust", "wrong_symbol", "wrong_interval", "wrong_timestamp"],
)
def test_response_identity_and_adjust_field_are_strict(
    changed_field: str,
    successful_payload: dict,
    calendar_evidence: HiThinkETFCalendarEvidence,
):
    data = dict(successful_payload["data"])
    if changed_field == "missing_adjust":
        data.pop("adjust")
    elif changed_field == "non_null_adjust":
        data["adjust"] = "qfq"
    elif changed_field == "wrong_symbol":
        data["thscode"] = "000001.SZ"
    elif changed_field == "wrong_interval":
        data["interval"] = "1w"
    elif changed_field == "wrong_timestamp":
        data["timestamp"] = _date_ms("2026-05-19")
    payload = {**successful_payload, "data": data}

    with pytest.raises(HiThinkETFAdapterError) as caught:
        parse_hithink_etf_daily_response(
            payload,
            symbol=_SYMBOL,
            start="2026-05-16",
            end="2026-05-20",
            calendar_evidence=calendar_evidence,
        )

    assert caught.value.code == "invalid_response"


@pytest.mark.parametrize(
    ("items", "expected_code"),
    [
        ([_item("2026-05-18"), _item("2026-05-18", 0.01)], "duplicate_bar_date"),
        ([_item("2026-05-17")], "calendar_mismatch"),
    ],
)
def test_duplicate_and_nontrading_rows_fail_closed(
    items: list[dict],
    expected_code: str,
    calendar_evidence: HiThinkETFCalendarEvidence,
):
    payload = {
        "code": 0,
        "data": {
            "timestamp": _date_ms("2026-05-18"),
            "thscode": _SYMBOL,
            "interval": "1d",
            "adjust": None,
            "item": items,
        },
    }

    with pytest.raises(HiThinkETFAdapterError) as caught:
        parse_hithink_etf_daily_response(
            payload,
            symbol=_SYMBOL,
            start="2026-05-18",
            end="2026-05-20",
            calendar_evidence=calendar_evidence,
        )

    assert caught.value.code == expected_code
    if expected_code == "calendar_mismatch":
        assert caught.value.diagnostics["unexpectedSessions"] == ["2026-05-17"]


@pytest.mark.parametrize(
    "changed_item",
    [
        {**_item("2026-05-18"), "high_price": 1.0},
        {**_item("2026-05-18"), "close_price": 0},
        {**_item("2026-05-18"), "volume": -1},
        {**_item("2026-05-18"), "turnover": float("nan")},
        {key: value for key, value in _item("2026-05-18").items() if key != "low_price"},
        {**_item("2026-05-18"), "date_ms": _date_ms("2026-05-18") + 60_000},
    ],
)
def test_invalid_price_units_and_trade_date_values_fail_closed(
    changed_item: dict,
    calendar_evidence: HiThinkETFCalendarEvidence,
):
    payload = {
        "code": 0,
        "data": {
            "timestamp": _date_ms("2026-05-18"),
            "thscode": _SYMBOL,
            "interval": "1d",
            "adjust": None,
            "item": [changed_item],
        },
    }

    with pytest.raises(HiThinkETFAdapterError) as caught:
        parse_hithink_etf_daily_response(
            payload,
            symbol=_SYMBOL,
            start="2026-05-18",
            end="2026-05-20",
            calendar_evidence=calendar_evidence,
        )

    assert caught.value.code == "invalid_response"


@pytest.mark.parametrize(
    ("provider_code", "expected_code", "retryable"),
    [(2001, "credential_required", False), (2003, "permission_denied", False),
     (3001, "unknown_instrument", False), (3002, "data_not_ready", False),
     (3004, "unsupported_instrument", False), (4001, "rate_limited", True),
     (5001, "upstream_unavailable", True)],
)
def test_official_business_error_codes_are_classified_without_provider_text(
    provider_code: int,
    expected_code: str,
    retryable: bool,
    calendar_evidence: HiThinkETFCalendarEvidence,
):
    payload = {"code": provider_code, "message": f"sensitive remote detail {_API_KEY}"}

    with pytest.raises(HiThinkETFAdapterError) as caught:
        parse_hithink_etf_daily_response(
            payload,
            symbol=_SYMBOL,
            start="2026-05-18",
            end="2026-05-20",
            calendar_evidence=calendar_evidence,
        )

    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable
    assert caught.value.provider_code == provider_code
    assert _API_KEY not in str(caught.value)
    assert _API_KEY not in repr(caught.value.diagnostics)


@pytest.mark.parametrize(
    "pagination_fields",
    [
        {"hasMore": True},
        {"pageToken": "synthetic-next-page"},
        {"pagination": {"next": "synthetic-next-page"}},
    ],
)
def test_explicit_pagination_markers_block_unverified_truncation(
    pagination_fields: dict,
    successful_payload: dict,
    calendar_evidence: HiThinkETFCalendarEvidence,
):
    payload = {
        **successful_payload,
        "data": {**successful_payload["data"], **pagination_fields},
    }

    with pytest.raises(HiThinkETFAdapterError) as caught:
        parse_hithink_etf_daily_response(
            payload,
            symbol=_SYMBOL,
            start="2026-05-16",
            end="2026-05-20",
            calendar_evidence=calendar_evidence,
        )

    assert caught.value.code == "pagination_unverified"
    assert caught.value.response_fingerprint is not None


def test_missing_listing_or_calendar_evidence_is_not_guessed(
    successful_payload: dict,
    calendar_evidence: HiThinkETFCalendarEvidence,
):
    with pytest.raises(HiThinkETFAdapterError) as missing:
        fetch_hithink_etf_daily_bars(
            symbol=_SYMBOL,
            start="2026-05-16",
            end="2026-05-20",
            adjustment="qfq",
            api_key=_API_KEY,
            calendar_evidence=None,
            http_get=lambda *_args, **_kwargs: pytest.fail("must not request without evidence"),
        )
    assert missing.value.code == "calendar_evidence_required"

    invalid_evidence = HiThinkETFCalendarEvidence(
        symbol=_SYMBOL,
        calendar_name="XSHG",
        calendar_version="exchange_calendars 4.13.2",
        market_sessions=calendar_evidence.market_sessions,
        listing_date="2023-07-27",
        listing_source="",
        listing_source_version="",
    )
    with pytest.raises(HiThinkETFAdapterError) as invalid:
        parse_hithink_etf_daily_response(
            successful_payload,
            symbol=_SYMBOL,
            start="2026-05-16",
            end="2026-05-20",
            calendar_evidence=invalid_evidence,
        )
    assert invalid.value.code == "invalid_calendar_evidence"


def test_provider_key_is_not_revealed_by_transport_error():
    def failing_get(*_args, **kwargs):
        raise RuntimeError(f"request failed with headers={kwargs['headers']}")

    evidence = HiThinkETFCalendarEvidence(
        symbol=_SYMBOL,
        calendar_name="XSHG",
        calendar_version="exchange_calendars 4.13.2",
        market_sessions=("2026-05-18",),
        listing_date="2023-07-27",
        listing_source="SZSE listing notice",
        listing_source_version="2023-07-24",
    )
    with pytest.raises(HiThinkETFAdapterError) as caught:
        fetch_hithink_etf_daily_bars(
            symbol=_SYMBOL,
            start="2026-05-18",
            end="2026-05-18",
            adjustment="qfq",
            api_key=_API_KEY,
            calendar_evidence=evidence,
            http_get=failing_get,
        )

    assert caught.value.code == "transport_error"
    assert _API_KEY not in str(caught.value)
    assert caught.value.__suppress_context__ is True


def test_timeout_exceptions_are_classified_without_provider_details(
    calendar_evidence: HiThinkETFCalendarEvidence,
):
    class ReadTimeout(Exception):
        pass

    def failing_get(*_args, **_kwargs):
        raise ReadTimeout(f"timeout while sending key={_API_KEY}")

    with pytest.raises(HiThinkETFAdapterError) as caught:
        fetch_hithink_etf_daily_bars(
            symbol=_SYMBOL,
            start="2026-05-18",
            end="2026-05-18",
            adjustment="qfq",
            api_key=_API_KEY,
            calendar_evidence=calendar_evidence,
            http_get=failing_get,
        )

    assert caught.value.code == "timeout"
    assert caught.value.retryable is True
    assert _API_KEY not in str(caught.value)


def test_five_year_window_limit_is_per_request_and_not_a_history_claim(
    calendar_evidence: HiThinkETFCalendarEvidence,
):
    called = False

    def fake_get(*_args, **_kwargs):
        nonlocal called
        called = True

    with pytest.raises(HiThinkETFAdapterError) as caught:
        fetch_hithink_etf_daily_bars(
            symbol=_SYMBOL,
            start="2021-09-25",
            end="2026-09-26",
            adjustment="qfq",
            api_key=_API_KEY,
            calendar_evidence=calendar_evidence,
            http_get=fake_get,
        )

    assert caught.value.code == "invalid_request"
    assert called is False
