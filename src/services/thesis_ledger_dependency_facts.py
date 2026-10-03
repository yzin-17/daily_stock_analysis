"""ThesisLedger 现行依赖事实的解析、覆盖率与 Provider 边界。"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.services.thesis_ledger_tradability_provider import real_cn_tradability
from src.services.thesis_ledger_calendar_release import (
    RELEASE_ADAPTER_REVISION, verified_calendar_available_at,
)

logger = logging.getLogger(__name__)


def _cn_day_start(day: date) -> str:
    """Represent when a requested CN calendar window starts being applicable."""
    from zoneinfo import ZoneInfo

    return datetime.combine(day, datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
        timezone.utc
    ).isoformat()


class DependencyFactError(ValueError):
    """现行依赖事实请求的稳定校验错误。"""

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
    """Build the dependency-scoped response envelope."""
    return {
        "version": 3,
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
        raise DependencyFactError("invalid_request", f"{field} 必须是 YYYY-MM-DD") from exc


def parse_data_as_of(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise DependencyFactError("invalid_request", "dataAsOf 必须是带时区的 ISO 8601 时间") from exc
    if parsed.tzinfo is None:
        raise DependencyFactError("invalid_request", "dataAsOf 必须包含时区")
    return parsed.astimezone(timezone.utc)


def validate_cn_stock(symbol: str, market: str, instrument_type: str, canonicalize: Any) -> str:
    """Validate a CN stock/ETF identity without conflating the two types."""
    if market != "CN":
        raise DependencyFactError("unsupported_capability", "当前合同仅支持 CN 市场")
    if instrument_type not in {"STOCK", "ETF"}:
        raise DependencyFactError("unsupported_capability", "当前合同仅支持 STOCK/ETF 标的")
    canonical = canonicalize(symbol)
    if not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", canonical):
        raise DependencyFactError("invalid_request", "symbol 必须是 CN 股票或 ETF 代码，例如 600519.SH")
    is_etf = canonical.split(".", 1)[0].startswith(("15", "16", "18", "51", "52", "56", "58"))
    if instrument_type == "ETF" and not is_etf:
        raise DependencyFactError("invalid_request", f"symbol {canonical} 与 instrumentType=ETF 不一致")
    if instrument_type == "STOCK" and is_etf:
        raise DependencyFactError("invalid_request", f"symbol {canonical} 与 instrumentType=STOCK 不一致")
    return canonical


def validate_range(start: str, end: str, data_as_of: datetime) -> tuple[date, date]:
    start_date = parse_date(start, "start")
    end_date = parse_date(end, "end")
    if start_date > end_date:
        raise DependencyFactError("invalid_request", "start 不能晚于 end")
    if end_date > data_as_of.date():
        raise DependencyFactError("invalid_request", "end 不能晚于 dataAsOf 的日期")
    return start_date, end_date


def validate_calendar_range(start: str, end: str, data_as_of: datetime) -> tuple[date, date]:
    """日历可包含有界的未来结算计划；不放宽行情日期校验。"""
    start_date = parse_date(start, "start")
    end_date = parse_date(end, "end")
    if start_date > end_date or start_date > data_as_of.date():
        raise DependencyFactError("invalid_request", "日历开始日期或日期顺序无效")
    if end_date > data_as_of.date() + timedelta(days=104):
        raise DependencyFactError("invalid_request", "日历结算范围超过 104 个自然日预算")
    return start_date, end_date


def calendar_fact(start: date, end: date, data_as_of: datetime) -> dict[str, Any] | None:
    """Read the installed XSHG calendar; never fall back to fixed facts."""
    try:
        from src.core import trading_calendar

        if not trading_calendar._XCALS_AVAILABLE:
            return None
        import exchange_calendars as xcals

        version = str(getattr(xcals, "__version__", "")).strip()
        available_at = verified_calendar_available_at(version, Path(xcals.__file__).parent, data_as_of)
        if available_at is None:
            return None
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
        return {
            "market": "CN",
            "timezone": "Asia/Shanghai",
            "availableAt": available_at.isoformat(),
            "provider": "exchange-calendars",
            "providerRevision": f"exchange-calendars-{version}-{RELEASE_ADAPTER_REVISION}",
            "sessions": sessions,
            "sessionOverrides": [],
            "holidays": holidays,
            "range": {"start": start.isoformat(), "end": end.isoformat()},
        }
    except Exception as exc:  # optional dependency boundary
        logger.warning("现行交易日历来源不可用: %s", exc)
        return None


def fixture_calendar(start: str, end: str, data_as_of: datetime, calendars: Iterable[dict[str, Any]]) -> dict[str, Any]:
    calendar = next(item for item in calendars if item["market"] == "CN")
    return {
        **calendar,
        "availableAt": _cn_day_start(parse_date(start, "start")),
        "sessionOverrides": [],
        "range": {"start": start, "end": end},
    }


def static_cn_instrument_fact(
    symbol: str,
    data_as_of: datetime,
    instrument_type: str = "STOCK",
) -> dict[str, Any]:
    """Return versioned CN A-share identity, lot/tick and raw execution-rule state.

    Historical applicability is resolved by ``real_cn_tradability`` from an
    explicit, complete routed BarSeries. The static rule fact must never be
    used as proof that a symbol was tradable throughout a requested range.
    """
    if instrument_type == "ETF":
        revision = "cn-etf-standard-lot-tick-v1"
        lot_size = "100"
        tick_size = "0.001"
    else:
        revision = "cn-a-share-standard-lot-tick-v1"
        lot_size = "100"
        tick_size = "0.01"
    timestamp = "1990-12-18T16:00:00+00:00"
    return {
        "symbol": symbol,
        "market": "CN",
        "instrumentType": instrument_type,
        "currency": "CNY",
        "lotSize": lot_size,
        "tickSize": tick_size,
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
    instrument_type: str | None = None,
    route_key: Mapping[str, Any] | None = None,
    route_target: Mapping[str, Any] | None = None,
    identity_only: bool = False,
) -> dict[str, Any]:
    """Resolve critical historical applicability separately from model assumptions."""
    start_date, end_date = validate_range(start, end, data_as_of)
    execution_first, execution_last = validate_range(execution_start, execution_end, data_as_of)
    if execution_first < start_date or execution_last > end_date:
        raise DependencyFactError("invalid_request", "事实范围必须包含执行范围")
    coverage = {"start": start, "end": end, "complete": bool(fixture_facts)}
    if fixture_facts:
        if identity_only:
            fixture_facts = [{**fact, "tradable": False} for fact in fixture_facts]
        return response(
            status="supported", provider="dsa-fixture",
            provider_revision="instrument-v2-fixture-1", coverage=coverage, facts=fixture_facts,
        )

    resolved_type = instrument_type or (
        "ETF"
        if symbol.split(".", 1)[0].startswith(("15", "16", "18", "51", "52", "56", "58"))
        else "STOCK"
    )
    fact = static_cn_instrument_fact(symbol, data_as_of, resolved_type)
    if identity_only:
        fact["tradable"] = False
        return response(status="supported", provider=fact["provider"], provider_revision=fact["providerRevision"],
                        coverage={"start": start, "end": end, "complete": True}, facts=[fact])
    tradability = real_cn_tradability(
        symbol, start_date, end_date, data_as_of,
        instrument_type=resolved_type, route_key=route_key, route_target=route_target,
    )
    coverage["complete"] = bool(tradability["coverage"].get("complete"))
    fact["tradable"] = bool(tradability.get("tradable")) if coverage["complete"] else False
    provider = f"{tradability['provider']}+{fact['provider']}"
    provider_revision = f"{tradability['providerRevision']}+{fact['providerRevision']}"
    fact["provider"] = provider
    fact["providerRevision"] = provider_revision
    # ``availableAt`` remains the point-in-time availability of the identity,
    # lot and tick fact consumed by the execution engine. Historical
    # tradability coverage is validated while the snapshot is built and is
    # carried by the response coverage plus the combined provider revision.

    missing: list[dict[str, Any]] = []
    if not coverage["complete"] or (not fact["tradable"] and not tradability.get("historicalTradability")):
        missing.append(
            {
                "field": "historicalTradability",
                "category": "criticalFact",
                "range": {"start": start, "end": end},
                "provider": tradability["provider"],
                "reason": tradability.get("reason") or "Provider 未证明请求区间历史可交易性",
            }
        )
    missing.append(
        {
            "field": "executionRules",
            "category": "modelAssumption",
            "range": {"start": execution_start, "end": execution_end},
            "provider": fact["provider"],
            "reason": fact["executionRules"]["reason"],
        }
    )

    critical_missing = next(
        (item for item in missing if item["category"] == "criticalFact"),
        None,
    )
    result = response(
        status="unavailable" if critical_missing else "supported",
        provider=provider,
        provider_revision=provider_revision,
        coverage=coverage,
        facts=[fact],
        reason=(
            f"{symbol} {start}..{end}: historicalTradability: {critical_missing['reason']}"
            if critical_missing
            else None
        ),
    )
    result["missingInputs"] = missing
    if tradability.get("historicalTradability") is not None:
        result["historicalTradability"] = tradability["historicalTradability"]
    return result
