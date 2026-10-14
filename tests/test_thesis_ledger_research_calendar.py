"""现行日历仅解锁明确股票研究窗口，上市及旧来源边界保持。"""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.services import thesis_ledger_research_calendar as research
from src.services.thesis_ledger_market_v3_facts import market_coverage_context_v3

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


def request(start="2023-07-08", end="2023-09-15"):
    return {
        "symbol": "600000.SH", "start": start, "end": end,
        "tradabilityMode": "assume-untradable-no-bar",
        "routeKey": {"kind": "bar", "market": "CN", "assetType": "STOCK", "capability": "DAILY_BAR",
                     "timeframe": "1d", "adjustment": "qfq"},
        "routeTarget": {"providerId": "tencent", "upstreamSource": "tencent", "routeIndex": 0},
    }


def test_extended_window_uses_real_verified_calendar():
    context = research.research_market_coverage_context(request(), observed_at=NOW)
    assert context is not None
    calendar = context["calendar"]
    days = calendar["expectedSessionDates"]
    assert len(days) == 50 and days[0] == "2023-07-10" and days[-1] == "2023-09-15"
    assert days == sorted(set(days))
    assert calendar["source"] == "exchange-calendars/XSHG"
    assert "availableAt=2026-03-10T03:24:37.055242+00:00" in calendar["revision"]
    assert context["listing"]["firstTradingDate"] == "1999-11-10"


def test_raw_etf_cash_research_uses_verified_calendar_without_listing_fabrication():
    payload = request("2023-01-03", "2023-02-01")
    payload["symbol"] = "510300.SH"
    payload["routeKey"].update(assetType="ETF", adjustment="none")
    context = research.research_market_coverage_context(payload, observed_at=NOW)
    assert context is not None
    days = context["calendar"]["expectedSessionDates"]
    assert len(days) == 17 and "2023-01-13" in days and "2023-01-16" in days and "2023-01-19" in days
    assert context["listing"]["symbol"] == "510300.SH"
    assert context["listing"].get("firstTradingDate") is None


def test_weekend_adjusted_workday_and_holiday_use_source_sessions():
    context = research.research_market_coverage_context(request("2023-09-28", "2023-10-09"), observed_at=NOW)
    assert context["calendar"]["expectedSessionDates"] == ["2023-09-28", "2023-10-09"]


def test_existing_window_identity_stays_exactly_equal(monkeypatch):
    old = market_coverage_context_v3(symbol="600000.SH", market="CN", start="2023-08-23", end="2023-08-29")

    def forbidden(*args):
        raise AssertionError("旧窗口不得读取另一日历")
    monkeypatch.setattr(research, "research_calendar_evidence", forbidden)
    assert research.research_market_coverage_context(request("2023-08-23", "2023-08-29")) == old


@pytest.mark.parametrize("change", ["mode", "target", "provider", "source", "asset", "market", "timeframe", "adjustment"])
def test_other_requests_do_not_gain_calendar_scope(change, monkeypatch):
    payload = request()
    if change == "mode":
        payload.pop("tradabilityMode")
    elif change == "target":
        payload.pop("routeTarget")
    elif change == "provider":
        payload["routeTarget"]["providerId"] = "hithink"
    elif change == "source":
        payload["routeTarget"]["upstreamSource"] = "another"
    elif change == "asset":
        payload["routeKey"]["assetType"] = "ETF"
    elif change == "market":
        payload["routeKey"]["market"] = "HK"
    elif change == "timeframe":
        payload["routeKey"]["timeframe"] = "1m"
    else:
        payload["routeKey"]["adjustment"] = "hfq"

    def forbidden(*args):
        raise AssertionError("非研究路径不得使用现行覆盖")
    monkeypatch.setattr(research, "research_calendar_evidence", forbidden)
    assert research.research_market_coverage_context(payload, observed_at=NOW) is None


def test_missing_listing_stays_unknown_with_actual_calendar():
    payload = request()
    payload["symbol"] = "600519.SH"
    context = research.research_market_coverage_context(payload, observed_at=NOW)
    assert context["listing"] == {"symbol": "600519.SH", "status": "unknown"}
    assert len(context["expectedPostListingSessionDates"]) == 50


@pytest.mark.parametrize("start,end", [
    ("bad", "2023-09-15"), ("2023-09-15", "2023-07-08"),
    ("2023-7-8", "2023-09-15"), ("1900-01-01", "2023-09-15"),
])
def test_invalid_or_excessive_ranges_fail_closed(start, end):
    assert research.research_calendar_evidence(start, end, NOW) is None


