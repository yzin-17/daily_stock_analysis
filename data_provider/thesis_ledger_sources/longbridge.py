"""Longbridge 八项既有日线；legacy/OAuth 工厂和 SDK 投影由来源拥有。"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead, EnvironmentCredentialPolicy, provider_symbol_for_contract
from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, QueryBinding, ExactKey, SourceDefinition
from src.services.thesis_ledger_security_lot_contract_v1 import CONTRACT as LOT_CONTRACT
from src.services.thesis_ledger_security_lot_values import LotSizeError, hk_symbol, positive_lot, reference_digest

PROTOCOL = "dsa-longbridge-exact-daily-single-response-v1"


def environment_credentials():
    try:
        from src.config import get_config
        from src.services.thesis_ledger_longbridge_environment import longbridge_environment_values
        return longbridge_environment_values(get_config())
    except Exception:
        return {}


def environment_method(values):
    return 'oauth' if 'tokenJson' in values else 'legacy'
LOT_SOURCE_URL = "https://open.longbridge.com/docs/quote/pull/static"


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    return build_provider_manifest("longbridge", "Longbridge", {
        "SECURITY_LOT_SIZE": ("STOCK",), "REALTIME_QUOTE": ("STOCK", "ETF"), "DAILY_BAR": ("STOCK", "ETF"),
    }, requires_credential=True, markets=("HK", "US"), configuration_mode="dsa_environment")


def factory(context):
    from data_provider.longbridge_fetcher import LongbridgeFetcher
    snapshot = context.snapshot
    values = snapshot.values
    if snapshot.method == "oauth":
        from src.services.provider_oauth_runtime import build_page_oauth
        oauth_token = build_page_oauth(snapshot, context.database_path, read_only=context.probe)
        return LongbridgeFetcher(oauth_token=oauth_token)
    if values:
        return LongbridgeFetcher(app_key=values.get("appKey"), app_secret=values.get("appSecret"), access_token=values.get("accessToken"))
    return LongbridgeFetcher()


def read_frame(adapter, request, market):
    from longbridge.openapi import Period, AdjustType
    ctx = adapter._get_ctx()
    if ctx is None:
        raise ExactDailyError("credential_missing", "Longbridge 凭证或连接未就绪")
    ticker = request.symbol if market == "HK" else request.symbol.removesuffix(".US") + ".US"
    candles = ctx.history_candlesticks_by_date(ticker, Period.Day,
        AdjustType.NoAdjust if request.adjustment == "none" else AdjustType.ForwardAdjust,
        date.fromisoformat(request.start), date.fromisoformat(request.end))
    timezone = "Asia/Hong_Kong" if market == "HK" else "America/New_York"
    rows = []
    for candle in candles:
        timestamp = candle.timestamp
        if isinstance(timestamp, datetime):
            trading_date = timestamp.astimezone(ZoneInfo(timezone)).date() if timestamp.tzinfo else timestamp.date()
        else:
            trading_date = datetime.fromtimestamp(int(timestamp), ZoneInfo(timezone)).date()
        rows.append({"date": trading_date.isoformat(), **{field: getattr(candle, field, None)
            for field in ("open", "high", "low", "close", "volume")}, "amount": getattr(candle, "turnover", None)})
    return normalize_exact_daily(pd.DataFrame(rows), start=request.start, end=request.end, source="longbridge", timezone=timezone)


def daily(query, context, market):
    try:
        return DailyRead(read_frame(context.adapter, query, market), 1)
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def lot_size(query, context):
    canonical = query.symbol
    ticker = str(int(canonical[:-3])) + ".HK"
    info = context.adapter._get_static_info(ticker)
    if info is None:
        raise CapabilityContractError("not_covered", "长桥未返回该证券静态资料，请检查授权或备用源")
    try:
        if hk_symbol(getattr(info, "symbol", None)) != canonical:
            raise LotSizeError("invalid_response", "长桥静态资料标的不匹配")
        board = getattr(info, "board", None)
        if str(getattr(board, "name", board)).split(".")[-1] != "HKEquity":
            raise LotSizeError("invalid_response", "长桥静态资料不是港股股票")
        shares = positive_lot(getattr(info, "lot_size", None))
    except LotSizeError as error:
        raise CapabilityContractError(error.code, str(error)) from None
    cached = getattr(context.adapter, "_static_cache", {}).get(ticker)
    observed = datetime.fromtimestamp(cached[1], timezone.utc) if cached else datetime.now(timezone.utc)
    stamp = observed.isoformat()
    return {"symbol": canonical, "sharesPerLot": shares, "historicalChanges": "unverified",
        "source": {"provider": "longbridge", "sourceUrl": LOT_SOURCE_URL, "observedAt": stamp,
                   "sha256": reference_digest(canonical, shares, "longbridge", stamp)}}


def quote(query, context):
    try:
        value = context.adapter.get_realtime_quote(provider_symbol_for_contract(query.symbol),
            strict_source=True, instrument_type=query.instrument_type)
    except ValueError:
        raise CapabilityContractError("invalid_response", "报价身份、资产或字段不匹配") from None
    if value is None:
        raise CapabilityContractError("not_covered", "Provider 未覆盖该标的")
    return value


SOURCE = SourceDefinition("longbridge", manifest, factory, tuple(
    DailyBinding(ExactKey(market, asset, "DAILY_BAR", adjustment), "longbridge",
        f"dsa-v3-{market.lower()}-{asset.lower()}-longbridge-{adjustment}-exact-daily-v1", PROTOCOL, PROTOCOL,
        None, lambda query, context, market=market: daily(query, context, market), gated=True)
    for market, asset, adjustment in (("HK", "STOCK", "none"), ("HK", "STOCK", "qfq"),
        ("HK", "ETF", "none"), ("HK", "ETF", "qfq"), ("US", "STOCK", "none"),
        ("US", "STOCK", "qfq"), ("US", "ETF", "none"), ("US", "ETF", "qfq"))
) + (QueryBinding(LOT_CONTRACT, ExactKey("HK", "STOCK", "SECURITY_LOT_SIZE"), "longbridge",
    "longbridge-security-lot-size-v1", "longbridge-static-info-v1", lot_size, admission_required=False),) + tuple(
    QuoteBinding(ExactKey(market, asset, "REALTIME_QUOTE"), "longbridge",
        f"longbridge-{market.lower()}-{asset.lower()}-exact-quote-v2", "longbridge-direct-quote-v2", quote)
    for market, asset in (("HK", "STOCK"), ("HK", "ETF"), ("US", "STOCK"), ("US", "ETF"))
), ("data_provider.longbridge_fetcher", "LongbridgeFetcher"),
    credentials=(("legacy", ("appKey", "appSecret", "accessToken")), ("oauth", ("clientId", "tokenJson"))),
    dependency_modules=("longbridge",), factory_scope="provider", allow_empty_environment_credentials=True,
    environment_credential_policy=EnvironmentCredentialPolicy(environment_credentials, select_method=environment_method))
