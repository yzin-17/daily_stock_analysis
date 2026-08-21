"""efinance 实时行情归一化的确定性回归。"""

import sys
import types

import pandas as pd

import data_provider.efinance_fetcher as efinance_module
from data_provider.efinance_fetcher import EfinanceFetcher
from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote


def _fetcher() -> EfinanceFetcher:
    """建立不等待限流间隔的 efinance fetcher。"""
    return EfinanceFetcher(sleep_min=0, sleep_max=0)


def test_fund_nav_history_normalizes_provider_descending_dates(monkeypatch) -> None:
    """efinance 倒序净值历史应在 adapter 边界归一化为升序。"""
    frame = pd.DataFrame(
        {
            "日期": ["2026-01-02", "2026-01-01"],
            "单位净值": [1.2, 1.1],
        }
    )
    fake_fund = types.SimpleNamespace(get_quote_history=lambda _code: frame)
    monkeypatch.setitem(
        sys.modules,
        "efinance",
        types.SimpleNamespace(fund=fake_fund),
    )

    result = _fetcher().get_fund_nav_history("110022")

    assert list(result["日期"]) == ["2026-01-01", "2026-01-02"]
    assert list(result["单位净值"]) == [1.1, 1.2]


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


def test_etf_prefers_single_symbol_snapshot_over_full_market(monkeypatch) -> None:
    """ETF 应优先使用单标的快照，避免正常 fallback 拉取全量 ETF。"""
    fetcher = _fetcher()
    snapshot_quote = UnifiedRealtimeQuote(
        code="159516",
        name="半导体设备ETF国泰",
        source=RealtimeSource.EFINANCE,
        price=0.729,
        pre_close=0.733,
    )
    full_market = lambda _symbol: (_ for _ in ()).throw(
        AssertionError("不应在单标的快照成功时调用全量 ETF 接口")
    )
    monkeypatch.setattr(fetcher, "_get_realtime_snapshot_quote", lambda _symbol: snapshot_quote)
    monkeypatch.setattr(fetcher, "_get_etf_realtime_quote", full_market)

    quote = fetcher.get_realtime_quote("159516")

    assert quote is snapshot_quote


def test_etf_falls_back_to_full_market_when_snapshot_unavailable(monkeypatch) -> None:
    """单标的快照不可用时，ETF 仍保留全量接口兜底。"""
    fetcher = _fetcher()
    fallback_quote = UnifiedRealtimeQuote(
        code="159516",
        name="半导体设备ETF国泰",
        source=RealtimeSource.EFINANCE,
        price=0.729,
    )
    monkeypatch.setattr(fetcher, "_get_realtime_snapshot_quote", lambda _symbol: None)
    monkeypatch.setattr(fetcher, "_get_etf_realtime_quote", lambda _symbol: fallback_quote)

    quote = fetcher.get_realtime_quote("159516")

    assert quote is fallback_quote
