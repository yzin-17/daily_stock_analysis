"""原研究日历范围按精确绑定扩展，不新增研究语义。"""

from dataclasses import replace
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from data_provider.thesis_ledger_sources.tencent import SOURCE
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services import thesis_ledger_research_calendar as research


def request(provider='tencent', source='tencent', market='CN', asset='STOCK', adjustment='qfq', symbol='600519.SH'):
    return {'symbol': symbol, 'start': '2025-08-06', 'end': '2025-08-08',
            'tradabilityMode': 'assume-untradable-no-bar',
            'routeKey': {'kind': 'bar', 'market': market, 'assetType': asset, 'capability': 'DAILY_BAR',
                         'timeframe': '1d', 'adjustment': adjustment},
            'routeTarget': {'providerId': provider, 'upstreamSource': source, 'routeIndex': 0}}


@pytest.mark.parametrize(('provider', 'source', 'asset', 'adjustment', 'expected'), [
    ('tencent', 'tencent', 'STOCK', 'none', True), ('tencent', 'tencent', 'STOCK', 'qfq', True),
    ('tencent', 'tencent', 'ETF', 'none', True), ('tencent', 'tencent', 'ETF', 'qfq', True),
    ('tencent', 'tencent', 'ETF', 'hfq', False),
    ('hithink', 'hithink-financial-api', 'STOCK', 'none', True),
    ('hithink', 'hithink-financial-api', 'STOCK', 'qfq', True),
    ('hithink', 'hithink-financial-api', 'STOCK', 'hfq', False),
    ('hithink', 'fund-market-historical', 'ETF', 'qfq', False),
    ('baostock', 'baostock', 'STOCK', 'qfq', False),
])
def test_cn_original_scope(provider, source, asset, adjustment, expected):
    symbol = '510300.SH' if asset == 'ETF' else '600519.SH'
    assert research._research_request(request(provider, source, asset=asset, adjustment=adjustment, symbol=symbol)) is expected


@pytest.mark.parametrize('symbol', ['AAPL.US', 'bad', '600519.HK'])
def test_cn_invalid_identity_remains_rejected(symbol):
    assert not research._research_request(request(symbol=symbol))


def test_yahoo_normalized_requires_original_identity(monkeypatch):
    monkeypatch.setattr('src.services.thesis_ledger_reference_identity.reference_identity',
                        lambda symbol, market: {'identity': True} if symbol == 'known.HK' else None)
    assert research._research_request(request('yfinance', 'yfinance', 'HK', symbol='known.HK'))
    assert not research._research_request(request('yfinance', 'yfinance', 'HK', symbol='unknown.HK'))
    assert not research._research_request(request('yfinance', 'yfinance', 'HK', asset='ETF', symbol='known.HK'))


@pytest.mark.parametrize(('market', 'module', 'source', 'symbol'), [
    ('US', 'data_provider.yahoo_share_window', 'yahoo-gme-share-window', 'GME.US'),
    ('HK', 'data_provider.yahoo_hk_share_window', 'yahoo-hk-share-window', '00147.HK'),
])
def test_share_window_keeps_scope_and_observation_clock(monkeypatch, market, module, source, symbol):
    import importlib
    reader = importlib.import_module(module)
    observed = datetime.now(timezone.utc) - timedelta(days=1)
    pack = {'symbol': symbol, 'coverage': {'start': '2025-08-06', 'end': '2025-08-08'}}
    monkeypatch.setattr(reader, 'load_share_window', lambda: (pack, None, observed, 'revision'))
    payload = request('yfinance', source, market, adjustment='none', symbol=symbol)
    assert research._research_request(payload)
    assert not research._research_request({**payload, 'start': '2025-08-05'})
    assert not research._research_request({**payload, 'symbol': 'wrong'})
    observed = datetime.now(timezone.utc) + timedelta(days=1)
    assert not research._research_request(payload)


@pytest.mark.parametrize('answer', [True, False, None, 1])
def test_new_source_only_declares_existing_calendar_eligibility(monkeypatch, answer):
    calls = []

    def eligible(context):
        assert context.symbol == '600519.SH' and context.market == 'CN'
        assert context.asset_type == 'STOCK' and context.start == '2025-08-06'
        assert not hasattr(context, 'request') and not hasattr(context, 'adapter')
        calls.append(context)
        return answer

    binding = replace(SOURCE.bindings[0], research_calendar_eligible=eligible)
    source = replace(SOURCE, provider_id='new-calendar-source', bindings=(binding,), quote_source_aliases=())
    registry = CapabilityRegistry((source,))
    monkeypatch.setattr(research, 'CAPABILITY_SOURCES', registry)
    payload = request('new-calendar-source', binding.upstream_source, adjustment='none')
    assert research._research_request(payload) is (answer is True)
    assert len(calls) == 1
    payload['routeKey']['adjustment'] = 'qfq'
    assert not research._research_request(payload) and len(calls) == 1


def test_binding_registration_rejects_non_callable_calendar_validator():
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCE, bindings=(replace(SOURCE.bindings[0], research_calendar_eligible=1),), quote_source_aliases=()),))


def test_absent_protocol_never_invokes_source(monkeypatch):
    def forbidden(*args):
        raise AssertionError('缺原协议不可选择来源')
    monkeypatch.setattr(research, 'CAPABILITY_SOURCES', SimpleNamespace(resolve=forbidden))
    payload = request()
    payload.pop('tradabilityMode')
    assert not research._research_request(payload)
