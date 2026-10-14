"""真实绑定、原 SQLite Control/Runtime、原认证 HTTP 入口的隔离闭环。"""

from contextlib import contextmanager
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_reference_data import build_reference_data_router
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime, ThesisLedgerDataRequest, ProviderCallError
from src.services.thesis_ledger_status_capabilities_v1 import RESULTS

DAY = "2024-07-15"
SYMBOL = "600000.SH"
TARGET = {"providerId": "baostock", "upstreamSource": "baostock"}


class Result:
    error_code = "0"
    error_msg = ""

    def __init__(self, fields, rows):
        self.fields, self.rows, self.index = fields, rows, -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


class Adapter:
    def __init__(self):
        self.calls = []
        self.basic_failure = False
        self.history_failure = False
        self.on_history = lambda: None

    def _convert_stock_code(self, symbol):
        code, market = symbol.split('.')
        return market.lower() + '.' + code

    @contextmanager
    def _baostock_session(self):
        self.calls.append('session')
        try:
            yield self
        finally:
            self.calls.append('released')

    def query_stock_basic(self, code):
        self.calls.append('basic')
        if self.basic_failure:
            raise RuntimeError('含敏感原始 SDK 信息')
        return Result(['code', 'ipoDate', 'outDate', 'type'], [[code, '1999-11-10', '', '1']])

    def query_trade_dates(self, **kwargs):
        self.calls.append('calendar')
        assert kwargs == {'start_date': DAY, 'end_date': DAY}
        return Result(['calendar_date', 'is_trading_day'], [[DAY, '1']])

    def query_history_k_data_plus(self, code, fields, **kwargs):
        self.calls.append('history')
        assert fields == 'date,code,tradestatus,isST'
        assert kwargs['frequency'] == 'd' and kwargs['adjustflag'] == '3'
        self.on_history()
        if self.history_failure:
            raise RuntimeError('含敏感原始 SDK 信息')
        return Result(['date', 'code', 'tradestatus', 'isST'], [[DAY, code, '0', '1']])


def route(capability, enabled=True):
    return {'key': {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': capability},
            'targets': [TARGET], 'enabled': enabled}


def setup(monkeypatch, tmp_path, routes=None):
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'isolated-data-token')
    store = ThesisLedgerControlStore(tmp_path / 'capability.db')
    policy = {'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'isolated-policy', 'revision': 1,
              'enabled': True, 'routes': routes or [route(name) for name in ('DAILY_TRADING_STATUS', 'RISK_WARNING', 'SECURITY_BASIC_DATES')]}
    store.apply_policy_v3(policy)
    adapter = Adapter()
    # SDK替身位于新子进程传输边界内；保留原解析/会话/失败断言，禁止测试联网。
    def child_response(payload):
        from datetime import date
        from src.services.thesis_ledger_baostock_daily_status import read_baostock_daily_status
        from src.services.thesis_ledger_baostock_lifecycle import read_baostock_lifecycle
        if payload['mode'] == 'daily-status':
            return read_baostock_daily_status(adapter, symbol=payload['symbol'],
                start=date.fromisoformat(payload['start']), end=date.fromisoformat(payload['end']))
        assert payload['mode'] == 'basic-dates'
        return read_baostock_lifecycle(adapter, payload['symbol'])
    monkeypatch.setattr('data_provider.baostock_query_process._read', child_response)
    runtime = ThesisLedgerProviderRuntime(store, adapters={'baostock': adapter})
    app = FastAPI()
    app.include_router(build_reference_data_router(lambda: runtime), prefix='/api/v3/thesis-ledger')
    return store, policy, adapter, runtime, TestClient(app)


def payload(capability):
    value = {'contractVersion': 3, 'capabilityVersion': 1, 'consumer': 'thesis-ledger', 'requestId': 'isolated-query',
             'symbol': SYMBOL, 'routeKey': route(capability)['key'], 'routeTarget': {**TARGET, 'routeIndex': 0}, 'policyRevision': 1}
    if capability != 'SECURITY_BASIC_DATES':
        value['range'] = {'start': DAY, 'end': DAY}
    if capability == 'RISK_WARNING':
        value['detail'] = {'kind': 'coarseST'}
    return value


