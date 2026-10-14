"""Finnhub 秒级数组、纽约夏令时及唯一实际执行路径回归。"""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from data_provider.thesis_ledger_sources.finnhub import SOURCE, PROTOCOL
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions

KEY = SOURCE.bindings[0].key.route_key()
TARGET = {'providerId': 'finnhub', 'upstreamSource': 'finnhub'}


def request(start='2026-05-18', end='2026-05-18'):
    return ThesisLedgerDataRequest('DAILY_BAR', 'AAPL.US', timeframe='1d', instrument_type='STOCK',
        adjustment='qfq', start=start, end=end)


def test_unique_target_and_original_revisions():
    from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    from src.services.thesis_ledger_native_daily import NATIVE_DAILY_ROUTES
    targets = [(key, target, gated) for key, target, gated in exact_route_inventory()
        if target['providerId'] == 'finnhub' and key['capability'] == 'DAILY_BAR']
    assert targets == [(KEY, TARGET, True)]
    assert not any(provider == 'finnhub' for _, _, provider, _ in NATIVE_DAILY_ROUTES)
    assert SOURCE.factory_scope == 'provider' and SOURCE.credentials == (('api_key', ('apiKey',)),)
    revision = market_v3_current_route_revisions(KEY, TARGET, PROVIDER_MANIFESTS['finnhub'], credential_version=1)
    assert revision['adapterRevision'] == 'dsa-v3-us-stock-finnhub-qfq-exact-daily-v1'
    assert revision['sourceRevision'] == PROTOCOL


@pytest.mark.parametrize('start,end,first,last', [('2026-05-18', '2026-05-18', 1779076800, 1779163199),
    ('2026-03-06', '2026-03-09', 1772773200, 1773115199)])
def test_actual_factory_runtime_and_new_york_window(monkeypatch, store, start, end, first, last):
    calls = []
    days = list(dict.fromkeys([start, end]))
    def get(url, params, timeout):
        calls.append((url, params, timeout))
        count = len(days)
        payload = {'s': 'ok', 't': [int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('America/New_York')).timestamp()) for day in days],
            'o': [1.] * count, 'h': [1.2] * count, 'l': [.9] * count, 'c': [1.1] * count, 'v': [100.] * count}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr('data_provider.multimarket_exact_daily.requests.get', get)
    store.save_provider_config('finnhub', {'requestId': 'fixture',
        'credentials': {'method': 'api_key', 'values': {'apiKey': 'synthetic'}}})
    assert apply(store, KEY, TARGET)['effective']['routes'][0]['targets'][0]['eligible']
    runtime = ThesisLedgerProviderRuntime(store)
    result = runtime.execute_market_bars_v3(request(start, end), KEY)
    assert result.value['date'].tolist() == days
    assert result.value['close'].tolist() == [1.1] * len(days) and result.value['amount'].tolist() == [None] * len(days)
    assert result.provider == 'finnhub' and result.upstream_source == 'finnhub' and not result.fallback_used
    assert calls == [('https://finnhub.io/api/v1/stock/candle', {'symbol': 'AAPL', 'resolution': 'D',
        'from': first, 'to': last, 'token': 'synthetic'}, 4.5)]
    assert runtime.adapters['finnhub']._strict_errors is True
    no_admissions(store)


def test_provider_factory_reuse_rotation_and_probe_preserve_original_cache(monkeypatch, store):
    def configure(value):
        store.save_provider_config('finnhub', {'requestId': 'fixture',
            'credentials': {'method': 'api_key', 'values': {'apiKey': value}}})
        return store.provider_credential_snapshot('finnhub')
    calls = []
    monkeypatch.setattr('data_provider.finnhub_fetcher.FinnhubFetcher', lambda **kwargs:
        calls.append(kwargs) or SimpleNamespace(options=kwargs))
    runtime = ThesisLedgerProviderRuntime(store)
    snapshot = configure('synthetic-first')
    first = runtime._adapter('finnhub', snapshot, market_v3_route_key=KEY)
    assert runtime._adapter('finnhub', snapshot, market_v3_route_key=KEY) is first
    quote = {'kind': 'data', 'market': 'US', 'assetType': 'STOCK', 'capability': 'REALTIME_QUOTE'}
    assert runtime._adapter('finnhub', snapshot, market_v3_route_key=quote) is first
    snapshot = configure('synthetic-second')
    second = runtime._adapter('finnhub', snapshot, market_v3_route_key=KEY)
    assert second is not first and first.options['api_key'] == 'synthetic-first'
    assert second.options == {'api_key': 'synthetic-second', 'strict_errors': True}
    assert runtime._adapter('finnhub', snapshot, cache=False, probe=True, market_v3_route_key=KEY) is not second
    assert runtime.adapters['finnhub'] is second and len(calls) == 3


@pytest.mark.parametrize('payload,code', [({'s': 'no_data'}, 'not_covered'), ({}, 'not_covered'),
    ([], 'invalid_response'), ({'s': 'ok', 't': [], 'o': [], 'h': [], 'l': [], 'c': [], 'v': []}, 'not_covered')])
def test_payload_failure_remains_classified(monkeypatch, payload, code):
    monkeypatch.setattr('data_provider.multimarket_exact_daily.requests.get', lambda *args, **kwargs:
        SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload))
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.daily(request(), KEY, TARGET, ReadContext(SimpleNamespace(_api_key='synthetic')))
    assert error.value.code == code
