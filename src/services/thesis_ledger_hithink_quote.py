"""HiThink 股票/场内基金快照的单标只读适配器。"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import requests


HITHINK_STOCK_SNAPSHOT_SOURCE = "a-share-prices-snapshot"
HITHINK_ETF_SNAPSHOT_SOURCE = "fund-market-snapshot"
HITHINK_STOCK_SNAPSHOT_URL = "https://fuyao.aicubes.cn/api/a-share/prices/snapshot"
HITHINK_ETF_SNAPSHOT_URL = "https://fuyao.aicubes.cn/api/fund/market/snapshot"

_STOCK_SYMBOL = re.compile(r"^[0-9]{6}\.(?:SH|SZ|BJ)$")
_ETF_SYMBOL = re.compile(r"^[0-9]{6}\.(?:SH|SZ)$")
_ERRORS: dict[int, tuple[str, str, bool]] = {
    1001: ("invalid_request", "HiThink 快照请求缺少必填参数", False),
    1002: ("invalid_request", "HiThink 快照请求参数格式无效", False),
    1003: ("range_exceeded", "HiThink 快照请求范围超限", False),
    1004: ("invalid_request", "HiThink 快照请求参数冲突", False),
    2001: ("authentication_failed", "HiThink 凭据未通过认证", False),
    2003: ("permission_denied", "HiThink 账号无权访问该快照能力", False),
    3001: ("symbol_not_found", "HiThink 未识别该标的代码", False),
    3002: ("data_not_ready", "HiThink 快照数据尚未准备", True),
    3004: ("unsupported_capability", "HiThink 不支持该标的快照能力", False),
    4001: ("rate_limited", "HiThink 快照请求触发限流", True),
    5001: ("upstream_failure", "HiThink 快照服务暂时不可用", True),
    5002: ("upstream_failure", "HiThink 快照上游暂时不可用", True),
    5003: ("upstream_failure", "HiThink 快照服务暂时不可用", True),
}


class HiThinkQuoteError(Exception):
    """稳定、脱敏的 HiThink 快照错误。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


HttpGet = Callable[..., Any]
Clock = Callable[[], datetime]


def _number(value: Any, field: str, *, positive: bool = False, signed: bool = False) -> float:
    if isinstance(value, bool):
        raise HiThinkQuoteError("invalid_response", f"HiThink 快照字段 {field} 非法")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HiThinkQuoteError("invalid_response", f"HiThink 快照字段 {field} 非法") from exc
    if not math.isfinite(number):
        raise HiThinkQuoteError("invalid_response", f"HiThink 快照字段 {field} 非法")
    if not signed and (number < 0 or (positive and number <= 0)):
        raise HiThinkQuoteError("invalid_response", f"HiThink 快照字段 {field} 非法")
    return number


def _provider_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HiThinkQuoteError("invalid_response", "HiThink 快照时间无效")
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError) as exc:
        raise HiThinkQuoteError("invalid_response", "HiThink 快照时间无效") from exc


def _business_code(payload: Mapping[str, Any]) -> int | None:
    value = payload.get("code")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


