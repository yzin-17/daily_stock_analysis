"""核对精确东财适配的口径、窗口、来源和失败边界，不请求网络。"""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from data_provider import akshare_fetcher as module


@pytest.mark.parametrize("symbol,endpoint", [
    ("159516", "fund_etf_hist_em"),
    ("000001", "stock_zh_a_hist"),
])
@pytest.mark.parametrize("adjustment,native", [("none", ""), ("qfq", "qfq"), ("hfq", "hfq")])
def test_exact_eastmoney_request_and_native_values(monkeypatch, symbol, endpoint, adjustment, native):
    raw = pd.DataFrame({
        "日期": ["2026-05-18", "2026-05-19"],
        "开盘": [1.1, 1.2], "最高": [1.2, 1.3], "最低": [1.0, 1.1],
        "收盘": [1.15, 1.25], "成交量": [123, 456], "成交额": [789.5, 987.5],
    })
    etf = Mock(return_value=raw.copy())
    stock = Mock(return_value=raw.copy())
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(
        fund_etf_hist_em=etf, stock_zh_a_hist=stock,
    ))
    # 仅替换进程隔离；保留实际 endpoint 分派、标准化和指标处理。
    monkeypatch.setattr(module, "_akshare_call_with_timeout",
                        lambda fn, *args, **kwargs: fn(*args))
    fetcher = object.__new__(module.AkshareFetcher)
    result = fetcher.get_daily_data_for_source(
        symbol, "eastmoney", start_date="2026-05-18", end_date="2026-05-19",
        adjustment=adjustment,
    )
    selected, other = (etf, stock) if endpoint == "fund_etf_hist_em" else (stock, etf)
    selected.assert_called_once_with(
        symbol=symbol, period="daily", start_date="20260518", end_date="20260519", adjust=native,
    )
    other.assert_not_called()
    assert result.attrs["upstream_source"] == "eastmoney"
    contract = result.attrs["native_daily_contract"]
    assert contract["endpoint"] == endpoint
    assert contract["adjustment"] == adjustment
    assert contract["nativeAdjust"] == native
    assert contract["independentSourceId"] == "eastmoney"
    assert contract["volumeUnit"] == ("hand" if endpoint == "stock_zh_a_hist" else "unknown")
    assert contract["amountUnit"] == ("CNY" if endpoint == "stock_zh_a_hist" else "unknown")
    assert contract["volumeAdjustment"] == "unknown"
    assert contract["valuesConverted"] is False
    assert result["close"].tolist() == [1.15, 1.25]
    # 精确适配保留原生数值，显式元数据不等于已执行换算或通过来源准入。
    assert result["volume"].tolist() == [123, 456]
    assert result["amount"].tolist() == [789.5, 987.5]


@pytest.mark.parametrize("symbol", ["159516", "000001"])
def test_exact_eastmoney_failure_does_not_call_another_source(monkeypatch, symbol):
    failure = TimeoutError("fixture timeout")
    call = Mock(side_effect=failure)
    monkeypatch.setattr(module, "_akshare_call_with_timeout", call)
    fetcher = object.__new__(module.AkshareFetcher)
    with pytest.raises(TimeoutError, match="fixture timeout"):
        fetcher.get_daily_data_for_source(
            symbol, "eastmoney", start_date="2026-05-18", end_date="2026-05-19",
            adjustment="hfq",
        )
    assert call.call_count == 1


def test_rate_limited_exact_source_does_not_poison_explicit_sibling(monkeypatch):
    eastmoney = Mock(side_effect=module.RateLimitError('synthetic rate limit'))
    tencent = Mock(return_value=pd.DataFrame({
        'date': ['2026-05-18', '2026-05-19'],
        'open': [1.1, 1.2], 'high': [1.2, 1.3], 'low': [1., 1.1],
        'close': [1.15, 1.25], 'volume': [100, 200], 'amount': [115., 250.],
    }))
    monkeypatch.setitem(sys.modules, 'akshare', SimpleNamespace(
        stock_zh_a_hist=eastmoney, stock_zh_a_hist_tx=tencent,
    ))
    monkeypatch.setattr(module, '_akshare_call_with_timeout',
                        lambda fn, *args, **kwargs: fn(*args))
    fetcher = object.__new__(module.AkshareFetcher)
    options = {'start_date': '2026-05-18', 'end_date': '2026-05-19', 'adjustment': 'qfq'}
    with pytest.raises(module.RateLimitError):
        fetcher.get_daily_data_for_source('000001', 'eastmoney', **options)
    tencent.assert_not_called()
    result = fetcher.get_daily_data_for_source('000001', 'tencent', **options)
    eastmoney.assert_called_once()
    tencent.assert_called_once_with(symbol='sz000001', start_date='20260518',
                                    end_date='20260519', adjust='qfq')
    assert result.attrs['upstream_source'] == 'tencent'
    assert result['close'].tolist() == [1.15, 1.25]
