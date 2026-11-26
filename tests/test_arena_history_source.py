"""历史全集生命周期、来源控制及专用分钟 HTTP 合同。"""

from types import SimpleNamespace
from unittest.mock import Mock
import json
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.thesis_ledger_arena_history import build_arena_history_router
from src.services.thesis_ledger_arena_history import HistoryRequest, project_catalog, read_catalog, source_failure
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply  # noqa: F401
from tests.test_thesis_ledger_futu_capability_path import configure, worker_transport
from tests.test_futu_minute import sdk, row


def datasets():
    result = {key: [] for key in ('stock_L', 'stock_D', 'stock_P', 'etf_L', 'etf_D', 'fund_L', 'fund_D')}
    result['stock_L'] = [{'ts_code': '920001.BJ', 'list_date': '20200101', 'delist_date': None},
                         {'ts_code': '600001.SH', 'list_date': '20261103', 'delist_date': None}]
    result['stock_D'] = [{'ts_code': '000001.SZ', 'list_date': '20200101', 'delist_date': '20261104'}]
    result['etf_D'] = [{'ts_code': '510300.SH', 'list_date': '20200101'}]
    result['fund_D'] = [{'ts_code': '510300.SH', 'list_date': '20200101', 'delist_date': '20261103'}]
    return result


def test_structured_permission_code_is_not_mistaken_for_frequency_help_text():
    from data_provider.tushare_http_client import TushareApiError
    permission = source_failure(TushareApiError('权限不足，请参阅访问频率说明', api_code=2002), 'stock_basic')
    assert permission.code == 'permission_denied'
    limited = source_failure(TushareApiError('每分钟最多访问该接口1次', api_code=2002), 'stock_basic')
    assert limited.code == 'rate_limited' and '每分钟最多1次' in str(limited)


def test_catalog_includes_later_delisting_excludes_future_listings_and_preserves_historical_identity():
    data = datasets()
    early = project_catalog(data, '2026-11-02')
    assert {i['symbol'] for i in early} == {'920001.BJ', '000001.SZ', '510300.SH'}
    assert all(i['name'] == i['symbol'] for i in early)
    assert {i['symbol'] for i in project_catalog(data, '2026-11-04')} == {'920001.BJ', '600001.SH'}
    del data['fund_D'][0]['delist_date']
    with pytest.raises(ProviderCallError, match='生效日期'):
        project_catalog(data, '2026-11-02')


@pytest.mark.parametrize('change', ['missing_etf_dates', 'missing_bj', 'duplicate'])
def test_incomplete_or_ambiguous_catalog_never_becomes_full_market(change):
    data = datasets()
    if change == 'missing_etf_dates':
        data['fund_D'] = []
    elif change == 'missing_bj':
        data['stock_L'] = data['stock_L'][1:]
    else:
        data['stock_P'] = data['stock_L'][:1]
    with pytest.raises(ProviderCallError):
        project_catalog(data, '2026-11-02')


def test_disabled_catalog_does_not_construct_adapter():
    store = Mock()
    store.provider_registry.return_value = [{'providerId': 'tushare', 'enabled': False, 'configVersion': 5}]
    runtime = SimpleNamespace(store=store, _adapter=Mock())
    with pytest.raises(ProviderCallError, match='未启用'):
        read_catalog(runtime, HistoryRequest(contractVersion=1, consumer='thesis-ledger', requestId='x', market='CN', date='2026-11-02'))
    runtime._adapter.assert_not_called()


def test_catalog_download_preserves_source_archive_and_checks_configuration_epoch(monkeypatch, tmp_path):
    data = datasets()
    for key, rows in data.items():
        for item in rows:
            item['status' if key.startswith('fund') else 'list_status'] = key[-1]
    # 上市 ETF 集合也必须存在，且按完整代码关联场内基金。
    data['etf_L'] = [{'ts_code': '510500.SH', 'list_date': '20200101', 'list_status': 'L'}]
    data['fund_L'] = [{'ts_code': '510500.SH', 'list_date': '20200101', 'delist_date': None, 'status': 'L'}]
    client = Mock()
    def query(api, **parameters):
        state = parameters.get('list_status', parameters.get('status'))
        return pd.DataFrame(data[api.split('_')[0] + '_' + state], columns=parameters['fields'].split(','))
    client.query.side_effect = query
    budget = SimpleNamespace(client=client, before_call=Mock(), check_complete=Mock())
    monkeypatch.setattr('data_provider.tushare_history_request.TushareHistoryRequest', lambda *args: budget)
    store = Mock(database_path=str(tmp_path / 'control.db'))
    store.provider_registry.return_value = [{'providerId': 'tushare', 'enabled': True, 'configVersion': 5}]
    store.effective_policy_v3.return_value = {'enabled': True}
    store.provider_credential_snapshot.return_value = 'synthetic-snapshot'
    runtime = SimpleNamespace(store=store, _adapter=Mock())
    request = HistoryRequest(contractVersion=1, consumer='thesis-ledger', requestId='catalog', market='CN', date='2026-11-02')
    result = read_catalog(runtime, request)
    assert client.query.call_count == 7 and len(result['items']) == 4
    assert result['observedAt'] > request.date and (tmp_path / result['archiveRef']).exists()
    assert result['providerVersion'] == 5
    client.query.reset_mock(side_effect=True)
    client.query.side_effect = Exception('权限不足 synthetic secret must not escape')
    with pytest.raises(ProviderCallError) as error:
        read_catalog(runtime, request)
    assert error.value.code == 'permission_denied' and 'secret' not in str(error.value)
    assert client.query.call_count == 1


def test_historical_minute_uses_configured_route_real_clock_and_persistent_archive(monkeypatch, store):
    configure(store)
    value, ctx = sdk([(0, pd.DataFrame([row(code='SH.600000'), row('2026-05-18 09:31:00', code='SH.600000')]), None)])
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{'code': 'SH.600000', 'stock_type': 'STOCK'}])
    worker_transport(monkeypatch, value)
    key = {'kind': 'bar', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'MINUTE_BAR', 'timeframe': '1m', 'adjustment': 'none'}
    apply(store, key, {'providerId': 'futu', 'upstreamSource': 'futu'})
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'synthetic-history-token')
    app = FastAPI()
    app.include_router(build_arena_history_router(lambda: ThesisLedgerProviderRuntime(store)))
    client = TestClient(app)
    payload = {'contractVersion': 1, 'consumer': 'thesis-ledger', 'requestId': 'history', 'market': 'CN',
               'date': '2026-05-18', 'symbol': '600000.SH', 'assetType': 'STOCK'}
    assert client.post('/arena/history/minutes', json=payload).status_code == 401
    response = client.post('/arena/history/minutes', json=payload, headers={'authorization': 'Bearer synthetic-history-token'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['kind'] == 'reconstructed-minutes' and len(result['value']['rows']) == 2
    assert result['value']['observedAt'] > '2026-05-19' and result['value']['timestampConvention'] == 'interval-end'
    from pathlib import Path
    archived = json.loads((Path(store.database_path).parent / result['archiveRef']).read_text())
    assert archived['value'] == result['value']
    assert archived['pagination'] == {'requestedStart': '2026-05-18', 'requestedEnd': '2026-05-18',
                                      'pagesFetched': 1, 'continuationPending': False}
    assert 'pagination' not in result['value']
    ctx.close.assert_called_once()