def post(client, value):
    return client.post('/api/v3/thesis-ledger/market/status-capabilities', json=value,
                       headers={'authorization': 'Bearer isolated-data-token'})


@pytest.mark.parametrize('capability', ['DAILY_TRADING_STATUS', 'RISK_WARNING', 'SECURITY_BASIC_DATES'])
def test_production_binding_through_original_control_runtime_and_http(monkeypatch, tmp_path, capability):
    store, policy, adapter, runtime, client = setup(monkeypatch, tmp_path)
    response = post(client, payload(capability))
    assert response.status_code == 200, response.text
    result = RESULTS.validate_python(response.json())
    assert result.routeKey.capability == capability
    assert result.routeTarget.providerId == 'baostock'
    assert result.policyRevision == 1
    if capability == 'SECURITY_BASIC_DATES':
        assert result.dates.ipoDate == '1999-11-10'
        assert result.dates.outDate is None
        assert result.dates.outDateStatus == 'notProvided'
        assert adapter.calls == ['session', 'basic', 'released']
        assert 'range' not in response.json() and 'coverageProof' not in response.json()
    else:
        assert adapter.calls == ['session', 'calendar', 'history', 'released']
        assert 'dates' not in response.json()
        assert result.coverage.complete
        if capability == 'DAILY_TRADING_STATUS':
            assert result.days[0].state == 'suspended'
        else:
            assert result.days[0].coarseST == 'flagged'
            assert result.days[0].delisting == result.days[0].other == 'unknown'


def test_disabled_function_makes_no_upstream_call_and_preserves_choices(monkeypatch, tmp_path):
    store, policy, adapter, runtime, client = setup(monkeypatch, tmp_path, [route('SECURITY_BASIC_DATES', False)])
    response = post(client, payload('SECURITY_BASIC_DATES'))
    assert response.status_code == 503
    assert not adapter.calls
    assert store.policy_projection_v3()['desired']['routes'][0]['targets'] == [TARGET]


def test_wrong_pin_and_stale_policy_are_rejected_before_sdk(monkeypatch, tmp_path):
    store, policy, adapter, runtime, client = setup(monkeypatch, tmp_path)
    value = payload('SECURITY_BASIC_DATES')
    value['routeTarget']['routeIndex'] = 1
    assert post(client, value).status_code == 503
    value = payload('SECURITY_BASIC_DATES')
    value['policyRevision'] = 2
    assert post(client, value).status_code == 503
    assert not adapter.calls


def test_coarse_source_does_not_call_sdk_for_precise_risk(monkeypatch, tmp_path):
    store, policy, adapter, runtime, client = setup(monkeypatch, tmp_path)
    value = payload('RISK_WARNING')
    value['detail'] = {'kind': 'riskCategories', 'categories': ['delisting', 'other']}
    response = post(client, value)
    assert response.status_code == 422, response.text
    assert response.json()['detail'] == {
        'contractVersion': 3, 'requestId': 'isolated-query',
        'error': {'code': 'unsupported_capability', 'message': '来源不支持请求的数据能力'},
    }
    assert not adapter.calls


def test_basic_dates_failure_remains_independent_from_valid_status(monkeypatch, tmp_path):
    store, policy, adapter, runtime, client = setup(monkeypatch, tmp_path)
    adapter.basic_failure = True
    dates = post(client, payload('SECURITY_BASIC_DATES'))
    assert dates.status_code == 200
    value = dates.json()
    assert value['dates']['ipoDateStatus'] == value['dates']['outDateStatus'] == 'unavailable'
    assert value['evidence'] == []
    assert '敏感' not in dates.text
    state = post(client, payload('DAILY_TRADING_STATUS'))
    assert state.status_code == 200 and state.json()['days'][0]['state'] == 'suspended'


