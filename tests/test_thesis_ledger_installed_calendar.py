"""实际安装的 XSHG 日历包回归，不请求外部来源。"""

from datetime import date, datetime, timedelta, timezone

import exchange_calendars as xcals

from src.core import trading_calendar
from src.services.thesis_ledger_dependency_facts import calendar_fact


def test_installed_xshg_version_sessions_and_holiday():
    fact = calendar_fact(date(2025, 1, 1), date(2025, 1, 3), datetime.now(timezone.utc))
    assert fact is not None
    assert fact["providerRevision"] == f"exchange-calendars-{xcals.__version__}-release-evidence-v1"
    assert fact["market"] == "CN"
    assert fact["timezone"] == "Asia/Shanghai"
    assert fact["holidays"] == ["2025-01-01"]
    assert fact["sessions"] == [
        {"startMinute": 570, "endMinute": 690},
        {"startMinute": 780, "endMinute": 900},
    ]
    assert fact["range"] == {"start": "2025-01-01", "end": "2025-01-03"}


def test_actual_package_coverage_edges_are_not_extrapolated():
    calendar = xcals.get_calendar("XSHG")
    first = calendar.first_session.date()
    last = calendar.last_session.date()
    observed = datetime.now(timezone.utc)
    assert calendar_fact(first, first, observed) is not None
    assert calendar_fact(last, last, observed) is not None
    assert calendar_fact(first - timedelta(days=1), first, observed) is None
    assert calendar_fact(last, last + timedelta(days=1), observed) is None


def test_installed_calendar_matches_2020_emergency_closure():
    # 上交所 2020-01-27 调整公告：1 月 31 日休市、2 月 3 日恢复。
    # 本断言仅核对当前包内容，不声称调整在公告前已可见。
    fact = calendar_fact(date(2020, 1, 30), date(2020, 2, 3), datetime.now(timezone.utc))
    assert fact is not None
    assert fact["holidays"] == ["2020-01-30", "2020-01-31"]
    assert "2020-02-03" not in fact["holidays"]


def test_unavailable_calendar_package_does_not_supply_fixed_facts(monkeypatch):
    monkeypatch.setattr(trading_calendar, "_XCALS_AVAILABLE", False)
    assert calendar_fact(date(2025, 1, 1), date(2025, 1, 3), datetime.now(timezone.utc)) is None
