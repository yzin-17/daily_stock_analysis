"""结构化拆分观测不能隐式升级成执行事实。"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from data_provider.eastmoney_fund_splits import normalize_eastmoney_fund_splits

ROW = {"基金代码": "159596", "拆分折算日": "2025-10-17", "拆分类型": "份额分拆", "拆分折算": "2.000000"}


def normalize(rows):
    return normalize_eastmoney_fund_splits(
        pd.DataFrame(rows, columns=ROW), "159596.SZ", start="2025-10-01", end="2025-10-31",
        observed_at=datetime(2026, 9, 27, tzinfo=timezone.utc), provider_revision="fixture-v1",
    )


def test_source_date_is_not_promoted_to_open_effective_event():
    result = normalize([ROW, ROW])
    assert result["facts"] == []
    assert result["coverage"]["complete"] is False
    assert len(result["observations"]) == 1
    row = result["observations"][0]
    assert row["sourceConversionDate"] == "2025-10-17"
    assert row["sourceRatioPerUnit"] == "2"
    assert row["effectiveDate"] is None
    assert row["effectivePhase"] == "unknown"
    assert row["announcementDate"] is None
    assert "occurredAt" not in row and "strategyVisibility" not in row


@pytest.mark.parametrize("ratio", ["0.010000", "1.123456789012345678901"])
def test_ratio_preserves_precision_and_reverse_conversion(ratio):
    result = normalize([{**ROW, "拆分折算": ratio}])
    assert result["observations"][0]["sourceRatioPerUnit"] == ratio.rstrip("0")


@pytest.mark.parametrize("field,value", [
    ("拆分折算", "NaN"), ("拆分折算", "Infinity"), ("拆分折算", "0"),
    ("拆分折算", "-2"), ("拆分折算", "1:2"), ("拆分折算日", "2025-02-30"),
    ("拆分折算日", None), ("拆分类型", "未知"), ("基金代码", 159596),
])
def test_invalid_source_facts_are_rejected(field, value):
    with pytest.raises(ValueError):
        normalize([{**ROW, field: value}])


def test_conflict_is_rejected():
    with pytest.raises(ValueError, match="冲突"):
        normalize([ROW, {**ROW, "拆分折算": "3"}])


@pytest.mark.parametrize("rows", [[], [{**ROW, "基金代码": "159220"}], [{**ROW, "拆分折算日": "2025-09-30"}]])
def test_empty_filtered_observations_do_not_prove_absence(rows):
    result = normalize(rows)
    assert result["facts"] == result["observations"] == []
    assert result["coverage"]["complete"] is False
