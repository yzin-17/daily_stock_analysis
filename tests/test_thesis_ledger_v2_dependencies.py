"""ThesisLedger V2 依赖级数据端点的定向测试。"""

from datetime import datetime, timezone
import sys
from types import SimpleNamespace

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router
from data_provider.fundamental_adapter import normalize_corporate_actions_v2


def _client(monkeypatch, *, fixture: bool) -> TestClient:
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true" if fixture else "false")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"authorization": "Bearer test-token"}


def test_v2_dependency_routes_have_auditable_fixture_envelope(monkeypatch):
    client = _client(monkeypatch, fixture=True)
    params = {
        "market": "CN",
        "start": "2025-01-01",
        "end": "2025-01-10",
        "dataAsOf": "2025-01-10T07:00:00Z",
    }
    calendar = client.get("/api/v1/thesis-ledger/v2/calendar", params=params, headers=_headers())
    instrument = client.get(
        "/api/v1/thesis-ledger/v2/instrument-facts",
        params={"symbol": "600519.SH", "market": "CN", "instrumentType": "STOCK", "dataAsOf": params["dataAsOf"]},
        headers=_headers(),
    )
    actions = client.get(
        "/api/v1/thesis-ledger/v2/corporate-actions",
        params={**params, "symbol": "600519.SH", "instrumentType": "STOCK"},
        headers=_headers(),
    )
    for response in (calendar, instrument, actions):
        assert response.status_code == 200
        payload = response.json()
        assert set(("version", "status", "coverage", "facts", "providerRevision", "reason")) <= set(payload)
        assert payload["version"] == 2
        assert payload["coverage"]["complete"] is True
        assert payload["facts"]
    assert calendar.json()["facts"][0]["availableAt"] < params["dataAsOf"]
    assert instrument.json()["facts"][0]["availableAt"] < params["dataAsOf"]


def test_v2_rejects_etf_and_never_leaks_fixture_facts(monkeypatch):
    client = _client(monkeypatch, fixture=False)
    response = client.get(
        "/api/v1/thesis-ledger/v2/instrument-facts",
        params={
            "symbol": "510300.SH",
            "market": "CN",
            "instrumentType": "ETF",
            "dataAsOf": "2025-01-10T07:00:00Z",
        },
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_capability"

    calendar = client.get(
        "/api/v1/thesis-ledger/v2/calendar",
        params={
            "market": "CN",
            "start": "2025-01-01",
            "end": "2025-01-10",
            "dataAsOf": "2025-01-10T07:00:00Z",
        },
        headers=_headers(),
    )
    assert calendar.status_code == 200
    assert all("fixture" not in str(value).lower() for value in calendar.json().values())

    instrument = client.get(
        "/api/v1/thesis-ledger/v2/instrument-facts",
        params={
            "symbol": "600519.SH",
            "market": "CN",
            "instrumentType": "STOCK",
            "dataAsOf": "2025-01-10T07:00:00Z",
        },
        headers=_headers(),
    )
    assert instrument.status_code == 200
    assert instrument.json()["status"] == "supported"
    assert instrument.json()["providerRevision"] == "cn-a-share-standard-lot-tick-v1"
    assert instrument.json()["facts"][0]["availableAt"] < "2025-01-10T07:00:00Z"


def test_corporate_action_normalization_filters_future_knowledge_and_preserves_provenance():
    frame = pd.DataFrame(
        [
            {
                "股票代码": "600519",
                "除权除息日": "2025-01-05",
                "公告日期": "2024-12-20",
                "每股派息": "1.25",
            },
            {
                "股票代码": "600519",
                "除权除息日": "2025-01-06",
                "公告日期": "2025-02-01",
                "每股派息": "0.50",
            },
        ]
    )
    result = normalize_corporate_actions_v2(
        frame,
        "600519.SH",
        start_date="2025-01-01",
        end_date="2025-01-31",
        data_as_of=datetime(2025, 1, 10, tzinfo=timezone.utc),
    )
    assert result["coverage"]["complete"] is True
    assert len(result["facts"]) == 1
    assert result["facts"][0]["cashAmount"] == "1.25"
    assert result["facts"][0]["providerRevision"].startswith("akshare")
    assert result["facts"][0]["availableAt"] <= "2025-01-10T00:00:00+00:00"


def test_corporate_action_empty_requires_explicit_complete_source():
    empty = pd.DataFrame()
    kwargs = {
        "stock_code": "600519.SH",
        "start_date": "2025-01-01",
        "end_date": "2025-01-31",
        "data_as_of": datetime(2025, 1, 31, tzinfo=timezone.utc),
    }
    assert normalize_corporate_actions_v2(empty, **kwargs)["coverage"]["complete"] is False
    assert normalize_corporate_actions_v2(empty, source_complete=True, **kwargs)["coverage"]["complete"] is True


def test_akshare_adapter_can_assert_a_successful_empty_source(monkeypatch):
    from data_provider.fundamental_adapter import AkshareFundamentalAdapter

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_fhps_detail_em=lambda **kwargs: pd.DataFrame()),
    )
    result = AkshareFundamentalAdapter().get_corporate_actions_v2(
        "600519.SH",
        start_date="2025-01-01",
        end_date="2025-01-31",
        data_as_of=datetime(2025, 1, 31, tzinfo=timezone.utc),
    )
    assert result["coverage"]["complete"] is True
    assert result["facts"] == []


def test_corporate_action_provider_failure_is_not_an_empty_success(monkeypatch):
    from data_provider.fundamental_adapter import AkshareFundamentalAdapter

    monkeypatch.setattr(
        AkshareFundamentalAdapter,
        "get_corporate_actions_v2",
        lambda self, *args, **kwargs: {
            "facts": [],
            "coverage": {"start": "2025-01-01", "end": "2025-01-31", "complete": False},
        },
    )
    client = _client(monkeypatch, fixture=False)
    response = client.get(
        "/api/v1/thesis-ledger/v2/corporate-actions",
        params={
            "symbol": "600519.SH",
            "market": "CN",
            "instrumentType": "STOCK",
            "start": "2025-01-01",
            "end": "2025-01-31",
            "dataAsOf": "2025-01-31T07:00:00Z",
        },
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["coverage"]["complete"] is False
