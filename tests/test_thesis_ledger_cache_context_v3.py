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


@pytest.mark.parametrize('market,asset,symbol', [
    ('HK', 'STOCK', '00700.HK'), ('HK', 'ETF', '02800.HK'),
    ('US', 'STOCK', 'AAPL.US'), ('US', 'ETF', 'SPY.US'),
])
def test_version_credentials_are_ready_and_rotate_cache_namespace_without_sdk(store, monkeypatch, market, asset, symbol):
    def configure(request_id, port):
        store.save_provider_config('futu', {'requestId': request_id, 'enabled': True,
            'credentials': {'method': 'opend', 'values': {'host': 'localhost', 'port': port}}})

    configure('first-connection', '11111')
    key = {**KEY, 'market': market, 'assetType': asset}
    target = {'providerId': 'futu', 'upstreamSource': 'futu'}
    assert apply(store, key, target)['effective']['routes'][0]['targets'][0]['eligible']
    runtime = ThesisLedgerProviderRuntime(store)
    data = Mock(side_effect=AssertionError('缓存上下文不应调用 SDK'))
    monkeypatch.setattr(runtime, 'execute_request', data)
    first = read_cache_context(runtime, request(symbol=symbol, assetType=asset))
    assert first['ready'] is True
    configure('changed-connection', '11112')
    second = read_cache_context(runtime, request(symbol=symbol, assetType=asset))
    assert second['ready'] is True
    assert second['policyRevision'] == first['policyRevision']
    assert second['generation'] != first['generation']
    store.save_provider_config('futu', {'requestId': 'disabled', 'enabled': False})
    assert read_cache_context(runtime, request(symbol=symbol, assetType=asset))['ready'] is False
    assert 'credential' not in json.dumps([first, second]).lower()
    data.assert_not_called()


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


# Background monitoring is a distinct caller contract, not a policy/permission toggle.
def background_request(**changes):
    return {'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'background-fixture',
            'symbol': '510300.SH', 'assetType': 'ETF', **changes}


def test_background_quote_plan_is_source_specific_and_never_reads_provider(store):
    from src.services.thesis_ledger_background_quote import background_quote_plan
    apply(store, KEY, TARGET)
    runtime = ThesisLedgerProviderRuntime(store)
    runtime.execute_request = Mock(side_effect=AssertionError('plan must not read data'))
    value = background_quote_plan(runtime, background_request())
    assert value['acquisition'] == 'public-snapshot'
    assert value['target'] == {**TARGET, 'routeIndex': 0}
    assert value['minIntervalSeconds'] == 600 and value['sourceUpdateIntervalSeconds'] is None
    assert background_quote_plan(runtime, background_request(symbol='600519.SH', assetType='STOCK'))['reason'] == 'not_selected'
    runtime.execute_request.assert_not_called()


def test_background_quote_rejects_control_changes_before_any_execution(store):
    from src.services.thesis_ledger_background_quote import background_quote_plan, constrain_background_quote
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ProviderCallError
    apply(store, KEY, TARGET)
    runtime = ThesisLedgerProviderRuntime(store)
    plan = background_quote_plan(runtime, background_request())
    req = ThesisLedgerDataRequest('REALTIME_QUOTE', '510300.SH', instrument_type='ETF',
        request_id='background-fixture', parameters={'backgroundQuote': {'generation': plan['generation']}})
    assert constrain_background_quote(runtime, req, [('tencent', 'tencent', 0)]) == [('tencent', 'tencent', 0)]
    with pytest.raises(ProviderCallError, match='不能跳过'):
        constrain_background_quote(runtime, req, [('hithink', 'fund-market-snapshot', 0), ('tencent', 'tencent', 1)])
    store.save_provider_config('tencent', {'requestId': 'stop', 'enabled': False})
    with pytest.raises(ProviderCallError, match='已变化'):
        constrain_background_quote(runtime, req, [('tencent', 'tencent', 0)])


