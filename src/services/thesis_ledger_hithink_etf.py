"""HiThink ETF daily bars with explicit request, calendar, and basis evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from src.services.thesis_ledger_hithink_pagination import has_unverified_pagination as _pagination_state


HITHINK_ETF_HISTORICAL_URL = "https://fuyao.aicubes.cn/api/fund/market/historical"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SYMBOL_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")
_REQUIRED_BAR_FIELDS = {
    "date_ms",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "volume",
    "turnover",
}


class HiThinkETFAdapterError(Exception):
    """Stable, sanitized failure with machine-readable coverage diagnostics."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        response_fingerprint: str | None = None,
        provider_code: int | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.response_fingerprint = response_fingerprint
        self.provider_code = provider_code
        self.diagnostics = dict(diagnostics or {})
        message = _ERROR_MESSAGES.get(code, "HiThink ETF 历史行情不可用")
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class HiThinkETFCalendarEvidence:
    """Versioned exchange sessions plus an independently sourced listing date.

    ``market_sessions`` contains all XSHG market sessions in or around the
    requested range before applying the listing-date filter. Callers must use
    a versioned independent calendar and an authoritative listing record.
    """

    symbol: str
    calendar_name: str
    calendar_version: str
    market_sessions: tuple[str, ...]
    listing_date: str
    listing_source: str
    listing_source_version: str


@dataclass(frozen=True, slots=True)
class HiThinkETFBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


@dataclass(frozen=True, slots=True)
class HiThinkETFBarSeries:
    symbol: str
    requested_start: str
    requested_end: str
    bars: tuple[HiThinkETFBar, ...]
    coverage: Mapping[str, Any]
    response_fingerprint: str
    adjustment: str = "qfq"
    adjustment_method: str = "provider-native"
    adjustment_method_version: str | None = None
    basis_scope: str = "provider-defined"
    adjustment_anchor: str | None = None
    price_currency: str = "provider-original-currency"
    volume_unit: str = "unknown"
    turnover_unit: str = "unknown"
    dividend_meaning: str = "provider-defined"
    missing_sessions: tuple[str, ...] = ()


_ERROR_MESSAGES = {
    "invalid_request": "HiThink ETF 行情请求参数无效",
    "missing_credentials": "HiThink API 凭据未配置",
    "unsupported_adjustment": "HiThink ETF 历史日线只支持前复权口径",
    "calendar_evidence_required": "缺少带版本的交易日历或上市日期证据",
    "invalid_calendar_evidence": "交易日历或上市日期证据无效",
    "unsupported_instrument": "HiThink ETF 历史日线不支持该标的",
    "credential_required": "HiThink API Key 未认证",
    "permission_denied": "HiThink API Key 无权访问该能力",
    "unknown_instrument": "HiThink 未找到该 ETF 标的",
    "data_not_ready": "HiThink 尚未准备该区间的数据",
    "rate_limited": "HiThink 请求频率受限",
    "provider_request_rejected": "HiThink 拒绝了历史行情请求",
    "upstream_unavailable": "HiThink 历史行情服务暂不可用",
    "transport_error": "HiThink 历史行情连接失败",
    "timeout": "HiThink 历史行情请求超时",
    "http_error": "HiThink 历史行情 HTTP 请求失败",
    "invalid_response": "HiThink ETF 历史行情响应格式无效",
    "pagination_unverified": "HiThink ETF 历史行情分页状态无法确认",
    "duplicate_bar_date": "HiThink ETF 历史行情包含重复日期",
    "calendar_mismatch": "HiThink ETF 历史行情日期不符合日历证据",
    "incomplete_coverage": "HiThink ETF 历史行情未覆盖全部预期交易日",
}


