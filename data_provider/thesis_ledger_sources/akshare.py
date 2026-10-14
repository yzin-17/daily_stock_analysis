"""AKShare 既有十二个精确日线绑定；复用单目标接口，不使用分析备用链。"""

from datetime import date

from src.services.thesis_ledger_capability_contracts import (
    CapabilityContractError, DailyQuery, DailyRead, ReadContext, provider_symbol_for_contract,
)
from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, QueryBinding, EventBinding, ExactKey, SourceDefinition
from data_provider.eastmoney_cash_capability import CONTRACT as CASH_CONTRACT, read_cash, revisions as cash_revisions, wire_project as cash_wire
from data_provider.eastmoney_split_capability import CONTRACT as SPLIT_CONTRACT, read_split, revisions as split_revisions, wire_project as split_wire
from src.services.thesis_ledger_fund_nav_contract_v1 import CONTRACT as NAV_CONTRACT
from src.services.thesis_ledger_fund_nav_history_contract_v1 import CONTRACT as NAV_HISTORY_CONTRACT
from src.services.thesis_ledger_fund_holdings_contract_v1 import CONTRACT as HOLDINGS_CONTRACT
from src.services.thesis_ledger_chip_summary_contract_v1 import CONTRACT as CHIP_CONTRACT
from src.services.thesis_ledger_daily_units_contract import DailyUnitsPolicy
from data_provider.akshare_daily_contract import project_public_units


def manifest():
    from src.services.thesis_ledger_akshare_manifest import build_akshare_manifest
    sina_daily_assets = tuple(sorted({binding.key.asset_type for binding in SOURCE.bindings
        if isinstance(binding, DailyBinding) and binding.upstream_source == 'sina'}))
    return build_akshare_manifest(sina_daily_assets=sina_daily_assets)


def factory(context):
    from data_provider.akshare_fetcher import AkshareFetcher
    return AkshareFetcher()


def probe_cash(context):
    from data_provider.eastmoney_fund_dividend_reader import fetch_fund_dividend_observations
    fetch_fund_dividend_observations(context.symbol, start=context.start, end=context.end, max_pages=2)


def probe_split(context):
    from src.services.thesis_ledger_split_event_reader_v3 import read_mapped_split_events
    read_mapped_split_events(context.credentials.database_path,
        {'symbol': context.symbol, 'start': context.start, 'end': context.end}, dict(context.admission))


def daily(upstream: str, query: DailyQuery, context: ReadContext) -> DailyRead:
    method = getattr(context.adapter, "get_daily_data_for_source", None)
    if not callable(method):
        raise CapabilityContractError("unsupported_source", "来源没有精确日线适配器")
    frame = method(
        provider_symbol_for_contract(query.symbol), upstream,
        start_date=query.start, end_date=query.end,
        days=(date.fromisoformat(query.end) - date.fromisoformat(query.start)).days + 1,
        adjustment=query.adjustment, timeout_seconds=context.timeout_seconds,
    )
    if isinstance(frame, tuple) and len(frame) == 2:
        frame = frame[0]
    return DailyRead(frame, 1)


def source_reader(upstream: str):
    def read(query: DailyQuery, context: ReadContext) -> DailyRead:
        return daily(upstream, query, context)
    return read


def etf_quote(query, context):
    from data_provider.akshare_etf_quote import exact_etf_quote
    return exact_etf_quote(query.symbol, CapabilityContractError)


def latest_nav(query, context):
    from data_provider.eastmoney_nav_projection import latest_nav as project
    return project(context.adapter.get_fund_nav_history(query.symbol.removesuffix('.OF')), query, 'akshare', 'eastmoney')


def history_nav(query, context):
    from data_provider.eastmoney_nav_projection import history_nav as project
    return project(context.adapter.get_fund_nav_history(query.symbol.removesuffix('.OF')), query, 'akshare', 'eastmoney')


def holdings(query, context):
    from data_provider.eastmoney_holdings_projection import holdings_result
    return holdings_result(context.adapter.get_fund_holdings(query.symbol.removesuffix('.OF')), query, 'akshare', 'eastmoney')


