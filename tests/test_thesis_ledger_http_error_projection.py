"""生产全局HTTP处理保留Data信封及既有Control/通用错误行为。"""
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient as FastApiClient
import pytest

from api.middlewares.error_handler import add_error_handlers
from api.thesis_ledger_market_errors import market_data_error_v3


def test_existing_daily_data_error_through_global_handler():
    app = FastAPI()
    add_error_handlers(app)

    @app.get('/daily')
    def daily():
        market_data_error_v3('insufficient_coverage', 422, 'daily-request')

    response = FastApiClient(app).get('/daily')
    assert response.status_code == 422
    assert response.json() == {'detail': {'contractVersion': 3, 'requestId': 'daily-request',
                                         'error': {'code': 'insufficient_coverage',
                                                   'message': '请求区间的数据覆盖不足'}}}


@pytest.mark.parametrize('detail,expected', [
    ({'contractVersion': 3, 'code': 'STALE_REVISION', 'message': '原控制修订冲突',
      'requestId': 'control', 'diagnosticId': 'control'},
     {'error': 'http_error', 'message': '原控制修订冲突',
      'detail': {'contractVersion': 3, 'code': 'STALE_REVISION', 'message': '原控制修订冲突',
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