def fetch_hithink_etf_daily_bars(
    *,
    symbol: str,
    start: str,
    end: str,
    adjustment: str,
    api_key: str,
    calendar_evidence: HiThinkETFCalendarEvidence | None,
    http_get: Callable[..., Any] | None = None,
    timeout_seconds: float = 10.0,
    allow_missing_sessions: bool = False,
) -> HiThinkETFBarSeries:
    """Fetch and validate one ETF window through an injectable HTTP seam.

    The credential is used only in the outbound header. It is never included
    in a result, exception message, or diagnostic value. Callers own retries.
    """

    start_date = _parse_iso_date(start, "start")
    end_date = _parse_iso_date(end, "end")
    if not isinstance(allow_missing_sessions, bool):
        raise HiThinkETFAdapterError("invalid_request")
    _validate_request(symbol, start_date, end_date, adjustment, api_key, timeout_seconds)
    if calendar_evidence is None:
        raise HiThinkETFAdapterError("calendar_evidence_required")

    _validated_calendar_evidence(calendar_evidence, symbol, start_date, end_date)
    params = {
        "thscode": symbol,
        "interval": "1d",
        "start": _shanghai_midnight_epoch_ms(start_date),
        "end": _shanghai_midnight_epoch_ms(end_date),
    }
    headers = {"X-api-key": api_key.strip()}
    get = http_get or _default_http_get

    try:
        response = get(
            HITHINK_ETF_HISTORICAL_URL,
            params=params,
            headers=headers,
            timeout=timeout_seconds,
            allow_redirects=False,
        )
    except TimeoutError:
        raise HiThinkETFAdapterError("timeout", retryable=True) from None
    except Exception as exc:  # noqa: BLE001 - upstream exception text may contain request details.
        if "timeout" in type(exc).__name__.casefold():
            raise HiThinkETFAdapterError("timeout", retryable=True) from None
        raise HiThinkETFAdapterError("transport_error", retryable=True) from None

    if allow_missing_sessions and not isinstance(getattr(response, "content", None), bytes):
        raise HiThinkETFAdapterError("invalid_response")
    response_fingerprint = _response_fingerprint(response)
    status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int) or isinstance(status_code, bool):
        raise HiThinkETFAdapterError(
            "invalid_response", response_fingerprint=response_fingerprint
        )
    if status_code in {401, 403}:
        raise HiThinkETFAdapterError(
            "permission_denied", response_fingerprint=response_fingerprint
        )
    if status_code == 429:
        raise HiThinkETFAdapterError(
            "rate_limited", retryable=True, response_fingerprint=response_fingerprint
        )
    if status_code >= 500:
        raise HiThinkETFAdapterError(
            "upstream_unavailable", retryable=True, response_fingerprint=response_fingerprint
        )
    if status_code < 200 or status_code >= 300:
        raise HiThinkETFAdapterError("http_error", response_fingerprint=response_fingerprint)

    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - decoder details are intentionally suppressed.
        raise HiThinkETFAdapterError(
            "invalid_response", response_fingerprint=response_fingerprint
        ) from exc

    return parse_hithink_etf_daily_response(
        payload,
        symbol=symbol,
        start=start,
        end=end,
        calendar_evidence=calendar_evidence,
        response_fingerprint=response_fingerprint,
        allow_missing_sessions=allow_missing_sessions,
    )


