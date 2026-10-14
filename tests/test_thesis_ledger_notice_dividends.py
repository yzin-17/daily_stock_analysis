"""公告现金事件的真实数据包与非法输入回归。"""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from src.services.thesis_ledger_notice_dividends import (
    DATA_PATH, NoticeDividendError, OfficialNoticeDividendProvider,
)

NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


def read(provider=None, **kwargs):
    params = {"symbol": "510300.SH", "start": "2023-01-03", "end": "2023-02-01",
              "data_as_of": NOW.isoformat(), **kwargs}
    return (provider or OfficialNoticeDividendProvider(clock=lambda: NOW)).read(**params)


def provider_for(tmp_path, mutate):
    pack = json.loads(DATA_PATH.read_text())
    mutate(pack)
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(pack, ensure_ascii=False))
    return OfficialNoticeDividendProvider(path, clock=lambda: NOW)


def test_real_pack_amount_dates_clock_identity_and_cash_only_coverage():
    result = read()
    fact, = result["facts"]
    assert fact == {
        "symbol": "510300.SH", "market": "CN", "instrumentType": "ETF", "type": "CASH_DIVIDEND",
        "cashAmount": "0.064", "currency": "CNY", "recordDate": "2023-01-13",
        "effectiveDate": "2023-01-16", "paymentDate": "2023-01-19",
        "occurredAt": "2023-01-16T00:00:00+08:00",
        "availableAt": "2026-10-06T18:31:16.730840+00:00", "provider": "official-dividends",
        "providerRevision": f"official-cash-notices-sha256:{sha256(DATA_PATH.read_bytes()).hexdigest()}",
    }
    assert result["coverage"]["capability"] == "CASH_DISTRIBUTION"
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["basis"]["sourceDocumentId"] == "510300-2023-annual-report"
    assert result["provenance"]["historicalAvailability"] == "notVerified"
    assert result["provenance"]["fetchedAt"] == NOW.isoformat()
    assert "strategyVisibility" not in fact
    assert "admission" not in result
    assert len(result["sourceDocuments"]) == 2


def test_query_by_ex_date_and_empty_subwindow_retains_verified_basis():
    assert len(read(start="2023-01-16", end="2023-01-16")["facts"]) == 1
    result = read(start="2023-01-19", end="2023-01-19")
    assert result["facts"] == []
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["basis"]["eventDates"] == ["2023-01-16"]


@pytest.mark.parametrize("params,code", [
    ({"symbol": "510300"}, "not_covered"),
    ({"symbol": "510300.SZ"}, "not_covered"),
    ({"symbol": "159516.SZ"}, "not_covered"),
    ({"start": "2023-01-02"}, "insufficient_coverage"),
    ({"end": "2023-02-02"}, "insufficient_coverage"),
    ({"start": "2023-02-01", "end": "2023-01-03"}, "invalid_request"),
    ({"start": "2023-02-30"}, "invalid_request"),
    ({"start": "20230103"}, "invalid_request"),
    ({"data_as_of": "2026-10-07T00:00:00"}, "invalid_request"),
    ({"data_as_of": "2026-10-06T18:31:16.730839Z"}, "not_available"),
])
def test_request_rejections(params, code):
    with pytest.raises(NoticeDividendError) as error:
        read(**params)
    assert error.value.code == code


@pytest.mark.parametrize("section,key,value", [
    (None, "schemaVersion", True), (None, "market", "HK"), (None, "instrumentType", "NAV_FUND"),
    (None, "currency", "USD"), (None, "symbol", "510300"), (None, "datasetId", True),
    (None, "events", {}), (None, "sourceDocuments", {}),
    ("coverage", "basis", "notice-only"), ("coverage", "sourceDocumentId", "missing"),
    ("coverage", "eventDates", []), ("coverage", "eventDates", ["2023-01-16", "2023-01-16"]),
    ("coverage", "reportStart", "2023-01-10"), ("coverage", "reportEnd", "2023-01-31"),
    ("events", "cashAmount", "-0.64"), ("events", "cashAmount", "0"),
    ("events", "cashAmount", "NaN"), ("events", "cashAmount", "6.4e-1"),
    ("events", "cashAmount", 0.64), ("events", "unit", "CNY/fund-unit"),
    ("events", "currency", "USD"), ("events", "recordDate", "2023-01-16"),
    ("events", "effectiveDate", "2023-02-02"), ("events", "paymentDate", "2023-01-15"),
    ("events", "announcementDate", "2023-01-12"), ("events", "sourceDocumentId", "missing"),
    ("sourceDocuments", "sha256", "a"), ("sourceDocuments", "title", 123),
    ("sourceDocuments", "url", "https://example.com/notice.pdf"),
    ("sourceDocuments", "observedAt", "2023-01-08T00:00:00Z"), ("sourceDocuments", "pages", []),
])
def test_corrupt_pack_fails_closed(tmp_path, section, key, value):
    def mutate(pack):
        target = pack if section is None else pack[section]
        if isinstance(target, list):
            target = target[0]
        target[key] = value
    with pytest.raises(NoticeDividendError) as error:
        read(provider_for(tmp_path, mutate))
    assert error.value.code == "invalid_response"


@pytest.mark.parametrize("section", ["events", "sourceDocuments"])
def test_duplicate_rows_rejected(tmp_path, section):
    def duplicate(pack):
        pack[section].append(deepcopy(pack[section][0]))
    with pytest.raises(NoticeDividendError, match="重复"):
        read(provider_for(tmp_path, duplicate))


def test_duplicate_json_keys_and_unreadable_pack_rejected(tmp_path):
    path = tmp_path / "pack.json"
    path.write_text('{"schemaVersion":1,"schemaVersion":1}')
    with pytest.raises(NoticeDividendError, match="重复 JSON"):
        read(OfficialNoticeDividendProvider(path, clock=lambda: NOW))
    path.unlink()
    with pytest.raises(NoticeDividendError) as error:
        read(OfficialNoticeDividendProvider(path, clock=lambda: NOW))
    assert error.value.code == "upstream_failure"


@pytest.mark.parametrize("source,expected", [("100", "10"), ("0.000000000001", "0.0000000000001")])
def test_decimal_conversion_keeps_integer_zeroes_and_precision(tmp_path, source, expected):
    provider = provider_for(tmp_path, lambda pack: pack["events"][0].update(cashAmount=source))
    assert read(provider)["facts"][0]["cashAmount"] == expected


def test_revision_binds_original_bytes_and_not_read_clock(tmp_path):
    first = read()
    later = read(OfficialNoticeDividendProvider(clock=lambda: datetime(2026, 10, 8, tzinfo=timezone.utc)))
    assert first["facts"] == later["facts"]
    assert first["provenance"]["fetchedAt"] != later["provenance"]["fetchedAt"]
    provider = provider_for(tmp_path, lambda pack: pack["sourceDocuments"][0].update(title="新标题"))
    assert read(provider)["provenance"]["providerRevision"] != first["provenance"]["providerRevision"]


def test_invalid_current_clock_rejected():
    for now in [datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2026, 10, 7)]:
        with pytest.raises(NoticeDividendError) as error:
            read(OfficialNoticeDividendProvider(clock=lambda: now))
        assert error.value.code == "invalid_response"
