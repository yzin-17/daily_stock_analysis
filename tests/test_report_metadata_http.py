"""研报 HTTP、长期路由就绪和控制变化；不连接外部来源。"""

from dataclasses import replace
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient as HttpClient
import pytest

from api.thesis_ledger_auth import require_contract_token
from api.thesis_ledger_report_metadata import build_report_metadata_router
from data_provider.eastmoney_report_metadata import BINDING, project_page
from data_provider import eastmoney_report_metadata as source
from src.services.thesis_ledger_price_route_access import configured_data_route
from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError, ThesisLedgerDataRequest, ThesisLedgerProviderRuntime,
)
from src.services import thesis_ledger_report_metadata_query as bridge
from tests.test_configured_market_routes import apply, no_admissions, store
from tests.test_report_metadata_page import payload, Response


BODY = {'contractVersion': 1, 'consumer': 'thesis-ledger', 'requestId': 'http-metadata',
        'symbol': '600519.SH', 'assetType': 'STOCK', 'start': '2026-08-01', 'end': '2026-10-23',
        'page': 1, 'pageSize': 2, 'requestBudgetMs': 3000}
KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'RESEARCH_REPORT_METADATA'}
TARGET = {'providerId': 'akshare', 'upstreamSource': 'eastmoney', 'routeIndex': 0, 'eligible': True, 'reason': None}


def request(**changes):
    body = {**BODY, **changes}
    return ThesisLedgerDataRequest('RESEARCH_REPORT_METADATA', body['symbol'], start=body['start'], end=body['end'],
        instrument_type='STOCK', request_id=body['requestId'],
        parameters={key: body[key] for key in ['page', 'pageSize', 'requestBudgetMs']})


def runtime(targets=None, enabled=True):
    policy = {'contractVersion': 1, 'enabled': True, 'revision': 3, 'sourceDesiredRevision': 3,
              'routes': [{'key': KEY, 'targets': [TARGET] if targets is None else targets}]}
    registry = Mock(return_value=[{'providerId': 'akshare', 'enabled': enabled, 'configVersion': 1}])
    return SimpleNamespace(store=SimpleNamespace(database_path=':memory:', effective_policy_v3=lambda: policy,
        provider_registry=registry), _current_market_v3_admission=Mock(return_value=None))


def empty_page(req):
    return project_page({'hits': 0, 'size': 2, 'TotalPage': 0, 'pageNo': 1, 'data': []},
                        bridge.ReportMetadataRequest.from_request(req), '2026-10-22T16:27:04Z')


def client(service):
    app = FastAPI()
    app.dependency_overrides[require_contract_token] = lambda: None
    app.include_router(build_report_metadata_router(lambda: service), prefix='/api/v1/thesis-ledger')
    return HttpClient(app)


@pytest.mark.parametrize('change', [{'pageSize': 21}, {'assetType': 'ETF'}, {'code': '600519'},
    {'requestBudgetMs': True}, {'symbol': '600519'}, {'start': '2026-11-01'}])
def test_invalid_http_request_never_executes_runtime(change):
    service = SimpleNamespace(execute_request=Mock())
    result = client(service).post('/api/v1/thesis-ledger/market/research-report-metadata', json={**BODY, **change})
    assert result.status_code == 422
    detail = result.json()['detail']
    assert set(detail) == {'contractVersion', 'requestId', 'error'}
    assert detail['requestId'] == BODY['requestId']
    assert detail['error']['code'] == 'invalid_request'
    service.execute_request.assert_not_called()


@pytest.mark.parametrize('symbol', ['600519.SH', '000001.SZ'])
def test_http_accepts_supported_stocks_without_date_filters(symbol):
    req = request(symbol=symbol, start=None, end=None)
    value = empty_page(req).model_dump(mode='json')
    service = SimpleNamespace(execute_request=Mock(return_value=SimpleNamespace(value=value,
        effective_revision=3, provider='akshare', upstream_source='eastmoney', route_index=0)))
    body = {key: val for key, val in {**BODY, 'symbol': symbol}.items() if key not in ('start', 'end')}
    result = client(service).post('/api/v1/thesis-ledger/market/research-report-metadata', json=body)
    assert result.status_code == 200
    assert result.json()['start'] is None and result.json()['end'] is None
    actual = service.execute_request.call_args.args[0]
    assert actual.start is None and actual.end is None


