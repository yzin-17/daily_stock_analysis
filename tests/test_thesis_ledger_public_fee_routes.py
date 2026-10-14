"""公共收费通过实际 Control 与已认证数据网关读取生产包。"""

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router_v3
from src.services import thesis_ledger_provider_runtime as runtime_module
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from src.services.thesis_ledger_public_fees import (
    CAPABILITY, PROVIDER_ID, UPSTREAM_SOURCE, OfficialPublicFeeProvider,
)

DATA_HEADERS = {"Authorization": "Bearer fees-data-test-token"}
CONTROL_HEADERS = {"Authorization": "Bearer fees-control-test-token"}
DATA_URL = "/api/v3/thesis-ledger/market/public-fees"
KEY = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": CAPABILITY}
TARGET = {"providerId": PROVIDER_ID, "upstreamSource": UPSTREAM_SOURCE}


def wire_request():
    return {
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "fee-http-read",
        "routeKey": KEY, "routeTarget": {**TARGET, "routeIndex": 0},
        "symbol": "600000.SH", "range": {"start": "2023-08-25", "end": "2023-08-29"},
    }


def policy(revision=1, enabled=True):
    return {
        "contractVersion": 3, "consumer": "thesis-ledger",
        "requestId": f"fee-policy-{revision}", "revision": revision,
        "enabled": enabled, "routes": [{"key": KEY, "targets": [TARGET]}],
    }


@pytest.fixture
def configured_client(tmp_path, monkeypatch):
    path = str(tmp_path / "public-fee-control.sqlite")
    monkeypatch.setenv("DATABASE_PATH", path)
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "fees-data-test-token")
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "fees-control-test-token")
    store = ThesisLedgerControlStore(path)
    runtime = ThesisLedgerProviderRuntime(store)
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(runtime_module, "_runtime_database_path", path)
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    with TestClient(app) as client:
        yield client, runtime, store


def test_catalog_and_saved_policy_can_read_real_pack_without_admission(configured_client):
    client, _runtime, store = configured_client
    catalog = client.get(
        "/api/v3/thesis-ledger/control/routes/capabilities?contractVersion=3",
        headers=CONTROL_HEADERS,
    )
    assert catalog.status_code == 200
    entry = next(row for row in catalog.json()["entries"] if row["key"] == KEY)
    assert entry == {"key": KEY, "target": TARGET, "state": "ready"}
    providers = client.get("/api/v3/thesis-ledger/control/providers", headers=CONTROL_HEADERS).json()["providers"]
    source = next(item for item in providers if item["providerId"] == PROVIDER_ID)
    assert source["configurationMode"] == "built_in"
    assert source["requiresCredential"] is False
    assert source["upstreamSources"][0]["capabilities"][CAPABILITY] == ["STOCK"]
    saved = client.post("/api/v3/thesis-ledger/control/policies/apply", json=policy(), headers=CONTROL_HEADERS)
    assert saved.status_code == 200
    assert saved.json()["effective"]["routes"][0]["targets"][0]["eligible"] is True
    assert store.get_route_admission_v3(key=KEY, target=TARGET) is None
    response = client.post(DATA_URL, json=wire_request(), headers=DATA_HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result["routeTarget"] == {**TARGET, "routeIndex": 0}
    assert result["provenance"]["providerId"] == PROVIDER_ID
    assert [segment["range"]["start"] for segment in result["segments"]] == ["2023-08-25", "2023-08-28"]
    assert result["segments"][1]["charges"][0]["rate"] == "0.0005"


def test_data_token_is_required_and_control_token_is_not_a_data_token(configured_client):
    client, _runtime, _store = configured_client
    for headers in ({}, CONTROL_HEADERS):
        response = client.post(DATA_URL, json=wire_request(), headers={**headers, "x-request-id": "fee-auth"})
        assert response.status_code == 401
        assert response.json()["detail"]["requestId"] == "fee-auth"


def test_unconfigured_or_disabled_policy_does_not_read_source(configured_client):
    client, _runtime, store = configured_client
    response = client.post(DATA_URL, json=wire_request(), headers=DATA_HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "no_eligible_provider"
    store.apply_policy_v3(policy(enabled=False))
    response = client.post(DATA_URL, json=wire_request(), headers=DATA_HEADERS)
    assert response.status_code == 503


@pytest.mark.parametrize("change,status,code", [
    ({"routeTarget": {**TARGET, "routeIndex": 1}}, 503, "no_eligible_provider"),
    ({"range": {"start": "2023-08-22", "end": "2023-08-29"}}, 503, "insufficient_coverage"),
    ({"symbol": "600000.SZ"}, 503, "not_covered"),
    ({"range": {"start": "2023-08-29", "end": "2023-08-25"}}, 422, "bad_request"),
    ({"routeKey": {**KEY, "market": "US"}}, 422, "bad_request"),
])
def test_pin_identity_and_window_are_not_silently_changed(configured_client, change, status, code):
    client, _runtime, store = configured_client
    store.apply_policy_v3(policy())
    payload = {**deepcopy(wire_request()), **change}
    response = client.post(DATA_URL, json=payload, headers=DATA_HEADERS)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def test_policy_change_during_read_is_not_returned_as_current(configured_client):
    client, runtime, store = configured_client
    store.apply_policy_v3(policy())

    class ChangedPolicySource:
        def read(self, request):
            value = OfficialPublicFeeProvider().read(request)
            store.apply_policy_v3(policy(revision=2, enabled=False))
            return value

    runtime.adapters[PROVIDER_ID] = ChangedPolicySource()
    response = client.post(DATA_URL, json=wire_request(), headers=DATA_HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "not_admitted"