@pytest.mark.parametrize("instant", [datetime(2026, 10, 6), datetime(2025, 1, 1, tzinfo=timezone.utc)])
def test_naive_or_before_release_clock_is_unavailable(instant):
    assert research.research_calendar_evidence("2023-07-08", "2023-09-15", instant) is None


@pytest.mark.parametrize("field,value", [
    ("timezone", "UTC"), ("providerRevision", "unverified"),
    ("range", {"start": "2023-08-01", "end": "2023-08-31"}),
    ("availableAt", "2027-01-01T00:00:00Z"), ("availableAt", "2026-03-10T00:00:00"),
    ("sessionOverrides", [{}]), ("holidays", ["bad"]),
])
def test_invalid_source_fact_is_not_projected(field, value, monkeypatch):
    from datetime import date
    fact = deepcopy(research.calendar_fact(date(2023, 7, 8), date(2023, 9, 15), NOW))
    fact[field] = value
    monkeypatch.setattr(research, "calendar_fact", lambda *args: fact)
    assert research.research_calendar_evidence("2023-07-08", "2023-09-15", NOW) is None


def test_verified_package_digest_failure_stays_unavailable(monkeypatch):
    from src.services import thesis_ledger_calendar_release as release
    monkeypatch.setattr(release, "RELEASE_SOURCE_DIGEST", "0" * 64)
    assert research.research_calendar_evidence("2023-07-08", "2023-09-15", NOW) is None


@pytest.mark.parametrize("missing", [False, True])
def test_authenticated_http_uses_extended_calendar_and_preserves_no_bar_assumption(monkeypatch, missing):
    from hashlib import sha256
    import json
    from types import SimpleNamespace
    import pandas as pd
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.thesis_ledger import router_v3
    from data_provider.tencent_native_daily import TENCENT_NATIVE_DAILY_PROTOCOL
    from src.services import thesis_ledger_provider_runtime as runtime

    payload = {"contractVersion": 3, "requestId": "research-calendar-http", **request()}
    days = research.research_market_coverage_context(payload, observed_at=NOW)["calendar"]["expectedSessionDates"]
    observed = days[1:] if missing else days
    frame = pd.DataFrame([{"date": day, "open": 1, "high": 1.2, "low": 0.9, "close": 1.1,
                           "volume": 100, "amount": 110} for day in observed])
    parts = [{"start": payload["start"], "end": payload["end"], "rows": len(frame), "sha256": "a" * 64}]
    frame.attrs.update({
        "has_more_before": False,
        "tencentDailyRetrieval": {"endpoint": "newfqkline/get", "adjustment": "qfq", "partitionComplete": True,
                                  "partitions": parts,
                                  "revision": sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()},
        "thesis_ledger_v3_pagination": {
            "status": "complete", "pagesFetched": 1, "continuationPending": False,
            "protocol": TENCENT_NATIVE_DAILY_PROTOCOL, "maximumRows": 3650,
            "requestedStart": payload["start"], "requestedEnd": payload["end"],
        },
    })
    calls = []

    class Provider:
        def execute_market_bars_v3(self, *args, **kwargs):
            calls.append(args[0].symbol)
            return SimpleNamespace(value=frame, provider="tencent", upstream_source="tencent", route_index=0,
                                   effective_revision=3, provider_revision="tencent-native-v1")

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "local-test-token")
    monkeypatch.setattr(runtime, "get_thesis_ledger_runtime", lambda: Provider())
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    client = TestClient(app)
    assert client.post("/api/v3/thesis-ledger/market/bars", json=payload).status_code == 401
    assert calls == []
    response = client.post("/api/v3/thesis-ledger/market/bars", json=payload,
                           headers={"Authorization": "Bearer local-test-token"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["bars"]) == len(observed)
    assert result["coverageProof"]["calendar"]["expectedSessionDates"] == days
    state = result["historicalTradabilityWindows"][0]["days"][0]["state"]
    assert state == ("assumed-untradable-no-bar" if missing else "observed-traded")
    payload["symbol"] = "600519.SH"
    unknown = client.post("/api/v3/thesis-ledger/market/bars", json=payload,
                          headers={"Authorization": "Bearer local-test-token"})
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()["coverageProof"]["listing"] == {"symbol": "600519.SH", "status": "unknown"}
    assert unknown.json()["historicalTradabilityWindows"][0]["listing"] == {"status": "unknown"}
    assert calls == ["600000.SH", "600519.SH"]
