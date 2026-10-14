"""缓存控制上下文走原Store，零SDK/数据读取且不披露秘密。"""

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_cache_context_v3 import router
from src.services.thesis_ledger_cache_context_v3 import read_cache_context, CacheContextError
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply

KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': 'REALTIME_QUOTE'}
TARGET = {'providerId': 'tencent', 'upstreamSource': 'tencent'}


def request(**changes):
    return {'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'cache-control',
            'capability': 'REALTIME_QUOTE', 'symbol': '510300.SH', **changes}


def test_original_type_resolution_and_stable_generation_have_zero_data_calls(store, monkeypatch):
    apply(store, KEY, TARGET)
    runtime = ThesisLedgerProviderRuntime(store)
    data = Mock(side_effect=AssertionError('不应读取数据'))
    monkeypatch.setattr(runtime, 'execute_request', data)
    first = read_cache_context(runtime, request())
    second = read_cache_context(runtime, request(requestId='another'))
    assert first['routeKey'] == KEY and first['ready'] is True
    assert first['generation'] == second['generation']
    assert set(first) == {'contractVersion', 'consumer', 'requestId', 'capability', 'symbol', 'routeKey',
                          'policyRevision', 'generation', 'ready', 'reason'}
    assert 'credential' not in json.dumps(first).lower()
    data.assert_not_called()


def test_provider_disable_and_restore_change_generation_even_same_policy(store):
    apply(store, KEY, TARGET)
    runtime = ThesisLedgerProviderRuntime(store)
    first = read_cache_context(runtime, request())
    store.save_provider_config('tencent', {'requestId': 'disabled', 'enabled': False})
    disabled = read_cache_context(runtime, request())
    assert disabled['ready'] is False and disabled['generation'] != first['generation']
    store.save_provider_config('tencent', {'requestId': 'restored', 'enabled': True})
    restored = read_cache_context(runtime, request())
    assert restored['ready'] is True and restored['generation'] not in {first['generation'], disabled['generation']}


def test_no_route_is_not_selected_not_implicitly_added(store):
    store.apply_policy_v3({'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'empty-enabled',
                          'revision': 1, 'enabled': True, 'routes': []})
    runtime = ThesisLedgerProviderRuntime(store)
    result = read_cache_context(runtime, request())
    assert result['ready'] is False and result['reason'] == 'not_selected'
    assert store.effective_policy_v3()['routes'] == []


def test_quote_function_disable_preserves_targets_and_rejects_cache_context(store):
    store.apply_policy_v3({'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'quote-off',
                          'revision': 1, 'enabled': True,
                          'routes': [{'key': KEY, 'targets': [TARGET], 'enabled': False}]})
    result = read_cache_context(ThesisLedgerProviderRuntime(store), request())
    assert result['ready'] is False and result['reason'] == 'disabled'
    route = store.effective_policy_v3()['routes'][0]
    assert route['targets'][0]['providerId'] == TARGET['providerId']


def test_state_change_during_control_sampling_is_rejected(store, monkeypatch):
    apply(store, KEY, TARGET)
    runtime = ThesisLedgerProviderRuntime(store)
    original = store.effective_policy_v3
    calls = []

    def read():
        value = deepcopy(original())
        calls.append(True)
        if len(calls) == 2:
            value['revision'] += 1
            value['sourceDesiredRevision'] += 1
        return value

    monkeypatch.setattr(store, 'effective_policy_v3', read)
    with pytest.raises(CacheContextError, match='policy_not_applied'):
        read_cache_context(runtime, request())


def test_environment_credential_rotation_is_private_and_changes_namespace(store, monkeypatch):
    monkeypatch.setenv('HITHINK_API_KEY', 'synthetic-first')
    target = next(target for key, target, _ in CAPABILITY_SOURCES.routes('REALTIME_QUOTE')
                  if key == KEY and target['providerId'] == 'hithink')
    apply(store, KEY, target)
    runtime = ThesisLedgerProviderRuntime(store)
    first = read_cache_context(runtime, request())
    config_version = next(item['configVersion'] for item in store.provider_registry()
                          if item['providerId'] == 'hithink')
    monkeypatch.setenv('HITHINK_API_KEY', 'synthetic-second')
    second = read_cache_context(runtime, request())
    assert first['policyRevision'] == second['policyRevision']
    assert first['generation'] != second['generation']
    assert next(item['configVersion'] for item in store.provider_registry()
                if item['providerId'] == 'hithink') == config_version
    assert 'synthetic-' not in json.dumps([first, second])


