"""真实有界港美规则来源与身份/日历边界。"""
from copy import deepcopy
from datetime import datetime, timezone, date
import json

import pytest

from src.services.thesis_ledger_multimarket_rules import SOURCES, BASE, MultimarketRuleProvider
from src.services.thesis_ledger_reference_identity import reference_instrument_fact, validate_instrument_request
from src.services.thesis_ledger_multimarket_calendar import multimarket_calendar_fact
from src.services.thesis_ledger_public_fees import PublicFeeSourceError
from src.services.thesis_ledger_trading_status import TradingStatusSourceError
from src.services.thesis_ledger_dependency_facts import DependencyFactError

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


@pytest.mark.parametrize("market,symbol,start,end", [("HK", "00147.HK", "2025-08-06", "2025-08-11"), ("US", "GME.US", "2024-05-10", "2024-05-24")])
def test_bar_wire_request_and_current_calendar_research_are_consistent(market, symbol, start, end):
    from api.thesis_ledger import _market_data_v3_request
    from src.services.thesis_ledger_research_calendar import research_market_coverage_context
    req = {"contractVersion": 3, "requestId": "p6-bar-wire", "symbol": symbol, "start": start, "end": end,
        "tradabilityMode": "assume-untradable-no-bar",
        "routeKey": {"kind": "bar", "market": market, "assetType": "STOCK", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq"},
        "routeTarget": {"providerId": "yfinance", "upstreamSource": "yfinance", "routeIndex": 0}}
    parsed = _market_data_v3_request(req, "fallback")
    assert parsed["routeKey"] == req["routeKey"]
    context = research_market_coverage_context(parsed, observed_at=NOW)
    assert context["calendar"]["market"] == market
    assert context["listing"].get("firstTradingDate") is None


def request(market, capability):
    identity = next(key for key in SOURCES if key[:2] == (market, capability))
    pack = json.loads((BASE / SOURCES[identity]).read_text())
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "p6-contract-test",
        "routeKey": {"kind": "data", "market": market, "assetType": "STOCK", "capability": capability},
        "routeTarget": {"providerId": identity[2], "upstreamSource": identity[3], "routeIndex": 0},
        "symbol": pack["symbol"], "range": pack["coverage"]}


@pytest.mark.parametrize("market", ["HK", "US"])
def test_partial_fees_do_not_invent_other_zero_charges(market):
    result = MultimarketRuleProvider(clock=lambda: NOW).read(request(market, "HISTORICAL_PUBLIC_FEES"))
    assert all(len(segment["charges"]) == 1 for segment in result["segments"])
    assert "未纳入" in result["provenance"]["coverageDescription"]
    assert all(doc["sha256"] and doc["observedAt"] for doc in result["sourceDocuments"])
    if market == "US":
        assert result["segments"][1]["range"]["start"] == "2024-05-20"
        assert result["provenance"]["chargeDatePolicy"]["rateChangeChargeDate"] == "2024-05-22"


@pytest.mark.parametrize("market,count", [("HK", 1), ("US", 3)])
def test_intraday_facts_do_not_assert_daily_suspension_or_missing_no_halts(market, count):
    result = MultimarketRuleProvider(clock=lambda: NOW).read(request(market, "HISTORICAL_TRADING_STATUS"))
    assert {day["state"] for day in result["days"]} == {"unknown"}
    assert sum(len(day.get("intradayHalts", [])) for day in result["days"]) == count
    assert result["provenance"]["granularity"] == "intraday"
    assert all("sourceDocumentIds" not in day for day in result["days"] if "intradayHalts" not in day)


@pytest.mark.parametrize("market", ["HK", "US"])
@pytest.mark.parametrize("capability", ["HISTORICAL_PUBLIC_FEES", "HISTORICAL_TRADING_STATUS"])
def test_exact_scope_rejects_wrong_market_symbol_window_and_early_clock(market, capability):
    req = request(market, capability)
    error = PublicFeeSourceError if capability == "HISTORICAL_PUBLIC_FEES" else TradingStatusSourceError
    for mutation in ({"symbol": "uncovered"}, {"range": {"start": "2000-01-01", "end": req["range"]["end"]}},
                     {"routeKey": {**req["routeKey"], "market": "CN"}}):
        with pytest.raises(error):
            MultimarketRuleProvider(clock=lambda: NOW).read({**req, **mutation})
    with pytest.raises(error):
        MultimarketRuleProvider(clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)).read(req)


def test_charge_date_trade_date_boundary_is_not_interchangeable(tmp_path):
    req = request("US", "HISTORICAL_PUBLIC_FEES")
    pack = json.loads((BASE / "us-gme-fees.json").read_text())
    pack["chargeDatePolicy"]["effectiveTradeDate"] = "2024-05-22"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(pack))
    with pytest.raises(PublicFeeSourceError):
        MultimarketRuleProvider(path, clock=lambda: NOW).read(req)


def test_exchange_calendar_preserves_holiday_early_close_and_hk_lunch():
    us = multimarket_calendar_fact("US", date(2025, 11, 24), date(2025, 11, 28), NOW)
    assert us["holidays"] == ["2025-11-27"]
    assert us["sessionOverrides"] == [{"date": "2025-11-28", "sessions": [{"startMinute": 570, "endMinute": 780}]}]
    hk = multimarket_calendar_fact("HK", date(2025, 8, 6), date(2025, 8, 11), NOW)
    assert hk["sessions"] == [{"startMinute": 570, "endMinute": 720}, {"startMinute": 780, "endMinute": 960}]
    assert multimarket_calendar_fact("US", date(2024, 5, 10), date(2024, 5, 24), datetime(2025, 1, 1, tzinfo=timezone.utc)) is None


@pytest.mark.parametrize("market,symbol", [("HK", "00147.HK"), ("US", "GME.US")])
def test_identity_keeps_unknown_units_and_raw_request_remains_rejected(market, symbol):
    fact = reference_instrument_fact(symbol, market, NOW)
    assert fact["lotSize"] is None and fact["tickSize"] is None
    assert fact["tradable"] is False
    assert fact["executionRules"]["status"] == "unavailable"
    assert validate_instrument_request(symbol, market, "STOCK", lambda x: x, True, "qfq") == symbol
    for identity_only, adjustment in [(False, "qfq"), (True, "none")]:
        with pytest.raises(DependencyFactError):
            validate_instrument_request(symbol, market, "STOCK", lambda x: x, identity_only, adjustment)
    assert reference_instrument_fact(symbol, market, datetime(2026, 10, 6, tzinfo=timezone.utc)) is None
