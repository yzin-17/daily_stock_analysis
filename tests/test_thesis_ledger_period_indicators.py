"""同一冻结 payload 的真实指标计算、HTTP 和降级合同验证。"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import calculate_indicators, router_v3
from api.middlewares.error_handler import add_error_handlers
from src.services.thesis_ledger_period_indicator_contract import period_indicator_capabilities

_FIXTURES = Path(__file__).parent / "fixtures"
_CASES = json.loads((_FIXTURES / "thesis_ledger_period_indicator_v1.shared.json").read_text())["cases"]


def _client(monkeypatch):
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "period-test-token")
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    add_error_handlers(app)
    return TestClient(app)


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["timeframe"])
def test_shared_payload_calculates_actual_period_points(case):
    assert hashlib.sha256(case["aggregationEncoding"].encode()).hexdigest() == case["payload"]["periodContext"]["aggregationFingerprint"]
    result = calculate_indicators(copy.deepcopy(case["payload"]))
    assert result["inputFingerprint"] == case["payload"]["inputFingerprint"]
    assert result["engineVersion"] == "dsa-indicator-v3"
    assert [item["name"] for item in result["results"]] == ["MA", "MACD", "RSI"]
    for item in result["results"]:
        assert item["inputFingerprint"] == result["inputFingerprint"]
        assert [datetime.fromisoformat(point["timestamp"].replace("Z", "+00:00")) for point in item["points"]] == [datetime.fromisoformat(point["timestamp"].replace("Z", "+00:00")) for point in case["payload"]["points"]]
    if case["timeframe"] == "1y":
        assert all(value is None for item in result["results"] for point in item["points"] for value in point["values"].values())
    if case["timeframe"] == "1mo":
        closes = [point["close"] for point in case["payload"]["points"][-5:]]
        assert result["results"][0]["points"][-1]["values"]["ma5"] == pytest.approx(sum(closes) / 5)


def test_period_capability_http_and_golden_are_exact(monkeypatch):
    golden = json.loads((_FIXTURES / "thesis_ledger_period_indicator_capabilities_v1.shared.json").read_text())
    assert period_indicator_capabilities() == golden
    client = _client(monkeypatch)
    url = "/api/v3/thesis-ledger/market/indicators/capabilities"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer period-test-token"}).json() == golden
    assert client.get("/api/v3/thesis-ledger/capabilities").json() == {
        "dataContractVersions": [3], "serviceCapabilities": {"fundNav": True},
        "multiWindowProtocols": ["market-multi-window-content-v1"],
    }


@pytest.mark.parametrize("kind", ["points", "aggregate", "binding", "context", "fund", "unknown", "duplicates", "over-limit", "unordered", "invalid-ohlc", "parameters"])
def test_period_http_rejects_invalid_binding_and_input(monkeypatch, kind):
    payload = copy.deepcopy(_CASES[0]["payload"])
    if kind == "points":
        payload["points"][0]["close"] = payload["points"][0]["open"]
    elif kind == "aggregate":
        payload["periodContext"]["aggregationFingerprint"] = "f" * 64
    elif kind == "binding":
        payload["inputFingerprint"] = "f" * 64
    elif kind == "context":
        payload.pop("periodContext")
    elif kind == "fund":
        payload["identity"]["assetType"] = "MUTUAL_FUND"
    elif kind == "unknown":
        payload["identity"]["timeframe"] = "weekly"
    elif kind == "duplicates":
        payload["requests"] = [payload["requests"][0], payload["requests"][0]]
    elif kind == "over-limit":
        payload["points"] = payload["points"] * 70
    elif kind == "unordered":
        payload["points"].reverse()
    elif kind == "parameters":
        payload["requests"] = [{"name": "MA", "parameters": {"period": 1}}]
    elif kind == "invalid-ohlc":
        payload["points"][0]["high"] = -1
    response = _client(monkeypatch).post(
        "/api/v3/thesis-ledger/market/indicators/calculate",
        headers={"Authorization": "Bearer period-test-token"}, json=payload,
    )
    assert response.status_code == 422


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["timeframe"])
def test_shared_period_payload_http_success(monkeypatch, case):
    response = _client(monkeypatch).post(
        "/api/v3/thesis-ledger/market/indicators/calculate",
        headers={"Authorization": "Bearer period-test-token"}, json=case["payload"],
    )
    assert response.status_code == 200
    assert response.json()["inputFingerprint"] == case["payload"]["inputFingerprint"]
    assert response.json()["results"] == calculate_indicators(copy.deepcopy(case["payload"]))["results"]



def test_changed_aggregation_context_uses_a_new_calculation_identity():
    payload = copy.deepcopy(_CASES[0]["payload"])
    payload["periodContext"]["aggregationFingerprint"] = "f" * 64
    context = payload["periodContext"]
    encoded = json.dumps(
        [context["protocol"], context["aggregationFingerprint"], context["pointFingerprint"]],
        separators=(",", ":"),
    )
    payload["inputFingerprint"] = hashlib.sha256(encoded.encode()).hexdigest()
    result = calculate_indicators(payload)
    assert result["inputFingerprint"] != _CASES[0]["payload"]["inputFingerprint"]
    assert result["inputFingerprint"] == payload["inputFingerprint"]
