"""HiThink 精确日线、快照报价及现金事件绑定。"""

from types import SimpleNamespace
from datetime import datetime, timezone
from collections.abc import Mapping
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_capability_contracts import QuoteAdmissionContext, QuoteGuardContext, QuoteExecutionGuard, QuoteGuardPolicy
from src.services.thesis_ledger_capability_contracts import SourceProbeContext, SourceProbePolicy, EnvironmentCredentialPolicy
from data_provider.hithink_etf_capability import (
    HITHINK_ETF_HISTORY_SOURCE, HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
    HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1, SOURCE_CONTRACT,
    project_public_units, plan_public_windows,
)

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyRead, QuoteQuery, QuoteValue, ReadContext
from src.services.thesis_ledger_capability_registry import DailyBinding, QuoteBinding, EventBinding, ExactKey, SourceDefinition
from data_provider.hithink_cash_capability import CONTRACT as CASH_CONTRACT, read_cash, revisions as cash_revisions, wire_project as cash_wire, REVISION_FACTS as CASH_REVISIONS, current_admission as cash_admission, HITHINK_DIVIDEND_SOURCE
from src.services.thesis_ledger_hithink_quote import HITHINK_ETF_SNAPSHOT_SOURCE, HITHINK_STOCK_SNAPSHOT_SOURCE
from src.services.thesis_ledger_quote_units_contract import QuotePublicUnits, QuotePublicUnitsError
from src.services.thesis_ledger_daily_units_contract import DailyUnitsPolicy

STOCK_SOURCE = "hithink-financial-api"
STOCK_SOURCE_REVISION = "dsa-hithink-stock-request-contract-v1"
STOCK_PROTOCOL = "hithink-stock-single-response-local-v1"


def quote_public_units(context):
    expected = {
        HITHINK_STOCK_SNAPSHOT_SOURCE: {'price_currency': 'CNY', 'volume': 'share', 'turnover_currency': 'CNY'},
        HITHINK_ETF_SNAPSHOT_SOURCE: {'price_currency': 'CNY', 'volume': 'unknown', 'turnover_currency': 'unknown'},
    }.get(context.upstream_source)
    units = getattr(context.value, 'units', None)
    if expected is None or not isinstance(units, Mapping) or dict(units) != expected:
        raise QuotePublicUnitsError('HiThink 报价单位合同无效')
    return QuotePublicUnits(priceCurrency=expected['price_currency'], volume=expected['volume'],
                            turnoverCurrency=expected['turnover_currency'])


def probe_cash(context):
    from src.services.thesis_ledger_hithink_mapped_dividend_read import read_hithink_mapped_fund_dividends
    read_hithink_mapped_fund_dividends(context.symbol, **context.mapped_read_options())


def research_calendar_eligible(context):
    from src.services.thesis_ledger_dependency_facts import DependencyFactError, validate_cn_stock
    try:
        validate_cn_stock(context.symbol, context.market, context.asset_type, lambda symbol: symbol)
    except DependencyFactError:
        return False
    return True


def policy_admission_guard(context):
    from src.services.thesis_ledger_control import ControlContractError, _provider_credential_revision_from_snapshot
    from src.services.thesis_ledger_hithink_quote_admission import hithink_quote_revisions
    from src.services.thesis_ledger_market_v3_revisions import _market_v3_admission_matches_current
    if context.state != 'admitted':
        return False
    try:
        snapshot = context.read_credentials()
    except (ControlContractError, ValueError):
        return False
    revision = _provider_credential_revision_from_snapshot(snapshot)
    key, target, payload = context.key, context.target, context.payload
    if key.get('kind') == 'data' and key['capability'] == 'REALTIME_QUOTE':
        versions = hithink_quote_revisions(key['assetType'], target['upstreamSource'])
        return (revision is not None and versions is not None
                and payload.get('adapterRevision') == versions['adapterRevision']
                and payload.get('sourceRevision') == versions['sourceRevision']
                and payload.get('credentialRevision') == revision)
    return revision is not None and _market_v3_admission_matches_current(
        {**payload, 'admissionState': context.state}, key, target, context.manifest,
        credential_revision=revision,
    )


def environment_credentials():
    import os
    api_key = os.environ.get('HITHINK_API_KEY', '').strip()
    return {'apiKey': api_key} if api_key else {}


