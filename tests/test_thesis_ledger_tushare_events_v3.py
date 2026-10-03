"""临时 SQLite/原文/实际 HTTP Reader 与进程内鉴权组合，零外部请求。"""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.thesis_ledger_events_v3 import router
from src.services import thesis_ledger_control as control
from src.services.thesis_ledger_control import ThesisLedgerControlStore, _provider_credential_revision_from_snapshot
from src.services.thesis_ledger_event_v3 import EventV3Error, execute_event_request
from src.services.thesis_ledger_event_v3_adapters import EVENT_KEY, TUSHARE_TARGET, event_adapter_revisions
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from tests.test_tushare_fund_identity_evidence import bundle


@pytest.fixture
def state(tmp_path, monkeypatch):
    config = SimpleNamespace(tushare_token="synthetic-event-token")
    monkeypatch.setattr("src.config.get_config", lambda: config)
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-event-master")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "synthetic-v1")
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-data-token")
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://synthetic.example.test/api")
    monkeypatch.delenv("DSA_SECRET_KEY", raising=False)
    store = ThesisLedgerControlStore(str(tmp_path / "control.sqlite"))
    store.save_provider_config("rqdata", {"requestId": "synthetic-control-account", "credentials": {
        "method": "username_password", "values": {"username": "synthetic-user", "password": "synthetic-password"},
    }})
    runtime = ThesisLedgerProviderRuntime(store)
    value = bundle()
    value["mappings"][0]["observedAt"] = "2020-01-01T00:00:00Z"
    value["mappings"][0]["identityEvidence"]["documentUrl"] = "https://identity.example.test/基金"
    content = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    evidence = MappingEvidenceStore(tmp_path / "thesis-ledger-mapping-evidence")
    proof = evidence.put(content)
    revision = _provider_credential_revision_from_snapshot(store.provider_credential_snapshot("tushare"))
    revisions = event_adapter_revisions(EVENT_KEY, TUSHARE_TARGET, revision)
    options = dict(key=EVENT_KEY, target=TUSHARE_TARGET, evidence_ref=proof,
                   evidence_sha256=proof[7:], scope_symbols=["159516.SZ"],
                   scope_date_from="2025-01-01", scope_date_to="2025-12-31",
                   valid_from="2020-01-01T00:00:00Z", valid_until="2099-01-01T00:00:00Z",
                   recorded_by="synthetic-pytest", adapter_revision=revisions["adapterRevision"],
                   source_revision=revisions["sourceRevision"], credential_revision=revision)
    store.record_route_admission_v3(**options)
    policy = {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "synthetic-policy",
              "revision": 1, "enabled": True, "routes": [{"key": EVENT_KEY, "targets": [TUSHARE_TARGET]}]}
    store.apply_policy_v3(policy)
    request = {"contractVersion": 3, "requestId": "synthetic-event", "symbol": "159516.SZ",
               "routeKey": EVENT_KEY, "routeTarget": {**TUSHARE_TARGET, "routeIndex": 0},
               "desiredRevision": 1, "effectivePolicyRevision": 1,
               "catalogRevision": runtime.market_route_catalog_v3()["catalogRevision"],
               "start": "2025-02-01", "end": "2025-03-01", "dataAsOf": "2098-01-01T00:00:00Z"}
    # 原始金额为 JSON number，实际 Requests/Decimal 解析必须保留所有小数。
    text = ('{"code":0,"data":{"fields":["ts_code","ann_date","imp_anndate","div_proc",'
            '"record_date","ex_date","pay_date","div_cash"],"items":'
            '[["159516.SZ","20250201","20250202","实施","20250219","20250220","20250221",'
            '0.01512345678901234567890123456789]]}}')
    source = Mock(return_value=Mock(status_code=200, text=text))
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", source)
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    app = FastAPI()
    app.include_router(router, prefix="/api/v3")
    return SimpleNamespace(store=store, runtime=runtime, request=request, config=config, content=content,
                           proof=proof, evidence=evidence, options=options, policy=policy, source=source,
                           client=TestClient(app), root=tmp_path)


def call(state):
    return execute_event_request(state.runtime, state.request)


def http(state, authorization="Bearer synthetic-data-token"):
    return state.client.post("/api/v3/thesis-ledger/market/events", json=state.request,
                             headers={} if authorization is None else {"Authorization": authorization})


