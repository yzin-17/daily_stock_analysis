"""来源共用 CN 研究日历语义，普通读取和不支持的精确绑定仍拒绝。"""

from copy import deepcopy

import pytest

from src.services.thesis_ledger_research_calendar import research_market_coverage_context


TARGETS = [
    ('efinance', 'eastmoney', 'STOCK', 'none'),
    ('efinance', 'eastmoney', 'STOCK', 'qfq'), ('efinance', 'eastmoney', 'STOCK', 'hfq'),
    ('efinance', 'eastmoney', 'ETF', 'qfq'), ('efinance', 'eastmoney', 'ETF', 'hfq'),
    ('akshare', 'eastmoney', 'STOCK', 'qfq'), ('akshare', 'eastmoney', 'STOCK', 'hfq'),
    ('akshare', 'sina', 'STOCK', 'qfq'), ('akshare', 'sina', 'STOCK', 'hfq'),
    ('akshare', 'tencent', 'STOCK', 'qfq'), ('akshare', 'tencent', 'STOCK', 'hfq'),
    ('akshare', 'eastmoney', 'ETF', 'qfq'), ('akshare', 'eastmoney', 'ETF', 'hfq'),
    ('baostock', 'baostock', 'STOCK', 'qfq'), ('baostock', 'baostock', 'STOCK', 'hfq'),
    ('baostock', 'baostock', 'ETF', 'qfq'), ('baostock', 'baostock', 'ETF', 'hfq'),
    ('tencent', 'tencent', 'STOCK', 'hfq'),
    ('futu', 'futu', 'STOCK', 'qfq'), ('futu', 'futu', 'STOCK', 'hfq'),
    ('futu', 'futu', 'ETF', 'qfq'), ('futu', 'futu', 'ETF', 'hfq'),
    ('yfinance', 'yfinance', 'STOCK', 'qfq'), ('yfinance', 'yfinance', 'ETF', 'qfq'),
    ('hithink', 'hithink-financial-api', 'STOCK', 'hfq'),
    ('hithink', 'fund-market-historical', 'ETF', 'qfq'), ('tencent', 'tencent', 'ETF', 'hfq'),
]


def request(provider, source, asset, adjustment):
    return {'symbol': '600519.SH' if asset == 'STOCK' else '510050.SH',
            'start': '2026-09-24', 'end': '2026-11-10', 'tradabilityMode': 'assume-untradable-no-bar',
            'routeKey': {'kind': 'bar', 'market': 'CN', 'assetType': asset, 'capability': 'DAILY_BAR',
                         'timeframe': '1d', 'adjustment': adjustment},
            'routeTarget': {'providerId': provider, 'upstreamSource': source, 'routeIndex': 0}}


@pytest.mark.parametrize('target', TARGETS)
def test_registered_adjusted_research_keeps_unknown_listing(target):
    payload = request(*target)
    context = research_market_coverage_context(payload)
    assert context is not None, target
    assert context['listing'] == {'symbol': payload['symbol'], 'status': 'unknown'}
    assert len(context['expectedPostListingSessionDates']) == 28
    assert context['expectedPostListingSessionDates'] == context['calendar']['expectedSessionDates']
    strict = deepcopy(payload)
    strict.pop('tradabilityMode')
    assert research_market_coverage_context(strict) is None


@pytest.mark.parametrize('change', ['source', 'provider', 'asset', 'market', 'adjustment', 'symbol', 'timeframe'])
def test_identity_or_binding_mismatch_does_not_gain_research_scope(change):
    payload = request('efinance', 'eastmoney', 'STOCK', 'qfq')
    if change == 'source':
        payload['routeTarget']['upstreamSource'] = 'sina'
    elif change == 'provider':
        payload['routeTarget']['providerId'] = 'pytdx'
    elif change == 'asset':
        payload['routeKey']['assetType'] = 'ETF'
    elif change == 'market':
        payload['routeKey']['market'] = 'HK'
    elif change == 'adjustment':
        payload['routeKey']['adjustment'] = 'unsupported'
    elif change == 'symbol':
        payload['symbol'] = '600519.HK'
    else:
        payload['routeKey']['timeframe'] = '1m'
    assert research_market_coverage_context(payload) is None


def test_baostock_does_not_gain_beijing_scope():
    payload = request('baostock', 'baostock', 'STOCK', 'qfq')
    payload['symbol'] = '920000.BJ'
    assert research_market_coverage_context(payload) is None
