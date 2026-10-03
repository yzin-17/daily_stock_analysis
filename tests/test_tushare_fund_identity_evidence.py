"""离线合同输入验证 Tushare 身份与币种关联，不代表真实标的审核。"""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from src.services.tushare_fund_identity_evidence import MAX_BYTES, resolve_tushare_fund_identity


NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def bundle():
    return {
        "contractVersion": 1, "kind": "tushare-fund-identity",
        "mappings": [{
            "symbol": "159516.SZ", "instrumentType": "ETF", "queryFundCode": "159516.SZ",
            "scopeDateFrom": "2025-01-01", "scopeDateTo": "2025-12-31",
            "observedAt": "2026-09-27T10:00:00Z",
            "identityEvidence": {"documentUrl": "https://identity.example.test/fund", "documentSha256": "a" * 64},
            "dividendCurrencyEvidence": {
                "currency": "CNY", "documentUrl": "https://identity.example.test/cash", "documentSha256": "b" * 64,
            },
        }],
    }


def encode(value):
    return json.dumps(value, ensure_ascii=True, indent=2).encode("utf-8")


def admission(content):
    digest = sha256(content).hexdigest()
    return {
        "consumer": "thesis-ledger",
        "routeKey": {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "CASH_DISTRIBUTION"},
        "target": {"providerId": "tushare", "upstreamSource": "tushare"},
        "status": "admitted", "admissionState": "admitted", "evidenceRef": f"sha256:{digest}",
        "evidenceSha256": digest, "scopeSymbols": ["159516.SZ"],
        "scopeDateFrom": "2025-01-01", "scopeDateTo": "2025-12-31",
        "adapterRevision": "synthetic-tushare-cash", "sourceRevision": "synthetic-fund-div",
        "credentialRevision": "synthetic-credential", "recordVersion": 1,
        "validFrom": "2026-09-27T11:00:00Z", "validUntil": "2026-09-28T00:00:00Z",
        "recordedAt": "2026-09-27T11:00:00Z", "invalidatedAt": None, "invalidationReason": None,
    }


def resolve(content, current=None, **changes):
    options = dict(symbol="159516.SZ", start="2025-02-01", end="2025-03-01",
                   data_as_of=NOW.isoformat(), observed_at=NOW)
    options.update(changes)
    return resolve_tushare_fund_identity(content, admission(content) if current is None else current, **options)


def test_direct_identity_independent_currency_and_original_bytes_are_immutable():
    content = encode(bundle())
    current = admission(content)
    before = deepcopy(current)
    identity = resolve(content, current)
    assert identity.query_fund_code == "159516.SZ"
    assert identity.currency == "CNY"
    assert identity.content is content
    assert identity.evidence_ref == "sha256:" + sha256(content).hexdigest()
    assert identity.evidence_sha256 == sha256(content).hexdigest()
    assert current == before
    with pytest.raises(FrozenInstanceError):
        identity.currency = "USD"


@pytest.mark.parametrize("currency", ["CNY", "HKD", "USD"])
def test_currency_is_only_taken_from_the_explicit_dividend_evidence(currency):
    value = bundle()
    value["mappings"][0]["dividendCurrencyEvidence"]["currency"] = currency
    assert resolve(encode(value)).currency == currency


def test_same_document_can_explicitly_support_both_evidence_roles():
    value = bundle()
    item = value["mappings"][0]
    item["dividendCurrencyEvidence"] = {**item["identityEvidence"], "currency": "CNY"}
    assert resolve(encode(value)).currency == "CNY"


@pytest.mark.parametrize("field", ["identityEvidence", "dividendCurrencyEvidence"])
def test_document_authority_backslash_is_rejected_in_both_evidence_roles(field):
    value = bundle()
    value["mappings"][0][field]["documentUrl"] = "https://identity.example.test\\other/doc"
    with pytest.raises(ValueError, match="^tushare_identity_invalid_document$"):
        resolve(encode(value))


@pytest.mark.parametrize("field", ["identityEvidence", "dividendCurrencyEvidence"])
@pytest.mark.parametrize("url", [
    "https://%/doc",
    "https://%GG.test/doc",
    "https://\ud800.test/doc",
    "https://example%2ftest/doc",
])
def test_document_invalid_host_is_rejected_in_both_evidence_roles(field, url):
    value = bundle()
    value["mappings"][0][field]["documentUrl"] = url
    with pytest.raises(ValueError, match="^tushare_identity_invalid_document$"):
        resolve(encode(value))