def parse_hithink_etf_daily_response(
    payload: Any,
    *,
    symbol: str,
    start: str,
    end: str,
    calendar_evidence: HiThinkETFCalendarEvidence,
    response_fingerprint: str | None = None,
    allow_missing_sessions: bool = False,
) -> HiThinkETFBarSeries:
    """Parse ETF history; sparse output requires an explicit caller opt-in."""

    if not isinstance(allow_missing_sessions, bool):
        raise HiThinkETFAdapterError("invalid_request")
    start_date = _parse_iso_date(start, "start")
    end_date = _parse_iso_date(end, "end")
    _validate_request(symbol, start_date, end_date, "qfq", None, 1.0)
    evidence = _validated_calendar_evidence(calendar_evidence, symbol, start_date, end_date)
    fingerprint = response_fingerprint or _payload_fingerprint(payload)

    if not isinstance(payload, Mapping):
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint)
    code = payload.get("code")
    if not isinstance(code, int) or isinstance(code, bool):
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint)
    if code != 0:
        _raise_provider_error(code, fingerprint)

    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint)
    if (
        data.get("thscode") != symbol
        or data.get("interval") != "1d"
        or "adjust" not in data
        or data.get("adjust") is not None
        or (
            data.get("timestamp") is not None
            and (
                not isinstance(data.get("timestamp"), int)
                or isinstance(data.get("timestamp"), bool)
            )
        )
    ):
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint)
    if _pagination_state(data) or _pagination_state(payload):
        raise HiThinkETFAdapterError("pagination_unverified", response_fingerprint=fingerprint)

    items = data.get("item")
    if not isinstance(items, list):
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint)
    if items and data.get("timestamp") is None:
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint)

    bars_by_date: dict[str, HiThinkETFBar] = {}
    returned_dates: list[str] = []
    for item in items:
        bar = _parse_bar(item, fingerprint)
        if not start_date.isoformat() <= bar.date <= end_date.isoformat():
            raise HiThinkETFAdapterError(
                "calendar_mismatch",
                response_fingerprint=fingerprint,
                diagnostics={"unexpectedSessions": [bar.date], **evidence.summary},
            )
        if bar.date in bars_by_date:
            raise HiThinkETFAdapterError(
                "duplicate_bar_date",
                response_fingerprint=fingerprint,
                diagnostics={"duplicateSession": bar.date, **evidence.summary},
            )
        bars_by_date[bar.date] = bar
        returned_dates.append(bar.date)

    if returned_dates != sorted(returned_dates):
        raise HiThinkETFAdapterError(
            "invalid_response", response_fingerprint=fingerprint,
            diagnostics={"reason": "bars_not_ascending", **evidence.summary},
        )

    expected = evidence.expected_sessions
    actual_set = set(returned_dates)
    expected_set = set(expected)
    unexpected = sorted(actual_set - expected_set)
    prelisting = sorted(
        session for session in actual_set if session < evidence.listing_date
    )
    if unexpected or prelisting:
        raise HiThinkETFAdapterError(
            "calendar_mismatch",
            response_fingerprint=fingerprint,
            diagnostics={
                **evidence.summary,
                "unexpectedSessions": unexpected,
                "prelistingRows": prelisting,
            },
        )

    response_timestamp = data.get("timestamp")
    if returned_dates and response_timestamp is not None:
        latest_response_date = (
            datetime.fromtimestamp(response_timestamp / 1000, tz=timezone.utc)
            .astimezone(_SHANGHAI)
            .date()
            .isoformat()
        )
        if latest_response_date != returned_dates[-1]:
            raise HiThinkETFAdapterError(
                "invalid_response",
                response_fingerprint=fingerprint,
                diagnostics={"reason": "data_timestamp_mismatch", **evidence.summary},
            )

    missing = sorted(expected_set - actual_set)
    if missing and not allow_missing_sessions:
        raise HiThinkETFAdapterError(
            "incomplete_coverage",
            response_fingerprint=fingerprint,
            diagnostics={
                **evidence.summary,
                "coverageStatus": "missing_expected_sessions",
                "missingSessions": missing,
                "missingSessionCount": len(missing),
                "receivedSessionCount": len(actual_set),
                "receivedSessionsFingerprint": _string_set_fingerprint(returned_dates),
            },
        )

    coverage_status = "missing_expected_sessions" if missing else (
        "complete" if expected else "no_expected_sessions"
    )
    coverage = {
        **evidence.summary,
        "requestedStart": start,
        "requestedEnd": end,
        "windowSemantics": "single_request_only",
        "maximumWindowYears": 5,
        "coverageStatus": coverage_status,
        "expectedSessionCount": len(expected),
        "expectedSessionsFingerprint": _string_set_fingerprint(expected),
        "receivedSessionCount": len(returned_dates),
        "receivedSessionsFingerprint": _string_set_fingerprint(returned_dates),
        "missingSessionCount": len(missing),
        "missingSessionsFingerprint": _string_set_fingerprint(missing),
        "unexpectedSessionCount": 0,
        "prelistingRowCount": 0,
        "responsePagination": "no_additional_page_signaled",
    }
    return HiThinkETFBarSeries(
        symbol=symbol,
        requested_start=start,
        requested_end=end,
        bars=tuple(bars_by_date[day] for day in returned_dates),
        coverage=coverage,
        response_fingerprint=fingerprint,
        missing_sessions=tuple(missing),
    )


