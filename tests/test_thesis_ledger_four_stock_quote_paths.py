"""四来源股票报价：原请求/预算、精确身份、缺失与真实零。"""

import json
from types import SimpleNamespace

import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, QuoteQuery, ReadContext
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions


def configure(store, provider):
    method, field = ('token', 'token') if provider == 'tushare' else ('api_key', 'apiKey')
    store.save_provider_config(provider, {'requestId': 'synthetic', 'enabled': True,
        'credentials': {'method': method, 'values': {field: 'synthetic'}}})
    market = 'CN' if provider in {'tushare', 'tickflow'} else 'US'
    key = {'kind': 'data', 'market': market, 'assetType': 'STOCK', 'capability': 'REALTIME_QUOTE'}
    apply(store, key, {'providerId': provider, 'upstreamSource': provider})
    return key


def test_four_unique_bindings_and_actual_adapter_revision_changes():
    from src.services.thesis_ledger_current_data_route import _DATA_ADAPTERS, current_data_adapter_revisions
    expected = {'tickflow': ('tickflow-cn-stock-exact-quote-v2', 'tickflow-direct-quote-v1'),
        'tushare': ('tushare-cn-stock-rt-k-quote-v1', 'tushare-pro-rt-k-v1'),
        'finnhub': ('finnhub-us-stock-exact-quote-v1', 'finnhub-direct-quote-v1'),
        'alphavantage': ('alphavantage-us-stock-exact-quote-v2', 'alphavantage-direct-quote-v1')}
    for provider, revisions in expected.items():
        bindings = [b for b in CAPABILITY_SOURCES.source(provider).bindings if b.key.capability == 'REALTIME_QUOTE']
        assert len(bindings) == 1
        key = bindings[0].key.route_key()
        assert (key['market'], 'STOCK', 'REALTIME_QUOTE', provider, provider) not in _DATA_ADAPTERS
        actual = current_data_adapter_revisions(key, {'providerId': provider, 'upstreamSource': provider})
        assert (actual['adapterRevision'], actual['sourceRevision']) == revisions


def test_tushare_actual_factory_preserves_rt_k_budget_fields_and_raw_timestamp(monkeypatch, store):
    monkeypatch.delenv('TUSHARE_HTTP_URL', raising=False)
    calls = []
    def post(url, *, json, timeout, allow_redirects):
        calls.append((url, json, timeout, allow_redirects))
        payload = {'code': 0, 'data': {'fields': ['ts_code', 'close', 'open', 'high', 'low', 'pre_close', 'vol', 'amount', 'trade_time'],
            'items': [['600519.SH', 10, 9, 11, 8, 9, 0, None, '2026-07-01 10:00:00']]}}
        return SimpleNamespace(status_code=200, text=__import__('json').dumps(payload))
    monkeypatch.setattr('data_provider.tushare_fetcher.requests.post', post)
    configure(store, 'tushare')
    value = ThesisLedgerProviderRuntime(store).execute_request(
        ThesisLedgerDataRequest('REALTIME_QUOTE', '600519.SH', instrument_type='STOCK')).value
    assert value.volume == 0 and value.amount is None
    assert value.source_time_observation.text == '2026-07-01 10:00:00'
    assert value.source_time_observation.basis == 'unverified' and value.provider_timestamp is None
    assert len(calls) == 1 and 0 < calls[0][2] <= 4.5 and calls[0][3] is False
    assert calls[0][1] == {'api_name': 'rt_k', 'token': 'synthetic',
        'fields': 'ts_code,close,open,high,low,pre_close,vol,amount,trade_time', 'params': {'ts_code': '600519.SH'}}
    no_admissions(store)


@pytest.mark.parametrize('volume,amount,expected', [(None, None, None), (0, 0, 0), (1, 10, 100)])
def test_tickflow_actual_sdk_projection_keeps_units_clock_and_missing(monkeypatch, store, volume, amount, expected):
    from data_provider.tickflow_fetcher import TickFlowFetcher
    calls = []
    raw = {'symbol': '600519.SH', 'last_price': 10, 'prev_close': 9, 'open': 9, 'high': 11, 'low': 8,
        'volume': volume, 'amount': amount, 'timestamp': 1791201600000}
    def get(**kwargs):
        calls.append(kwargs)
        return [raw]
    monkeypatch.setattr(TickFlowFetcher, '_get_client', lambda self: SimpleNamespace(quotes=SimpleNamespace(get=get)))
    configure(store, 'tickflow')
    value = ThesisLedgerProviderRuntime(store).execute_request(
        ThesisLedgerDataRequest('REALTIME_QUOTE', '600519.SH', instrument_type='STOCK')).value
    assert value.volume == expected and value.amount == amount and value.provider_timestamp
    assert calls == [{'symbols': ['600519.SH']}]
    no_admissions(store)


