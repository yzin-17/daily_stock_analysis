"""Futu 十二个既有日线目标，保留原隔离查询与完整 SDK 分页。"""

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead
from types import SimpleNamespace

from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, QueryBinding, ExactKey, SourceDefinition
from src.services.thesis_ledger_security_static_contract_v1 import CONTRACT as STATIC_CONTRACT
from src.services.thesis_ledger_security_lot_contract_v1 import CONTRACT as LOT_CONTRACT

PROTOCOL = "dsa-futu-exact-daily-sdk-pagination-v1"
LOT_SOURCE_URL = "https://openapi.futunn.com/futu-api-doc/quote/get-static-info.html"


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    return build_provider_manifest("futu", "富途 OpenAPI", {
        "SECURITY_LOT_SIZE": ("STOCK",), "REALTIME_QUOTE": ("STOCK", "ETF"),
        "DAILY_BAR": ("STOCK", "ETF"), "SECURITY_STATIC_INFO": ("STOCK", "ETF"),
    }, requires_credential=True, markets=("HK", "US"))


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
    return SimpleNamespace(price=value.get("price"), open_price=value.get("open_price"), high=value.get("high"),
        low=value.get("low"), pre_close=value.get("pre_close"), volume=value.get("volume"), amount=value.get("amount"),
        change_amount=value.get("change_amount"), change_pct=value.get("change_pct"),
        fetched_at=value.get("fetched_at"), provider_timestamp=value.get("provider_timestamp"),
        is_stale=value.get("is_stale", False), source=value.get("source"), units=value.get("units"))


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


SOURCE = SourceDefinition("futu", manifest, factory, tuple(
    DailyBinding(ExactKey(market, asset, "DAILY_BAR", adjustment), "futu",
        f"dsa-v3-{market.lower()}-{asset.lower()}-futu-{adjustment}-exact-daily-v1", PROTOCOL, PROTOCOL,
        None, daily, gated=True)
    for market, asset, adjustment in (
        ("HK", "STOCK", "none"), ("HK", "STOCK", "qfq"), ("HK", "STOCK", "hfq"),
        ("HK", "ETF", "none"), ("HK", "ETF", "qfq"), ("HK", "ETF", "hfq"),
        ("US", "STOCK", "none"), ("US", "STOCK", "qfq"), ("US", "STOCK", "hfq"),
        ("US", "ETF", "none"), ("US", "ETF", "qfq"), ("US", "ETF", "hfq"),
    )
) + tuple(
    QuoteBinding(ExactKey(market, asset, "REALTIME_QUOTE"), "futu",
        f"futu-{market.lower()}-{asset.lower()}-exact-quote-v1", "futu-direct-quote-v1", quote)
    for market, asset in (("HK", "STOCK"), ("HK", "ETF"), ("US", "STOCK"), ("US", "ETF"))
) + tuple(
    QueryBinding(STATIC_CONTRACT, ExactKey(market, asset, "SECURITY_STATIC_INFO"), "futu",
        "futu-security-static-info-v1", "futu-single-static-info-v1", static_info, admission_required=False)
    for market, asset in (("HK", "STOCK"), ("HK", "ETF"), ("US", "STOCK"), ("US", "ETF"))
) + (QueryBinding(LOT_CONTRACT, ExactKey("HK", "STOCK", "SECURITY_LOT_SIZE"), "futu",
    "futu-security-lot-size-v1", "futu-static-info-v1", lot_size, admission_required=False),),
    ("data_provider.futu_market", "FutuMarketAdapter"), credentials=(("opend", ("host", "port")),),
    dependency_modules=("futu",), factory_scope="provider", quote_source_aliases=(('futu', 'futu'),))
