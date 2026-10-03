"""东财元/份、日期分离及不完整覆盖的离线合同反例。"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from data_provider.eastmoney_fund_dividends import normalize_eastmoney_fund_dividends


ROW = {"基金代码": "510300", "权益登记日": "2025-06-17", "除息日期": "2025-06-18",
       "分红": "0.088000", "分红发放日": "2025-06-27"}


def normalize(rows):
    return normalize_eastmoney_fund_dividends(
        pd.DataFrame(rows, columns=list(ROW)), "510300.SH", start="2025-06-01", end="2025-06-30",
        observed_at=datetime(2026, 9, 27, tzinfo=timezone.utc), provider_revision="fixture-v1",
    )


def test_cash_is_per_unit_and_visibility_is_not_backdated():
    result = normalize([ROW, {**ROW, "基金代码": "159516"}, ROW])
    assert len(result["facts"]) == 1
    fact = result["facts"][0]
    assert fact["cashAmount"] == "0.088"
    assert fact["effectiveDate"] == "2025-06-18"
    assert fact["recordDate"] == "2025-06-17"
    assert fact["paymentDate"] == "2025-06-27"
    assert fact["occurredAt"] == "2025-06-18T00:00:00+08:00"
    assert fact["availableAt"] == "2026-09-27T00:00:00Z"
    assert "strategyVisibility" not in fact
    observation = result["observations"][0]
    assert observation["recordDate"] == "2025-06-17"
    assert observation["paymentDate"] == "2025-06-27"
    assert observation["announcementDate"] is None
    assert result["coverage"]["complete"] is False


@pytest.mark.parametrize("rows", [[], [{**ROW, "除息日期": "2025-07-01"}],
                                  [{**ROW, "基金代码": "159516"}]])
def test_empty_or_filtered_response_does_not_prove_coverage(rows):
    result = normalize(rows)
    assert result["facts"] == []
    assert result["coverage"]["complete"] is False


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-0.1", "0", None, True, "not-a-number"])
def test_invalid_cash_is_rejected(value):
    with pytest.raises(ValueError):
        normalize([{**ROW, "分红": value}])


@pytest.mark.parametrize("overrides", [
    {"除息日期": None}, {"除息日期": "2025-02-30"}, {"权益登记日": "2025-06-19"},
    {"分红发放日": "2025-06-17"}, {"基金代码": 510300},
])
def test_missing_identity_or_invalid_dates_are_rejected(overrides):
    with pytest.raises(ValueError):
        normalize([{**ROW, **overrides}])


def test_missing_optional_dates_remain_unknown():
    result = normalize([{**ROW, "权益登记日": None, "分红发放日": "--"}])
    assert result["observations"][0]["recordDate"] is None
    assert result["observations"][0]["paymentDate"] is None
    assert result["facts"][0]["effectiveDate"] == "2025-06-18"
    assert "recordDate" not in result["facts"][0]
    assert "paymentDate" not in result["facts"][0]


def test_conflicting_duplicate_is_not_silently_deduplicated():
    with pytest.raises(ValueError, match="冲突"):
        normalize([ROW, {**ROW, "分红": "0.09"}])
