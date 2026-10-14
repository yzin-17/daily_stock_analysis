"""原环境凭据生命周期特征；只使用合成配置和临时SQLite。"""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.services import thesis_ledger_control as control
from src.services.thesis_ledger_environment_credentials import environment_credential_values
from src.services.thesis_ledger_capability_contracts import EnvironmentCredentialPolicy
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES


SINGLE_KEYS = [('tushare', 'tushare_token', 'token', 'token'),
               ('tickflow', 'tickflow_api_key', 'apiKey', 'api_key'),
               ('finnhub', 'finnhub_api_key', 'apiKey', 'api_key'),
               ('alphavantage', 'alphavantage_api_key', 'apiKey', 'api_key')]


def store_and_reads(monkeypatch, tmp_path, config):
    config_reads, value_reads = [], []
    monkeypatch.setattr('src.config.get_config', lambda: config_reads.append('config') or config)
    original = control._environment_credential_values

    def read(provider):
        value_reads.append(provider)
        return original(provider)

    monkeypatch.setattr(control, '_environment_credential_values', read)
    store = control.ThesisLedgerControlStore(str(tmp_path / 'environment.db'))
    config_reads.clear()
    value_reads.clear()
    return store, config_reads, value_reads


@pytest.mark.parametrize('provider,config_field,value_field,method', SINGLE_KEYS)
def test_snapshot_preserves_original_three_reads_and_method(
        monkeypatch, tmp_path, provider, config_field, value_field, method):
    monkeypatch.setenv('TUSHARE_HTTP_URL', 'https://isolated.example.invalid/tushare')
    store, configs, values = store_and_reads(monkeypatch, tmp_path,
        SimpleNamespace(**{config_field: '  isolated-value  '}))
    actual = store.provider_credential_snapshot(provider)
    expected = {value_field: 'isolated-value'}
    if provider == 'tushare':
        expected['httpUrl'] = 'https://isolated.example.invalid/tushare'
    assert actual.source == 'environment' and actual.method == method
    assert actual.values == expected
    assert configs == ['config'] * 3 and values == [provider] * 3
    assert actual.config_version == actual.credential_version == 0
    # 端点仅在最终runtime快照追加，不加入环境可配置字段/原验证阶段。
    assert environment_credential_values(provider) == {value_field: 'isolated-value'}


@pytest.mark.parametrize('provider,config_field,value_field,method', SINGLE_KEYS)
@pytest.mark.parametrize('raw', ['', '  ', None])
def test_empty_environment_does_not_claim_configured_method(
        monkeypatch, tmp_path, provider, config_field, value_field, method, raw):
    store, configs, values = store_and_reads(monkeypatch, tmp_path, SimpleNamespace(**{config_field: raw}))
    actual = store.provider_credential_snapshot(provider)
    assert actual.source == 'none' and actual.method is None and actual.values == {}
    assert configs == ['config'] and values == [provider]


def test_hithink_environment_does_not_load_generic_config(monkeypatch, tmp_path):
    monkeypatch.setenv('HITHINK_API_KEY', '  isolated-hithink  ')
    store, configs, values = store_and_reads(monkeypatch, tmp_path, SimpleNamespace())
    actual = store.provider_credential_snapshot('hithink')
    assert actual.source == 'environment' and actual.method == 'api_key'
    assert actual.values == {'apiKey': 'isolated-hithink'}
    assert configs == [] and values == ['hithink'] * 3


@pytest.mark.parametrize('method', ['legacy', 'oauth'])
def test_longbridge_original_reader_and_method_selection(monkeypatch, tmp_path, method):
    from src.services import thesis_ledger_longbridge_environment as environment
    token = json.dumps({'client_id': 'isolated-client', 'access_token': 'isolated-access',
                       'refresh_token': 'isolated-refresh', 'expires_at': 1900000000})
    supplied = {'appKey': 'isolated-key', 'appSecret': 'isolated-secret', 'accessToken': 'isolated-access'}
    if method == 'oauth':
        supplied = {'clientId': 'isolated-client', 'tokenJson': token}
    delegates = []
    config = SimpleNamespace()
    monkeypatch.setattr(environment, 'longbridge_environment_values',
        lambda value: delegates.append(value) or supplied)
    store, configs, values = store_and_reads(monkeypatch, tmp_path, config)
    actual = store.provider_credential_snapshot('longbridge')
    assert actual.source == 'environment' and actual.method == method and actual.values == supplied
    assert configs == ['config'] * 3 and values == ['longbridge'] * 3 and delegates == [config] * 3


@pytest.mark.parametrize('provider', ['tushare', 'tickflow', 'finnhub', 'alphavantage', 'longbridge', 'unknown'])
def test_config_read_failure_remains_empty_credentials(monkeypatch, provider):
    def fail():
        raise RuntimeError('isolated config error')
    monkeypatch.setattr('src.config.get_config', fail)
    assert environment_credential_values(provider) == {}


def test_invalid_tushare_endpoint_remains_snapshot_error(monkeypatch, tmp_path):
    monkeypatch.setenv('TUSHARE_HTTP_URL', 'isolated-invalid-endpoint')
    store, _, _ = store_and_reads(monkeypatch, tmp_path, SimpleNamespace(tushare_token='isolated-token'))
    with pytest.raises(ValueError, match='TUSHARE_HTTP_URL 必须以'):
        store.provider_credential_snapshot('tushare')


@pytest.mark.parametrize('invalid', [object(), EnvironmentCredentialPolicy(None),
    EnvironmentCredentialPolicy(lambda: {}, select_method='oauth'),
    EnvironmentCredentialPolicy(lambda: {}, snapshot_values=True)])
def test_invalid_environment_policy_rejected(invalid):
    source = CAPABILITY_SOURCES.source('tencent')
    with pytest.raises(ValueError, match='来源环境凭据策略无效'):
        CapabilityRegistry((replace(source, environment_credential_policy=invalid),))


def test_new_provider_environment_credentials_need_only_source_declaration(monkeypatch, tmp_path):
    from src.services.provider_credentials import CredentialDefinition
    source = CAPABILITY_SOURCES.source('tencent')
    provider = 'isolated-environment-provider'
    calls = []
    manifest = {**source.manifest(), 'providerId': provider, 'requiresCredential': True}
    replacement = replace(source, provider_id=provider, manifest=lambda: manifest,
        credentials=(('api_key', ('apiKey',)),), environment_credential_policy=EnvironmentCredentialPolicy(
            lambda: calls.append('read') or {'apiKey': 'isolated-key'}))
    registry = CapabilityRegistry((*CAPABILITY_SOURCES.sources, replacement))
    monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, '_bindings', registry._bindings)
    monkeypatch.setitem(control.PROVIDER_MANIFESTS, provider, manifest)
    monkeypatch.setitem(control.PROVIDER_CREDENTIAL_DEFINITIONS, provider,
                        (CredentialDefinition('api_key', ('apiKey',)),))
    store = control.ThesisLedgerControlStore(str(tmp_path / 'new-source.db'))
    actual = store.provider_credential_snapshot(provider)
    assert actual.source == 'environment' and actual.method == 'api_key'
    assert actual.values == {'apiKey': 'isolated-key'} and calls == ['read'] * 3
