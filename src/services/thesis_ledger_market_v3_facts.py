"""Data V3 独立日历与上市事实，不从行情结果推断边界。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Mapping

from data_provider.tencent_native_daily import MAX_ROWS, TENCENT_NATIVE_DAILY_PROTOCOL
from src.services.thesis_ledger_hithink_etf import HITHINK_ETF_HISTORICAL_URL


HITHINK_ETF_HISTORY_SOURCE = "fund-market-historical"
HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1 = "hithink-etf-single-response-local-v1"
HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1 = "dsa-hithink-etf-request-contract-v1"
HITHINK_ETF_NATIVE_FIELD_CONTRACT_V1 = "dsa-hithink-etf-native-field-units-v1"
HITHINK_ETF_NATIVE_FIELD_CONTRACT_V2 = "dsa-hithink-etf-native-field-units-v2"
HITHINK_ETF_LOCAL_COVERAGE_PROTOCOL_V1 = (
    "dsa-hithink-etf-independent-calendar-session-equality-v1"
)


_CN_LISTING_FACTS_V3: dict[str, dict[str, str]] = {
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
    if (asset, provider_id, source) == ("ETF", "tushare", "tushare"):
        from data_provider.tushare_fund_daily import PAGINATION_PROTOCOL

        return {"protocol": PAGINATION_PROTOCOL, "maximumRows": None}
    source_contract = market_source_contract_v3(asset, provider_id, source)
    if source_contract is not None:
        return {
            "protocol": source_contract["paginationProtocol"],
            "maximumRows": source_contract["maximumRows"],
        }
    if provider_id == "akshare":
        protocols = {
            ("ETF", "eastmoney"): ("akshare-etf-range-response-v1", None),
            ("STOCK", "eastmoney"): ("akshare-stock-eastmoney-range-response-v1", None),
            ("STOCK", "sina"): ("akshare-stock-sina-range-response-v1", None),
            ("STOCK", "tencent"): ("akshare-stock-tencent-range-response-v1", None),
        }
        contract = protocols.get((asset, source))
        if contract is None:
            return None
        protocol, maximum_rows = contract
        return {"protocol": protocol, "maximumRows": maximum_rows}
    if provider_id == "tencent" and source == "tencent":
        return {"protocol": TENCENT_NATIVE_DAILY_PROTOCOL, "maximumRows": MAX_ROWS}
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
    if identity != ("ETF", "hithink", HITHINK_ETF_HISTORY_SOURCE):
        return None
    return {
        "providerId": "hithink",
        "upstreamSource": HITHINK_ETF_HISTORY_SOURCE,
        "endpoint": HITHINK_ETF_HISTORICAL_URL,
        "sourceContractRevision": HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
        "paginationProtocol": HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1,
        "continuationCursorSupported": False,
        "maximumRows": None,
        "maximumRowsKnown": False,
        "upstreamPaginationVerified": False,
        "requestWindowMaximumYears": 5,
        "coverageProtocol": HITHINK_ETF_LOCAL_COVERAGE_PROTOCOL_V1,
        "upstreamCoverageVerified": False,
        "upstreamDataRevision": None,
        "adjustmentAlgorithmRevision": None,
    }


def market_calendar_evidence_v3(
    market: str,
    start: str,
    end: str,
) -> dict[str, Any] | None:
    """Return the finite 2026 SZSE calendar supported by the official notice."""
    if market != "CN":
        return None
    try:
        requested_start = date.fromisoformat(start)
        requested_end = date.fromisoformat(end)
        supported_start = date.fromisoformat(_CN_CALENDAR_WINDOW_V3["start"])
        supported_end = date.fromisoformat(_CN_CALENDAR_WINDOW_V3["end"])
    except (TypeError, ValueError):
        return None
    if (
        requested_start > requested_end
        or requested_start < supported_start
        or requested_end > supported_end
    ):
        return None

    expected_sessions: list[str] = []
    current = requested_start
    while current <= requested_end:
        value = current.isoformat()
        if current.weekday() < 5 and value not in _CN_CALENDAR_WINDOW_V3["holidays"]:
            expected_sessions.append(value)
        current += timedelta(days=1)

    return {
        "market": market,
        "timezone": _MARKET_TIMEZONES_V3[market],
        "source": _CN_CALENDAR_WINDOW_V3["source"],
        "revision": _CN_CALENDAR_WINDOW_V3["revision"],
        "supportedRange": {
            "start": _CN_CALENDAR_WINDOW_V3["start"],
            "end": _CN_CALENDAR_WINDOW_V3["end"],
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
) -> dict[str, Any] | None:
    """Build independently sourced Data V3 coverage facts for one request window."""
    if market != "CN":
        return None
    try:
        requested_start = date.fromisoformat(start)
        requested_end = date.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    if requested_start > requested_end:
        return None

    listing = market_listing_fact_v3(symbol)
    if listing is None:
        return None
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
        session.isoformat() for session in parsed_sessions if session >= first_trading_date
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
        [
            listing["symbol"],
            listing["firstTradingDate"],
            listing["source"],
            listing["revision"],
            listing["knownAt"],
        ],
        [window["status"], window["requestedStart"], window["requestedEnd"]],
        [
            pagination["status"],
            pagination["pagesFetched"],
            pagination["continuationPending"],
        ],
    ]
