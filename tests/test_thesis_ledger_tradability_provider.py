"""ThesisLedger V2 历史上市/停牌事实的定向测试。"""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from src.services import thesis_ledger_dependency_facts as dependencies
from src.services import thesis_ledger_tradability_provider as tradability
from src.services.thesis_ledger_tradability_provider import (
    normalize_complete_bar_tradability,
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


def test_complete_point_in_time_bars_prove_tradability_with_provenance():
    result = normalize_complete_bar_tradability(
        bars=pd.DataFrame(
            [
                {"date": "2024-01-02", "volume": 100},
                {"date": "2024-01-03", "volume": 200},
            ]
        ),
        expected_sessions={"2024-01-02", "2024-01-03"},
        start=date(2024, 1, 2),
        end=date(2024, 1, 3),
        data_as_of=datetime(2024, 1, 4, tzinfo=timezone.utc),
        provider="akshare",
        upstream_source="tencent",
        provider_revision="akshare:manifest:1:config:0",
        route_index=0,
        policy_revision=23,
    )

    assert result["coverage"]["complete"] is True
    assert result["tradable"] is True
    assert result["provider"] == "akshare/tencent"
    assert "upstreamSource=tencent" in result["providerRevision"]
    assert "policyRevision=23" in result["providerRevision"]
    assert result["availableAt"] == "2024-01-03T07:00:00+00:00"


def test_real_tradability_uses_exact_v3_qfq_route_and_pinned_target(monkeypatch):
    frame = pd.DataFrame(
        [
            {"date": "2024-01-02", "volume": 100},
            {"date": "2024-01-03", "volume": 200},
        ]
    )
    frame.attrs["upstream_source"] = "fund-market-historical"
    for name, value in {"open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "amount": 1000}.items():
        frame[name] = value
    source = {"provider": "fixture", "revision": "r1", "availableAt": "2024-01-01T00:00:00Z"}
    frame.attrs["daily_tradability_input"] = {
        "symbol": "159516.SZ", "range": {"start": "2024-01-02", "end": "2024-01-03"},
        "listing": {"listedOn": "2023-07-27", "source": source},
        "calendar": {"source": source, "expectedSessions": ["2024-01-02", "2024-01-03"]},
        "responseSha256": "a" * 64, "observedAt": "2024-01-03T08:00:00Z",
        "missingSessions": [], "paginationComplete": True,
    }
    route_key = {
        "kind": "bar", "market": "CN", "assetType": "ETF",
        "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq",
    }
    route_target = {
        "providerId": "hithink", "upstreamSource": "fund-market-historical", "routeIndex": 0,
    }
    observed = []

    class FakeRuntime:
        def execute_market_bars_v3(self, request, key, *, route_target):
            observed.append((request, key, route_target))
            return type("FakeResult", (), {
                "value": frame,
                "provider": "hithink",
                "provider_revision": "hithink:admitted:1",
                "route_index": 0,
                "fallback_used": False,
                "effective_policy": {"revision": 23},
            })()

    monkeypatch.setattr(tradability, "_cn_exchange_sessions", lambda *_args: {"2024-01-02", "2024-01-03"})
    monkeypatch.setattr(
        "src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime",
        FakeRuntime,
    )

    result = real_cn_tradability(
        "159516.SZ",
        date(2024, 1, 2),
        date(2024, 1, 3),
        datetime(2024, 1, 4, tzinfo=timezone.utc),
        instrument_type="ETF", route_key=route_key, route_target=route_target,
    )

    assert result["coverage"]["complete"] is True
    assert result["tradable"] is True
    assert result["provider"] == "hithink/fund-market-historical"
    assert len(observed) == 1
    request, actual_key, actual_target = observed[0]
    assert request.adjustment == "qfq"
    assert request.instrument_type == "ETF"
    assert actual_key == route_key
    assert actual_target == route_target

    frame.attrs["upstream_source"] = "unexpected-source"
    rejected = real_cn_tradability(
        "159516.SZ", date(2024, 1, 2), date(2024, 1, 3),
        datetime(2024, 1, 4, tzinfo=timezone.utc),
        instrument_type="ETF", route_key=route_key, route_target=route_target,
    )
    assert rejected["coverage"]["complete"] is False
    assert rejected["tradable"] is False


def test_real_tradability_without_exact_route_does_not_query_provider(monkeypatch):
    monkeypatch.setattr(
        "src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime",
        lambda: pytest.fail("缺少精确路由时不得查询来源"),
    )
    result = real_cn_tradability(
        "159516.SZ", date(2024, 1, 2), date(2024, 1, 3),
        datetime(2024, 1, 4, tzinfo=timezone.utc), instrument_type="ETF",
    )
    assert result["coverage"]["complete"] is False
    assert "精确日线 RouteKey" in result["reason"]


@pytest.mark.parametrize(
    ("bars", "reason_fragment"),
    [
        (
            pd.DataFrame([{"date": "2024-01-02", "volume": 100}]),
            "覆盖不完整",
        ),
        (
            pd.DataFrame(
                [
                    {"date": "2024-01-02", "volume": 100},
                    {"date": "2024-01-03", "volume": 0},
                ]
            ),
            "成交量未证明为正数",
        ),
    ],
)
def test_incomplete_point_in_time_bars_fail_closed_without_inferring_suspension(
    bars: pd.DataFrame, reason_fragment: str
):
    result = normalize_complete_bar_tradability(
        bars=bars,
        expected_sessions={"2024-01-02", "2024-01-03"},
        start=date(2024, 1, 2),
        end=date(2024, 1, 3),
        data_as_of=datetime(2024, 1, 4, tzinfo=timezone.utc),
        provider="akshare",
        upstream_source="tencent",
        provider_revision="akshare:manifest:1:config:0",
        route_index=0,
        policy_revision=23,
    )

    assert result["coverage"]["complete"] is False
    assert result["tradable"] is False
    assert result["suspendedDates"] == []
    assert reason_fragment in result["reason"]


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
    assert payload["facts"][0]["availableAt"] == "1990-12-18T16:00:00+00:00"
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
