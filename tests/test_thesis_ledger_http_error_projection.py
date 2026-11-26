"""生产全局HTTP处理保留Data信封及既有Control/通用错误行为。"""
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient as FastApiClient
import pytest

from api.middlewares.error_handler import add_error_handlers
from api.thesis_ledger_market_errors import market_data_error_v3
from src.services.thesis_ledger_market_errors_v3 import market_runtime_error
from src.services.thesis_ledger_provider_runtime import ProviderCallError, classify_provider_exception


def test_existing_daily_data_error_through_global_handler():
    app = FastAPI()
    add_error_handlers(app)

    @app.get('/daily')
    def daily():
        market_data_error_v3('insufficient_coverage', 422, 'daily-request')

    response = FastApiClient(app).get('/daily')
    assert response.status_code == 422
    assert response.json() == {'detail': {'contractVersion': 1, 'requestId': 'daily-request',
                                         'error': {'code': 'insufficient_coverage',
                                                   'message': '请求区间的数据覆盖不足'}}}


@pytest.mark.parametrize('code,status', [('rate_limited', 503), ('quota_unavailable', 503),
                                      ('permission_denied', 403), ('authentication_failed', 403)])
def test_source_restriction_is_not_lost_in_http_projection(code, status):
    error = ProviderCallError(code, 'private upstream message')
    assert market_runtime_error(error) == (code, status)
    assert classify_provider_exception(error) == code


@pytest.mark.parametrize('detail,expected', [
    ({'contractVersion': 1, 'code': 'STALE_REVISION', 'message': '原控制修订冲突',
      'requestId': 'control', 'diagnosticId': 'control'},
     {'error': 'http_error', 'message': '原控制修订冲突',
      'detail': {'contractVersion': 1, 'code': 'STALE_REVISION', 'message': '原控制修订冲突',
                 'requestId': 'control', 'diagnosticId': 'control'}}),
    ({'error': 'plain_error', 'message': '原普通错误'}, {'error': 'plain_error', 'message': '原普通错误'}),
    ('原字符串错误', {'error': 'http_error', 'message': '原字符串错误', 'detail': None}),
])
def test_existing_non_data_errors_unchanged(detail, expected):
    app = FastAPI()
    add_error_handlers(app)

    @app.get('/other')
    def other():
        raise HTTPException(status_code=409, detail=detail)

    response = FastApiClient(app).get('/other')
    assert response.status_code == 409
    assert response.json() == expected
