"""基金分红每份金额、三类日期与缺失可见性验证。"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from data_provider.rqdata_fund_dividends import normalize_rqdata_fund_dividends


def frame():
    return pd.DataFrame({"book_closure_date": ["2025-06-01"], "payable_date": ["2025-06-03"],
                         "dividend_before_tax": ["0.01234567890123456789"]},
                        index=pd.to_datetime(["2025-06-02"]))


def read(value=None, **changes):
    args = dict(query_fund_code="050116", instrument_type="NAV_FUND", currency="CNY",
                start="2025-01-01", end="2025-12-31", observed_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
                provider_revision="fixture-version")
    args.update(changes)
    return normalize_rqdata_fund_dividends(frame() if value is None else value, "050116.OF", **args)


def test_dates_precision_and_actual_observation_preserved():
    result = read()
    fact = result["facts"][0]
    assert fact["cashAmount"] == "0.01234567890123456789"
    assert (fact["recordDate"], fact["effectiveDate"], fact["paymentDate"]) == (
        "2025-06-01", "2025-06-02", "2025-06-03",
    )
    assert fact["availableAt"] == "2026-09-27T00:00:00Z"
    assert "strategyVisibility" not in fact
    assert result["coverage"]["complete"] is False


@pytest.mark.parametrize("cash", [0, -1, None, True, "NaN", "Infinity"])
def test_invalid_cash_rejected(cash):
    value = frame()
    value["dividend_before_tax"] = cash
    with pytest.raises(ValueError):
        read(value)


@pytest.mark.parametrize("changes", [{"currency": None}, {"provider_revision": ""},
                                     {"observed_at": datetime(2026, 9, 27)}, {"instrument_type": "STOCK"}])
def test_unknown_context_rejected(changes):
    with pytest.raises(ValueError):
        read(**changes)


def test_duplicate_conflict_rejected():
    value = pd.concat([frame(), frame()])
    assert len(read(value)["facts"]) == 1
    value.iloc[1, 2] = "0.02"
    with pytest.raises(ValueError, match="conflicting"):
        read(value)


def test_missing_optional_dates_not_invented():
    value = frame()
    value["book_closure_date"], value["payable_date"] = None, pd.NaT
    fact = read(value)["facts"][0]
    assert "recordDate" not in fact and "paymentDate" not in fact


def test_stock_amount_field_does_not_substitute_for_fund_amount():
    with pytest.raises(ValueError, match="fields"):
        read(frame().rename(columns={"dividend_before_tax": "dividend_cash_before_tax"}))


def test_empty_window_preserves_version_without_claiming_completeness():
    result = read(start="2025-07-01")
    assert result["facts"] == [] and result["providerRevision"] == "fixture-version"
    assert result["coverage"]["complete"] is False


@pytest.mark.parametrize("field,value", [
    ("book_closure_date", "2025-06-03"), ("payable_date", "2025-06-01"),
    ("order_book_id", "000001"), ("ex_dividend_date", "2025-06-04"),
])
def test_conflicting_identity_or_dates_rejected(field, value):
    source = frame()
    source[field] = value
    with pytest.raises(ValueError):
        read(source)
