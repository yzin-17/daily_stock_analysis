import sys
from types import SimpleNamespace

import pandas as pd

from data_provider import efinance_fetcher as module
from data_provider.efinance_fetcher import EfinanceFetcher


def make_fetcher():
    fetcher = EfinanceFetcher(sleep_min=0, sleep_max=0)
    fetcher._set_random_user_agent = lambda: None
    fetcher._enforce_rate_limit = lambda: None
    return fetcher


def install_snapshot(monkeypatch, frame, *, now=1_800_000_000.0):
    monkeypatch.setattr(
        module,
        "_realtime_cache",
        {"data": None, "timestamp": 0, "ttl": 600},
    )
    monkeypatch.setattr(module.time, "time", lambda: now)
    monkeypatch.setattr(module.time, "monotonic", lambda: 10.0)
    api = lambda *_args, **_kwargs: frame
    monkeypatch.setitem(
        sys.modules,
        "efinance",
        SimpleNamespace(stock=SimpleNamespace(get_realtime_quotes=api)),
    )
    monkeypatch.setattr(
        module,
        "_ef_call_with_timeout",
        lambda fn, *args, **kwargs: fn(*args, **kwargs),
    )


def test_market_breadth_keeps_eastmoney_source_and_local_observation(monkeypatch):
    install_snapshot(
        monkeypatch,
        pd.DataFrame(
            {
                "股票代码": ["600000", "000001"],
                "股票名称": ["浦发银行", "平安银行"],
                "最新价": [11.0, 9.0],
                "昨收": [10.0, 10.0],
                "成交额": [100_000_000.0, 200_000_000.0],
            }
        ),
    )

    stats = make_fetcher().get_market_stats()

    assert stats["up_count"] == 1
    assert stats["down_count"] == 1
    assert stats["source"] == "efinance/eastmoney:get_realtime_quotes(stock)"
    assert stats["contract"] == "cn-stock-breadth-v1"
    assert stats["source_available_at"] is None
    assert stats["historical_visibility_verified"] is False
    assert stats["observed_at"].endswith("+00:00")


def test_market_breadth_rejects_duplicate_stock_identity(monkeypatch):
    install_snapshot(
        monkeypatch,
        pd.DataFrame(
            {
                "股票代码": ["600000", "600000"],
                "股票名称": ["浦发银行", "浦发银行"],
                "最新价": [11.0, 11.0],
                "昨收": [10.0, 10.0],
                "成交额": [100_000_000.0, 100_000_000.0],
            }
        ),
    )

    assert make_fetcher().get_market_stats() is None


def test_market_breadth_missing_identity_column_fails_closed(monkeypatch):
    install_snapshot(
        monkeypatch,
        pd.DataFrame(
            {
                "股票名称": ["浦发银行"],
                "最新价": [11.0],
                "昨收": [10.0],
                "成交额": [100_000_000.0],
            }
        ),
    )

    assert make_fetcher().get_market_stats() is None
