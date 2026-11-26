"""深市 ETF 的实际 SDK 字段、隔离进程、Data HTTP 与来源控制检查。"""

from datetime import datetime, timezone
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import json
import subprocess

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import thesis_ledger_current_trading as api
from data_provider.futu_market import FutuMarketAdapter, FutuMarketError, RESULT_PREFIX
from data_provider.futu_trading_state import read_futu_trading_state
from src.services import thesis_ledger_sz_etf_trading as service
from src.services.thesis_ledger_current_trading import current_trading

NOW = datetime(2026, 11, 23, 14, 0, 50, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr('socket.socket.connect', lambda *a, **k: pytest.fail('不得访问真实网络'))


def sdk_for(**patch):
    context = Mock()
    # 字段及样例值来自已保存的 159915.SZ 真实快照；状态异常用受控变体。
    row = {'code': 'SZ.159915', 'name': '创业板ETF易方达', 'update_time': '2026-11-23 15:00:00',
           'suspension': False, 'sec_status': 'NORMAL', 'lot_size': 100, 'last_price': 2.295, **patch}
    context.get_market_snapshot.return_value = 0, pd.DataFrame([row])
    sdk = SimpleNamespace(OpenQuoteContext=Mock(return_value=context), RET_OK=0)
    return sdk, context


@pytest.mark.parametrize('fields,state', [
    ({}, 'normal'), ({'suspension': True}, 'suspended'),
    ({'suspension': None}, 'unknown'), ({'sec_status': 'NONE'}, 'unknown'),
    ({'update_time': '2026-11-24 10:00:00'}, 'unknown'), ({'update_time': 'bad'}, 'unknown')])
def test_actual_snapshot_fields_and_unknown_boundaries(fields, state):
    sdk, context = sdk_for(**fields)
    result = read_futu_trading_state(sdk, 'localhost', 11111, '159915.SZ', clock=lambda: NOW)
    assert result['state'] == state and result['symbol'] == '159915.SZ'
    assert result['assetType'] == 'ETF' and result['day'] == '2026-11-23'
    assert result['evidence']['provider'] == 'futu'
    context.get_market_snapshot.assert_called_once_with(['SZ.159915'])
    context.close.assert_called_once()
    assert not context.get_stock_basicinfo.called


def test_no_trade_today_does_not_replace_explicit_current_state_with_price_inference():
    sdk, _ = sdk_for(update_time='2026-11-20 15:00:00')
    result = read_futu_trading_state(sdk, 'localhost', 11111, '159915.SZ', clock=lambda: NOW)
    assert result['state'] == 'normal'
    assert result['evidence']['fields']['marketTime'].startswith('2026-11-20')


def test_wrong_symbol_fails_and_closes_sdk():
    sdk, context = sdk_for(code='SZ.159919')
    with pytest.raises(FutuMarketError):
        read_futu_trading_state(sdk, 'localhost', 11111, '159915.SZ', clock=lambda: NOW)
    context.close.assert_called_once()


def test_shared_worker_one_bounded_request_and_timeout_no_retry(monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append(json.loads(kwargs['input']))
        assert kwargs['timeout'] == 4.5
        return SimpleNamespace(returncode=0, stdout=RESULT_PREFIX + json.dumps({'state': 'normal'}))
    monkeypatch.setattr(subprocess, 'run', run)
    adapter = FutuMarketAdapter(host='localhost', port='11111')
    assert adapter.get_current_trading_state('159915.SZ')['state'] == 'normal'
    assert calls[0]['operation'] == 'trading-state' and len(calls) == 1

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('controlled', 4.5)
    monkeypatch.setattr(subprocess, 'run', timeout)
    with pytest.raises(FutuMarketError) as error:
        adapter.get_current_trading_state('159915.SZ')
    assert error.value.code == 'timeout'


def store(enabled=True, version=1):
    return SimpleNamespace(provider_registry=lambda: [{'providerId': 'futu', 'enabled': enabled, 'configVersion': version}],
                           provider_credential_snapshot=lambda name: SimpleNamespace(values={'host': 'localhost', 'port': '11111'}))


@pytest.mark.parametrize('variant', ['enabled', 'disabled', 'changed', 'missing'])
def test_provider_switch_and_configuration_guard(monkeypatch, variant):
    s = store(enabled=variant != 'disabled')
    adapter = Mock()
    adapter.get_current_trading_state.return_value = {'symbol': '159915.SZ', 'state': 'normal', 'evidence': {}}
    monkeypatch.setattr(service, 'FutuMarketAdapter', lambda **kw: adapter)
    if variant == 'changed':
        s.provider_registry = Mock(side_effect=[store().provider_registry(), store(version=2).provider_registry()])
    if variant == 'missing':
        s.provider_credential_snapshot = lambda name: SimpleNamespace(values={})
    result = service.read_sz_etf_trading(s, '159915.SZ')
    assert result['state'] == ('normal' if variant == 'enabled' else 'unknown')
    assert adapter.get_current_trading_state.call_count == (0 if variant in {'disabled', 'missing'} else 1)


@pytest.mark.parametrize('change', ['timestamp', 'revision', 'enabled', 'route', 'eligibility', 'removed'])
def test_full_data_http_uses_sdk_projection_and_keeps_policy(monkeypatch, change):
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'isolated')
    policy = {'contractVersion': 1, 'enabled': True, 'revision': 202, 'sourceDesiredRevision': 202,
              'routes': [{'key': {'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': 'REALTIME_QUOTE'},
                         'targets': [{'providerId': 'hithink', 'upstreamSource': 'fund-market-snapshot',
                                      'routeIndex': 0, 'eligible': True, 'reason': None}]}]}
    s = store()
    after = deepcopy(policy)
    policy['appliedAt'] = '2026-11-23T14:00:49+00:00'
    after['appliedAt'] = '2026-11-23T14:00:50+00:00'
    if change == 'revision':
        after['revision'] += 1
    elif change == 'enabled':
        after['enabled'] = False
    elif change == 'route':
        after['routes'][0]['targets'][0]['providerId'] = 'other'
    elif change == 'eligibility':
        after['routes'][0]['targets'][0]['eligible'] = False
    elif change == 'removed':
        after = None
    s.effective_policy_v3 = Mock(side_effect=[policy, after])
    monkeypatch.setattr(api, 'ThesisLedgerControlStore', lambda: s)
    sdk, context = sdk_for()

    def read(symbol):
        return read_futu_trading_state(sdk, 'localhost', 11111, symbol, clock=lambda: NOW)

    adapter = SimpleNamespace(get_current_trading_state=read)
    monkeypatch.setattr(service, 'FutuMarketAdapter', lambda **kw: adapter)
    app = FastAPI()
    app.include_router(api.router, prefix='/api/v1')
    response = TestClient(app).get('/api/v1/thesis-ledger/market/trading-observation?symbol=159915.SZ&assetType=ETF',
                                   headers={'Authorization': 'Bearer isolated'})
    assert response.status_code == (200 if change == 'timestamp' else 409)
    if change == 'timestamp':
        assert response.json()['state'] == 'normal' and response.json()['evidence']['configVersion'] == 1
    else:
        assert response.json()['detail']['code'] == 'policy_changed'
    context.get_market_snapshot.assert_called_once_with(['SZ.159915'])
    assert policy['routes'][0]['targets'][0]['providerId'] == 'hithink'


def test_current_dispatch_reads_sz_etf_instead_of_unconditional_unknown():
    seen = []

    def read(symbol):
        seen.append(symbol)
        return {'symbol': symbol, 'state': 'normal'}
    assert current_trading('159915.SZ', 'ETF', now=NOW, sz_etf_reader=read)['state'] == 'normal'
    assert seen == ['159915.SZ']


@pytest.mark.parametrize('symbol,asset', [('000001.SZ', 'ETF'), ('159915.SZ', 'STOCK')])
def test_asset_mismatch_rejected_before_source_or_control_read(monkeypatch, symbol, asset):
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'isolated')
    monkeypatch.setattr(api, 'ThesisLedgerControlStore', lambda: pytest.fail('不应读取控制或启动来源'))
    app = FastAPI()
    app.include_router(api.router)
    path = '/thesis-ledger/market/trading-observation'
    response = TestClient(app).get(path, params={'symbol': symbol, 'assetType': asset},
                                   headers={'Authorization': 'Bearer isolated'})
    assert response.status_code == 422