def test_policy_change_during_upstream_call_isolates_old_result(monkeypatch, tmp_path):
    store, policy, adapter, runtime, client = setup(monkeypatch, tmp_path)

    def disable():
        changed = deepcopy(policy)
        changed['revision'] = 2
        changed['routes'][0]['enabled'] = False
        store.apply_policy_v3(changed)

    adapter.on_history = disable
    assert post(client, payload('DAILY_TRADING_STATUS')).status_code != 200
    before = adapter.calls[:]
    value = payload('DAILY_TRADING_STATUS')
    value['policyRevision'] = 2
    assert post(client, value).status_code != 200
    assert adapter.calls == before


def test_outer_runtime_request_cannot_hide_different_symbol(monkeypatch, tmp_path):
    store, policy, adapter, runtime, client = setup(monkeypatch, tmp_path)
    value = payload('SECURITY_BASIC_DATES')
    with pytest.raises(ProviderCallError, match='未通过合同'):
        runtime.execute_request(ThesisLedgerDataRequest('SECURITY_BASIC_DATES', '000995.SZ', instrument_type='STOCK',
                                                       request_id=value['requestId'], parameters=value))
    assert not adapter.calls


def test_provider_disable_reenable_during_read_does_not_publish_previous_generation(monkeypatch, tmp_path):
    store, policy, adapter, runtime, client = setup(monkeypatch, tmp_path)

    def flip():
        first = store.save_provider_config('baostock', {'requestId': 'isolated-disable', 'enabled': False, 'expectedConfigVersion': 0})
        store.save_provider_config('baostock', {'requestId': 'isolated-restore', 'enabled': True,
                                               'expectedConfigVersion': first['configVersion']})

    adapter.on_history = flip
    response = post(client, payload('DAILY_TRADING_STATUS'))
    assert response.status_code != 200
    assert store.effective_policy_v3()['routes'][0]['targets'][0]['eligible']
    adapter.on_history = lambda: None
    assert post(client, payload('DAILY_TRADING_STATUS')).status_code == 200


def test_state_and_coarse_risk_share_flags_but_rebuild_request_identity(monkeypatch, tmp_path):
    store, policy, adapter, runtime, client = setup(monkeypatch, tmp_path)
    state = post(client, payload('DAILY_TRADING_STATUS')).json()
    risk_request = payload('RISK_WARNING')
    risk_request['requestId'] = 'separate-risk-consumer'
    risk = post(client, risk_request).json()
    assert adapter.calls == ['session', 'calendar', 'history', 'released']
    assert risk['requestId'] == 'separate-risk-consumer' and state['requestId'] == 'isolated-query'
    assert risk['days'][0]['coarseST'] == 'flagged' and state['days'][0]['state'] == 'suspended'
    assert risk['evidence'][0]['sha256'] == state['evidence'][0]['sha256']
    post(client, payload('SECURITY_BASIC_DATES'))
    assert adapter.calls[-3:] == ['session', 'basic', 'released']


def test_cache_hit_cannot_bypass_disabled_route_or_new_control_epoch(monkeypatch, tmp_path):
    store, policy, adapter, runtime, client = setup(monkeypatch, tmp_path)
    assert post(client, payload('DAILY_TRADING_STATUS')).status_code == 200
    before = adapter.calls[:]
    changed = deepcopy(policy)
    changed.update(revision=2, requestId='isolated-feature-disable')
    changed['routes'][0]['enabled'] = False
    store.apply_policy_v3(changed)
    value = payload('DAILY_TRADING_STATUS')
    value['policyRevision'] = 2
    assert post(client, value).status_code != 200 and adapter.calls == before
    changed.update(revision=3, requestId='isolated-feature-restore')
    changed['routes'][0]['enabled'] = True
    store.apply_policy_v3(changed)
    value['policyRevision'] = 3
    assert post(client, value).status_code == 200
    assert adapter.calls.count('history') == 2
    disabled = store.save_provider_config('baostock', {'requestId': 'isolated-provider-disable', 'enabled': False, 'expectedConfigVersion': 0})
    before = adapter.calls[:]
    assert post(client, value).status_code != 200 and adapter.calls == before
    store.save_provider_config('baostock', {'requestId': 'isolated-provider-restore', 'enabled': True,
                                          'expectedConfigVersion': disabled['configVersion']})
    assert post(client, value).status_code == 200 and adapter.calls.count('history') == 3
