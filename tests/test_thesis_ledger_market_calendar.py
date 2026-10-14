"""展示年度计划与回测结算日历预算的独立验证。"""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router_v3
from api.middlewares.error_handler import add_error_handlers
from src.services import thesis_ledger_calendar_contract as contract
from src.services.thesis_ledger_dependency_facts import DependencyFactError, parse_data_as_of


def _client(monkeypatch):
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "calendar-test-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "false")
    monkeypatch.setattr("api.thesis_ledger._fixture_mode", lambda: False)
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    add_error_handlers(app)
    return TestClient(app)


def _fact(start, end, _as_of):
    return {
        "market": "CN", "timezone": "Asia/Shanghai", "provider": "controlled-calendar",
        "providerRevision": "controlled-v1", "availableAt": "2025-12-31T00:00:00Z",
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "sessions": [{"startMinute": 570, "endMinute": 900}],
        "sessionOverrides": [], "holidays": ["2026-01-01"],
    }


def _params(**patch):
    return {"market": "CN", "start": "2026-01-01", "end": "2026-12-31",
            "dataAsOf": "2026-01-02T00:00:00Z", **patch}


def _headers():
    return {"Authorization": "Bearer calendar-test-token"}


def test_full_year_display_is_independent_of_settlement_budget(monkeypatch):
    monkeypatch.setattr(contract, "calendar_fact", _fact)
    client = _client(monkeypatch)
    result = client.get("/api/v3/thesis-ledger/market/calendar", params=_params(), headers=_headers())
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "supported" and body["coverage"]["complete"] is True
    assert body["facts"][0]["range"] == {"start": "2026-01-01", "end": "2026-12-31"}
    assert client.get("/api/v3/thesis-ledger/backtest/calendar", params=_params(), headers=_headers()).status_code == 422
    short = _params(end="2026-02-01")
    display = client.get("/api/v3/thesis-ledger/market/calendar", params=short, headers=_headers())
    settlement = client.get("/api/v3/thesis-ledger/backtest/calendar", params=short, headers=_headers())
    assert display.status_code == settlement.status_code == 200
    assert display.json() == settlement.json()


def test_exact_future_and_total_budget_boundaries():
    as_of = parse_data_as_of("2026-01-02T00:00:00Z")
    last = as_of.date() + timedelta(days=366)
    assert contract.validate_market_calendar_range("2026-01-01", last.isoformat(), as_of)[1] == last
    with pytest.raises(DependencyFactError):
        contract.validate_market_calendar_range("2026-01-01", (last + timedelta(days=1)).isoformat(), as_of)
    first = as_of.date() - timedelta(days=19999)
    assert contract.validate_market_calendar_range(first.isoformat(), "2026-01-02", as_of)[0] == first
    with pytest.raises(DependencyFactError):
        contract.validate_market_calendar_range((first - timedelta(days=1)).isoformat(), "2026-01-02", as_of)


@pytest.mark.parametrize("patch", [
    {"market": "JP"}, {"start": "2026-02-30"}, {"start": "2026-01-03"},
    {"end": "2025-12-31"}, {"end": "2027-01-04"}, {"start": "1900-01-01"},
    {"dataAsOf": "2026-01-02T00:00:00"},
])
def test_invalid_display_calendar_is_rejected_before_source(monkeypatch, patch):
    def forbidden(*_args):
        pytest.fail("非法范围不应读取来源")
    monkeypatch.setattr(contract, "calendar_fact", forbidden)
    result = _client(monkeypatch).get("/api/v3/thesis-ledger/market/calendar", params=_params(**patch), headers=_headers())
    assert result.status_code == 422


def test_calendar_source_unavailable_and_authentication(monkeypatch):
    monkeypatch.setattr(contract, "calendar_fact", lambda *_args: None)
    client = _client(monkeypatch)
    url = "/api/v3/thesis-ledger/market/calendar"
    assert client.get(url, params=_params()).status_code == 401
    result = client.get(url, params=_params(), headers=_headers())
    assert result.status_code == 200
    assert result.json()["status"] == "unavailable"
    assert result.json()["coverage"]["complete"] is False
    assert result.json()["facts"] == []
