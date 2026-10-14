"""上市与摘牌维度的边界验证，不把阶段当作全天交易状态。"""

import pytest

from src.services.thesis_ledger_status_dimensions import validate_status_dimensions


@pytest.mark.parametrize("phase", ["notListed", "initialListing", "listed", "delistingPeriod", "delisted"])
def test_phase_can_be_known_while_daily_trading_status_is_unknown(phase):
    validate_status_dimensions({"state": "unknown", "listingPhase": phase})


def test_explicit_initial_no_daily_limit_is_not_an_omitted_ratio():
    validate_status_dimensions({"state": "unknown", "listingPhase": "initialListing",
                                "noDailyPriceLimit": {"reason": "首五个交易日不设日涨跌幅限制"}})


@pytest.mark.parametrize("day", [
    {"state": "trading", "listingPhase": "notListed"},
    {"state": "trading", "listingPhase": "delisted"},
    {"state": "unknown", "listingPhase": "unknown"},
    {"state": "unknown", "noDailyPriceLimit": {"reason": ""}},
    {"state": "unknown", "noDailyPriceLimit": {"reason": "无日限制"},
     "dailyPriceLimit": {"reference": "previousRawClose", "maxUpRatio": "0.1", "maxDownRatio": "0.1"}},
])
def test_contradictory_or_unknown_facts_fail(day):
    with pytest.raises(ValueError):
        validate_status_dimensions(day)
