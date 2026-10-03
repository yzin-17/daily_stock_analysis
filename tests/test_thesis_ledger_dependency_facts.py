"""ThesisLedger V2 依赖级数据端点的定向测试。"""

from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import thesis_ledger as thesis_api
from api.thesis_ledger import router_v3
from src.services import thesis_ledger_dependency_facts as dependencies
from src.services.thesis_ledger_dependency_facts import calendar_fact


def _client(monkeypatch, *, fixture: bool) -> TestClient:
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true" if fixture else "false")
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"authorization": "Bearer test-token"}


def test_instrument_facts_http_passes_exact_qfq_route_and_target(monkeypatch):
    observed = []

    def capture(*_args, **kwargs):
        observed.append(kwargs)
        return {"version": 3, "status": "unavailable"}

    monkeypatch.setattr(thesis_api, "instrument_facts_response", capture)
    client = _client(monkeypatch, fixture=False)
    params = {
        "symbol": "159516.SZ", "market": "CN", "instrumentType": "ETF",
        "start": "2026-04-30", "end": "2026-08-09",
        "executionStart": "2026-05-01", "executionEnd": "2026-08-07",
        "dataAsOf": "2026-09-29T19:36:48Z",
        "barAdjustment": "qfq", "barProviderId": "hithink",
        "barUpstreamSource": "fund-market-historical", "barRouteIndex": 0,
    }
    response = client.get("/api/v3/thesis-ledger/backtest/instrument-facts", params=params, headers=_headers())
    assert response.status_code == 200
    assert observed[0]["route_key"] == {
        "kind": "bar", "market": "CN", "assetType": "ETF",
        "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq",
    }
    assert observed[0]["route_target"] == {
        "providerId": "hithink", "upstreamSource": "fund-market-historical", "routeIndex": 0,
    }
    del params["barRouteIndex"]
    assert client.get(
        "/api/v3/thesis-ledger/backtest/instrument-facts", params=params, headers=_headers(),
    ).status_code == 422
    assert len(observed) == 1


def test_removed_dependency_routes_return_404(monkeypatch):
    client = _client(monkeypatch, fixture=True)
    for path in ("calendar", "instrument-facts", "corporate-actions"):
        response = client.get(f"/api/v1/thesis-ledger/v2/{path}", headers=_headers())
        assert response.status_code == 404


