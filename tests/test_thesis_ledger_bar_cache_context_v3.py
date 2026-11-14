"""精确K线控制投影使用原Store与目录，不执行价格或SDK调用。"""

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.thesis_ledger_cache_context_v3 import router
from api.thesis_ledger_auth import require_contract_token
from src.services.thesis_ledger_bar_cache_context_v3 import read_bar_cache_context
from src.services.thesis_ledger_cache_context_v3 import CacheContextError
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply

KEY = {'kind': 'bar', 'market': 'US', 'assetType': 'STOCK', 'capability': 'MINUTE_BAR',
       'timeframe': '1m', 'adjustment': 'none'}
TARGET = {'providerId': 'futu', 'upstreamSource': 'futu'}


def request(**changes):
    return {'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'bar-cache',
            'purpose': 'interactive-chart', 'capability': 'MINUTE_BAR', 'symbol': 'AAPL.US',
            'routeKey': KEY, 'routeTarget': {**TARGET, 'routeIndex': 0},
            'start': '2026-11-11', 'end': '2026-11-11', **changes}


@pytest.fixture
def runtime(store, monkeypatch):
    configure(store, 'initial', '11111')
    assert apply(store, KEY, TARGET)['effective']['routes'][0]['targets'][0]['eligible']
    runtime = ThesisLedgerProviderRuntime(store)
    for name in ['execute_request', 'execute_market_bars_v3', '_adapter']:
        monkeypatch.setattr(runtime, name, Mock(side_effect=AssertionError('控制投影不得调用价格或SDK')))
    return runtime


def configure(store, request_id, port):
    store.save_provider_config('futu', {'requestId': request_id, 'enabled': True,
        'credentials': {'method': 'opend', 'values': {'host': 'synthetic.local', 'port': port}}})


def test_stable_exact_context_and_current_request_id_without_price(runtime):
    first = read_bar_cache_context(runtime, request())
    second = read_bar_cache_context(runtime, request(requestId='other'))
    assert first['ready'] and first['reason'] is None
    assert first['generation'] == second['generation'] and second['requestId'] == 'other'
    for key, value in request().items():
        assert first[key] == value
    for name in ['execute_request', 'execute_market_bars_v3', '_adapter']:
        getattr(runtime, name).assert_not_called()
    assert 'synthetic.local' not in json.dumps(first) and 'credential' not in json.dumps(first).lower()


def test_first_success_evidence_does_not_invalidate_already_ready_context(runtime):
    from src.services.thesis_ledger_capability_verification import begin_verification, publish_verified
    before = read_bar_cache_context(runtime, request())
    attempt = begin_verification(runtime.store, KEY, TARGET)
    assert publish_verified(runtime.store, attempt, evidence={'controlled': 'first-success'})
    entry = next(e for e in runtime.market_route_catalog_v3()['entries'] if e['key'] == KEY and e['target'] == TARGET)
    assert entry['verification']['state'] == 'verified'
    after = read_bar_cache_context(runtime, request())
    assert before['ready'] and after['ready'] and before['generation'] == after['generation']


def test_actual_permission_change_still_invalidates_verified_context(runtime, monkeypatch):
    original = runtime.market_route_catalog_v3()
    before = read_bar_cache_context(runtime, request())
    entry = next(e for e in original['entries'] if e['key'] == KEY and e['target'] == TARGET)
    entry['verification'] = {'state': 'verified'}
    entry['accountAccess'] = {'state': 'permission_denied', 'attempted': True, 'checkedAt': '2026-11-12T00:00:00Z'}
    monkeypatch.setattr(runtime, 'market_route_catalog_v3', lambda: original)
    after = read_bar_cache_context(runtime, request())
    assert not after['ready'] and after['generation'] != before['generation']


@pytest.mark.parametrize('change', [
    {'purpose': 'point-in-time'}, {'symbol': 'aapl.us'}, {'start': '2026-11-12'},
    {'end': '2026-02-30'}, {'capability': 'DAILY_BAR'}, {'sdk_method': 'arbitrary'},
    {'routeKey': {**KEY, 'market': 'HK'}}, {'routeKey': {**KEY, 'timeframe': '1d'}},
    {'routeKey': {**KEY, 'adjustment': 'qfq'}}, {'routeTarget': {**TARGET, 'routeIndex': True}},
])
def test_invalid_identity_is_rejected_before_control_read(runtime, monkeypatch, change):
    read = Mock(side_effect=AssertionError('参数无效时不得读取控制'))
    monkeypatch.setattr(runtime.store, 'effective_policy_v3', read)
    with pytest.raises(ValidationError):
        read_bar_cache_context(runtime, request(**change))
    read.assert_not_called()


