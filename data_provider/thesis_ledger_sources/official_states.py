"""公告事实的独立状态、风险、价格规则与阶段投影；沿用原只读证据获取。"""

from datetime import date, timedelta, timezone
from hashlib import sha256
import json

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_status_capabilities_v1 import (
    TRADING_CONTRACT, RISK_CONTRACT, PRICE_CONTRACT, PHASE_CONTRACT,
    TradingResult, RiskResult, PriceResult, PhaseResult, TradingFact, RiskFact, PriceFact, PhaseFact, satisfies,
)
from src.services.thesis_ledger_trading_status import build_trading_status_manifest, OfficialTradingStatusProvider, TradingStatusSourceError
from src.services.thesis_ledger_trading_status import DATA_PATH, RISK_DATA_PATH, RISK_UPSTREAM_SOURCE, LIFECYCLE_PATHS
from src.services.thesis_ledger_status_query_support_v1 import TradingSupport, RiskSupport, PriceSupport, PhaseSupport
from src.services.thesis_ledger_legacy_status_contract_v1 import CONTRACT as LEGACY_CONTRACT
from data_provider.legacy_status_projection import read_status as read_legacy_status

SUSPENSION = "cn-stock-daily-suspension"
RISK = "cn-stock-risk-price-limits"
INITIAL = "cn-stock-initial-listing"
DELISTING = "cn-stock-delisting-period"


def manifest():
    value = build_trading_status_manifest()
    for binding in BINDINGS:
        value["capabilities"][binding.key.capability] = ["STOCK"]
        source = next(item for item in value["upstreamSources"] if item["sourceId"] == binding.upstream_source)
        source["capabilities"][binding.key.capability] = ["STOCK"]
    return value


def factory(context):
    return OfficialTradingStatusProvider()


def catalog_readiness(context):
    from src.services.thesis_ledger_trading_status import trading_status_route_matches, trading_status_source_ready
    if trading_status_route_matches(context.key, context.target):
        return trading_status_source_ready(context.target)
    return None


def exact_probe(context):
    binding = next((item for item in BINDINGS if item.key.route_key() == dict(context.key)
                    and item.upstream_source == context.target['upstreamSource']), None)
    if binding is not None and context.policy_revision is None:
        raise CapabilityContractError('control_rejected', '独立功能检查缺少有效实际政策修订')
    path = RISK_DATA_PATH if context.target['upstreamSource'] == RISK_UPSTREAM_SOURCE else DATA_PATH
    path = LIFECYCLE_PATHS.get(context.target['upstreamSource'], path)
    before = path.read_bytes()
    pack = json.loads(before)
    symbol = pack.get('symbol', '600519.SH')
    start, end = pack['coverage']['start'], pack['coverage']['end']
    request = {'contractVersion': 3, 'consumer': 'thesis-ledger',
        'requestId': 'source-check', 'symbol': symbol, 'range': {'start': start, 'end': end},
        'routeKey': dict(context.key), 'routeTarget': {**context.target, 'routeIndex': 0}}
    adapter = OfficialTradingStatusProvider(path)
    if binding is None:
        adapter.read(request)
    else:
        read_probe_binding(binding, request, context.policy_revision, adapter)
    if path.read_bytes() != before:
        raise CapabilityContractError('admission_invalid', '检查期间官方事实包发生变化')
    return {'symbol': symbol, 'start': start, 'end': end}


def read_probe_binding(binding, request, policy_revision, adapter):
    request = {**request, 'capabilityVersion': 1, 'policyRevision': policy_revision}
    if binding.key.capability == 'RISK_WARNING':
        request['detail'] = {'kind': 'riskCategories', 'categories': binding.query_support.riskCategories}
    elif binding.key.capability == 'DAILY_PRICE_RULE':
        request['representation'] = 'rule'
    contract = binding.contract
    query = contract.request_schema.model_validate(request)
    contract.validate_binding(query, request['routeKey'], request['routeTarget'])
    binding.validate_parameters(query)
    result = binding.read(query, ReadContext(adapter, probe=True))
    contract.correlate_result(query, contract.validate_result(result), request['routeTarget'])


