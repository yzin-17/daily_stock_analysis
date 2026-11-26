"""富途分钟精度、完整分页和拒绝路径；不请求真实 OpenD。"""

from datetime import datetime, timezone
import json
import subprocess

import pandas as pd
import pytest

from data_provider.exact_daily_contract import ExactDailyError
from data_provider.futu_market import FutuMarketAdapter, FutuMarketError
from data_provider.futu_minute import normalize_futu_minute, read_futu_minute
from tests.test_futu_daily import sdk_for


def request(**changes):
    return {"symbol": "00700.HK", "instrumentType": "STOCK", "adjustment": "none",
            "start": "2026-05-18", "end": "2026-05-18", **changes}


def row(stamp="2026-05-18 09:30:00", **changes):
    return {"code": "HK.00700", "time_key": stamp, "open": 10., "high": 12.,
            "low": 9., "close": 11., "volume": 0, "turnover": None, **changes}


def sdk(pages):
    value, ctx = sdk_for(pages)
    value.KLType.K_1M = "K_1M"
    return value, ctx


def test_all_pages_preserve_same_day_minutes_and_missing_metrics():
    value, ctx = sdk([(0, pd.DataFrame([row()]), b"next"),
                      (0, pd.DataFrame([row("2026-05-18 09:31:00", volume=None)]), None)])
    frame = read_futu_minute(value, "localhost", 11111, request())
    assert frame["timestamp"].tolist() == ["2026-05-18T09:30:00+08:00", "2026-05-18T09:31:00+08:00"]
    assert frame["volume"].tolist() == [0, None] and frame["amount"].tolist() == [None, None]
    assert frame.attrs["fieldAvailability"] == {"volume": False, "amount": False}
    assert frame.attrs["session"] == "regular" and frame.attrs["timezone"] == "Asia/Hong_Kong"
    assert frame.attrs["has_more_before"] is False and len(frame.attrs["responseSha256"]) == 64
    assert json.loads(json.dumps(frame.to_dict(orient="records"), allow_nan=False))[0]["volume"] == 0
    calls = ctx.request_history_kline.call_args_list
    assert len(calls) == 2 and calls[0].kwargs["page_req_key"] is None and calls[1].kwargs["page_req_key"] == b"next"
    assert frame.attrs["pagination"] == {"requestedStart": "2026-05-18", "requestedEnd": "2026-05-18",
                                         "pagesFetched": 2, "continuationPending": False}
    assert all(c.kwargs["ktype"] == "K_1M" and c.kwargs["autype"] == "NONE"
               and c.kwargs["extended_time"] is False for c in calls)
    ctx.close.assert_called_once()


@pytest.mark.parametrize("symbol,asset,code,zone", [
    ("00700.HK", "STOCK", "HK.00700", "+08:00"), ("02800.HK", "ETF", "HK.02800", "+08:00"),
    ("AAPL.US", "STOCK", "US.AAPL", "-04:00"), ("SPY.US", "ETF", "US.SPY", "-04:00"),
])
def test_market_asset_identity_and_timezone(symbol, asset, code, zone):
    value, ctx = sdk([(0, pd.DataFrame([row(code=code)]), None)])
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{"code": code, "stock_type": asset}])
    frame = read_futu_minute(value, "localhost", 11111, request(symbol=symbol, instrumentType=asset))
    assert frame.iloc[0]["timestamp"].endswith(zone)
    assert ctx.request_history_kline.call_args.args == (code,)


@pytest.mark.parametrize("stamp,offset", [("2026-01-12 09:30:00", "-05:00"), ("2026-07-13 09:30:00", "-04:00")])
def test_new_york_winter_and_summer_offsets(stamp, offset):
    frame = normalize_futu_minute(pd.DataFrame([row(stamp)]), "America/New_York")
    assert frame.iloc[0]["timestamp"].endswith(offset)


@pytest.mark.parametrize("changes", [
    {"time_key": "2026-05-18"}, {"time_key": "2026-05-18 09:30:01"},
    {"open": None}, {"close": float("inf")}, {"low": -1}, {"high": 10},
    {"open": True}, {"volume": -1}, {"turnover": float("inf")}, {"close": "bad"},
])
def test_bad_prices_times_and_metrics_fail_closed(changes):
    value, ctx = sdk([(0, pd.DataFrame([row(**changes)]), None)])
    with pytest.raises(ExactDailyError) as error:
        read_futu_minute(value, "localhost", 11111, request())
    assert error.value.code == "invalid_response"
    ctx.close.assert_called_once()