def stock_quote_reader(upstream):
    sdk_source = "em" if upstream == "eastmoney" else upstream

    def read(query, context):
        from data_provider.akshare_fetcher import _is_etf_code
        from data_provider.realtime_types import get_realtime_circuit_breaker
        symbol = provider_symbol_for_contract(query.symbol)
        if _is_etf_code(symbol):
            raise CapabilityContractError("invalid_request", "股票报价不能使用 ETF 代码")
        if not get_realtime_circuit_breaker().is_available(f"akshare_{sdk_source}"):
            raise CapabilityContractError("not_covered", "原来源熔断期间未返回报价")
        method = getattr(context.adapter, f"_get_stock_realtime_quote_{sdk_source}", None)
        if not callable(method):
            raise CapabilityContractError("unsupported_source", "来源没有对应股票报价接口")
        value = method(symbol)
        if value is None:
            raise CapabilityContractError("not_covered", "原来源未返回目标报价")
        return value
    return read


def sina_stock_quote(query, context):
    import random
    import re
    from data_provider import akshare_fetcher as sdk
    from data_provider.realtime_types import UnifiedRealtimeQuote, RealtimeSource, safe_float, safe_int
    symbol = provider_symbol_for_contract(query.symbol)
    if sdk._is_etf_code(symbol):
        raise CapabilityContractError("invalid_request", "股票报价不能使用 ETF 代码")
    identity = sdk._to_sina_tx_symbol(symbol)
    breaker = sdk.get_realtime_circuit_breaker()
    source_key = "akshare_sina"
    if not breaker.is_available(source_key):
        raise CapabilityContractError("not_covered", "原来源熔断期间未返回报价")
    try:
        context.adapter._enforce_rate_limit()
        response = sdk.requests.get(f"http://{sdk.SINA_REALTIME_ENDPOINT}={identity}",
            headers={"Referer": "http://finance.sina.com.cn", "User-Agent": random.choice(sdk.USER_AGENTS)}, timeout=10)
        response.encoding = "gbk"
    except Exception:
        breaker.record_failure(source_key, "Sina 报价读取失败")
        raise CapabilityContractError("not_covered", "原来源未返回目标报价") from None
    if response.status_code != 200 or not response.text.strip():
        breaker.record_failure(source_key, "Sina 报价不可用")
        raise CapabilityContractError("not_covered", "原来源未返回目标报价")
    envelope = re.fullmatch(r'\s*var\s+hq_str_' + re.escape(identity) + r'\s*=\s*"([^"\r\n]*)";?\s*', response.text)
    if envelope is None:
        breaker.record_failure(source_key, "Sina 响应身份或封套不匹配")
        raise CapabilityContractError("invalid_response", "Sina 响应身份或封套不匹配")
    if not envelope.group(1):
        breaker.record_failure(source_key, "Sina 报价不可用")
        raise CapabilityContractError("not_covered", "原来源未返回目标报价")
    fields = envelope.group(1).split(',')
    if len(fields) < 32 or not fields[0].strip():
        breaker.record_failure(source_key, "Sina 响应字段不完整")
        raise CapabilityContractError("invalid_response", "Sina 响应字段不完整")
    price, previous = safe_float(fields[3]), safe_float(fields[2])
    change, percentage = None, None
    if price and previous and previous > 0:
        change = price - previous
        percentage = change / previous * 100
    breaker.record_success(source_key)
    return UnifiedRealtimeQuote(code=symbol, name=fields[0], source=RealtimeSource.AKSHARE_SINA,
        price=price, pre_close=previous, change_amount=change, change_pct=percentage,
        volume=safe_int(fields[8]), amount=safe_float(fields[9]), open_price=safe_float(fields[1]),
        high=safe_float(fields[4]), low=safe_float(fields[5]))


