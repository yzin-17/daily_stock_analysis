"""拆分日期映射严格绑定当前准入及具体事件。"""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from src.services.thesis_ledger_split_mapping_v3 import resolve_split_mapping_observations


@pytest.fixture
def sample():
    mapping = {"symbol": "159596.SZ", "sourceConversionDate": "2025-10-17",
               "sourceRatioPerUnit": "2", "effectiveDate": "2025-10-20", "recordDate": "2025-10-17", "announcementDate": "2025-10-14",
               "documentUrl": "https://example.invalid/reviewed-fixture.pdf", "documentSha256": "a" * 64}
    bundle = {"contractVersion": 1, "kind": "split-date-mapping", "mappings": [mapping]}
    admission = {
        "consumer": "thesis-ledger", "status": "admitted", "admissionState": "admitted",
        "routeKey": {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "SPLIT_EVENT"},
        "target": {"providerId": "akshare", "upstreamSource": "eastmoney"},
        "evidenceRef": "fixture://split-mapping", "evidenceSha256": "",
        "scopeSymbols": ["159596.SZ"], "scopeDateFrom": "2025-10-01", "scopeDateTo": "2025-10-31",
        "adapterRevision": "fixture-v1", "sourceRevision": "fixture-v1", "credentialRevision": "not-required",
        "validFrom": "2026-01-01T00:00:00Z", "validUntil": "2027-01-01T00:00:00Z",
        "recordedAt": "2026-01-01T00:00:00Z", "recordVersion": 1,
        "invalidatedAt": None, "invalidationReason": None,
    }
    observation = {"symbol": "159596.SZ", "sourceConversionDate": "2025-10-17", "sourceRatioPerUnit": "2.0",
                   "endpoint": "fund_cf_em", "upstreamSource": "eastmoney", "observedAt": "2026-09-27T00:00:00Z",
                   "effectiveDate": None, "effectivePhase": "unknown"}
    return bundle, admission, observation


def resolve(sample, *, bind_digest=True):
    bundle, admission, observation = sample
    raw = json.dumps(bundle).encode()
    if bind_digest:
        admission["evidenceSha256"] = sha256(raw).hexdigest()
    return resolve_split_mapping_observations(
        [observation], evidence_bytes=raw, admission=admission,
        observed_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )


def test_mapping_preserves_source_date_and_actual_observation(sample):
    original = deepcopy(sample[2])
    result = resolve(sample)[0]
    assert result["sourceConversionDate"] == "2025-10-17"
    assert result["effectiveDate"] == "2025-10-20"
    assert result["observedAt"] == original["observedAt"]
    assert "availableAt" not in result and "strategyVisibility" not in result
    assert sample[2] == original
    assert result["dateMappingEvidence"]["sha256"] == sample[1]["evidenceSha256"]


@pytest.mark.parametrize("ratio", ["2e0", "+2", " 2", "2 ", "02", "2.", "２", "2_0"])
def test_mapping_rejects_non_wire_decimal_text(sample, ratio):
    sample[0]["mappings"][0]["sourceRatioPerUnit"] = ratio
    with pytest.raises(ValueError, match="十进制文本"):
        resolve(sample)


@pytest.mark.parametrize("field,value", [
    ("sourceConversionDate", "2025-10-16"), ("sourceRatioPerUnit", "3"), ("symbol", "159220.SZ"),
    ("effectiveDate", "2025-09-30"), ("announcementDate", "2025-11-01"),
    ("documentSha256", "unverified"), ("documentUrl", "http://example.invalid/a"),
])
def test_mapping_identity_and_evidence_fields_rejected(sample, field, value):
    sample[0]["mappings"][0][field] = value
    with pytest.raises(ValueError):
        resolve(sample)


@pytest.mark.parametrize("field,value", [
    ("status", "revoked"), ("invalidatedAt", "2026-09-26T00:00:00Z"),
    ("validUntil", "2026-09-26T00:00:00Z"), ("scopeSymbols", ["159220.SZ"]),
    ("scopeDateTo", "2025-10-19"),
])
def test_current_admission_required(sample, field, value):
    sample[1][field] = value
    with pytest.raises(ValueError):
        resolve(sample)


def test_digest_and_duplicate_mapping_rejected(sample):
    resolve(sample)
    sample[0]["mappings"][0]["effectiveDate"] = "2025-10-21"
    with pytest.raises(ValueError, match="摘要"):
        resolve(sample, bind_digest=False)
    sample[0]["mappings"].append(deepcopy(sample[0]["mappings"][0]))
    with pytest.raises(ValueError, match="重复"):
        resolve(sample)


def test_cash_admission_cannot_authorize_split_mapping(sample):
    sample[1]["routeKey"]["capability"] = "CASH_DISTRIBUTION"
    with pytest.raises(ValueError, match="能力"):
        resolve(sample)


def test_159516_two_announced_splits_map_without_historical_visibility(sample):
    bundle, admission, observation = sample
    events = [
        ("2026-03-27", "2026-03-30", "2026-03-24",
         "https://static.cninfo.com.cn/finalpage/2026-03-24/1225024265.PDF",
         "8c79b77770e5bd9f853789a83c68981cb2a62735e2182540f3f3299a1bb02359"),
        ("2026-07-09", "2026-07-10", "2026-07-06",
         "https://disc.static.szse.cn/disc/disk03/finalpage/2026-07-06/"
         "f85c28ad-046c-4b29-96c3-142bf21dec58.PDF",
         "a758c693cd24aa4eeeac3d4de9492ac637e7d0206d7ac0b10469901db7a39fd6"),
    ]
    bundle["mappings"] = [
        {"symbol": "159516.SZ", "sourceConversionDate": source_date,
         "sourceRatioPerUnit": "2", "recordDate": source_date,
         "effectiveDate": effective_date, "announcementDate": announcement_date,
         "documentUrl": document_url, "documentSha256": document_sha256}
        for source_date, effective_date, announcement_date, document_url, document_sha256 in events
    ]
    admission["scopeSymbols"] = ["159516.SZ"]
    admission["scopeDateFrom"] = "2026-01-01"
    admission["scopeDateTo"] = "2026-08-09"
    admission["evidenceRef"] = "fixture://159516-split-mapping"
    observations = [
        {**observation, "symbol": "159516.SZ", "sourceConversionDate": source_date}
        for source_date, *_ in events
    ]
    original = deepcopy(observations)
    raw = json.dumps(bundle).encode()
    admission["evidenceSha256"] = sha256(raw).hexdigest()

    result = resolve_split_mapping_observations(
        observations, evidence_bytes=raw, admission=admission,
        observed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
    )

    mapped_dates = [
        (item["sourceConversionDate"], item["recordDate"], item["effectiveDate"])
        for item in result
    ]
    assert mapped_dates == [
        ("2026-03-27", "2026-03-27", "2026-03-30"),
        ("2026-07-09", "2026-07-09", "2026-07-10"),
    ]
    assert [item["dateMappingEvidence"]["documentSha256"] for item in result] == [
        event[4] for event in events
    ]
    assert all(item["effectivePhase"] == "ex-right-session" for item in result)
    assert all("availableAt" not in item and "strategyVisibility" not in item for item in result)
    assert observations == original
