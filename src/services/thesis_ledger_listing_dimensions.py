"""上市阶段与明确无日涨跌幅事实；与全天停牌保持独立。"""

from typing import Mapping

LISTING_PHASES = {"notListed", "initialListing", "listed", "delistingPeriod", "delisted"}


def validate_listing_dimensions(item: Mapping) -> None:
    phase = item.get("listingPhase")
    unlimited = item.get("noDailyPriceLimit")
    if phase is not None and (not isinstance(phase, str) or phase not in LISTING_PHASES):
        raise ValueError("上市阶段无效")
    if phase in {"notListed", "delisted"} and item["state"] == "trading":
        raise ValueError("未上市或已摘牌不能声明交易所可交易")
    if unlimited is not None:
        if not isinstance(unlimited, dict) or set(unlimited) != {"reason"}:
            raise ValueError("无日涨跌幅限制的维度无效")
        if not isinstance(unlimited["reason"], str) or not unlimited["reason"].strip():
            raise ValueError("无日涨跌幅限制必须说明准确来源规则")
        if item.get("dailyPriceLimit") is not None:
            raise ValueError("日价格限制与无日限制不能同时声明")