def legacy_request(query):
    # 仅适配来源读取 DTO；控制与执行始终使用请求中的独立能力 key。
    return {"contractVersion": 3, "consumer": query.consumer, "requestId": query.requestId, "symbol": query.symbol,
            "routeKey": {**query.routeKey.model_dump(), "capability": "HISTORICAL_TRADING_STATUS"},
            "routeTarget": query.routeTarget.model_dump(), "range": query.range.model_dump()}


def source_read(query, context):
    try:
        return context.adapter.read(legacy_request(query))
    except TradingStatusSourceError as error:
        raise CapabilityContractError(error.code, str(error)) from None


def validate_source_request(query):
    if (date.fromisoformat(query.range.end) - date.fromisoformat(query.range.start)).days > 3660:
        raise CapabilityContractError("invalid_request", "公告状态请求窗口超过 3661 日")
    if hasattr(query, "detail") and query.detail.kind != "riskCategories":
        raise CapabilityContractError("unsupported_capability", "公告细分风险不提供粗 ST 字段")
    if hasattr(query, "representation") and query.representation != "rule":
        raise CapabilityContractError("unsupported_capability", "公告比例规则不能提供精确涨跌停价格")


def document_evidence(item):
    digest = item.get("sha256")
    return {"id": item["id"], "kind": "document", "sha256": digest,
            "hashScope": "originalBytes" if digest is not None else "unknown",
            "reference": item["url"], "observedAt": item.get("observedAt"), "availableAt": None}