@dataclass(frozen=True, slots=True)
class _ValidatedCalendar:
    listing_date: str
    expected_sessions: tuple[str, ...]
    summary: Mapping[str, Any]


def _validated_calendar_evidence(
    evidence: HiThinkETFCalendarEvidence,
    symbol: str,
    start_date: date,
    end_date: date,
) -> _ValidatedCalendar:
    if not isinstance(evidence, HiThinkETFCalendarEvidence):
        raise HiThinkETFAdapterError("calendar_evidence_required")
    required_text = (
        evidence.symbol,
        evidence.calendar_name,
        evidence.calendar_version,
        evidence.listing_source,
        evidence.listing_source_version,
    )
    if any(not isinstance(value, str) or not value.strip() for value in required_text):
        raise HiThinkETFAdapterError("invalid_calendar_evidence")
    if evidence.symbol != symbol or not isinstance(evidence.market_sessions, (tuple, list)):
        raise HiThinkETFAdapterError("invalid_calendar_evidence")

    try:
        listing_date = _parse_iso_date(evidence.listing_date, "listing_date")
        sessions = tuple(_parse_iso_date(value, "market_session").isoformat() for value in evidence.market_sessions)
    except (HiThinkETFAdapterError, TypeError, ValueError):
        raise HiThinkETFAdapterError("invalid_calendar_evidence") from None
    if len(set(sessions)) != len(sessions) or tuple(sorted(sessions)) != sessions:
        raise HiThinkETFAdapterError("invalid_calendar_evidence")

    sessions_in_window = tuple(
        session for session in sessions if start_date.isoformat() <= session <= end_date.isoformat()
    )
    prelisting_sessions = tuple(session for session in sessions_in_window if session < listing_date.isoformat())
    expected_sessions = tuple(session for session in sessions_in_window if session >= listing_date.isoformat())
    if any(session > end_date.isoformat() for session in sessions_in_window):
        raise HiThinkETFAdapterError("invalid_calendar_evidence")

    summary = {
        "requestedStart": start_date.isoformat(),
        "requestedEnd": end_date.isoformat(),
        "calendarName": evidence.calendar_name,
        "calendarVersion": evidence.calendar_version,
        "listingDate": listing_date.isoformat(),
        "listingSource": evidence.listing_source,
        "listingSourceVersion": evidence.listing_source_version,
        "marketSessionCount": len(sessions_in_window),
        "marketSessionsFingerprint": _string_set_fingerprint(sessions_in_window),
        "prelistingSessionCount": len(prelisting_sessions),
        "prelistingSessionsFingerprint": _string_set_fingerprint(prelisting_sessions),
        "expectedSessionCount": len(expected_sessions),
        "expectedSessionsFingerprint": _string_set_fingerprint(expected_sessions),
    }
    return _ValidatedCalendar(
        listing_date=listing_date.isoformat(),
        expected_sessions=expected_sessions,
        summary=summary,
    )


def _parse_bar(item: Any, fingerprint: str) -> HiThinkETFBar:
    if not isinstance(item, Mapping) or not _REQUIRED_BAR_FIELDS.issubset(item):
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint)
    date_ms = item.get("date_ms")
    if not isinstance(date_ms, int) or isinstance(date_ms, bool):
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint)
    try:
        local_datetime = datetime.fromtimestamp(date_ms / 1000, tz=timezone.utc).astimezone(_SHANGHAI)
    except (OverflowError, OSError, ValueError) as exc:
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint) from exc
    if local_datetime.timetz().replace(tzinfo=None) != time.min:
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint)
    values = {
        "open": _finite_number(item.get("open_price"), "open_price", fingerprint),
        "high": _finite_number(item.get("high_price"), "high_price", fingerprint),
        "low": _finite_number(item.get("low_price"), "low_price", fingerprint),
        "close": _finite_number(item.get("close_price"), "close_price", fingerprint),
        "volume": _finite_number(item.get("volume"), "volume", fingerprint),
        "amount": _finite_number(item.get("turnover"), "turnover", fingerprint),
    }
    if min(values[name] for name in ("open", "high", "low", "close")) <= 0:
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint)
    if values["high"] < max(values["open"], values["low"], values["close"]):
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint)
    if values["low"] > min(values["open"], values["high"], values["close"]):
        raise HiThinkETFAdapterError("invalid_response", response_fingerprint=fingerprint)
    return HiThinkETFBar(date=local_datetime.date().isoformat(), **values)


