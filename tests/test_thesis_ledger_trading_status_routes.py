"""生产停复牌包通过实际 Control 和认证网关读取。"""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tests import test_thesis_ledger_public_fee_routes as fee_routes
from src.services import thesis_ledger_trading_status as states

configured_client = fee_routes.configured_client
DATA_HEADERS = fee_routes.DATA_HEADERS
CONTROL_HEADERS = fee_routes.CONTROL_HEADERS
DATA_URL = "/api/v1/thesis-ledger/market/status-capabilities"
ROUTE_KEY = {**states.ROUTE_KEY, "capability": "DAILY_TRADING_STATUS"}
TARGET = {"providerId": states.PROVIDER_ID, "upstreamSource": states.UPSTREAM_SOURCE}


def wire_request():
    return {
        "contractVersion": 1, "consumer": "thesis-ledger", "requestId": "state-http-read",
        "capabilityVersion": 1, "policyRevision": 1,
        "routeKey": ROUTE_KEY, "routeTarget": {**TARGET, "routeIndex": 0},
        "symbol": "000995.SZ", "range": {"start": "2023-08-16", "end": "2023-08-21"},
    }


def policy(revision=1, enabled=True):
    return {
        "contractVersion": 1, "consumer": "thesis-ledger", "requestId": f"states-policy-{revision}",
        "revision": revision, "enabled": enabled, "routes": [{"key": ROUTE_KEY, "enabled": True, "targets": [TARGET]}],
    }


def test_catalog_policy_and_http_use_real_pack_without_credentials_or_admission(configured_client):
    client, _runtime, store = configured_client
    catalog = client.get(
        "/api/v1/thesis-ledger/control/routes/capabilities?contractVersion=1", headers=CONTROL_HEADERS,
    ).json()
    entry = next(row for row in catalog["entries"] if row["key"] == ROUTE_KEY and row["target"] == TARGET)
    assert entry["state"] == "ready" and entry["verification"] == {"state": "unverified"}
    assert not any(row["key"] == states.ROUTE_KEY for row in catalog["entries"])
    providers = client.get("/api/v1/thesis-ledger/control/providers", headers=CONTROL_HEADERS).json()["providers"]
    source = next(row for row in providers if row["providerId"] == states.PROVIDER_ID)
    assert source["configurationMode"] == "built_in"
    assert source["requiresCredential"] is False
    assert source["upstreamSources"][0]["capabilities"][ROUTE_KEY["capability"]] == ["STOCK"]
    saved = client.post("/api/v1/thesis-ledger/control/policies/apply", json=policy(), headers=CONTROL_HEADERS)
    assert saved.status_code == 200
    assert saved.json()["effective"]["routes"][0]["targets"][0]["eligible"] is True
    assert store.get_route_admission_v3(key=ROUTE_KEY, target=TARGET) is None
    response = client.post(DATA_URL, json=wire_request(), headers=DATA_HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result["routeTarget"] == {**TARGET, "routeIndex": 0}
    assert [day["state"] for day in result["days"]] == ["unknown", "suspended", "trading", "unknown", "unknown", "unknown"]
    assert result["routeTarget"]["upstreamSource"] == states.UPSTREAM_SOURCE
    assert any(e["reference"].endswith("1217557800.PDF") for e in result["evidence"] if e["kind"] == "document")
    path = Path(__file__).resolve().parents[2] / "thesis-ledger/packages/schemas/fixtures/market-trading-status-v3.response.json"
    if path.exists():
        sample = json.loads(path.read_text())["response"]
        assert result["provenance"]["sourceRevision"] == "cn-000995-daily-suspension-2023-08-v1"
        assert result["days"] == [{"date": day["date"], "state": day["state"],
            "evidenceRefs": day.get("sourceDocumentIds", []), "intradayHalts": []} for day in sample["days"]]


def test_data_authentication_is_preserved(configured_client):
    client, _runtime, _store = configured_client
    for headers in ({}, CONTROL_HEADERS):
        result = client.post(DATA_URL, json=wire_request(), headers={**headers, "x-request-id": "state-auth"})
        assert result.status_code == 401
        assert result.json()["detail"]["requestId"] == "state-auth"


def test_unconfigured_or_disabled_policy_does_not_read(configured_client):
    client, runtime, store = configured_client

    class NeverRead:
        def read(self, request):
            raise AssertionError("停用或未配置的来源不得读取")

    runtime.adapters[states.PROVIDER_ID] = NeverRead()
    for configured in (None, policy(enabled=False)):
        if configured:
            store.apply_policy_v3(configured)
        response = client.post(DATA_URL, json=wire_request(), headers=DATA_HEADERS)
        assert response.status_code == 503
        assert response.json()["detail"]["error"]["code"] == "upstream_failure"


@pytest.mark.parametrize("change,status,code", [
    ({"routeTarget": {**TARGET, "routeIndex": 1}}, 503, "upstream_failure"),
    ({"routeTarget": {**TARGET, "upstreamSource": "not-configured", "routeIndex": 0}}, 503, "upstream_failure"),
    ({"routeTarget": {**TARGET, "upstreamSource": "not-configured"}}, 422, "invalid_request"),
    ({"symbol": "600000.SH"}, 422, "insufficient_coverage"),
    ({"range": {"start": "2023-08-21", "end": "2023-08-16"}}, 422, "invalid_request"),
    ({"routeKey": {**ROUTE_KEY, "capability": "HISTORICAL_PUBLIC_FEES"}}, 422, "invalid_request"),
])
def test_bad_identity_pin_and_range_do_not_change_source(configured_client, change, status, code):
    client, _runtime, store = configured_client
    store.apply_policy_v3(policy())
    response = client.post(DATA_URL, json={**deepcopy(wire_request()), **change}, headers=DATA_HEADERS)
    assert response.status_code == status
    assert response.json()["detail"]["error"]["code"] == code


def test_policy_change_during_read_rejects_late_response(configured_client):
    client, runtime, store = configured_client
    store.apply_policy_v3(policy())

    class ChangedPolicySource:
        def read(self, request):
            result = states.OfficialTradingStatusProvider().read(request)
            store.apply_policy_v3(policy(revision=2, enabled=False))
            return result

    runtime.adapters[states.PROVIDER_ID] = ChangedPolicySource()
    response = client.post(DATA_URL, json=wire_request(), headers=DATA_HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "upstream_failure"


def test_missing_package_is_not_ready_or_silently_replaced(configured_client, monkeypatch, tmp_path):
    client, _runtime, store = configured_client
    monkeypatch.setattr(states, "DATA_PATH", tmp_path / "missing.json")
    entries = client.get(
        "/api/v1/thesis-ledger/control/routes/capabilities?contractVersion=1", headers=CONTROL_HEADERS,
    ).json()["entries"]
    assert next(row for row in entries if row["key"] == ROUTE_KEY and row["target"] == TARGET)["state"] != "ready"
    store.apply_policy_v3(policy())
    response = client.post(DATA_URL, json=wire_request(), headers=DATA_HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "upstream_failure"


def test_single_day_http_does_not_return_future_resume(configured_client):
    client, _runtime, store = configured_client
    store.apply_policy_v3(policy())
    payload = wire_request()
    payload["range"] = {"start": "2023-08-17", "end": "2023-08-17"}
    response = client.post(DATA_URL, json=payload, headers=DATA_HEADERS)
    assert response.status_code == 200
    assert response.json()["days"] == [{
        "date": "2023-08-17", "state": "suspended", "evidenceRefs": ["huangtai-2023-029"], "intradayHalts": [],
    }]
    assert "2023-08-18" not in response.text
