"""TickFlow 统一来源；免凭证日线和账号功能共用精确目录。"""

from dataclasses import replace
from data_provider.thesis_ledger_sources import tickflow_free_history as history
from data_provider.tickflow_account_access import probe_account_access, requires_credential
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, SourceProbePolicy, EnvironmentCredentialPolicy
from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, ExactKey, SourceDefinition
from src.services.thesis_ledger_quote_units_contract import QuotePublicUnits

PROTOCOL = "dsa-tickflow-unified-daily-v2"
ETF_DAILY_PROTOCOL = "dsa-tickflow-etf-free-daily-v1"


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
    return {**build_provider_manifest("tickflow", "TickFlow", {
        "MINUTE_BAR": ("STOCK", "ETF"), "REALTIME_QUOTE": ("STOCK", "ETF"), "DAILY_BAR": ("STOCK", "ETF"),
    }, requires_credential=False, markets=("CN",), configuration_mode="dsa_environment"), "accountAccessSupported": True}


def factory(context):
    from data_provider.tickflow_fetcher import TickFlowFetcher
    from src.data_config import get_data_config as get_config

    config = get_config()
    values = context.snapshot.values if context.snapshot else {}
    return TickFlowFetcher(
        api_key=values.get("apiKey"), timeout=context.timeout_seconds,
        kline_adjust=getattr(config, "tickflow_kline_adjust", "none"),
        batch_daily_enabled=getattr(config, "tickflow_batch_daily_enabled", True),
        batch_size=getattr(config, "tickflow_batch_size", 100),
        priority=getattr(config, "tickflow_priority", 2),
    )


def daily(query, context):
    return history.daily(query, replace(context, adapter=history.factory(context)), upstream_source="tickflow")


def quote(query, context):
    etf = query.instrument_type == "ETF"
    if etf and not history.etf_symbol_in_scope(query.symbol):
        raise CapabilityContractError("invalid_request", "TickFlow ETF 报价需要沪深完整证券代码")
    client = context.adapter._get_client()
    if client is None:
        raise CapabilityContractError("credential_missing", "TickFlow 凭证未配置")
    quotes = client.quotes.get(symbols=[query.symbol])
    if (not isinstance(quotes, list) or len(quotes) != 1 or not isinstance(quotes[0], dict)
            or quotes[0].get("symbol") != query.symbol):
        raise CapabilityContractError("invalid_response", "TickFlow 报价标的身份不一致")
    from src.services.thesis_ledger_quote_validation import number, validate_quote
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
    if etf:
        # ETF 量额保留上游数值，避免沿用旧 Fetcher 的股票成交量换算。
        value.volume = raw.get("volume")
        return validate_quote(value).model_copy(update={
            "upstream_source": "tickflow",
            "units": QuotePublicUnits(priceCurrency="CNY", volume="unknown", turnoverCurrency="unknown"),
        })
    return value


from data_provider.intraday_projection import intraday_bindings
from data_provider.tickflow_intraday import read_minute

INTRADAY_BINDINGS = intraday_bindings(read_minute, 'tickflow')


SOURCE = SourceDefinition("tickflow", manifest, factory, tuple(
    DailyBinding(ExactKey("CN", "STOCK", "DAILY_BAR", adjustment), "tickflow",
                 f"dsa-v3-cn-stock-tickflow-{adjustment}-exact-daily-v2", PROTOCOL, PROTOCOL,
                 history.MAXIMUM_ROWS, daily, research_calendar_eligible=history.research_calendar_eligible)
    for adjustment in ("none", "qfq", "hfq")
) + INTRADAY_BINDINGS + (
    DailyBinding(ExactKey("CN", "ETF", "DAILY_BAR", "none"), "tickflow",
                 "dsa-v3-cn-etf-tickflow-none-exact-daily-v1", ETF_DAILY_PROTOCOL, ETF_DAILY_PROTOCOL,
                 history.MAXIMUM_ROWS, daily),
    QuoteBinding(ExactKey("CN", "STOCK", "REALTIME_QUOTE"), "tickflow",
                 "tickflow-cn-stock-exact-quote-v2", "tickflow-direct-quote-v1", quote),
    QuoteBinding(ExactKey("CN", "ETF", "REALTIME_QUOTE"), "tickflow",
                 "tickflow-cn-etf-exact-quote-v1", "tickflow-direct-etf-quote-v1", quote),
),
    ("data_provider.tickflow_fetcher", "TickFlowFetcher"),
    credentials=(("api_key", ("apiKey",)),), dependency_modules=("tickflow",), factory_scope="provider",
    probe_policy=SourceProbePolicy(probe_quote, capabilities=('REALTIME_QUOTE',), adapter_required=True),
    account_access_probe=probe_account_access,
    route_requires_credential=requires_credential,
    account_access_max_requests=16,
    upstream_groups=(('tickflow', 'tickflow'), ('tickflow-free-history', 'tickflow-free-history')),
    environment_credential_policy=EnvironmentCredentialPolicy(environment_credentials))
