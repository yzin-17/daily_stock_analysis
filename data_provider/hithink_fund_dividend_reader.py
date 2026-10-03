"""HiThink 单基金分红端点的有界只读传输；不证明历史事件完整。"""

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

import requests


DIVIDEND_URL = "https://fuyao.aicubes.cn/api/fund/corporate-actions/dividends"
_SYMBOL = re.compile(r"^[0-9]{6}\.(?:SH|SZ|OF)$")
_BUSINESS_ERRORS = {
    1001: "invalid_request", 1002: "invalid_request", 1003: "range_exceeded",
    2001: "authentication_failed", 2003: "permission_denied",
    3001: "symbol_not_found", 3002: "data_not_ready", 3004: "unsupported_capability",
    4001: "rate_limited", 5001: "upstream_failure", 5002: "upstream_failure",
    5003: "upstream_failure",
}


class HiThinkFundDividendReadError(Exception):
    """不含凭据、URL、响应正文的稳定错误。"""

    def __init__(self, code: str) -> None:
        super().__init__(f"HiThink 基金分红读取失败：{code}")
        self.code = code


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_body(response: Any, max_bytes: int) -> bytes:
    chunks = []
    size = 0
    try:
        for chunk in response.iter_content(chunk_size=65536):
            if not isinstance(chunk, bytes):
                raise HiThinkFundDividendReadError("invalid_response")
            size += len(chunk)
            if size > max_bytes:
                raise HiThinkFundDividendReadError("response_too_large")
            chunks.append(chunk)
    except HiThinkFundDividendReadError:
        raise
    except Exception:
        raise HiThinkFundDividendReadError("transport_error") from None
    return b"".join(chunks)


def fetch_hithink_fund_dividends(
    symbol: str,
    *,
    fund_type: str,
    api_key: str,
    http_get: Callable[..., Any] = requests.get,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    timeout_seconds: float = 15.0,
    max_bytes: int = 1024 * 1024,
    max_items: int = 2000,
) -> dict[str, Any]:
    """读取一次完整端点响应；调用方负责身份、币种和历史覆盖审核。"""
    if (not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None
            or fund_type not in {"exchange", "otc"}
            or (fund_type == "exchange" and symbol.endswith(".OF"))
            or (fund_type == "otc" and not symbol.endswith(".OF"))):
        raise HiThinkFundDividendReadError("invalid_request")
    if not isinstance(api_key, str) or not api_key.strip():
        raise HiThinkFundDividendReadError("missing_credentials")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 60:
        raise HiThinkFundDividendReadError("invalid_request")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= 4 * 1024 * 1024:
        raise HiThinkFundDividendReadError("invalid_request")
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 5000:
        raise HiThinkFundDividendReadError("invalid_request")
    try:
        response = http_get(
            DIVIDEND_URL,
            params={"fund_type": fund_type, "thscode": symbol},
            headers={"X-api-key": api_key.strip(), "Accept": "application/json"},
            timeout=(min(5.0, float(timeout_seconds)), float(timeout_seconds)),
            allow_redirects=False,
            stream=True,
        )
    except Exception:
        raise HiThinkFundDividendReadError("transport_error") from None
    try:
        status = getattr(response, "status_code", None)
        if isinstance(status, bool) or not isinstance(status, int):
            raise HiThinkFundDividendReadError("invalid_response")
        if status == 401:
            raise HiThinkFundDividendReadError("authentication_failed")
        if status == 403:
            raise HiThinkFundDividendReadError("permission_denied")
        if status == 429:
            raise HiThinkFundDividendReadError("rate_limited")
        if status >= 500:
            raise HiThinkFundDividendReadError("upstream_failure")
        if status < 200 or status >= 300:
            raise HiThinkFundDividendReadError("http_error")
        raw = _read_body(response, max_bytes)
    finally:
        response.close()
    fingerprint = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, TypeError):
        raise HiThinkFundDividendReadError("invalid_response") from None
    if not isinstance(payload, dict) or isinstance(payload.get("code"), bool) or not isinstance(payload.get("code"), int):
        raise HiThinkFundDividendReadError("invalid_response")
    if payload["code"] != 0:
        raise HiThinkFundDividendReadError(_BUSINESS_ERRORS.get(payload["code"], "provider_error"))
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("item"), list):
        raise HiThinkFundDividendReadError("invalid_response")
    items = data["item"]
    if len(items) > max_items or any(not isinstance(item, dict) for item in items):
        raise HiThinkFundDividendReadError("invalid_response")
    count = data.get("dividend_count")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count != len(items)):
        raise HiThinkFundDividendReadError("count_mismatch")
    timestamp = data.get("timestamp")
    if timestamp is not None and (isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0):
        raise HiThinkFundDividendReadError("invalid_response")
    observed_at = clock()
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise HiThinkFundDividendReadError("invalid_clock")
    return {
        "symbol": symbol, "fundType": fund_type, "items": items, "responseSha256": fingerprint,
        "observedAt": observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sourceTimestampMs": timestamp, "transportCountVerified": count is not None,
        "historyComplete": False,
    }
