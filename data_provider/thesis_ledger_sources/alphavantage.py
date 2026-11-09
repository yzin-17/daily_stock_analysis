"""Alpha Vantage 免费近期美股未复权日线；来源拥有窗口边界及工厂。"""

import pandas as pd
from datetime import date, datetime, timezone
from types import SimpleNamespace

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead, SourceProbePolicy, EnvironmentCredentialPolicy, provider_symbol_for_contract
from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, ExactKey, SourceDefinition

PROTOCOL = "dsa-alphavantage-compact-daily-single-response-v2"


def environment_credentials():
    from src.services.thesis_ledger_environment_credentials import read_config_fields
    return read_config_fields((('apiKey', 'alphavantage_api_key'),))


def probe_daily(context):
    from src.services.thesis_ledger_daily_probe_validation import daily_frame, validate_bars
    validate_bars(daily_frame(context.adapter.get_daily_data(context.sample_symbol, days=5, strict=True)))


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    return build_provider_manifest("alphavantage", "Alpha Vantage", {
        "MINUTE_BAR": ("STOCK", "ETF"), "REALTIME_QUOTE": ("STOCK",), "DAILY_BAR": ("STOCK",),
    }, requires_credential=True, markets=("US",), configuration_mode="dsa_environment")


def factory(context):
    from data_provider.alphavantage_fetcher import AlphaVantageFetcher
    snapshot = context.snapshot
    return AlphaVantageFetcher(api_key=snapshot.values.get("apiKey"), strict_errors=snapshot.source == "control")


def read_frame(adapter, request):
    from data_provider.multimarket_exact_daily import _http_json
    symbol = request.symbol.removesuffix(".US")
    payload = _http_json("https://www.alphavantage.co/query", {
        "function": "TIME_SERIES_DAILY", "symbol": symbol,
        "outputsize": "compact", "apikey": adapter._api_key,
    })
    if "Note" in payload or "Information" in payload:
        raise ExactDailyError("quota_unavailable", "Alpha Vantage 免费日线权限或额度不可用")
    series = payload.get("Time Series (Daily)")
    if not isinstance(series, dict) or not series:
        raise ExactDailyError("not_covered", "Alpha Vantage 未返回请求日线")
    metadata = payload.get("Meta Data")
    if not isinstance(metadata, dict) or metadata.get("2. Symbol") != symbol:
        raise ExactDailyError("invalid_response", "Alpha Vantage 日线证券不匹配")
    if len(series) > 100 or any(not isinstance(values, dict) for values in series.values()):
        raise ExactDailyError("invalid_response", "Alpha Vantage 免费日线响应超额或结构无效")
    try:
        dates = sorted(date.fromisoformat(day).isoformat() for day in series)
        if dates != sorted(series):
            raise ValueError("noncanonical date")
    except (TypeError, ValueError):
        raise ExactDailyError("invalid_response", "Alpha Vantage 日线日期无效") from None
    if request.start < dates[0] or request.end > dates[-1]:
        raise ExactDailyError("not_covered", "请求超出 Alpha Vantage 免费近期日线范围")
    if not any(request.start <= day <= request.end for day in dates):
        raise ExactDailyError("not_covered", "请求窗口没有 Alpha Vantage 日线")
    raw = pd.DataFrame([{"date": day, **{field: values.get(source) for field, source in {
        "open": "1. open", "high": "2. high", "low": "3. low", "close": "4. close", "volume": "5. volume",
    }.items()}} for day, values in series.items()])
    return normalize_exact_daily(raw, start=request.start, end=request.end, source="alphavantage", timezone="America/New_York")


def daily(query, context):
    try:
        return DailyRead(read_frame(context.adapter, query), 1)
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def quote(query, context):
    import requests
    from data_provider.alphavantage_fetcher import _AV_BASE_URL, _strict_request_error, _strict_payload_error
    from src.services.thesis_ledger_quote_validation import number
    symbol = provider_symbol_for_contract(query.symbol).removesuffix(".US")
    adapter = context.adapter
    if not adapter._api_key or not adapter._is_us_stock(symbol):
        raise CapabilityContractError("not_covered", "Provider 未覆盖该标的")
    try:
        adapter.random_sleep(0.5, 1.5)
        response = requests.get(_AV_BASE_URL, params={'function': 'GLOBAL_QUOTE', 'symbol': symbol, 'apikey': adapter._api_key}, timeout=5)
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        raise _strict_request_error(error) from None
    payload_error = _strict_payload_error(payload)
    if payload_error is not None:
        raise payload_error
    raw = payload.get('Global Quote')
    if not isinstance(raw, dict) or not raw.get('05. price'):
        raise CapabilityContractError("not_covered", "Alpha Vantage 未返回目标报价")
    if raw.get('01. symbol') != symbol:
        raise CapabilityContractError("invalid_response", "Alpha Vantage 报价证券不匹配")
    values = {}
    for field, original in {'price': '05. price', 'pre_close': '08. previous close', 'open_price': '02. open',
            'high': '03. high', 'low': '04. low', 'volume': '06. volume'}.items():
        value = raw.get(original)
        values[field] = None if value is None else number(value, field, positive=field == 'price')
    return SimpleNamespace(**values, amount=None, provider_timestamp=None,
        fetched_at=datetime.now(timezone.utc).isoformat(), source='alphavantage')


from data_provider.intraday_projection import intraday_bindings
from data_provider.alphavantage_intraday import read_minute

INTRADAY_BINDINGS = intraday_bindings(read_minute, 'alphavantage', markets=('US',))


SOURCE = SourceDefinition("alphavantage", manifest, factory, (*INTRADAY_BINDINGS,
    DailyBinding(ExactKey("US", "STOCK", "DAILY_BAR", "none"), "alphavantage",
                 "dsa-v3-us-stock-alphavantage-none-exact-daily-v2", PROTOCOL, PROTOCOL,
                 100, daily, gated=True),
    QuoteBinding(ExactKey("US", "STOCK", "REALTIME_QUOTE"), "alphavantage",
        "alphavantage-us-stock-exact-quote-v2", "alphavantage-direct-quote-v1", quote),
), ("data_provider.alphavantage_fetcher", "AlphaVantageFetcher"),
    credentials=(("api_key", ("apiKey",)),), dependency_modules=("requests",), factory_scope="provider",
    probe_policy=SourceProbePolicy(probe_daily, capabilities=('DAILY_BAR',), adapter_required=True),
    environment_credential_policy=EnvironmentCredentialPolicy(environment_credentials))
