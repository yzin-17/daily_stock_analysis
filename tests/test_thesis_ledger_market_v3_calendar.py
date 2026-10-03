"""2026 深交所公告日历：预热窗口及调休周末的确定性回归。"""

import pytest

from src.services.thesis_ledger_market_v3_facts import (
    market_calendar_evidence_v3,
    market_coverage_context_v3,
)


@pytest.mark.parametrize("day", [
    "2026-01-01", "2026-01-02", "2026-01-04",
    "2026-02-14", "2026-02-16", "2026-02-20", "2026-02-23", "2026-02-28",
    "2026-04-06", "2026-05-01", "2026-05-04", "2026-05-05", "2026-05-09",
    "2026-06-19", "2026-09-20", "2026-09-25", "2026-10-01", "2026-10-07",
    "2026-10-10",
])
def test_official_closures_and_adjusted_work_weekends_stay_closed(day):
    evidence = market_calendar_evidence_v3("CN", day, day)
    assert evidence is not None
    assert evidence["expectedSessionDates"] == []


@pytest.mark.parametrize("day", [
    "2026-01-05", "2026-02-24", "2026-04-07", "2026-05-06",
    "2026-06-22", "2026-09-28", "2026-10-08", "2026-12-31",
])
def test_first_regular_sessions_after_holidays(day):
    evidence = market_calendar_evidence_v3("CN", day, day)
    assert evidence is not None
    assert evidence["expectedSessionDates"] == [day]


def test_warmup_crosses_spring_holidays_without_inventing_sessions():
    evidence = market_calendar_evidence_v3("CN", "2026-03-01", "2026-08-09")
    assert evidence is not None
    dates = evidence["expectedSessionDates"]
    assert dates[0] == "2026-03-02"
    assert dates[-1] == "2026-08-07"
    assert "2026-04-06" not in dates
    assert "2026-05-05" not in dates
    assert "2026-05-06" in dates
    assert len([day for day in dates if day >= "2026-05-16"]) == 59
    assert dates == sorted(set(dates))


def test_calendar_scope_does_not_expand_listing_identity():
    known = market_coverage_context_v3(
        symbol="159516.SZ", market="CN", start="2026-03-01", end="2026-08-09",
    )
    assert known is not None
    assert known["listing"]["firstTradingDate"] == "2023-07-27"
    assert market_coverage_context_v3(
        symbol="000001.SZ", market="CN", start="2026-03-01", end="2026-08-09",
    ) is None


@pytest.mark.parametrize("market,start,end", [
    ("CN", "2025-12-31", "2026-01-05"),
    ("CN", "2026-12-30", "2027-01-01"),
    ("CN", "2026-08-09", "2026-03-01"),
    ("CN", "invalid", "2026-03-01"),
    ("HK", "2026-03-01", "2026-08-09"),
    ("US", "2026-03-01", "2026-08-09"),
])
def test_unreviewed_or_invalid_windows_remain_unavailable(market, start, end):
    assert market_calendar_evidence_v3(market, start, end) is None
