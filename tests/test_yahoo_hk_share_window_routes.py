"""真实保存HK原价经鉴权网关、日历及零量证据序列化。"""

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.thesis_ledger import router_v3
from src.services import thesis_ledger_provider_runtime as runtime_module
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime


def test_hk_share_window_wire_excludes_only_zero_volume_and_keeps_origin(tmp_path, monkeypatch):
    path = str(tmp_path / "hk-share-control.sqlite")
    monkeypatch.setenv("DATABASE_PATH", path)
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "hk-share-data-token")
    store = ThesisLedgerControlStore(path)
    runtime = ThesisLedgerProviderRuntime(store, adapters={"yfinance": object()})
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(runtime_module, "_runtime_database_path", path)
    key = {"kind": "bar", "market": "HK", "assetType": "STOCK", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "none"}
    target = {"providerId": "yfinance", "upstreamSource": "yahoo-hk-share-window"}
    store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "hk-share-policy",
        "revision": 1, "enabled": True, "routes": [{"key": key, "targets": [target]}]})
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    request = {"contractVersion": 3, "requestId": "hk-share-wire", "symbol": "00147.HK",
        "routeKey": key, "routeTarget": {**target, "routeIndex": 0}, "start": "2025-08-06", "end": "2025-08-11",
        "tradabilityMode": "assume-untradable-no-bar"}
    headers = {"Authorization": "Bearer hk-share-data-token"}
    with TestClient(app) as client:
        url = "/api/v3/thesis-ledger/market/bars"
        assert client.post(url, json=request).status_code == 401
        response = client.post(url, json=request, headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        capture = os.environ.get("THESIS_LEDGER_CAPTURE_HK_SHARE_WIRE")
        if capture:
            Path(capture).write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
        assert len(result["bars"]) == 3 and all(bar["amount"] is None for bar in result["bars"])
        assert result["sourcePriceBasis"]["adjustment"] == "none"
        assert result["coverageProof"]["calendar"]["market"] == "HK"
        assert result["coverageProof"]["listing"].get("firstTradingDate") is None
        assert result["provenance"]["upstreamSource"] == target["upstreamSource"]
        day = next(day for day in result["historicalTradabilityWindows"][0]["days"] if day["date"] == "2025-08-07")
        assert day["state"] == "assumed-untradable-no-bar" and day["excludedZeroVolumeObservation"]["volume"] == "0"
        assert client.post(url, json={**request, "start": "2025-08-05"}, headers=headers).status_code != 200
        assert client.post(url, json={**request, "symbol": "00700.HK"}, headers=headers).status_code != 200
