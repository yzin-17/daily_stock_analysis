"""efinance 实时行情归一化的确定性回归。"""

import sys
import types

import pandas as pd

import data_provider.efinance_fetcher as efinance_module
from data_provider.efinance_fetcher import EfinanceFetcher


def _fetcher() -> EfinanceFetcher:
    """建立不等待限流间隔的 efinance fetcher。"""
    return EfinanceFetcher(sleep_min=0, sleep_max=0)


def test_snapshot_dataframe_maps_normal_fields() -> None:
    """DataFrame snapshot 应映射中文字段并保留完整数值。"""
    snapshot = pd.DataFrame(
        [
            {
                "代码": "600519",
                "名称": "贵州茅台",
                "最新价": 1800.5,
                "涨跌幅": 1.2,
                "涨跌额": 21.4,
                "成交量": 1234,
                "成交额": 987654.0,
                "换手率": 0.8,
                "振幅": 2.1,
                "最高": 1810.0,
                "最低": 1778.0,
                "今开": 1780.0,
                "昨收": 1779.1,
                "量比": 1.5,
                "市盈率": 25.0,
                "总市值": 2_000_000.0,
                "流通市值": 1_800_000.0,
            }
        ]
    )

    quote = EfinanceFetcher._quote_from_snapshot(snapshot, "600519")

    assert quote is not None
    assert quote.code == "600519"
    assert quote.name == "贵州茅台"
    assert quote.price == 1800.5
    assert quote.change_pct == 1.2
    assert quote.volume == 1234
    assert quote.open_price == 1780.0
    assert quote.pre_close == 1779.1
    assert quote.total_mv == 2_000_000.0


def test_snapshot_series_maps_english_aliases() -> None:
    """Series snapshot 应复用同一归一化逻辑并支持英文列名。"""
    snapshot = pd.Series(
        {
            "code": "600519",
            "name": "Kweichow Moutai",
            "price": "1800.5",
            "pct_chg": "1.2",
            "change": "21.4",
            "volume": "1234",
            "amount": "987654",
            "high": "1810",
            "low": "1778",
            "open": "1780",
        }
    )

    quote = EfinanceFetcher._quote_from_snapshot(snapshot, "600519")

    assert quote is not None
    assert quote.name == "Kweichow Moutai"
    assert quote.price == 1800.5
    assert quote.change_amount == 21.4
    assert quote.volume == 1234
    assert quote.high == 1810.0


def test_snapshot_rejects_code_mismatch() -> None:
    """snapshot 返回其他标的时不能误归一化为请求标的。"""
    snapshot = pd.DataFrame([{"代码": "000001", "名称": "平安银行", "最新价": 12.3}])

    assert EfinanceFetcher._quote_from_snapshot(snapshot, "600519") is None


def test_snapshot_rejects_missing_price() -> None:
    """缺少最新价时必须返回 None，让上层继续既定 fallback。"""
    snapshot = pd.DataFrame([{"代码": "600519", "名称": "贵州茅台"}])

    assert EfinanceFetcher._quote_from_snapshot(snapshot, "600519") is None


def test_snapshot_skips_pandas_missing_scalars() -> None:
    """pd.NA/NaN 应视为缺失并继续查找别名，不触发真假值歧义。"""
    snapshot = pd.Series(
        {
            "代码": pd.NA,
            "code": "600519",
            "名称": pd.NA,
            "name": "贵州茅台",
            "最新价": pd.NA,
            "price": 1800.5,
        }
    )

    quote = EfinanceFetcher._quote_from_snapshot(snapshot, "600519")

    assert quote is not None
    assert quote.code == "600519"
    assert quote.name == "贵州茅台"
    assert quote.price == 1800.5
    assert (
        EfinanceFetcher._quote_from_snapshot(
            pd.Series({"code": "600519", "price": float("nan")}), "600519"
        )
        is None
    )


def test_full_market_path_reuses_row_normalizer(monkeypatch) -> None:
    """全市场接口应复用 snapshot 使用的 row→quote 归一化函数。"""
    frame = pd.DataFrame(
        [{"股票代码": "600519", "股票名称": "贵州茅台", "最新价": 1800.5}]
    )
    monkeypatch.setattr(
        efinance_module,
        "_realtime_cache",
        {"data": frame, "timestamp": efinance_module.time.time(), "ttl": 600},
    )
    monkeypatch.setitem(
        sys.modules,
        "efinance",
        types.SimpleNamespace(stock=types.SimpleNamespace()),
    )
    fetcher = _fetcher()
    monkeypatch.setattr(fetcher, "_get_realtime_snapshot_quote", lambda _symbol: None)

    quote = fetcher.get_realtime_quote("600519")

    assert quote is not None
    assert quote.name == "贵州茅台"
    assert quote.price == 1800.5
