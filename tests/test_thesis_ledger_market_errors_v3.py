"""完整窗口与图表端点保持相同安全错误，无供应商消息透传。"""

from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.services.thesis_ledger_provider_runtime import ProviderCallError


@pytest.mark.parametrize("endpoint", ["bars", "chart-bars"])
@pytest.mark.parametrize("raw,wire,status,message", [
    ("symbol_not_found", "source_symbol_unrecognized", 404, "来源未识别请求的证券"),
    ("invalid_request", "invalid_request", 422, "来源拒绝请求参数"),
    ("permission_denied", "permission_denied", 403, "来源权限不足"),
    ("authentication_failed", "authentication_failed", 403, "来源凭据未通过认证"),
    ("not_covered", "insufficient_coverage", 422, "请求区间的数据覆盖不足"),
    ("data_not_ready", "upstream_failure", 503, "行情上游暂时不可用"),
])
def test_existing_http_boundaries_preserve_source_error(endpoint, raw, wire, status, message, monkeypatch):
    from api import thesis_ledger
    from api.thesis_ledger_chart_v3 import router

    def fail(*args, **kwargs):
        raise ProviderCallError(raw, "sensitive-provider-message")

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-error-token")
    monkeypatch.setattr(thesis_ledger, "_market_data_v3_coverage_context", lambda request: {})
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime",
                        lambda: SimpleNamespace(execute_market_bars_v3=fail))
    request = {"contractVersion": 3, "requestId": "source-error-proof", "symbol": "600112.SH",
               "routeKey": {"kind": "bar", "market": "CN", "assetType": "STOCK", "capability": "DAILY_BAR",
                            "timeframe": "1d", "adjustment": "qfq"},
               "routeTarget": {"providerId": "hithink", "upstreamSource": "hithink-financial-api", "routeIndex": 0},
               "start": "2024-07-15", "end": "2024-07-19"}
    app = FastAPI()
    app.include_router(thesis_ledger.router_v3)
    app.include_router(router)
    if endpoint == "chart-bars":
        request["purpose"] = "interactive-chart"
    with TestClient(app) as client:
        response = client.post(f"/thesis-ledger/market/{endpoint}", json=request,
                               headers={"Authorization": "Bearer synthetic-error-token"})
    assert response.status_code == status
    assert response.json() == {"contractVersion": 3, "requestId": request["requestId"],
                               "error": {"code": wire, "message": message}}
    assert "sensitive-provider-message" not in response.text