@pytest.mark.parametrize('provider', ['finnhub', 'alphavantage'])
def test_actual_us_factory_preserves_endpoint_strict_budget_and_single_response(monkeypatch, store, provider):
    from data_provider.finnhub_fetcher import FinnhubFetcher
    from data_provider.alphavantage_fetcher import AlphaVantageFetcher
    cls = FinnhubFetcher if provider == 'finnhub' else AlphaVantageFetcher
    sleeps, calls = [], []
    monkeypatch.setattr(cls, 'random_sleep', lambda self, *args: sleeps.append(args))
    payload = {'c': 10, 'pc': 9, 'o': 9, 'h': 11, 'l': 8} if provider == 'finnhub' else {
        'Global Quote': {'01. symbol': 'AAPL', '05. price': '10', '08. previous close': '9',
                         '02. open': '9', '03. high': '11', '04. low': '8'}}
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr('requests.get', get)
    configure(store, provider)
    value = ThesisLedgerProviderRuntime(store).execute_request(
        ThesisLedgerDataRequest('REALTIME_QUOTE', 'AAPL.US', instrument_type='STOCK')).value
    assert value.price == 10 and value.volume is None and value.amount is None
    assert len(calls) == 1 and calls[0][1]['timeout'] == 5 and len(sleeps) == 1
    expected = {'symbol': 'AAPL', 'token': 'synthetic'} if provider == 'finnhub' else {
        'function': 'GLOBAL_QUOTE', 'symbol': 'AAPL', 'apikey': 'synthetic'}
    assert calls[0][1]['params'] == expected
    no_admissions(store)


@pytest.mark.parametrize('changes,code', [({'01. symbol': 'OTHER'}, 'invalid_response'),
    ({'06. volume': True}, 'invalid_response'), ({'05. price': True}, 'invalid_response'),
    ({'02. open': None}, 'invalid_response')])
def test_alpha_standard_response_rejects_identity_and_invalid_or_missing_price(monkeypatch, changes, code):
    from data_provider.thesis_ledger_sources.alphavantage import SOURCE
    raw = {'01. symbol': 'AAPL', '05. price': '10', '08. previous close': '9', '02. open': '9', '03. high': '11', '04. low': '8', **changes}
    monkeypatch.setattr('requests.get', lambda *args, **kwargs: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'Global Quote': raw}))
    adapter = SimpleNamespace(_api_key='synthetic', _is_us_stock=lambda _: True, random_sleep=lambda *args: None)
    binding = next(b for b in SOURCE.bindings if b.key.capability == 'REALTIME_QUOTE')
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.quote(QuoteQuery(symbol='AAPL.US', instrument_type='STOCK'), binding.key.route_key(),
            {'providerId': 'alphavantage', 'upstreamSource': 'alphavantage'}, ReadContext(adapter))
    assert error.value.code == code


def test_alpha_real_zero_volume_and_day_is_not_exact_provider_clock(monkeypatch):
    from data_provider.thesis_ledger_sources.alphavantage import SOURCE
    raw = {'01. symbol': 'AAPL', '05. price': '10', '08. previous close': '9', '02. open': '9',
        '03. high': '11', '04. low': '8', '06. volume': '0', '07. latest trading day': '2026-07-01'}
    monkeypatch.setattr('requests.get', lambda *args, **kwargs: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'Global Quote': raw}))
    adapter = SimpleNamespace(_api_key='synthetic', _is_us_stock=lambda _: True, random_sleep=lambda *args: None)
    binding = next(b for b in SOURCE.bindings if b.key.capability == 'REALTIME_QUOTE')
    value = CAPABILITY_SOURCES.quote(QuoteQuery(symbol='AAPL.US', instrument_type='STOCK'), binding.key.route_key(),
        {'providerId': 'alphavantage', 'upstreamSource': 'alphavantage'}, ReadContext(adapter))
    assert value.volume == 0 and value.provider_timestamp is None and value.fetched_at