def test_http_preserves_source_empty_page_and_control_provenance():
    value = empty_page(request()).model_dump(mode='json')
    service = SimpleNamespace(execute_request=Mock(return_value=SimpleNamespace(value=value,
        effective_revision=3, provider='akshare', upstream_source='eastmoney', route_index=0)))
    result = client(service).post('/api/v1/thesis-ledger/market/research-report-metadata', json=BODY)
    assert result.status_code == 200
    body = result.json()
    assert body['historyComplete'] is False and body['items'] == []
    assert body['policyRevision'] == 3 and body['routeTarget']['upstreamSource'] == 'eastmoney'


def test_http_reports_missing_route_without_requesting_another_source():
    service = SimpleNamespace(execute_request=Mock(side_effect=ProviderCallError('NO_ELIGIBLE_PROVIDER', '未配置路由')))
    result = client(service).post('/api/v1/thesis-ledger/market/research-report-metadata', json=BODY)
    assert result.status_code == 503 and result.json()['detail']['error']['code'] == 'upstream_failure'
    assert service.execute_request.call_count == 1


@pytest.mark.parametrize('code,status,expected', [
    ('upstream_failure', 503, 'upstream_failure'), ('invalid_response', 502, 'invalid_response'),
    ('permission_denied', 403, 'permission_denied'), ('authentication_failed', 403, 'authentication_failed'),
    ('invalid_request', 422, 'invalid_request'), ('not_admitted', 503, 'upstream_failure'),
])
def test_provider_errors_use_the_standard_data_v3_envelope(code, status, expected):
    service = SimpleNamespace(execute_request=Mock(side_effect=ProviderCallError(code, '不得暴露的来源原文')))
    result = client(service).post('/api/v1/thesis-ledger/market/research-report-metadata', json=BODY)
    assert result.status_code == status
    detail = result.json()['detail']
    assert set(detail) == {'contractVersion', 'requestId', 'error'}
    assert detail['contractVersion'] == 1 and detail['requestId'] == BODY['requestId']
    assert set(detail['error']) == {'code', 'message'} and detail['error']['code'] == expected
    assert '不得暴露' not in result.text and service.execute_request.call_count == 1


@pytest.mark.parametrize('request_id', [None, True, '', 'x' * 129])
def test_invalid_request_identifier_still_has_a_valid_error_envelope(request_id):
    from uuid import UUID
    service = SimpleNamespace(execute_request=Mock())
    result = client(service).post('/api/v1/thesis-ledger/market/research-report-metadata',
                                  json={**BODY, 'requestId': request_id})
    assert result.status_code == 422
    assert str(UUID(result.json()['detail']['requestId'])) == result.json()['detail']['requestId']
    service.execute_request.assert_not_called()


@pytest.mark.parametrize('legacy', [None, {'admissionState': 'expired'},
    {'admissionState': 'admitted', 'scopeSymbols': ['600519.SH'], 'scopeDateTo': '2026-10-23'}])
def test_legacy_acceptance_scope_or_expiry_does_not_block_other_stocks(legacy):
    rt = runtime()
    rt._current_market_v3_admission.return_value = legacy
    req = request(symbol='000001.SZ', start=None, end=None)
    with patch.object(bridge.CAPABILITY_SOURCES, 'query', return_value=empty_page(req)) as consume:
        operation = bridge.report_metadata_operation(req, ProviderCallError, rt)
        actual = operation('akshare', None, 'eastmoney')
    assert configured_data_route(KEY, TARGET) is True
    assert actual['symbol'] == '000001.SZ' and actual['historyComplete'] is False
    assert consume.call_count == 1
    rt._current_market_v3_admission.assert_not_called()


