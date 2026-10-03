"""按显式预算采集 HiThink ETF 窗口，并验证全部已观测交集。"""

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from src.services.thesis_ledger_hithink_etf import (
    HiThinkETFAdapterError, HiThinkETFBarSeries, HiThinkETFCalendarEvidence,
    _parse_iso_date, _validate_request, _validated_calendar_evidence,
    fetch_hithink_etf_daily_bars,
)
from src.services.thesis_ledger_hithink_overlap import (
    HiThinkOverlapEvidence, compare_hithink_etf_windows,
)


@dataclass(frozen=True, slots=True)
class HiThinkWindowObservation:
    series: HiThinkETFBarSeries
    started_at: str
    completed_at: str


@dataclass(frozen=True, slots=True)
class HiThinkWindowCollection:
    observations: tuple[HiThinkWindowObservation, ...]
    overlaps: tuple[HiThinkOverlapEvidence, ...]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise HiThinkETFAdapterError("invalid_request")
    return value.astimezone(timezone.utc).isoformat()


def collect_hithink_etf_windows(
    *, symbol: str, windows: tuple[tuple[str, str], ...], api_key: str,
    calendar_evidence: HiThinkETFCalendarEvidence,
    max_requests: int, timeout_seconds: float,
    fetch: Callable[..., HiThinkETFBarSeries] = fetch_hithink_etf_daily_bars,
    monotonic: Callable[[], float] = time.monotonic,
    clock: Callable[[], datetime] = _utc_now,
) -> HiThinkWindowCollection:
    """调用方负责准入；本函数不重试、不合并，也不提升未知来源口径。"""
    if (not isinstance(max_requests, int) or isinstance(max_requests, bool)
            or not windows or not 1 <= len(windows) <= max_requests
            or not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise HiThinkETFAdapterError("invalid_request")
    previous_start = None
    previous_sessions = None
    for start, end in windows:
        start_date, end_date = _parse_iso_date(start, "start"), _parse_iso_date(end, "end")
        _validate_request(symbol, start_date, end_date, "qfq", api_key, timeout_seconds)
        calendar = _validated_calendar_evidence(calendar_evidence, symbol, start_date, end_date)
        sessions = set(calendar.expected_sessions)
        if (previous_start is not None and start <= previous_start
                or previous_sessions is not None and not sessions.intersection(previous_sessions)):
            raise HiThinkETFAdapterError("invalid_request")
        previous_start, previous_sessions = start, sessions
    deadline = monotonic() + timeout_seconds
    observations = []
    overlaps = []
    for start, end in windows:
        started_at = _timestamp(clock)
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise HiThinkETFAdapterError("timeout", retryable=False)
        series = fetch(symbol=symbol, start=start, end=end, adjustment="qfq", api_key=api_key,
                       calendar_evidence=calendar_evidence, timeout_seconds=remaining)
        if monotonic() >= deadline:
            raise HiThinkETFAdapterError("timeout", retryable=False)
        if (series.symbol, series.requested_start, series.requested_end) != (symbol, start, end):
            raise HiThinkETFAdapterError("invalid_response")
        for observation in observations:
            earlier = observation.series
            if max(earlier.requested_start, start) > min(earlier.requested_end, end):
                continue
            comparison = compare_hithink_etf_windows(earlier, series)
            if comparison.status != "consistent":
                raise HiThinkETFAdapterError("invalid_response", diagnostics={
                    "windowComparison": comparison.reason,
                    "conflictDates": list(comparison.conflict_dates),
                })
            overlaps.append(comparison)
        completed_at = _timestamp(clock)
        if completed_at < started_at:
            raise HiThinkETFAdapterError("invalid_response")
        observations.append(HiThinkWindowObservation(series, started_at, completed_at))
    return HiThinkWindowCollection(tuple(observations), tuple(overlaps))
