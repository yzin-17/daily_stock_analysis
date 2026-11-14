"""本地资料统一入口；旧资料与精确身份仍由原来源拥有。"""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

from data_provider.thesis_ledger_sources.official_rules import SOURCE as FEES
from data_provider.thesis_ledger_sources.official_hk_rules import SOURCE as HK
from data_provider.thesis_ledger_sources.official_us_rules import SOURCE as US
from data_provider.thesis_ledger_sources.official_states import SOURCE as STATES
from data_provider.thesis_ledger_sources.official_dividends import SOURCE as DIVIDENDS
from src.services.thesis_ledger_capability_registry import QueryBinding, EventBinding, SourceDefinition
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, CatalogReadinessContext
from src.services.thesis_ledger_local_material_selection import select_material

ORIGINS = (FEES, HK, US, STATES, DIVIDENDS)
PROVIDER = 'local-materials'
UPSTREAM = 'local-materials'


def origin_candidates(key):
    return [(source, binding) for source in ORIGINS for binding in source.bindings
            if binding.key.route_key() == dict(key)]


def manifest():
    # 文件清单仍从旧身份的 localHistory 只读查看，避免把离散范围声明为连续覆盖。
    value = deepcopy(FEES.manifest())
    capabilities = {}
    for source in ORIGINS:
        for capability, types in source.manifest()['capabilities'].items():
            capabilities[capability] = sorted(set(capabilities.get(capability, []) + types))
    value.update(providerId=PROVIDER, displayName='本地资料', capabilities=capabilities,
                 markets=sorted({market for source in ORIGINS for market in source.manifest().get('markets', [])}),
                 sourceKind='local_history', upstreamSources=[{
                     'sourceId': UPSTREAM, 'displayName': '本地资料', 'capabilities': capabilities}])
    return value


def factory(context):
    return object()


def catalog_readiness(context):
    candidates = origin_candidates(context.key)
    # 需准入的资料不得借统一入口获得文件存在即就绪的资格。
    if any(binding.admission_required is True for _, binding in candidates):
        return None
    checks = []
    for source, binding in candidates:
        if source.catalog_readiness:
            target = {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source}
            checks.append(source.catalog_readiness(CatalogReadinessContext(context.key, target)))
    if any(value is True for value in checks):
        return True
    return False if checks and all(value is False for value in checks) else None


def read(query, context):
    candidates = origin_candidates(query.routeKey.model_dump())
    material = None
    try:
        source, binding = select_material(candidates, query)
    except CapabilityContractError as error:
        if error.code != 'insufficient_coverage' or query.routeKey.capability != 'HISTORICAL_PUBLIC_FEES':
            raise
        if not context.database_path:
            raise
        from src.services.local_rule_demand_refresh import refresh_selection
        source, binding, material = refresh_selection(candidates, query, context)
    if binding.admission_required is True:
        raise CapabilityContractError('not_admitted', '本地公告资料须沿用原来源准入，尚未准入不能读取')
    target = {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source,
              'routeIndex': query.routeTarget.routeIndex}
    legacy = binding.contract.request_schema.model_validate({**query.model_dump(), 'routeTarget': target})
    binding.contract.validate_binding(legacy, legacy.routeKey.model_dump(), target)
    if binding.validate_parameters:
        binding.validate_parameters(legacy)
    adapter = source.factory(context)
    if material is not None:
        from src.services.local_rule_demand_refresh import material_adapter
        adapter = material_adapter(source, material)
    result = binding.read(legacy, replace(context, adapter=adapter))
    validated = binding.contract.correlate_result(legacy, binding.contract.validate_result(result), target)
    value = validated.model_dump(mode='json', exclude_unset=True)
    value['routeTarget'] = query.routeTarget.model_dump()
    provenance = value.get('provenance', {})
    if 'providerId' in provenance:
        provenance.update(providerId=PROVIDER, upstreamSource=UPSTREAM,
                          providerRevision=f'{source.provider_id}:{binding.upstream_source}:' + provenance['providerRevision'])
    return binding.contract.validate_result(value)


def current_cash_admission(store, key, target, manifest):
    from src.services.thesis_ledger_market_v3_revisions import _market_v3_admission_matches_current
    original = DIVIDENDS.bindings[0]
    identity = {'providerId': DIVIDENDS.provider_id, 'upstreamSource': original.upstream_source}
    admission = store.get_route_admission_v3(key=dict(key), target=identity)
    if not isinstance(admission, dict) or not _market_v3_admission_matches_current(
            admission, key, identity, DIVIDENDS.manifest()):
        return None
    return {**deepcopy(admission), 'target': {'providerId': PROVIDER, 'upstreamSource': UPSTREAM}}


def read_cash(query, context):
    from src.services.thesis_ledger_capability_contracts import ReadContext
    original = DIVIDENDS.bindings[0]
    # 外层已经通过原资料的当前准入；原字节读取器会再次检查修订及完整覆盖。
    delegated = replace(context, adapter_factory=lambda: DIVIDENDS.factory(ReadContext(None)))
    value = original.read(query, delegated)
    value['facts'] = [{**fact, 'provider': PROVIDER} for fact in value['facts']]
    return original.contract.validate_result(value)


def bindings():
    grouped = {}
    for source in ORIGINS:
        for binding in source.bindings:
            grouped.setdefault(binding.key, []).append(binding)
    result = []
    for key, originals in grouped.items():
        original = originals[0]
        digest = sha256(json.dumps([(item.adapter_revision, item.source_revision) for item in originals]).encode()).hexdigest()
        if isinstance(original, QueryBinding):
            support = original.query_support
            if support is not None and hasattr(support, 'phases'):
                support = type(support).model_validate({**support.model_dump(),
                    'phases': sorted({phase for item in originals for phase in item.query_support.phases})})
            result.append(replace(original, upstream_source=UPSTREAM, read=read,
                                  source_revision='local-materials:' + digest,
                                  query_support=support,
                                  admission_required=any(item.admission_required is True for item in originals)))
        elif isinstance(original, EventBinding):
            # 原事件入口和冻结版本保留；新入口保持同样的独立准入门禁。
            result.append(replace(original, upstream_source=UPSTREAM, read=read_cash,
                                  current_admission=current_cash_admission))
    return tuple(result)


SOURCE = SourceDefinition(PROVIDER, manifest, factory, bindings(), catalog_readiness=catalog_readiness)
