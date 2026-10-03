"""真实 SQLite 策略与合成 HiThink HTTP 结果的精确分红执行闭环。"""

from datetime import datetime, timezone
from hashlib import sha256
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services.thesis_ledger_control import (
    PROVIDER_MANIFESTS, ThesisLedgerControlStore, _provider_credential_revision_from_snapshot,
)
from src.services.thesis_ledger_event_v3 import EventV3Error, execute_event_request
from src.services.thesis_ledger_catalog_manifest_v3 import catalog_manifest_matches_v3
from src.services.thesis_ledger_hithink_dividend_contract_v3 import (
    HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET,
)
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from api.thesis_ledger_events_v3 import router
from tests.test_hithink_fund_identity_evidence import SYMBOL, evidence
from tests.test_thesis_ledger_hithink_mapped_dividend_read import millis


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("HITHINK_API_KEY", "synthetic-event-key")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-event-master")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "v1")
    monkeypatch.delenv("DSA_SECRET_KEY", raising=False)
    store = ThesisLedgerControlStore(str(tmp_path / "control.sqlite"))
    runtime = ThesisLedgerProviderRuntime(store)
    content, _ = evidence()
    MappingEvidenceStore(tmp_path / "thesis-ledger-mapping-evidence").put(content)
    store.apply_policy_v3({
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "synthetic-hithink-policy",
        "revision": 1, "enabled": True,
        "routes": [{"key": HITHINK_DIVIDEND_KEY, "targets": [HITHINK_DIVIDEND_TARGET]}],
    })
    credential_revision = _provider_credential_revision_from_snapshot(
        store.provider_credential_snapshot("hithink"),
    )
    revisions = market_v3_current_route_revisions(
        HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET, PROVIDER_MANIFESTS["hithink"],
        credential_revision=credential_revision,
    )
    assert revisions is not None
    digest = sha256(content).hexdigest()

    def admit():
        return store.record_route_admission_v3(
            key=HITHINK_DIVIDEND_KEY, target=HITHINK_DIVIDEND_TARGET,
            evidence_ref=f"sha256:{digest}", evidence_sha256=digest,
            scope_symbols=[SYMBOL], scope_date_from="2025-01-01", scope_date_to="2026-12-31",
            valid_from="2026-01-01T00:00:00Z", valid_until="2027-01-01T00:00:00Z",
            recorded_by="synthetic-test", adapter_revision=revisions["adapterRevision"],
            source_revision=revisions["sourceRevision"], credential_revision=revisions["credentialRevision"],
        )

    def request():
        return {
            "contractVersion": 3, "requestId": "synthetic-hithink-dividend", "symbol": SYMBOL,
            "routeKey": HITHINK_DIVIDEND_KEY,
            "routeTarget": {**HITHINK_DIVIDEND_TARGET, "routeIndex": 0},
            "desiredRevision": 1, "effectivePolicyRevision": 1,
            "catalogRevision": runtime.market_route_catalog_v3()["catalogRevision"],
            "start": "2025-06-01", "end": "2025-06-30", "dataAsOf": "2099-01-01T00:00:00Z",
        }

    def fetch(_symbol, **_kwargs):
        return {
            "symbol": SYMBOL, "fundType": "exchange", "responseSha256": "c" * 64,
            "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "historyComplete": False,
            "items": [{"progress": "实施", "publish_date_ms": millis("2025-06-01"),
                       "registration_date_ms": millis("2025-06-17"),
                       "ex_dividend_date_ms": millis("2025-06-18"),
                       "payment_date_ms": millis("2025-06-27"),
                       "per_ten_cash_before_tax": "0.880"}],
        }

    return {"store": store, "runtime": runtime, "content": content, "admit": admit,
            "request": request, "fetch": Mock(side_effect=fetch), "monkeypatch": monkeypatch,
            "evidence_dir": tmp_path / "thesis-ledger-mapping-evidence"}


def catalog_state(state):
    entries = state["runtime"].market_route_catalog_v3()["entries"]
    return next(item["state"] for item in entries if item["key"] == HITHINK_DIVIDEND_KEY
                and item["target"] == HITHINK_DIVIDEND_TARGET)


def target(state):
    return state["store"].effective_policy_v3()["routes"][0]["targets"][0]


