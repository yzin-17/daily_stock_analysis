"""富途精确日线与分钟绑定，复用隔离查询及完整 SDK 分页。"""

from functools import partial

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead
from src.services.thesis_ledger_quote_result_contract import QuoteResult

from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, QueryBinding, ExactKey, SourceDefinition
from src.services.thesis_ledger_security_static_contract_v1 import CONTRACT as STATIC_CONTRACT
from src.services.thesis_ledger_security_lot_contract_v1 import CONTRACT as LOT_CONTRACT
from src.services.thesis_ledger_minute_contract_v1 import CONTRACT as MINUTE_CONTRACT, minute_market
from src.services.thesis_ledger_security_static_contract_v1 import security_market
from src.services.thesis_ledger_research_instrument_contract import ResearchInstrumentPolicy
from data_provider.futu_research_identity import calendar_eligible, read_instrument, supports_symbol
from src.services.thesis_ledger_dependency_facts import cn_research_calendar_eligible

PROTOCOL = "dsa-futu-exact-daily-sdk-pagination-v1"
LOT_SOURCE_URL = "https://openapi.futunn.com/futu-api-doc/quote/get-static-info.html"


def research_calendar_eligible(context):
    if context.market == 'CN':
        return cn_research_calendar_eligible(context)
    return calendar_eligible(context)


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    return build_provider_manifest("futu", "富途 OpenAPI", {
        "SECURITY_LOT_SIZE": ("STOCK",), "REALTIME_QUOTE": ("STOCK", "ETF"),
        "DAILY_BAR": ("STOCK", "ETF"), "MINUTE_BAR": ("STOCK", "ETF"), "SECURITY_STATIC_INFO": ("STOCK", "ETF"),
    }, requires_credential=True, markets=("CN", "HK", "US"))


def factory(context):
    from data_provider.futu_market import FutuMarketAdapter
    return FutuMarketAdapter(**context.snapshot.values)


def daily(query, context):
    from data_provider.futu_market import FutuMarketError
    try:
        # 一次完整隔离响应；原 worker 不导出 SDK 内部页数，不能从行数推算。
        return DailyRead(context.adapter.get_exact_daily(query), 1)
    except FutuMarketError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def quote(query, context):
    from data_provider.futu_market import FutuMarketError
    try:
        value = context.adapter.get_realtime_quote(query.symbol, strict_source=True, instrument_type=query.instrument_type)
    except FutuMarketError as error:
        raise CapabilityContractError(error.code, str(error)) from None
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from src.services.us_live_execution_rules import current_us_cash_rules
    rules = None
    if value.get('execution_facts') and value.get('fetched_at'):
        observed = datetime.fromisoformat(value['fetched_at'])
        day = observed.astimezone(ZoneInfo('America/New_York')).date().isoformat()
        rules = current_us_cash_rules(day, observed_at=value['fetched_at'], context=context)
    return QuoteResult(
        price=value.get("price"), open_price=value.get("open_price"), high=value.get("high"),
        low=value.get("low"), pre_close=value.get("pre_close"), volume=value.get("volume"), amount=value.get("amount"),
        change_amount=value.get("change_amount"), change_pct=value.get("change_pct"),
        fetched_at=value.get("fetched_at"), provider_timestamp=value.get("provider_timestamp"),
        is_stale=value.get("is_stale", False), source=value.get("source"), units=value.get("units"),
        execution_facts=value.get("execution_facts"), execution_rules=rules)


def minute(query, context):
    from data_provider.futu_market import FutuMarketError
    try:
        value = context.adapter.get_exact_minute(query)
    except FutuMarketError as error:
        raise CapabilityContractError(error.code, str(error), retryable=error.code in {"timeout", "upstream_failure"}) from None
    try:
        attrs = value["attrs"]
        return {"symbol": query.symbol, "market": minute_market(query.symbol), "assetType": query.instrument_type,
                **({"fieldUnits": {"volume": "share", "amount": "unknown"}}
                   if minute_market(query.symbol) == "CN" and query.instrument_type == "STOCK" and query.timeframe == "1m" else {}),
                **({"pagination": attrs["pagination"]} if "pagination" in attrs else {}),
                "source": attrs["upstream_source"], "rows": value["rows"], "observedAt": attrs["observedAt"],
                "timestampConvention": attrs.get("timestampConvention", "interval-start"),
                "timeframe": query.timeframe, "timezone": attrs["timezone"], "session": attrs["session"], "responseSha256": attrs["responseSha256"]}
    except (KeyError, TypeError):
        raise CapabilityContractError("invalid_response", "富途分钟响应缺少标准字段") from None


