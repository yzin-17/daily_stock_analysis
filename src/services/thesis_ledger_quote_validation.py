"""基础报价标准结果校验；不依赖供应商或运行器。"""

import math
from datetime import datetime
from pydantic import ValidationError

from .thesis_ledger_capability_contracts import CapabilityContractError
from .thesis_ledger_quote_result_contract import QuoteResult, UnverifiedQuoteTime
from .thesis_ledger_quote_values import optional_quote_number


def number(raw, name, *, positive=False):
    if isinstance(raw, bool):
        raise CapabilityContractError("invalid_response", f"Provider 响应字段 {name} 非法")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise CapabilityContractError("invalid_response", f"Provider 响应字段 {name} 非法") from None
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise CapabilityContractError("invalid_response", f"Provider 响应字段 {name} 非法")
    return value


def validate_quote(value: object) -> QuoteResult:
    if isinstance(value, QuoteResult):
        try:
            return QuoteResult.model_validate(value.model_dump())
        except (ValidationError, ValueError):
            raise CapabilityContractError('invalid_response', '标准报价结果合同无效') from None
    price = number(getattr(value, "price", None), "price", positive=True)
    previous = getattr(value, "pre_close", None)
    if previous is None:
        change_amount = getattr(value, "change_amount", None)
        if change_amount is not None:
            try:
                derived = price - float(change_amount)
            except (TypeError, ValueError):
                derived = None
            if derived is not None and math.isfinite(derived) and derived >= 0:
                previous = derived
        if previous is None:
            change_pct = getattr(value, "change_pct", None)
            if change_pct is not None:
                try:
                    divisor = 1.0 + float(change_pct) / 100.0
                    derived = price / divisor if divisor > 0 else None
                except (TypeError, ValueError, ZeroDivisionError):
                    derived = None
                if derived is not None and math.isfinite(derived) and derived >= 0:
                    previous = derived
    if previous is None:
        raise CapabilityContractError("invalid_response", "Provider 响应字段 previousClose 缺失")
    fields = {"open": getattr(value, "open_price", None), "high": getattr(value, "high", None),
              "low": getattr(value, "low", None), "price": price, "previousClose": previous}
    normalized = {name: number(raw, name, positive=name == "price") for name, raw in fields.items()}
    if (normalized["high"] < max(normalized["open"], normalized["low"], normalized["price"])
            or normalized["low"] > min(normalized["open"], normalized["high"], normalized["price"])):
        raise CapabilityContractError("invalid_response", "Provider Quote OHLC 非法")
    raw_source = getattr(value, 'source', None)
    source = getattr(raw_source, 'value', raw_source)
    provider_time, fetched = getattr(value, 'provider_timestamp', None), getattr(value, 'fetched_at', None)
    unverified_time = getattr(value, 'timestamp', None)
    stale = getattr(value, 'is_stale', None)
    try:
        return QuoteResult(price=normalized['price'], open_price=normalized['open'], high=normalized['high'],
            low=normalized['low'], pre_close=normalized['previousClose'],
            volume=optional_quote_number(getattr(value, 'volume', None)), amount=optional_quote_number(getattr(value, 'amount', None)),
            change_amount=optional_quote_number(getattr(value, 'change_amount', None), signed=True),
            change_pct=optional_quote_number(getattr(value, 'change_pct', None), signed=True),
            code=getattr(value, 'code', None) if isinstance(getattr(value, 'code', None), str) else None,
            name=getattr(value, 'name', None) if isinstance(getattr(value, 'name', None), str) else None,
            source=source if isinstance(source, str) else None,
            provider_timestamp=provider_time if isinstance(provider_time, str) else None,
            source_time_observation=UnverifiedQuoteTime(text=unverified_time) if isinstance(unverified_time, str) else None,
            fetched_at=fetched,
            is_stale=stale if isinstance(stale, bool) else None)
    except (ValidationError, ValueError):
        raise CapabilityContractError('invalid_response', '标准报价结果合同无效') from None