def test_http_calendar_allows_bounded_settlement_dates_without_relaxing_prices(monkeypatch):
    client = _client(monkeypatch, fixture=False)
    params = {"start": "2026-05-18", "end": "2026-06-06",
              "dataAsOf": "2026-05-21T00:00:00Z", "market": "CN"}
    response = client.get("/api/v3/thesis-ledger/backtest/calendar", params=params, headers=_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "supported"
    assert response.json()["facts"][0]["range"]["end"] == "2026-06-06"
    params["end"] = "2027-01-01"
    assert client.get("/api/v3/thesis-ledger/backtest/calendar", params=params, headers=_headers()).status_code == 422
    with pytest.raises(dependencies.DependencyFactError):
        dependencies.validate_range("2026-05-18", "2026-06-06",
                                       datetime(2026, 5, 21, tzinfo=timezone.utc))


def test_http_calendar_respects_verified_package_publication(monkeypatch):
    client = _client(monkeypatch, fixture=False)
    params = {"start": "2020-01-30", "end": "2020-02-03", "market": "CN",
              "dataAsOf": "2026-03-10T03:24:37.055241Z"}
    response = client.get("/api/v3/thesis-ledger/backtest/calendar", params=params, headers=_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["facts"] == []
    params["dataAsOf"] = "2026-03-10T03:24:37.055242Z"
    response = client.get("/api/v3/thesis-ledger/backtest/calendar", params=params, headers=_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "supported"
    assert response.json()["facts"][0]["availableAt"] == "2026-03-10T03:24:37.055242+00:00"


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
        "/api/v3/thesis-ledger/backtest/calendar",
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
    calendar = client.get("/api/v3/thesis-ledger/backtest/calendar", params=params, headers=_headers())
    instrument = client.get(
        "/api/v3/thesis-ledger/backtest/instrument-facts",
        params={**params, "symbol": "600519.SH", "instrumentType": "STOCK", "executionStart": params["start"], "executionEnd": params["end"]},
        headers=_headers(),
    )
    for response in (calendar, instrument):
        assert response.status_code == 200
        payload = response.json()
        assert set(("version", "status", "coverage", "facts", "providerRevision", "reason")) <= set(payload)
        assert payload["version"] == 3
        assert payload["coverage"]["complete"] is True
        assert payload["facts"]
    assert calendar.json()["facts"][0]["availableAt"] < params["dataAsOf"]
    assert instrument.json()["facts"][0]["availableAt"] < params["dataAsOf"]
    assert instrument.json()["facts"][0]["executionRules"]["status"] == "supported"


def test_v2_rejects_symbol_type_mismatch_and_never_leaks_fixture_facts(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "real_cn_tradability",
        lambda *_args, **_kwargs: {
            "provider": "baostock",
            "providerRevision": "baostock-test",
            "coverage": {"start": "2025-01-01", "end": "2025-01-10", "complete": False},
            "tradable": False,
            "suspendedDates": [],
            "ipoDate": "2001-08-27",
            "outDate": None,
            "availableAt": "2025-01-10T16:00:00+00:00",
            "reason": "Provider 未提供历史状态",
        },
    )
    client = _client(monkeypatch, fixture=False)
    response = client.get(
        "/api/v3/thesis-ledger/backtest/instrument-facts",
        params={
            "symbol": "510300.SH",
            "start": "2025-01-01", "end": "2025-01-10",
            "executionStart": "2025-01-02", "executionEnd": "2025-01-10",
            "market": "CN",
            "instrumentType": "STOCK",
            "dataAsOf": "2025-01-10T07:00:00Z",
        },
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"

    calendar = client.get(
        "/api/v3/thesis-ledger/backtest/calendar",
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
        "/api/v3/thesis-ledger/backtest/instrument-facts",
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
    assert instrument.json()["providerRevision"] == "baostock-test+cn-a-share-standard-lot-tick-v1"
    assert instrument.json()["facts"][0]["availableAt"] < "2025-01-10T07:00:00Z"
    assert instrument.json()["facts"][0]["executionRules"] == {
        "status": "unavailable",
        "reason": "缺少覆盖请求历史区间的价格限制、法定收费与结算规则事实",
    }


def test_v2_etf_facts_keep_exchange_identity_and_bar_provenance(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "real_cn_tradability",
        lambda *_args, **_kwargs: {
            "provider": "akshare/tencent",
            "providerRevision": "akshare:manifest:1:config:0;upstreamSource=tencent;routeIndex=0;policyRevision=26",
            "coverage": {"start": "2024-01-02", "end": "2024-01-03", "complete": True},
            "tradable": True,
            "suspendedDates": [],
            "ipoDate": None,
            "outDate": None,
            "availableAt": "2024-01-03T07:00:00+00:00",
            "reason": None,
        },
    )
    client = _client(monkeypatch, fixture=False)
    response = client.get(
        "/api/v3/thesis-ledger/backtest/instrument-facts",
        params={
            "symbol": "159516.SZ",
            "market": "CN",
            "instrumentType": "ETF",
            "start": "2024-01-02",
            "end": "2024-01-03",
            "executionStart": "2024-01-02",
            "executionEnd": "2024-01-03",
            "dataAsOf": "2024-01-04T07:00:00Z",
        },
        headers=_headers(),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "supported"
    assert payload["facts"][0]["instrumentType"] == "ETF"
    assert payload["facts"][0]["currency"] == "CNY"
    assert payload["facts"][0]["lotSize"] == "100"
    assert payload["facts"][0]["tickSize"] == "0.001"
    assert payload["facts"][0]["executionRules"]["status"] == "unavailable"


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
    result = client.get("/api/v3/thesis-ledger/backtest/instrument-facts",
                        params={key: value for key, value in params.items() if value is not None},
                        headers=_headers())
    assert result.status_code == 422


def test_removed_corporate_action_route_is_not_available(monkeypatch):
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
    assert response.status_code == 404