def static_info(query, context):
    from data_provider.futu_market import FutuMarketError
    try:
        return context.adapter.get_security_static_info(query.symbol, query.instrument_type)
    except FutuMarketError as error:
        raise CapabilityContractError(error.code, str(error), retryable=error.code in {"timeout", "upstream_failure"}) from None


def lot_size(query, context):
    from src.services.thesis_ledger_security_lot_values import LotSizeError
    try:
        return context.adapter.get_security_lot_size(query.symbol)
    except LotSizeError as error:
        raise CapabilityContractError(error.code, str(error), retryable=error.code in {"timeout", "upstream_failure"}) from None


def minute_revisions(market, asset, period):
    if market != "CN":
        return "futu-exact-minute-v1", "futu-complete-minute-sdk-pagination-v1"
    if asset == "STOCK" and period == "1m":
        return "futu-exact-cn-stock-minute-v4", "futu-observed-cn-stock-minute-sdk-v4"
    revision = "v2" if period == "1m" else "v3"
    return f"futu-exact-cn-minute-end-{revision}", f"futu-complete-cn-minute-end-sdk-pagination-{revision}"


SOURCE = SourceDefinition("futu", manifest, factory, tuple(
    DailyBinding(ExactKey(market, asset, "DAILY_BAR", adjustment), "futu",
        f"dsa-v3-{market.lower()}-{asset.lower()}-futu-{adjustment}-exact-daily-v1", PROTOCOL, PROTOCOL,
        None, daily, gated=True,
        research_calendar_eligible=research_calendar_eligible if
        ((market == 'CN' and adjustment in {'none', 'qfq', 'hfq'})
         or (asset == 'STOCK' and adjustment in {'none', 'qfq', 'hfq'})) else None,
        research_instrument_policy=ResearchInstrumentPolicy(partial(supports_symbol, market=market), read_instrument)
        if market in {'HK', 'US'} and asset == 'STOCK' and adjustment in {'none', 'qfq', 'hfq'} else None)
    for market, asset, adjustment in (
        ("HK", "STOCK", "none"), ("HK", "STOCK", "qfq"), ("HK", "STOCK", "hfq"),
        ("HK", "ETF", "none"), ("HK", "ETF", "qfq"), ("HK", "ETF", "hfq"),
        ("US", "STOCK", "none"), ("US", "STOCK", "qfq"), ("US", "STOCK", "hfq"),
        ("US", "ETF", "none"), ("US", "ETF", "qfq"), ("US", "ETF", "hfq"),
        ("CN", "STOCK", "none"), ("CN", "STOCK", "qfq"), ("CN", "STOCK", "hfq"),
        ("CN", "ETF", "none"), ("CN", "ETF", "qfq"), ("CN", "ETF", "hfq"),
    )
) + tuple(
    QuoteBinding(ExactKey(market, asset, "REALTIME_QUOTE"), "futu",
        f"futu-{market.lower()}-{asset.lower()}-exact-quote-v2" if market == 'US' else f"futu-{market.lower()}-{asset.lower()}-exact-quote-v1",
        'futu-current-us-execution-v2' if market == 'US' else "futu-direct-quote-v1", quote)
    for market, asset in (("HK", "STOCK"), ("HK", "ETF"), ("US", "STOCK"), ("US", "ETF"))
) + tuple(
    QueryBinding(STATIC_CONTRACT, ExactKey(market, asset, "SECURITY_STATIC_INFO"), "futu",
        "futu-security-static-info-v1", "futu-single-static-info-v1", static_info, admission_required=False)
    for market, asset in (("HK", "STOCK"), ("HK", "ETF"), ("US", "STOCK"), ("US", "ETF"))
) + tuple(
    QueryBinding(MINUTE_CONTRACT, ExactKey(market, asset, "MINUTE_BAR", "none", period), "futu",
        *minute_revisions(market, asset, period),
        minute, admission_required=False)
    for market in ("CN", "HK", "US") for asset in ("STOCK", "ETF")
    for period in ("1m", "5m", "15m", "30m", "60m")
) + (QueryBinding(LOT_CONTRACT, ExactKey("HK", "STOCK", "SECURITY_LOT_SIZE"), "futu",
    "futu-security-lot-size-v1", "futu-static-info-v1", lot_size, admission_required=False),),
    ("data_provider.futu_market", "FutuMarketAdapter"), credentials=(("opend", ("host", "port")),),
    dependency_modules=("futu",), factory_scope="provider", quote_source_aliases=(('futu', 'futu'),),
    upstream_groups=(('futu', 'futu'),))
