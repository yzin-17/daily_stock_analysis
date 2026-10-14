"""使用真实控制快照/鉴权路由验证只读复核；供应商调用必须为零。"""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_events_v3 import router
from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot
from src.services.thesis_ledger_known_cash_control_v3 import (
    execute_known_cash_control_request, parse_known_cash_control_request,
)
from src.services.thesis_ledger_known_cash_state_v3 import KnownCashReadError
from tests.test_thesis_ledger_known_cash_v3 import state as observation_state
from tests.test_thesis_ledger_hithink_known_cash import snapshot

state = observation_state
EXCHANGE = json.loads((Path(__file__).parent / "fixtures/thesis_ledger_known_cash_control_v3.shared.json").read_text())["exchange"]


@pytest.fixture
def control_request(state):
    return {**deepcopy(EXCHANGE["request"]), "expectedControlIdentity": {
        "providerConfigVersion": state.credentials[0].config_version,
        "credentialRevision": _provider_credential_revision_from_snapshot(state.credentials[0]),
    }}


def test_verified_control_is_read_only_and_never_dispatches_supplier(state, control_request, monkeypatch):
    import src.services.thesis_ledger_known_cash_v3 as observations
    target = control_request["routeTarget"]
    monkeypatch.setitem(observations.READERS, (target["providerId"], target["upstreamSource"]),
                        lambda *_args, **_kwargs: pytest.fail("supplier called"))
    original = deepcopy((state.policy, state.catalog, state.provider))
    permissions = []
    result = execute_known_cash_control_request(state.runtime, control_request, check_security=lambda: permissions.append(True))
    assert result["status"] == "verified" and result["readOnly"] is True and result["upstreamRead"] is False
    assert result["controlIdentity"] == control_request["expectedControlIdentity"]
    assert result["targetSequence"] == [target] and len(permissions) == 2
    assert {key: result[key] for key in control_request} == control_request
    assert original == (state.policy, state.catalog, state.provider) and not state.calls
    assert set(result) == set(control_request) | {"status", "readOnly", "upstreamRead", "checkedAt", "controlIdentity", "targetSequence"}
    assert "test-only-secret" not in json.dumps(result) and "test-hmac-secret" not in json.dumps(result)


@pytest.mark.parametrize("change", [
    {"contractVersion": True}, {"protocolVersion": "known-cash-observations-v1"}, {"readUpstream": True},
    {"expectedControlIdentity": {}}, {"expectedControlIdentity": {"providerConfigVersion": True, "credentialRevision": "not-required"}},
    {"expectedControlIdentity": {"providerConfigVersion": 1, "credentialRevision": "private-secret"}},
    {"expectedControlIdentity": {"providerConfigVersion": 1, "credentialRevision": "not-required", "extra": True}},
    {"start": "2026-10-10"}, {"currency": "HKD"}, {"researchPolicy": {}},
])
def test_invalid_requests_rejected_before_control(state, control_request, change):
    with pytest.raises(KnownCashReadError):
        parse_known_cash_control_request({**control_request, **change})
    assert not state.calls


@pytest.mark.parametrize("field", ["providerConfigVersion", "credentialRevision"])
def test_frozen_identity_change_requires_repreparation(state, control_request, field):
    control_request["expectedControlIdentity"][field] = 4 if field == "providerConfigVersion" else "hmac-sha256-v1:" + "c" * 64
    with pytest.raises(KnownCashReadError, match="configuration_changed"):
        execute_known_cash_control_request(state.runtime, control_request)
    assert not state.calls


