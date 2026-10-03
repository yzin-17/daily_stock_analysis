"""共同 ETF 价格以外的 HiThink 股票能力仍使用独立准入。"""

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


def test_exact_current_admission_can_make_hithink_v3_effective(store):
    assert _effective_target(store)["reason"] == "not_admitted"
    assert _catalog_state(store) == "not_admitted"

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
def test_stale_revision_keeps_hithink_v3_policy_denied(store, field):
    _admit(store, **{field: "stale-synthetic-revision"})
    assert _catalog_state(store) == "not_admitted"
    assert _effective_target(store)["reason"] == "not_admitted"


def test_credential_rotation_and_revocation_reclose_hithink_v3(store, monkeypatch):
    _admit(store)
    assert _effective_target(store)["eligible"] is True

    monkeypatch.setenv("HITHINK_API_KEY", "synthetic-hithink-key-v2")
    assert _catalog_state(store) == "not_admitted"
    assert _effective_target(store)["reason"] == "not_admitted"
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

    monkeypatch.setenv("HITHINK_API_KEY", "synthetic-hithink-key-v1")
    store.revoke_route_admission_v3(key=KEY, target=TARGET, reason="fixture revoke")
    assert _catalog_state(store) == "not_admitted"
    assert _effective_target(store)["reason"] == "not_admitted"


def test_missing_revision_secret_keeps_hithink_v3_denied(store, monkeypatch):
    _admit(store)
    monkeypatch.delenv("THESIS_LEDGER_DSA_SECRET_KEY")
    monkeypatch.delenv("DSA_SECRET_KEY", raising=False)
    assert _catalog_state(store) == "not_admitted"
    assert _effective_target(store)["reason"] == "not_admitted"