def test_credentials_and_disable_restore_invalidate_without_leaking_values(runtime):
    first = read_bar_cache_context(runtime, request())
    configure(runtime.store, 'rotated', '11112')
    rotated = read_bar_cache_context(runtime, request())
    assert rotated['ready'] and rotated['generation'] != first['generation']
    runtime.store.save_provider_config('futu', {'requestId': 'off', 'enabled': False})
    disabled = read_bar_cache_context(runtime, request())
    assert not disabled['ready'] and disabled['generation'] != rotated['generation']
    runtime.store.save_provider_config('futu', {'requestId': 'on', 'enabled': True})
    restored = read_bar_cache_context(runtime, request())
    assert restored['ready'] and restored['generation'] not in {first['generation'], rotated['generation']}


def test_exact_target_order_and_window_are_isolated(runtime):
    first = read_bar_cache_context(runtime, request())
    changed = read_bar_cache_context(runtime, request(start='2026-11-10'))
    assert first['generation'] != changed['generation']
    wrong_order = read_bar_cache_context(runtime, request(routeTarget={**TARGET, 'routeIndex': 1}))
    assert not wrong_order['ready'] and wrong_order['reason'] == 'not_selected'


def test_market_permission_and_dependency_facts_fail_closed(runtime, monkeypatch):
    original = runtime.market_route_catalog_v3()
    for state in ['dependency_missing', 'quota_unavailable']:
        catalog = deepcopy(original)
        entry = next(e for e in catalog['entries'] if e['key'] == KEY and e['target'] == TARGET)
        entry['state'] = state
        monkeypatch.setattr(runtime, 'market_route_catalog_v3', lambda: catalog)
        result = read_bar_cache_context(runtime, request())
        assert not result['ready'] and result['reason'] == 'not_eligible'
    catalog = deepcopy(original)
    entry = next(e for e in catalog['entries'] if e['key'] == KEY and e['target'] == TARGET)
    entry['accountAccess'] = {'state': 'permission_denied', 'attempted': True, 'checkedAt': '2026-11-11T00:00:00Z'}
    monkeypatch.setattr(runtime, 'market_route_catalog_v3', lambda: catalog)
    assert not read_bar_cache_context(runtime, request())['ready']


def test_control_change_during_projection_is_rejected(runtime, monkeypatch):
    original = runtime.store.effective_policy_v3
    calls = []

    def read():
        value = deepcopy(original())
        calls.append(True)
        if len(calls) > 1:
            value['revision'] += 1
            value['sourceDesiredRevision'] += 1
        return value

    monkeypatch.setattr(runtime.store, 'effective_policy_v3', read)
    with pytest.raises(CacheContextError, match='policy_not_applied'):
        read_bar_cache_context(runtime, request())


def test_account_expiry_and_admission_window_rejection_cannot_hit_cache(runtime, monkeypatch):
    catalog = deepcopy(runtime.market_route_catalog_v3())
    entry = next(e for e in catalog['entries'] if e['key'] == KEY and e['target'] == TARGET)
    entry['accountAccess'] = {'state': 'allowed', 'attempted': True, 'checkedAt': '2026-01-01T00:00:00Z',
                              'validUntil': '2026-01-02T00:00:00Z'}
    monkeypatch.setattr(runtime, 'market_route_catalog_v3', lambda: catalog)
    assert not read_bar_cache_context(runtime, request())['ready']
    entry.pop('accountAccess')
    monkeypatch.setattr('src.services.thesis_ledger_bar_cache_context_v3.price_route_admission',
                        lambda *args: (None, 'insufficient_coverage'))
    assert not read_bar_cache_context(runtime, request())['ready']


@pytest.mark.parametrize('kind', ['catalog', 'registry', 'route'])
def test_malformed_control_facts_return_stable_error(runtime, monkeypatch, kind):
    if kind == 'catalog':
        monkeypatch.setattr(runtime, 'market_route_catalog_v3', lambda: {'integrity': 'complete', 'entries': [None]})
    elif kind == 'registry':
        monkeypatch.setattr(runtime.store, 'provider_registry', lambda: [None])
    else:
        policy = deepcopy(runtime.store.effective_policy_v3())
        policy['routes'][0]['targets'][0]['routeIndex'] = True
        monkeypatch.setattr(runtime.store, 'effective_policy_v3', lambda: policy)
    with pytest.raises(CacheContextError, match='invalid_response'):
        read_bar_cache_context(runtime, request())


def test_original_authenticated_api_dispatches_bar_contract(runtime, monkeypatch):
    monkeypatch.setattr('api.thesis_ledger_cache_context_v3.get_thesis_ledger_runtime', lambda: runtime)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_contract_token] = lambda: None
    response = TestClient(app).post('/thesis-ledger/market/cache-context', json=request())
    assert response.status_code == 200 and response.json()['ready'] is True
