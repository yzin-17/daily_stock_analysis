"""HiThink 基金分红身份与币种只来自当前准入绑定的原字节。"""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from src.services.hithink_fund_identity_evidence import resolve_hithink_fund_identity
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_hithink_dividend_contract_v3 import (
    HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET,
)


SYMBOL = "510300.SH"
OBSERVED = datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc)
DOCUMENT = {"documentUrl": "https://www.sse.com.cn/fixture/etf.pdf", "documentSha256": "a" * 64}


def evidence(mapping=None):
    item = {
        "symbol": SYMBOL, "instrumentType": "ETF", "queryThscode": SYMBOL,
        "fundType": "exchange", "scopeDateFrom": "2025-01-01",
        "scopeDateTo": "2026-12-31", "observedAt": "2026-01-01T00:00:00Z",
        "identityEvidence": DOCUMENT,
        "dividendCurrencyEvidence": {**DOCUMENT, "currency": "CNY"},
    }
    if mapping:
        item.update(mapping)
    content = json.dumps({"contractVersion": 1, "kind": "hithink-fund-identity",
                          "mappings": [item]}, sort_keys=True).encode()
    digest = sha256(content).hexdigest()
    admission = {
        "consumer": "thesis-ledger", "status": "admitted", "admissionState": "admitted",
        "invalidatedAt": None, "invalidationReason": None,
        "routeKey": HITHINK_DIVIDEND_KEY, "target": HITHINK_DIVIDEND_TARGET,
        "scopeSymbols": [SYMBOL], "scopeDateFrom": "2025-01-01",
        "scopeDateTo": "2026-12-31", "validFrom": "2026-01-01T00:00:00Z",
        "validUntil": "2027-01-01T00:00:00Z", "recordedAt": "2026-02-01T00:00:00Z",
        "recordVersion": 1, "evidenceRef": f"sha256:{digest}", "evidenceSha256": digest,
    }
    return content, admission


def resolve(content, admission, **overrides):
    options = {"symbol": SYMBOL, "start": "2025-06-01", "end": "2025-06-30",
               "data_as_of": "2026-09-29T00:00:00Z", "observed_at": OBSERVED}
    return resolve_hithink_fund_identity(content, admission, **{**options, **overrides})


def test_current_exact_evidence_returns_full_symbol_currency_and_original_bytes():
    content, admission = evidence()
    identity = resolve(content, admission)
    assert identity.query_thscode == SYMBOL
    assert identity.currency == "CNY"
    assert identity.evidence_ref == admission["evidenceRef"]
    assert identity.evidence_sha256 == admission["evidenceSha256"]
    assert identity.content == content


@pytest.mark.parametrize("change", [
    {"queryThscode": "159516.SZ"}, {"fundType": "otc"},
    {"instrumentType": "NAV_FUND"}, {"dividendCurrencyEvidence": DOCUMENT},
    {"dividendCurrencyEvidence": {**DOCUMENT, "currency": "EUR"}},
    {"dividendCurrencyEvidence": {**DOCUMENT, "currency": {"code": "CNY"}}},
    {"identityEvidence": {**DOCUMENT, "documentUrl": "http://example.com/etf.pdf"}},
    {"identityEvidence": {**DOCUMENT, "documentUrl": "https://%zz/etf.pdf"}},
    {"scopeDateFrom": "2024-12-31"}, {"observedAt": "2026-02-01T00:00:00.000000001Z"},
])
def test_identity_currency_and_scope_are_independent_verified_inputs(change):
    content, admission = evidence(change)
    with pytest.raises(ValueError):
        resolve(content, admission)


@pytest.mark.parametrize("change", [
    {"status": "pending"}, {"invalidatedAt": "2026-09-01T00:00:00Z"},
    {"target": {**HITHINK_DIVIDEND_TARGET, "upstreamSource": "fund-market-historical"}},
    {"recordedAt": "2026-09-29T00:00:00.000000001Z"},
    {"validUntil": "2026-09-29T00:00:00Z"},
    {"scopeSymbols": ["159516.SZ"]},
    {"scopeSymbols": None}, {"recordVersion": 0},
])
def test_expired_revoked_late_or_wrong_admission_rejects(change):
    content, admission = evidence()
    admission.update(change)
    with pytest.raises(ValueError):
        resolve(content, admission)


def test_original_bytes_and_unique_mapping_are_mandatory():
    content, admission = evidence()
    with pytest.raises(ValueError, match="digest_mismatch"):
        resolve(content + b" ", admission)
    duplicated = json.loads(content)
    duplicated["mappings"].append(deepcopy(duplicated["mappings"][0]))
    repeated = json.dumps(duplicated).encode()
    digest = sha256(repeated).hexdigest()
    admission.update({"evidenceRef": f"sha256:{digest}", "evidenceSha256": digest})
    with pytest.raises(ValueError, match="invalid_mapping"):
        resolve(repeated, admission)


def test_content_addressed_store_rechecks_bytes_before_identity_use(tmp_path):
    content, admission = evidence()
    store = MappingEvidenceStore(tmp_path)
    assert store.put(content) == admission["evidenceRef"]
    assert resolve(store.read(admission["evidenceRef"]), admission).currency == "CNY"
    (tmp_path / f'{admission["evidenceSha256"]}.json').write_bytes(content + b" ")
    with pytest.raises(ValueError, match="摘要不匹配"):
        store.read(admission["evidenceRef"])
