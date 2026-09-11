"""ThesisLedger V2 依赖事实的解析、覆盖率与 Provider 边界。"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def _cn_day_start(day: date) -> str:
    """Represent when a requested CN calendar window starts being applicable."""
    from zoneinfo import ZoneInfo

    return datetime.combine(day, datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    ).isoformat()


class V2DependencyError(ValueError):
    """Stable user-safe validation error for dependency-scoped V2 routes."""

    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def response(
    *,
    status: str,
    provider: str,
    provider_revision: str,
    coverage: dict[str, Any],
    facts: list[dict[str, Any]],
    reason: str | None = None,
) -> dict[str, Any]:
    """Build the stable dependency-scoped V2 response envelope."""
    return {
        "version": 2,
        "status": status,
        "provider": provider,
        "providerRevision": provider_revision,
        "coverage": coverage,
        "facts": facts,
        "reason": reason,
    }


def parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise V2DependencyError("invalid_request", f"{field} 必须是 YYYY-MM-DD") from exc


def parse_data_as_of(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise V2DependencyError("invalid_request", "dataAsOf 必须是带时区的 ISO 8601 时间") from exc
    if parsed.tzinfo is None:
        raise V2DependencyError("invalid_request", "dataAsOf 必须包含时区")
    return parsed.astimezone(timezone.utc)


def validate_cn_stock(symbol: str, market: str, instrument_type: str, canonicalize: Any) -> str:
    if market != "CN":
        raise V2DependencyError("unsupported_capability", "V2 当前仅支持 CN 市场")
    if instrument_type != "STOCK":
        raise V2DependencyError("unsupported_capability", "V2 当前仅支持 STOCK 标的")
    canonical = canonicalize(symbol)
    if not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", canonical):
        raise V2DependencyError("invalid_request", "symbol 必须是 CN 股票代码，例如 600519.SH")
    if canonical.split(".", 1)[0].startswith(("15", "16", "18", "51", "52", "56", "58")):
        raise V2DependencyError("unsupported_capability", "V2 暂不支持 CN ETF 事实")
    return canonical


def validate_range(start: str, end: str, data_as_of: datetime) -> tuple[date, date]:
    start_date = parse_date(start, "start")
    end_date = parse_date(end, "end")
    if start_date > end_date:
        raise V2DependencyError("invalid_request", "start 不能晚于 end")
    if end_date > data_as_of.date():
        raise V2DependencyError("invalid_request", "end 不能晚于 dataAsOf 的日期")
    return start_date, end_date


def calendar_fact(start: date, end: date, data_as_of: datetime) -> dict[str, Any] | None:
    """Read the installed XSHG calendar; never fall back to fixed facts."""
    try:
        from src.core import trading_calendar

        if not trading_calendar._XCALS_AVAILABLE:
            return None
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XSHG")
        first_session = getattr(calendar, "first_session", None)
        last_session = getattr(calendar, "last_session", None)
        if first_session is None or last_session is None:
            return None
        coverage_start = first_session.date()
        coverage_end = last_session.date()
        if start < coverage_start or end > coverage_end:
            return None
        schedule = calendar.schedule.loc[start.isoformat() : end.isoformat()]
        session_dates = {index.date() for index in schedule.index}
        holidays: list[str] = []
        current = start
        while current <= end:
            if current.weekday() < 5 and current not in session_dates:
                holidays.append(current.isoformat())
            current += timedelta(days=1)
        sessions = [
            {
                "startMinute": int(calendar.open_times[0][1].hour * 60 + calendar.open_times[0][1].minute),
                "endMinute": int(calendar.break_start_times[0][1].hour * 60 + calendar.break_start_times[0][1].minute),
            },
            {
                "startMinute": int(calendar.break_end_times[0][1].hour * 60 + calendar.break_end_times[0][1].minute),
                "endMinute": int(calendar.close_times[0][1].hour * 60 + calendar.close_times[0][1].minute),
            },
        ]
        version = str(getattr(xcals, "__version__", "")).strip()
        if not version:
            return None
        return {
            "market": "CN",
            "timezone": "Asia/Shanghai",
            "availableAt": _cn_day_start(start),
            "provider": "exchange-calendars",
            "providerRevision": f"exchange-calendars-{version}",
            "sessions": sessions,
            "sessionOverrides": [],
            "holidays": holidays,
            "range": {"start": start.isoformat(), "end": end.isoformat()},
        }
    except Exception as exc:  # optional dependency boundary
        logger.warning("V2 calendar provider unavailable: %s", exc)
        return None


def fixture_calendar(start: str, end: str, data_as_of: datetime, calendars: Iterable[dict[str, Any]]) -> dict[str, Any]:
    calendar = next(item for item in calendars if item["market"] == "CN")
    return {
        **calendar,
        "availableAt": _cn_day_start(parse_date(start, "start")),
        "sessionOverrides": [],
        "range": {"start": start, "end": end},
    }


def static_cn_instrument_fact(symbol: str, data_as_of: datetime) -> dict[str, Any]:
    """Return the versioned CN A-share market-rule fact.

    This is deliberately a market-rule fact, not a listing/suspension check:
    ``tradable`` means the standard lot/tick rule permits trading.  Current
    listing status remains outside this endpoint until a PIT instrument
    provider is available.
    """
    revision = "cn-a-share-standard-lot-tick-v1"
    timestamp = "1990-12-18T16:00:00+00:00"
    return {
        "symbol": symbol,
        "market": "CN",
        "instrumentType": "STOCK",
        "currency": "CNY",
        "lotSize": "100",
        "tickSize": "0.01",
        "tradable": True,
        "executionRules": {
            "status": "unavailable",
            "reason": "缺少覆盖请求历史区间的价格限制、法定收费与结算规则事实",
        },
        "provider": "dsa-market-rules",
        "providerRevision": revision,
        "occurredAt": timestamp,
        "availableAt": timestamp,
    }


def instrument_facts_response(
    symbol: str, data_as_of: datetime, start: str, end: str,
    execution_start: str, execution_end: str, fixture_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep static identity separate from unavailable historical applicability."""
    start_date, end_date = validate_range(start, end, data_as_of)
    execution_first, execution_last = validate_range(execution_start, execution_end, data_as_of)
    if execution_first < start_date or execution_last > end_date:
        raise V2DependencyError("invalid_request", "事实范围必须包含执行范围")
    coverage = {"start": start, "end": end, "complete": bool(fixture_facts)}
    if fixture_facts:
        return response(
            status="supported", provider="dsa-fixture",
            provider_revision="instrument-v2-fixture-1", coverage=coverage, facts=fixture_facts,
        )
    fact = static_cn_instrument_fact(symbol, data_as_of)
    missing = [
        {"field": "historicalTradability", "category": "criticalFact",
         "range": {"start": start, "end": end}, "provider": fact["provider"],
         "reason": "静态 lot/tick 不证明请求区间内的上市、停牌及价格限制适用性"},
        {"field": "executionRules", "category": "modelAssumption",
         "range": {"start": execution_start, "end": execution_end}, "provider": fact["provider"],
         "reason": fact["executionRules"]["reason"]},
    ]
    result = response(
        status="unavailable", provider=fact["provider"],
        provider_revision=fact["providerRevision"], coverage=coverage, facts=[fact],
        reason=f"{symbol} {start}..{end}: historicalTradability: {missing[0]['reason']}; "
               f"executionRules: {missing[1]['reason']}",
    )
    result["missingInputs"] = missing
    return result