def test_production_reader_preserves_raw_identity_decimal_dates_and_snapshot(state, monkeypatch):
    builder = Mock(wraps=state.runtime._adapter)
    monkeypatch.setattr(state.runtime, "_adapter", builder)
    result = call(state)
    identity = result["tushareIdentityEvidence"]
    assert identity == {"ref": state.proof, "sha256": state.proof[7:], "content": state.content.decode()}
    assert "identityEvidence" not in result and "dateMappingEvidence" not in result
    assert "retrieval" not in result and "observations" not in result
    assert result["coverage"]["complete"] is False
    admission = state.store.get_route_admission_v3(key=EVENT_KEY, target=TUSHARE_TARGET)
    assert result["admission"] == {k: v for k, v in admission.items() if k != "recordedBy"}
    fact = result["facts"][0]
    assert fact["cashAmount"] == "0.01512345678901234567890123456789" and fact["currency"] == "CNY"
    assert (fact["effectiveDate"], fact["recordDate"], fact["paymentDate"]) == (
        "2025-02-20", "2025-02-19", "2025-02-21",
    )
    assert fact["strategyVisibility"] == {"kind": "conservative-day", "visibleDate": "2025-02-02"}
    snapshot = builder.call_args.kwargs["snapshot"]
    with pytest.raises(TypeError):
        snapshot.values["token"] = "mutated"
    assert state.source.call_args.args == (snapshot.values["httpUrl"],)
    args = state.source.call_args.kwargs
    assert args["json"]["token"] == snapshot.values["token"]
    assert args["json"]["api_name"] == "fund_div" and args["json"]["params"] == {"ts_code": "159516.SZ"}
    assert args["allow_redirects"] is False and 0 < args["timeout"] <= 30
    assert fact["providerRevision"] == result["providerRevision"]
    assert result["providerRevision"].startswith("tushare-fund-div-content-v1:")
    state.source.assert_called_once()


@pytest.mark.parametrize("change", [
    "missing", "file", "tamper", "revoke", "expired", "scope", "adapter", "source", "currency", "future",
])
def test_invalid_proof_rejects_before_policy_catalog_and_every_account_path(state, monkeypatch, change):
    path = state.root / "thesis-ledger-mapping-evidence" / (state.proof[7:] + ".json")
    if change == "missing":
        with state.store._connect() as connection:
            connection.execute("DELETE FROM thesis_ledger_route_admission_v3")
    elif change == "file":
        path.unlink()
    elif change == "tamper":
        path.write_bytes(b"broken")
    elif change == "revoke":
        state.store.revoke_route_admission_v3(key=EVENT_KEY, target=TUSHARE_TARGET, reason="synthetic-revoked")
    elif change in {"expired", "scope", "adapter", "source"}:
        values = {"expired": {"valid_until": "2021-01-01T00:00:00Z"},
                  "scope": {"scope_symbols": ["510300.SH"]}, "adapter": {"adapter_revision": "stale"},
                  "source": {"source_revision": "stale"}}[change]
        state.store.record_route_admission_v3(**{**state.options, **values})
    else:
        value = json.loads(state.content)
        if change == "currency":
            del value["mappings"][0]["dividendCurrencyEvidence"]
        else:
            value["mappings"][0]["observedAt"] = "2099-01-01T00:00:00.000000001Z"
        proof = state.evidence.put(json.dumps(value).encode())
        state.store.record_route_admission_v3(**{**state.options, "evidence_ref": proof, "evidence_sha256": proof[7:]})
    spies = []
    for name in ("_credential_state", "_environment_credential_values", "_decrypt_secret"):
        spy = Mock(wraps=getattr(control, name))
        monkeypatch.setattr(control, name, spy)
        spies.append(spy)
    for name in ("provider_credential_snapshot", "effective_policy_v3", "provider_registry"):
        spy = Mock(wraps=getattr(state.store, name))
        monkeypatch.setattr(state.store, name, spy)
        spies.append(spy)
    builder = Mock(wraps=state.runtime._adapter)
    monkeypatch.setattr(state.runtime, "_adapter", builder)
    with pytest.raises(EventV3Error, match="not_admitted"):
        call(state)
    for spy in spies:
        spy.assert_not_called()
    builder.assert_not_called()
    state.source.assert_not_called()


@pytest.mark.parametrize("field", ["identityEvidence", "dividendCurrencyEvidence"])
def test_document_authority_backslash_rejects_before_policy_catalog_and_account_paths(state, monkeypatch, field):
    value = json.loads(state.content)
    value["mappings"][0][field]["documentUrl"] = "https://identity.example.test\\other/doc"
    proof = state.evidence.put(json.dumps(value).encode())
    state.store.record_route_admission_v3(**{**state.options, "evidence_ref": proof, "evidence_sha256": proof[7:]})
    spies = []
    for name in ("_credential_state", "_environment_credential_values", "_decrypt_secret"):
        spy = Mock(wraps=getattr(control, name))
        monkeypatch.setattr(control, name, spy)
        spies.append(spy)
    for name in ("provider_credential_snapshot", "effective_policy_v3", "provider_registry"):
        spy = Mock(wraps=getattr(state.store, name))
        monkeypatch.setattr(state.store, name, spy)
        spies.append(spy)
    builder = Mock(wraps=state.runtime._adapter)
    monkeypatch.setattr(state.runtime, "_adapter", builder)
    with pytest.raises(EventV3Error, match="^not_admitted$"):
        call(state)
    for spy in spies:
        spy.assert_not_called()
    builder.assert_not_called()
    state.source.assert_not_called()


