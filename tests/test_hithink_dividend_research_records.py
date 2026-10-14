"""新研究合同：未知/坏行/冲突独立披露，具体核对不推广枚举。"""

from copy import deepcopy
import hashlib
import json

import pytest

from data_provider.hithink_dividend_research_records import normalize_research_records
from data_provider.hithink_fund_dividend_semantics import RESEARCH_RECORDS_SEMANTICS
from src.services.thesis_ledger_hithink_known_cash import read_hithink_known_cash
from src.services.thesis_ledger_known_cash_v3 import execute_known_cash_request
from tests.test_thesis_ledger_hithink_known_cash import EXCHANGE, raw, snapshot
from tests.test_thesis_ledger_known_cash_v3 import state  # noqa: F401


def source(items=None):
    value = raw()
    value.update(researchSemantics=RESEARCH_RECORDS_SEMANTICS, transportCountVerified=True)
    if items is not None:
        value["items"] = items
    body = json.dumps({"code": 0, "data": {"item": value["items"]}})
    value.update(rawBusinessResponse=body, responseSha256=hashlib.sha256(body.encode()).hexdigest(),
                 rawItemCount=len(value["items"]))
    return value


def request():
    value = deepcopy(EXCHANGE["request"])
    value["researchPolicy"]["uncertainRecords"] = "exclude"
    return value


def test_real_reader_contract_preserves_unknown_and_specific_reconciliation():
    calls = []
    def fetch(*args, **kwargs):
        calls.append(kwargs)
        return source()
    result = read_hithink_known_cash(request(), snapshot(), fetch=fetch)
    record = result["records"][0]
    assert record["sourceStatus"] == "2" and record["normalizedStatus"] == "unknown"
    assert record["statusBasis"] == "event-reconciled" and record["planStatus"] == "unknown"
    assert record["issues"] == ["UNKNOWN_DIVIDEND_STATUS"]
    assert record["amountPerFundUnit"] == "0.088" and record["sourceAmount"]["value"] == "0.88"
    assert result["assessment"]["responseComplete"] and not result["assessment"]["historicalCoverageVerified"]
    assert calls[0]["research_semantics"] == RESEARCH_RECORDS_SEMANTICS


@pytest.mark.parametrize("field,value,reason", [
    ("registration_date_ms", None, "MISSING_RECORDDATE"),
    ("payment_date_ms", None, "MISSING_PAYMENTDATE"),
    ("publish_date_ms", None, "MISSING_ANNOUNCEMENTDATE"),
    ("ex_dividend_date_ms", -1, "INVALID_EFFECTIVEDATE"),
    ("per_ten_cash_before_tax", None, "INVALID_CASH_AMOUNT"),
    ("per_ten_cash_before_tax", 0, "INVALID_CASH_AMOUNT"),
    ("per_ten_cash_before_tax", "NaN", "INVALID_CASH_AMOUNT"),
    ("per_ten_cash_before_tax", "1e999999", "INVALID_CASH_AMOUNT"),
    ("progress", None, "MISSING_DIVIDEND_STATUS"),
])
def test_bad_record_does_not_erase_valid_records(field, value, reason):
    bad = deepcopy(raw()["items"][0]); bad[field] = value
    # 无除息日的损坏行无法归并事件；其他同日损坏行会显式隔离冲突组。
    good = deepcopy(raw()["items"][0]); good["ex_dividend_date_ms"] += 86400000
    result = normalize_research_records(source([good, bad]), {**request(), "observedDate": "2026-10-09"})
    assert len(result["records"]) == 1
    assert result["assessment"]["counts"]["excluded"] == 1
    assert reason in result["assessment"]["observations"][1]["issues"]


@pytest.mark.parametrize("status", ["3", "mystery", "2"])
def test_unknown_status_never_defaults_to_implemented(status):
    row = deepcopy(raw()["items"][0]); row["progress"] = status; row["per_ten_cash_before_tax"] = "0.89"
    result = normalize_research_records(source([row]), {**request(), "observedDate": "2026-10-09"})
    assert result["records"][0]["statusBasis"] == "unverified"
    assert result["records"][0]["normalizedStatus"] == "unknown"


@pytest.mark.parametrize("status", ["预案", "取消", "撤销", "终止"])
def test_preliminary_or_cancelled_never_eligible(status):
    row = deepcopy(raw()["items"][0]); row["progress"] = status
    result = normalize_research_records(source([row]), {**request(), "observedDate": "2026-10-09"})
    assert not result["records"] and result["assessment"]["counts"]["excluded"] == 1


def test_duplicate_and_conflict_have_counts_and_no_double_cash():
    row = raw()["items"][0]
    scope = {**request(), "observedDate": "2026-10-09"}
    duplicate = normalize_research_records(source([row, row]), scope)
    assert len(duplicate["records"]) == 1 and duplicate["assessment"]["counts"]["duplicate"] == 1
    conflict = deepcopy(row); conflict["per_ten_cash_before_tax"] = "0.89"
    result = normalize_research_records(source([row, conflict]), scope)
    assert not result["records"] and result["assessment"]["counts"]["excluded"] == 2
    assert all("CONFLICTING_EVENT" in x["issues"] for x in result["assessment"]["observations"])


def test_new_contract_reads_without_scope_admission_and_rechecks_configuration(state):
    state.request["researchPolicy"]["uncertainRecords"] = "exclude"
    result = execute_known_cash_request(state.runtime, state.request,
        reader=lambda req, pinned: read_hithink_known_cash(req, pinned, fetch=lambda *a, **k: source()))
    assert result["assessment"]["responseComplete"] and result["records"][0]["normalizedStatus"] == "unknown"
    assert state.policy["routes"][0]["targets"][0]["reason"] == "not_admitted"


def test_high_precision_per_ten_conversion_and_missing_payment_do_not_make_cash():
    row = deepcopy(raw()["items"][0]); row["per_ten_cash_before_tax"] = "1.234567890123456789012345678901"
    result = normalize_research_records(source([row]), {**request(), "observedDate": "2026-10-09"})
    assert result["records"][0]["amountPerFundUnit"] == "0.1234567890123456789012345678901"
    row["payment_date_ms"] = None
    result = normalize_research_records(source([row]), {**request(), "observedDate": "2026-10-09"})
    assert not result["records"] and result["assessment"]["observations"][0]["paymentDate"] is None


def test_reconciliation_is_not_reused_after_opaque_status_changes():
    row = deepcopy(raw()["items"][0]); row["progress"] = "3"
    result = normalize_research_records(source([row]), {**request(), "observedDate": "2026-10-09"})
    assert result["records"][0]["statusBasis"] == "unverified"
    assert result["records"][0]["normalizedStatus"] == "unknown"