def result(query, context, model, fact_model, project):
    from src.services.thesis_ledger_market_v3_facts import market_calendar_evidence_v3
    raw = source_read(query, context)
    binding = next(item for item in BINDINGS if item.key.route_key() == query.routeKey.model_dump()
                   and item.upstream_source == query.routeTarget.upstreamSource)
    observed = context.adapter.clock().astimezone(timezone.utc).isoformat()
    calendar = market_calendar_evidence_v3(query.routeKey.market, query.range.start, query.range.end)
    days = [project(item, query) for item in raw["days"]]
    value = {**query.model_dump(), "provenance": {"adapterRevision": binding.adapter_revision, "sourceRevision": binding.source_revision},
             "clock": {"observedAt": observed, "upstreamAt": None, "timezone": "Asia/Shanghai", "basis": "effectiveDate"},
             "evidence": [document_evidence(item) for item in raw["sourceDocuments"]], "days": days}
    first, last = date.fromisoformat(query.range.start), date.fromisoformat(query.range.end)
    all_dates = [(first + timedelta(days=index)).isoformat() for index in range((last - first).days + 1)]
    expected = calendar["expectedSessionDates"] if calendar is not None else []
    by_date = {item["date"]: fact_model.model_validate(item) for item in days}
    unknown = [day for day in expected if not satisfies(by_date[day], query)]
    calendar_refs = []
    if calendar is not None:
        digest = sha256(json.dumps(calendar, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        identifier = "calendar:" + digest
        calendar_refs.append(identifier)
        value["evidence"].append({"id": identifier, "kind": "calendar", "sha256": digest, "hashScope": "normalizedResponse",
                                  "reference": None, "observedAt": observed, "availableAt": None})
    value["coverage"] = {"range": query.range.model_dump(), "expectedDates": expected,
                         "calendarUnknownDates": all_dates if calendar is None else [], "unknownDates": unknown,
                         "calendarEvidenceRefs": calendar_refs, "complete": calendar is not None and not unknown}
    return model.model_validate(value)


def trading(query, context):
    def project(item, query):
        return {"date": item["date"], "state": item["state"], "intradayHalts": [],
                "evidenceRefs": item.get("sourceDocumentIds", []) if item["state"] != "unknown" else []}
    return result(query, context, TradingResult, TradingFact, project)


def risk(query, context):
    def project(item, query):
        facts = item.get("riskWarning", {})
        return {"date": item["date"], "coarseST": None, "delisting": facts.get("delisting", "unknown"),
                "other": facts.get("other", "unknown"), "evidenceRefs": item.get("sourceDocumentIds", []) if facts else []}
    return result(query, context, RiskResult, RiskFact, project)


def price(query, context):
    def project(item, query):
        rule = {"kind": "unknown"}
        if item.get("dailyPriceLimit") is not None:
            rule = {"kind": "ratio", **item["dailyPriceLimit"]}
        elif item.get("noDailyPriceLimit") is not None:
            rule = {"kind": "unlimited", **item["noDailyPriceLimit"]}
        return {"date": item["date"], "rule": rule, "evidenceRefs": item.get("sourceDocumentIds", []) if rule["kind"] != "unknown" else []}
    return result(query, context, PriceResult, PriceFact, project)


def phase(query, context):
    def project(item, query):
        return {"date": item["date"], "phase": item.get("listingPhase", "unknown"),
                "evidenceRefs": item.get("sourceDocumentIds", []) if item.get("listingPhase") is not None else []}
    return result(query, context, PhaseResult, PhaseFact, project)


BINDINGS = (
    QueryBinding(TRADING_CONTRACT, ExactKey("CN", "STOCK", "DAILY_TRADING_STATUS"), SUSPENSION,
                 "official-trading-status-adapter-v1", "cn-000995-daily-suspension-2023-08-v1", trading, gated=False,
                 validate_parameters=validate_source_request, query_support=TradingSupport(maximumWindowDays=3661)),
    QueryBinding(RISK_CONTRACT, ExactKey("CN", "STOCK", "RISK_WARNING"), RISK,
                 "official-risk-price-limits-adapter-v1", "cn-000995-risk-limits-2023-08-v1", risk, gated=False,
                 validate_parameters=validate_source_request, query_support=RiskSupport(detailKinds=["riskCategories"], riskCategories=["delisting", "other"])),
    QueryBinding(PRICE_CONTRACT, ExactKey("CN", "STOCK", "DAILY_PRICE_RULE"), RISK,
                 "official-risk-price-limits-adapter-v1", "cn-000995-risk-limits-2023-08-v1", price, gated=False,
                 validate_parameters=validate_source_request, query_support=PriceSupport(representations=["rule"])),
    *(QueryBinding(contract, ExactKey("CN", "STOCK", capability), source,
                   "official-listing-lifecycle-adapter-v1", revision, handler, gated=False, validate_parameters=validate_source_request,
                   query_support=PriceSupport(representations=["rule"]) if capability == "DAILY_PRICE_RULE" else PhaseSupport(phases=phases))
      for source, revision, phases in ((INITIAL, "cn-603350-initial-listing-2024-07-v1", ["notListed", "initialListing", "listed"]),
                                      (DELISTING, "cn-600462-delisting-2025-07-v1", ["delistingPeriod", "delisted"]))
      for capability, contract, handler in (("DAILY_PRICE_RULE", PRICE_CONTRACT, price), ("LISTING_PHASE", PHASE_CONTRACT, phase))),
)
LEGACY_BINDINGS = tuple(QueryBinding(LEGACY_CONTRACT, ExactKey('CN','STOCK','HISTORICAL_TRADING_STATUS'), source,
    revision, source_revision, read_legacy_status, admission_required=False)
    for source, revision, source_revision in (
        (SUSPENSION, 'official-trading-status-adapter-v1', 'cn-000995-daily-suspension-2023-08-v1'),
        (RISK, 'official-risk-price-limits-adapter-v1', 'cn-000995-risk-limits-2023-08-v1'),
        (INITIAL, 'official-listing-lifecycle-adapter-v1', 'cn-603350-initial-listing-2024-07-v1'),
        (DELISTING, 'official-listing-lifecycle-adapter-v1', 'cn-600462-delisting-2025-07-v1')))
SOURCE = SourceDefinition("official-states", manifest, factory, BINDINGS + LEGACY_BINDINGS,
                          ("src.services.thesis_ledger_trading_status", "OfficialTradingStatusProvider"), exact_probe_read=exact_probe, catalog_readiness=catalog_readiness)