def test_background_quote_never_admits_paid_primary_or_changes_selected_route(store, monkeypatch):
    from src.services.thesis_ledger_background_quote import background_quote_plan
    monkeypatch.setenv('HITHINK_API_KEY', 'synthetic-only-no-network')
    target = next(target for key, target, _ in CAPABILITY_SOURCES.routes('REALTIME_QUOTE')
                  if key == KEY and target['providerId'] == 'hithink')
    apply(store, KEY, target)
    runtime = ThesisLedgerProviderRuntime(store)
    runtime.execute_request = Mock(side_effect=AssertionError('paid request must not execute'))
    before = deepcopy(store.effective_policy_v3())
    plan = background_quote_plan(runtime, background_request())
    assert plan['acquisition'] == 'blocked' and plan['reason'] == 'background_budget_unverified'
    assert plan['target']['providerId'] == 'hithink'
    assert store.effective_policy_v3()['routes'] == before['routes']
    assert store.effective_policy_v3()['revision'] == before['revision']
    runtime.execute_request.assert_not_called()


def test_background_pin_runs_inside_actual_runtime_before_provider_and_budget(store):
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerGatewayError
    apply(store, KEY, TARGET)
    runtime = ThesisLedgerProviderRuntime(store)
    runtime._execute_with_metadata = Mock(side_effect=AssertionError('bad pin must not reserve budget/call adapter'))
    request = ThesisLedgerDataRequest('REALTIME_QUOTE', '510300.SH', instrument_type='ETF',
        request_id='background-fixture', parameters={'backgroundQuote': {'generation': '0' * 64}})
    with pytest.raises(ThesisLedgerGatewayError) as error:
        runtime.execute_request(request)
    assert error.value.code == 'not_admitted'
    runtime._execute_with_metadata.assert_not_called()


def test_background_http_auth_identity_and_success(store, monkeypatch):
    from api.thesis_ledger_background_quote import build_background_quote_router
    from src.services.thesis_ledger_background_quote import background_quote_plan
    apply(store, KEY, TARGET)
    runtime = ThesisLedgerProviderRuntime(store)
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'synthetic-background-token')
    fake_quote = Mock(return_value={'provider': 'tencent', 'symbol': '510300.SH', 'price': 4.0})
    monkeypatch.setattr('api.thesis_ledger._real_quote', fake_quote)
    app = FastAPI(); app.include_router(build_background_quote_router(lambda: runtime), prefix='/api/v3/thesis-ledger')
    client = TestClient(app); url = '/api/v3/thesis-ledger/market/background-quote'
    headers = {'Authorization': 'Bearer synthetic-background-token'}
    assert client.post(url + '/plan', json=background_request()).status_code == 401
    assert client.post(url + '/plan', json=background_request(probe=True), headers=headers).status_code == 422
    plan = client.post(url + '/plan', json=background_request(), headers=headers).json()
    assert plan == background_quote_plan(runtime, background_request())
    assert client.post(url, json={**background_request(), 'generation': '0' * 64}, headers=headers).status_code == 503
    fake_quote.assert_not_called()
    response = client.post(url, json={**background_request(), 'generation': plan['generation']}, headers=headers)
    assert response.status_code == 200 and response.json()['quote']['provider'] == 'tencent'
    fake_quote.assert_called_once_with('510300.SH', request_id='background-fixture', asset_type='ETF',
                                      background={'generation': plan['generation']})


@pytest.mark.parametrize('fail', [False, True])
def test_background_actual_execution_shares_persistent_budget_across_restart(store, fail):
    from types import SimpleNamespace
    from src.services.thesis_ledger_background_quote import background_quote_plan
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerGatewayError
    apply(store, KEY, TARGET)
    calls = []

    def read(symbol):
        calls.append(symbol)
        if fail:
            raise RuntimeError('synthetic-provider-failure')
        return {'price': 4.0, 'open_price': 4.0, 'high': 4.0, 'low': 4.0, 'pre_close': 4.0,
                'source': 'tencent', 'provider_timestamp': '2026-11-06T02:00:00Z',
                'fetched_at': '2026-11-06T02:00:01Z',
                'units': {'price_currency': 'CNY', 'volume': 'unknown', 'turnover_currency': 'unknown'}}

    adapter = SimpleNamespace(get_realtime_quote_single_symbol=read)
    runtime = ThesisLedgerProviderRuntime(store, adapters={'tencent': adapter})
    plan = background_quote_plan(runtime, background_request())
    req = ThesisLedgerDataRequest('REALTIME_QUOTE', '510300.SH', instrument_type='ETF',
        request_id='background-budget', parameters={'backgroundQuote': {'generation': plan['generation']}})
    if fail:
        with pytest.raises(ThesisLedgerGatewayError):
            runtime.execute_request(req)
    else:
        result = runtime.execute_request(req)
        assert result.provider == 'tencent' and result.upstream_source == 'tencent'
    restarted = ThesisLedgerProviderRuntime(store, adapters={'tencent': adapter})
    new_plan = background_quote_plan(restarted, background_request())
    req = ThesisLedgerDataRequest('REALTIME_QUOTE', '510300.SH', instrument_type='ETF',
        request_id='background-budget-restart', parameters={'backgroundQuote': {'generation': new_plan['generation']}})
    with pytest.raises(ThesisLedgerGatewayError):
        restarted.execute_request(req)
    assert calls == ['510300.SH']


