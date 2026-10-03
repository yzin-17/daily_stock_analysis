"""ThesisLedger 当前数据合同的确定性测试。"""

from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import _fx_row, router_v3


def _client(monkeypatch, *, fixture: bool = True) -> TestClient:
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true" if fixture else "false")
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    return TestClient(app)


def test_contract_requires_independent_bearer_token(monkeypatch):
    client = _client(monkeypatch)
    response = client.get("/api/v3/thesis-ledger/market/quote?symbol=600519.SH")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthorized"


def test_contract_fixture_exposes_capabilities_and_daily_quote(monkeypatch):
    client = _client(monkeypatch)
    headers = {"authorization": "Bearer test-token"}

    capabilities_response = client.get("/api/v3/thesis-ledger/capabilities")
    quote_response = client.get(
        "/api/v3/thesis-ledger/market/quote?symbol=600519.SH",
        headers=headers,
    )

    assert capabilities_response.status_code == 200
    assert capabilities_response.json()["dataContractVersions"] == [3]
    assert capabilities_response.json()["serviceCapabilities"] == {"fundNav": True}
    assert quote_response.status_code == 200
    assert quote_response.json()["symbol"] == "600519.SH"
    assert quote_response.json()["version"] == 3
    assert client.get("/api/v1/thesis-ledger/capabilities", headers=headers).status_code == 404
    assert client.get(
        "/api/v1/thesis-ledger/market/quote?symbol=600519.SH",
        headers=headers,
    ).status_code == 404
    assert client.get(
        "/api/v1/thesis-ledger/market/chip?symbol=600519.SH",
        headers=headers,
    ).status_code == 404
    assert client.get(
        "/api/v1/thesis-ledger/market/fund-holdings?symbol=000001.OF",
        headers=headers,
    ).status_code == 404
    assert client.get(
        "/api/v1/thesis-ledger/market/fx-rates?baseCurrency=CNY&currencies=CNY",
        headers=headers,
    ).status_code == 404


def test_removed_v2_capability_route_returns_404(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v1/thesis-ledger/v2/capabilities",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 404


def test_removed_v2_bar_route_returns_404(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v1/thesis-ledger/v2/market/bars?symbol=600519.SH&timeframe=1d",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 404


def test_session_close_is_pit_safe_for_unclosed_and_future_bars():
    from api.thesis_ledger import _session_close_available_at

    assert (
        _session_close_available_at(
            "2025-01-10T06:00:00+00:00",
            "2025-01-10T06:30:00+00:00",
        )
        is None
    )
    assert (
        _session_close_available_at(
            "2025-01-10T07:00:00+00:00",
            "2025-01-10T06:30:00+00:00",
        )
        is None
    )
    assert _session_close_available_at(
        "2025-01-10T06:00:00+00:00",
        "2025-01-11T00:00:00+00:00",
    ) == "2025-01-10T07:00:00+00:00"


def test_contract_fixture_exposes_fund_nav(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v3/thesis-ledger/market/fund-nav?symbol=000001.OF",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 3
    assert payload["symbol"] == "000001.OF"
    assert payload["unitNav"] == 1.2345
    assert payload["freshness"] == "delayed"
    assert client.get(
        "/api/v1/thesis-ledger/market/fund-nav?symbol=000001.OF",
        headers={"authorization": "Bearer test-token"},
    ).status_code == 404


def test_contract_fixture_exposes_fx_rates_with_age_contract(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v3/thesis-ledger/market/fx-rates?baseCurrency=CNY&currencies=HKD,USD&asOf=2025-01-10",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 3
    assert payload["baseCurrency"] == "CNY"
    assert payload["maxAgeDays"] == 7
    assert {row["fromCurrency"] for row in payload["rates"]} == {"CNY", "HKD", "USD"}
    hkd = next(row for row in payload["rates"] if row["fromCurrency"] == "HKD")
    assert hkd["rate"] == 0.92
    assert hkd["available"] is True
    assert hkd["freshness"] == "delayed"


def test_contract_fixture_supports_inverse_and_cross_currency_rates(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v3/thesis-ledger/market/fx-rates?baseCurrency=HKD&currencies=CNY,USD&asOf=2025-01-10",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    rows = {row["fromCurrency"]: row for row in response.json()["rates"]}
    assert rows["CNY"]["rate"] == 1 / 0.92
    assert rows["USD"]["rate"] == 7.2 / 0.92


def test_contract_rejects_unknown_fx_currency(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v3/thesis-ledger/market/fx-rates?baseCurrency=CNY&currencies=JPY",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_currency"


def test_fx_age_marks_stale_within_seven_days_and_blocks_after_threshold():
    recent = _fx_row(
        from_currency="HKD",
        to_currency="CNY",
        rate=0.92,
        rate_date=date(2025, 1, 3),
        provider="cache",
        fetched_at="2025-01-03T00:00:00+00:00",
        stale=False,
        as_of=date(2025, 1, 10),
    )
    expired = _fx_row(
        from_currency="HKD",
        to_currency="CNY",
        rate=0.92,
        rate_date=date(2025, 1, 2),
        provider="cache",
        fetched_at="2025-01-02T00:00:00+00:00",
        stale=False,
        as_of=date(2025, 1, 10),
    )
    assert recent["stale"] is True
    assert recent["available"] is True
    assert recent["ageDays"] == 7
    assert expired["available"] is False
