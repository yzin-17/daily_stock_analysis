"""只对显式研究的已确认全空占位形状兼容，计数与传输限制保持。"""

import hashlib
import json
from datetime import datetime, timezone

import pytest

from data_provider.hithink_fund_dividend_reader import HiThinkFundDividendReadError, fetch_hithink_fund_dividends
from data_provider.hithink_fund_dividend_semantics import BUSINESS_FIELDS, PLAN_ANNOUNCED_SEMANTICS


EMPTY = {key: None for key in BUSINESS_FIELDS}


class Response:
    status_code = 200

    def __init__(self, rows, count):
        self.body = json.dumps({"code": 0, "data": {"item": rows, "dividend_count": count}}).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


def read(response, **changes):
    options = {"fund_type": "exchange", "api_key": "test-key",
               "http_get": lambda *a, **kw: response,
               "clock": lambda: datetime(2026, 10, 9, tzinfo=timezone.utc),
               "research_semantics": PLAN_ANNOUNCED_SEMANTICS}
    return fetch_hithink_fund_dividends("159516.SZ", **{**options, **changes})


def test_single_full_empty_row_and_count_zero_preserve_original_provenance():
    response = Response([EMPTY], 0)
    result = read(response)
    assert result["items"] == []
    assert result["rawItemCount"] == result["ignoredEmptyRows"] == 1
    assert result["researchSemantics"] == PLAN_ANNOUNCED_SEMANTICS
    assert result["transportCountVerified"] is True
    assert result["historyComplete"] is False
    assert result["responseSha256"] == hashlib.sha256(response.body).hexdigest()
    assert response.closed


def test_default_entry_keeps_original_count_mismatch():
    with pytest.raises(HiThinkFundDividendReadError) as error:
        read(Response([EMPTY], 0), research_semantics=None)
    assert error.value.code == "count_mismatch"


@pytest.mark.parametrize("rows,count", [
    ([{}], 0), ([{key: None for key in BUSINESS_FIELDS if key != "progress"}], 0),
    ([{**EMPTY, "unknown": "not-empty"}], 0), ([{**EMPTY, "progress": "2"}], 0),
    ([EMPTY, EMPTY], 0), ([EMPTY, {"progress": "2"}], 0), ([EMPTY], 2),
    ([EMPTY], False), ([EMPTY], "0"), ([EMPTY], -1),
])
def test_other_count_conflicts_are_not_suppressed(rows, count):
    response = Response(rows, count)
    with pytest.raises(HiThinkFundDividendReadError) as error:
        read(response)
    assert error.value.code == "count_mismatch"
    assert response.closed


def test_missing_count_is_not_assigned_zero_or_verified():
    result = read(Response([EMPTY], None))
    assert result["items"] == [EMPTY]
    assert result["transportCountVerified"] is False
    assert result["ignoredEmptyRows"] == 0
    assert result["historyComplete"] is False


@pytest.mark.parametrize("rows,count", [([], 0), ([{"progress": "2"}], 1), ([EMPTY], 1)])
def test_matching_raw_counts_keep_rows_unchanged(rows, count):
    result = read(Response(rows, count))
    assert result["items"] == rows
    assert result["rawItemCount"] == len(rows) and result["ignoredEmptyRows"] == 0
    assert result["historyComplete"] is False


def test_unknown_version_rejects_before_source_read():
    def forbidden(*args, **kwargs):
        raise AssertionError("must not read")
    with pytest.raises(HiThinkFundDividendReadError) as error:
        read(Response([EMPTY], 0), research_semantics="unknown", http_get=forbidden)
    assert error.value.code == "invalid_request"


def test_placeholder_does_not_relax_transport_limits():
    with pytest.raises(HiThinkFundDividendReadError) as oversized:
        read(Response([EMPTY], 0), max_bytes=1)
    assert oversized.value.code == "response_too_large"
    with pytest.raises(HiThinkFundDividendReadError) as excessive:
        read(Response([EMPTY, EMPTY], 0), max_items=1)
    assert excessive.value.code == "invalid_response"
