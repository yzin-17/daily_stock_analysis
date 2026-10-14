"""Yahoo 九目标合同、MultiIndex 身份及原参考窗口隔离。"""

from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.thesis_ledger_sources.yfinance import SOURCE, PROTOCOL
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions


def request():
    return ThesisLedgerDataRequest('DAILY_BAR', 'AAPL.US', instrument_type='STOCK', timeframe='1d',
        adjustment='qfq', start='2026-05-18', end='2026-05-18')


def test_nine_exact_targets_keep_revisions_and_bounded_windows():
    from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    from src.services.thesis_ledger_native_daily import NATIVE_DAILY_ROUTES
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    targets = [(key, target, gated) for key, target, gated in exact_route_inventory()
        if target == {'providerId': 'yfinance', 'upstreamSource': 'yfinance'} and key['kind'] == 'bar' and key['assetType'] != 'INDEX']
    assert len(targets) == 6 and all(gated and key['adjustment'] == 'qfq' for key, _, gated in targets)
    assert len([b for b in SOURCE.bindings if b.key.capability == 'DAILY_BAR']) == 9 and SOURCE.factory_scope == 'provider'
    for binding in (b for b in SOURCE.bindings if b.key.capability == 'DAILY_BAR'):
        key, target = binding.key.route_key(), {'providerId': 'yfinance', 'upstreamSource': binding.upstream_source}
        assert CAPABILITY_SOURCES.resolve(key, target) is binding
        assert (key['market'], key['assetType'], 'yfinance', binding.upstream_source) not in NATIVE_DAILY_ROUTES
        revisions = market_v3_current_route_revisions(key, target, PROVIDER_MANIFESTS['yfinance'])
        assert revisions['adapterRevision'] == f"dsa-v3-{key['market'].lower()}-{key['assetType'].lower()}-yfinance-{key['adjustment']}-exact-daily-v1"
        assert revisions['sourceRevision'] == PROTOCOL and revisions['credentialRevision'] == 'not-required'
    remaining = {key: value for key, value in NATIVE_DAILY_ROUTES.items() if key[2] == 'yfinance'}
    assert remaining == {}
    assert {source['sourceId']: source['capabilities'] for source in SOURCE.manifest()['upstreamSources']
        if source['sourceId'] != 'yfinance'} == {
        'yahoo-gme-share-window': {'DAILY_BAR': ['STOCK']}, 'yahoo-hk-share-window': {'DAILY_BAR': ['STOCK']}}


@pytest.mark.parametrize('ticker,empty,code', [('AAPL', False, None), ('OTHER', False, 'invalid_response'),
    ('AAPL', True, 'not_covered')])
def test_multiindex_preserves_identity_and_empty_errors(monkeypatch, ticker, empty, code):
    raw = pd.DataFrame([[1., 1.2, .9, 1.1, 100.]], columns=pd.MultiIndex.from_product([
        ['Open', 'High', 'Low', 'Close', 'Volume'], [ticker]]), index=pd.DatetimeIndex(['2026-05-18'], name='Date'))
    if empty:
        raw = raw.iloc[0:0]
    calls = []
    def download(**kwargs):
        calls.append(kwargs)
        return raw
    monkeypatch.setattr('yfinance.download', download)
    binding = next(binding for binding in SOURCE.bindings if binding.key.market == 'US' and binding.key.asset_type == 'STOCK')
    context = ReadContext(SimpleNamespace(_convert_stock_code=lambda symbol: symbol))
    if code:
        with pytest.raises(CapabilityContractError) as error:
            CAPABILITY_SOURCES.daily(request(), binding.key.route_key(),
                {'providerId': 'yfinance', 'upstreamSource': 'yfinance'}, context)
        assert error.value.code == code
    else:
        result = CAPABILITY_SOURCES.daily(request(), binding.key.route_key(),
            {'providerId': 'yfinance', 'upstreamSource': 'yfinance'}, context)
        assert result.frame['date'].tolist() == ['2026-05-18'] and result.frame.iloc[0]['amount'] is None
    assert len(calls) == 1 and calls[0]['tickers'] == 'AAPL'


def test_factory_cache_and_probe_do_not_change_legacy_scope(store):
    runtime = ThesisLedgerProviderRuntime(store)
    first = runtime._adapter('yfinance', market_v3_route_key=SOURCE.bindings[0].key.route_key())
    assert runtime._adapter('yfinance', market_v3_route_key=SOURCE.bindings[-1].key.route_key()) is first
    index_key = {'kind': 'bar', 'market': 'US', 'assetType': 'INDEX', 'capability': 'DAILY_BAR', 'timeframe': '1d', 'adjustment': 'qfq'}
    assert runtime._adapter('yfinance', market_v3_route_key=index_key) is first
    assert runtime._adapter('yfinance', market_v3_route_key=index_key, cache=False, probe=True) is not first
    assert runtime.adapters['yfinance'] is first


