"""共享JSON范围驱动读取，来源与研究依据不冒充事实。"""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from data_provider.hithink_fund_dividend_reader import HiThinkFundDividendReadError
from data_provider.hithink_fund_dividend_semantics import BUSINESS_FIELDS, PLAN_ANNOUNCED_SEMANTICS
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_hithink_known_cash import KnownCashReadError, read_hithink_known_cash


PUBLICATION = json.loads((Path(__file__).resolve().parent / "fixtures/thesis_ledger_known_cash_v3.shared.json").read_text())
EXCHANGE = PUBLICATION["exchange"]


def raw():
    record = EXCHANGE["response"]["records"][0]
    row = {field: None for field in BUSINESS_FIELDS}
    for source, key in [("publish_date_ms", "announcementDate"), ("registration_date_ms", "recordDate"),
                        ("ex_dividend_date_ms", "effectiveDate"), ("payment_date_ms", "paymentDate")]:
        row[source] = int(datetime.fromisoformat(record[key]).replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
    row.update({"progress": "2", "per_ten_cash_before_tax": "0.88", "per_ten_cash_after_tax": "0.88"})
    return {"symbol": "510300.SH", "fundType": "exchange", "historyComplete": False,
            "researchSemantics": PLAN_ANNOUNCED_SEMANTICS, "items": [row], "ignoredEmptyRows": 0,
            "responseSha256": "a" * 64, "observedAt": EXCHANGE["response"]["source"]["observedAt"]}


def snapshot(**changes):
    values = {"provider_id": "hithink", "source": "control", "method": "api_key",
              "values": {"apiKey": "test-only-secret"}, "config_version": 3, "credential_version": 1}
    return ProviderCredentialSnapshot.create(**{**values, **changes})


def read(value=None, request=None, credentials=None):
    return read_hithink_known_cash(request or deepcopy(EXCHANGE["request"]), credentials or snapshot(),
                                  fetch=lambda *args, **kwargs: value if value is not None else raw())


def test_bounded_reader_reuses_explicit_semantics_and_matches_shared_record():
    calls = []
    def fetch(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return raw()
    result = read_hithink_known_cash(EXCHANGE["request"], snapshot(), fetch=fetch)
    assert result["records"] == EXCHANGE["response"]["records"]
    assert result["source"]["queryIdentity"] == EXCHANGE["response"]["source"]["queryIdentity"]
    assert calls[0][0] == "510300.SH"
    assert calls[0][1]["research_semantics"] == PLAN_ANNOUNCED_SEMANTICS
    assert calls[0][1]["fund_type"] == "exchange" and calls[0][1]["timeout_seconds"] == 10
    assert "facts" not in result and "admission" not in result
    assert result["coverage"]["complete"] is False


def test_switch_symbol_and_range_without_raw_record_or_code_changes():
    request = deepcopy(EXCHANGE["request"]); request["symbol"] = "510050.SH"
    source = raw(); source["symbol"] = request["symbol"]
    result = read(source, request=request)
    assert result["records"][0]["symbol"] == "510050.SH"
    request["start"] = "2025-06-19"
    empty = read(source, request=request)
    assert empty["records"] == [] and empty["coverage"]["complete"] is False


def test_placeholder_ignored_count_kept_and_preliminary_plan_not_promoted():
    source = raw(); source["items"] = []; source["ignoredEmptyRows"] = 1
    result = read(source)
    assert result["records"] == [] and result["ignoredEmptyRows"] == 1
    assert result["coverage"]["complete"] is False
    source = raw(); source["items"][0]["progress"] = "预案"
    result = read(source)
    assert result["records"] == [] and "preliminary_plans_excluded=1" in result["coverage"]["reason"]


@pytest.mark.parametrize("change", [
    {"source": "missing"}, {"provider_id": "other"}, {"method": "password"},
    {"values": {}}, {"values": {"apiKey": " "}},
])
def test_credentials_rejected_before_source_call(change):
    calls = []
    with pytest.raises(KnownCashReadError) as caught:
        read_hithink_known_cash(EXCHANGE["request"], snapshot(**change), fetch=lambda *a, **k: calls.append(True))
    assert caught.value.reason == "credential_missing" and not calls


@pytest.mark.parametrize("field,value", [
    ("symbol", "510050.SH"), ("fundType", "otc"), ("historyComplete", True),
    ("researchSemantics", "unknown"), ("responseSha256", "invalid"), ("ignoredEmptyRows", True),
    ("observedAt", "2026-10-10T00:00:00Z"), ("observedAt", "2026-10-09T13:00:00"),
])
def test_invalid_source_bound_fields_rejected(field, value):
    source = raw(); source[field] = value
    with pytest.raises(KnownCashReadError, match="invalid_source_response"):
        read(source)


@pytest.mark.parametrize("field,value", [
    ("progress", "3"), ("registration_date_ms", None), ("payment_date_ms", None),
    ("publish_date_ms", None), ("per_ten_cash_before_tax", None), ("per_ten_cash_before_tax", 0),
])
def test_missing_or_unknown_business_fields_rejected(field, value):
    source = raw(); source["items"][0][field] = value
    with pytest.raises(KnownCashReadError, match="invalid_source_response"):
        read(source)


def test_conflict_and_missing_as_of_not_masked_as_empty():
    source = raw(); second = dict(source["items"][0]); second["per_ten_cash_before_tax"] = "0.89"
    source["items"].append(second)
    with pytest.raises(KnownCashReadError): read(source)
    request = deepcopy(EXCHANGE["request"]); request["dataAsOf"] = "2026-10-09T12:59:59Z"
    with pytest.raises(KnownCashReadError): read(request=request)


def test_source_failures_have_stable_safe_reason_no_secret_or_exception_body():
    def upstream(*args, **kwargs): raise HiThinkFundDividendReadError("permission_denied")
    with pytest.raises(KnownCashReadError) as caught:
        read_hithink_known_cash(EXCHANGE["request"], snapshot(), fetch=upstream)
    assert caught.value.code == "upstream_failure" and caught.value.reason == "permission_denied"
    def broken(*args, **kwargs): raise RuntimeError("test-only-secret: body")
    with pytest.raises(KnownCashReadError) as caught:
        read_hithink_known_cash(EXCHANGE["request"], snapshot(), fetch=broken)
    assert str(caught.value) == "source_unavailable"