@pytest.mark.parametrize('rt', [runtime(enabled=False), runtime([]), runtime([{**TARGET, 'eligible': False}])])
def test_disabled_source_or_revoked_route_prevents_consumption(rt):
    with patch.object(bridge.CAPABILITY_SOURCES, 'query') as consume:
        with pytest.raises(ProviderCallError):
            bridge.report_metadata_operation(request(), ProviderCallError, rt)
    consume.assert_not_called()


def test_two_eligible_targets_are_rejected_without_upstream_calls():
    other = {**TARGET, 'providerId': 'other', 'routeIndex': 1}
    with patch.object(bridge.CAPABILITY_SOURCES, 'query') as consume:
        with pytest.raises(ProviderCallError) as error:
            bridge.report_metadata_operation(request(), ProviderCallError, runtime([TARGET, other]))
    assert error.value.code == 'not_admitted'
    consume.assert_not_called()


@pytest.mark.parametrize('change', ['disabled', 'config', 'policy'])
def test_control_change_after_read_discards_the_result(change):
    rt = runtime()

    def consume(*_args):
        if change == 'policy':
            rt.store.effective_policy_v3()['revision'] += 1
            rt.store.effective_policy_v3()['routes'] = []
        else:
            row = rt.store.provider_registry.return_value[0]
            row['configVersion'] += 1
            if change == 'disabled':
                row['enabled'] = False
        return empty_page(request())

    with patch.object(bridge.CAPABILITY_SOURCES, 'query', side_effect=consume) as fetch:
        operation = bridge.report_metadata_operation(request(), ProviderCallError, rt)
        with pytest.raises(ProviderCallError) as error:
            operation('akshare', None, 'eastmoney')
    assert error.value.code == 'not_admitted' and fetch.call_count == 1


def test_binding_change_after_read_discards_the_result():
    changed = replace(BINDING, source_revision='changed')
    with patch.object(bridge.CAPABILITY_SOURCES, 'resolve', side_effect=[BINDING, BINDING, changed]), \
            patch.object(bridge.CAPABILITY_SOURCES, 'query', return_value=empty_page(request())) as consume:
        operation = bridge.report_metadata_operation(request(), ProviderCallError, runtime())
        with pytest.raises(ProviderCallError) as error:
            operation('akshare', None, 'eastmoney')
    assert error.value.code == 'not_admitted' and consume.call_count == 1


def test_stale_result_revision_is_rejected():
    value = empty_page(request()).model_copy(update={'adapterRevision': 'old'})
    with patch.object(bridge.CAPABILITY_SOURCES, 'query', return_value=value) as consume:
        operation = bridge.report_metadata_operation(request(), ProviderCallError, runtime())
        with pytest.raises(ProviderCallError) as error:
            operation('akshare', None, 'eastmoney')
    assert error.value.code == 'invalid_response' and consume.call_count == 1


@pytest.mark.parametrize('symbol', ['600519.SH', '000001.SZ'])
def test_real_control_registry_executor_and_http_need_no_temporary_admission(store, symbol):
    target = {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}
    assert apply(store, KEY, target)['effective']['routes'][0]['targets'][0]['eligible']
    rt = ThesisLedgerProviderRuntime(store, adapters={'akshare': SimpleNamespace()})
    data = payload()
    for row in data['data']:
        row['stockCode'] = symbol[:6]
    body = {key: val for key, val in {**BODY, 'symbol': symbol}.items() if key not in ('start', 'end')}
    with patch.object(source, 'open_list', return_value=Response(json.dumps(data).encode())) as fetch, \
            patch.object(rt, '_current_market_v3_admission', side_effect=AssertionError('不能读取临时准入')):
        result = client(rt).post('/api/v1/thesis-ledger/market/research-report-metadata', json=body)
    assert result.status_code == 200, result.text
    assert result.json()['metadataVersion'] == 'market-external-report-metadata-v1'
    assert result.json()['symbol'] == symbol and result.json()['start'] is None and result.json()['end'] is None
    assert fetch.call_count == 1
    params = parse_qs(urlsplit(fetch.call_args.args[0].full_url).query, keep_blank_values=True)
    assert params['beginTime'] == [''] and params['endTime'] == ['']
    no_admissions(store)
