"""来源声明驱动凭据修订；隔离登记不增加公共供应商分支。"""

from dataclasses import replace
from types import SimpleNamespace
import hashlib
import hmac
import json

import pytest

from data_provider.thesis_ledger_sources.tencent import SOURCE
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.provider_credential_revision import provider_credential_revision
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot
from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime


@pytest.fixture
def registered_source(monkeypatch):
    provider = 'isolated-credential-source'
    binding = next(binding for binding in SOURCE.bindings
                   if binding.key.capability == 'DAILY_BAR' and binding.key.adjustment == 'none')
    source = replace(SOURCE, provider_id=provider, bindings=(binding,), credential_revision_kind='hmac', quote_source_aliases=(),
                     credential_revision_payload=lambda snapshot: snapshot.values['apiKey'].encode('utf-8'))
    registry = CapabilityRegistry((*CAPABILITY_SOURCES.sources, source))
    monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, '_bindings', registry._bindings)
    return source, binding.key.route_key(), {'providerId': provider, 'upstreamSource': binding.upstream_source}


def test_credential_revision_strategy_is_explicit_and_validated():
    assert SOURCE.credential_revision_kind == 'version'
    for provider in ('hithink', 'tushare'):
        assert CAPABILITY_SOURCES.source(provider).credential_revision_kind == 'hmac'
    for invalid in ('unknown', None, True):
        with pytest.raises(ValueError, match='凭据准入修订策略无效'):
            CapabilityRegistry((replace(SOURCE, credential_revision_kind=invalid),))
    with pytest.raises(ValueError, match='载荷构造器无效'):
        CapabilityRegistry((replace(SOURCE, credential_revision_payload='arbitrary'),))


@pytest.mark.parametrize('revision', [None, '', '0', 'hmac-sha256-v1:' + 'a' * 63,
                                    'hmac-sha256-v1:' + 'A' * 64, 'hmac-sha256-v1:' + 'g' * 64])
def test_new_source_rejects_invalid_hmac_without_version_fallback(registered_source, revision):
    source, key, target = registered_source
    manifest = {'providerId': source.provider_id, 'requiresCredential': True}
    assert market_v3_current_route_revisions(key, target, manifest,
                                            credential_version=7, credential_revision=revision) is None


def test_new_source_uses_hmac_and_detects_credential_change(registered_source, monkeypatch):
    source, key, target = registered_source
    monkeypatch.setenv('THESIS_LEDGER_DSA_SECRET_KEY', 'isolated-master-key')
    monkeypatch.setenv('THESIS_LEDGER_DSA_SECRET_KEY_VERSION', 'test-v1')
    snapshot = ProviderCredentialSnapshot.create(source.provider_id, 'environment', 'api_key',
                                                 {'apiKey': 'isolated-key'}, config_version=0, credential_version=0)
    revision = _provider_credential_revision_from_snapshot(snapshot)
    manifest = {'providerId': source.provider_id, 'requiresCredential': True, 'credentialVersion': 0}
    revisions = market_v3_current_route_revisions(key, target, manifest, credential_revision=revision)
    assert revisions['credentialRevision'] == revision
    admission = {'consumer': 'thesis-ledger', 'admissionState': 'admitted',
                 'scopeSymbols': ['510300.SH'], 'scopeDateFrom': '2026-09-29',
                 'scopeDateTo': '2026-09-30', **revisions}
    snapshots = []

    def read_snapshot(provider):
        snapshots.append(provider)
        return snapshot

    store = SimpleNamespace(provider_credential_snapshot=read_snapshot,
                            get_route_admission_v3=lambda **kwargs: admission)
    runtime = ThesisLedgerProviderRuntime(store)
    assert runtime._current_market_v3_admission(key, target, provider_manifest=manifest) == admission
    assert snapshots == [source.provider_id]
    snapshots.clear()
    assert runtime._current_market_v3_admission(key, target, provider_manifest=manifest,
                                              credential_snapshot=snapshot) == admission
    assert snapshots == []
    snapshot = ProviderCredentialSnapshot.create(source.provider_id, 'environment', 'api_key',
                                                 {'apiKey': 'rotated-key'}, config_version=0, credential_version=0)
    assert runtime._current_market_v3_admission(key, target, provider_manifest=manifest) is None


@pytest.mark.parametrize('version, valid', [(0, True), (7, True), (-1, False), (True, False), ('7', False), (None, False)])
def test_version_strategy_preserves_integer_contract(registered_source, monkeypatch, version, valid):
    source, key, target = registered_source
    registry = CapabilityRegistry((*[item for item in CAPABILITY_SOURCES.sources
                                    if item.provider_id != source.provider_id],
                                   replace(source, credential_revision_kind='version')))
    monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, '_bindings', registry._bindings)
    result = market_v3_current_route_revisions(key, target,
        {'providerId': source.provider_id, 'requiresCredential': True}, credential_version=version,
        credential_revision='hmac-sha256-v1:' + 'a' * 64)
    assert (result is not None) == valid
    if valid:
        assert result['credentialRevision'] == str(version)


@pytest.mark.parametrize('provider, method, values, payload', [
    ('hithink', 'api_key', {'apiKey': ' synthetic-key '}, b'provider=hithink\0credential=api_key\0synthetic-key'),
    ('tushare', 'token', {'token': ' synthetic-token ', 'httpUrl': 'https://example.invalid/api'},
     b'provider=tushare\0credential=token-and-endpoint-v1\0' + json.dumps(
         ['synthetic-token', 'https://example.invalid/api'], separators=(',', ':')).encode()),
    ('rqdata', 'username_password', {'username': 'synthetic-user', 'password': ' synthetic-password '},
     b'provider=rqdata\0credential=username-password-v1\0' + json.dumps(
         ['synthetic-user', ' synthetic-password '], separators=(',', ':')).encode()),
])
@pytest.mark.parametrize('origin', ['environment', 'control'])
def test_original_hmac_bytes_and_origin_permissions_preserved(provider, method, values, payload, origin):
    snapshot = ProviderCredentialSnapshot.create(provider, origin, method, values,
                                                 config_version=0, credential_version=0)
    if provider == 'tushare' and origin == 'control':
        assert provider_credential_revision(snapshot, 'test-v1', b'isolated-master') is None
        return
    if origin == 'control':
        payload = b'source=control\0' + payload
    context = b'thesis-ledger/provider-credential-revision/v1\0test-v1'
    key = hmac.new(b'isolated-master', context, hashlib.sha256).digest()
    expected = 'hmac-sha256-v1:' + hmac.new(key, payload, hashlib.sha256).hexdigest()
    assert provider_credential_revision(snapshot, 'test-v1', b'isolated-master') == expected


def test_undeclared_source_and_payload_fail_closed(registered_source, monkeypatch):
    source, _, _ = registered_source
    snapshot = ProviderCredentialSnapshot.create('unknown-source', 'environment', 'api_key',
                                                 {'apiKey': 'synthetic'}, config_version=0, credential_version=0)
    assert provider_credential_revision(snapshot, 'v1', b'master') is None
    snapshot = replace(snapshot, provider_id=source.provider_id)
    for payload in (None, lambda snapshot: '', lambda snapshot: b''):
        registry = CapabilityRegistry((replace(source, credential_revision_payload=payload),))
        monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
        assert provider_credential_revision(snapshot, 'v1', b'master') is None
