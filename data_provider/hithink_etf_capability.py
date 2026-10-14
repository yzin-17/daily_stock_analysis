"""HiThink ETF 原专用读取的能力适配与既有请求元信息。"""

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_hithink_etf import HITHINK_ETF_HISTORICAL_URL
from collections.abc import Mapping
from src.services.thesis_ledger_daily_units_contract import DailyFieldUnits
from src.services.thesis_ledger_daily_window_contract import DailyWindow, DailyWindowPlan
from datetime import date

HITHINK_ETF_HISTORY_SOURCE = "fund-market-historical"
HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1 = "hithink-etf-single-response-local-v1"
HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1 = "dsa-hithink-etf-request-contract-v1"
HITHINK_ETF_LOCAL_COVERAGE_PROTOCOL_V1 = "dsa-hithink-etf-independent-calendar-session-equality-v1"

SOURCE_CONTRACT = {
    "providerId": "hithink", "upstreamSource": HITHINK_ETF_HISTORY_SOURCE,
    "endpoint": HITHINK_ETF_HISTORICAL_URL,
    "sourceContractRevision": HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
    "paginationProtocol": HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1,
    "continuationCursorSupported": False, "maximumRows": None, "maximumRowsKnown": False,
    "upstreamPaginationVerified": False, "requestWindowMaximumYears": 5,
    "coverageProtocol": HITHINK_ETF_LOCAL_COVERAGE_PROTOCOL_V1,
    "upstreamCoverageVerified": False, "upstreamDataRevision": None, "adjustmentAlgorithmRevision": None,
}


def read_full_etf(request, snapshot, timeout_seconds=4.5):
    from src.services.thesis_ledger_market_v3_facts import market_listing_fact_v3, market_calendar_evidence_v3
    """Lazily invoke the exact HiThink ETF adapter after V3 admission checks."""
    if (
        request.capability != "DAILY_BAR"
        or request.instrument_type != "ETF"
        or request.timeframe != "1d"
        or request.adjustment != "qfq"
        or request.start is None
        or request.end is None
    ):
        raise CapabilityContractError("unsupported_source", "HiThink V3 仅登记 ETF qfq 日线")

    listing = market_listing_fact_v3(request.symbol)
    calendar = market_calendar_evidence_v3("CN", request.start, request.end)
    if (
        listing is None
        or calendar is None
        or not isinstance(calendar.get("expectedSessionDates"), list)
    ):
        raise CapabilityContractError("not_covered", "HiThink ETF 缺少本地范围证据")

    if snapshot is None:
        raise CapabilityContractError("not_configured", "HiThink API 凭据未与当前 admission 复核")
    api_key = snapshot.values.get("apiKey")
    if (
        snapshot.source not in {"environment", "control"}
        or snapshot.method != "api_key"
        or not isinstance(api_key, str)
        or not api_key.strip()
    ):
        raise CapabilityContractError("not_configured", "HiThink API 凭据未配置")

    from src.services.thesis_ledger_hithink_etf import (
        HiThinkETFAdapterError,
        HiThinkETFCalendarEvidence,
        fetch_hithink_etf_daily_bars,
    )

    calendar_evidence = HiThinkETFCalendarEvidence(
        symbol=request.symbol,
        calendar_name="SZSE",
        calendar_version=str(calendar["revision"]),
        market_sessions=tuple(calendar["expectedSessionDates"]),
        listing_date=listing["firstTradingDate"],
        listing_source=listing["source"],
        listing_source_version=listing["revision"],
    )
    try:
        result = fetch_hithink_etf_daily_bars(
            symbol=request.symbol,
            start=request.start,
            end=request.end,
            adjustment="qfq",
            api_key=api_key,
            calendar_evidence=calendar_evidence,
            timeout_seconds=min(timeout_seconds, request.parameters.get("target_timeout_seconds", 4.5)),
            allow_missing_sessions=request.parameters.get("historical_tradability") is True,
        )
    except HiThinkETFAdapterError as exc:
        raise CapabilityContractError(
            exc.code,
            str(exc),
            retryable=exc.retryable,
            diagnostics=exc.diagnostics,
        ) from exc

    from src.services.thesis_ledger_hithink_etf_tradability import hithink_etf_frame
    frame = hithink_etf_frame(
        result, listing=listing, calendar=calendar,
        upstream_source=HITHINK_ETF_HISTORY_SOURCE,
        historical_tradability=request.parameters.get("historical_tradability") is True,
    )
    if result.volume_unit != "unknown" or result.turnover_unit != "unknown":
        raise CapabilityContractError("invalid_response", "HiThink ETF 适配器单位合同已变更")
    from src.services.thesis_ledger_hithink_etf_units import hithink_etf_field_contract

    frame.attrs["hithink_etf_field_units"] = hithink_etf_field_contract(
        symbol=request.symbol,
        start=request.start,
        end=request.end,
        source_revision=HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
    )
    return frame


def project_public_units(context):
    from src.services.thesis_ledger_hithink_etf_units import hithink_etf_field_contract_is_valid
    contract = context.metadata.get('hithink_etf_field_units')
    if contract is None:
        return None
    if (not isinstance(contract, Mapping) or not hithink_etf_field_contract_is_valid(
            dict(contract), dict(context.request) if isinstance(context.request, Mapping) else None)):
        raise ValueError('invalid HiThink ETF field unit contract')
    return DailyFieldUnits(volume=contract['volumeUnit'], amount=contract['amountUnit'])


def plan_hithink_windows(start: str, end: str, sessions: list[str], max_requests: int) -> list[tuple[str, str]]:
    from src.services.thesis_ledger_hithink_etf import _add_calendar_years
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or not isinstance(max_requests, int) or isinstance(max_requests, bool) or max_requests < 1:
        raise ValueError('多窗口请求预算或日期非法')
    if sessions != sorted(set(sessions)):
        raise ValueError('交易日必须升序且唯一')
    windows = []
    while first <= last:
        if len(windows) >= max_requests:
            raise ValueError('多窗口请求次数超限')
        boundary = min(_add_calendar_years(first, 5), last)
        windows.append((first.isoformat(), boundary.isoformat()))
        if boundary == last:
            return windows
        overlap = [day for day in sessions if first.isoformat() < day <= boundary.isoformat()]
        if not overlap:
            raise ValueError('窗口分界缺少交易日交集')
        first = date.fromisoformat(overlap[-1])
    return windows


def plan_public_windows(context):
    windows = plan_hithink_windows(context.start, context.end, list(context.sessions), context.max_requests)
    return DailyWindowPlan(windows=tuple(DailyWindow(start=start, end=end) for start, end in windows))