# 精确有效组合；不根据来源名字或独立参数数组推断其他市场/资产支持。
def chip(query, context):
    from data_provider.chip_summary_projection import project
    return project(context.adapter.get_chip_distribution(query.symbol.split('.')[0]), query,
                   'akshare', 'eastmoney', 'eastmoney-price-estimate-v1')


SOURCE = SourceDefinition("akshare", manifest, factory, tuple(
    DailyBinding(ExactKey("CN", asset, "DAILY_BAR", adjustment), upstream,
                 f"dsa-v3-{asset.lower()}-akshare-{upstream}-{adjustment}-adapter-v1",
                 protocol, protocol, None, source_reader(upstream),
                 public_units_policy=DailyUnitsPolicy(('native_daily_contract',), project_public_units) if upstream == 'eastmoney' else None)
    for asset, upstream, protocol in (
        ("STOCK", "eastmoney", "akshare-stock-eastmoney-range-response-v1"),
        ("STOCK", "sina", "akshare-stock-sina-range-response-v1"),
        ("STOCK", "tencent", "akshare-stock-tencent-range-response-v1"),
        ("ETF", "eastmoney", "akshare-etf-range-response-v1"),
    )
    for adjustment in ("none", "qfq", "hfq")
) + (QuoteBinding(ExactKey("CN", "ETF", "REALTIME_QUOTE"), "eastmoney",
    "akshare-etf-single-em-v1", "eastmoney-single-security-v1", etf_quote, dependency_modules=('requests',)),
    QuoteBinding(ExactKey("CN", "STOCK", "REALTIME_QUOTE"), "eastmoney",
        "akshare-stock-em-v2", "akshare-stock-spot-em-v1", stock_quote_reader("eastmoney")),
    QuoteBinding(ExactKey("CN", "STOCK", "REALTIME_QUOTE"), "tencent",
        "akshare-stock-tencent-v2", "akshare-stock-spot-tencent-v1", stock_quote_reader("tencent")),
    QuoteBinding(ExactKey("CN", "STOCK", "REALTIME_QUOTE"), "sina",
        "akshare-stock-sina-v2", "akshare-stock-spot-sina-v1", sina_stock_quote, legacy_probe_default=True),
    QueryBinding(NAV_CONTRACT, ExactKey('CN', 'MUTUAL_FUND', 'FUND_NAV'), 'eastmoney',
        'akshare-fund-nav-em-v1', 'akshare-fund-open-info-em-v1', latest_nav, admission_required=False),
    QueryBinding(NAV_HISTORY_CONTRACT, ExactKey('CN', 'MUTUAL_FUND', 'FUND_NAV_HISTORY'), 'eastmoney',
        'akshare-fund-nav-em-v1', 'akshare-fund-open-info-em-v1', history_nav, admission_required=False),
    QueryBinding(HOLDINGS_CONTRACT, ExactKey('CN', 'MUTUAL_FUND', 'FUND_HOLDINGS'), 'eastmoney',
        'akshare-fund-holdings-em-v1', 'akshare-fund-portfolio-em-v1', holdings, admission_required=False),
    QueryBinding(CHIP_CONTRACT, ExactKey('CN', 'STOCK', 'CHIP_SUMMARY'), 'eastmoney',
        'akshare-stock-chip-em-v1', 'akshare-stock-cyq-em-v1', chip, admission_required=False),
    EventBinding(CASH_CONTRACT, ExactKey('CN', 'ETF', 'CASH_DISTRIBUTION'), 'eastmoney',
                 read_cash, cash_revisions, cash_wire, probe_read=probe_cash),
    EventBinding(SPLIT_CONTRACT, ExactKey('CN', 'ETF', 'SPLIT_EVENT'), 'eastmoney',
                 read_split, split_revisions, split_wire, probe_read=probe_split)),
    ("data_provider.akshare_fetcher", "AkshareFetcher"), dependency_modules=("akshare",),
    quote_source_aliases=(('eastmoney', 'eastmoney'), ('akshare_em', 'eastmoney'), ('sina', 'sina'),
                          ('akshare_sina', 'sina'), ('akshare_qq', 'tencent'), ('tencent', 'tencent')))