def catalog_manifest_policy(context):
    from src.services.thesis_ledger_hithink_dividend_contract_v3 import hithink_dividend_route_matches
    provider, key, target = context.provider, context.key, context.target
    if hithink_dividend_route_matches(key, target):
        markets, sources = provider.get('markets'), provider.get('upstreamSources')
        if (provider.get('providerId') != 'hithink' or not isinstance(markets, list)
                or 'CN' not in markets or not isinstance(sources, list)):
            return False
        return any(isinstance(source, Mapping) and source.get('sourceId') == target.get('upstreamSource')
            and isinstance(source.get('capabilities'), Mapping)
            and source['capabilities'].get('CASH_DISTRIBUTION') == ['ETF'] for source in sources)
    if (provider.get('providerId') == 'hithink' and key.get('assetType') == 'STOCK'
            and target.get('providerId') == 'hithink' and target.get('upstreamSource') == STOCK_SOURCE
            and context.has_exact_daily_binding):
        return True
    return None


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    from src.services.thesis_ledger_hithink_dividend_contract_v3 import HITHINK_DIVIDEND_SOURCE
    from src.services.thesis_ledger_hithink_quote import HITHINK_ETF_SNAPSHOT_SOURCE, HITHINK_STOCK_SNAPSHOT_SOURCE

    etf_source = "fund-market-historical"
    value = build_provider_manifest("hithink", "HiThink", {
        "REALTIME_QUOTE": ("ETF", "STOCK"), "DAILY_BAR": ("ETF", "STOCK"), "CASH_DISTRIBUTION": ("ETF",),
    }, requires_credential=True, upstream_sources=(
        (etf_source, "HiThink ETF 历史接口"), (STOCK_SOURCE, "HiThink 股票历史接口"),
        (HITHINK_STOCK_SNAPSHOT_SOURCE, "HiThink A股行情快照"),
        (HITHINK_ETF_SNAPSHOT_SOURCE, "HiThink ETF/LOF 场内快照"),
        (HITHINK_DIVIDEND_SOURCE, "HiThink 场内基金分红"),
    ), markets=("CN",), configuration_mode="dsa_environment")
    capabilities = {
        etf_source: {"DAILY_BAR": ["ETF"]}, STOCK_SOURCE: {"DAILY_BAR": ["STOCK"]},
        HITHINK_STOCK_SNAPSHOT_SOURCE: {"REALTIME_QUOTE": ["STOCK"]},
        HITHINK_ETF_SNAPSHOT_SOURCE: {"REALTIME_QUOTE": ["ETF"]},
        HITHINK_DIVIDEND_SOURCE: {"CASH_DISTRIBUTION": ["ETF"]},
    }
    for source in value["upstreamSources"]:
        source["capabilities"] = capabilities[source["sourceId"]]
    value["credentialEnvironmentKey"] = "HITHINK_API_KEY"
    return value


def factory(context):
    from src.services.thesis_ledger_hithink_stock_read import read_hithink_stock_bars
    from data_provider.hithink_etf_capability import read_full_etf
    def read_bars(query):
        if query.instrument_type == 'ETF':
            return read_full_etf(query, context.snapshot, context.timeout_seconds)
        return read_hithink_stock_bars(query, context.snapshot, context.timeout_seconds)
    return SimpleNamespace(
        get_market_bars_v3=read_bars,
        fetch_quote=lambda symbol, asset: snapshot_quote(context, symbol, asset),
    )


def snapshot_quote(context, symbol, asset):
    from src.services.thesis_ledger_hithink_quote import HiThinkQuoteError, HiThinkSnapshotAdapter
    if context.snapshot is None:
        raise HiThinkQuoteError("not_configured", "HiThink 报价缺少凭据快照")
    return HiThinkSnapshotAdapter(api_key=context.snapshot.values.get("apiKey"), timeout_seconds=4.5).fetch_quote(symbol, asset)


def quote(query: QuoteQuery, context: ReadContext) -> QuoteValue:
    from collections.abc import Mapping
    from src.services.thesis_ledger_hithink_quote import HiThinkQuoteError
    try:
        value = context.adapter.fetch_quote(query.symbol, query.instrument_type)
    except HiThinkQuoteError as error:
        raise CapabilityContractError(error.code, str(error), retryable=error.retryable) from None
    if not isinstance(value, Mapping):
        return value
    return SimpleNamespace(**{field: value.get(field) for field in (
        "price", "open_price", "high", "low", "pre_close", "volume", "amount", "change_amount", "change_pct",
        "fetched_at", "provider_timestamp", "source", "units",
    )}, is_stale=value.get("is_stale", False))


def daily(query, context):
    if query.parameters.interactive_chart:
        from data_provider.hithink_interactive_daily import read_interactive_hithink_daily
        frame = read_interactive_hithink_daily(query, context.snapshot)
    else:
        frame = context.adapter.get_market_bars_v3(query)
    return DailyRead(frame, 1)


def read_probe(context: SourceProbeContext) -> None:
    from src.services.thesis_ledger_hithink_probe import probe_hithink_capability
    probe_hithink_capability(context.capability, context.snapshot, error_type=CapabilityContractError)


