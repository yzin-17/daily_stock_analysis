"""HiThink 股票历史适配器；每次显式指定口径，避免默认前复权混入原始行情。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from src.services.thesis_ledger_hithink_pagination import has_unverified_pagination

HITHINK_STOCK_HISTORICAL_URL = (
    "https://fuyao.aicubes.cn/api/a-share/prices/historical"
)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SYMBOL_PATTERN = re.compile(r"^[0-9]{6}\.(?:SH|SZ|BJ)$")
_ADJUSTMENTS = {
    "none": "none",
    "qfq": "forward",
    "hfq": "backward",
}
_ERRORS: dict[int, tuple[str, str, bool]] = {
    1001: ("invalid_request", "HiThink 历史行情请求缺少必填参数", False),
    1002: ("invalid_request", "HiThink 历史行情请求参数格式无效", False),
    1003: ("range_exceeded", "HiThink 历史行情请求范围超限", False),
    1004: ("invalid_request", "HiThink 历史行情请求参数冲突", False),
    2001: ("authentication_failed", "HiThink 凭据未通过认证", False),
    2003: ("permission_denied", "HiThink 账号无权访问该行情能力", False),
    3001: ("symbol_not_found", "HiThink 未识别该股票代码", False),
    3002: ("data_not_ready", "HiThink 股票行情数据尚未准备", True),
    3004: ("unsupported_capability", "HiThink 不支持该股票行情能力", False),
    4001: ("rate_limited", "HiThink 行情请求触发限流", True),
    5001: ("upstream_failure", "HiThink 行情服务暂时不可用", True),
    5002: ("upstream_failure", "HiThink 上游行情暂时不可用", True),
    5003: ("upstream_failure", "HiThink 行情服务暂时不可用", True),
}


class HiThinkStockError(Exception):
    """Classified adapter error with no upstream body or credential content."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.request_id = request_id


HttpGet = Callable[..., Any]
Clock = Callable[[], datetime]


