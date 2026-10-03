"""RQData 场内 ETF 日线请求与来源响应的失败关闭合同。"""

from decimal import Decimal

import pandas as pd
import pytest

from data_provider.rqdata_etf_daily_contract import (
    normalize_rqdata_etf_daily, rqdata_etf_daily_request,
)


SYMBOL = "159516.SZ"
SOURCE_ID = "159516.XSHE"
START = "2026-07-09"
END = "2026-07-10"


def frame(rows=None):
    if rows is None:
        rows = [
            (SOURCE_ID, START, 1.0, 1.1, 0.9, 1.05, 100.0, 105.0),
            (SOURCE_ID, END, 0.51, 0.55, 0.48, 0.52, 200.0, 104.0),
        ]
    index = pd.MultiIndex.from_tuples(
        [(row[0], pd.Timestamp(row[1])) for row in rows],
        names=["order_book_id", "date"],
    )
    return pd.DataFrame(
        [row[2:] for row in rows], index=index,
        columns=["open", "high", "low", "close", "volume", "total_turnover"],
    )


def normalize(value=None, **options):
    return normalize_rqdata_etf_daily(
        frame() if value is None else value, SYMBOL, SOURCE_ID, START, END, **options,
    )


def test_exact_request_and_unknown_source_semantics():
    request = rqdata_etf_daily_request(SYMBOL, SOURCE_ID, START, END)
    assert request == {
        "order_book_ids": SOURCE_ID, "start_date": START, "end_date": END,
        "frequency": "1d", "fields": [
            "open", "high", "low", "close", "volume", "total_turnover",
        ], "adjust_type": "none", "skip_suspended": False,
        "expect_df": True, "market": "cn",
    }
    result = normalize(frame())
    assert [row["date"] for row in result["rows"]] == [START, END]
    assert result["request"] == request
    assert result["validation"]["nativeVolumeUnit"] == "unknown"
    assert result["validation"]["nativeAmountUnit"] == "unknown"
    assert result["validation"]["historicalCoverageComplete"] is False
    assert result["validation"]["upstreamDataRevision"] is None


@pytest.mark.parametrize("identity", ["159516.XSHG", "159516", "510300.XSHE"])
def test_request_rejects_unverified_identity_shape(identity):
    with pytest.raises(ValueError, match="identity_invalid"):
        rqdata_etf_daily_request(SYMBOL, identity, START, END)


@pytest.mark.parametrize("start,end", [
    ("2026-07-11", END), ("2026-7-09", END), (START, "2027-07-10"),
])
def test_request_rejects_invalid_or_oversized_window(start, end):
    with pytest.raises(ValueError, match="window_invalid"):
        rqdata_etf_daily_request(SYMBOL, SOURCE_ID, start, end)


def test_normalizer_rejects_duplicate_date_and_wrong_source():
    row = (SOURCE_ID, START, 1, 1.1, 0.9, 1.05, 100, 105)
    for bad in (frame([row, row]), frame([(SOURCE_ID.replace("159516", "159517"), *row[1:])])):
        with pytest.raises(ValueError, match="scope_invalid"):
            normalize(bad)


def test_normalizer_rejects_invalid_ohlc_and_nonfinite_number():
    base = (SOURCE_ID, START, 1, 1.1, 0.9, 1.05, 100, 105)
    for bad in (
        frame([(*base[:3], 0.95, *base[4:])]),
        frame([(*base[:6], float("nan"), base[7])]),
    ):
        with pytest.raises(ValueError):
            normalize(bad)


def test_normalizer_rejects_missing_duplicate_columns_and_row_budget():
    missing = frame().drop(columns="total_turnover")
    duplicate = frame()
    duplicate.columns = ["open", "high", "low", "close", "volume", "open"]
    for bad in (missing, duplicate):
        with pytest.raises(ValueError, match="response_invalid"):
            normalize(bad)
    with pytest.raises(ValueError, match="response_invalid"):
        normalize(frame(), maximum_rows=1)


def test_empty_frame_keeps_coverage_incomplete():
    result = normalize(frame([]))
    assert result["rows"] == []
    assert result["validation"]["rowsValidated"] == 0
    assert result["validation"]["historicalCoverageComplete"] is False


def test_decimal_fields_keep_source_precision():
    precise = frame([(
        SOURCE_ID, START, Decimal("1.000000000000000001"),
        Decimal("1.100000000000000001"), Decimal("0.900000000000000001"),
        Decimal("1.050000000000000001"), Decimal("100.000000000000000001"),
        Decimal("105.000000000000000001"),
    )])
    result = normalize(precise)
    assert result["rows"][0]["open"] == "1.000000000000000001"
    assert result["rows"][0]["volume"] == "100.000000000000000001"


def test_normalizer_rejects_timezone_or_wrong_index_contract():
    wrong_index = frame()
    wrong_index.index = wrong_index.index.set_names(["instrument", "date"])
    with pytest.raises(ValueError, match="response_invalid"):
        normalize(wrong_index)
    timezone_index = frame()
    timezone_index.index = pd.MultiIndex.from_tuples(
        [(SOURCE_ID, pd.Timestamp(day, tz="Asia/Shanghai")) for day in (START, END)],
        names=["order_book_id", "date"],
    )
    with pytest.raises(ValueError, match="date_invalid"):
        normalize(timezone_index)


def test_normalizer_fingerprint_covers_request_and_rows_without_clock():
    result = normalize(frame())
    assert result["validation"]["contentFingerprint"] == normalize(frame())["validation"]["contentFingerprint"]
    changed = frame()
    changed.loc[(SOURCE_ID, pd.Timestamp(START)), "close"] = 1.06
    assert result["validation"]["contentFingerprint"] != normalize(changed)["validation"]["contentFingerprint"]
    wider = normalize_rqdata_etf_daily(
        frame(), SYMBOL, SOURCE_ID, "2026-07-08", END,
    )
    assert result["validation"]["contentFingerprint"] != wider["validation"]["contentFingerprint"]
