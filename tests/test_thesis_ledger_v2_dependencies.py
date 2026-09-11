"""ThesisLedger V2 依赖级数据端点的定向测试。"""

from datetime import date, datetime, time, timezone
import sys
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router
from data_provider.fundamental_adapter import normalize_corporate_actions_v2
from src.services.thesis_ledger_v2_dependencies import calendar_fact


def _client(monkeypatch, *, fixture: bool) -> TestClient:
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true" if fixture else "false")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"authorization": "Bearer test-token"}


def test_calendar_fact_rejects_requests_outside_provider_coverage(monkeypatch):
    import exchange_calendars as xcals
    from src.core import trading_calendar

    schedule = pd.DataFrame(
        index=pd.DatetimeIndex(["2025-01-02", "2025-01-03", "2025-01-06"])
    )
    calendar = SimpleNamespace(
        first_session=pd.Timestamp("2025-01-01"),
        last_session=pd.Timestamp("2025-01-06"),
        schedule=schedule,
        open_times=[(None, time(9, 30))],
        break_start_times=[(None, time(11, 30))],
        break_end_times=[(None, time(13, 0))],
        close_times=[(None, time(15, 0))],
    )
    monkeypatch.setattr(trading_calendar, "_XCALS_AVAILABLE", True)
    monkeypatch.setattr(xcals, "get_calendar", lambda _name: calendar)

    assert calendar_fact(date(2024, 12, 31), date(2025, 1, 2), datetime.now(timezone.utc)) is None
    assert calendar_fact(date(2025, 1, 3), date(2025, 1, 7), datetime.now(timezone.utc)) is None

    fact = calendar_fact(date(2025, 1, 1), date(2025, 1, 3), datetime.now(timezone.utc))
    assert fact is not None
    assert fact["range"] == {"start": "2025-01-01", "end": "2025-01-03"}
    assert fact["holidays"] == ["2025-01-01"]

    client = _client(monkeypatch, fixture=False)
    response = client.get(
        "/api/v1/thesis-ledger/v2/calendar",
        params={
            "market": "CN",
            "start": "2024-12-31",
            "end": "2025-01-02",
            "dataAsOf": "2025-01-10T00:00:00Z",
        },
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["coverage"]["complete"] is False
    assert response.json()["facts"] == []


def test_v2_dependency_routes_have_auditable_fixture_envelope(monkeypatch):
    client = _client(monkeypatch, fixture=True)
    params = {
        "market": "CN",
        "start": "2025-01-01",
        "end": "2025-01-10",
        "dataAsOf": "2025-01-10T07:00:00Z",
    }
    calendar = client.get("/api/v1/thesis-ledger/v2/calendar", params=params, headers=_headers())
    instrument = client.get(
        "/api/v1/thesis-ledger/v2/instrument-facts",
        params={**params, "symbol": "600519.SH", "instrumentType": "STOCK", "executionStart": params["start"], "executionEnd": params["end"]},
        headers=_headers(),
    )
    actions = client.get(
        "/api/v1/thesis-ledger/v2/corporate-actions",
        params={**params, "symbol": "600519.SH", "instrumentType": "STOCK"},
        headers=_headers(),
    )
    for response in (calendar, instrument, actions):
        assert response.status_code == 200
        payload = response.json()
        assert set(("version", "status", "coverage", "facts", "providerRevision", "reason")) <= set(payload)
        assert payload["version"] == 2
        assert payload["coverage"]["complete"] is True
        assert payload["facts"]
    assert calendar.json()["facts"][0]["availableAt"] < params["dataAsOf"]
    assert instrument.json()["facts"][0]["availableAt"] < params["dataAsOf"]
    assert instrument.json()["facts"][0]["executionRules"]["status"] == "supported"


def test_v2_rejects_etf_and_never_leaks_fixture_facts(monkeypatch):
    client = _client(monkeypatch, fixture=False)
    response = client.get(
        "/api/v1/thesis-ledger/v2/instrument-facts",
        params={
            "symbol": "510300.SH",
            "start": "2025-01-01", "end": "2025-01-10",
            "executionStart": "2025-01-02", "executionEnd": "2025-01-10",
            "market": "CN",
            "instrumentType": "ETF",
            "dataAsOf": "2025-01-10T07:00:00Z",
        },
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_capability"

    calendar = client.get(
        "/api/v1/thesis-ledger/v2/calendar",
        params={
            "market": "CN",
            "start": "2025-01-01",
            "end": "2025-01-10",
            "dataAsOf": "2025-01-10T07:00:00Z",
        },
        headers=_headers(),
    )
    assert calendar.status_code == 200
    assert all("fixture" not in str(value).lower() for value in calendar.json().values())

    instrument = client.get(
        "/api/v1/thesis-ledger/v2/instrument-facts",
        params={
            "symbol": "600519.SH",
            "market": "CN",
            "instrumentType": "STOCK",
            "start": "2025-01-01", "end": "2025-01-10",
            "executionStart": "2025-01-02", "executionEnd": "2025-01-10",
            "dataAsOf": "2025-01-10T07:00:00Z",
        },
        headers=_headers(),
    )
    assert instrument.status_code == 200
    assert instrument.json()["status"] == "unavailable"
    assert instrument.json()["coverage"] == {"start": "2025-01-01", "end": "2025-01-10", "complete": False}
    assert instrument.json()["missingInputs"][0]["field"] == "historicalTradability"
    assert instrument.json()["missingInputs"][1]["range"]["start"] == "2025-01-02"
    assert instrument.json()["providerRevision"] == "cn-a-share-standard-lot-tick-v1"
    assert instrument.json()["facts"][0]["availableAt"] < "2025-01-10T07:00:00Z"
    assert instrument.json()["facts"][0]["executionRules"] == {
        "status": "unavailable",
        "reason": "缺少覆盖请求历史区间的价格限制、法定收费与结算规则事实",
    }


@pytest.mark.parametrize("overrides", [
    {"start": None},
    {"executionStart": None},
    {"start": "2025-01-11"},
    {"executionStart": "2024-12-31"},
    {"executionEnd": "2025-01-11"},
    {"end": "2025-02-30"},
])
def test_instrument_facts_reject_missing_or_invalid_ranges(monkeypatch, overrides):
    client = _client(monkeypatch, fixture=False)
    params = {
        "symbol": "600519.SH", "market": "CN", "instrumentType": "STOCK",
        "start": "2025-01-01", "end": "2025-01-10",
        "executionStart": "2025-01-02", "executionEnd": "2025-01-10",
        "dataAsOf": "2025-01-10T07:00:00Z", **overrides,
    }
    result = client.get("/api/v1/thesis-ledger/v2/instrument-facts",
                        params={key: value for key, value in params.items() if value is not None},
                        headers=_headers())
    assert result.status_code == 422


def test_corporate_action_normalization_filters_future_knowledge_and_preserves_provenance():
    frame = pd.DataFrame(
        [
            {
                "股票代码": "600519",
                "除权除息日": "2025-01-05",
                "公告日期": "2024-12-20",
                "每股派息": "1.25",
            },
            {
                "股票代码": "600519",
                "除权除息日": "2025-01-06",
                "公告日期": "2025-02-01",
                "每股派息": "0.50",
            },
        ]
    )
    result = normalize_corporate_actions_v2(
        frame,
        "600519.SH",
        start_date="2025-01-01",
        end_date="2025-01-31",
        data_as_of=datetime(2025, 1, 10, tzinfo=timezone.utc),
    )
    assert result["coverage"]["complete"] is True
    assert len(result["facts"]) == 1
    assert result["facts"][0]["cashAmount"] == "1.25"
    assert result["facts"][0]["providerRevision"].startswith("akshare")
    assert result["facts"][0]["availableAt"] <= "2025-01-10T00:00:00+00:00"


def test_corporate_action_normalization_requires_economic_effective_date():
    frame = pd.DataFrame(
        [
            {
                "股票代码": "600519",
                "公告日期": "2025-01-03",
                "每股派息": "1.25",
            },
            {
                "股票代码": "600519",
                "股权登记日": "2025-01-08",
                "公告日期": "2025-01-03",
                "每股派息": "0.50",
            },
            {
                "股票代码": "600519",
                "除权除息日": "2025-01-10",
                "公告日期": "2025-01-03",
                "每股派息": "0.80",
            },
        ]
    )

    result = normalize_corporate_actions_v2(
        frame,
        "600519.SH",
        start_date="2025-01-01",
        end_date="2025-01-31",
        data_as_of=datetime(2025, 1, 31, tzinfo=timezone.utc),
        source_complete=True,
    )

    assert result["coverage"]["complete"] is False
    assert [fact["cashAmount"] for fact in result["facts"]] == ["0.8"]
    assert result["facts"][0]["occurredAt"].startswith("2025-01-09T16:00:00")
    assert result["facts"][0]["availableAt"].startswith("2025-01-02T16:00:00")


def test_corporate_action_empty_requires_explicit_complete_source():
    empty = pd.DataFrame()
    kwargs = {
        "stock_code": "600519.SH",
        "start_date": "2025-01-01",
        "end_date": "2025-01-31",
        "data_as_of": datetime(2025, 1, 31, tzinfo=timezone.utc),
    }
    assert normalize_corporate_actions_v2(empty, **kwargs)["coverage"]["complete"] is False
    assert normalize_corporate_actions_v2(empty, source_complete=True, **kwargs)["coverage"]["complete"] is True


def test_akshare_adapter_can_assert_a_successful_empty_source(monkeypatch):
    from data_provider.fundamental_adapter import AkshareFundamentalAdapter

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_fhps_detail_em=lambda **kwargs: pd.DataFrame()),
    )
    result = AkshareFundamentalAdapter().get_corporate_actions_v2(
        "600519.SH",
        start_date="2025-01-01",
        end_date="2025-01-31",
        data_as_of=datetime(2025, 1, 31, tzinfo=timezone.utc),
    )
    assert result["coverage"]["complete"] is True
    assert result["facts"] == []


def test_corporate_action_provider_failure_is_not_an_empty_success(monkeypatch):
    from data_provider.fundamental_adapter import AkshareFundamentalAdapter

    monkeypatch.setattr(
        AkshareFundamentalAdapter,
        "get_corporate_actions_v2",
        lambda self, *args, **kwargs: {
            "facts": [],
            "coverage": {"start": "2025-01-01", "end": "2025-01-31", "complete": False},
        },
    )
    client = _client(monkeypatch, fixture=False)
    response = client.get(
        "/api/v1/thesis-ledger/v2/corporate-actions",
        params={
            "symbol": "600519.SH",
            "market": "CN",
            "instrumentType": "STOCK",
            "start": "2025-01-01",
            "end": "2025-01-31",
            "dataAsOf": "2025-01-31T07:00:00Z",
        },
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["coverage"]["complete"] is False