@pytest.mark.parametrize("url", [
    "https://identity.example.test/基金",
    "https://identity.example.test/fund\\part?proof=one\\two",
    "https://%65xample.test/基金",
])
def test_document_path_and_query_remain_original_urls(url):
    value = bundle()
    value["mappings"][0]["identityEvidence"]["documentUrl"] = url
    content = encode(value)
    assert resolve(content).content is content


@pytest.mark.parametrize("change", [
    {"symbol": "159516.OF", "queryFundCode": "159516.OF"},
    {"symbol": "159516", "queryFundCode": "159516"},
    {"symbol": "１５９５１６.SZ", "queryFundCode": "１５９５１６.SZ"},
    {"symbol": " 159516.SZ"}, {"symbol": "159516.sz"},
    {"queryFundCode": "159516.OF"}, {"queryFundCode": "159516.SH"},
    {"queryFundCode": "159516"}, {"queryFundCode": "510300.SH"},
    {"instrumentType": "NAV_FUND"}, {"instrumentType": "STOCK"},
    {"extra": "unsupported"}, {"scopeDateFrom": "2025-02-30"},
    {"scopeDateFrom": "20250101"}, {"scopeDateFrom": "2026-01-01"},
    {"scopeDateFrom": "2024-12-31"}, {"scopeDateTo": "2026-01-01"},
    {"observedAt": "2026-09-27T11:00:00.000000001Z"},
    {"observedAt": "2026-09-27T13:00:00Z"},
])
def test_invalid_mapping_cannot_become_query_arguments(change):
    value = bundle()
    value["mappings"][0].update(change)
    with pytest.raises(ValueError, match="^tushare_identity_"):
        resolve(encode(value))


@pytest.mark.parametrize("field", ["identityEvidence", "dividendCurrencyEvidence"])
@pytest.mark.parametrize("change", [
    None, {}, {"documentUrl": "http://identity.example.test/doc", "documentSha256": "a" * 64},
    {"documentUrl": "https://user:password@identity.example.test/doc", "documentSha256": "a" * 64},
    {"documentUrl": "https://@identity.example.test/doc", "documentSha256": "a" * 64},
    {"documentUrl": "https://identity.example.test/doc\n", "documentSha256": "a" * 64},
    {"documentUrl": "https://identity.example.test/doc", "documentSha256": "A" * 64},
    {"documentUrl": "https://identity.example.test/doc", "documentSha256": "a" * 63},
    {"documentUrl": "https://identity.example.test/doc", "documentSha256": "a" * 64, "extra": 1},
])
def test_missing_invalid_or_extra_document_fields_are_rejected(field, change):
    value = bundle()
    if field == "dividendCurrencyEvidence" and isinstance(change, dict):
        change = {**change, "currency": "CNY"}
    value["mappings"][0][field] = change
    with pytest.raises(ValueError, match="^tushare_identity_"):
        resolve(encode(value))


@pytest.mark.parametrize("currency", [None, "EUR", "cny", " CNY", [], True])
def test_invalid_currency_is_not_inferred(currency):
    value = bundle()
    value["mappings"][0]["dividendCurrencyEvidence"]["currency"] = currency
    with pytest.raises(ValueError, match="tushare_identity_invalid_currency"):
        resolve(encode(value))


def test_absent_currency_evidence_is_not_replaced_with_trading_currency():
    value = bundle()
    del value["mappings"][0]["dividendCurrencyEvidence"]
    with pytest.raises(ValueError, match="tushare_identity_invalid_mapping"):
        resolve(encode(value))


