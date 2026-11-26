"""当前停牌读取与功能开关的离线验证；禁止外网。"""

from datetime import datetime, timezone, timedelta
import json
from urllib.parse import urlencode
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services.thesis_ledger_current_trading import current_trading
from api import thesis_ledger_current_trading as api

NOW = datetime(2026, 11, 23, 2, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('不得访问真实来源'))


def stock_fetch(rows, *, count=None):
    def fetch(url, params):
        assert params['filter'] == '(MARKET="全部")(DATETIME=\'2026-11-23\')'
        body = {'success': True, 'code': 0,
                'result': {'count': len(rows) if count is None else count, 'pages': 1, 'data': rows}}
        return url + '?' + urlencode(params), json.dumps(body).encode()
    return fetch


def test_complete_stock_list_normal_halt_and_intraday_resume():
    fetch = stock_fetch([{'SECUCODE': '600036.SH', 'SUSPEND_START_TIME': '2026-11-23 09:30:00',
                          'SUSPEND_END_TIME': '2026-11-23 10:30:00'}])
    assert current_trading('600036.SH', 'STOCK', now=NOW, fetch=fetch)['state'] == 'suspended'
    normal = current_trading('600000.SH', 'STOCK', now=NOW, fetch=fetch)
    assert normal['state'] == 'normal' and normal['evidence']['complete']
    assert current_trading('600036.SH', 'STOCK', now=NOW + timedelta(hours=1), fetch=fetch)['state'] == 'normal'


def test_missing_page_and_wrong_identity_never_mean_normal():
    assert current_trading('600036.SH', 'STOCK', now=NOW, fetch=stock_fetch([], count=1))['state'] == 'unknown'
    assert current_trading('600036.SH', 'STOCK', now=NOW, fetch=stock_fetch([{'SECUCODE': 'wrong'}]))['state'] == 'unknown'


def etf_fetch(rows, total=None):
    def fetch(url, params):
        assert params['secCode'] == '510300' and params['endDate'] == '20261123'
        count = len(rows) if total is None else total
        return url + '?' + urlencode(params), json.dumps({
            'sqlId': 'SSE_PL_JYTS_TFPXX_JJ', 'isPagination': 'true',
            'actionErrors': [], 'fieldErrors': {}, 'result': rows,
            'pageHelp': {'pageNo': 1, 'pageSize': 25, 'total': count,
                         'pageCount': (count + 24) // 25, 'data': rows}}).encode()
    return fetch


def test_etf_empty_complete_query_and_subscription_halt():
    assert current_trading('510300.SH', 'ETF', now=NOW, fetch=etf_fetch([]))['state'] == 'normal'
    assert current_trading('510300.SH', 'ETF', now=NOW, fetch=etf_fetch([
        {'secCode': '510300', 'dateSource': '1'}]))['state'] == 'normal'
    suspended = current_trading('510300.SH', 'ETF', now=NOW, fetch=etf_fetch([{
        'secCode': '510300', 'dateSource': '2', 'startStopType': 'LSTP', 'stopTime': 'WH',
        'startStopDate': '2026-11-23', 'endStopDate': '2026-11-23'}]))
    assert suspended['state'] == 'suspended'


def test_uncovered_etf_and_unknown_interval():
    assert current_trading('159919.SZ', 'ETF', now=NOW)['state'] == 'unknown'
    assert current_trading('510300.SH', 'ETF', now=NOW, fetch=etf_fetch([{
        'secCode': '510300', 'dateSource': '2', 'startStopType': 'LSTP', 'stopTime': '?',
        'startStopDate': '2026-11-23', 'endStopDate': ''}]))['state'] == 'unknown'


@pytest.mark.parametrize('disabled', ['master', 'asset', 'none'])
def test_data_http_respects_master_and_asset_switch(monkeypatch, disabled):
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'isolated')
    policy = {'contractVersion': 1, 'enabled': disabled != 'master', 'revision': 1,
              'sourceDesiredRevision': 1, 'routes': [{
                  'key': {'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': 'REALTIME_QUOTE'},
                  'enabled': disabled != 'asset', 'targets': [{
                      'providerId': 'hithink', 'upstreamSource': 'fund-market-snapshot',
                      'routeIndex': 0, 'eligible': True, 'reason': None}]}]}
    monkeypatch.setattr(api, 'ThesisLedgerControlStore', lambda: SimpleNamespace(effective_policy_v3=lambda: policy))
    calls = []

    def read(symbol, asset):
        calls.append(symbol)
        return current_trading(symbol, asset, now=NOW, fetch=etf_fetch([]))
    monkeypatch.setattr(api, 'current_trading', read)
    app = FastAPI()
    app.include_router(api.router, prefix='/api/v1')
    client = TestClient(app)
    path = '/api/v1/thesis-ledger/market/trading-observation?symbol=510300.SH&assetType=ETF'
    assert client.get(path).status_code == 401
    response = client.get(path, headers={'Authorization': 'Bearer isolated'})
    assert response.status_code == (200 if disabled == 'none' else 503)
    assert len(calls) == (1 if disabled == 'none' else 0)
