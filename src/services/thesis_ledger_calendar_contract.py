"""日历事实信封的来源装配与展示范围校验。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable

from src.services.thesis_ledger_dependency_facts import (
    DependencyFactError, calendar_fact, fixture_calendar, parse_date, parse_data_as_of, response,
    validate_calendar_range,
)


def validate_market_calendar_range(start: str, end: str, data_as_of: datetime) -> tuple[date, date]:
    start_date = parse_date(start, "start")
    end_date = parse_date(end, "end")
    if start_date.isoformat() != start or end_date.isoformat() != end:
        raise DependencyFactError("invalid_request", "展示日历日期必须为 YYYY-MM-DD")
    if start_date > end_date or start_date > data_as_of.date():
        raise DependencyFactError("invalid_request", "展示日历开始日期或日期顺序无效")
    if (end_date - start_date).days + 1 > 20000:
        raise DependencyFactError("invalid_request", "展示日历范围超过 20000 个自然日预算")
    if end_date > data_as_of.date() + timedelta(days=366):
        raise DependencyFactError("invalid_request", "展示日历计划超过 366 个自然日预算")
    return start_date, end_date


def calendar_contract_response(
    start: date, end: date, data_as_of: datetime,
    fixtures: Iterable[dict[str, Any]] | None = None,
    market: str = "CN",
) -> dict[str, Any]:
    """两入口复用来源事实，不把展示预算应用到回测结算校验。"""
    from src.services.thesis_ledger_multimarket_calendar import multimarket_calendar_fact

    if market == "CN":
        fact = (fixture_calendar(start.isoformat(), end.isoformat(), data_as_of, fixtures)
                if fixtures is not None else calendar_fact(start, end, data_as_of))
    else:
        fact = multimarket_calendar_fact(market, start, end, data_as_of)
    coverage = {"start": start.isoformat(), "end": end.isoformat(), "complete": fact is not None}
    if fact is None:
        return response(
            status="unavailable", provider="exchange-calendars",
            provider_revision="exchange-calendars-unavailable", coverage=coverage, facts=[],
            reason=f"{market} 交易日历 Provider 当前不可用或未确认覆盖",
        )
    return response(
        status="supported", provider=str(fact["provider"]),
        provider_revision=str(fact["providerRevision"]), coverage=coverage, facts=[fact],
    )


def calendar_request_response(
    start: str, end: str, data_as_of: str, market: str,
    fixtures: Iterable[dict[str, Any]] | None = None, *, display: bool = False,
) -> dict[str, Any]:
    if market not in {"CN", "HK", "US"}:
        raise DependencyFactError("unsupported_capability", "当前合同仅支持 CN/HK/US 市场交易日历")
    instant = parse_data_as_of(data_as_of)
    validator = validate_market_calendar_range if display else validate_calendar_range
    first, last = validator(start, end, instant)
    return calendar_contract_response(first, last, instant, fixtures, market)
