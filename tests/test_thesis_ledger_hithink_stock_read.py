"""股票价格研究使用现行日历，来源缺行与完整覆盖要求分别验证。"""
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router_v3
from src.services import thesis_ledger_hithink_stock as stock
from src.services import thesis_ledger_hithink_stock_read as reader
from src.services import thesis_ledger_provider_runtime as runtime
from src.services.thesis_ledger_research_calendar import research_market_coverage_context
from src.services.thesis_ledger_market_v3_adapters import HITHINK_STOCK_SINGLE_RESPONSE_PROTOCOL_V1


def payload():
    return {"contractVersion": 3, "requestId": "stock-research-test", "symbol": "300757.SZ",
            "start": "2023-08-21", "end": "2023-09-05", "tradabilityMode": "assume-untradable-no-bar",
            "routeKey": {"kind": "bar", "market": "CN", "assetType": "STOCK", "capability": "DAILY_BAR",
                         "timeframe": "1d", "adjustment": "qfq"},
            "routeTarget": {"providerId": "hithink", "upstreamSource": "hithink-financial-api", "routeIndex": 0}}


def request(research=True):
    p = payload()
    return runtime.ThesisLedgerDataRequest(capability="DAILY_BAR", symbol=p["symbol"], timeframe="1d",
        start=p["start"], end=p["end"], instrument_type="STOCK", adjustment="qfq",
        parameters={"historical_tradability": research})


def credential():
    return SimpleNamespace(provider_id="hithink", source="control", method="api_key", values={"apiKey": "synthetic"})


def fake_source(monkeypatch, dates):
    def ms(day):
        return int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
    data = {"code": 0, "data": {"timestamp": ms("2026-10-09"), "thscode": "300757.SZ", "adjust": "forward",
            "item": [{"date_ms": ms(day), "open_price": "10", "high_price": "11", "low_price": "9",
                      "close_price": "10.5", "volume": 100, "turnover": 1050} for day in dates]}}
    monkeypatch.setattr(stock.requests, "get", lambda *args, **kwargs: SimpleNamespace(status_code=200, json=lambda: data))


@pytest.mark.parametrize("adjustment", ["none", "qfq"])
def test_exact_hithink_stock_research_gets_calendar_with_unknown_listing(adjustment):
    p = payload(); p["routeKey"]["adjustment"] = adjustment
    c = research_market_coverage_context(p)
    assert c["listing"] == {"symbol": "300757.SZ", "status": "unknown"}
    assert c["calendar"]["expectedSessionDates"][0] == "2023-08-21"
    assert c["calendar"]["source"] == "exchange-calendars/XSHG"


@pytest.mark.parametrize("change", ["mode", "ETF", "source", "hfq"])
def test_unselected_paths_do_not_gain_research_scope(change):
    p = payload()
    if change == "mode": p.pop("tradabilityMode")
    elif change == "ETF": p["routeKey"]["assetType"] = "ETF"; p["symbol"] = "510300.SH"
    elif change == "source": p["routeTarget"]["upstreamSource"] = "fund-market-historical"
    else: p["routeKey"]["adjustment"] = "hfq"
    assert research_market_coverage_context(p) is None


def test_adapter_read_allows_missing_rows_only_for_explicit_research(monkeypatch):
    fake_source(monkeypatch, ["2023-08-28", "2023-09-05"])
    frame = reader.read_hithink_stock_bars(request(), credential(), 10)
    assert frame["date"].tolist() == ["2023-08-28", "2023-09-05"]
    assert frame.attrs["thesis_ledger_source"]["symbol"] == "300757.SZ"
    # 原日期窗口已有日历时，普通请求仍拒绝缺行。
    monkeypatch.setattr(reader, "market_calendar_evidence_v3", lambda *args: {
        "expectedSessionDates": ["2023-08-21", "2023-08-28", "2023-09-05"], "revision": "synthetic"})
    with pytest.raises(runtime.ProviderCallError) as error:
        reader.read_hithink_stock_bars(request(False), credential(), 10)
    assert error.value.code == "incomplete_coverage"


def test_unavailable_calendar_does_not_request_source(monkeypatch):
    from src.services import thesis_ledger_research_calendar as calendar
    monkeypatch.setattr(reader, "market_calendar_evidence_v3", lambda *args: None)
    monkeypatch.setattr(calendar, "research_calendar_evidence", lambda *args: None)
    monkeypatch.setattr(stock.requests, "get", lambda *args, **kwargs: pytest.fail("日历缺失不得读取"))
    with pytest.raises(runtime.ProviderCallError) as error:
        reader.read_hithink_stock_bars(request(), credential(), 10)
    assert error.value.code == "not_covered"


@pytest.mark.parametrize("dates,expected", [(["2023-08-28", "2023-09-05"], 200), (["2023-08-26"], 422)])
def test_authenticated_api_uses_real_adapter_and_checks_actual_days(monkeypatch, dates, expected):
    fake_source(monkeypatch, dates)
    class Provider:
        def execute_market_bars_v3(self, req, key, **kwargs):
            frame = reader.read_hithink_stock_bars(req, credential(), 10)
            frame.attrs["thesis_ledger_v3_pagination"] = {
                "status": "complete", "pagesFetched": 1, "continuationPending": False,
                "protocol": HITHINK_STOCK_SINGLE_RESPONSE_PROTOCOL_V1, "maximumRows": None,
                "requestedStart": req.start, "requestedEnd": req.end}
            return SimpleNamespace(value=frame, provider="hithink", upstream_source="hithink-financial-api",
                route_index=0, effective_revision=73, provider_revision="synthetic")
    monkeypatch.setattr(runtime, "get_thesis_ledger_runtime", lambda: Provider())
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic")
    app = FastAPI(); app.include_router(router_v3, prefix="/api/v3")
    client = TestClient(app)
    assert client.post("/api/v3/thesis-ledger/market/bars", json=payload()).status_code == 401
    r = client.post("/api/v3/thesis-ledger/market/bars", json=payload(), headers={"Authorization": "Bearer synthetic"})
    assert r.status_code == expected, r.text
    if expected == 200:
        d = r.json(); assert len(d["bars"]) == len(dates)
        assert d["coverageProof"]["listing"]["status"] == "unknown"
        assert d["historicalTradabilityWindows"][0]["days"][0]["state"] == "assumed-untradable-no-bar"