def read_quote_admission(context: QuoteAdmissionContext) -> Mapping | None:
    from src.services.thesis_ledger_control import ControlContractError, _provider_credential_revision_from_snapshot
    from src.services.thesis_ledger_hithink_quote_admission import (
        hithink_quote_admission_matches_current, hithink_quote_revisions,
    )
    key, target = context.key, context.target
    if hithink_quote_revisions(key.get('assetType'), target.get('upstreamSource')) is None:
        return None
    try:
        snapshot = context.snapshot or context.store.provider_credential_snapshot('hithink')
    except (AttributeError, ControlContractError, ValueError):
        return None
    credential_revision = _provider_credential_revision_from_snapshot(snapshot)
    admission = context.store.get_route_admission_v3(key=dict(key), target=dict(target))
    if not isinstance(admission, Mapping):
        return None
    symbols = admission.get('scopeSymbols')
    if not isinstance(symbols, list) or not symbols:
        return None
    now = datetime.now(timezone.utc)
    if all(hithink_quote_admission_matches_current(
        admission, asset_type=key['assetType'], source=target['upstreamSource'], symbol=symbol,
        credential_revision=credential_revision, now=now,
    ) for symbol in symbols):
        return admission
    return None


def prepare_quote_guard(context: QuoteGuardContext) -> QuoteExecutionGuard | None:
    from src.services.thesis_ledger_hithink_quote_runtime import (
        current_hithink_quote_guard, hithink_quote_guard_still_current,
    )
    guard = current_hithink_quote_guard(context.store, asset_type=context.asset_type,
        source=context.upstream_source, symbol=context.symbol,
        expected_policy_revision=context.expected_policy_revision)
    if guard is None:
        return None
    return QuoteExecutionGuard(guard.snapshot, lambda: hithink_quote_guard_still_current(
        context.store, guard, asset_type=context.asset_type,
        source=context.upstream_source, symbol=context.symbol))


def credential_revision_payload(snapshot: ProviderCredentialSnapshot) -> bytes | None:
    if snapshot.source not in {'environment', 'control'} or snapshot.method != 'api_key':
        return None
    secret = snapshot.values.get('apiKey')
    if not isinstance(secret, str) or not secret.strip():
        return None
    payload = b'provider=hithink\0credential=api_key\0' + secret.strip().encode('utf-8')
    if snapshot.source == 'control':
        payload = b'source=control\0' + payload
    return payload


SOURCE = SourceDefinition("hithink", manifest, factory, tuple(
    DailyBinding(ExactKey("CN", "STOCK", "DAILY_BAR", adjustment), STOCK_SOURCE,
                 f"dsa-v3-stock-hithink-financial-api-{adjustment}-adapter-v1",
                 STOCK_SOURCE_REVISION, STOCK_PROTOCOL, None, daily, gated=True,
                 research_calendar_eligible=research_calendar_eligible if adjustment in {'none', 'qfq'} else None)
    for adjustment in ("none", "qfq", "hfq")
) + (
    DailyBinding(ExactKey('CN', 'ETF', 'DAILY_BAR', 'qfq'), HITHINK_ETF_HISTORY_SOURCE,
        'dsa-v3-etf-hithink-fund-market-historical-qfq-adapter-v3',
        HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1, HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1,
        None, daily, gated=True, source_contract=SOURCE_CONTRACT,
        public_units_policy=DailyUnitsPolicy(('hithink_etf_field_units',), project_public_units),
        window_planner=plan_public_windows),
    QuoteBinding(ExactKey("CN", "STOCK", "REALTIME_QUOTE"), HITHINK_STOCK_SNAPSHOT_SOURCE,
        "dsa-hithink-stock-quote-adapter-v1", "dsa-hithink-stock-snapshot-request-v1", quote),
    QuoteBinding(ExactKey("CN", "ETF", "REALTIME_QUOTE"), HITHINK_ETF_SNAPSHOT_SOURCE,
        "dsa-hithink-etf-quote-adapter-v1", "dsa-hithink-etf-snapshot-request-v1", quote),
    EventBinding(CASH_CONTRACT, ExactKey('CN', 'ETF', 'CASH_DISTRIBUTION'), HITHINK_DIVIDEND_SOURCE,
        read_cash, cash_revisions, cash_wire, revision_facts=CASH_REVISIONS, current_admission=cash_admission, probe_read=probe_cash),
), credentials=(("api_key", ("apiKey",)),), dependency_modules=("requests",), unscoped_factory=factory,
    credential_revision_kind='hmac', credential_revision_payload=credential_revision_payload,
    quote_guard_policy=QuoteGuardPolicy(prepare_quote_guard, 'HiThink 报价缺少当前精确准入', 'HiThink 报价准入已变更'),
    quote_admission_reader=read_quote_admission,
    quote_public_units=quote_public_units,
    quote_source_aliases=(('hithink/a-share-prices-snapshot', HITHINK_STOCK_SNAPSHOT_SOURCE),
                          ('hithink/fund-market-snapshot', HITHINK_ETF_SNAPSHOT_SOURCE)),
    policy_admission_guard=policy_admission_guard,
    probe_policy=SourceProbePolicy(read_probe, (('CASH_DISTRIBUTION', 'ETF'),)),
    catalog_manifest_policy=catalog_manifest_policy,
    environment_credential_policy=EnvironmentCredentialPolicy(environment_credentials))
