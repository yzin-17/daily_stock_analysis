"""报价 HTTP 数值投影：必要价格保持校验，量额缺失仅影响自身。"""

from __future__ import annotations

from typing import Any, Callable
from datetime import datetime, timezone

from src.services.thesis_ledger_quote_values import optional_quote_number


def quote_public_fields(quote: Any, number: Callable[[Any, str], float]) -> dict[str, Any]:
    fields = {
        "open": getattr(quote, "open_price", None),
        "high": getattr(quote, "high", None),
        "low": getattr(quote, "low", None),
        "price": getattr(quote, "price", None),
        "previousClose": getattr(quote, "pre_close", None),
    }
    return {
        **{key: number(value, key) for key, value in fields.items()},
        "volume": optional_quote_number(getattr(quote, "volume", None)),
        "amount": optional_quote_number(getattr(quote, "amount", None)),
    }


def quote_response_payload(quote, *, symbol, provider, fallback_used, source, fetched_at, number, timestamp):
    """构造报价消费 DTO；格式化沿用 HTTP 边界，来源时钟独立判定。"""
    from api.thesis_ledger_quote_time import quote_time_state

    provider_timestamp, stale, freshness = quote_time_state(
        getattr(quote, "provider_timestamp", None), getattr(quote, "is_stale", None),
        datetime.now(timezone.utc),
    )
    result = {
        "version": 3, "symbol": symbol, **quote_public_fields(quote, number),
        "stale": stale, "provider": provider,
        "marketTime": timestamp(provider_timestamp) if provider_timestamp else None,
        "fetchedAt": fetched_at, "freshness": freshness, "fallbackUsed": fallback_used,
    }
    if source:
        result["upstreamSource"] = source
    return result
