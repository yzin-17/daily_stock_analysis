"""报价准入兼容查找的原错误/范围/同次快照合同。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
from data_provider.thesis_ledger_sources.tencent import SOURCE
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_hithink_quote_admission import hithink_quote_route_key
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from tests.test_thesis_ledger_hithink_quote_runtime import route, _admit


@pytest.mark.parametrize('provided_snapshot', [False, True])
@pytest.mark.parametrize('case', ['current', 'missing', 'empty-scope', 'invalid-scope', 'wrong-adapter', 'expired', 'rotated'])
def test_current_quote_admission_preserves_original_scope_and_snapshot(route, monkeypatch, provided_snapshot, case):
    store, asset, symbol, source = route
    _admit(store, asset, symbol, source)
    key = hithink_quote_route_key(asset)
    target = {'providerId': 'hithink', 'upstreamSource': source}
    admission = store.get_route_admission_v3(key=key, target=target)
    snapshot = store.provider_credential_snapshot('hithink')
    if case == 'missing':
        admission = None
    elif case == 'empty-scope':
        admission = {**admission, 'scopeSymbols': []}
    elif case == 'invalid-scope':
        admission = {**admission, 'scopeSymbols': [symbol, 'BAD']}
    elif case == 'wrong-adapter':
        admission = {**admission, 'adapterRevision': 'obsolete'}
    elif case == 'expired':
        admission = {**admission, 'validUntil': '2000-01-01T00:00:00+00:00'}
    elif case == 'rotated':
        snapshot = replace(snapshot, values={'apiKey': 'synthetic-rotated-key'})
    reads = []

    def read_snapshot(provider):
        reads.append(provider)
        return snapshot
    monkeypatch.setattr(store, 'provider_credential_snapshot', read_snapshot)
    monkeypatch.setattr(store, 'get_route_admission_v3', lambda **kwargs: admission)
    result = ThesisLedgerProviderRuntime(store)._current_market_v3_admission(key, target,
        provider_manifest=PROVIDER_MANIFESTS['hithink'], credential_snapshot=snapshot if provided_snapshot else None)
    assert result == (admission if case == 'current' else None)
    assert reads == ([] if provided_snapshot else ['hithink'])


def test_invalid_reader_declaration_rejected():
    with pytest.raises(ValueError, match='报价准入查找器无效'):
        CapabilityRegistry((replace(SOURCE, quote_admission_reader='arbitrary'),))


def test_new_provider_owns_quote_admission_lookup_without_public_branch(monkeypatch):
    provider = 'isolated-quote-admission'
    binding = next(binding for binding in SOURCE.bindings if binding.key.capability == 'REALTIME_QUOTE')
    key = binding.key.route_key()
    target = {'providerId': provider, 'upstreamSource': binding.upstream_source}
    manifest = {**SOURCE.manifest(), 'providerId': provider}
    snapshot = ProviderCredentialSnapshot.create(provider, 'environment', 'api_key',
        {'apiKey': 'isolated'}, config_version=0, credential_version=0)
    admission = {'admissionState': 'admitted', 'scopeSymbols': ['510300.SH']}
    calls = []

    def read(context):
        calls.append(context)
        return admission

    source = replace(SOURCE, provider_id=provider, manifest=lambda: manifest,
                     bindings=(binding,), quote_admission_reader=read)
    registry = CapabilityRegistry((*CAPABILITY_SOURCES.sources, source))
    monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, '_bindings', registry._bindings)
    store = SimpleNamespace(get_route_admission_v3=lambda **kwargs: None)
    runtime = ThesisLedgerProviderRuntime(store)
    assert runtime._current_market_v3_admission(key, target, provider_manifest=manifest,
                                              credential_snapshot=snapshot) is admission
    context, = calls
    assert context.store is store and context.key == key and context.target == target
    assert context.snapshot is snapshot and context.provider_manifest is manifest
    assert runtime._current_market_v3_admission({**key, 'capability': 'MYSTERY'}, target,
                                              provider_manifest=manifest) is None
    assert runtime._current_market_v3_admission(key, {**target, 'upstreamSource': 'unregistered'},
                                              provider_manifest=manifest) is None
    assert len(calls) == 1
