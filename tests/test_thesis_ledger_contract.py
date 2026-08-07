"""ThesisLedger Contract V1 的确定性测试。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def test_contract_requires_independent_bearer_token(monkeypatch):
    client = _client(monkeypatch)
    response = client.get("/api/v1/thesis-ledger/capabilities")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthorized"


def test_contract_fixture_exposes_capabilities_and_daily_quote(monkeypatch):
    client = _client(monkeypatch)
    headers = {"authorization": "Bearer test-token"}

    capabilities_response = client.get(
        "/api/v1/thesis-ledger/capabilities",
        headers=headers,
    )
    quote_response = client.get(
        "/api/v1/thesis-ledger/market/quote?symbol=600519.SH",
        headers=headers,
    )

    assert capabilities_response.status_code == 200
    assert capabilities_response.json()["contractVersion"] == 1
    assert capabilities_response.json()["capabilities"]["bars"]["timeframes"] == ["1d"]
    assert quote_response.status_code == 200
    assert quote_response.json()["symbol"] == "600519.SH"
    assert quote_response.json()["version"] == 1


def test_contract_returns_structured_unsupported_error(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v1/thesis-ledger/market/bars?symbol=600519.SH&timeframe=1m",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_capability"


def test_contract_fixture_exposes_fund_nav(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v1/thesis-ledger/market/fund-nav?symbol=000001.OF",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 1
    assert payload["symbol"] == "000001.OF"
    assert payload["unitNav"] == 1.2345
    assert payload["freshness"] == "delayed"
