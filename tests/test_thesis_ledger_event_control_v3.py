"""真实 SQLite 策略必须允许精确事件库存，同时保留准入及撤销门禁。"""

from unittest.mock import Mock

import pytest

from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_event_v3 import EventV3Error, execute_event_request
from src.services.thesis_ledger_event_v3_adapters import EVENT_KEY, EVENT_REVISIONS, EVENT_TARGET
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime


def test_real_policy_event_admission_and_revocation(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "events.sqlite"))
    runtime = ThesisLedgerProviderRuntime(store)
    store.apply_policy_v3({
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "fixture-policy",
        "revision": 1, "enabled": True, "routes": [{"key": EVENT_KEY, "targets": [EVENT_TARGET]}],
    })

    def target():
        return store.effective_policy_v3()["routes"][0]["targets"][0]
    assert target()["reason"] == "not_admitted"
    store.record_route_admission_v3(
        key=EVENT_KEY, target=EVENT_TARGET, evidence_ref="fixture://events", evidence_sha256="a" * 64,
        scope_symbols=["510300.SH"], scope_date_from="2025-06-01", scope_date_to="2025-06-30",
        valid_from="2020-01-01T00:00:00Z", valid_until="2099-01-01T00:00:00Z",
        recorded_by="pytest", adapter_revision=EVENT_REVISIONS["adapterRevision"],
        source_revision=EVENT_REVISIONS["sourceRevision"],
        credential_revision=EVENT_REVISIONS["credentialRevision"],
    )
    assert target()["eligible"] is True
    request = {
        "contractVersion": 3, "requestId": "fixture-events", "symbol": "510300.SH",
        "routeKey": EVENT_KEY, "routeTarget": {**EVENT_TARGET, "routeIndex": 0},
        "desiredRevision": 1, "effectivePolicyRevision": 1,
        "catalogRevision": runtime.market_route_catalog_v3()["catalogRevision"],
        "start": "2025-06-01", "end": "2025-06-30", "dataAsOf": "2099-01-01T00:00:00Z",
    }
    reader = Mock(return_value={"facts": [], "providerRevision": "fixture-content"})
    result = execute_event_request(runtime, request, reader=reader)
    assert result["coverage"]["complete"] is False
    reader.assert_called_once()
    store.revoke_route_admission_v3(key=EVENT_KEY, target=EVENT_TARGET, reason="fixture-revoked")
    assert target()["eligible"] is False
    reader.reset_mock()
    with pytest.raises(EventV3Error):
        execute_event_request(runtime, request, reader=reader)
    reader.assert_not_called()
