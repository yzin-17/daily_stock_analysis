"""零量观察不得冒充可执行行情，坏数据不被过滤掩盖。"""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import pandas as pd
import pytest
from src.services.thesis_ledger_zero_volume_observations import executable_research_frame
from src.services.thesis_ledger_market_daily_tradability import market_daily_tradability


def sample():
    req = {"requestId": "zero-volume", "symbol": "00147.HK", "start": "2025-08-06", "end": "2025-08-08",
        "tradabilityMode": "assume-untradable-no-bar",
        "routeKey": {"kind": "bar", "market": "HK", "assetType": "STOCK", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq"},
        "routeTarget": {"providerId": "yfinance", "upstreamSource": "yfinance", "routeIndex": 0}}
    frame = pd.DataFrame([{ "date": day, "open": .2, "high": .3, "low": .1, "close": .25,
        "volume": 0 if day == "2025-08-07" else 100, "amount": None } for day in ["2025-08-06", "2025-08-07", "2025-08-08"]])
    frame.attrs["responseSha256"] = "a" * 64
    return req, frame


def test_keeps_original_source_observation_hash_and_excludes_price_without_mutating_source():
    req, frame = sample()
    original = frame.copy(deep=True)
    result = executable_research_frame(req, frame)
    assert list(result["date"]) == ["2025-08-06", "2025-08-08"]
    observation = result.attrs["excluded_zero_volume_observations"]["2025-08-07"]
    assert observation == {"open": "0.2", "high": "0.3", "low": "0.1", "close": "0.25", "volume": "0", "sourceResponseSha256": "a" * 64}
    pd.testing.assert_frame_equal(frame, original)
    assert "excluded_zero_volume_observations" not in frame.attrs
    ctx = {"listing": {"symbol": req["symbol"], "status": "unknown"},
        "calendar": {"source": "test-calendar", "revision": "1"}, "expectedPostListingSessionDates": list(frame["date"])}
    evidence = market_daily_tradability(req, SimpleNamespace(value=frame,provider="yfinance",upstream_source="yfinance",provider_revision="test"),
        ctx,list(result["date"]),"2026-10-07T10:00:00Z")[0]
    assert evidence["days"][1] == {"date": "2025-08-07", "state": "assumed-untradable-no-bar", "excludedZeroVolumeObservation": observation}


@pytest.mark.parametrize("mutation", ["zero_price", "negative_price", "nonzero_amount", "bad_hash", "duplicate_date"])
def test_invalid_observation_is_not_hidden_by_exclusion(mutation):
    req, frame = sample()
    if mutation == "zero_price": frame.loc[1, "open"] = 0
    elif mutation == "negative_price": frame.loc[1, "close"] = -1
    elif mutation == "nonzero_amount": frame.loc[1, "amount"] = 1
    elif mutation == "bad_hash": frame.attrs["responseSha256"] = "bad"
    else: frame = pd.concat([frame,frame.iloc[1:2]],ignore_index=True)
    with pytest.raises(ValueError): executable_research_frame(req, frame)


@pytest.mark.parametrize("change", ["mode", "market", "adjustment", "provider"])
def test_unqualified_reads_keep_original_zero_volume_rejection(change):
    req, frame = sample()
    if change == "mode": req.pop("tradabilityMode")
    elif change == "market": req["routeKey"]["market"] = "CN"
    elif change == "adjustment": req["routeKey"]["adjustment"] = "none"
    else: req["routeTarget"]["providerId"] = "other"
    assert executable_research_frame(req, frame) is frame
