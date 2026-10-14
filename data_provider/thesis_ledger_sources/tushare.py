"""Tushare 既有 CN/HK 股票未复权日线及原 HTTP 工厂生命周期。"""

import pandas as pd
import json
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot

from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily
from data_provider.tushare_fund_daily import PAGINATION_PROTOCOL as ETF_PROTOCOL
from data_provider.tushare_fund_transport import verify_transport as verify_etf_transport
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead, SourceProbePolicy, EnvironmentCredentialPolicy
from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, QueryBinding, EventBinding, ExactKey, SourceDefinition
from data_provider.tushare_cash_capability import CONTRACT as CASH_CONTRACT, read_cash, revisions as cash_revisions, wire_project as cash_wire, REVISION_FACTS as CASH_REVISIONS, current_admission as cash_admission
from src.services.thesis_ledger_chip_summary_contract_v1 import CONTRACT as CHIP_CONTRACT
from data_provider.tushare_account_access import probe_account_access

PROTOCOL = "dsa-tushare-exact-daily-single-response-v1"


def probe_cash(context):
    from src.services.thesis_ledger_tushare_mapped_read import read_tushare_mapped_fund_dividends
    read_tushare_mapped_fund_dividends(context.symbol, **context.mapped_read_options(),
        build_fetcher=context.credentials.build_adapter)


def environment_credentials():
    from src.services.thesis_ledger_environment_credentials import read_config_fields
    return read_config_fields((('token', 'tushare_token'),))


def environment_snapshot(values):
    from data_provider.tushare_endpoint import DEFAULT_TUSHARE_HTTP_URL, _resolve_tushare_http_url
    return {**values, 'httpUrl': _resolve_tushare_http_url() or DEFAULT_TUSHARE_HTTP_URL}


def account_snapshot(snapshot):
    values = environment_snapshot(dict(snapshot.values))
    return ProviderCredentialSnapshot.create(snapshot.provider_id, snapshot.source, snapshot.method, values,
                                             snapshot.config_version, snapshot.credential_version)


def probe_quote(context):
    from src.services.thesis_ledger_quote_dispatch import realtime_quote
    symbol = context.sample_symbol
    if '.' not in symbol:
        suffix = 'SH' if symbol.startswith(('6', '9')) else 'SZ'
        symbol = f'{symbol}.{suffix}'
    value = realtime_quote(context.adapter, 'tushare', symbol, 'STOCK', 'tushare', CapabilityContractError)
    if value is None or getattr(value, 'price', None) is None:
        raise CapabilityContractError('not_covered', 'Provider 未返回代表性行情')


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    return {**build_provider_manifest("tushare", "Tushare Pro", {
        "REALTIME_QUOTE": ("STOCK",), "DAILY_BAR": ("STOCK", "ETF"), "CHIP_SUMMARY": ("STOCK",),
    }, requires_credential=True, markets=("CN", "HK"), configuration_mode="dsa_environment"), "accountAccessSupported": True}


def factory(context):
    from data_provider.tushare_fetcher import TushareFetcher
    values = context.snapshot.values
    adapter = TushareFetcher(token=values.get("token"), request_timeout=5 if context.probe else 30,
                             http_url=values.get("httpUrl"))
    if adapter._api is not None:
        adapter._api._source_pinned = True
    return adapter


