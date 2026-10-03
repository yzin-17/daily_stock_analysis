"""ThesisLedger 单标报价的精确适配器分发。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Mapping

from .thesis_ledger_hithink_quote import (
    HITHINK_ETF_SNAPSHOT_SOURCE,
    HITHINK_STOCK_SNAPSHOT_SOURCE,
    HiThinkQuoteError,
)


_ETF_SINGLE_SYMBOL_GENERIC_PROVIDERS = frozenset({"yfinance", "longbridge"})


def realtime_quote(
    adapter: Any,
    provider_id: str,
    symbol: str,
    instrument_type: str | None,
    upstream_source: str | None,
    error_type: type[Exception],
) -> Any:
    """只调目标单标来源，并保留来源时间与抓取时间。"""
    if provider_id == "hithink":
        expected = {
            "STOCK": HITHINK_STOCK_SNAPSHOT_SOURCE,
            "ETF": HITHINK_ETF_SNAPSHOT_SOURCE,
        }.get(instrument_type)
        if expected is None or upstream_source != expected:
            raise error_type("unsupported_source", "HiThink 报价来源与资产类型不匹配")
        try:
            value = adapter.fetch_quote(symbol, instrument_type)
        except HiThinkQuoteError as exc:
            raise error_type(exc.code, str(exc), retryable=exc.retryable) from exc
    elif instrument_type == "ETF":
        if provider_id == "akshare":
            raise error_type("unsupported", "AKShare 当前没有可执行的 ETF 单标的行情适配器")
        single_symbol_method = getattr(adapter, "get_realtime_quote_single_symbol", None)
        if callable(single_symbol_method):
            value = single_symbol_method(symbol)
        elif provider_id == "efinance":
            raise error_type("unsupported", "efinance 当前没有可执行的 ETF 单标的行情适配器")
        elif provider_id not in _ETF_SINGLE_SYMBOL_GENERIC_PROVIDERS:
            raise error_type("unsupported", f"{provider_id} 当前没有已确认的 ETF 单标的行情适配器")
        else:
            value = adapter.get_realtime_quote(symbol)
    elif upstream_source == provider_id:
        value = adapter.get_realtime_quote(symbol)
    elif provider_id == "akshare":
        value = adapter.get_realtime_quote(
            symbol, source="em" if upstream_source == "eastmoney" else upstream_source or "sina"
        )
    elif provider_id in {"finnhub", "alphavantage"}:
        value = adapter.get_realtime_quote(symbol, strict=True)
    else:
        value = adapter.get_realtime_quote(symbol)
    if isinstance(value, Mapping):
        return SimpleNamespace(
            price=value.get("price"),
            open_price=value.get("open_price", value.get("open")),
            high=value.get("high"),
            low=value.get("low"),
            pre_close=value.get("pre_close", value.get("previousClose")),
            volume=value.get("volume"),
            amount=value.get("amount"),
            change_amount=value.get("change_amount"),
            change_pct=value.get("change_pct"),
            fetched_at=value.get("fetched_at"),
            provider_timestamp=value.get("provider_timestamp"),
            is_stale=value.get("is_stale", False),
            source=value.get("source"),
            units=value.get("units"),
        )
    return value
