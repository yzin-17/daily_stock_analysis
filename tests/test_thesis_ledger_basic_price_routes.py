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
    assert not basic_market_price_route({**key, "assetType": "STOCK"}, target)
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


def test_other_price_source_still_uses_its_existing_admission():
    request = SimpleNamespace(symbol="159516.SZ", start="2026-01-01", end="2026-10-01")
    target = {"providerId": "akshare", "upstreamSource": "eastmoney"}
    assert price_route_admission(lambda *args, **kwargs: None, request, KEY, target) == (
        None, "NO_ELIGIBLE_PROVIDER",
    )