def _parse_date(value: str | date, field: str) -> date:
    if isinstance(value, datetime):
        raise HiThinkStockError("invalid_request", f"{field} 必须是上海自然日")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise HiThinkStockError("invalid_request", f"{field} 必须使用 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HiThinkStockError("invalid_request", f"{field} 不是有效日期") from exc
    if parsed.isoformat() != value:
        raise HiThinkStockError("invalid_request", f"{field} 必须使用 YYYY-MM-DD")
    return parsed


def _local_timestamp_ms(day: date, *, end_of_day: bool) -> int:
    local_time = time(23, 59, 59, 999000) if end_of_day else time.min
    value = datetime.combine(day, local_time, tzinfo=_SHANGHAI)
    return int(value.timestamp() * 1000)


def _date_from_provider_ms(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HiThinkStockError("invalid_response", "HiThink 行情日期格式无效")
    try:
        parsed = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise HiThinkStockError("invalid_response", "HiThink 行情日期超出范围") from exc
    return parsed.astimezone(_SHANGHAI).date().isoformat()


def _decimal(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise HiThinkStockError("invalid_response", f"HiThink 行情字段 {field} 无效")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HiThinkStockError("invalid_response", f"HiThink 行情字段 {field} 无效") from exc
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise HiThinkStockError("invalid_response", f"HiThink 行情字段 {field} 无效")
    return result


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _request_id(payload: Any, forbidden_value: str) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("request_id")
    if (
        isinstance(value, str)
        and value
        and len(value) <= 128
        and forbidden_value not in value
    ):
        return value
    return None


def _business_code(payload: Any) -> int | None:
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get("code")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and re.fullmatch(r"-?\d+", raw.strip()):
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def _safe_http_error(status_code: int, request_id: str | None) -> HiThinkStockError:
    if status_code in {401, 403}:
        code, message = (
            ("authentication_failed", "HiThink 凭据未通过认证")
            if status_code == 401
            else ("permission_denied", "HiThink 账号无权访问该行情能力")
        )
        return HiThinkStockError(code, message, request_id=request_id)
    if status_code == 429:
        return HiThinkStockError(
            "rate_limited", "HiThink 行情请求触发限流", retryable=True,
            request_id=request_id,
        )
    if status_code >= 500:
        return HiThinkStockError(
            "upstream_failure", "HiThink 行情服务暂时不可用", retryable=True,
            request_id=request_id,
        )
    return HiThinkStockError(
        "upstream_failure", "HiThink 行情请求未成功", request_id=request_id
    )


class HiThinkStockHistoricalAdapter:
    """Fetch and validate one explicit HiThink stock adjustment series.

    ``expected_sessions`` and ``calendar_revision`` are required so the caller
    supplies a target-market calendar proof.  The adapter does not assume that
    Shanghai Stock Exchange sessions are interchangeable with Shenzhen Stock
    Exchange sessions.
    """

    provider_id = "hithink"
    upstream_source = "hithink-financial-api"
    _method_version = "provider-defined-unversioned"

    def __init__(
        self,
        api_key: str,
        *,
        http_get: HttpGet | None = None,
        timeout_seconds: float = 10.0,
        clock: Clock | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise HiThinkStockError("not_configured", "HiThink API Key 未配置")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须为正数")
        self._api_key = api_key
        self._http_get = http_get or requests.get
        self._timeout_seconds = timeout_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch_daily_bars(
        self,
        symbol: str,
        start: str | date,
        end: str | date,
        adjustment: str,
        *,
        expected_sessions: Sequence[str | date],
        calendar_revision: str,
    ) -> pd.DataFrame:
        """Return a complete daily frame with explicit source semantics."""
        normalized_symbol = str(symbol or "").strip().upper()
        if not _SYMBOL_PATTERN.fullmatch(normalized_symbol):
            raise HiThinkStockError("invalid_request", "股票代码必须包含六位代码和交易所后缀")

        normalized_adjustment = str(adjustment or "").strip().lower()
        upstream_adjustment = _ADJUSTMENTS.get(normalized_adjustment)
        if upstream_adjustment is None:
            raise HiThinkStockError("unsupported_adjustment", "股票复权口径必须为 none、qfq 或 hfq")

        start_date = _parse_date(start, "start")
        end_date = _parse_date(end, "end")
        if end_date < start_date:
            raise HiThinkStockError("invalid_request", "end 不得早于 start")
        ten_year_boundary = date(
            start_date.year + 10,
            start_date.month,
            min(start_date.day, _days_in_month(start_date.year + 10, start_date.month)),
        )
        if end_date >= ten_year_boundary:
            raise HiThinkStockError("range_exceeded", "单次股票历史行情范围不得达到或超过十年")

        if not isinstance(calendar_revision, str) or not calendar_revision.strip():
            raise HiThinkStockError("invalid_request", "必须提供目标市场日历版本")
        sessions = _normalize_expected_sessions(expected_sessions, start_date, end_date)

        params = {
            "thscode": normalized_symbol,
            "interval": "1d",
            "start": _local_timestamp_ms(start_date, end_of_day=False),
            "end": _local_timestamp_ms(end_date, end_of_day=True),
            "adjust": upstream_adjustment,
            "offset": 0,
        }
        try:
            response = self._http_get(
                HITHINK_STOCK_HISTORICAL_URL,
                params=params,
                headers={"X-api-key": self._api_key, "Accept": "application/json"},
                timeout=self._timeout_seconds,
            )
        except requests.RequestException:
            raise HiThinkStockError(
                "network_failure", "HiThink 行情请求网络失败", retryable=True
            ) from None
        except Exception:
            # The transport seam may raise a non-requests network exception.
            # Do not copy its text: it may contain request or credential data.
            raise HiThinkStockError(
                "network_failure", "HiThink 行情请求网络失败", retryable=True
            ) from None

        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, bool) or not isinstance(status_code, int):
            raise HiThinkStockError("invalid_response", "HiThink HTTP 响应状态无效")
        try:
            payload = response.json()
        except Exception:
            if status_code >= 400:
                raise _safe_http_error(status_code, None) from None
            raise HiThinkStockError("invalid_response", "HiThink 行情响应不是有效 JSON") from None

        request_id = _request_id(payload, self._api_key)
        if status_code >= 400:
            raise _safe_http_error(status_code, request_id)
        if status_code < 200 or status_code >= 300:
            raise HiThinkStockError(
                "upstream_failure", "HiThink 行情请求未成功", request_id=request_id
            )
        if not isinstance(payload, Mapping) or "code" not in payload:
            raise HiThinkStockError(
                "invalid_response", "HiThink 行情响应缺少业务状态", request_id=request_id
            )
        code = _business_code(payload)
        if code is None:
            raise HiThinkStockError(
                "invalid_response", "HiThink 行情响应业务状态无效", request_id=request_id
            )
        if code != 0:
            if code in _ERRORS:
                error_code, message, retryable = _ERRORS[code]
                raise HiThinkStockError(
                    error_code, message, retryable=retryable, request_id=request_id
                )
            raise HiThinkStockError(
                "provider_error", "HiThink 行情接口返回未知业务状态", request_id=request_id
            )

        if has_unverified_pagination(payload):
            raise HiThinkStockError("incomplete_coverage", "HiThink 股票分页状态未确认", request_id=request_id)
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            raise HiThinkStockError(
                "invalid_response", "HiThink 行情响应缺少 data", request_id=request_id
            )
        echoed_adjustment = data.get("adjust")
        if echoed_adjustment is not None and echoed_adjustment != upstream_adjustment:
            raise HiThinkStockError(
                "invalid_response", "HiThink 响应复权口径与请求不一致", request_id=request_id
            )
        # A present but null label is not a valid stock adjustment echo.  Null is
        # the ETF endpoint's convention and must not bleed into this adapter.
        if "adjust" in data and echoed_adjustment is None:
            raise HiThinkStockError(
                "invalid_response", "HiThink 股票响应复权口径缺失", request_id=request_id
            )

        ready_timestamp = data.get("timestamp")
        ready_at = _datetime_from_ms(ready_timestamp)
        raw_items = data.get("item")
        if not isinstance(raw_items, list):
            raise HiThinkStockError(
                "invalid_response", "HiThink 股票响应 item 必须为数组", request_id=request_id
            )
        bars = _normalize_items(raw_items, start_date, end_date, request_id)
        actual_sessions = {item["date"] for item in bars}
        missing_sessions = set(sessions) - actual_sessions
        if missing_sessions:
            raise HiThinkStockError(
                "incomplete_coverage", "HiThink 股票行情缺少目标市场交易日", request_id=request_id
            )
        if actual_sessions != set(sessions):
            raise HiThinkStockError(
                "invalid_response", "HiThink 股票行情包含非目标市场交易日", request_id=request_id
            )

        observed_at = self._observed_at()
        fingerprint = _content_fingerprint(
            normalized_symbol,
            normalized_adjustment,
            start_date,
            end_date,
            calendar_revision.strip(),
            bars,
        )
        frame = pd.DataFrame(
            [
                {
                    "date": item["date"],
                    "open": item["open"],
                    "high": item["high"],
                    "low": item["low"],
                    "close": item["close"],
                    "volume": item["volume"],
                    "amount": item["turnover"],
                }
                for item in bars
            ],
            columns=["date", "open", "high", "low", "close", "volume", "amount"],
        )
        frame.attrs["upstream_source"] = self.upstream_source
        frame.attrs["has_more_before"] = False
        frame.attrs["thesis_ledger_source"] = {
            "provider_id": self.provider_id,
            "upstream_source": self.upstream_source,
            "asset_type": "STOCK",
            "capability": "DAILY_BAR",
            "timeframe": "1d",
            "symbol": normalized_symbol,
            "adjustment": normalized_adjustment,
            "coverage": {
                "requested_start": start_date.isoformat(),
                "requested_end": end_date.isoformat(),
                "actual_start": bars[0]["date"] if bars else None,
                "actual_end": bars[-1]["date"] if bars else None,
                "complete": True,
                "calendar_revision": calendar_revision.strip(),
            },
            "units": {
                "price_currency": "CNY",
                "volume": "share",
                "turnover_currency": "CNY",
                "turnover_source_field": "turnover",
                "volume_adjustment_basis": "unknown",
                "turnover_adjustment_basis": "unknown",
            },
            "source_price_basis": {
                "adjustment": normalized_adjustment,
                "method": "provider-native",
                "method_version": self._method_version,
                "basis_scope": "provider-defined",
                "anchor": None,
                "revision": {"origin": "local-observation", "content_hash": fingerprint},
                "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                "volume_basis": "unknown",
                "dividend_meaning": "provider-defined",
                "dividend_evidence_ref": None,
                "conversion_available": False,
                "conversion_evidence_ref": None,
                "derivation": None,
            },
            "content_fingerprint": fingerprint,
            "upstream_latest_bar_valid_at": ready_at.isoformat().replace("+00:00", "Z"),
        }
        return frame

    def _observed_at(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise HiThinkStockError("invalid_response", "本地观测时钟必须含时区")
        return value.astimezone(timezone.utc)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return (date(year + 1, 1, 1) - date(year, month, 1)).days
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def _normalize_expected_sessions(
    values: Sequence[str | date], start: date, end: date
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise HiThinkStockError("invalid_request", "expected_sessions 必须是目标市场交易日序列")
    result: list[str] = []
    for value in values:
        parsed = _parse_date(value, "expected_sessions")
        if parsed < start or parsed > end:
            raise HiThinkStockError("invalid_request", "目标市场交易日超出请求范围")
        result.append(parsed.isoformat())
    if len(result) != len(set(result)):
        raise HiThinkStockError("invalid_request", "目标市场交易日列表包含重复日期")
    return tuple(sorted(result))


def _datetime_from_ms(value: Any) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HiThinkStockError("invalid_response", "HiThink 行情就绪时间无效")
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise HiThinkStockError("invalid_response", "HiThink 行情就绪时间超出范围") from exc


def _normalize_items(
    items: list[Any], start: date, end: date, request_id: str | None
) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise HiThinkStockError(
                "invalid_response", "HiThink 股票日线项目必须为对象", request_id=request_id
            )
        day_text = _date_from_provider_ms(item.get("date_ms"))
        day = date.fromisoformat(day_text)
        if day < start or day > end:
            raise HiThinkStockError(
                "invalid_response", "HiThink 股票日线超出请求日期范围", request_id=request_id
            )
        if day_text in seen:
            raise HiThinkStockError(
                "duplicate_data", "HiThink 股票响应包含重复交易日", request_id=request_id
            )
        seen.add(day_text)
        values = {
            "open": _decimal(item.get("open_price"), "open_price", positive=True),
            "high": _decimal(item.get("high_price"), "high_price", positive=True),
            "low": _decimal(item.get("low_price"), "low_price", positive=True),
            "close": _decimal(item.get("close_price"), "close_price", positive=True),
            "volume": _decimal(item.get("volume"), "volume"),
            "turnover": _decimal(item.get("turnover"), "turnover"),
        }
        if values["high"] < max(values["open"], values["low"], values["close"]):
            raise HiThinkStockError(
                "invalid_response", "HiThink 股票 OHLC 高低价关系无效", request_id=request_id
            )
        if values["low"] > min(values["open"], values["high"], values["close"]):
            raise HiThinkStockError(
                "invalid_response", "HiThink 股票 OHLC 高低价关系无效", request_id=request_id
            )
        bars.append({"date": day_text, **values})
    return sorted(bars, key=lambda item: item["date"])


def _content_fingerprint(
    symbol: str,
    adjustment: str,
    start: date,
    end: date,
    calendar_revision: str,
    bars: Sequence[Mapping[str, Any]],
) -> str:
    content = {
        "provider_id": "hithink",
        "upstream_source": "hithink-financial-api",
        "symbol": symbol,
        "adjustment": adjustment,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar_revision": calendar_revision,
        "bars": [
            {
                "date": item["date"],
                "open": _decimal_text(item["open"]),
                "high": _decimal_text(item["high"]),
                "low": _decimal_text(item["low"]),
                "close": _decimal_text(item["close"]),
                "volume": _decimal_text(item["volume"]),
                "turnover": _decimal_text(item["turnover"]),
            }
            for item in bars
        ],
    }
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "HITHINK_STOCK_HISTORICAL_URL",
    "HiThinkStockError",
    "HiThinkStockHistoricalAdapter",
]
