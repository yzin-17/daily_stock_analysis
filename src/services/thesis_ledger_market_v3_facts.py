"""Data V3 独立日历与上市事实，不从行情结果推断边界。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Mapping

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_hithink_etf import HITHINK_ETF_HISTORICAL_URL


from data_provider.hithink_etf_capability import (
    HITHINK_ETF_HISTORY_SOURCE, HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1,
    HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1, HITHINK_ETF_LOCAL_COVERAGE_PROTOCOL_V1,
)

HITHINK_ETF_NATIVE_FIELD_CONTRACT_V1 = "dsa-hithink-etf-native-field-units-v1"
HITHINK_ETF_NATIVE_FIELD_CONTRACT_V2 = "dsa-hithink-etf-native-field-units-v2"


_CN_LISTING_FACTS_V3: dict[str, dict[str, str]] = {
    "000995.SZ": {
        "firstTradingDate": "2000-08-07",
        "source": "https://static.cninfo.com.cn/finalpage/2021-04-28/1209836916.PDF",
        "revision": "huangtai-2020-annual-report-1209836916",
        "knownAt": "2021-04-29T00:00:00+08:00",
    },
    "600000.SH": {
        "firstTradingDate": "1999-11-10",
        "source": "https://news.spdb.com.cn/about_spd/xwdt_1632/202302/t20230201_1115740.shtml",
        "revision": "spdb-listing-t20230201_1115740",
        "knownAt": "2023-02-01T00:00:00+08:00",
    },
    "159516.SZ": {
        "firstTradingDate": "2023-07-27",
        "source": "https://www.szse.cn/disclosure/notice/fund/t20230724_602100.html",
        "revision": "szse-fund-listing-t20230724_602100",
        "knownAt": "2023-07-24T00:00:00+08:00",
    },
}

_MARKET_TIMEZONES_V3 = {
    "CN": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong",
    "US": "America/New_York",
}

_CN_CALENDAR_WINDOW_V3 = {
    "start": "2026-01-01",
    "end": "2026-12-31",
    "source": "https://www.szse.cn/disclosure/notice/t20251222_618087.html",
    "revision": "szse-2026-holidays-t20251222_618087-year-v1",
    # Weekday closures from the SZSE annual notice, reviewed 2026-09-25.
    # Adjustment-work weekends remain closed. This is a calendar fact only;
    # instrument listing/tradability and provider admission remain independent.
    "holidays": frozenset({
        "2026-01-01", "2026-01-02",
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
        "2026-02-20", "2026-02-23", "2026-04-06",
        "2026-05-01", "2026-05-04", "2026-05-05", "2026-06-19",
        "2026-09-25", "2026-10-01", "2026-10-02", "2026-10-05",
        "2026-10-06", "2026-10-07",
    }),
}


_CN_CALENDAR_WINDOWS_V3 = (
    _CN_CALENDAR_WINDOW_V3,
    {
        "start": "2023-08-01",
        "end": "2023-08-31",
        "source": "https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20221227_5714459.shtml",
        "revision": "sse-2023-holidays-c_20221227_5714459-august-v1",
        # 上交所全年休市公告的八月没有节假日休市；周末保持休市。
        "holidays": frozenset(),
    },
)


def market_listing_fact_v3(symbol: str) -> dict[str, str] | None:
    """Return only explicitly sourced listing dates for exact canonical symbols."""
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    canonical = symbol.strip().upper()
    fact = _CN_LISTING_FACTS_V3.get(canonical)
    if fact is None:
        return None
    return {"symbol": symbol.strip(), **fact}


def market_pagination_contract_v3(
    asset_type: str,
    provider: str,
    upstream_source: str,
) -> dict[str, Any] | None:
    """Return the reviewed retrieval contract for exact adapters."""
    asset = asset_type.strip().upper()
    provider_id = provider.strip().lower()
    source = upstream_source.strip().lower()
    for key, target, binding in CAPABILITY_SOURCES.routes("DAILY_BAR"):
        if (key["assetType"], target["providerId"], target["upstreamSource"]) == (asset, provider_id, source):
            return {"protocol": binding.protocol, "maximumRows": binding.maximum_rows}
    source_contract = market_source_contract_v3(asset, provider_id, source)
    if source_contract is not None:
        return {
            "protocol": source_contract["paginationProtocol"],
            "maximumRows": source_contract["maximumRows"],
        }
    return None


def market_source_contract_v3(
    asset_type: str,
    provider: str,
    upstream_source: str,
) -> dict[str, Any] | None:
    """Return local request/coverage metadata without inventing upstream facts.

    ``sourceContractRevision`` describes this DSA endpoint/request contract.
    It is distinct from the unknown upstream data and adjustment revisions.
    """
    identity = (
        asset_type.strip().upper(),
        provider.strip().lower(),
        upstream_source.strip().lower(),
    )
    for key, target, binding in CAPABILITY_SOURCES.routes("DAILY_BAR"):
        if (key["assetType"], target["providerId"], target["upstreamSource"]) == identity and binding.source_contract is not None:
            return dict(binding.source_contract)
    return None


def market_calendar_evidence_v3(
    market: str,
    start: str,
    end: str,
) -> dict[str, Any] | None:
    """返回官方休市公告支持的有限日历窗口。"""
    if market != "CN":
        return None
    try:
        requested_start = date.fromisoformat(start)
        requested_end = date.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    if requested_start > requested_end:
        return None
    window = next((
        candidate for candidate in _CN_CALENDAR_WINDOWS_V3
        if date.fromisoformat(candidate["start"]) <= requested_start
        and requested_end <= date.fromisoformat(candidate["end"])
    ), None)
    if window is None:
        return None

    expected_sessions: list[str] = []
    current = requested_start
    while current <= requested_end:
        value = current.isoformat()
        if current.weekday() < 5 and value not in window["holidays"]:
            expected_sessions.append(value)
        current += timedelta(days=1)

    return {
        "market": market,
        "timezone": _MARKET_TIMEZONES_V3[market],
        "source": window["source"],
        "revision": window["revision"],
        "supportedRange": {
            "start": window["start"],
            "end": window["end"],
        },
        "expectedSessionDates": expected_sessions,
    }


def market_coverage_context_v3(
    *,
    symbol: str,
    market: str,
    start: str,
    end: str,
    calendar_evidence_provider: Callable[[str, str, str], Mapping[str, Any] | None]
    = market_calendar_evidence_v3,
    allow_unknown_listing: bool = False,
) -> dict[str, Any] | None:
    """Build independently sourced Data V3 coverage facts for one request window."""
    if market not in {"CN", "HK", "US"} or (market != "CN" and not allow_unknown_listing):
        return None
    try:
        requested_start = date.fromisoformat(start)
        requested_end = date.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    if requested_start > requested_end:
        return None

    listing = market_listing_fact_v3(symbol)
    first_trading_date = None
    if listing is None:
        if not allow_unknown_listing or not isinstance(symbol, str) or not symbol.strip():
            return None
        listing = {"symbol": symbol.strip(), "status": "unknown"}
    else:
        try:
            first_trading_date = date.fromisoformat(listing["firstTradingDate"])
        except (KeyError, TypeError, ValueError):
            return None
        if first_trading_date > requested_end:
            return None

    raw_calendar = calendar_evidence_provider(market, start, end)
    if not isinstance(raw_calendar, Mapping):
        return None
    calendar = dict(raw_calendar)
    expected_keys = {
        "market",
        "timezone",
        "source",
        "revision",
        "supportedRange",
        "expectedSessionDates",
    }
    if set(calendar) != expected_keys:
        return None
    timezone = _MARKET_TIMEZONES_V3.get(market)
    if (
        calendar.get("market") != market
        or calendar.get("timezone") != timezone
        or not isinstance(calendar.get("source"), str)
        or not calendar["source"].strip()
        or not isinstance(calendar.get("revision"), str)
        or not calendar["revision"].strip()
    ):
        return None

    supported = calendar.get("supportedRange")
    if not isinstance(supported, Mapping) or set(supported) != {"start", "end"}:
        return None
    try:
        supported_start = date.fromisoformat(str(supported["start"]))
        supported_end = date.fromisoformat(str(supported["end"]))
    except (KeyError, TypeError, ValueError):
        return None
    if (
        supported_start > supported_end
        or supported_start > requested_start
        or supported_end < requested_end
    ):
        return None

    expected_sessions = calendar.get("expectedSessionDates")
    if not isinstance(expected_sessions, list):
        return None
    parsed_sessions: list[date] = []
    try:
        parsed_sessions = [date.fromisoformat(value) for value in expected_sessions]
    except (TypeError, ValueError):
        return None
    if parsed_sessions != sorted(set(parsed_sessions)):
        return None
    if any(
        session < requested_start
        or session > requested_end
        or session < supported_start
        or session > supported_end
        for session in parsed_sessions
    ):
        return None

    expected_post_listing = [
        session.isoformat() for session in parsed_sessions
        if first_trading_date is None or session >= first_trading_date
    ]
    window = {
        "status": "complete",
        "requestedStart": start,
        "requestedEnd": end,
    }
    return {
        "calendar": calendar,
        "listing": listing,
        "window": window,
        "expectedPostListingSessionDates": expected_post_listing,
    }


def canonical_market_coverage_proof_v3(proof: Mapping[str, Any]) -> list[Any]:
    """Encode the proof in the same stable field order as the Schema V3 helper."""
    from src.services.thesis_ledger_listing_information import canonical_listing_information

    calendar = proof["calendar"]
    listing = proof["listing"]
    window = proof["window"]
    pagination = proof["pagination"]
    supported = calendar["supportedRange"]
    return [
        3,
        [
            calendar["market"],
            calendar["timezone"],
            calendar["source"],
            calendar["revision"],
            [supported["start"], supported["end"]],
            calendar["expectedSessionDates"],
        ],
        canonical_listing_information(listing),
        [window["status"], window["requestedStart"], window["requestedEnd"]],
        [
            pagination["status"],
            pagination["pagesFetched"],
            pagination["continuationPending"],
        ],
    ]
