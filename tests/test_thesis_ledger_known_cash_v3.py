"""真实鉴权端点与配置前后校验；观察读取不改变旧完整事件入口。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_events_v3 import router
from src.services.thesis_ledger_known_cash_v3 import execute_known_cash_request, parse_known_cash_request
from src.services.thesis_ledger_hithink_known_cash import KnownCashReadError, read_hithink_known_cash
from tests.test_thesis_ledger_hithink_known_cash import EXCHANGE, PUBLICATION, raw, snapshot
from tests.hithink_cash_research_fixture import research_source_fixture


@pytest.fixture
def state(monkeypatch):
    import src.services.thesis_ledger_control as control
    monkeypatch.setattr(control, "_secret_key", lambda: ("test-v1", b"test-hmac-secret"))
    request = deepcopy(EXCHANGE["request"])
    request["researchPolicy"]["uncertainRecords"] = "exclude"
    target = request["routeTarget"]
    policy = {"contractVersion": 3, "revision": 1, "sourceDesiredRevision": 1, "enabled": True,
              "routes": [{"key": request["routeKey"], "reason": "not_admitted",
                          "targets": [{**target, "eligible": False, "reason": "not_admitted"}]}]}
    catalog = {"integrity": "complete", "catalogRevision": 1,
               "entries": [{"key": request["routeKey"], "target": {k: target[k] for k in ("providerId", "upstreamSource")}, "state": "not_admitted"}]}
    provider = {"providerId": "hithink", "enabled": True, "configured": True, "requiresCredential": True, "configVersion": 3, "tombstone": None}
    credentials = [snapshot()]
    store = SimpleNamespace(effective_policy_v3=lambda: deepcopy(policy), provider_registry=lambda: [deepcopy(provider)],
                            provider_credential_snapshot=lambda _provider: credentials[0])
    runtime = SimpleNamespace(store=store, market_route_catalog_v3=lambda: deepcopy(catalog))
    calls = []
    def read(request, pinned):
        calls.append((deepcopy(request), pinned))
        return read_hithink_known_cash(request, pinned, fetch=lambda *a, **k: research_source_fixture(raw()))
    return SimpleNamespace(request=request, runtime=runtime, policy=policy, catalog=catalog,
                           provider=provider, credentials=credentials, read=read, calls=calls)


def test_current_config_reads_known_observations_and_keeps_old_admission_state(state):
    original = deepcopy(state.policy)
    result = execute_known_cash_request(state.runtime, state.request, reader=state.read)
    assert result["records"][0]["normalizedStatus"] == "unknown"
    assert result["records"][0]["amountPerFundUnit"] == EXCHANGE["response"]["records"][0]["amountPerFundUnit"]
    assert result["coverage"]["complete"] is False and "admission" not in result and "facts" not in result
    assert state.policy == original and state.catalog["entries"][0]["state"] == "not_admitted"
    assert len(state.calls) == 1 and state.calls[0][1] == state.credentials[0]
    assert result["source"]["controlIdentity"]["credentialRevision"].startswith("hmac-sha256-v1:")
    assert "test-only-secret" not in str(result) and "test-hmac-secret" not in str(result)


@pytest.mark.parametrize("key", ["protocolVersion", "researchPolicy", "currency", "routeTarget", "dataAsOf"])
def test_missing_choice_fails_before_source(state, key):
    request = deepcopy(state.request); del request[key]
    with pytest.raises(KnownCashReadError): execute_known_cash_request(state.runtime, request, reader=state.read)
    assert not state.calls


@pytest.mark.parametrize("change", [
    {"contractVersion": True}, {"desiredRevision": True}, {"symbol": "00700.HK"},
    {"currency": "HKD"}, {"start": "2025-07-01"}, {"dataAsOf": "2026-10-09T13:00:00"},
    {"researchPolicy": {}}, {"extra": True}, {"requestId": " "},
])
def test_invalid_contract_request_rejected(state, change):
    with pytest.raises(KnownCashReadError): parse_known_cash_request({**state.request, **change})


@pytest.mark.parametrize("change", ["disabled", "policy_revision", "catalog_revision", "catalog_partial",
                                    "target_order", "provider_disabled", "removed", "unconfigured", "config_mismatch"])
def test_control_rejection_never_calls_source(state, change):
    if change == "disabled": state.policy["enabled"] = False
    elif change == "policy_revision": state.policy["revision"] = 2
    elif change == "catalog_revision": state.catalog["catalogRevision"] = 2
    elif change == "catalog_partial": state.catalog["integrity"] = "partial"
    elif change == "target_order": state.policy["routes"][0]["targets"][0]["routeIndex"] = 1
    elif change == "provider_disabled": state.provider["enabled"] = False
    elif change == "removed": state.provider["tombstone"] = {"reason": "removed"}
    elif change == "unconfigured": state.provider["configured"] = False
    else: state.provider["configVersion"] = 4
    with pytest.raises(KnownCashReadError): execute_known_cash_request(state.runtime, state.request, reader=state.read)
    assert not state.calls


@pytest.mark.parametrize("change", ["disable", "policy", "catalog", "secret", "config", "target", "remove"])
def test_changes_during_source_read_reject_result(state, change):
    def read(request, pinned):
        result = state.read(request, pinned)
        if change == "disable": state.provider["enabled"] = False
        elif change == "policy": state.policy["revision"] = 2
        elif change == "catalog": state.catalog["catalogRevision"] = 2
        elif change == "secret": state.credentials[0] = snapshot(values={"apiKey": "changed-secret"})
        elif change == "config": state.provider["configVersion"] = 4
        elif change == "target": state.policy["routes"][0]["targets"][0]["upstreamSource"] = "other"
        else: state.provider["tombstone"] = {"reason": "removed"}
        return result
    with pytest.raises(KnownCashReadError): execute_known_cash_request(state.runtime, state.request, reader=read)
    assert len(state.calls) == 1


@pytest.mark.parametrize("change", ["source", "target", "complete", "secret", "records", "query"])
def test_malformed_reader_output_not_emitted(state, change):
    def read(request, pinned):
        result = state.read(request, pinned)
        if change == "source": result["source"] = None
        elif change == "target": result["source"]["target"]["routeIndex"] = 1
        elif change == "complete": result["coverage"]["complete"] = True
        elif change == "secret": result["source"]["apiKey"] = "private"
        elif change == "records": result["records"] = None
        else: result["source"]["queryIdentity"]["currencyBasis"] = "provider-echo"
        return result
    with pytest.raises(KnownCashReadError, match="invalid_source_response"):
        execute_known_cash_request(state.runtime, state.request, reader=read)


def test_unsupported_adapter_not_added_or_fallback_selected(state):
    request = deepcopy(state.request); request["routeTarget"]["providerId"] = "unknown"
    with pytest.raises(KnownCashReadError, match="research_protocol_not_supported"):
        execute_known_cash_request(state.runtime, request, reader=state.read)
    assert not state.calls


@pytest.mark.parametrize("reason", ["disabled", "quota_unavailable", "credential_missing", "dependency_missing"])
def test_direct_endpoint_cannot_bypass_selected_target_physical_state(state, reason):
    state.policy["routes"][0]["targets"][0]["reason"] = reason
    with pytest.raises(KnownCashReadError):
        execute_known_cash_request(state.runtime, state.request, reader=state.read)
    assert not state.calls


def test_invalid_selected_target_eligibility_shape_rejected(state):
    state.policy["routes"][0]["targets"][0]["eligible"] = True
    with pytest.raises(KnownCashReadError, match="invalid_control_or_source_response"):
        execute_known_cash_request(state.runtime, state.request, reader=state.read)
    assert not state.calls


def test_literal_failure_reasons_match_published_shared_contract():
    import ast
    from pathlib import Path
    reasons = set(PUBLICATION["failureReasons"])
    root = Path(__file__).resolve().parents[1]
    for name in ("thesis_ledger_known_cash_v3.py", "thesis_ledger_hithink_known_cash.py",
                 "thesis_ledger_known_cash_state_v3.py", "thesis_ledger_known_cash_control_v3.py"):
        tree = ast.parse((root / "src/services" / name).read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "KnownCashReadError" and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)):
                assert node.args[1].value in reasons


def test_real_http_auth_dispatch_and_recheck_during_read(state, monkeypatch):
    import api.thesis_ledger_events_v3 as api
    import src.services.thesis_ledger_provider_runtime as runtime_module
    import src.services.thesis_ledger_known_cash_v3 as known
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setattr(runtime_module, "get_thesis_ledger_runtime", lambda: state.runtime)
    monkeypatch.setitem(known.READERS, ("hithink", "fund-corporate-actions-dividends"), state.read)
    monkeypatch.setattr(api, "execute_event_request", lambda *_args, **_kwargs: {"legacy": True})
    app = FastAPI(); app.include_router(router, prefix="/api/v3"); client = TestClient(app)
    endpoint = "/api/v3/thesis-ledger/market/events"
    assert client.post(endpoint, json=state.request).status_code == 401 and not state.calls
    headers = {"Authorization": "Bearer test-token"}
    response = client.post(endpoint, json=state.request, headers=headers)
    assert response.status_code == 200 and response.json()["records"][0]["normalizedStatus"] == "unknown"
    assert client.post(endpoint, json={"legacy": True}, headers=headers).json() == {"legacy": True}
    def revoke(request, pinned):
        result = state.read(request, pinned); monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "revoked")
        return result
    monkeypatch.setitem(known.READERS, ("hithink", "fund-corporate-actions-dividends"), revoke)
    assert client.post(endpoint, json=state.request, headers=headers).status_code == 401


def test_http_safe_error_reason_preserved(state, monkeypatch):
    import src.services.thesis_ledger_provider_runtime as runtime_module
    import src.services.thesis_ledger_known_cash_v3 as known
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setattr(runtime_module, "get_thesis_ledger_runtime", lambda: state.runtime)
    def denied(*_args): raise KnownCashReadError("upstream_failure", "permission_denied")
    monkeypatch.setitem(known.READERS, ("hithink", "fund-corporate-actions-dividends"), denied)
    app = FastAPI(); app.include_router(router, prefix="/api/v3")
    response = TestClient(app).post("/api/v3/thesis-ledger/market/events", json=state.request,
                                    headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 503
    assert response.json()["error"] == {"code": "upstream_failure", "message": "方案读取失败：permission_denied", "reason": "permission_denied"}
    assert response.json()["protocolVersion"] == "known-cash-observations-v1"


def test_legacy_new_read_never_silently_uses_previous_plan_assumption(state):
    request = deepcopy(state.request)
    del request["researchPolicy"]["uncertainRecords"]
    with pytest.raises(KnownCashReadError, match="invalid_research_request"):
        execute_known_cash_request(state.runtime, request, reader=state.read)
    assert not state.calls
