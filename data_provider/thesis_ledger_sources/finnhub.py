"""Finnhub 既有美股 qfq 日线；保留纽约时钟与秒级数组投影。"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead, SourceProbePolicy, EnvironmentCredentialPolicy, provider_symbol_for_contract
from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, ExactKey, SourceDefinition

PROTOCOL = "dsa-finnhub-exact-daily-single-response-v1"


def environment_credentials():
    from src.services.thesis_ledger_environment_credentials import read_config_fields
    return read_config_fields((('apiKey', 'finnhub_api_key'),))


def probe_daily(context):
    from src.services.thesis_ledger_daily_probe_validation import daily_frame, validate_bars
    validate_bars(daily_frame(context.adapter.get_daily_data(context.sample_symbol, days=5, strict=True)))


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    return build_provider_manifest("finnhub", "Finnhub", {
        "REALTIME_QUOTE": ("STOCK",), "DAILY_BAR": ("STOCK",),
    }, requires_credential=True, markets=("US",), configuration_mode="dsa_environment")


def factory(context):
    from data_provider.finnhub_fetcher import FinnhubFetcher
    snapshot = context.snapshot
    return FinnhubFetcher(api_key=snapshot.values.get("apiKey"), strict_errors=snapshot.source == "control")


def read_frame(adapter, request):
    from data_provider.multimarket_exact_daily import _http_json
    timezone = "America/New_York"
    first = datetime.combine(date.fromisoformat(request.start), datetime.min.time(), ZoneInfo(timezone))
    last = datetime.combine(date.fromisoformat(request.end) + timedelta(days=1), datetime.min.time(), ZoneInfo(timezone))
    payload = _http_json("https://finnhub.io/api/v1/stock/candle", {
        "symbol": request.symbol.removesuffix(".US"), "resolution": "D", "from": int(first.timestamp()),
        "to": int(last.timestamp()) - 1, "token": adapter._api_key,
    })
    if payload.get("s") != "ok":
        raise ExactDailyError("not_covered", "Finnhub 未返回请求日线")
    raw = pd.DataFrame({field: payload[source] for field, source in
        {"date": "t", "open": "o", "high": "h", "low": "l", "close": "c", "volume": "v"}.items()})
    return normalize_exact_daily(raw, start=request.start, end=request.end, source="finnhub", date_unit="s", timezone=timezone)


def daily(query, context):
    try:
        return DailyRead(read_frame(context.adapter, query), 1)
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def quote(query, context):
    value = context.adapter.get_realtime_quote(provider_symbol_for_contract(query.symbol).removesuffix(".US"), strict=True)
    if value is None:
        raise CapabilityContractError("not_covered", "Provider 未覆盖该标的")
    return value


SOURCE = SourceDefinition("finnhub", manifest, factory, (
    DailyBinding(ExactKey("US", "STOCK", "DAILY_BAR", "qfq"), "finnhub",
                 "dsa-v3-us-stock-finnhub-qfq-exact-daily-v1", PROTOCOL, PROTOCOL,
                 None, daily, gated=True),
    QuoteBinding(ExactKey("US", "STOCK", "REALTIME_QUOTE"), "finnhub",
        "finnhub-us-stock-exact-quote-v1", "finnhub-direct-quote-v1", quote),
), ("data_provider.finnhub_fetcher", "FinnhubFetcher"),
    credentials=(("api_key", ("apiKey",)),), dependency_modules=("requests",), factory_scope="provider",
    probe_policy=SourceProbePolicy(probe_daily, capabilities=('DAILY_BAR',), adapter_required=True),
    environment_credential_policy=EnvironmentCredentialPolicy(environment_credentials))
