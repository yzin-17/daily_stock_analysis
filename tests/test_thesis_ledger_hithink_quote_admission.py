from datetime import datetime, timezone

import pytest

from src.services.thesis_ledger_hithink_quote import (
    HITHINK_ETF_SNAPSHOT_SOURCE,
    HITHINK_STOCK_SNAPSHOT_SOURCE,
)
from src.services.thesis_ledger_hithink_quote_admission import (
    hithink_quote_admission_matches_current,
    hithink_quote_revisions,
    hithink_quote_route_key,
)


NOW = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
REVISION = "hmac-sha256-v1:" + "a" * 64


def admitted(asset_type="STOCK", source=HITHINK_STOCK_SNAPSHOT_SOURCE, symbol="600519.SH"):
    return {
        "consumer": "thesis-ledger",
        "evidenceRef": "sha256:" + "b" * 64,
        "evidenceSha256": "b" * 64,
        "recordVersion": 1,
        "routeKey": hithink_quote_route_key(asset_type),
        "target": {"providerId": "hithink", "upstreamSource": source},
        "status": "admitted", "admissionState": "admitted",
        "invalidatedAt": None, "recordedAt": "2026-09-28T00:00:00+00:00",
        "validFrom": "2026-09-28T00:00:00+00:00",
        "validUntil": "2026-09-30T00:00:00+00:00",
        "scopeSymbols": [symbol],
        "scopeDateFrom": "2026-09-29", "scopeDateTo": "2026-09-29",
        "credentialRevision": REVISION,
        **hithink_quote_revisions(asset_type, source),
    }


def matches(row, *, asset_type="STOCK", source=HITHINK_STOCK_SNAPSHOT_SOURCE,
            symbol="600519.SH", revision=REVISION, now=NOW):
    return hithink_quote_admission_matches_current(
        row, asset_type=asset_type, source=source, symbol=symbol,
        credential_revision=revision, now=now,
    )


def test_stock_and_etf_have_independent_exact_admissions():
    stock = admitted()
    etf = admitted("ETF", HITHINK_ETF_SNAPSHOT_SOURCE, "510300.SH")
    assert matches(stock)
    assert matches(etf, asset_type="ETF", source=HITHINK_ETF_SNAPSHOT_SOURCE,
                   symbol="510300.SH")
    assert not matches(stock, asset_type="ETF", source=HITHINK_ETF_SNAPSHOT_SOURCE,
                       symbol="510300.SH")
    assert not matches(etf)


@pytest.mark.parametrize("change", [
    {"status": "revoked"},
    {"admissionState": "expired"},
    {"invalidatedAt": "2026-09-28T23:00:00+00:00"},
    {"adapterRevision": "old-adapter"},
    {"sourceRevision": "old-source"},
    {"credentialRevision": "hmac-sha256-v1:" + "b" * 64},
    {"scopeSymbols": ["000001.SZ"]},
    {"scopeDateFrom": "2026-09-30"},
    {"validUntil": "2026-09-29T00:00:00+00:00"},
    {"recordedAt": "2026-09-29T01:01:00+00:00"},
    {"target": {"providerId": "hithink", "upstreamSource": HITHINK_ETF_SNAPSHOT_SOURCE}},
    {"routeKey": hithink_quote_route_key("ETF")},
    {"evidenceRef": ""},
    {"evidenceSha256": "invalid"},
    {"recordVersion": 0},
])
def test_changed_or_out_of_scope_evidence_rejects(change):
    assert not matches({**admitted(), **change})


def test_missing_or_rotated_credential_and_china_date_boundary_reject():
    assert not matches(None)
    assert not matches(admitted(), revision=None)
    assert not matches(admitted(), revision="hmac-sha256-v1:" + "b" * 64)
    assert not matches(admitted(), now=datetime(2026, 9, 28, 15, 59, tzinfo=timezone.utc))
    assert not matches(admitted(), symbol="600519")


def test_unknown_source_or_naive_clock_rejects():
    assert not matches(admitted(), source="fund-market-historical")
    assert not matches(admitted(), now=NOW.replace(tzinfo=None))