@pytest.mark.parametrize('market,symbol,start,end,reader_name', [
    ('US', 'GME.US', '2024-05-10', '2024-05-24', 'yahoo_share_window'),
    ('HK', '00147.HK', '2025-08-06', '2025-08-11', 'yahoo_hk_share_window'),
])
def test_bounded_reference_binding_preserves_original_frame_and_observations(
        monkeypatch, store, market, symbol, start, end, reader_name):
    from importlib import import_module
    monkeypatch.setattr('yfinance.download', lambda **kwargs: pytest.fail('参考窗口不能在线下载'))
    binding = next(binding for binding in SOURCE.bindings
        if binding.key.market == market and binding.key.adjustment == 'none')
    query = ThesisLedgerDataRequest('DAILY_BAR', symbol, instrument_type='STOCK', timeframe='1d',
        adjustment='none', start=start, end=end)
    original = getattr(import_module(f'data_provider.{reader_name}'), f'read_{reader_name}')(query)
    key = binding.key.route_key()
    target = {'providerId': 'yfinance', 'upstreamSource': binding.upstream_source}
    assert apply(store, key, target)['effective']['routes'][0]['targets'][0]['eligible']
    result = ThesisLedgerProviderRuntime(store).execute_market_bars_v3(query, key)
    pd.testing.assert_frame_equal(result.value, original)
    assert original.attrs and all(result.value.attrs[name] == value for name, value in original.attrs.items())
    assert result.value.attrs['thesis_ledger_v3_pagination']['protocol'] == PROTOCOL
    assert result.upstream_source == binding.upstream_source
    no_admissions(store)


def test_existing_us_index_runs_through_control_factory_and_binding(monkeypatch, store):
    calls = []
    def download(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame({'Open': [1.], 'High': [1.2], 'Low': [.9], 'Close': [1.1], 'Volume': [100.]},
            index=pd.DatetimeIndex(['2026-05-18'], name='Date'))
    monkeypatch.setattr('yfinance.download', download)
    binding = next(binding for binding in SOURCE.bindings if binding.key.asset_type == 'INDEX')
    key, target = binding.key.route_key(), {'providerId': 'yfinance', 'upstreamSource': 'yfinance'}
    assert apply(store, key, target)['effective']['routes'][0]['targets'][0]['eligible']
    query = ThesisLedgerDataRequest('DAILY_BAR', 'SPX.US', instrument_type='INDEX', timeframe='1d',
        adjustment='qfq', start='2026-05-18', end='2026-05-18')
    result = ThesisLedgerProviderRuntime(store).execute_market_bars_v3(query, key)
    assert result.value['date'].tolist() == ['2026-05-18'] and result.value.iloc[0]['amount'] is None
    assert calls == [{'tickers': '^GSPC', 'start': '2026-05-18', 'end': '2026-05-19',
        'auto_adjust': True, 'progress': False, 'threads': False, 'timeout': 4.5, 'multi_level_index': False}]
    assert result.value.attrs['thesis_ledger_v3_pagination']['protocol'] == PROTOCOL
    no_admissions(store)


def test_invalid_index_identity_rejects_before_download_and_other_sources_stay_unbound(monkeypatch, store):
    monkeypatch.setattr('yfinance.download', lambda **kwargs: pytest.fail('非法指数不应下载'))
    binding = next(binding for binding in SOURCE.bindings if binding.key.asset_type == 'INDEX')
    key = binding.key.route_key()
    query = ThesisLedgerDataRequest('DAILY_BAR', 'AAPL.US', instrument_type='INDEX', timeframe='1d',
        adjustment='qfq', start='2026-05-18', end='2026-05-18')
    adapter = ThesisLedgerProviderRuntime(store)._adapter('yfinance', market_v3_route_key=key)
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.daily(query, key, {'providerId': 'yfinance', 'upstreamSource': 'yfinance'}, ReadContext(adapter))
    assert error.value.code == 'invalid_request'
    assert CAPABILITY_SOURCES.resolve(key, {'providerId': 'tencent', 'upstreamSource': 'tencent'}) is None
    assert CAPABILITY_SOURCES.resolve(key, {'providerId': 'tushare', 'upstreamSource': 'tushare'}) is None
