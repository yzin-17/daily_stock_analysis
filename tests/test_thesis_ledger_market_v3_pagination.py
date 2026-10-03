"""V3 分段传输证明的严格窗口、计数与原始摘要校验。"""

from copy import deepcopy
from hashlib import sha256
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from data_provider.tushare_fund_daily import PAGINATION_PROTOCOL, fetch_tushare_fund_daily
from data_provider.tencent_native_daily import TENCENT_NATIVE_DAILY_PROTOCOL
from src.services.thesis_ledger_market_v3_pagination import MarketPaginationError, market_pagination_proof_v3


def sample():
    api = Mock()
    api.fund_daily.side_effect = lambda **kwargs: pd.DataFrame([{
        "ts_code": "159516.SZ", "trade_date": kwargs["start_date"],
        "open": 1, "high": 2, "low": 1, "close": 2, "vol": 10, "amount": 1,
    }])
    result = fetch_tushare_fund_daily(api, "159516.SZ", "2024-01-01", "2025-01-02", before_call=Mock())
    result.attrs["thesis_ledger_v3_pagination"] = {
        "status": "complete", "pagesFetched": 2, "continuationPending": False,
        "protocol": PAGINATION_PROTOCOL, "maximumRows": None,
        "requestedStart": "2024-01-01", "requestedEnd": "2025-01-02",
    }
    return result


def proof(frame, **overrides):
    args = dict(
        asset_type="ETF", provider="tushare", upstream_source="tushare",
        expected_session_count=250, requested_start="2024-01-01", requested_end="2025-01-02",
    )
    args.update(overrides)
    return market_pagination_proof_v3(frame, **args)


def test_partition_completion_retains_actual_pages_without_claiming_session_coverage():
    frame = sample()
    assert len(frame) == 2
    assert proof(frame) == {"status": "complete", "pagesFetched": 2, "continuationPending": False}
    assert frame.attrs["fundDailyRetrieval"]["tradingCalendarVerified"] is False


@pytest.mark.parametrize("field,value", [
    ("pagesFetched", 1), ("pagesFetched", True), ("continuationPending", True),
    ("status", "partial"), ("requestedStart", "2024-01-02"), ("requestedEnd", "2025-01-01"),
    ("maximumRows", 5000), ("protocol", "unknown"),
])
def test_changed_pagination_header_rejected(field, value):
    frame = sample()
    frame.attrs["thesis_ledger_v3_pagination"][field] = value
    with pytest.raises(MarketPaginationError, match="invalid_response"):
        proof(frame)


@pytest.mark.parametrize("field,value", [
    ("start", "2024-12-31"), ("end", "2025-01-01"), ("rows", True),
    ("rows", 3), ("rows", 0), ("sha256", "invalid"),
])
def test_partition_scope_or_row_count_rejected_even_with_matching_revision(field, value):
    frame = sample()
    retrieval = frame.attrs["fundDailyRetrieval"]
    retrieval["partitions"][1][field] = value
    retrieval["revision"] = sha256(json.dumps(
        retrieval["partitions"], sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    with pytest.raises(MarketPaginationError):
        proof(frame)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "digest", "coverage", "endpoint", "unit"])
def test_missing_or_stale_retrieval_evidence_rejected(mutation):
    frame = sample()
    retrieval = frame.attrs["fundDailyRetrieval"]
    if mutation == "missing":
        del frame.attrs["fundDailyRetrieval"]
    elif mutation == "duplicate":
        retrieval["partitions"].append(deepcopy(retrieval["partitions"][0]))
    elif mutation == "digest":
        retrieval["revision"] = "0" * 64
    elif mutation == "coverage":
        retrieval["tradingCalendarVerified"] = True
    elif mutation == "endpoint":
        retrieval["endpoint"] = "daily"
    else:
        retrieval["nativeAmountUnit"] = "CNY"
    with pytest.raises(MarketPaginationError):
        proof(frame)


def test_tencent_rejects_old_protocol_and_missing_native_retrieval():
    frame = sample()
    header = frame.attrs["thesis_ledger_v3_pagination"]
    header.update(protocol="tencent-range-response-max-800-v1", maximumRows=800)
    with pytest.raises(MarketPaginationError, match="invalid_response"):
        proof(frame, provider="tencent", upstream_source="tencent")
    header.update(protocol=TENCENT_NATIVE_DAILY_PROTOCOL, pagesFetched=2)
    with pytest.raises(MarketPaginationError, match="invalid_response"):
        proof(frame, provider="tencent", upstream_source="tencent", expected_session_count=801)


def _tencent_frame():
    frame = pd.DataFrame({"date": ["2024-12-31", "2025-01-02"]})
    partitions = [
        {"start": "2024-12-31", "end": "2024-12-31", "rows": 1, "sha256": "a" * 64},
        {"start": "2025-01-01", "end": "2025-01-02", "rows": 1, "sha256": "b" * 64},
    ]
    frame.attrs["tencentDailyRetrieval"] = {
        "endpoint": "newfqkline/get", "adjustment": "none", "partitionComplete": True,
        "partitions": partitions,
        "revision": sha256(json.dumps(
            partitions, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest(),
    }
    frame.attrs["thesis_ledger_v3_pagination"] = {
        "status": "complete", "pagesFetched": 2, "continuationPending": False,
        "protocol": TENCENT_NATIVE_DAILY_PROTOCOL, "maximumRows": 800,
        "requestedStart": "2024-12-31", "requestedEnd": "2025-01-02",
    }
    return frame


def test_tencent_year_partitions_are_counted_but_do_not_prove_market_coverage():
    frame = _tencent_frame()
    args = dict(
        provider="tencent", upstream_source="tencent", expected_session_count=2,
        requested_start="2024-12-31", requested_end="2025-01-02",
        requested_adjustment="none",
    )
    assert proof(frame, **args) == {
        "status": "complete", "pagesFetched": 2, "continuationPending": False,
    }
    with pytest.raises(MarketPaginationError, match="insufficient_coverage"):
        proof(frame, **{**args, "expected_session_count": 801})
    frame.attrs["tencentDailyRetrieval"]["partitions"][1]["rows"] = 0
    with pytest.raises(MarketPaginationError, match="invalid_response"):
        proof(frame, **args)