def _finite_number(value: Any, field: str, fingerprint: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise HiThinkETFAdapterError(
            "invalid_response",
            response_fingerprint=fingerprint,
            diagnostics={"invalidField": field},
        )
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise HiThinkETFAdapterError(
            "invalid_response",
            response_fingerprint=fingerprint,
            diagnostics={"invalidField": field},
        )
    return number


def _validate_request(
    symbol: str,
    start_date: date,
    end_date: date,
    adjustment: str,
    api_key: str | None,
    timeout_seconds: float,
) -> None:
    if not isinstance(symbol, str) or not _SYMBOL_PATTERN.fullmatch(symbol):
        raise HiThinkETFAdapterError("unsupported_instrument")
    if start_date > end_date:
        raise HiThinkETFAdapterError("invalid_request")
    if end_date > _add_calendar_years(start_date, 5):
        raise HiThinkETFAdapterError("invalid_request")
    if adjustment != "qfq":
        raise HiThinkETFAdapterError("unsupported_adjustment")
    if api_key is not None and (not isinstance(api_key, str) or not api_key.strip()):
        raise HiThinkETFAdapterError("missing_credentials")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
        raise HiThinkETFAdapterError("invalid_request")
    if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
        raise HiThinkETFAdapterError("invalid_request")


def _parse_iso_date(value: str, field: str) -> date:
    if not isinstance(value, str):
        raise HiThinkETFAdapterError("invalid_request", diagnostics={"invalidField": field})
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HiThinkETFAdapterError("invalid_request", diagnostics={"invalidField": field}) from exc
    if parsed.isoformat() != value:
        raise HiThinkETFAdapterError("invalid_request", diagnostics={"invalidField": field})
    return parsed


def _add_calendar_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _shanghai_midnight_epoch_ms(value: date) -> int:
    local_midnight = datetime.combine(value, time.min, tzinfo=_SHANGHAI)
    return int(local_midnight.timestamp() * 1000)


def _raise_provider_error(code: int, fingerprint: str) -> None:
    mapped = {
        1001: ("provider_request_rejected", False),
        1002: ("provider_request_rejected", False),
        1003: ("provider_request_rejected", False),
        1004: ("provider_request_rejected", False),
        2001: ("credential_required", False),
        2003: ("permission_denied", False),
        3001: ("unknown_instrument", False),
        3002: ("data_not_ready", False),
        3004: ("unsupported_instrument", False),
        4001: ("rate_limited", True),
        5001: ("upstream_unavailable", True),
        5002: ("upstream_unavailable", True),
        5003: ("upstream_unavailable", True),
    }
    error = mapped.get(code, ("provider_request_rejected", False))
    raise HiThinkETFAdapterError(
        error[0],
        retryable=error[1],
        response_fingerprint=fingerprint,
        provider_code=code,
    )


def _string_set_fingerprint(values: Sequence[str]) -> str:
    canonical = json.dumps(list(values), ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _payload_fingerprint(payload: Any) -> str:
    try:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        canonical = type(payload).__qualname__.encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _response_fingerprint(response: Any) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return hashlib.sha256(content).hexdigest()
    try:
        return _payload_fingerprint(response.json())
    except Exception:  # noqa: BLE001 - fingerprint fallback must not expose response data.
        return hashlib.sha256(type(response).__qualname__.encode("utf-8")).hexdigest()


def _default_http_get(*args: Any, **kwargs: Any) -> Any:
    import requests

    return requests.get(*args, **kwargs)
