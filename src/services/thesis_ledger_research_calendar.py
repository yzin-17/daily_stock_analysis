"""固定来源股票/ETF研究复用现行日历，缺上市日期明确保持未知。"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from src.services.thesis_ledger_calendar_release import RELEASE_ADAPTER_REVISION, RELEASE_VERSION
from src.services.thesis_ledger_dependency_facts import DependencyFactError, calendar_fact, validate_calendar_range
from src.services.thesis_ledger_market_v3_facts import market_calendar_evidence_v3, market_coverage_context_v3
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import DailyBinding
from src.services.thesis_ledger_capability_contracts import ResearchCalendarContext


def research_calendar_evidence(start: str, end: str, observed_at: datetime) -> dict[str, Any] | None:
    """投影已经核验来源身份的日历，不把现行版本标为历史可见。"""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        return None
    try:
        first, last = validate_calendar_range(start, end, observed_at)
    except (DependencyFactError, TypeError, ValueError):
        return None
    if first.isoformat() != start or last.isoformat() != end or (last - first).days >= 20_000:
        return None
    fact = calendar_fact(first, last, observed_at)
    if fact is None:
        return None
    revision = f"exchange-calendars-{RELEASE_VERSION}-{RELEASE_ADAPTER_REVISION}"
    if (fact.get("market") != "CN" or fact.get("timezone") != "Asia/Shanghai"
            or fact.get("provider") != "exchange-calendars" or fact.get("providerRevision") != revision
            or fact.get("range") != {"start": start, "end": end} or fact.get("sessionOverrides") != []):
        return None
    try:
        available_at = datetime.fromisoformat(fact["availableAt"].replace("Z", "+00:00"))
        holidays = [date.fromisoformat(day) for day in fact["holidays"]]
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if available_at.tzinfo is None or available_at.utcoffset() is None or available_at > observed_at:
        return None
    if any(day < first or day > last for day in holidays):
        return None
    closed = set(holidays)
    sessions = []
    current = first
    while current <= last:
        if current.weekday() < 5 and current not in closed:
            sessions.append(current.isoformat())
        current += timedelta(days=1)
    return {
        "market": "CN", "timezone": "Asia/Shanghai", "source": "exchange-calendars/XSHG",
        "revision": f"{revision}:availableAt={available_at.isoformat()}",
        "supportedRange": {"start": start, "end": end}, "expectedSessionDates": sessions,
    }


def _research_request(request: Mapping[str, Any]) -> bool:
    key, target = request.get("routeKey"), request.get("routeTarget")
    if (request.get('tradabilityMode') != 'assume-untradable-no-bar'
            or not isinstance(key, Mapping) or not isinstance(target, Mapping)):
        return False
    try:
        binding = CAPABILITY_SOURCES.resolve(key, target)
    except TypeError:
        return False
    if not isinstance(binding, DailyBinding) or binding.research_calendar_eligible is None:
        return False
    return binding.research_calendar_eligible(ResearchCalendarContext(
        symbol=str(request.get('symbol', '')), start=str(request.get('start', '')), end=str(request.get('end', '')),
        market=binding.key.market, asset_type=binding.key.asset_type,
    )) is True


def research_market_coverage_context(
    request: Mapping[str, Any], *,
    calendar_evidence_provider: Callable = market_calendar_evidence_v3,
    observed_at: datetime | None = None,
) -> dict[str, Any] | None:
    """旧窗口优先保持原证明；明确研究请求才补用现行日历。"""
    def calendar(market: str, start: str, end: str):
        existing = calendar_evidence_provider(market, start, end)
        if existing is not None or not _research_request(request):
            return existing
        instant = observed_at or datetime.now(timezone.utc)
        if market != "CN":
            from src.services.thesis_ledger_multimarket_calendar import multimarket_calendar_evidence

            return multimarket_calendar_evidence(market, start, end, instant)
        return research_calendar_evidence(start, end, instant)

    return market_coverage_context_v3(
        symbol=str(request["symbol"]), market=str(request["routeKey"]["market"]),
        start=str(request["start"]), end=str(request["end"]), calendar_evidence_provider=calendar,
        allow_unknown_listing=_research_request(request),
    )
