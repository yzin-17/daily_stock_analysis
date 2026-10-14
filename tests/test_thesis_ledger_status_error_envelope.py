"""独立能力真实HTTP错误遵守Data信封；不执行SDK或访问真实配置。"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient as FastApiClient
import pytest

from api.thesis_ledger_status_capabilities import build_status_capability_router
from api.middlewares.error_handler import add_error_handlers
from src.services.thesis_ledger_provider_runtime import ProviderCallError


def request():
    cases = json.loads((Path(__file__).parent / 'fixtures/market-status-capabilities-v1.shared.json').read_text())
    return deepcopy(cases['valid'][0]['request'])


@pytest.mark.parametrize('runtime_code,code,status,message', [
    ('upstream_unavailable', 'upstream_failure', 503, '行情上游暂时不可用'),
    ('NO_ELIGIBLE_PROVIDER', 'upstream_failure', 503, '行情上游暂时不可用'),
    ('unsupported_capability', 'unsupported_capability', 422, '来源不支持请求的数据能力'),
    ('insufficient_coverage', 'insufficient_coverage', 422, '请求区间的数据覆盖不足'),
    ('symbol_not_found', 'source_symbol_unrecognized', 404, '来源未识别请求的证券'),
    ('permission_denied', 'permission_denied', 403, '来源权限不足'),
    ('authentication_failed', 'authentication_failed', 403, '来源凭据未通过认证'),
    ('invalid_request', 'invalid_request', 422, '来源拒绝请求参数'),
])
def test_actual_endpoint_standard_error_and_request_correlation(monkeypatch, runtime_code, code, status, message):
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'synthetic-token')
    calls = []

    def execute(value):
        calls.append(value)
        raise ProviderCallError(runtime_code, 'synthetic-secret-and-vendor-body')

    app = FastAPI()
    add_error_handlers(app)
    app.include_router(build_status_capability_router(lambda: SimpleNamespace(execute_request=execute)))
    payload = request()
    response = FastApiClient(app).post('/market/status-capabilities', json=payload,
                                   headers={'authorization': 'Bearer synthetic-token', 'x-request-id': 'different-trace'})
    assert response.status_code == status
    assert response.json() == {'detail': {'contractVersion': 3, 'requestId': payload['requestId'],
                                         'error': {'code': code, 'message': message}}}
    assert len(calls) == 1 and calls[0].request_id == payload['requestId']
    assert 'synthetic-secret' not in response.text


def test_invalid_capability_parameters_rejected_before_runtime(monkeypatch):
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'synthetic-token')
    calls = []
    app = FastAPI()
    add_error_handlers(app)
    app.include_router(build_status_capability_router(lambda: SimpleNamespace(execute_request=lambda value: calls.append(value))))
    payload = request()
    payload['unexpectedParameter'] = True
    response = FastApiClient(app).post('/market/status-capabilities', json=payload,
                                   headers={'authorization': 'Bearer synthetic-token'})
    assert response.status_code == 422 and calls == []
    assert response.json()['detail'] == {'contractVersion': 3, 'requestId': payload['requestId'],
                                        'error': {'code': 'invalid_request', 'message': '来源拒绝请求参数'}}
