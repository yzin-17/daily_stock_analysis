"""共同价格能力不依赖逐标的人工准入，扩展能力保持原边界。"""

from types import SimpleNamespace

import pytest

from src.services.thesis_ledger_market_v3_adapters import basic_market_price_route
from src.services.thesis_ledger_price_route_access import price_route_admission


KEY = {"kind": "bar", "market": "CN", "assetType": "ETF", "capability": "DAILY_BAR",
       "timeframe": "1d", "adjustment": "qfq"}


@pytest.mark.parametrize("provider,source,adjustment,expected", [
    ("hithink", "fund-market-historical", "qfq", True),
    ("hithink", "fund-market-historical", "hfq", False),
    ("tencent", "tencent", "none", True),
    ("tencent", "tencent", "qfq", True),
    ("tencent", "tencent", "hfq", True),
    ("akshare", "tencent", "qfq", False),
    ("tencent", "eastmoney", "qfq", False),
])
def test_basic_price_scope_is_exact(provider, source, adjustment, expected):
    key = {**KEY, "adjustment": adjustment}
    target = {"providerId": provider, "upstreamSource": source}
    assert basic_market_price_route(key, target) is expected
    assert not basic_market_price_route({**key, "timeframe": "1m"}, target)


@pytest.mark.parametrize("symbol,start,end", [
    ("159516.SZ", "2026-04-30", "2026-08-09"),
    ("510300.SH", "2025-01-02", "2026-09-30"),
])
def test_basic_prices_do_not_read_manual_symbol_or_window_admission(symbol, start, end):
    def unexpected_read(*args, **kwargs):
        pytest.fail("基础价格读取不应查询人工证据包")

    request = SimpleNamespace(symbol=symbol, start=start, end=end)
    for target in [
        {"providerId": "hithink", "upstreamSource": "fund-market-historical"},
        {"providerId": "tencent", "upstreamSource": "tencent"},
    ]:
        assert price_route_admission(unexpected_read, request, KEY, target) == (None, None)


def test_other_exact_price_source_uses_configuration_without_manual_admission():
    request = SimpleNamespace(symbol="159516.SZ", start="2026-01-01", end="2026-10-01")
    target = {"providerId": "akshare", "upstreamSource": "eastmoney"}
    assert price_route_admission(lambda *args, **kwargs: None, request, KEY, target) == (None, None)


@pytest.mark.parametrize("provider,source,adjustment,market,expected", [
    ("tencent", "tencent", "none", "CN", True),
    ("tencent", "tencent", "qfq", "CN", True),
    ("tencent", "tencent", "hfq", "CN", False),
    ("tencent", "eastmoney", "none", "CN", False),
    ("hithink", "hithink-financial-api", "none", "CN", True),
    ("tencent", "tencent", "none", "HK", False),
])
def test_stock_basic_price_uses_only_exact_supported_adapters(provider, source, adjustment, market, expected):
    key = {**KEY, "assetType": "STOCK", "adjustment": adjustment, "market": market}
    target = {"providerId": provider, "upstreamSource": source}
    assert basic_market_price_route(key, target) is expected
    if expected:
        request = SimpleNamespace(symbol="600000.SH", start="2023-08-23", end="2023-08-29")
        def unexpected_read(*args, **kwargs):
            pytest.fail("已实现的基础股票价格不应读取人工窗口准入")
        assert price_route_admission(unexpected_read, request, key, target) == (None, None)
