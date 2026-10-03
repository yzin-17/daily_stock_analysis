"""研究日期使用的 XSHG 日历制品核验；不提供基金真实开放状态。"""

from pathlib import Path

from src.services.thesis_ledger_calendar_release import (
    RELEASE_ADAPTER_REVISION, RELEASE_SOURCE_DIGEST, verified_calendar_available_at,
)


def read_nav_research_workdays(start: str, end: str, data_as_of) -> tuple[tuple[str, ...], dict]:
    import exchange_calendars as xcals

    version = str(xcals.__version__)
    available = verified_calendar_available_at(version, Path(xcals.__file__).parent, data_as_of)
    if available is None:
        raise ValueError('研究日历制品未核验或在冻结时点不可用')
    calendar = xcals.get_calendar('XSHG')
    if start < calendar.first_session.date().isoformat() or end > calendar.last_session.date().isoformat():
        raise ValueError('研究日期超出已核验 XSHG 日历范围')
    days = tuple(index.date().isoformat() for index in calendar.schedule.loc[start:end].index)
    if not days:
        raise ValueError('研究范围没有披露工作日')
    return days, {
        'calendar': 'XSHG', 'version': version, 'adapterRevision': RELEASE_ADAPTER_REVISION,
        'sourceHash': RELEASE_SOURCE_DIGEST, 'availableAt': available.isoformat(),
    }
