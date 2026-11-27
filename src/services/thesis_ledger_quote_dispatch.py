"""ThesisLedger 单标报价的精确适配器分发。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_capability_contracts import CapabilityContractError, QuoteQuery, QuoteValue, ReadContext
from .thesis_ledger_quote_result_contract import QuoteResult



def realtime_quote(
    adapter: Any,
    provider_id: str,
    symbol: str,
    instrument_type: str | None,
    upstream_source: str | None,
    error_type: type[Exception],
    context: ReadContext | None = None,
) -> QuoteResult:
    """只调目标单标来源，并保留来源时间与抓取时间。"""
    from .thesis_ledger_current_data_route import current_data_route_key
    if instrument_type is None and upstream_source is None:
        stock_key = current_data_route_key("REALTIME_QUOTE", "STOCK", symbol)
        candidates = [candidate_target for candidate_key, candidate_target, _ in CAPABILITY_SOURCES.routes("REALTIME_QUOTE")
                      if candidate_key == stock_key and candidate_target["providerId"] == provider_id]
        if len(candidates) == 1:
            instrument_type, upstream_source = "STOCK", candidates[0]["upstreamSource"]
        elif candidates:
            defaults = [target for target in candidates if getattr(CAPABILITY_SOURCES.resolve(stock_key, target), "legacy_probe_default", False)]
            if len(defaults) != 1:
                raise error_type("unsupported_source", "报价检查必须指定唯一精确来源")
            instrument_type, upstream_source = "STOCK", defaults[0]["upstreamSource"]
    key = current_data_route_key("REALTIME_QUOTE", instrument_type, symbol)
    target = {"providerId": provider_id, "upstreamSource": upstream_source}
    if CAPABILITY_SOURCES.resolve(key, target) is not None:
        try:
            return CAPABILITY_SOURCES.quote(QuoteQuery(symbol=symbol, instrument_type=instrument_type),
                                            key, target, context or ReadContext(adapter))
        except CapabilityContractError as error:
            if error.retryable:
                raise error_type(error.code, str(error), retryable=True) from None
            raise error_type(error.code, str(error)) from None
    raise error_type("unsupported_source", "报价来源未匹配该精确能力绑定")


def quote_request_operation(runtime, request, instrument_type, error_type):
    def operation(provider_id, adapter, upstream_source=None):
        from .thesis_ledger_current_data_route import current_data_route_key
        from .thesis_ledger_current_rule_context import current_rule_context
        context = None
        key = current_data_route_key('REALTIME_QUOTE', instrument_type, request.symbol)
        if provider_id == 'futu' and key['market'] == 'US':
            context = current_rule_context(runtime, provider_id, adapter)
        if (request.parameters or {}).get('backgroundQuote') is not None:
            context = replace(context or ReadContext(adapter), background_quote=True)
        value = realtime_quote(adapter, provider_id, request.symbol, instrument_type, upstream_source, error_type, context)
        if context and context.publish_guard and not context.publish_guard():
            raise error_type('not_admitted', '规则读取期间来源控制已变化')
        if value is None:
            raise error_type('not_covered', 'Provider 未覆盖该标的')
        return runtime._validate_quote(value)
    return operation