@pytest.mark.parametrize("second", [
    (-1, "permission denied", None), (0, pd.DataFrame(), None),
    (0, pd.DataFrame([row()]), None),
    (0, pd.DataFrame([row("2026-05-18 09:31:00")]), b"next"),
    (0, pd.DataFrame([row("2026-05-18 09:31:00")]), "bad"),
    (0, pd.DataFrame([row("2026-05-18 09:31:00", code="HK.00147")]), None),
    (0, pd.DataFrame([row("2026-05-19 09:31:00")]), None),
])
def test_later_page_failure_never_returns_a_prefix(second):
    value, ctx = sdk([(0, pd.DataFrame([row()]), b"next"), second])
    with pytest.raises(ExactDailyError):
        read_futu_minute(value, "localhost", 11111, request())
    ctx.close.assert_called_once()


@pytest.mark.parametrize("changes", [
    {"adjustment": "qfq"}, {"adjustment": "hfq"}, {"instrumentType": "INDEX"},
    {"symbol": "600519.BJ"}, {"start": "bad"}, {"end": "2026-05-17"},
])
def test_invalid_minute_request_never_connects(changes):
    value, ctx = sdk([])
    with pytest.raises((ExactDailyError, FutuMarketError)):
        read_futu_minute(value, "localhost", 11111, request(**changes))
    value.OpenQuoteContext.assert_not_called()


def test_minute_completion_uses_minute_end_and_observation():
    raw = pd.DataFrame([row(), row("2026-05-18 09:31:00")])
    frame = normalize_futu_minute(raw, "Asia/Hong_Kong",
        observed_at=datetime(2026, 5, 18, 1, 31, 0, tzinfo=timezone.utc))
    assert frame["complete"].tolist() == [True, False]


@pytest.mark.parametrize("period", ["1m", "5m", "15m", "30m", "60m"])
def test_cn_period_close_is_available_at_1130_without_lunch_shift(period):
    raw = pd.DataFrame([row("2026-05-18 11:30:00"), row("2026-05-18 15:00:00")])
    at = datetime(2026, 5, 18, 3, 30, tzinfo=timezone.utc)
    before = normalize_futu_minute(raw, "Asia/Shanghai", timeframe=period,
        observed_at=at.replace(minute=29, second=59))
    closed = normalize_futu_minute(raw, "Asia/Shanghai", timeframe=period, observed_at=at)
    assert before["complete"].tolist() == [False, False]
    assert closed["complete"].tolist() == [True, False]
    assert closed.iloc[0]["timestamp"] == "2026-05-18T11:30:00+08:00"
    assert closed.attrs["timestampConvention"] == "interval-end"


@pytest.mark.parametrize("zone", ["Asia/Hong_Kong", "America/New_York"])
def test_other_markets_keep_period_start_completion(zone):
    from zoneinfo import ZoneInfo
    at = datetime(2026, 5, 18, 9, 35, tzinfo=ZoneInfo(zone))
    frame = normalize_futu_minute(pd.DataFrame([row(), row("2026-05-18 09:31:00")]),
        zone, timeframe="5m", observed_at=at)
    assert frame.attrs["timestampConvention"] == "interval-start"
    assert frame["complete"].tolist() == [True, False]


def test_page_ceiling_remains_bounded():
    value, ctx = sdk([(0, pd.DataFrame([row()]), str(i).encode()) for i in range(64)])
    with pytest.raises(ExactDailyError) as error:
        read_futu_minute(value, "localhost", 11111, request())
    assert error.value.code == "insufficient_coverage" and ctx.request_history_kline.call_count == 64
    ctx.close.assert_called_once()


def test_existing_worker_boundary_terminates_slow_minute_queries(monkeypatch):
    adapter = FutuMarketAdapter.__new__(FutuMarketAdapter)
    adapter.host, adapter.port = "localhost", 11111
    calls = []
    def run(*args, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(FutuMarketError) as error:
        adapter._query({"operation": "minute", **request()})
    assert error.value.code == "timeout" and calls[0]["timeout"] == 4.5
