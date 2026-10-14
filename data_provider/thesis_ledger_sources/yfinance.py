"""Yahoo 既有在线日线与两项有界参考窗口，分别保留原读取语义。"""

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead, provider_symbol_for_contract
from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, ExactKey, SourceDefinition
from data_provider.yfinance_research_frame import project_normalized, project_hk_share_window
from src.services.thesis_ledger_research_instrument_contract import ResearchInstrumentFact, ResearchInstrumentPolicy

PROTOCOL = "dsa-yfinance-exact-daily-single-response-v1"


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    value = build_provider_manifest("yfinance", "Yahoo Finance", {
        "REALTIME_QUOTE": ("STOCK", "ETF", "INDEX"), "DAILY_BAR": ("STOCK", "ETF", "INDEX"),
    }, upstream_sources=(("yfinance", "Yahoo Finance"), ("yahoo-gme-share-window", "Yahoo GME按股历史窗口（有界）"),
                         ("yahoo-hk-share-window", "Yahoo 00147按股历史窗口（研究整手）")),
        markets=("CN", "HK", "US", "JP", "KR", "TW"), configuration_mode="built_in")
    for source in value["upstreamSources"]:
        if source["sourceId"] in {"yahoo-gme-share-window", "yahoo-hk-share-window"}:
            source["capabilities"] = {"DAILY_BAR": ["STOCK"]}
    return value


def factory(context):
    from data_provider.yfinance_fetcher import YfinanceFetcher
    return YfinanceFetcher()


def normalized_research_calendar_eligible(context):
    from src.services.thesis_ledger_reference_identity import reference_identity
    return reference_identity(context.symbol, context.market) is not None


def share_research_calendar_eligible(context):
    if context.market == 'HK':
        from data_provider.yahoo_hk_share_window import load_share_window, share_window_scope
    else:
        from data_provider.yahoo_share_window import load_share_window, share_window_scope
    try:
        pack, _, observed, _ = load_share_window()
        share_window_scope(context.symbol, context.start, context.end, pack)
        return observed <= datetime.now(timezone.utc)
    except (ExactDailyError, ValueError):
        return False


def research_instrument_fact(request):
    if request.market == 'HK':
        from data_provider.yahoo_hk_share_window import share_window_instrument_fact
    else:
        from data_provider.yahoo_share_window import share_window_instrument_fact
    try:
        return ResearchInstrumentFact.model_validate(share_window_instrument_fact(
            request.symbol, request.start, request.end, request.data_as_of))
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def read_frame(adapter, request, market):
    import yfinance as yf
    symbol = request.symbol
    if market == "CN":
        ticker = symbol.removesuffix(".SH") + ".SS" if symbol.endswith(".SH") else symbol
    elif market == "HK":
        ticker = symbol.removesuffix(".HK").lstrip("0").zfill(4) + ".HK"
    else:
        ticker = adapter._convert_stock_code(symbol.removesuffix(".US"))
    if request.instrument_type == "INDEX" and (market != "US" or not ticker.startswith("^")):
        raise ExactDailyError("invalid_request", "Yahoo 指数需要已登记的精确指数代码")
    raw = yf.download(tickers=ticker, start=request.start, end=(date.fromisoformat(request.end) + timedelta(days=1)).isoformat(),
                      auto_adjust=True, progress=False, threads=False, timeout=4.5, multi_level_index=False)
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(-1):
            raise ExactDailyError("invalid_response", "Yahoo 日线标的身份不匹配")
        raw = raw.xs(ticker, axis=1, level=-1)
    raw = raw.reset_index().rename(columns={"Date": "date", "Open": "open", "High": "high", "Low": "low",
                                           "Close": "close", "Volume": "volume"})
    timezone = {"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}[market]
    return normalize_exact_daily(raw, start=request.start, end=request.end, source="yfinance", timezone=timezone)


def daily(query, context):
    from src.services.thesis_ledger_current_data_route import current_data_route_key
    market = current_data_route_key(query.capability, query.instrument_type, query.symbol)["market"]
    try:
        return DailyRead(read_frame(context.adapter, query, market), 1)
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def share_window(query, context, market):
    if market == "US":
        from data_provider.yahoo_share_window import read_yahoo_share_window
        reader = read_yahoo_share_window
    else:
        from data_provider.yahoo_hk_share_window import read_yahoo_hk_share_window
        reader = read_yahoo_hk_share_window
    try:
        return DailyRead(reader(query), 1)
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def quote(query, context):
    symbol = provider_symbol_for_contract(query.symbol).removesuffix(".US")
    try:
        value = context.adapter.get_realtime_quote(symbol, strict_source=True, instrument_type=query.instrument_type)
    except ValueError:
        raise CapabilityContractError("invalid_response", "报价身份、资产或字段不匹配") from None
    if value is None:
        raise CapabilityContractError("not_covered", "Provider 未覆盖该标的")
    return value


SOURCE = SourceDefinition("yfinance", manifest, factory, tuple(
    DailyBinding(ExactKey(market, asset, "DAILY_BAR", "qfq"), "yfinance",
                 f"dsa-v3-{market.lower()}-{asset.lower()}-yfinance-qfq-exact-daily-v1", PROTOCOL, PROTOCOL,
                 None, daily, gated=True,
                 research_calendar_eligible=normalized_research_calendar_eligible if market in {'HK', 'US'} and asset == 'STOCK' else None,
                 research_frame_projection=project_normalized if market in {'HK', 'US'} and asset == 'STOCK' else None)
    for market, asset in (("CN", "STOCK"), ("CN", "ETF"), ("HK", "STOCK"), ("HK", "ETF"),
                         ("US", "STOCK"), ("US", "ETF"), ("US", "INDEX"))
) + tuple(
    DailyBinding(ExactKey(market, "STOCK", "DAILY_BAR", "none"), upstream,
                 f"dsa-v3-{market.lower()}-stock-yfinance-none-exact-daily-v1", PROTOCOL, PROTOCOL,
                 None, lambda query, context, market=market: share_window(query, context, market), gated=True,
                 research_calendar_eligible=share_research_calendar_eligible,
                 research_frame_projection=project_hk_share_window if market == 'HK' else None,
                 research_instrument_policy=ResearchInstrumentPolicy(
                     supports_symbol=lambda symbol, market=market: symbol == {'HK': '00147.HK', 'US': 'GME.US'}[market],
                     read=research_instrument_fact))
    for market, upstream in (("US", "yahoo-gme-share-window"), ("HK", "yahoo-hk-share-window"))
) + tuple(
    QuoteBinding(ExactKey(market, asset, "REALTIME_QUOTE"), "yfinance",
        f"yfinance-{market.lower()}-{asset.lower()}-exact-quote-v2" if market != "CN" else f"yfinance-{asset.lower()}-quote-v2",
        "yfinance-direct-quote-v2", quote)
    for market, asset in (("CN", "STOCK"), ("CN", "ETF"), ("HK", "STOCK"), ("HK", "ETF"),
                          ("US", "STOCK"), ("US", "ETF"), ("US", "INDEX"))
), ("data_provider.yfinance_fetcher", "YfinanceFetcher"), dependency_modules=("yfinance",), factory_scope="provider")