@pytest.mark.parametrize("change", ["policy", "catalog", "removed", "disabled", "credentials", "quota", "target", "duplicate"])
def test_current_unavailable_state_cannot_verify(state, control_request, change):
    target = state.policy["routes"][0]["targets"][0]
    if change == "policy": state.policy["revision"] += 1
    elif change == "catalog": state.catalog["catalogRevision"] += 1
    elif change == "removed": state.provider["tombstone"] = {"reason": "removed"}
    elif change == "disabled": state.provider["enabled"] = False
    elif change == "credentials": state.provider["configured"] = False
    elif change == "quota": target["reason"] = "quota_unavailable"
    elif change == "target": target["upstreamSource"] = "changed"
    else: state.policy["routes"][0]["targets"].append({**target, "routeIndex": 1})
    with pytest.raises(KnownCashReadError): execute_known_cash_control_request(state.runtime, control_request)
    assert not state.calls


@pytest.mark.parametrize("change", ["credential", "config", "catalog", "backup", "disabled"])
def test_changes_between_control_captures_fail_closed(state, control_request, change):
    original = state.runtime.store.effective_policy_v3
    calls = []
    def policy_read():
        calls.append(True)
        if len(calls) == 2:
            if change == "credential": state.credentials[0] = snapshot(values={"apiKey": "changed-secret"})
            elif change == "config":
                state.provider["configVersion"] = 4
                state.credentials[0] = snapshot(config_version=4)
            elif change == "catalog": state.catalog["catalogRevision"] += 1
            elif change == "backup": state.policy["routes"][0]["targets"].append({"providerId": "other", "upstreamSource": "plans", "routeIndex": 1})
            else: state.provider["enabled"] = False
        return original()
    state.runtime.store.effective_policy_v3 = policy_read
    with pytest.raises(KnownCashReadError): execute_known_cash_control_request(state.runtime, control_request)
    assert len(calls) == 2 and not state.calls


def test_backup_selection_is_preserved_and_unsupported_adapter_not_assumed_ready(state, control_request):
    primary = {**state.policy["routes"][0]["targets"][0], "providerId": "other", "upstreamSource": "plans"}
    selected = {**state.policy["routes"][0]["targets"][0], "routeIndex": 1}
    state.policy["routes"][0]["targets"] = [primary, selected]
    control_request["routeTarget"]["routeIndex"] = 1
    result = execute_known_cash_control_request(state.runtime, control_request)
    assert result["routeTarget"]["routeIndex"] == 1 and result["targetSequence"][0]["providerId"] == "other"
    control_request["routeTarget"] = {k: primary[k] for k in ("providerId", "upstreamSource", "routeIndex")}
    with pytest.raises(KnownCashReadError, match="research_protocol_not_supported"):
        execute_known_cash_control_request(state.runtime, control_request)
    assert not state.calls


def test_actual_http_auth_recheck_safe_rejection_and_legacy_dispatch(state, control_request, monkeypatch):
    import api.thesis_ledger_events_v3 as api
    import src.services.thesis_ledger_provider_runtime as runtime_module
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setattr(runtime_module, "get_thesis_ledger_runtime", lambda: state.runtime)
    monkeypatch.setattr(api, "execute_event_request", lambda *_args, **_kwargs: {"legacy": True})
    app = FastAPI(); app.include_router(router, prefix="/api/v3"); client = TestClient(app)
    endpoint = "/api/v3/thesis-ledger/market/events"
    headers = {"authorization": "Bearer test-token"}
    assert client.post(endpoint, json=control_request).status_code == 401
    response = client.post(endpoint, json=control_request, headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "verified"
    altered = deepcopy(control_request); altered["expectedControlIdentity"]["providerConfigVersion"] += 1
    rejected = client.post(endpoint, json=altered, headers=headers)
    assert rejected.status_code == 422
    assert rejected.json()["protocolVersion"] == "known-cash-control-v1"
    assert rejected.json()["error"]["reason"] == "configuration_changed"
    assert client.post(endpoint, json={"legacy": True}, headers=headers).json() == {"legacy": True}
    original = state.runtime.store.provider_credential_snapshot
    count = []
    def credential_read(provider):
        count.append(True)
        if len(count) == 2: monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "revoked")
        return original(provider)
    state.runtime.store.provider_credential_snapshot = credential_read
    assert client.post(endpoint, json=control_request, headers=headers).status_code == 401
    assert not state.calls
