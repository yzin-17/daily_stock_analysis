"""既有规则入口经认证和全局处理仍返回安全且关联请求的 Data V3 错误。"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient as ApiClient

from api.middlewares.error_handler import add_error_handlers
from api.thesis_ledger_local_rules import build_rule_data_router
from src.services.thesis_ledger_provider_runtime import ProviderCallError


@pytest.mark.parametrize("capability,path", [
    ("HISTORICAL_PUBLIC_FEES", "/market/public-fees"),
    ("HISTORICAL_TRADING_STATUS", "/market/trading-status"),
])
@pytest.mark.parametrize("runtime_code,code,status", [
    ("NO_ELIGIBLE_PROVIDER", "upstream_failure", 503),
    ("upstream_unavailable", "upstream_failure", 503),
    ("not_admitted", "upstream_failure", 503),
    ("insufficient_coverage", "insufficient_coverage", 422),
    ("not_covered", "insufficient_coverage", 422),
    ("invalid_request", "invalid_request", 422),
    ("unsupported_capability", "unsupported_capability", 422),
    ("invalid_response", "invalid_response", 502),
    ("permission_denied", "permission_denied", 403),
    ("authentication_failed", "authentication_failed", 403),
    ("rate_limited", "rate_limited", 503),
    ("quota_unavailable", "quota_unavailable", 503),
])
def test_rule_errors_are_standard_and_do_not_expose_source_text(monkeypatch, capability, path,
                                                                runtime_code, code, status):
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "isolated-rule-error-token")

    def execute(request):
        raise ProviderCallError(runtime_code, "private upstream token=must-not-escape")

    app = FastAPI()
    add_error_handlers(app)
    app.include_router(build_rule_data_router(capability, path, "隔离规则", lambda: SimpleNamespace(execute_request=execute)))
    payload = {
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "isolated-rule-error",
        "symbol": "300058.SZ",
        "routeKey": {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": capability},
        "routeTarget": {"providerId": "local-materials", "upstreamSource": "local-materials", "routeIndex": 0},
        "range": {"start": "2025-11-16", "end": "2026-05-14"},
    }
    response = ApiClient(app).post(path, json=payload, headers={"Authorization": "Bearer isolated-rule-error-token"})
    assert response.status_code == status
    detail = response.json()["detail"]
    assert set(detail) == {"contractVersion", "requestId", "error"}
    assert detail["contractVersion"] == 3 and detail["requestId"] == payload["requestId"]
    assert detail["error"]["code"] == code
    assert "private" not in response.text and "must-not-escape" not in response.text
