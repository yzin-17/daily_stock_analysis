"""保存的真实按股窗口经鉴权网关、实际日历与生产序列化读取。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.thesis_ledger import router_v3
from src.services import thesis_ledger_provider_runtime as runtime_module
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime


def test_share_window_authenticated_wire_keeps_scope_and_unknown_listing(tmp_path, monkeypatch):
    path = str(tmp_path / "share-window-control.sqlite")
    monkeypatch.setenv("DATABASE_PATH", path)
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "share-window-data-token")
    store = ThesisLedgerControlStore(path)
    runtime = ThesisLedgerProviderRuntime(store, adapters={"yfinance": object()})
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(runtime_module, "_runtime_database_path", path)
    key = {"kind": "bar", "market": "US", "assetType": "STOCK", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "none"}
    target = {"providerId": "yfinance", "upstreamSource": "yahoo-gme-share-window"}
    store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "share-policy",
        "revision": 1, "enabled": True, "routes": [{"key": key, "targets": [target]}]})
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    request = {"contractVersion": 3, "requestId": "share-window-wire", "symbol": "GME.US",
        "routeKey": key, "routeTarget": {**target, "routeIndex": 0}, "start": "2024-05-10", "end": "2024-05-24",
        "tradabilityMode": "assume-untradable-no-bar"}
    headers = {"Authorization": "Bearer share-window-data-token"}
    with TestClient(app) as client:
        url = "/api/v3/thesis-ledger/market/bars"
        assert client.post(url, json=request).status_code == 401
        response = client.post(url, json=request, headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        assert len(result["bars"]) == 11 and all(bar["amount"] is None for bar in result["bars"])
        assert result["sourcePriceBasis"]["adjustment"] == "none"
        assert result["coverageProof"]["calendar"]["market"] == "US"
        assert result["coverageProof"]["listing"].get("firstTradingDate") is None
        assert result["provenance"]["upstreamSource"] == target["upstreamSource"]
        assert client.post(url, json={**request, "start": "2024-04-26"}, headers=headers).status_code != 200
        assert client.post(url, json={**request, "symbol": "AAPL.US"}, headers=headers).status_code != 200