def real_corporate_actions(symbol: str, start: str, end: str, data_as_of: datetime) -> dict[str, Any]:
    try:
        from data_provider.fundamental_adapter import AkshareFundamentalAdapter

        result = AkshareFundamentalAdapter().get_corporate_actions_v2(
            symbol,
            start_date=start,
            end_date=end,
            data_as_of=data_as_of,
        )
    except Exception as exc:  # upstream/provider boundary; do not expose details
        logger.warning("V2 corporate-action provider unavailable: %s", exc)
        return response(
            status="unavailable",
            provider="akshare",
            provider_revision="akshare-corporate-actions-unavailable",
            coverage={"start": start, "end": end, "complete": False},
            facts=[],
            reason="Provider 公司行动接口当前不可用",
        )
    coverage = result.get("coverage") or {"start": start, "end": end, "complete": False}
    facts = result.get("facts") or []
    provider_revision = str(
        facts[0].get("providerRevision") if facts else "akshare-corporate-actions-v1"
    )
    if not coverage.get("complete"):
        return response(
            status="unavailable",
            provider="akshare",
            provider_revision=provider_revision,
            coverage=coverage,
            facts=[],
            reason="Provider 未确认公司行动覆盖完整性",
        )
    return response(
        status="supported",
        provider="akshare",
        provider_revision=provider_revision,
        coverage=coverage,
        facts=facts,
    )
