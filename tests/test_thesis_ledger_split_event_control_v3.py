"""拆分映射经真实 Control、存储和执行入口消费，完整覆盖保持独立。"""

import json
from unittest.mock import Mock

import pytest

from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_event_v3 import execute_event_request, EventV3Error
from src.services.thesis_ledger_event_v3_adapters import SPLIT_KEY, SPLIT_REVISIONS, EVENT_TARGET


@pytest.fixture
def setup(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "control.sqlite"))
    runtime = ThesisLedgerProviderRuntime(store)
    mapping = {"symbol": "159596.SZ", "sourceConversionDate": "2025-10-17", "sourceRatioPerUnit": "2",
               "effectiveDate": "2025-10-20", "recordDate": "2025-10-17", "announcementDate": "2025-10-14",
               "documentUrl": "https://example.invalid/fixture.pdf", "documentSha256": "a" * 64}
    evidence = MappingEvidenceStore(tmp_path / "thesis-ledger-mapping-evidence")
    reference = evidence.put(json.dumps({"contractVersion": 1, "kind": "split-date-mapping", "mappings": [mapping]}).encode())
    store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "split-policy",
                           "revision": 1, "enabled": True, "routes": [{"key": SPLIT_KEY, "targets": [EVENT_TARGET]}]})
    store.record_route_admission_v3(
        key=SPLIT_KEY, target=EVENT_TARGET, evidence_ref=reference, evidence_sha256=reference[7:],
        scope_symbols=["159596.SZ"], scope_date_from="2025-10-01", scope_date_to="2025-10-31",
        valid_from="2020-01-01T00:00:00Z", valid_until="2099-01-01T00:00:00Z", recorded_by="pytest",
        adapter_revision=SPLIT_REVISIONS["adapterRevision"], source_revision=SPLIT_REVISIONS["sourceRevision"],
        credential_revision=SPLIT_REVISIONS["credentialRevision"],
    )
    request = {"contractVersion": 3, "requestId": "split-fixture", "symbol": "159596.SZ", "routeKey": SPLIT_KEY,
               "routeTarget": {**EVENT_TARGET, "routeIndex": 0}, "desiredRevision": 1, "effectivePolicyRevision": 1,
               "catalogRevision": runtime.market_route_catalog_v3()["catalogRevision"],
               "start": "2025-10-20", "end": "2025-10-20", "dataAsOf": "2099-01-01T00:00:00Z"}
    reader = Mock(return_value={"providerRevision": "fixture-source", "observations": [{
        "symbol": "159596.SZ", "sourceConversionDate": "2025-10-17", "sourceRatioPerUnit": "2",
        "endpoint": "fund_cf_em", "upstreamSource": "eastmoney", "observedAt": "2026-09-27T00:00:00Z",
    }]})
    return runtime, request, reader, tmp_path / "thesis-ledger-mapping-evidence" / f"{reference[7:]}.json"


def test_real_control_consumes_source_date_outside_economic_window(setup):
    runtime, request, reader, _ = setup
    result = execute_event_request(runtime, request, reader=reader)
    reader.assert_called_once_with("159596.SZ", start="2025-10-17", end="2025-10-20")
    assert result["coverage"]["complete"] is False
    assert result["dateMappingEvidence"]["ref"] == result["admission"]["evidenceRef"]
    assert json.loads(result["dateMappingEvidence"]["content"])["mappings"][0]["effectiveDate"] == "2025-10-20"
    fact = result["facts"][0]
    assert fact["effectiveDate"] == "2025-10-20"
    assert fact["recordDate"] == "2025-10-17"
    assert fact["availableAt"] == "2026-09-27T00:00:00Z"
    assert fact["ratio"] == "2" and fact["type"] == "SPLIT"
    assert "strategyVisibility" not in fact


@pytest.mark.parametrize("failure", ["missing", "corrupt", "revoked", "old-asof", "revoke-during-read"])
def test_missing_revoked_or_historically_unavailable_evidence_fails_closed(setup, failure):
    runtime, request, reader, path = setup
    if failure == "missing":
        path.unlink()
    elif failure == "corrupt":
        path.write_bytes(b'{}')
    elif failure == "revoked":
        runtime.store.revoke_route_admission_v3(key=SPLIT_KEY, target=EVENT_TARGET, reason="fixture")
    elif failure == "old-asof":
        request["dataAsOf"] = "2025-10-21T00:00:00Z"
    else:
        result = reader.return_value

        def read(*args, **kwargs):
            runtime.store.revoke_route_admission_v3(key=SPLIT_KEY, target=EVENT_TARGET, reason="fixture")
            return result
        reader.side_effect = read
    with pytest.raises(EventV3Error):
        execute_event_request(runtime, request, reader=reader)
    if failure in {"missing", "corrupt", "revoked"}:
        reader.assert_not_called()
