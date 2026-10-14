"""通达信既有沪深股票未复权日线；单目标 SDK 投影由来源拥有。"""

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead, provider_symbol_for_contract
from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, ExactKey, SourceDefinition

PROTOCOL = "dsa-pytdx-exact-daily-single-response-v1"


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    return build_provider_manifest("pytdx", "通达信（pytdx）", {
        "REALTIME_QUOTE": ("STOCK",), "DAILY_BAR": ("STOCK",),
    }, markets=("CN",), configuration_mode="built_in")


def factory(context):
    from data_provider.pytdx_fetcher import PytdxFetcher
    return PytdxFetcher()


def read_frame(adapter, request):
    code, exchange = request.symbol.split(".", 1)
    if exchange not in {"SH", "SZ", "BJ"} or len(code) != 6 or not code.isdigit():
        raise ExactDailyError("invalid_request", "中国内地日线需要完整证券代码")
    if exchange == "BJ":
        raise ExactDailyError("not_covered", "pytdx 当前精确接入仅覆盖沪深市场")
    raw = adapter._fetch_raw_data(request.symbol, request.start, request.end)
    columns = {"vol": "volume"}
    if "date" not in raw.columns:
        columns["datetime"] = "date"
    return normalize_exact_daily(raw, start=request.start, end=request.end, source="pytdx", columns=columns)


def daily(query, context):
    try:
        return DailyRead(read_frame(context.adapter, query), 1)
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def quote(query, context):
    from types import SimpleNamespace
    from data_provider.pytdx_fetcher import is_bse_code
    symbol = provider_symbol_for_contract(query.symbol)
    if is_bse_code(symbol):
        raise CapabilityContractError("not_covered", "pytdx 当前仅覆盖沪深股票")
    adapter = context.adapter
    try:
        market, code = adapter._get_market_code(symbol)
        with adapter._pytdx_session() as api:
            values = api.get_security_quotes([(market, code)])
    except Exception:
        raise CapabilityContractError("not_covered", "pytdx 未返回目标报价") from None
    if values is None or isinstance(values, list) and not values:
        raise CapabilityContractError("not_covered", "pytdx 未返回目标报价")
    if (not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict)
            or values[0].get("code") != code or "market" in values[0] and (
                isinstance(values[0]["market"], bool) or values[0]["market"] != market)):
        raise CapabilityContractError("invalid_response", "pytdx 报价证券或市场不匹配")
    row = values[0]
    return SimpleNamespace(price=row.get("price"), open_price=row.get("open"), high=row.get("high"), low=row.get("low"),
        pre_close=row.get("last_close"), volume=row.get("vol"), amount=row.get("amount"),
        provider_timestamp=None, source="pytdx")


SOURCE = SourceDefinition("pytdx", manifest, factory, (
    DailyBinding(ExactKey("CN", "STOCK", "DAILY_BAR", "none"), "pytdx",
                 "dsa-v3-cn-stock-pytdx-none-exact-daily-v1", PROTOCOL, PROTOCOL,
                 None, daily, gated=True),
    QuoteBinding(ExactKey("CN", "STOCK", "REALTIME_QUOTE"), "pytdx",
        "pytdx-stock-quote-v2", "pytdx-direct-v1", quote),
), ("data_provider.pytdx_fetcher", "PytdxFetcher"), dependency_modules=("pytdx",))
