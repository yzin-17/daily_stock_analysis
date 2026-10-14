"""实际 Control、当前准入及鉴权 HTTP 使用公告生产包。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from tests import test_thesis_ledger_public_fee_routes as fee_routes
from src.services import thesis_ledger_notice_dividends as notices
from src.services.thesis_ledger_event_v3 import execute_event_request, EventV3Error
from api.thesis_ledger_events_v3 import router as event_router

KEY, TARGET = notices.ROUTE_KEY, notices.ROUTE_TARGET
configured_client = fee_routes.configured_client
CONTROL_HEADERS, DATA_HEADERS = fee_routes.CONTROL_HEADERS, fee_routes.DATA_HEADERS
URL = "/api/v3/thesis-ledger/market/events"


@pytest.fixture(autouse=True)
def expose_event_router(configured_client):
    configured_client[0].app.include_router(event_router, prefix="/api/v3")


def request(runtime):
    return {"contractVersion": 3, "requestId": "notice-http", "symbol": "510300.SH",
            "routeKey": KEY, "routeTarget": {**TARGET, "routeIndex": 0},
            "desiredRevision": 1, "effectivePolicyRevision": 1,
            "catalogRevision": runtime.market_route_catalog_v3()["catalogRevision"],
            "start": "2023-01-03", "end": "2023-02-01",
            "dataAsOf": datetime.now(timezone.utc).isoformat()}


def admit(store, evidence_digest=None):
    now = datetime.now(timezone.utc)
    digest = evidence_digest or sha256(notices.DATA_PATH.read_bytes()).hexdigest()
    revisions = notices.notice_dividend_revisions()
    store.record_route_admission_v3(
        key=KEY, target=TARGET, evidence_ref=f"sha256:{digest}", evidence_sha256=digest,
        scope_symbols=["510300.SH"], scope_date_from="2023-01-03", scope_date_to="2023-02-01",
        adapter_revision=revisions["adapterRevision"], source_revision=revisions["sourceRevision"],
        credential_revision=revisions["credentialRevision"], valid_from=(now - timedelta(minutes=1)).isoformat(),
        valid_until=(now + timedelta(days=1)).isoformat(), recorded_by="pytest-source-review",
    )
    store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "notice-policy",
                           "revision": 1, "enabled": True, "routes": [{"key": KEY, "targets": [TARGET]}]})


def test_real_source_requires_admission_and_preserves_exact_cash_scope(configured_client):
    client, runtime, store = configured_client
    entries = runtime.market_route_catalog_v3()["entries"]
    assert next(entry for entry in entries if entry["target"] == TARGET)["state"] == "not_admitted"
    admit(store)
    payload = request(runtime)
    result = client.post(URL, json=payload, headers=DATA_HEADERS)
    assert result.status_code == 200, result.text
    wire = result.json()
    assert wire["facts"][0]["cashAmount"] == "0.064"
    assert wire["coverage"] == {"complete": True, "admissionEvidenceRef": wire["noticeDividendEvidence"]["ref"]}
    assert sha256(wire["noticeDividendEvidence"]["content"].encode()).hexdigest() == wire["admission"]["evidenceSha256"]
    assert wire["providerRevision"] == notices.notice_dividend_revisions()["sourceRevision"]
    assert "recordedBy" not in wire["admission"]
    providers = client.get("/api/v3/thesis-ledger/control/providers", headers=CONTROL_HEADERS).json()["providers"]
    assert next(provider for provider in providers if provider["providerId"] == "official-dividends")["requiresCredential"] is False
    assert not any(entry["target"] == TARGET and entry["key"]["capability"] == "SPLIT_EVENT" for entry in entries)


@pytest.mark.parametrize("headers", [{}, CONTROL_HEADERS])
def test_data_auth_does_not_accept_control_token(configured_client, headers):
    client, runtime, store = configured_client
    admit(store)
    assert client.post(URL, json=request(runtime), headers=headers).status_code == 401


def test_false_evidence_digest_is_rejected(configured_client):
    _client, runtime, store = configured_client
    admit(store, "a" * 64)
    with pytest.raises(EventV3Error) as error:
        execute_event_request(runtime, request(runtime))
    assert error.value.code == "invalid_response"


@pytest.mark.parametrize("change", [{"symbol": "159516.SZ"}, {"start": "2023-01-02"},
                                    {"routeKey": {**KEY, "capability": "SPLIT_EVENT"}}])
def test_uncovered_or_other_capability_cannot_read(configured_client, change):
    client, runtime, store = configured_client
    admit(store)
    assert client.post(URL, json={**request(runtime), **change}, headers=DATA_HEADERS).status_code == 422


def test_policy_revision_during_read_drops_late_result(configured_client):
    _client, runtime, store = configured_client
    admit(store)

    def late_read(*args):
        result = notices.OfficialNoticeDividendProvider().read(*args)
        store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "disable-notice",
                               "revision": 2, "enabled": False, "routes": []})
        return result

    with pytest.raises(EventV3Error) as error:
        execute_event_request(runtime, request(runtime), reader=late_read)
    assert error.value.code == "policy_not_applied"


def test_changed_package_invalidates_current_admission(configured_client, monkeypatch, tmp_path):
    _client, runtime, store = configured_client
    path = tmp_path / "notice.json"
    path.write_bytes(notices.DATA_PATH.read_bytes())
    monkeypatch.setattr(notices, "DATA_PATH", path)
    admit(store)
    payload = request(runtime)
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(EventV3Error):
        execute_event_request(runtime, payload)
    entries = runtime.market_route_catalog_v3()["entries"]
    assert next(entry for entry in entries if entry["target"] == TARGET)["state"] != "ready"
