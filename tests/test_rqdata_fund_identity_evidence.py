"""RQData 基金身份映射与币种证据按实际准入摘要、范围及时间核对。"""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from src.services.rqdata_fund_identity_evidence import resolve_rqdata_fund_identity


NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def bundle(currency=True):
    mapping = {
        "symbol": "159516.SZ", "instrumentType": "ETF", "queryFundCode": "159516",
        "scopeDateFrom": "2025-01-01", "scopeDateTo": "2025-12-31",
        "observedAt": "2026-09-27T10:00:00Z",
        "identityEvidence": {"documentUrl": "https://identity.example.test/etf", "documentSha256": "a" * 64},
    }
    if currency:
        mapping["dividendCurrencyEvidence"] = {
            "currency": "CNY", "documentUrl": "https://identity.example.test/distribution", "documentSha256": "b" * 64,
        }
    return {"contractVersion": 1, "kind": "rqdata-fund-identity", "mappings": [mapping]}


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def admission(content, kind="split"):
    digest = sha256(content).hexdigest()
    return {
        "consumer": "thesis-ledger",
        "routeKey": {"kind": "data", "market": "CN", "assetType": "ETF",
                     "capability": "SPLIT_EVENT" if kind == "split" else "CASH_DISTRIBUTION"},
        "target": {"providerId": "rqdata", "upstreamSource": "rqdata"},
        "status": "admitted", "admissionState": "admitted",
        "evidenceRef": f"sha256:{digest}", "evidenceSha256": digest,
        "scopeSymbols": ["159516.SZ"], "scopeDateFrom": "2025-01-01", "scopeDateTo": "2025-12-31",
        "adapterRevision": "synthetic-rqdata-event", "sourceRevision": "synthetic-rqdata-api",
        "credentialRevision": "synthetic-credential", "validFrom": "2026-09-27T11:00:00Z",
        "validUntil": "2026-09-28T00:00:00Z", "recordVersion": 1,
        "recordedAt": "2026-09-27T11:00:00Z", "invalidatedAt": None, "invalidationReason": None,
    }


def resolve(content, current=None, **changes):
    options = dict(kind="split", symbol="159516.SZ", start="2025-02-01", end="2025-03-01",
                   data_as_of=NOW.isoformat(), observed_at=NOW)
    options.update(changes)
    return resolve_rqdata_fund_identity(content, current or admission(content, options["kind"]), **options)


def test_explicit_direct_etf_identity_and_independent_currency_are_retained():
    content = encode(bundle())
    identity = resolve(content, kind="dividend")
    assert identity.query_fund_code == "159516"
    assert identity.currency == "CNY"
    assert identity.content == content
    assert identity.evidence_ref == "sha256:" + identity.evidence_sha256


def test_split_does_not_infer_or_require_dividend_currency():
    content = encode(bundle(currency=False))
    assert resolve(content).currency is None
    with pytest.raises(ValueError, match="rqdata_identity_missing_currency"):
        resolve(content, kind="dividend")


@pytest.mark.parametrize("change", [
    {"symbol": "159516.SH"}, {"symbol": "000246.OF"}, {"instrumentType": "STOCK"},
    {"queryFundCode": "000246"}, {"queryFundCode": "159516_CH0"},
    {"observedAt": "2026-09-27T13:00:00Z"}, {"observedAt": "2026-09-27T11:30:00Z"},
    {"scopeDateFrom": "2024-01-01"}, {"scopeDateTo": "2026-01-01"},
    {"scopeDateFrom": "2025-06-01", "scopeDateTo": "2025-12-31"},
    {"scopeDateFrom": "2025-02-30"}, {"observedAt": "2026-09-27T10:00:00"},
    {"identityEvidence": {"documentUrl": "http://identity.example.test", "documentSha256": "a" * 64}},
    {"identityEvidence": {"documentUrl": "https://secret:password@identity.example.test", "documentSha256": "a" * 64}},
    {"identityEvidence": {"documentUrl": None, "documentSha256": "a" * 64}},
    {"identityEvidence": {"documentUrl": " https://identity.example.test", "documentSha256": "a" * 64}},
    {"dividendCurrencyEvidence": None},
    {"dividendCurrencyEvidence": {"currency": [], "documentUrl": "https://identity.example.test", "documentSha256": "a" * 64}},
    {"dividendCurrencyEvidence": {"currency": "EUR", "documentUrl": "https://identity.example.test", "documentSha256": "a" * 64}},
])
def test_bad_identity_or_document_cannot_become_query_arguments(change):
    value = bundle()
    value["mappings"][0].update(change)
    content = encode(value)
    with pytest.raises(ValueError):
        resolve(content)


@pytest.mark.parametrize("change", [
    {"target": {"providerId": "rqdata", "upstreamSource": "other-rqdata-source"}},
    {"scopeSymbols": ["159516.SH"]}, {"scopeDateTo": "2025-02-01"},
    {"evidenceSha256": "c" * 64}, {"evidenceRef": "sha256:" + "c" * 64},
    {"invalidatedAt": "2026-09-27T11:30:00Z"}, {"validUntil": "2026-09-27T12:00:00Z"},
    {"recordedAt": "2026-09-27T13:00:00Z"},
])
def test_admission_mismatch_revocation_expiration_or_future_record_is_rejected(change):
    content = encode(bundle())
    current = admission(content)
    current.update(change)
    with pytest.raises(ValueError):
        resolve(content, current)


def test_capability_and_freeze_cutoff_are_not_relaxed_for_mapping_evidence():
    content = encode(bundle())
    with pytest.raises(ValueError):
        resolve(content, admission(content, "split"), kind="dividend")
    with pytest.raises(ValueError):
        resolve(content, data_as_of="2026-09-27T10:30:00Z")


def test_duplicate_mapping_and_duplicate_json_fields_are_rejected():
    value = bundle()
    value["mappings"].append(deepcopy(value["mappings"][0]))
    with pytest.raises(ValueError):
        resolve(encode(value))
    content = encode(bundle()).replace(b'"contractVersion":1', b'"contractVersion":1,"contractVersion":1')
    with pytest.raises(ValueError, match="rqdata_identity_duplicate_field"):
        resolve(content)


@pytest.mark.parametrize("value", [
    [], {"contractVersion": True, "kind": "rqdata-fund-identity", "mappings": []},
    {"contractVersion": 1, "kind": "rqdata-fund-identity", "mappings": []},
    {"contractVersion": 1, "kind": "other", "mappings": []},
])
def test_invalid_or_empty_bundles_fail_closed(value):
    with pytest.raises(ValueError):
        resolve(encode(value))
