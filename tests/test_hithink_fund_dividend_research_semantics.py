"""研究约定只解释已公布方案，旧入口与未知覆盖保持。"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from data_provider.hithink_fund_dividend_semantics import BUSINESS_FIELDS, PLAN_ANNOUNCED_SEMANTICS
from data_provider.hithink_fund_dividends import normalize_hithink_fund_dividends


def ms(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)


ROW = {
    "progress": "2", "per_ten_cash_before_tax": "0.8800", "per_ten_cash_after_tax": 0,
    "publish_date_ms": ms("2025-06-11"), "registration_date_ms": ms("2025-06-17"),
    "ex_dividend_date_ms": ms("2025-06-18"), "payment_date_ms": ms("2025-06-27"),
    "reinvestment_date_ms": None, "profit_base_date_ms": ms("2025-04-08"),
    "in_dividend_date_ms": ms("2025-06-18"),
}
EMPTY = {field: None for field in BUSINESS_FIELDS}


def normalize(rows, **changes):
    options = {
        "instrument_type": "ETF", "currency": "CNY", "start": "2025-06-01", "end": "2025-06-30",
        "observed_at": datetime(2026, 10, 9, tzinfo=timezone.utc), "provider_revision": "test-v1",
        "research_semantics": PLAN_ANNOUNCED_SEMANTICS,
    }
    return normalize_hithink_fund_dividends(rows, "510300.SH", **{**options, **changes})


@pytest.mark.parametrize("observed_at", [
    datetime(2025, 6, 11, tzinfo=timezone.utc), datetime(2026, 10, 9, tzinfo=timezone.utc),
])
def test_announced_plan_never_becomes_payment_fact_before_or_after_payment_date(observed_at):
    result = normalize([ROW, ROW], observed_at=observed_at)
    assert result["facts"] == []
    assert len(result["observations"]) == 1
    plan = result["observations"][0]
    assert plan["planProgress"] == "实施方案已公布"
    assert plan["rawPlanProgress"] == "2"
    assert plan["progressSemantics"] == PLAN_ANNOUNCED_SEMANTICS
    assert plan["progressSemanticsBasis"] == "research-assumption"
    assert plan["cashAmount"] == "0.088" and plan["cashAfterTaxAmount"] == "0"
    assert plan["cashBeforeTaxPerTen"] == "0.88" and plan["cashAfterTaxPerTen"] == "0"
    assert plan["cashOriginalUnit"] == "per-ten-fund-units" and plan["currency"] == "CNY"
    assert [plan[key] for key in ("announcementDate", "recordDate", "effectiveDate", "paymentDate")] == [
        "2025-06-11", "2025-06-17", "2025-06-18", "2025-06-27",
    ]
    assert plan["reinvestmentDate"] is None
    assert "paid" not in plan and "strategyVisibility" not in plan
    assert result["coverage"] == {"start": "2025-06-01", "end": "2025-06-30", "complete": False}


def test_default_entry_still_rejects_numeric_progress_and_preserves_literal_implementation():
    with pytest.raises(ValueError, match="口径未经核验"):
        normalize([ROW], research_semantics=None)
    legacy = normalize([{**ROW, "progress": "实施"}], research_semantics=None)
    assert len(legacy["facts"]) == 1
    assert "researchSemantics" not in legacy
    assert "progressSemantics" not in legacy["observations"][0]


@pytest.mark.parametrize("progress", ["0", "1", "3", "取消", 2, None, True])
def test_other_progress_values_are_not_inferred(progress):
    with pytest.raises(ValueError, match="进度"):
        normalize([{**ROW, "progress": progress}])


@pytest.mark.parametrize("version", ["", "hithink-plan-announced-v2", True])
def test_unknown_research_version_rejects_even_empty_input(version):
    with pytest.raises(ValueError, match="语义版本"):
        normalize([], research_semantics=version)


@pytest.mark.parametrize("change", [
    {"publish_date_ms": None}, {"ex_dividend_date_ms": None}, {"per_ten_cash_before_tax": None},
    {"per_ten_cash_before_tax": "NaN"}, {"per_ten_cash_before_tax": -1},
    {"per_ten_cash_before_tax": 0}, {"per_ten_cash_before_tax": True},
    {"registration_date_ms": ms("2025-06-19")}, {"payment_date_ms": ms("2025-06-17")},
])
def test_announced_plan_retains_required_field_and_date_checks(change):
    with pytest.raises(ValueError):
        normalize([{**ROW, **change}])


def test_empty_placeholder_is_observed_as_ignored_without_zero_history_claim():
    result = normalize([EMPTY])
    assert result["facts"] == result["observations"] == []
    assert result["ignoredEmptyRows"] == 1
    assert result["coverage"]["complete"] is False
    with pytest.raises(ValueError, match="进度缺失"):
        normalize([EMPTY], research_semantics=None)


@pytest.mark.parametrize("row", [{}, {**EMPTY, "unknown": "not-empty"}, {**EMPTY, "progress": "2"}])
def test_nearly_empty_rows_are_not_silently_discarded(row):
    with pytest.raises(ValueError):
        normalize([row])


@pytest.mark.parametrize("change", [
    {"per_ten_cash_before_tax": "0.89"}, {"payment_date_ms": ms("2025-06-28")},
    {"progress": "预案"}, {"progress": "实施"},
])
def test_conflicting_same_day_plans_reject(change):
    with pytest.raises(ValueError, match="冲突"):
        normalize([ROW, {**ROW, **change}])


def test_requested_window_filters_observations_without_changing_coverage():
    result = normalize([ROW], start="2025-07-01", end="2025-07-31")
    assert result["facts"] == result["observations"] == []
    assert result["coverage"]["complete"] is False


def test_future_announcement_cannot_be_called_published_at_observation_time():
    with pytest.raises(ValueError, match="公告日晚于观察"):
        normalize([ROW], observed_at=datetime(2025, 6, 10, tzinfo=timezone.utc))
