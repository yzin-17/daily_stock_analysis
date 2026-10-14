"""宽manifest与精确来源登记的既有兼容行为，迁移前逐目标锁定。"""

from copy import deepcopy
from dataclasses import replace

import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_catalog_manifest_v3 import catalog_manifest_matches_v3
from src.services.thesis_ledger_current_data_route import current_data_adapter_revisions
from src.services.thesis_ledger_event_v3_adapters import event_adapter_matches
from src.services.thesis_ledger_control import ControlContractError, normalize_policy_v3


HITHINK_ROUTES = [(key, target, binding) for key, target, binding in CAPABILITY_SOURCES.routes()
                 if target['providerId'] == 'hithink']


@pytest.mark.parametrize('key,target,binding', list(CAPABILITY_SOURCES.routes()))
def test_all_current_exact_routes_preserve_legacy_manifest_match(key, target, binding):
    assert catalog_manifest_matches_v3(CAPABILITY_SOURCES.source(target['providerId']).manifest(), key, target)


@pytest.mark.parametrize('key,target,binding', HITHINK_ROUTES)
@pytest.mark.parametrize('change', ['source', 'market', 'provider', 'capability'])
def test_hithink_manifest_compatibility_by_exact_route(key, target, binding, change):
    manifest = deepcopy(CAPABILITY_SOURCES.source('hithink').manifest())
    if change == 'source':
        manifest['upstreamSources'] = []
    elif change == 'market':
        manifest['markets'] = []
    elif change == 'provider':
        manifest['providerId'] = 'another-provider'
    else:
        for source in manifest['upstreamSources']:
            source['capabilities'] = {}
    # 旧股票价格兼容只查精确bar适配；ETF价格仍要求market/source但不比providerId。
    expected = key['capability'] == 'DAILY_BAR' and (key['assetType'] == 'STOCK' or change == 'provider')
    assert catalog_manifest_matches_v3(manifest, key, target) is expected


def test_hithink_data_target_exemption_is_redundant_for_all_current_bound_routes():
    data_routes = [(key, target) for key, target, _ in HITHINK_ROUTES if key['kind'] == 'data']
    assert len(data_routes) == 3
    for key, target in data_routes:
        # Control原条件：event跳过；其余均已具备current-data修订，不靠providerId豁免。
        assert event_adapter_matches(key, target) or current_data_adapter_revisions(key, target) is not None


def test_exact_quote_never_uses_broad_daily_source_fallback():
    source = CAPABILITY_SOURCES.source('hithink')
    manifest = source.manifest()
    manifest['upstreamSources'].append({'sourceId': 'isolated-unregistered',
        'capabilities': {'DAILY_BAR': ['STOCK'], 'REALTIME_QUOTE': ['STOCK']}})
    key = {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'REALTIME_QUOTE'}
    target = {'providerId': 'hithink', 'upstreamSource': 'isolated-unregistered'}
    assert not catalog_manifest_matches_v3(manifest, key, target)


def test_data_daily_key_is_rejected_before_control_target_eligibility():
    policy = {'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'isolated-key-contract',
        'revision': 1, 'enabled': True, 'routes': [{'key': {'kind': 'data', 'market': 'CN',
        'assetType': 'STOCK', 'capability': 'DAILY_BAR'}, 'targets': [{'providerId': 'hithink',
        'upstreamSource': 'hithink-financial-api'}]}]}
    with pytest.raises(ControlContractError) as caught:
        normalize_policy_v3(policy)
    assert caught.value.code == 'INVALID_POLICY_SCHEMA'


@pytest.mark.parametrize('decision,expected', [(True, True), (False, False), (None, True), ('invalid', False)])
def test_source_catalog_policy_receives_only_current_facts(monkeypatch, decision, expected):
    calls = []
    source = CAPABILITY_SOURCES.source('hithink')
    replacement = replace(source, catalog_manifest_policy=lambda context: calls.append(context) or decision)
    registry = CapabilityRegistry(tuple(replacement if item.provider_id == 'hithink' else item
                                        for item in CAPABILITY_SOURCES.sources))
    monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, '_bindings', registry._bindings)
    key, target, _ = next(route for route in HITHINK_ROUTES
                         if route[0]['capability'] == 'DAILY_BAR' and route[0]['assetType'] == 'STOCK')
    provider = source.manifest()
    assert catalog_manifest_matches_v3(provider, key, target) is expected
    context, = calls
    assert context.provider is provider and context.key is key and context.target is target
    assert context.has_exact_daily_binding is True
    assert not hasattr(context, 'runtime') and not hasattr(context, 'snapshot')


def test_quote_does_not_consume_legacy_source_catalog_policy(monkeypatch):
    source = CAPABILITY_SOURCES.source('hithink')
    replacement = replace(source, catalog_manifest_policy=lambda context: pytest.fail('精确报价不走旧manifest兼容'))
    registry = CapabilityRegistry(tuple(replacement if item.provider_id == 'hithink' else item
                                        for item in CAPABILITY_SOURCES.sources))
    monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, '_bindings', registry._bindings)
    for key, target, _ in HITHINK_ROUTES:
        if key['capability'] == 'REALTIME_QUOTE':
            assert catalog_manifest_matches_v3(source.manifest(), key, target)


def test_non_callable_source_catalog_policy_rejected():
    source = CAPABILITY_SOURCES.source('hithink')
    with pytest.raises(ValueError, match='来源目录兼容策略无效'):
        CapabilityRegistry((replace(source, catalog_manifest_policy=True),))
