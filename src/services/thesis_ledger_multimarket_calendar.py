"""读取已核验发布制品的XHKG/XNYS真实会话与早收市，保留发布可见时钟。"""

from datetime import date, datetime, timedelta
from pathlib import Path

from src.services.thesis_ledger_calendar_release import verified_calendar_available_at, RELEASE_ADAPTER_REVISION

MARKETS = {
    "HK": ("XHKG", "Asia/Hong_Kong", [{"startMinute": 570, "endMinute": 720}, {"startMinute": 780, "endMinute": 960}]),
    "US": ("XNYS", "America/New_York", [{"startMinute": 570, "endMinute": 960}]),
}


def multimarket_calendar_fact(market: str, start: date, end: date, data_as_of: datetime):
    if market not in MARKETS:
        return None
    try:
        import exchange_calendars as xcals
        import pandas as pd

        version = getattr(xcals, "__version__", "unknown")
        available = verified_calendar_available_at(version, Path(xcals.__file__).parent, data_as_of)
        if available is None:
            return None
        name, timezone, normal = MARKETS[market]
        calendar = xcals.get_calendar(name)
        if start < calendar.first_session.date() or end > calendar.last_session.date():
            return None
        schedule = calendar.schedule.loc[start.isoformat():end.isoformat()]
        days = {index.date() for index in schedule.index}
        overrides = []
        for index, row in schedule.iterrows():
            def minute(value):
                if value is None or pd.isna(value) or value.tzinfo is None:
                    raise ValueError("会话时刻缺失或没有时区")
                local = value.tz_convert(timezone)
                if local.date() != index.date():
                    raise ValueError("会话不在对应交易日")
                return local.hour * 60 + local.minute

            opening, closing = minute(row["open"]), minute(row["close"])
            if pd.isna(row["break_start"]):
                sessions = [{"startMinute": opening, "endMinute": closing}]
            else:
                sessions = [{"startMinute": opening, "endMinute": minute(row["break_start"])},
                            {"startMinute": minute(row["break_end"]), "endMinute": closing}]
            if sessions != normal:
                overrides.append({"date": index.date().isoformat(), "sessions": sessions})
        holidays = []
        current = start
        while current <= end:
            if current.weekday() < 5 and current not in days:
                holidays.append(current.isoformat())
            current += timedelta(days=1)
        return {"market": market, "timezone": timezone, "provider": "exchange-calendars",
                "providerRevision": f"exchange-calendars-{name}-{version}-{RELEASE_ADAPTER_REVISION}",
                "availableAt": available.isoformat(), "sessions": normal, "sessionOverrides": overrides,
                "holidays": holidays, "range": {"start": start.isoformat(), "end": end.isoformat()}}
    except (ImportError, ValueError, KeyError, TypeError, AttributeError, OSError):
        return None


def multimarket_calendar_evidence(market: str, start: str, end: str, observed_at: datetime):
    fact = multimarket_calendar_fact(market, date.fromisoformat(start), date.fromisoformat(end), observed_at)
    if fact is None:
        return None
    closed, dates = set(fact["holidays"]), []
    current, last = date.fromisoformat(start), date.fromisoformat(end)
    while current <= last:
        if current.weekday() < 5 and current.isoformat() not in closed:
            dates.append(current.isoformat())
        current += timedelta(days=1)
    return {"market": market, "timezone": fact["timezone"], "source": "exchange-calendars/" + MARKETS[market][0],
            "revision": fact["providerRevision"] + ":availableAt=" + fact["availableAt"],
            "supportedRange": {"start": start, "end": end}, "expectedSessionDates": dates}
