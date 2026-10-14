"""TickFlow 既有三项股票日线；工厂及精确 SDK 参数由来源拥有。"""

import pandas as pd

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead, SourceProbePolicy, EnvironmentCredentialPolicy
from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, ExactKey, SourceDefinition

PROTOCOL = "dsa-tickflow-exact-daily-single-response-v1"


def environment_credentials():
    from src.services.thesis_ledger_environment_credentials import read_config_fields
    return read_config_fields((('apiKey', 'tickflow_api_key'),))


def probe_quote(context):
    from src.services.thesis_ledger_quote_dispatch import realtime_quote
    symbol = context.sample_symbol
    if '.' not in symbol:
        suffix = 'SH' if symbol.startswith(('6', '9')) else 'SZ'
        symbol = f'{symbol}.{suffix}'
    value = realtime_quote(context.adapter, 'tickflow', symbol, 'STOCK', 'tickflow', CapabilityContractError)
    if value is None or getattr(value, 'price', None) is None:
        raise CapabilityContractError('not_covered', 'Provider 未返回代表性行情')


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    return build_provider_manifest("tickflow", "TickFlow", {
        "REALTIME_QUOTE": ("STOCK",), "DAILY_BAR": ("STOCK",),
    }, requires_credential=True, markets=("CN",), configuration_mode="dsa_environment")


def factory(context):
    from data_provider.tickflow_fetcher import TickFlowFetcher
    from src.config import get_config

    config = get_config()
    values = context.snapshot.values if context.snapshot else {}
    return TickFlowFetcher(
        api_key=values.get("apiKey"), timeout=context.timeout_seconds,
        kline_adjust=getattr(config, "tickflow_kline_adjust", "none"),
        batch_daily_enabled=getattr(config, "tickflow_batch_daily_enabled", True),
        batch_size=getattr(config, "tickflow_batch_size", 100),
        priority=getattr(config, "tickflow_priority", 2),
    )


def read_frame(adapter, request, *, source="tickflow", validate_raw=None):
    code, exchange = request.symbol.split(".", 1)
    if exchange not in {"SH", "SZ", "BJ"} or len(code) != 6 or not code.isdigit():
        raise ExactDailyError("invalid_request", "中国内地日线需要完整证券代码")
    client = adapter._get_client()
    if client is None:
        raise ExactDailyError("credential_missing", "TickFlow API Key 未配置")
    payload = client.klines.get(
        request.symbol, period="1d", count=adapter._daily_kline_count(request.start, request.end),
        start_time=adapter._date_to_ms(request.start), end_time=adapter._date_to_ms(request.end, end_of_day=True),
        adjust={"none": "none", "qfq": "forward", "hfq": "backward"}[request.adjustment], as_dataframe=False,
    )
    if validate_raw is not None:
        validate_raw(payload, request, adapter._daily_kline_count(request.start, request.end))
    raw = pd.DataFrame(payload)
    if "date" not in raw.columns:
        timestamp = next((field for field in ("timestamp", "time", "ts") if field in raw.columns), None)
        if timestamp is None:
            raise ExactDailyError("invalid_response", "TickFlow 日线缺少日期")
        return normalize_exact_daily(raw.rename(columns={timestamp: "date"}), start=request.start,
                                     end=request.end, source=source, date_unit="ms")
    return normalize_exact_daily(raw, start=request.start, end=request.end, source=source)


def daily(query, context):
    try:
        return DailyRead(read_frame(context.adapter, query), 1)
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def quote(query, context):
    client = context.adapter._get_client()
    if client is None:
        raise CapabilityContractError("credential_missing", "TickFlow 凭证未配置")
    quotes = client.quotes.get(symbols=[query.symbol])
    if not isinstance(quotes, list) or len(quotes) != 1 or quotes[0].get("symbol") != query.symbol:
        raise CapabilityContractError("invalid_response", "TickFlow 报价标的身份不一致")
    from src.services.thesis_ledger_quote_validation import number
    raw = quotes[0]
    for field in ("volume", "amount"):
        if raw.get(field) is not None:
            number(raw[field], field)
    value = context.adapter._quote_to_unified_quote(query.symbol, raw)
    if value is None:
        raise CapabilityContractError("not_covered", "Provider 未覆盖该标的")
    if raw.get("volume") is None:
        value.volume = None
    if raw.get("amount") is None:
        value.amount = None
    return value


SOURCE = SourceDefinition("tickflow", manifest, factory, tuple(
    DailyBinding(ExactKey("CN", "STOCK", "DAILY_BAR", adjustment), "tickflow",
                 f"dsa-v3-cn-stock-tickflow-{adjustment}-exact-daily-v1", PROTOCOL, PROTOCOL,
                 None, daily, gated=True)
    for adjustment in ("none", "qfq", "hfq")
) + (QuoteBinding(ExactKey("CN", "STOCK", "REALTIME_QUOTE"), "tickflow",
    "tickflow-cn-stock-exact-quote-v2", "tickflow-direct-quote-v1", quote),),
    ("data_provider.tickflow_fetcher", "TickFlowFetcher"),
    credentials=(("api_key", ("apiKey",)),), dependency_modules=("tickflow",), factory_scope="provider",
    probe_policy=SourceProbePolicy(probe_quote, capabilities=('REALTIME_QUOTE',), adapter_required=True),
    environment_credential_policy=EnvironmentCredentialPolicy(environment_credentials))
