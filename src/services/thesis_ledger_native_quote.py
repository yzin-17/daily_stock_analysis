"""原精确股票报价兼容入口；实际读取由标准来源绑定决定。"""

from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_capability_contracts import CapabilityContractError, QuoteQuery, ReadContext
from .thesis_ledger_current_data_route import current_data_route_key


def native_quote(provider, adapter, symbol, error_type):
    key = current_data_route_key("REALTIME_QUOTE", "STOCK", symbol)
    target = {"providerId": provider, "upstreamSource": provider}
    if CAPABILITY_SOURCES.resolve(key, target) is None:
        raise error_type("unsupported_source", "未登记精确报价接口")
    try:
        return CAPABILITY_SOURCES.quote(QuoteQuery(symbol=symbol, instrument_type="STOCK"), key, target, ReadContext(adapter))
    except CapabilityContractError as error:
        raise error_type(error.code, str(error)) from None
