"""基金拆分语义、来源身份和缺失可见性边界。"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from data_provider.rqdata_fund_splits import normalize_rqdata_fund_splits


def read(frame=None, **changes):
    args = dict(symbol="000246.OF", query_fund_code="000246", instrument_type="NAV_FUND",
                start="2025-01-01", end="2025-12-31", observed_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
                provider_revision="fixture-revision")
    args.update(changes)
    if frame is None:
        frame = pd.DataFrame({"split_ratio": ["1.25"]}, index=pd.to_datetime(["2025-06-01"]))
    return normalize_rqdata_fund_splits(frame, **args)


def test_effective_date_does_not_backdate_observation_or_create_visibility():
    result = read()
    fact = result["facts"][0]
    assert fact["ratio"] == "1.25"
    assert fact["type"] == "SPLIT"
    assert fact["effectiveDate"] == "2025-06-01"
    assert fact["availableAt"] == "2026-09-27T00:00:00Z"
    assert "strategyVisibility" not in fact
    assert result["coverage"]["complete"] is False


@pytest.mark.parametrize("ratio,kind", [("0.25", "REVERSE_SPLIT"), ("2", "SPLIT"), ("1", None)])
def test_ratio_direction_and_noop(ratio, kind):
    result = read(pd.DataFrame({"ex_dividend_date": ["2025-06-01"], "split_ratio": [ratio]}))
    assert len(result["observations"]) == 1
    if kind is None:
        assert result["facts"] == []
    else:
        assert result["facts"][0]["type"] == kind


@pytest.mark.parametrize("ratio", [0, -1, True, None, "NaN", "Infinity", "bad"])
def test_invalid_ratios_fail(ratio):
    with pytest.raises(ValueError):
        read(pd.DataFrame({"ex_dividend_date": ["2025-06-01"], "split_ratio": [ratio]}))


def test_identical_duplicates_dedupe_but_conflicts_reject():
    frame = pd.DataFrame({"ex_dividend_date": ["2025-06-01"] * 2, "split_ratio": ["2.0", "2"]})
    assert len(read(frame)["facts"]) == 1
    frame.loc[1, "split_ratio"] = "1"
    with pytest.raises(ValueError, match="conflicting"):
        read(frame)


def test_empty_window_retains_revision_and_unknown_coverage():
    result = read(start="2025-07-01")
    assert result["facts"] == []
    assert result["providerRevision"] == "fixture-revision"
    assert result["coverage"]["complete"] is False


@pytest.mark.parametrize("changes", [
    {"query_fund_code": "000246.XSHE"}, {"instrument_type": "STOCK"},
    {"instrument_type": "ETF"}, {"provider_revision": ""},
    {"observed_at": datetime(2026, 9, 27)}, {"start": "2025-12-31", "end": "2025-01-01"},
])
def test_invalid_context_rejected(changes):
    with pytest.raises(ValueError):
        read(**changes)


def test_response_code_must_match_exact_query():
    frame = pd.DataFrame({"order_book_id": ["999999"], "ex_dividend_date": ["2025-06-01"], "split_ratio": [2]})
    with pytest.raises(ValueError, match="identity"):
        read(frame)


@pytest.mark.parametrize("day", [None, "20250601", "2025-06-01T12:00:00", pd.Timestamp("2025-06-01T01:00:00")])
def test_missing_or_intraday_dates_rejected(day):
    with pytest.raises(ValueError):
        read(pd.DataFrame({"ex_dividend_date": [day], "split_ratio": [2]}))