@pytest.mark.parametrize("change", [
    {"routeKey": {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "SPLIT_EVENT"}},
    {"routeKey": {"kind": "data", "market": "CN", "assetType": "NAV_FUND", "capability": "CASH_DISTRIBUTION"}},
    {"routeKey": {"kind": "data", "market": "US", "assetType": "ETF", "capability": "CASH_DISTRIBUTION"}},
    {"target": {"providerId": "rqdata", "upstreamSource": "rqdata"}},
    {"target": {"providerId": "tushare", "upstreamSource": "other"}},
    {"target": {"providerId": "tushare", "upstreamSource": "tushare", "extra": 1}},
    {"scopeSymbols": ["159516.SH"]}, {"scopeDateFrom": "2025-02-02"},
    {"scopeDateTo": "2025-02-28"}, {"consumer": "other"}, {"status": "pending"},
    {"admissionState": "pending"}, {"invalidatedAt": "2026-09-27T11:30:00Z"},
    {"invalidationReason": "revoked"}, {"recordVersion": True}, {"recordVersion": 0},
    {"evidenceRef": "sha256:" + "c" * 64}, {"evidenceSha256": "c" * 64},
    {"validFrom": "2026-09-27T12:00:00.000000001Z"},
    {"validUntil": "2026-09-27T12:00:00Z"},
    {"recordedAt": "2026-09-27T12:00:00.000000001Z"},
])
def test_admission_identity_scope_digest_and_lifecycle_are_strict(change):
    content = encode(bundle())
    current = admission(content)
    current.update(change)
    with pytest.raises(ValueError, match="^tushare_identity_"):
        resolve(content, current)


@pytest.mark.parametrize("change", [
    {"symbol": "159516.OF"}, {"symbol": "159516.sz"}, {"symbol": " 159516.SZ"},
    {"symbol": "１５９５１６.SZ"}, {"start": "20250201"}, {"start": "2025-03-02"},
    {"end": "2025-02-30"}, {"start": None}, {"observed_at": NOW.replace(tzinfo=None)},
    {"observed_at": NOW.isoformat()}, {"data_as_of": NOW},
])
def test_request_context_cannot_be_normalized_into_a_valid_identity(change):
    with pytest.raises(ValueError, match="^tushare_identity_"):
        resolve(encode(bundle()), **change)


def test_every_mapping_is_checked_even_after_the_requested_mapping():
    value = bundle()
    other = deepcopy(value["mappings"][0])
    other.update(symbol="510300.SH", queryFundCode="510300.SH")
    value["mappings"].append(other)
    content = encode(value)
    with pytest.raises(ValueError, match="tushare_identity_invalid_scope"):
        resolve(content)
    current = admission(content)
    current["scopeSymbols"].append("510300.SH")
    assert resolve(content, current).query_fund_code == "159516.SZ"
    other["observedAt"] = "2026-09-27T11:00:00.000000001Z"
    content = encode(value)
    current.update(evidenceRef="sha256:" + sha256(content).hexdigest(), evidenceSha256=sha256(content).hexdigest())
    with pytest.raises(ValueError, match="tushare_identity_invalid_scope"):
        resolve(content, current)


def test_mapping_must_cover_the_whole_request_not_just_its_start():
    value = bundle()
    value["mappings"][0]["scopeDateTo"] = "2025-02-28"
    with pytest.raises(ValueError, match="tushare_identity_missing_mapping"):
        resolve(encode(value))


@pytest.mark.parametrize("clock", [
    "2026-09-27T10:00:00-00:00", "2026-09-27T10:00:00", "2026-09-27T10:00:00+24:00",
    "2026-09-27T10:00:00+00:60", "2026-02-30T10:00:00Z", "2026-09-27T24:00:00Z",
    "2026-09-27T10:00:60Z", "0000-01-01T00:00:00Z", "2026-09-27T10:00:00.Z",
    "2026-09-27T10:00:00." + "0" * 1025 + "Z",
])
@pytest.mark.parametrize("field", ["observedAt", "recordedAt", "validFrom", "validUntil", "data_as_of"])
def test_all_clock_roles_reject_unknown_invalid_or_over_limit_time(clock, field):
    value = bundle()
    options = {}
    if field == "observedAt":
        value["mappings"][0][field] = clock
    content = encode(value)
    current = admission(content)
    if field in {"recordedAt", "validFrom", "validUntil"}:
        current[field] = clock
    if field == "data_as_of":
        options[field] = clock
    with pytest.raises(ValueError, match="^tushare_identity_"):
        resolve(content, current, **options)