def test_exact_inventory_stays_denied_until_admission_then_executes_own_reader(state):
    assert catalog_state(state) == "not_admitted"
    assert target(state)["reason"] == "not_admitted"
    with pytest.raises(EventV3Error, match="not_admitted"):
        execute_event_request(state["runtime"], state["request"](), reader=state["fetch"])
    state["fetch"].assert_not_called()

    state["admit"]()
    assert catalog_state(state) == "ready"
    assert target(state)["eligible"] is True
    reapplied = state["store"].apply_policy_v3({
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "synthetic-reapply",
        "revision": 1, "enabled": True,
        "routes": [{"key": HITHINK_DIVIDEND_KEY, "targets": [HITHINK_DIVIDEND_TARGET]}],
    })
    assert reapplied["idempotent"] is True
    assert reapplied["effective"]["routes"][0]["targets"][0]["eligible"] is True
    result = execute_event_request(state["runtime"], state["request"](), reader=state["fetch"])
    state["fetch"].assert_called_once()
    assert state["fetch"].call_args.args == (SYMBOL,)
    assert state["fetch"].call_args.kwargs["fund_type"] == "exchange"
    assert state["fetch"].call_args.kwargs["api_key"] == "synthetic-event-key"
    assert result["hithinkIdentityEvidence"]["content"].encode() == state["content"]
    assert result["facts"][0]["cashAmount"] == "0.088"
    assert result["coverage"] == {"complete": False, "reason": "historical_coverage_unverified"}
    assert result["admission"].get("recordedBy") is None


def test_missing_evidence_does_not_make_catalog_ready_or_fetch(state):
    state["admit"]()
    digest = sha256(state["content"]).hexdigest()
    (state["evidence_dir"] / f"{digest}.json").unlink()
    assert catalog_state(state) == "not_admitted"
    assert target(state)["reason"] == "not_admitted"
    with pytest.raises(EventV3Error, match="not_admitted"):
        execute_event_request(state["runtime"], state["request"](), reader=state["fetch"])
    state["fetch"].assert_not_called()


def test_rotated_key_or_revoked_admission_closes_policy_catalog_and_reader(state):
    state["admit"]()
    state["monkeypatch"].setenv("HITHINK_API_KEY", "synthetic-rotated-key")
    assert catalog_state(state) == "not_admitted"
    assert target(state)["reason"] == "not_admitted"
    with pytest.raises(EventV3Error, match="not_admitted"):
        execute_event_request(state["runtime"], state["request"](), reader=state["fetch"])
    state["fetch"].assert_not_called()
    state["monkeypatch"].setenv("HITHINK_API_KEY", "synthetic-event-key")
    state["store"].revoke_route_admission_v3(
        key=HITHINK_DIVIDEND_KEY, target=HITHINK_DIVIDEND_TARGET, reason="synthetic-revoke",
    )
    assert catalog_state(state) == "not_admitted"
    assert target(state)["reason"] == "not_admitted"


@pytest.mark.parametrize("change", [
    {"markets": None}, {"upstreamSources": None},
    {"upstreamSources": [{"sourceId": HITHINK_DIVIDEND_TARGET["upstreamSource"],
                          "capabilities": None}]},
])
def test_malformed_manifest_never_claims_exact_hithink_event_capability(change):
    manifest = {**PROVIDER_MANIFESTS["hithink"], **change}
    assert not catalog_manifest_matches_v3(manifest, HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET)


def test_authenticated_http_uses_exact_hithink_reader_and_denies_missing_token(state, monkeypatch):
    import src.services.thesis_ledger_hithink_event_v3 as event_module
    import src.services.thesis_ledger_provider_runtime as runtime_module
    from src.services.thesis_ledger_hithink_mapped_dividend_read import read_hithink_mapped_fund_dividends

    state["admit"]()
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-dsa-token")
    monkeypatch.setattr(runtime_module, "get_thesis_ledger_runtime", lambda: state["runtime"])
    monkeypatch.setattr(event_module, "read_hithink_mapped_fund_dividends",
                        lambda symbol, **options: read_hithink_mapped_fund_dividends(
                            symbol, **options, fetch=state["fetch"],
                        ))
    app = FastAPI()
    app.include_router(router, prefix="/api/v3")
    client = TestClient(app)
    request = state["request"]()
    denied = client.post("/api/v3/thesis-ledger/market/events", json=request)
    assert denied.status_code in (401, 403)
    state["fetch"].assert_not_called()
    response = client.post(
        "/api/v3/thesis-ledger/market/events", json=request,
        headers={"Authorization": "Bearer synthetic-dsa-token"},
    )
    assert response.status_code == 200
    assert response.json()["hithinkIdentityEvidence"]["content"].encode() == state["content"]
    assert response.json()["coverage"]["complete"] is False
    state["fetch"].assert_called_once()


@pytest.mark.parametrize("change", ["revoked", "key", "progress"])
def test_late_revocation_rotation_or_unknown_progress_never_returns_success(state, change):
    state["admit"]()
    original = state["fetch"].side_effect

    def fetch(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "revoked":
            state["store"].revoke_route_admission_v3(
                key=HITHINK_DIVIDEND_KEY, target=HITHINK_DIVIDEND_TARGET, reason="synthetic-revoke",
            )
        elif change == "key":
            state["monkeypatch"].setenv("HITHINK_API_KEY", "synthetic-rotated-key")
        else:
            result["items"][0]["progress"] = "2"
        return result

    with pytest.raises(EventV3Error):
        execute_event_request(state["runtime"], state["request"](), reader=fetch)
