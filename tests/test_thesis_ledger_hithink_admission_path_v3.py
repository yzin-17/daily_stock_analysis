"""HiThink 普通股票价格采用当前凭证，旧准入记录只保留审计身份。"""

from __future__ import annotations

import pytest

from src.services.thesis_ledger_control import (
    PROVIDER_MANIFESTS,
    ThesisLedgerControlStore,
    _provider_credential_revision_from_snapshot,
)
from src.services.thesis_ledger_market_v3_adapters import HITHINK_STOCK_HISTORY_SOURCE
from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError, ThesisLedgerDataRequest, ThesisLedgerProviderRuntime,
)


KEY = {
    "kind": "bar", "market": "CN", "assetType": "STOCK", "capability": "DAILY_BAR",
    "timeframe": "1d", "adjustment": "qfq",
}
TARGET = {"providerId": "hithink", "upstreamSource": HITHINK_STOCK_HISTORY_SOURCE}


class _NeverCallAdapter:
    def __init__(self):
        self.calls = 0

    def get_market_bars_v3(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("stale admission must not call HiThink")


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("HITHINK_API_KEY", "synthetic-hithink-key-v1")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-route-master-key")
    result = ThesisLedgerControlStore(str(tmp_path / "hithink-admission-v3.sqlite"))
    result.apply_policy_v3({
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "hithink-admission-path",
        "revision": 1, "enabled": True, "routes": [{"key": KEY, "targets": [TARGET]}],
    })
    return result


def _admit(store, **overrides):
    snapshot = store.provider_credential_snapshot("hithink")
    credential_revision = _provider_credential_revision_from_snapshot(snapshot)
    revisions = market_v3_current_route_revisions(
        KEY, TARGET, PROVIDER_MANIFESTS["hithink"],
        credential_revision=credential_revision,
    )
    assert revisions is not None
    revisions.update(overrides)
    store.record_route_admission_v3(
        key=KEY, target=TARGET, evidence_ref="fixture://hithink-g0",
        evidence_sha256="a" * 64, scope_symbols=["000001.SZ"],
        scope_date_from="2026-04-30", scope_date_to="2026-08-09",
        valid_from="2026-01-01T00:00:00+00:00",
        valid_until="2027-01-01T00:00:00+00:00", recorded_by="pytest-fixture",
        **{
            "adapter_revision": revisions["adapterRevision"],
            "source_revision": revisions["sourceRevision"],
            "credential_revision": revisions["credentialRevision"],
        },
    )


def _effective_target(store):
    return store.effective_policy_v3()["routes"][0]["targets"][0]


def _catalog_state(store):
    catalog = ThesisLedgerProviderRuntime(store).market_route_catalog_v3()
    entry = next(item for item in catalog["entries"] if item["key"] == KEY and item["target"] == TARGET)
    return entry["state"]


def test_current_configured_price_is_effective_without_manual_admission(store):
    assert store.get_route_admission_v3(key=KEY, target=TARGET) is None
    assert _effective_target(store)["reason"] is None
    assert _catalog_state(store) == "ready"

    _admit(store)
    assert _catalog_state(store) == "ready"
    assert _effective_target(store)["eligible"] is True
    assert _effective_target(store)["reason"] is None
    reapplied = store.apply_policy_v3({
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "reapply",
        "revision": 1, "enabled": True, "routes": [{"key": KEY, "targets": [TARGET]}],
    })
    assert reapplied["idempotent"] is True
    assert reapplied["effective"]["routes"][0]["targets"][0]["eligible"] is True


@pytest.mark.parametrize("field", ["adapterRevision", "sourceRevision", "credentialRevision"])
def test_stale_legacy_revision_does_not_block_current_price_configuration(store, field):
    _admit(store, **{field: "stale-synthetic-revision"})
    assert store.get_route_admission_v3(key=KEY, target=TARGET)[field] == "stale-synthetic-revision"
    assert _catalog_state(store) == "ready"
    assert _effective_target(store)["reason"] is None


def test_current_price_credentials_and_legacy_record_revocation_are_independent(store, monkeypatch):
    _admit(store)
    assert _effective_target(store)["eligible"] is True

    original_revision = _provider_credential_revision_from_snapshot(store.provider_credential_snapshot("hithink"))
    monkeypatch.setenv("HITHINK_API_KEY", "synthetic-hithink-key-v2")
    current_revision = _provider_credential_revision_from_snapshot(store.provider_credential_snapshot("hithink"))
    assert current_revision and current_revision != original_revision
    assert _catalog_state(store) == "ready"
    assert _effective_target(store)["reason"] is None
    store.revoke_route_admission_v3(key=KEY, target=TARGET, reason="fixture revoke")
    assert store.get_route_admission_v3(key=KEY, target=TARGET)["admissionState"] == "revoked"
    assert _catalog_state(store) == "ready"

    monkeypatch.delenv("HITHINK_API_KEY")
    assert _catalog_state(store) == "credential_missing"
    assert _effective_target(store)["reason"] == "credential_missing"
    adapter = _NeverCallAdapter()
    runtime = ThesisLedgerProviderRuntime(store, adapters={"hithink": adapter})
    request = ThesisLedgerDataRequest(
        capability="DAILY_BAR", symbol="000001.SZ", timeframe="1d",
        start="2026-05-18", end="2026-05-20", instrument_type="STOCK",
        adjustment="qfq", request_id="rotated-hithink-fixture",
    )
    with pytest.raises(ProviderCallError) as error:
        runtime.execute_market_bars_v3(
            request, KEY, route_target={**TARGET, "routeIndex": 0},
        )
    assert error.value.code == "NO_ELIGIBLE_PROVIDER"
    assert adapter.calls == 0

def test_missing_audit_secret_does_not_disable_current_price_credentials(store, monkeypatch):
    _admit(store)
    monkeypatch.delenv("THESIS_LEDGER_DSA_SECRET_KEY")
    monkeypatch.delenv("DSA_SECRET_KEY", raising=False)
    assert _provider_credential_revision_from_snapshot(store.provider_credential_snapshot("hithink")) is None
    assert _catalog_state(store) == "ready"
    assert _effective_target(store)["reason"] is None
