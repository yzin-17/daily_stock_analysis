"""新来源声明报价生命周期，仍通过原查询入口和标准结果。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from data_provider.thesis_ledger_sources.tencent import SOURCE
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.provider_manifest import build_provider_manifest
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, QuoteExecutionGuard, QuoteGuardPolicy
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_control import PROVIDER_MANIFESTS, ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import (
    ThesisLedgerDataRequest, ThesisLedgerGatewayError, ThesisLedgerProviderRuntime,
)


@pytest.mark.parametrize('invalid', [object(), QuoteGuardPolicy(None, '拒绝', '变化'),
                                  QuoteGuardPolicy(lambda context: None, '', '变化'),
                                  QuoteGuardPolicy(lambda context: None, '拒绝', None)])
def test_invalid_quote_guard_declaration_rejected(invalid):
    with pytest.raises(ValueError, match='报价守卫策略无效'):
        CapabilityRegistry((replace(SOURCE, quote_guard_policy=invalid),))


def test_guard_result_requires_immutable_snapshot_and_verifier():
    with pytest.raises(CapabilityContractError, match='有效快照或复核器'):
        QuoteExecutionGuard(object(), lambda: True)
    snapshot = ProviderCredentialSnapshot.create('isolated', 'environment', 'api_key',
        {'apiKey': 'isolated'}, config_version=0, credential_version=0)
    with pytest.raises(CapabilityContractError, match='有效快照或复核器'):
        QuoteExecutionGuard(snapshot, None)


@pytest.mark.parametrize('phase', ['success', 'prepare-rejected', 'changed'])
def test_new_provider_guard_uses_original_query_and_snapshot(tmp_path, monkeypatch, phase):
    provider = 'isolated-guarded-quote'
    binding = next(binding for binding in SOURCE.bindings
                   if binding.key.capability == 'REALTIME_QUOTE' and binding.key.asset_type == 'ETF')
    binding = replace(binding, upstream_source=provider)
    target = {'providerId': provider, 'upstreamSource': binding.upstream_source}
    snapshot = ProviderCredentialSnapshot.create(provider, 'environment', 'api_key',
        {'apiKey': 'isolated-snapshot'}, config_version=0, credential_version=0)
    events = []

    def prepare(context):
        events.append('prepare')
        assert context.asset_type == 'ETF' and context.symbol == '510300.SH'
        assert context.upstream_source == target['upstreamSource']
        assert context.expected_policy_revision == 1
        if phase == 'prepare-rejected':
            return None

        def still_current():
            events.append('verify')
            return phase != 'changed'
        return QuoteExecutionGuard(snapshot, still_current)

    def factory(context):
        events.append('factory')
        assert context.snapshot is snapshot
        return object()

    def read(query, context):
        events.append('read')
        return SimpleNamespace(price=2, pre_close=2, open_price=2, high=2, low=2,
                               volume=None, amount=None, timestamp=None)

    def manifest():
        return build_provider_manifest(provider, '隔离守卫来源', {'REALTIME_QUOTE': ('ETF',)},
            upstream_sources=((target['upstreamSource'], '隔离报价'),))

    source = replace(SOURCE, provider_id=provider, manifest=manifest, factory=factory,
        bindings=(replace(binding, read=read),), unscoped_factory=None, quote_source_aliases=(),
        quote_guard_policy=QuoteGuardPolicy(prepare, '隔离来源拒绝', '隔离来源变化'))
    registry = CapabilityRegistry((*CAPABILITY_SOURCES.sources, source))
    monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, '_bindings', registry._bindings)
    monkeypatch.setitem(PROVIDER_MANIFESTS, provider, manifest())
    store = ThesisLedgerControlStore(str(tmp_path / 'guard.sqlite'))
    store.apply_policy_v3({'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'guard-test',
        'revision': 1, 'enabled': True, 'routes': [{'key': binding.key.route_key(), 'targets': [target]}]})
    original_claim = store.claim_provider_request_budget

    def claim(*args, **kwargs):
        events.append('budget')
        return original_claim(*args, **kwargs)
    monkeypatch.setattr(store, 'claim_provider_request_budget', claim)
    request = ThesisLedgerDataRequest('REALTIME_QUOTE', '510300.SH', instrument_type='ETF')
    runtime = ThesisLedgerProviderRuntime(store)
    if phase == 'success':
        result = runtime.execute_request(request)
        assert result.provider == provider and result.value.price == 2 and not result.fallback_used
    else:
        with pytest.raises(ThesisLedgerGatewayError) as error:
            runtime.execute_request(request)
        assert error.value.code == 'not_admitted'
        assert str(error.value) == ('隔离来源拒绝' if phase == 'prepare-rejected' else '隔离来源变化')
    assert events == (['prepare'] if phase == 'prepare-rejected'
                      else ['prepare', 'budget', 'factory', 'read', 'verify'])
