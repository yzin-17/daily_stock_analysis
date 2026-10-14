"""港美股交易日坐标、DST、早收市与当前观察时钟；旧CN表示不变。"""
from datetime import datetime
from api.thesis_ledger_market_daily_points import MarketDailyPointSource


def source(market, start, end, at):
    return MarketDailyPointSource({"routeKey": {"market": market}, "start": start, "end": end}, at,
        lambda value: f"legacy:{value}", lambda value, **kwargs: {"legacy": value, **kwargs})


def test_us_date_only_is_not_previous_new_york_date_and_has_dst_offset():
    read = source("US", "2024-05-10", "2024-05-24", "2026-10-07T10:00:00+00:00")
    assert read.timestamp("2024-05-10") == "2024-05-10T00:00:00-04:00"
    point = read.point({"timestamp": read.timestamp("2024-05-10"), "close": 17.46})
    assert point["completionStatus"] == "complete" and point["availableAt"] == read.fetched_at
    winter = source("US", "2024-01-02", "2024-01-03", "2026-10-07T10:00:00+00:00")
    assert winter.timestamp("2024-01-02").endswith("-05:00")


def test_hk_date_stays_local_and_us_early_close_drives_completion():
    hk = source("HK", "2025-08-06", "2025-08-11", "2026-10-07T10:00:00+00:00")
    assert hk.timestamp("2025-08-06") == "2025-08-06T00:00:00+08:00"
    early = source("US", "2025-11-28", "2025-11-28", "2026-10-07T10:00:00+00:00")
    # 已核验发布时钟之后，固定历史会话仍使用该日实际13:00早收市。
    early.observed_at = datetime.fromisoformat("2025-11-28T18:00:00+00:00")
    assert early.point({"timestamp": early.timestamp("2025-11-28")})["completionStatus"] == "complete"
    early.observed_at = datetime.fromisoformat("2025-11-28T17:59:59+00:00")
    assert early.point({"timestamp": early.timestamp("2025-11-28")})["completionStatus"] == "incomplete"


def test_cn_delegates_existing_date_and_point_without_changing_identity():
    read = source("CN", "2023-01-03", "2023-02-01", "2026-10-07T10:00:00+00:00")
    assert read.timestamp("2023-01-03") == "legacy:2023-01-03"
    assert read.point({"timestamp": "old"}) == {"legacy": {"timestamp": "old"},
        "fetched_at": read.fetched_at, "observed_availability": True}