def read_frame(adapter, request, market):
    from data_provider.tushare_history_request import TushareHistoryRequest
    if market == "CN":
        code, exchange = request.symbol.split(".", 1)
        if exchange not in {"SH", "SZ", "BJ"} or len(code) != 6 or not code.isdigit():
            raise ExactDailyError("invalid_request", "中国内地日线需要完整证券代码")
    budget = TushareHistoryRequest(adapter, 4.5)
    budget.before_call()
    raw = budget.client.query("daily" if market == "CN" else "hk_daily", ts_code=request.symbol,
                              start_date=request.start.replace("-", ""), end_date=request.end.replace("-", ""))
    budget.check_complete()
    if raw is None or raw.empty:
        raise ExactDailyError("not_covered", "Tushare 未返回请求日线")
    if "ts_code" not in raw or not raw["ts_code"].eq(request.symbol).all():
        raise ExactDailyError("invalid_response", "Tushare 日线标的身份不一致")
    raw = raw.rename(columns={"trade_date": "date", "vol": "volume"})
    raw["date"] = pd.to_datetime(raw["date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    timezone = "Asia/Shanghai" if market == "CN" else "Asia/Hong_Kong"
    return normalize_exact_daily(raw, start=request.start, end=request.end, source="tushare", timezone=timezone)


def daily(query, context):
    market = "HK" if query.symbol.endswith(".HK") else "CN"
    try:
        return DailyRead(read_frame(context.adapter, query, market), 1)
    except ExactDailyError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def etf_daily(query, context):
    from datetime import date
    frame = context.adapter.get_daily_data_for_source(query.symbol, 'tushare',
        start_date=query.start, end_date=query.end,
        days=(date.fromisoformat(query.end) - date.fromisoformat(query.start)).days + 1,
        adjustment=query.adjustment, timeout_seconds=context.timeout_seconds)
    pages = verify_etf_transport(frame, query.start, query.end, query.adjustment)
    return DailyRead(frame, pages)


def quote(query, context):
    from types import SimpleNamespace
    from data_provider.tushare_history_request import TushareHistoryRequest
    budget = TushareHistoryRequest(context.adapter, 4.5)
    budget.before_call()
    frame = budget.client.query("rt_k", ts_code=query.symbol,
        fields="ts_code,close,open,high,low,pre_close,vol,amount,trade_time")
    budget.check_complete()
    if frame is None or frame.empty:
        raise CapabilityContractError("not_covered", "Tushare Pro 未返回目标报价")
    if "ts_code" not in frame or len(frame) != 1 or frame.iloc[0]["ts_code"] != query.symbol:
        raise CapabilityContractError("invalid_response", "Tushare Pro 报价标的身份不一致")
    row = frame.iloc[0]
    return SimpleNamespace(price=row.get("close"), open_price=row.get("open"), high=row.get("high"), low=row.get("low"),
        pre_close=row.get("pre_close"), volume=row.get("vol"), amount=row.get("amount"),
        change_pct=row.get("pct_chg"), timestamp=row.get("trade_time"))


def chip(query, context):
    from data_provider.chip_summary_projection import project
    return project(context.adapter.get_chip_distribution(query.symbol.split('.')[0]), query,
                   'tushare', 'tushare', 'tushare-cyq-estimate-v1')


def credential_revision_payload(snapshot: ProviderCredentialSnapshot) -> bytes | None:
    if snapshot.source != 'environment' or snapshot.method != 'token':
        return None
    token, endpoint = snapshot.values.get('token'), snapshot.values.get('httpUrl')
    if (not isinstance(token, str) or not token.strip() or not isinstance(endpoint, str)
            or not endpoint.startswith(('http://', 'https://'))):
        return None
    return b'provider=tushare\0credential=token-and-endpoint-v1\0' + json.dumps(
        [token.strip(), endpoint], ensure_ascii=True, separators=(',', ':'),
    ).encode('utf-8')


SOURCE = SourceDefinition("tushare", manifest, factory, tuple(
    DailyBinding(ExactKey(market, "STOCK", "DAILY_BAR", "none"), "tushare",
                 f"dsa-v3-{market.lower()}-stock-tushare-none-exact-daily-v1", PROTOCOL, PROTOCOL,
                 None, daily, gated=True)
    for market in ("CN", "HK")
) + (DailyBinding(ExactKey('CN', 'ETF', 'DAILY_BAR', 'none'), 'tushare',
    'dsa-v3-etf-tushare-fund-daily-none-adapter-v1', ETF_PROTOCOL, ETF_PROTOCOL,
    None, etf_daily, gated=True, verify_transport=verify_etf_transport),
    QuoteBinding(ExactKey("CN", "STOCK", "REALTIME_QUOTE"), "tushare",
    "tushare-cn-stock-rt-k-quote-v1", "tushare-pro-rt-k-v1", quote),
    QueryBinding(CHIP_CONTRACT, ExactKey('CN', 'STOCK', 'CHIP_SUMMARY'), 'tushare',
        'tushare-stock-chip-v1', 'tushare-cyq-chips-v1', chip, admission_required=False),
    EventBinding(CASH_CONTRACT, ExactKey('CN', 'ETF', 'CASH_DISTRIBUTION'), 'tushare',
        read_cash, cash_revisions, cash_wire, revision_facts=CASH_REVISIONS, current_admission=cash_admission, probe_read=probe_cash)),
    ("data_provider.tushare_fetcher", "TushareFetcher"), credentials=(("token", ("token",)),),
    dependency_modules=("tushare",), factory_scope="provider", require_credential_revision_for_cache=True,
    cache_verified_daily_factory=True, credential_revision_kind='hmac', credential_revision_payload=credential_revision_payload,
    probe_policy=SourceProbePolicy(
        probe_quote, capabilities=('REALTIME_QUOTE',), adapter_required=True),
    account_access_probe=probe_account_access,
    account_access_snapshot=account_snapshot,
    environment_credential_policy=EnvironmentCredentialPolicy(environment_credentials, snapshot_values=environment_snapshot))
