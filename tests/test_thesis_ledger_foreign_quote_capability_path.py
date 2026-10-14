"""既有 Longbridge/Yahoo 报价经原工厂/执行器，代码/时钟/原型保持。"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from data_provider.longbridge_fetcher import LongbridgeFetcher
from data_provider.thesis_ledger_sources.longbridge import SOURCE as LB
from data_provider.thesis_ledger_sources.yfinance import SOURCE as YAHOO
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from tests.test_configured_market_routes import store, apply, no_admissions


def test_eleven_quote_bindings_have_one_fact_and_original_v2_revisions():
    from src.services.thesis_ledger_current_data_route import _DATA_ADAPTERS, current_data_adapter_revisions
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    for source, count in [(LB, 4), (YAHOO, 7)]:
        bindings = [b for b in source.bindings if b.key.capability == 'REALTIME_QUOTE']
        targets = [(k, t) for k, t, _ in exact_route_inventory()
            if k['capability'] == 'REALTIME_QUOTE' and t['providerId'] == source.provider_id]
        assert len(bindings) == len(targets) == count
        for binding in bindings:
            key = binding.key.route_key()
            target = {'providerId': source.provider_id, 'upstreamSource': source.provider_id}
            assert (key['market'], key['assetType'], 'REALTIME_QUOTE', source.provider_id, source.provider_id) not in _DATA_ADAPTERS
            assert current_data_adapter_revisions(key, target)['adapterRevision'] == binding.adapter_revision
            assert current_data_adapter_revisions(key, target)['sourceRevision'] == source.provider_id + '-direct-quote-v2'
    index = {'kind': 'data', 'market': 'US', 'assetType': 'INDEX', 'capability': 'REALTIME_QUOTE'}
    assert CAPABILITY_SOURCES.resolve(index, {'providerId': 'longbridge', 'upstreamSource': 'longbridge'}) is None
    assert CAPABILITY_SOURCES.resolve(index, {'providerId': 'futu', 'upstreamSource': 'futu'}) is None


@pytest.mark.parametrize('market,asset,symbol,ticker', [('HK', 'STOCK', '00700.HK', '0700.HK'),
    ('HK', 'ETF', '02800.HK', '2800.HK'), ('US', 'STOCK', 'AAPL.US', 'AAPL.US'), ('US', 'ETF', 'SPY.US', 'SPY.US')])
def test_longbridge_four_actual_factory_sdk_single_call_and_original_clock(monkeypatch, store, market, asset, symbol, ticker):
    store.save_provider_config('longbridge', {'requestId': 'synthetic', 'enabled': True,
        'credentials': {'method': 'legacy', 'values': {'appKey': 'synthetic', 'appSecret': 'synthetic', 'accessToken': 'synthetic'}}})
    calls = []
    def read(symbols):
        calls.append(symbols)
        return [SimpleNamespace(symbol=ticker, last_done=10, prev_close=9, open=9, high=11, low=8,
            volume=0, turnover=None, timestamp=datetime(2026, 7, 1, tzinfo=timezone.utc))]
    monkeypatch.setattr(LongbridgeFetcher, '_get_ctx', lambda self: SimpleNamespace(quote=read))
    key = {'kind': 'data', 'market': market, 'assetType': asset, 'capability': 'REALTIME_QUOTE'}
    assert apply(store, key, {'providerId': 'longbridge', 'upstreamSource': 'longbridge'})['effective']['routes'][0]['targets'][0]['eligible']
    result = ThesisLedgerProviderRuntime(store).execute_request(ThesisLedgerDataRequest('REALTIME_QUOTE', symbol, instrument_type=asset))
    assert calls == [[ticker]]
    assert result.value.code == ticker and result.value.volume == 0 and result.value.amount is None
    assert result.value.provider_timestamp == '2026-07-01T00:00:00+00:00'
    assert result.value.fetched_at != result.value.provider_timestamp
    assert result.provider == result.upstream_source == 'longbridge'
    no_admissions(store)


@pytest.mark.parametrize('market,asset,symbol,ticker', [('CN', 'STOCK', '600519.SH', '600519.SS'),
    ('CN', 'ETF', '510300.SH', '510300.SS'), ('HK', 'STOCK', '00700.HK', '0700.HK'),
    ('HK', 'ETF', '02800.HK', '2800.HK'), ('US', 'STOCK', 'AAPL.US', 'AAPL'),
    ('US', 'ETF', 'SPY.US', 'SPY'), ('US', 'INDEX', 'SPX.US', '^GSPC')])
def test_yahoo_seven_actual_factory_sdk_request_asset_and_unknown_metrics(monkeypatch, store, market, asset, symbol, ticker):
    import yfinance as yf
    calls = []
    def create(actual):
        calls.append(actual)
        return SimpleNamespace(get_info=lambda: {'symbol': ticker, 'quoteType': {'STOCK': 'EQUITY', 'ETF': 'ETF', 'INDEX': 'INDEX'}[asset],
            'regularMarketPrice': 10, 'regularMarketPreviousClose': 9, 'regularMarketOpen': 9,
            'regularMarketDayHigh': 11, 'regularMarketDayLow': 8, 'regularMarketVolume': None, 'regularMarketTime': None})
    monkeypatch.setattr(yf, 'Ticker', create)
    monkeypatch.setattr(yf, 'download', lambda **kwargs: pytest.fail('报价不能读取历史'))
    key = {'kind': 'data', 'market': market, 'assetType': asset, 'capability': 'REALTIME_QUOTE'}
    assert apply(store, key, {'providerId': 'yfinance', 'upstreamSource': 'yfinance'})['effective']['routes'][0]['targets'][0]['eligible']
    result = ThesisLedgerProviderRuntime(store).execute_request(ThesisLedgerDataRequest('REALTIME_QUOTE', symbol, instrument_type=asset))
    assert calls == [ticker] and result.value.code == ticker
    assert result.value.volume is None and result.value.amount is None and result.value.provider_timestamp is None
    assert result.value.fetched_at and result.provider == result.upstream_source == 'yfinance'
    no_admissions(store)


@pytest.mark.parametrize('provider,failure,code', [('longbridge', 'empty', 'upstream_unavailable'),
    ('longbridge', 'wrong', 'invalid_response'), ('yfinance', 'wrong', 'invalid_response'),
    ('yfinance', 'wrong-asset', 'invalid_response')])
def test_old_empty_and_identity_errors_are_not_reinterpreted(monkeypatch, store, provider, failure, code):
    if provider == 'longbridge':
        store.save_provider_config(provider, {'requestId': 'synthetic', 'enabled': True,
            'credentials': {'method': 'legacy', 'values': {'appKey': 'synthetic', 'appSecret': 'synthetic', 'accessToken': 'synthetic'}}})
        monkeypatch.setattr(LongbridgeFetcher, '_get_ctx', lambda self: SimpleNamespace(quote=lambda _: [] if failure == 'empty' else [SimpleNamespace(symbol='OTHER.US')]))
    else:
        import yfinance as yf
        monkeypatch.setattr(yf, 'Ticker', lambda _: SimpleNamespace(get_info=lambda: {'symbol': 'OTHER' if failure == 'wrong' else 'AAPL', 'quoteType': 'ETF'}))
    key = {'kind': 'data', 'market': 'US', 'assetType': 'STOCK', 'capability': 'REALTIME_QUOTE'}
    apply(store, key, {'providerId': provider, 'upstreamSource': provider})
    with pytest.raises(ProviderCallError) as error:
        ThesisLedgerProviderRuntime(store).execute_request(ThesisLedgerDataRequest('REALTIME_QUOTE', 'AAPL.US', instrument_type='STOCK'))
    assert error.value.code == code
    no_admissions(store)
