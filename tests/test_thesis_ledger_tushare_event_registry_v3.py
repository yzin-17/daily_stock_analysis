"""Tushare 精确事件库存、真实凭据修订与临时 SQLite 控制门禁。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services.thesis_ledger_control import (
    PROVIDER_MANIFESTS, ThesisLedgerControlStore, _provider_credential_revision_from_snapshot,
)
from src.services.thesis_ledger_event_v3_adapters import (
    EVENT_KEY, EVENT_REVISIONS, EVENT_TARGET, RQDATA_REVISIONS, RQDATA_TARGET,
    SPLIT_KEY, SPLIT_REVISIONS, TUSHARE_TARGET, event_adapter_matches,
    event_adapter_revisions, iter_event_adapters,
)
from src.services.thesis_ledger_hithink_dividend_contract_v3 import (
    HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET,
)
from src.services.thesis_ledger_market_v3_revisions import (
    _market_v3_admission_matches_current, market_v3_current_route_revisions,
)
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from src.services.thesis_ledger_tushare_mapped_read import (
    TUSHARE_FUND_DIV_ADAPTER_REVISION, TUSHARE_FUND_DIV_SOURCE_REVISION,
)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-registry-master")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "synthetic-v1")
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://synthetic.example/api")
    monkeypatch.setattr("src.config.get_config", lambda: SimpleNamespace(tushare_token="synthetic-token"))
    store = ThesisLedgerControlStore(str(tmp_path / "registry.sqlite"))
    runtime = ThesisLedgerProviderRuntime(store)
    store.apply_policy_v3({
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "synthetic-policy",
        "revision": 1, "enabled": True, "routes": [{"key": EVENT_KEY, "targets": [TUSHARE_TARGET]}],
    })
    credential = _provider_credential_revision_from_snapshot(store.provider_credential_snapshot("tushare"))
    assert credential is not None
    revisions = event_adapter_revisions(EVENT_KEY, TUSHARE_TARGET, credential)

    def admit():
        store.record_route_admission_v3(
            key=EVENT_KEY, target=TUSHARE_TARGET, evidence_ref="fixture://synthetic-registry",
            evidence_sha256="a" * 64, scope_symbols=["159516.SZ"],
            scope_date_from="2025-01-01", scope_date_to="2025-12-31",
            valid_from="2020-01-01T00:00:00Z", valid_until="2099-01-01T00:00:00Z",
            recorded_by="synthetic-pytest", adapter_revision=revisions["adapterRevision"],
            source_revision=revisions["sourceRevision"], credential_revision=credential,
        )

    return store, runtime, credential, admit


def test_inventory_preserves_existing_routes_with_exact_tushare_and_hithink_cash():
    entries = list(iter_event_adapters())
    assert entries == [
        (EVENT_KEY, EVENT_TARGET, True), (SPLIT_KEY, EVENT_TARGET, True),
        (EVENT_KEY, RQDATA_TARGET, True), (SPLIT_KEY, RQDATA_TARGET, True),
        (EVENT_KEY, TUSHARE_TARGET, True),
        (HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET, True),
    ]
    assert event_adapter_matches(EVENT_KEY, {**TUSHARE_TARGET, "routeIndex": 0})
    assert event_adapter_revisions(EVENT_KEY, {**TUSHARE_TARGET, "routeIndex": 0},
                                   "hmac-sha256-v1:" + "a" * 64) == {
        "adapterRevision": TUSHARE_FUND_DIV_ADAPTER_REVISION,
        "sourceRevision": TUSHARE_FUND_DIV_SOURCE_REVISION,
        "credentialRevision": "hmac-sha256-v1:" + "a" * 64,
    }
    assert event_adapter_revisions(EVENT_KEY, EVENT_TARGET) == EVENT_REVISIONS
    assert event_adapter_revisions(SPLIT_KEY, EVENT_TARGET) == SPLIT_REVISIONS
    credential = "hmac-sha256-v1:" + "a" * 64
    for key in (EVENT_KEY, SPLIT_KEY):
        assert event_adapter_revisions(key, RQDATA_TARGET, credential) == {
            **RQDATA_REVISIONS[key["capability"]], "credentialRevision": credential,
        }


@pytest.mark.parametrize("change", [
    {"capability": "SPLIT_EVENT"}, {"capability": "NAV"}, {"assetType": "STOCK"},
    {"market": "HK"}, {"kind": "catalog"}, {"adjustment": "none"},
])
def test_non_exact_keys_are_not_registered(change):
    key = {**EVENT_KEY, **change}
    assert not event_adapter_matches(key, TUSHARE_TARGET)
    assert event_adapter_revisions(key, TUSHARE_TARGET, "hmac-sha256-v1:" + "a" * 64) is None
    assert market_v3_current_route_revisions(key, TUSHARE_TARGET, PROVIDER_MANIFESTS["tushare"]) is None


@pytest.mark.parametrize("target", [
    {"providerId": "tushare", "upstreamSource": "eastmoney"},
    {"providerId": "akshare", "upstreamSource": "tushare"},
    {"providerId": "tushare", "upstreamSource": "unknown"},
])
def test_crossed_or_unknown_sources_are_not_registered(target):
    assert not event_adapter_matches(EVENT_KEY, target)
    assert event_adapter_revisions(EVENT_KEY, target, "hmac-sha256-v1:" + "a" * 64) is None


@pytest.mark.parametrize("credential", [None, 0, "0", "not-required", "hmac-sha256-v1:" + "A" * 64,
                                        "hmac-sha256-v1:" + "a" * 63])
def test_missing_or_invalid_current_credential_revision_refuses(credential):
    assert event_adapter_revisions(EVENT_KEY, TUSHARE_TARGET, credential) is None
    assert market_v3_current_route_revisions(
        EVENT_KEY, TUSHARE_TARGET, PROVIDER_MANIFESTS["tushare"],
        credential_version=0, credential_revision=credential,
    ) is None


@pytest.mark.parametrize("manifest", [
    {"providerId": "tushare", "requiresCredential": False},
    {"providerId": "tushare", "requiresCredential": None},
    {"providerId": "tushare", "requiresCredential": 1},
    {"providerId": "rqdata", "requiresCredential": True},
])
def test_manifest_requires_exact_provider_and_boolean_credential_requirement(manifest):
    assert market_v3_current_route_revisions(
        EVENT_KEY, TUSHARE_TARGET, manifest, credential_revision="hmac-sha256-v1:" + "a" * 64,
    ) is None


def test_real_sqlite_policy_and_current_matcher_use_actual_synthetic_hmac(state):
    store, runtime, credential, admit = state
    assert store.effective_policy_v3()["routes"][0]["targets"][0]["reason"] == "not_admitted"
    assert runtime._current_market_v3_admission(EVENT_KEY, TUSHARE_TARGET) is None
    admit()
    revisions = market_v3_current_route_revisions(
        EVENT_KEY, TUSHARE_TARGET, PROVIDER_MANIFESTS["tushare"], credential_revision=credential,
    )
    assert revisions == {
        "adapterRevision": TUSHARE_FUND_DIV_ADAPTER_REVISION,
        "sourceRevision": TUSHARE_FUND_DIV_SOURCE_REVISION, "credentialRevision": credential,
    }
    admission = store.get_route_admission_v3(key=EVENT_KEY, target=TUSHARE_TARGET)
    assert store.effective_policy_v3()["routes"][0]["targets"][0]["eligible"] is True
    assert _market_v3_admission_matches_current(
        admission, EVENT_KEY, TUSHARE_TARGET, PROVIDER_MANIFESTS["tushare"], credential_revision=credential,
    )
    assert runtime._current_market_v3_admission(EVENT_KEY, TUSHARE_TARGET) == admission
    assert not _market_v3_admission_matches_current(
        admission, EVENT_KEY, TUSHARE_TARGET, PROVIDER_MANIFESTS["tushare"], credential_version=0,
    )


@pytest.mark.parametrize("field,value", [
    ("adapterRevision", "stale-adapter"), ("sourceRevision", "stale-source"),
    ("credentialRevision", None), ("credentialRevision", "hmac-sha256-v1:" + "b" * 64),
    ("consumer", "other"), ("admissionState", "revoked"), ("scopeSymbols", []),
    ("scopeDateFrom", "invalid"), ("scopeDateTo", "2024-01-01"),
])
def test_actual_current_matcher_rejects_stale_revision_and_invalid_scope(state, field, value):
    store, _, credential, admit = state
    admit()
    admission = deepcopy(store.get_route_admission_v3(key=EVENT_KEY, target=TUSHARE_TARGET))
    admission[field] = value
    assert not _market_v3_admission_matches_current(
        admission, EVENT_KEY, TUSHARE_TARGET, PROVIDER_MANIFESTS["tushare"], credential_revision=credential,
    )


@pytest.mark.parametrize("change", ["token", "endpoint", "master", "revoke"])
def test_runtime_current_admission_refuses_actual_rotation_or_revocation(state, monkeypatch, change):
    store, runtime, _, admit = state
    admit()
    assert runtime._current_market_v3_admission(EVENT_KEY, TUSHARE_TARGET) is not None
    if change == "token":
        monkeypatch.setattr("src.config.get_config", lambda: SimpleNamespace(tushare_token="synthetic-rotated"))
    elif change == "endpoint":
        monkeypatch.setenv("TUSHARE_HTTP_URL", "https://synthetic-rotated.example/api")
    elif change == "master":
        monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-rotated-master")
    else:
        store.revoke_route_admission_v3(key=EVENT_KEY, target=TUSHARE_TARGET, reason="synthetic-revoke")
    assert runtime._current_market_v3_admission(EVENT_KEY, TUSHARE_TARGET) is None