class HiThinkSnapshotAdapter:
    """只执行精确单标快照，不做 Provider fallback。"""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 4.5,
        http_get: HttpGet = requests.get,
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise HiThinkQuoteError("not_configured", "HiThink API 凭据未配置")
        self._api_key = api_key
        self._timeout_seconds = float(timeout_seconds)
        self._http_get = http_get
        self._clock = clock


    def fetch_quote(self, symbol: str, instrument_type: str) -> dict[str, Any]:
        normalized_type = str(instrument_type or "").strip().upper()
        if normalized_type == "STOCK":
            if not isinstance(symbol, str) or _STOCK_SYMBOL.fullmatch(symbol) is None:
                raise HiThinkQuoteError("invalid_request", "HiThink 股票快照代码必须是完整 thscode")
            source = HITHINK_STOCK_SNAPSHOT_SOURCE
            url = HITHINK_STOCK_SNAPSHOT_URL
            params = {"thscodes": symbol}
        elif normalized_type == "ETF":
            if not isinstance(symbol, str) or _ETF_SYMBOL.fullmatch(symbol) is None:
                raise HiThinkQuoteError("invalid_request", "HiThink ETF 快照代码必须是完整 thscode")
            source = HITHINK_ETF_SNAPSHOT_SOURCE
            url = HITHINK_ETF_SNAPSHOT_URL
            params = {"thscode": symbol}
        else:
            raise HiThinkQuoteError("unsupported_capability", "HiThink 快照只接入 STOCK/ETF")

        try:
            response = self._http_get(
                url,
                params=params,
                headers={"X-api-key": self._api_key, "Accept": "application/json"},
                timeout=self._timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise HiThinkQuoteError("network_failure", "HiThink 快照请求网络失败", retryable=True) from None
        except Exception:
            raise HiThinkQuoteError("network_failure", "HiThink 快照请求网络失败", retryable=True) from None

        status = getattr(response, "status_code", None)
        if isinstance(status, bool) or not isinstance(status, int):
            raise HiThinkQuoteError("invalid_response", "HiThink HTTP 响应状态无效")
        try:
            payload = response.json()
        except Exception:
            if status in {401, 403}:
                raise HiThinkQuoteError(
                    "authentication_failed" if status == 401 else "permission_denied",
                    "HiThink 快照请求未获授权",
                ) from None
            if status == 429:
                raise HiThinkQuoteError("rate_limited", "HiThink 快照请求触发限流", retryable=True) from None
            if status >= 500:
                raise HiThinkQuoteError("upstream_failure", "HiThink 快照服务暂时不可用", retryable=True) from None
            raise HiThinkQuoteError("invalid_response", "HiThink 快照响应不是有效 JSON") from None

        if status >= 400:
            if status == 401:
                raise HiThinkQuoteError("authentication_failed", "HiThink 快照请求未获授权")
            if status == 403:
                raise HiThinkQuoteError("permission_denied", "HiThink 快照请求未获授权")
            if status == 429:
                raise HiThinkQuoteError("rate_limited", "HiThink 快照请求触发限流", retryable=True)
            raise HiThinkQuoteError("upstream_failure", "HiThink 快照请求未成功", retryable=status >= 500)
        if not isinstance(payload, Mapping):
            raise HiThinkQuoteError("invalid_response", "HiThink 快照响应格式无效")
        code = _business_code(payload)
        if code is None:
            raise HiThinkQuoteError("invalid_response", "HiThink 快照业务状态无效")
        if code != 0:
            error = _ERRORS.get(code)
            if error is None:
                raise HiThinkQuoteError("provider_error", "HiThink 快照返回未知业务状态")
            raise HiThinkQuoteError(error[0], error[1], retryable=error[2])

        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise HiThinkQuoteError("invalid_response", "HiThink 快照响应缺少 data")
        items = data.get("item")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], Mapping):
            raise HiThinkQuoteError("invalid_response", "HiThink 单标快照必须恰好返回一条记录")
        item = items[0]
        if item.get("thscode") != symbol:
            raise HiThinkQuoteError("invalid_response", "HiThink 快照标的身份不匹配")


        price = _number(item.get("last_price"), "last_price", positive=True)
        result = {
            "price": price,
            "open_price": _number(item.get("open_price"), "open_price"),
            "high": _number(item.get("high_price"), "high_price"),
            "low": _number(item.get("low_price"), "low_price"),
            "pre_close": _number(item.get("prev_price"), "prev_price"),
            "volume": _number(item.get("volume"), "volume"),
            "amount": _number(item.get("turnover"), "turnover"),
            "change_amount": _number(item.get("price_change"), "price_change", signed=True),
            "change_pct": _number(
                item.get("price_change_ratio_pct"),
                "price_change_ratio_pct",
                signed=True,
            ),
            "provider_timestamp": _provider_time(data.get("timestamp")),
            "fetched_at": self._observed_at(),
            "source": f"hithink/{source}",
            "instrument_type": normalized_type,
            "historical_visibility_verified": False,
            "units": (
                {
                    "price_currency": "CNY",
                    "volume": "share",
                    "turnover_currency": "CNY",
                }
                if normalized_type == "STOCK"
                else {
                    "price_currency": "CNY",
                    "volume": "unknown",
                    "turnover_currency": "unknown",
                }
            ),
        }
        if result["high"] < max(result["open_price"], result["low"], price):
            raise HiThinkQuoteError("invalid_response", "HiThink 快照 OHLC 非法")
        if result["low"] > min(result["open_price"], result["high"], price):
            raise HiThinkQuoteError("invalid_response", "HiThink 快照 OHLC 非法")
        return result

    def _observed_at(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise HiThinkQuoteError("invalid_response", "本地观测时钟必须含时区")
        return value.astimezone(timezone.utc).isoformat()
