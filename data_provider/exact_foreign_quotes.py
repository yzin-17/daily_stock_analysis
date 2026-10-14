"""境外报价的精确 SDK 读取；不切换来源或生成缺失量额。"""

from data_provider.realtime_types import UnifiedRealtimeQuote, RealtimeSource
from data_provider.exact_quote_fields import finish_exact_quote


def yahoo_quote(adapter, code, instrument_type):
    import yfinance as yf
    symbol = adapter._convert_stock_code(code)
    info = yf.Ticker(symbol).get_info()
    if not isinstance(info, dict) or info.get("symbol") != symbol:
        raise ValueError("quote_identity_mismatch")
    types = {"STOCK": "EQUITY", "ETF": "ETF", "INDEX": "INDEX"}
    if info.get("quoteType") and types.get(instrument_type) and info["quoteType"] != types[instrument_type]:
        raise ValueError("quote_asset_mismatch")
    quote = UnifiedRealtimeQuote(
        code=symbol, name=info.get("shortName", ""), source=RealtimeSource.FALLBACK,
        price=info.get("regularMarketPrice"), pre_close=info.get("regularMarketPreviousClose"),
        open_price=info.get("regularMarketOpen"), high=info.get("regularMarketDayHigh"),
        low=info.get("regularMarketDayLow"), volume=info.get("regularMarketVolume"), amount=None,
    )
    return finish_exact_quote(quote, info.get("regularMarketTime"))


def longbridge_quote(adapter, code):
    from data_provider.longbridge_fetcher import _to_longbridge_symbol
    symbol = _to_longbridge_symbol(code)
    context = adapter._get_ctx()
    if symbol is None or context is None:
        return None
    values = context.quote([symbol])
    if not values:
        return None
    if len(values) != 1 or getattr(values[0], "symbol", None) != symbol:
        raise ValueError("quote_identity_mismatch")
    value = values[0]
    quote = UnifiedRealtimeQuote(
        code=symbol, name="", source=RealtimeSource.LONGBRIDGE,
        price=getattr(value, "last_done", None), pre_close=getattr(value, "prev_close", None),
        open_price=getattr(value, "open", None), high=getattr(value, "high", None),
        low=getattr(value, "low", None), volume=getattr(value, "volume", None),
        amount=getattr(value, "turnover", None),
    )
    return finish_exact_quote(quote, getattr(value, "timestamp", None))
