"""真实 SQLite、公开事件入口与 RQData 身份/账号/隔离标准化组合。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_events_v3 import router
from data_provider.rqdata_fund_dividend_reader import fetch_rqdata_fund_dividends
from data_provider.rqdata_fund_split_reader import fetch_rqdata_fund_splits
from src.services.thesis_ledger_control import ThesisLedgerControlStore, _secret_key
from src.services.provider_credential_revision import provider_credential_revision
from src.services.thesis_ledger_event_v3 import EventV3Error, execute_event_request
from src.services.thesis_ledger_event_v3_adapters import EVENT_KEY, SPLIT_KEY, RQDATA_TARGET, RQDATA_REVISIONS
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from tests.test_rqdata_fund_identity_evidence import bundle, encode
from tests.test_rqdata_fund_event_process import working_factory, dividend_factory


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-rqdata-event-key")
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-data-token")
    store = ThesisLedgerControlStore(str(tmp_path / "control.sqlite"))
    store.save_provider_config("rqdata", {
        "requestId": "synthetic-credentials",
        "credentials": {"method": "username_password",
                        "values": {"username": "synthetic-user", "password": " secret "}},
    })
    value = bundle()
    value["mappings"][0]["observedAt"] = "2020-01-01T00:00:00Z"
    content = encode(value)
    proof = MappingEvidenceStore(tmp_path / "thesis-ledger-mapping-evidence").put(content)
    runtime = ThesisLedgerProviderRuntime(store)
    store.apply_policy_v3({
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "synthetic-policy",
        "revision": 1, "enabled": True,
        "routes": [{"key": key, "targets": [RQDATA_TARGET]} for key in (EVENT_KEY, SPLIT_KEY)],
    })

    def admit(key):
        revisions = RQDATA_REVISIONS[key["capability"]]
        credential = provider_credential_revision(store.provider_credential_snapshot("rqdata"), *_secret_key())
        store.record_route_admission_v3(
            key=key, target=RQDATA_TARGET, evidence_ref=proof, evidence_sha256=proof.removeprefix("sha256:"),
            scope_symbols=["159516.SZ"], scope_date_from="2025-01-01", scope_date_to="2025-12-31",
            valid_from="2020-01-01T00:00:00Z", valid_until="2099-01-01T00:00:00Z", recorded_by="pytest",
            credential_revision=credential, adapter_revision=revisions["adapterRevision"],
            source_revision=revisions["sourceRevision"],
        )

    def request(key):
        return {"contractVersion": 3, "requestId": "synthetic-rqdata-http", "symbol": "159516.SZ",
                "routeKey": key, "routeTarget": {**RQDATA_TARGET, "routeIndex": 0},
                "desiredRevision": 1, "effectivePolicyRevision": 1,
                "catalogRevision": runtime.market_route_catalog_v3()["catalogRevision"],
                "start": "2025-06-01", "end": "2025-06-30", "dataAsOf": "2099-01-01T00:00:00Z"}

    return store, runtime, admit, request, content, tmp_path


def controlled_reader(monkeypatch, *, ratio="1.25", change=None):
    def isolated(factory, kind, symbol, **options):
        assert (factory.username, factory.password) == ("synthetic-user", " secret ")
        assert options.pop("timeout_seconds") == 30
        assert options["query_fund_code"] == "159516" and options["instrument_type"] == "ETF"
        if kind == "split":
            frame = pd.DataFrame({"split_ratio": [ratio]}, index=pd.to_datetime(["2025-06-01"]))
            api = SimpleNamespace(fund=SimpleNamespace(get_split=Mock(return_value=frame)))
            reader = fetch_rqdata_fund_splits
        else:
            frame = pd.DataFrame({"dividend_before_tax": ["0.012"], "book_closure_date": ["2025-06-01"],
                                  "payable_date": ["2025-06-03"]}, index=pd.to_datetime(["2025-06-02"]))
            api = SimpleNamespace(fund=SimpleNamespace(get_dividend=Mock(return_value=frame)))
            reader = fetch_rqdata_fund_dividends
        result = reader(api, symbol, **options, before_call=lambda: None, after_call=lambda: None)
        if change:
            change()
        return result
    spy = Mock(side_effect=isolated)
    monkeypatch.setattr("src.services.thesis_ledger_rqdata_read.read_rqdata_fund_event_isolated", spy)
    return spy


@pytest.mark.parametrize("key", [EVENT_KEY, SPLIT_KEY])
def test_admitted_exact_route_retains_original_identity_and_actual_standardized_facts(state, monkeypatch, key):
    store, runtime, admit, request, content, _ = state
    admit(key)
    read = controlled_reader(monkeypatch)
    result = execute_event_request(runtime, request(key))
    assert result["identityEvidence"]["content"].encode() == content
    assert result["coverage"]["complete"] is False
    assert "dateMappingEvidence" not in result
    assert "recordedBy" not in result["admission"]
    assert result["facts"][0]["provider"] == "rqdata"
    assert result["providerRevision"] == result["facts"][0]["providerRevision"]
    if key == EVENT_KEY:
        assert result["facts"][0]["currency"] == "CNY"
    read.assert_called_once()


def test_reverse_split_is_an_economic_split_capability(state, monkeypatch):
    _, runtime, admit, request, *_ = state
    admit(SPLIT_KEY)
    controlled_reader(monkeypatch, ratio="0.5")
    result = execute_event_request(runtime, request(SPLIT_KEY))
    assert result["facts"][0]["type"] == "REVERSE_SPLIT"
    assert result["facts"][0]["ratio"] == "0.5"


def test_no_admission_never_decrypts_accounts_or_calls_sdk(state, monkeypatch):
    store, runtime, _, request, *_ = state
    credentials = Mock(side_effect=AssertionError("unexpected credentials"))
    original = store.provider_credential_snapshot
    monkeypatch.setattr(store, "provider_credential_snapshot",
                        lambda provider: credentials() if provider == "rqdata" else original(provider))
    read = controlled_reader(monkeypatch)
    payload = request(SPLIT_KEY)
    with pytest.raises(EventV3Error):
        execute_event_request(runtime, payload)
    credentials.assert_not_called()
    read.assert_not_called()


@pytest.mark.parametrize("change", ["revoke", "credentials", "policy", "file"])
def test_during_read_changes_reject_late_result_without_retry(state, monkeypatch, change):
    store, runtime, admit, request, _, root = state
    admit(SPLIT_KEY)
    payload = request(SPLIT_KEY)

    def mutate():
        if change == "revoke":
            store.revoke_route_admission_v3(key=SPLIT_KEY, target=RQDATA_TARGET, reason="synthetic-revoke")
        elif change == "credentials":
            store.save_provider_config("rqdata", {"requestId": "synthetic-rotation", "credentials": {
                "method": "username_password", "values": {"username": "new-user", "password": "new-password"}}})
        elif change == "policy":
            store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "synthetic-disable",
                                   "revision": 2, "enabled": False,
                                   "routes": [{"key": SPLIT_KEY, "targets": [RQDATA_TARGET]}]})
        else:
            proof = store.get_route_admission_v3(key=SPLIT_KEY, target=RQDATA_TARGET)
            (root / "thesis-ledger-mapping-evidence" / f'{proof["evidenceSha256"]}.json').write_bytes(b"tampered")

    read = controlled_reader(monkeypatch, change=mutate)
    with pytest.raises(EventV3Error):
        execute_event_request(runtime, payload)
    read.assert_called_once()


@pytest.mark.parametrize("key,factory", [(SPLIT_KEY, working_factory), (EVENT_KEY, dividend_factory)])
def test_authenticated_http_reaches_real_spawn_without_external_service(state, monkeypatch, key, factory):
    _, runtime, admit, request, content, _ = state
    admit(key)
    constructor = Mock(return_value=factory)
    monkeypatch.setattr("src.services.thesis_ledger_rqdata_read.RqDataClientFactory", constructor)
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    app = FastAPI()
    app.include_router(router, prefix="/api/v3")
    response = TestClient(app).post("/api/v3/thesis-ledger/market/events", json=request(key),
                                    headers={"Authorization": "Bearer synthetic-data-token"})
    assert response.status_code == 200, response.json()
    assert response.json()["identityEvidence"]["content"].encode() == content
    assert response.json()["coverage"]["complete"] is False
    constructor.assert_called_once_with("synthetic-user", " secret ")


def test_read_failure_http_does_not_expose_credentials_or_sdk_text(state, monkeypatch):
    _, runtime, admit, request, *_ = state
    admit(SPLIT_KEY)
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    read = Mock(side_effect=RuntimeError("synthetic-sensitive-detail"))
    monkeypatch.setattr("src.services.thesis_ledger_rqdata_read.read_rqdata_fund_event_isolated", read)
    app = FastAPI()
    app.include_router(router, prefix="/api/v3")
    response = TestClient(app).post("/api/v3/thesis-ledger/market/events", json=request(SPLIT_KEY),
                                    headers={"Authorization": "Bearer synthetic-data-token"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_failure"
    assert "synthetic-sensitive-detail" not in response.text and " secret " not in response.text
    read.assert_called_once()