@pytest.mark.parametrize("field", ["identityEvidence", "dividendCurrencyEvidence"])
@pytest.mark.parametrize("url", ["https://%/doc", "https://%GG.test/doc", "https://\ud800.test/doc"])
def test_document_invalid_host_rejects_before_policy_catalog_and_account_paths(state, monkeypatch, field, url):
    value = json.loads(state.content)
    value["mappings"][0][field]["documentUrl"] = url
    proof = state.evidence.put(json.dumps(value).encode())
    state.store.record_route_admission_v3(**{**state.options, "evidence_ref": proof, "evidence_sha256": proof[7:]})
    spies = []
    for name in ("_credential_state", "_environment_credential_values", "_decrypt_secret"):
        spy = Mock(wraps=getattr(control, name))
        monkeypatch.setattr(control, name, spy)
        spies.append(spy)
    for name in ("provider_credential_snapshot", "effective_policy_v3", "provider_registry"):
        spy = Mock(wraps=getattr(state.store, name))
        monkeypatch.setattr(state.store, name, spy)
        spies.append(spy)
    builder = Mock(wraps=state.runtime._adapter)
    monkeypatch.setattr(state.runtime, "_adapter", builder)
    with pytest.raises(EventV3Error, match="^not_admitted$"):
        call(state)
    for spy in spies:
        spy.assert_not_called()
    builder.assert_not_called()
    state.source.assert_not_called()


@pytest.mark.parametrize("change", ["token", "endpoint", "master", "version", "hmac"])
def test_current_credential_mismatch_never_calls_source(state, monkeypatch, change):
    if change == "token":
        state.config.tushare_token = "synthetic-rotated-token"
    elif change == "endpoint":
        monkeypatch.setenv("TUSHARE_HTTP_URL", "https://synthetic-rotated.example.test/api")
    elif change == "master":
        monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-rotated-master")
    elif change == "version":
        monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "synthetic-v2")
    else:
        state.store.record_route_admission_v3(**{**state.options, "credential_revision": "hmac-sha256-v1:" + "0" * 64})
    with pytest.raises(EventV3Error):
        call(state)
    state.source.assert_not_called()


@pytest.mark.parametrize("change", [
    "revoke", "adapter", "source", "record", "file", "token", "endpoint", "master", "version",
    "policy", "targets", "provider-disabled", "catalog",
])
def test_late_results_refuse_every_current_state_mutation_without_retry(state, monkeypatch, change):
    response = state.source.return_value

    def mutate(*args, **kwargs):
        if change == "revoke":
            state.store.revoke_route_admission_v3(key=EVENT_KEY, target=TUSHARE_TARGET, reason="synthetic-revoke")
        elif change in {"adapter", "source", "record"}:
            values = {"recorded_by": "synthetic-reapproval"} if change == "record" else {
                change + "_revision": "synthetic-stale",
            }
            state.store.record_route_admission_v3(**{**state.options, **values})
        elif change == "file":
            (state.root / "thesis-ledger-mapping-evidence" / (state.proof[7:] + ".json")).write_bytes(b"changed")
        elif change == "token":
            state.config.tushare_token = "synthetic-rotated-token"
        elif change == "endpoint":
            monkeypatch.setenv("TUSHARE_HTTP_URL", "https://synthetic-rotated.example.test/api")
        elif change == "master":
            monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-rotated-master")
        elif change == "version":
            monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "synthetic-v2")
        elif change in {"policy", "targets"}:
            policy = deepcopy(state.policy)
            policy.update(revision=2, enabled=change != "policy")
            if change == "targets":
                policy["routes"][0]["targets"] = [{"providerId": "akshare", "upstreamSource": "eastmoney"}]
            state.store.apply_policy_v3(policy)
        elif change == "provider-disabled":
            state.store.save_provider_config("tushare", {"requestId": "synthetic-disabled", "enabled": False})
        else:
            key = {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "SPLIT_EVENT"}
            target = {"providerId": "akshare", "upstreamSource": "eastmoney"}
            state.store.record_route_admission_v3(**{
                **state.options, "key": key, "target": target,
                "adapter_revision": "dsa-eastmoney-fund-split-mapped-v1",
                "source_revision": "eastmoney-fund-cf-pageinfo-json-v1", "credential_revision": "not-required",
            })
        return response

    state.source.side_effect = mutate
    with pytest.raises(EventV3Error):
        call(state)
    state.source.assert_called_once()


