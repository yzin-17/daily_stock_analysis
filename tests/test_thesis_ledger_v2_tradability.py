"""ThesisLedger V2 历史上市/停牌事实的定向测试。"""

from datetime import date, datetime, timezone

import pandas as pd

from src.services import thesis_ledger_v2_dependencies as dependencies
from src.services.thesis_ledger_v2_tradability import (
    normalize_baostock_tradability,
    real_cn_tradability,
)


def _basic(*, ipo: str = "2001-08-27", out: str = "") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "code": "sh.600519",
                "code_name": "贵州茅台",
                "ipoDate": ipo,
                "outDate": out,
                "type": "1",
                "status": "1" if not out else "0",
            }
        ]
    )


def _calendar() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"calendar_date": "2024-01-01", "is_trading_day": "0"},
            {"calendar_date": "2024-01-02", "is_trading_day": "1"},
            {"calendar_date": "2024-01-03", "is_trading_day": "1"},
        ]
    )


def _normalize(history: pd.DataFrame, *, basic: pd.DataFrame | None = None):
    return normalize_baostock_tradability(
        basic=basic if basic is not None else _basic(),
        trading_calendar=_calendar(),
        history=history,
        start=date(2024, 1, 1),
        end=date(2024, 1, 3),
        provider_revision="baostock-test-tradestatus-v1",
        available_at="2024-01-03T16:00:00+00:00",
    )


def test_baostock_tradability_requires_explicit_status_for_every_trading_session():
    result = _normalize(pd.DataFrame([{"date": "2024-01-02", "tradestatus": "1"}]))

    assert result["coverage"]["complete"] is False
    assert result["tradable"] is False
    assert result["suspendedDates"] == []
    assert "缺失会话 2024-01-03" in result["reason"]


def test_baostock_tradability_identifies_suspension_without_guessing_from_missing_bars():
    result = _normalize(
        pd.DataFrame(
            [
                {"date": "2024-01-02", "tradestatus": "1"},
                {"date": "2024-01-03", "tradestatus": "0"},
            ]
        )
    )

    assert result["coverage"]["complete"] is True
    assert result["tradable"] is False
    assert result["suspendedDates"] == ["2024-01-03"]
    assert "2024-01-03" in result["reason"]


def test_baostock_tradability_accepts_fully_covered_listed_range():
    result = _normalize(
        pd.DataFrame(
            [
                {"date": "2024-01-02", "tradestatus": "1"},
                {"date": "2024-01-03", "tradestatus": "1"},
            ]
        )
    )

    assert result["coverage"] == {"start": "2024-01-01", "end": "2024-01-03", "complete": True}
    assert result["tradable"] is True
    assert result["reason"] is None
    assert result["ipoDate"] == "2001-08-27"


def test_baostock_tradability_rejects_pre_listing_range_with_complete_evidence():
    result = _normalize(
        pd.DataFrame(),
        basic=_basic(ipo="2024-01-03"),
    )

    assert result["coverage"]["complete"] is True
    assert result["tradable"] is False
    assert "上市日为 2024-01-03" in result["reason"]


def test_real_tradability_does_not_query_provider_before_conservative_knowledge_time():
    result = real_cn_tradability(
        "600519.SH",
        date(2025, 1, 1),
        date(2025, 1, 10),
        datetime(2025, 1, 10, 7, 0, tzinfo=timezone.utc),
    )

    assert result["coverage"]["complete"] is False
    assert result["tradable"] is False
    assert "dataAsOf" in result["reason"]


def test_instrument_facts_supports_critical_history_while_rules_remain_model_assumption(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "real_cn_tradability",
        lambda *_args, **_kwargs: {
            "provider": "baostock",
            "providerRevision": "baostock-test-tradestatus-v1",
            "coverage": {"start": "2024-01-01", "end": "2024-01-03", "complete": True},
            "tradable": True,
            "suspendedDates": [],
            "ipoDate": "2001-08-27",
            "outDate": None,
            "availableAt": "2024-01-03T16:00:00+00:00",
            "reason": None,
        },
    )

    payload = dependencies.instrument_facts_response(
        "600519.SH",
        datetime(2024, 1, 3, 16, 0, tzinfo=timezone.utc),
        "2024-01-01",
        "2024-01-03",
        "2024-01-02",
        "2024-01-03",
        [],
    )

    assert payload["status"] == "supported"
    assert payload["coverage"]["complete"] is True
    assert payload["reason"] is None
    assert payload["facts"][0]["tradable"] is True
    assert payload["facts"][0]["executionRules"]["status"] == "unavailable"
    assert [item["field"] for item in payload["missingInputs"]] == ["executionRules"]
    assert payload["missingInputs"][0]["category"] == "modelAssumption"


def test_instrument_facts_keeps_known_suspension_as_critical_blocker(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "real_cn_tradability",
        lambda *_args, **_kwargs: {
            "provider": "baostock",
            "providerRevision": "baostock-test-tradestatus-v1",
            "coverage": {"start": "2024-01-01", "end": "2024-01-03", "complete": True},
            "tradable": False,
            "suspendedDates": ["2024-01-03"],
            "ipoDate": "2001-08-27",
            "outDate": None,
            "availableAt": "2024-01-03T16:00:00+00:00",
            "reason": "请求区间包含停牌交易日: 2024-01-03",
        },
    )

    payload = dependencies.instrument_facts_response(
        "600519.SH",
        datetime(2024, 1, 3, 16, 0, tzinfo=timezone.utc),
        "2024-01-01",
        "2024-01-03",
        "2024-01-02",
        "2024-01-03",
        [],
    )

    assert payload["status"] == "unavailable"
    assert payload["coverage"]["complete"] is True
    assert payload["facts"][0]["tradable"] is False
    assert payload["missingInputs"][0]["field"] == "historicalTradability"
    assert payload["missingInputs"][0]["category"] == "criticalFact"
    assert "2024-01-03" in payload["reason"]
