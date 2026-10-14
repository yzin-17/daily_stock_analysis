"""首个停复牌标的的独立上市事实与交易所日历回归。"""

from src.services.thesis_ledger_market_v3_facts import (
    market_coverage_context_v3,
    market_listing_fact_v3,
)


def test_huangtai_listing_binds_issuer_report_and_conservative_document_clock():
    assert market_listing_fact_v3("000995.SZ") == {
        "symbol": "000995.SZ",
        "firstTradingDate": "2000-08-07",
        "source": "https://static.cninfo.com.cn/finalpage/2021-04-28/1209836916.PDF",
        "revision": "huangtai-2020-annual-report-1209836916",
        "knownAt": "2021-04-29T00:00:00+08:00",
    }
    assert market_listing_fact_v3("000996.SZ") is None


def test_independent_calendar_keeps_exchange_session_on_suspended_stock_day():
    context = market_coverage_context_v3(
        symbol="000995.SZ", market="CN", start="2023-08-16", end="2023-08-21",
    )
    assert context is not None
    assert context["listing"]["firstTradingDate"] == "2000-08-07"
    assert context["expectedPostListingSessionDates"] == [
        "2023-08-16", "2023-08-17", "2023-08-18", "2023-08-21",
    ]
    assert context["calendar"]["supportedRange"] == {
        "start": "2023-08-01", "end": "2023-08-31",
    }
    assert context["window"]["status"] == "complete"
    assert market_coverage_context_v3(
        symbol="000995.SZ", market="CN", start="2023-07-31", end="2023-08-21",
    ) is None
