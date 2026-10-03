"""多窗口采集预算、交集及失败停止边界。"""

from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.services.thesis_ledger_hithink_etf import (
    HiThinkETFAdapterError, HiThinkETFBar, HiThinkETFBarSeries, HiThinkETFCalendarEvidence,
    fetch_hithink_etf_daily_bars,
)
from src.services.thesis_ledger_hithink_windows import collect_hithink_etf_windows


SESSIONS = tuple(f"2025-01-{day:02}" for day in range(6, 11))
WINDOWS = ((SESSIONS[0], SESSIONS[2]), (SESSIONS[1], SESSIONS[3]), (SESSIONS[2], SESSIONS[4]))
CALENDAR = HiThinkETFCalendarEvidence(
    symbol="159516.SZ", calendar_name="SZSE", calendar_version="fixture",
    market_sessions=SESSIONS, listing_date="2025-01-06", listing_source="fixture",
    listing_source_version="fixture",
)


def _series(options):
    return HiThinkETFBarSeries(
        options["symbol"], options["start"], options["end"],
        tuple(HiThinkETFBar(day, 10, 11, 9, 10, 100, 1000) for day in SESSIONS
              if options["start"] <= day <= options["end"]), {}, options["start"],
    )


def _collect(fetch, **overrides):
    return collect_hithink_etf_windows(**{
        "symbol": "159516.SZ", "windows": WINDOWS, "api_key": "synthetic",
        "calendar_evidence": CALENDAR, "max_requests": 3, "timeout_seconds": 10,
        "fetch": fetch, "monotonic": lambda: 0,
        "clock": lambda: datetime(2025, 1, 11, tzinfo=timezone.utc), **overrides,
    })


def test_collects_all_pairwise_intersections_and_preserves_observations():
    calls = []

    def fetch(**options):
        calls.append(options)
        return _series(options)

    result = _collect(fetch)
    assert len(result.observations) == 3
    assert len(result.overlaps) == 3
    assert [item.series.response_fingerprint for item in result.observations] == [w[0] for w in WINDOWS]
    assert all(item.completed_at.endswith("+00:00") for item in result.observations)
    assert all(call["timeout_seconds"] == 10 for call in calls)


@pytest.mark.parametrize("overrides", [
    {"max_requests": 2}, {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
    {"windows": ((SESSIONS[0], SESSIONS[1]), (SESSIONS[3], SESSIONS[4]))},
    {"windows": (WINDOWS[0], ("2025-01-07", "2031-01-07"))},
])
def test_rejects_entire_invalid_plan_before_first_call(overrides):
    with pytest.raises(HiThinkETFAdapterError):
        _collect(lambda **_: pytest.fail("invalid plan called provider"), **overrides)


@pytest.mark.parametrize("failure", ["conflict", "provider"])
def test_stops_after_second_window_failure_without_retry_or_third_call(failure):
    calls = []

    def fetch(**options):
        calls.append(options)
        series = _series(options)
        if len(calls) == 2:
            if failure == "provider":
                raise HiThinkETFAdapterError("permission_denied")
            series = replace(series, bars=tuple(replace(bar, close=10.5) for bar in series.bars))
        return series

    with pytest.raises(HiThinkETFAdapterError) as error:
        _collect(fetch)
    assert error.value.code == ("permission_denied" if failure == "provider" else "invalid_response")
    assert len(calls) == 2


def test_late_first_response_is_rejected_without_second_call():
    ticks = iter([0, 1, 11])
    calls = []

    def fetch(**options):
        calls.append(options)
        return _series(options)

    with pytest.raises(HiThinkETFAdapterError) as error:
        _collect(fetch, monotonic=lambda: next(ticks))
    assert error.value.code == "timeout"
    assert len(calls) == 1
    assert calls[0]["timeout_seconds"] == 9


@pytest.mark.parametrize("mode", ["complete", "missing_session", "pagination", "conflict"])
def test_collection_uses_real_http_parser_and_stops_on_invalid_second_window(mode):
    calls = []

    def epoch(day):
        return int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)

    def http_get(_url, **options):
        calls.append(options)
        params = options["params"]
        days = [day for day in SESSIONS if params["start"] <= epoch(day) <= params["end"]]
        items = [{"date_ms": epoch(day), "open_price": 10, "high_price": 11,
                  "low_price": 9, "close_price": 10, "volume": 100, "turnover": 1000}
                 for day in days]
        payload = {"code": 0, "data": {"timestamp": epoch(days[-1]), "thscode": "159516.SZ",
                                        "interval": "1d", "adjust": None, "item": items}}
        if len(calls) == 2:
            if mode == "missing_session":
                items.pop(1)
            elif mode == "pagination":
                payload["data"]["hasMore"] = True
            elif mode == "conflict":
                items[0]["close_price"] = 10.5
        return SimpleNamespace(status_code=200, content=json.dumps(payload).encode(), json=lambda: payload)

    fetch = partial(fetch_hithink_etf_daily_bars, http_get=http_get)
    if mode == "complete":
        result = _collect(fetch)
        assert len(result.observations) == 3
        assert len(result.overlaps) == 3
        assert all(len(item.series.response_fingerprint) == 64 for item in result.observations)
        assert all(item.series.coverage["missingSessionCount"] == 0 for item in result.observations)
    else:
        with pytest.raises(HiThinkETFAdapterError) as error:
            _collect(fetch)
        assert error.value.code == {
            "missing_session": "incomplete_coverage", "pagination": "pagination_unverified",
            "conflict": "invalid_response",
        }[mode]
        if mode == "conflict":
            assert error.value.diagnostics["windowComparison"] == "bar_mismatch"
        assert len(calls) == 2
    assert all(call["timeout"] <= 10 for call in calls)
