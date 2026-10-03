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


def install_efinance(monkeypatch, frame):
    api = lambda *_args, **_kwargs: frame
    monkeypatch.setitem(
        sys.modules,
        "efinance",
        SimpleNamespace(stock=SimpleNamespace(get_realtime_quotes=api)),
    )
    calls = []

    def call_with_timeout(fn, *args, **kwargs):
        calls.append((fn, args, kwargs))
        return fn(*args, **kwargs)

    monkeypatch.setattr(module, "_ef_call_with_timeout", call_with_timeout)
    return api, calls


def test_efinance_sector_rankings_keep_eastmoney_wrapper_identity(monkeypatch):
    api, calls = install_efinance(
        monkeypatch,
        pd.DataFrame({"股票名称": ["银行", "半导体"], "涨跌幅": [-1.0, 2.0]}),
    )
    top, bottom = make_fetcher().get_sector_rankings(1)

    assert calls == [(api, (["行业板块"],), {})]
    assert top == [
        {
            "name": "半导体",
            "change_pct": 2.0,
            "source": "efinance/eastmoney:get_realtime_quotes(行业板块)",
        }
    ]
    assert bottom[0]["source"] == "efinance/eastmoney:get_realtime_quotes(行业板块)"


def test_efinance_sector_rankings_reject_duplicate_identity(monkeypatch):
    install_efinance(
        monkeypatch,
        pd.DataFrame({"股票名称": ["重复行业", "重复行业"], "涨跌幅": [1.0, 2.0]}),
    )
    assert make_fetcher().get_sector_rankings(2) is None


def test_efinance_sector_rankings_preserve_alternate_columns(monkeypatch):
    install_efinance(
        monkeypatch,
        pd.DataFrame({"name": ["行业甲", "行业乙"], "pct_chg": [0.1, -0.2]}),
    )
    top, bottom = make_fetcher().get_sector_rankings(1)
    assert top[0]["name"] == "行业甲"
    assert bottom[0]["name"] == "行业乙"