@pytest.mark.parametrize("fraction", ["000001", "000000001", "0" * 1023 + "1"])
def test_late_evidence_is_rejected_at_microsecond_nanosecond_and_maximum_precision(fraction):
    value = bundle()
    value["mappings"][0]["observedAt"] = "2026-09-27T12:00:00." + fraction + "Z"
    content = encode(value)
    current = admission(content)
    current["recordedAt"] = "2026-09-27T12:00:00Z"
    with pytest.raises(ValueError, match="tushare_identity_invalid_scope"):
        resolve(content, current)


def test_recorded_at_later_than_freeze_by_one_nanosecond_is_rejected():
    content = encode(bundle())
    current = admission(content)
    current["recordedAt"] = "2026-09-27T11:00:00.000000001Z"
    with pytest.raises(ValueError, match="tushare_identity_not_admitted"):
        resolve(content, current, data_as_of="2026-09-27T11:00:00Z")


def test_nanosecond_expiry_is_current_but_equal_expiry_is_not_and_original_admission_is_preserved():
    content = encode(bundle())
    current = admission(content)
    current["validUntil"] = "2026-09-27T12:00:00.000000001Z"
    before = deepcopy(current)
    assert resolve(content, current).content is content
    assert current == before
    current["validUntil"] = "2026-09-27T12:00:00.000000000Z"
    with pytest.raises(ValueError, match="tushare_identity_not_admitted"):
        resolve(content, current)


def test_equivalent_offsets_and_trailing_zeros_preserve_exact_ordering():
    value = bundle()
    value["mappings"][0]["observedAt"] = "2026-09-27T20:00:00." + "0" * 1024 + "+08:00"
    content = encode(value)
    current = admission(content)
    current["recordedAt"] = "2026-09-27T07:00:00.000000000-05:00"
    assert resolve(content, current, data_as_of="2026-09-27T20:00:00+08:00").content is content


def test_expiry_projection_overflow_fails_closed():
    content = encode(bundle())
    current = admission(content)
    current["validUntil"] = "9999-12-31T23:59:59.9999991Z"
    last = datetime.max.replace(tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="tushare_identity_not_admitted"):
        resolve(content, current, observed_at=last, data_as_of=last.isoformat())


def test_non_minute_datetime_offset_is_not_an_accepted_evidence_clock():
    with pytest.raises(ValueError, match="tushare_identity_invalid_time"):
        resolve(encode(bundle()), observed_at=NOW.astimezone(timezone(timedelta(seconds=1))))


@pytest.mark.parametrize("value", [
    [], {"contractVersion": True, "kind": "tushare-fund-identity", "mappings": []},
    {"contractVersion": 1, "kind": "rqdata-fund-identity", "mappings": []},
    {"contractVersion": 1, "kind": "tushare-fund-identity", "mappings": []},
    {**bundle(), "extra": True},
])
def test_bundle_shape_is_strict(value):
    with pytest.raises(ValueError, match="tushare_identity_invalid_evidence"):
        resolve(encode(value))


def test_duplicate_mapping_and_escaped_duplicate_json_keys_are_rejected():
    value = bundle()
    value["mappings"].append(deepcopy(value["mappings"][0]))
    with pytest.raises(ValueError, match="tushare_identity_invalid_mapping"):
        resolve(encode(value))
    content = encode(bundle()).replace(b'"symbol": "159516.SZ"', b'"symbol": "159516.SZ", "\\u0073ymbol": "159516.SZ"')
    with pytest.raises(ValueError, match="tushare_identity_duplicate_field"):
        resolve(content)


@pytest.mark.parametrize("content", [
    b"", b"\xff", b"{}", b'{"contractVersion":NaN}', b" " * (MAX_BYTES + 1),
    b'{"contractVersion":' + b"1" * 5000 + b"}", b"[" * 2000 + b"]" * 2000,
])
def test_invalid_utf8_json_nonfinite_and_size_are_rejected(content):
    with pytest.raises(ValueError, match="tushare_identity_invalid_evidence"):
        resolve(content)


def test_bytes_budget_and_mapping_count_upper_bounds():
    content = encode(bundle())
    padded = content + b" " * (MAX_BYTES - len(content))
    assert resolve(padded).content is padded
    value = bundle()
    value["mappings"] *= 1001
    with pytest.raises(ValueError, match="tushare_identity_invalid_evidence"):
        resolve(encode(value))