@pytest.mark.parametrize('change', ['registry_duplicate', 'route_index', 'config_version'])
def test_malformed_control_facts_fail_closed(store, monkeypatch, change):
    apply(store, KEY, TARGET)
    runtime = ThesisLedgerProviderRuntime(store)
    if change == 'route_index':
        original = store.effective_policy_v3()
        original['routes'][0]['targets'][0]['routeIndex'] = 1
        monkeypatch.setattr(store, 'effective_policy_v3', lambda: original)
    else:
        registry = store.provider_registry()
        if change == 'registry_duplicate':
            registry.append(deepcopy(registry[0]))
        else:
            next(item for item in registry if item['providerId'] == 'tencent')['configVersion'] = -1
        monkeypatch.setattr(store, 'provider_registry', lambda: registry)
    with pytest.raises(CacheContextError, match='invalid_response'):
        read_cache_context(runtime, request())


def test_http_data_auth_and_parameter_isolation(store, monkeypatch):
    apply(store, KEY, TARGET)
    runtime = ThesisLedgerProviderRuntime(store)
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'synthetic-cache-token')
    monkeypatch.setattr('api.thesis_ledger_cache_context_v3.get_thesis_ledger_runtime', lambda: runtime)
    app = FastAPI(); app.include_router(router, prefix='/api/v3'); client = TestClient(app)
    url = '/api/v3/thesis-ledger/market/cache-context'
    assert client.post(url, json=request()).status_code == 401
    invalid = client.post(url, json=request(sdk_method='arbitrary'), headers={'Authorization': 'Bearer synthetic-cache-token'})
    assert invalid.status_code == 422
    assert invalid.json() == {'contractVersion': 3, 'requestId': 'cache-control',
                              'error': {'code': 'invalid_request', 'message': '来源拒绝请求参数'}}
    value = client.post(url, json=request(), headers={'Authorization': 'Bearer synthetic-cache-token'})
    assert value.status_code == 200 and value.json()['ready'] is True


def test_http_missing_control_uses_correlated_v3_error(store, monkeypatch):
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'synthetic-cache-token')
    monkeypatch.setattr('api.thesis_ledger_cache_context_v3.get_thesis_ledger_runtime',
                        lambda: ThesisLedgerProviderRuntime(store))
    app = FastAPI(); app.include_router(router, prefix='/api/v3')
    value = TestClient(app).post('/api/v3/thesis-ledger/market/cache-context', json=request(),
                                headers={'Authorization': 'Bearer synthetic-cache-token'})
    assert value.status_code == 503
    assert value.json()['requestId'] == 'cache-control'
    assert value.json()['error']['code'] == 'upstream_failure'


def test_http_malformed_control_is_response_error_not_request_error(store, monkeypatch):
    apply(store, KEY, TARGET)
    registry = store.provider_registry()
    registry.append(deepcopy(registry[0]))
    monkeypatch.setattr(store, 'provider_registry', lambda: registry)
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'synthetic-cache-token')
    monkeypatch.setattr('api.thesis_ledger_cache_context_v3.get_thesis_ledger_runtime',
                        lambda: ThesisLedgerProviderRuntime(store))
    app = FastAPI(); app.include_router(router, prefix='/api/v3')
    value = TestClient(app).post('/api/v3/thesis-ledger/market/cache-context', json=request(),
                                headers={'Authorization': 'Bearer synthetic-cache-token'})
    assert value.status_code == 502
    assert value.json() == {'contractVersion': 3, 'requestId': 'cache-control',
                            'error': {'code': 'invalid_response', 'message': '行情上游响应格式无效'}}
