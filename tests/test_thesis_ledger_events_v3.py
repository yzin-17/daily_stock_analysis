"""事件执行复用真实目录/准入比较，验证读取前后撤销和精确范围。"""

from copy import deepcopy
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_events_v3 import router
from src.services.thesis_ledger_event_v3 import execute_event_request, EventV3Error
from src.services.thesis_ledger_event_v3_adapters import EVENT_KEY, EVENT_TARGET, EVENT_REVISIONS
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from tests.test_thesis_ledger_market_v3_admission_runtime import _CatalogStore, _identity


@pytest.fixture
def state():
    admission = {
        "consumer": "thesis-ledger", "admissionState": "admitted",
        "scopeSymbols": ["510300.SH"], "scopeDateFrom": "2025-06-01", "scopeDateTo": "2025-06-30",
        "evidenceRef": "fixture-reviewed", "recordVersion": 1, **EVENT_REVISIONS,
        "status": "admitted", "routeKey": dict(EVENT_KEY), "target": dict(EVENT_TARGET),
        "evidenceSha256": "a" * 64, "validFrom": "2020-01-01T00:00:00Z",
        "validUntil": "2099-01-01T00:00:00Z", "recordedAt": "2020-01-01T00:00:00Z",
        "invalidatedAt": None, "invalidationReason": None, "recordedBy": "private-reviewer",
    }
    store = _CatalogStore({_identity(EVENT_KEY, EVENT_TARGET): admission})
    policy = {
        "contractVersion": 3, "enabled": True, "revision": 5, "sourceDesiredRevision": 5,
        "routes": [{"key": EVENT_KEY, "targets": [
            {**EVENT_TARGET, "routeIndex": 0, "eligible": True, "reason": None},
        ]}],
    }
    store.effective_policy_v3 = lambda: deepcopy(policy)
    runtime = ThesisLedgerProviderRuntime(store=store)
    request = {
        "contractVersion": 3, "requestId": "event-test", "symbol": "510300.SH",
        "routeKey": dict(EVENT_KEY), "routeTarget": {**EVENT_TARGET, "routeIndex": 0},
        "desiredRevision": 5, "effectivePolicyRevision": 5,
        "catalogRevision": runtime.market_route_catalog_v3()["catalogRevision"],
        "start": "2025-06-01", "end": "2025-06-30", "dataAsOf": "2099-01-01T00:00:00Z",
    }
    reader = Mock(return_value={"facts": [], "providerRevision": "fixture-observation"})
    return runtime, request, reader, store, policy


def test_current_admission_calls_exact_source_and_retains_incomplete_coverage(state):
    runtime, request, read, *_ = state
    result = execute_event_request(runtime, request, reader=read)
    read.assert_called_once_with("510300.SH", start="2025-06-01", end="2025-06-30")
    assert result["routeTarget"] == request["routeTarget"]
    assert result["coverage"]["complete"] is False
    assert result["providerRevision"] == "fixture-observation"
    assert result["admission"]["evidenceRef"] == "fixture-reviewed"
    assert result["admission"]["routeKey"] == request["routeKey"]
    assert "recordedBy" not in result["admission"]


@pytest.mark.parametrize("field,value", [
    ("validUntil", "2020-01-01T00:00:00Z"), ("recordedAt", "2099-01-01T00:00:00Z"),
    ("evidenceSha256", "missing-digest"), ("invalidatedAt", "2026-01-01T00:00:00Z"),
])
def test_invalid_admission_snapshot_is_not_returned(state, field, value):
    runtime, request, read, store, _ = state
    store.admissions[_identity(EVENT_KEY, EVENT_TARGET)][field] = value
    with pytest.raises(EventV3Error):
        execute_event_request(runtime, request, reader=read)


@pytest.mark.parametrize("field,value", [
    ("symbol", "159516.SZ"), ("start", "2025-05-01"),
    ("catalogRevision", 1), ("effectivePolicyRevision", 6),
])
def test_invalid_scope_never_calls_upstream(state, field, value):
    runtime, request, read, *_ = state
    with pytest.raises(EventV3Error):
        execute_event_request(runtime, {**request, field: value}, reader=read)
    read.assert_not_called()


@pytest.mark.parametrize("change", ["revoke", "revision", "disable", "adapter"])
def test_changes_during_read_reject_late_results(state, change):
    runtime, request, read, store, policy = state

    def changed(*_args, **_kwargs):
        admission = store.admissions[_identity(EVENT_KEY, EVENT_TARGET)]
        if change == "revoke":
            admission["admissionState"] = "revoked"
        elif change == "revision":
            policy["revision"] += 1
        elif change == "adapter":
            admission["adapterRevision"] = "old"
        else:
            policy["enabled"] = False
        return {"facts": [], "providerRevision": "fixture-observation"}

    read.side_effect = changed
    with pytest.raises(EventV3Error):
        execute_event_request(runtime, request, reader=read)
    assert read.call_count == 1


def test_http_authentication_prevents_execution(state, monkeypatch):
    import api.thesis_ledger_events_v3 as api
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    execute = Mock()
    monkeypatch.setattr(api, "execute_event_request", execute)
    app = FastAPI()
    app.include_router(router, prefix="/api/v3")
    client = TestClient(app)
    response = client.post("/api/v3/thesis-ledger/market/events", json=state[1])
    assert response.status_code in (401, 403)
    execute.assert_not_called()


def test_authenticated_http_uses_the_admitted_runtime(state, monkeypatch):
    import src.services.thesis_ledger_event_v3 as service
    import src.services.thesis_ledger_provider_runtime as module
    runtime, request, read, *_ = state
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setattr(module, "get_thesis_ledger_runtime", lambda: runtime)
    monkeypatch.setattr(service, "fetch_fund_dividend_observations", read)
    app = FastAPI()
    app.include_router(router, prefix="/api/v3")
    response = TestClient(app).post(
        "/api/v3/thesis-ledger/market/events", json=request,
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert response.json()["coverage"]["complete"] is False
    assert response.json()["routeTarget"] == request["routeTarget"]
    assert read.call_count == 1