def test_background_preserves_selected_fallback_identity_without_trying_restricted_primary(store):
    from types import SimpleNamespace
    from src.services.thesis_ledger_background_quote import background_quote_plan
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest
    paid = {'providerId': 'hithink', 'upstreamSource': 'fund-market-snapshot'}
    store.apply_policy_v3({'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'fallback',
        'revision': 1, 'enabled': True, 'routes': [{'key': KEY, 'targets': [paid, TARGET]}]})
    value = {'price': 4.0, 'open_price': 4.0, 'high': 4.0, 'low': 4.0, 'pre_close': 4.0,
             'source': 'tencent', 'provider_timestamp': '2026-11-06T02:00:00Z',
             'fetched_at': '2026-11-06T02:00:01Z',
             'units': {'price_currency': 'CNY', 'volume': 'unknown', 'turnover_currency': 'unknown'}}
    public = Mock(return_value=value)
    restricted = Mock(side_effect=AssertionError('restricted source must not run'))
    runtime = ThesisLedgerProviderRuntime(store, adapters={
        'tencent': SimpleNamespace(get_realtime_quote_single_symbol=public),
        'hithink': SimpleNamespace(fetch_quote=restricted)})
    plan = background_quote_plan(runtime, background_request())
    assert plan['acquisition'] == 'public-snapshot' and plan['target']['routeIndex'] == 1
    req = ThesisLedgerDataRequest('REALTIME_QUOTE', '510300.SH', instrument_type='ETF', request_id='fallback',
        parameters={'backgroundQuote': {'generation': plan['generation']}})
    result = runtime.execute_request(req)
    assert result.provider == 'tencent' and result.fallback_used is True and result.route_index == 1
    public.assert_called_once_with('510300.SH'); restricted.assert_not_called()


def _policy(store, revision, routes, enabled=True):
    return store.apply_policy_v3({'contractVersion':3,'consumer':'thesis-ledger','requestId':f'epoch-{revision}',
        'revision':revision,'enabled':enabled,'routes':routes})


def test_unrelated_minute_policy_does_not_invalidate_quote_cache_or_read_data(store, monkeypatch):
    routes=[{'key':KEY,'targets':[TARGET]}]
    _policy(store,1,routes)
    runtime=ThesisLedgerProviderRuntime(store)
    monkeypatch.setattr(runtime,'execute_request',Mock(side_effect=AssertionError('不得读取来源')))
    first=read_cache_context(runtime,request())
    minute={'key':{'kind':'bar','market':'CN','assetType':'ETF','capability':'MINUTE_BAR','timeframe':'60m','adjustment':'none'},
            'targets':[{'providerId':'efinance','upstreamSource':'eastmoney'}]}
    _policy(store,2,[*routes,minute])
    current=read_cache_context(runtime,request())
    assert current['policyRevision']==2 and first['policyRevision']==1
    assert current['generation']==first['generation']
    runtime.execute_request.assert_not_called()


@pytest.mark.parametrize('transition',['global-disable','route-disable','remove','reorder'])
def test_quote_control_change_and_aba_restore_never_reuse_old_generation(store, transition):
    other={'providerId':'efinance','upstreamSource':'eastmoney'}
    routes=[{'key':KEY,'targets':[TARGET,other]}]
    _policy(store,1,routes)
    runtime=ThesisLedgerProviderRuntime(store)
    first=read_cache_context(runtime,request())
    changed=deepcopy(routes)
    enabled=True
    if transition=='global-disable':enabled=False
    elif transition=='route-disable':changed[0]['enabled']=False
    elif transition=='remove':changed=[]
    else:changed[0]['targets'].reverse()
    _policy(store,2,changed,enabled)
    middle=read_cache_context(runtime,request())
    _policy(store,3,routes)
    restored=read_cache_context(runtime,request())
    assert len({first['generation'],middle['generation'],restored['generation']})==3
    assert restored['ready'] is True