def test_empty_history_keeps_actual_stable_content_revision_and_incomplete_coverage(state):
    value = json.loads(state.source.return_value.text)
    value["data"]["items"] = []
    state.source.return_value.text = json.dumps(value)
    first, second = call(state), call(state)
    assert first["facts"] == second["facts"] == []
    assert first["providerRevision"] == second["providerRevision"]
    assert first["coverage"]["complete"] is second["coverage"]["complete"] is False
    assert state.runtime.adapters["tushare"]._call_count == 2


@pytest.mark.parametrize("failure", [PermissionError("synthetic-sensitive-token"), TimeoutError("synthetic-sensitive-url")])
def test_upstream_failure_is_deidentified_single_attempt(state, failure):
    state.source.side_effect = failure
    with pytest.raises(EventV3Error) as error:
        call(state)
    assert str(error.value) == "upstream_failure"
    state.source.assert_called_once()


@pytest.mark.parametrize("authorization", [None, "Bearer wrong"])
def test_http_invalid_authorization_never_enters_runtime(state, monkeypatch, authorization):
    runtime = Mock(side_effect=AssertionError("unexpected runtime"))
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", runtime)
    assert http(state, authorization).status_code == 401
    runtime.assert_not_called()
    state.source.assert_not_called()


def test_http_missing_configured_bearer_never_executes(state, monkeypatch):
    monkeypatch.delenv("THESIS_LEDGER_DSA_TOKEN")
    assert http(state).status_code == 503
    state.source.assert_not_called()


def test_http_missing_identity_never_decrypts_other_configured_control_accounts(state, monkeypatch):
    (state.root / "thesis-ledger-mapping-evidence" / (state.proof[7:] + ".json")).unlink()
    decrypt = Mock(wraps=control._decrypt_secret)
    credential = Mock(wraps=control._credential_state)
    monkeypatch.setattr(control, "_decrypt_secret", decrypt)
    monkeypatch.setattr(control, "_credential_state", credential)
    response = http(state)
    assert response.status_code == 422 and response.json()["error"]["code"] == "not_admitted"
    decrypt.assert_not_called()
    credential.assert_not_called()
    state.source.assert_not_called()


def test_file_change_during_final_policy_catalog_review_is_rejected(state, monkeypatch):
    from src.services import thesis_ledger_event_v3 as events

    original = events._admitted_state
    calls = []

    def check(runtime, request):
        value = original(runtime, request)
        calls.append(value)
        if len(calls) == 2:
            (state.root / "thesis-ledger-mapping-evidence" / (state.proof[7:] + ".json")).write_bytes(b"late-change")
        return value

    monkeypatch.setattr(events, "_admitted_state", check)
    with pytest.raises(EventV3Error, match="not_admitted"):
        call(state)
    assert len(calls) == 2
    state.source.assert_called_once()


def test_http_upstream_failure_keeps_public_error_deidentified(state):
    state.source.side_effect = RuntimeError("synthetic-sensitive-account-url")
    response = http(state)
    assert response.status_code == 503 and response.json()["error"]["code"] == "upstream_failure"
    assert "synthetic-sensitive" not in response.text and "facts" not in response.json()
    state.source.assert_called_once()


@pytest.mark.parametrize("change,status", [("rotate", 401), ("clear", 503)])
def test_http_bearer_rotation_during_actual_source_read_refuses_late_response(state, monkeypatch, change, status):
    original = state.source.return_value

    def mutate(*args, **kwargs):
        if change == "rotate":
            monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-rotated-bearer")
        else:
            monkeypatch.delenv("THESIS_LEDGER_DSA_TOKEN")
        return original

    state.source.side_effect = mutate
    response = http(state)
    assert response.status_code == status
    assert "synthetic-rotated-bearer" not in response.text and "facts" not in response.json()
    state.source.assert_called_once()


def test_http_success_consumes_actual_built_schema_exchange_without_rebuild(state, tmp_path):
    response = http(state)
    assert response.status_code == 200, response.json()
    exchange = tmp_path / "synthetic-exchange.json"
    exchange.write_text(json.dumps({"request": state.request, "response": response.json()}, ensure_ascii=False))
    schemas = Path(__file__).resolve().parents[2] / "thesis-ledger" / "packages" / "schemas" / "dist" / "index.js"
    script = ('const fs=require("fs");const s=require(process.argv[1]);'
              'const value=JSON.parse(fs.readFileSync(process.argv[2],"utf8"));'
              's.marketEventExchangeV3Schema.parse(value); console.log("synthetic exchange passed");')
    result = subprocess.run(["rtk", "proxy", "node", "-e", script, str(schemas), str(exchange)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "synthetic exchange passed"
    state.source.assert_called_once()
