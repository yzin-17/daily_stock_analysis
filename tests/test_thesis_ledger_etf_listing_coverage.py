"""核验 ETF 上市事实补齐后，现行完整窗口门禁仍按真实事实判断。"""

import pytest

from api.thesis_ledger import _market_data_v3_request
from src.services.thesis_ledger_market_v3_facts import (
    market_calendar_evidence_v3,
    market_coverage_context_v3,
    market_listing_fact_v3,
)
from src.services.thesis_ledger_research_calendar import research_market_coverage_context


def test_510300_listing_is_bound_to_disclosed_identity_and_source():
    fact = market_listing_fact_v3("510300.SH")
    assert fact["symbol"] == "510300.SH"
    assert fact["firstTradingDate"] == "2012-05-28"
    assert fact["source"] == "https://www.hkexnews.hk/listedco/listconews/sehk/2025/0804/2025080402003_c.pdf"
    assert fact["knownAt"] == "2025-08-04T00:00:00+08:00"
    assert market_listing_fact_v3("510300.HK") is None
    assert market_listing_fact_v3("510050.SH") is None


@pytest.mark.parametrize("provider,source,index", [
    ("hithink", "fund-market-historical", 0),
    ("akshare", "eastmoney", 1),
])
def test_current_qfq_window_has_strict_coverage_before_upstream_read(provider, source, index):
    request = _market_data_v3_request({
        "contractVersion": 3, "requestId": "listing-coverage-check", "symbol": "510300.SH",
        "routeKey": {"kind": "bar", "market": "CN", "assetType": "ETF", "capability": "DAILY_BAR",
                     "timeframe": "1d", "adjustment": "qfq"},
        "routeTarget": {"providerId": provider, "upstreamSource": source, "routeIndex": index},
        "start": "2026-09-24", "end": "2026-11-10",
    }, "listing-coverage-check")
    context = research_market_coverage_context(request)
    assert context is not None
    assert context["listing"]["firstTradingDate"] == "2012-05-28"
    assert context["calendar"] == market_calendar_evidence_v3("CN", request["start"], request["end"])
    assert context["window"] == {"status": "complete", "requestedStart": request["start"], "requestedEnd": request["end"]}
    assert context["expectedPostListingSessionDates"][0] == "2026-09-24"
    assert context["expectedPostListingSessionDates"][-1] == "2026-11-10"
    assert "tradabilityMode" not in request


def test_known_listing_does_not_grant_missing_calendar_coverage():
    assert market_coverage_context_v3(symbol="510300.SH", market="CN", start="2025-01-01", end="2025-01-31") is None


def test_window_before_listing_is_rejected_before_calendar_read():
    def forbidden(*args):
        raise AssertionError("上市前窗口不应继续读取日历")
    assert market_coverage_context_v3(
        symbol="510300.SH", market="CN", start="2012-05-01", end="2012-05-27",
        calendar_evidence_provider=forbidden,
    ) is None
