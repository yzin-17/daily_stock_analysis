"""生产港美来源经正式控制策略与鉴权HTTP，精确范围不借旧准入替代。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.thesis_ledger import router_v3
from src.services import thesis_ledger_provider_runtime as runtime_module
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from tests.test_thesis_ledger_multimarket_rules import request


@pytest.mark.parametrize("market", ["HK", "US"])
@pytest.mark.parametrize("capability", ["HISTORICAL_PUBLIC_FEES", "DAILY_TRADING_STATUS"])
def test_configured_market_source_uses_authenticated_runtime_without_admission(tmp_path, monkeypatch, market, capability):
    path = str(tmp_path / "p6-control.sqlite")
    monkeypatch.setenv("DATABASE_PATH", path)
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "p6-data-token")
    store = ThesisLedgerControlStore(path)
    runtime = ThesisLedgerProviderRuntime(store)
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(runtime_module, "_runtime_database_path", path)
    if capability == "DAILY_TRADING_STATUS":
        req = request(market, "HISTORICAL_TRADING_STATUS")
        target = {"providerId": "local-materials", "upstreamSource": "local-materials"}
        req.update(capabilityVersion=1, policyRevision=1, routeTarget={**target, "routeIndex": 0})
        req["routeKey"]["capability"] = capability
        url = "/api/v1/thesis-ledger/market/status-capabilities"
    else:
        req = request(market, capability)
        target = {key: req["routeTarget"][key] for key in ("providerId", "upstreamSource")}
        url = "/api/v1/thesis-ledger/market/public-fees"
    store.apply_policy_v3({"contractVersion": 1, "consumer": "thesis-ledger", "requestId": "p6-policy",
        "revision": 1, "enabled": True, "routes": [{"key": req["routeKey"], "enabled": True, "targets": [target]}]})
    assert store.get_route_admission_v3(key=req["routeKey"], target=target) is None
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v1")
    with TestClient(app) as client:
        assert client.post(url, json=req).status_code == 401
        response = client.post(url, json=req, headers={"Authorization": "Bearer p6-data-token"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["routeTarget"] == {**target, "routeIndex": 0}
        if capability == "DAILY_TRADING_STATUS":
            assert all(day["state"] == "unknown" for day in result["days"])
            assert any(day["intradayHalts"] for day in result["days"])
        wrong = {**req, "symbol": "00700.HK" if market == "HK" else "AAPL.US"}
        outside = client.post(url, json=wrong, headers={"Authorization": "Bearer p6-data-token"})
        assert outside.status_code == 422
        assert outside.json()["detail"]["error"]["code"] == "insufficient_coverage"
