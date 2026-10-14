"""Alpha Vantage 唯一绑定、原 HTTP 语义与实际工厂/执行器验证。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
import requests

from data_provider.thesis_ledger_sources.alphavantage import SOURCE, PROTOCOL
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions

KEY = SOURCE.bindings[0].key.route_key()
TARGET = {'providerId': 'alphavantage', 'upstreamSource': 'alphavantage'}


def request():
    return ThesisLedgerDataRequest('DAILY_BAR', 'AAPL.US', timeframe='1d', instrument_type='STOCK',
        adjustment='none', start='2026-05-18', end='2026-05-18')


def test_original_registration_revisions_and_credential_definition():
    from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    from src.services.thesis_ledger_native_daily import NATIVE_DAILY_ROUTES
    from src.services.provider_credentials import credential_schema
    targets = [(key, target, gated) for key, target, gated in exact_route_inventory()
        if target['providerId'] == 'alphavantage' and key['capability'] == 'DAILY_BAR']
    assert targets == [(KEY, TARGET, True)]
    assert not any(provider == 'alphavantage' for _, _, provider, _ in NATIVE_DAILY_ROUTES)
    assert CAPABILITY_SOURCES.resolve(KEY, TARGET) is SOURCE.bindings[0]
    assert SOURCE.factory_scope == 'provider' and SOURCE.dependency_modules == ('requests',)
    assert credential_schema('alphavantage')['methods'][0]['method'] == 'api_key'
    revision = market_v3_current_route_revisions(KEY, TARGET, PROVIDER_MANIFESTS['alphavantage'], credential_version=1)
    assert revision['adapterRevision'] == 'dsa-v3-us-stock-alphavantage-none-exact-daily-v1'
    assert revision['sourceRevision'] == PROTOCOL


def test_actual_factory_and_runtime_preserve_pinned_full_response(monkeypatch, store):
    calls = []
    def get(url, params, timeout):
        calls.append((url, params, timeout))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'Time Series (Daily)': {
            '2026-05-19': {'1. open': '99', '2. high': '99', '3. low': '99', '4. close': '99'},
            '2026-05-18': {'1. open': '1', '2. high': '1.2', '3. low': '.9', '4. close': '1.1', '5. volume': '100'},
        }})
    monkeypatch.setattr('data_provider.multimarket_exact_daily.requests.get', get)
    store.save_provider_config('alphavantage', {'requestId': 'fixture',
        'credentials': {'method': 'api_key', 'values': {'apiKey': 'synthetic'}}})
    assert apply(store, KEY, TARGET)['effective']['routes'][0]['targets'][0]['eligible']
    runtime = ThesisLedgerProviderRuntime(store)
    result = runtime.execute_market_bars_v3(request(), KEY)
    adapter = runtime.adapters['alphavantage']
    assert adapter._api_key == 'synthetic' and adapter._strict_errors is True
    assert result.provider == 'alphavantage' and result.upstream_source == 'alphavantage' and not result.fallback_used
    assert result.value['date'].tolist() == ['2026-05-18']
    assert result.value.iloc[0]['close'] == 1.1 and result.value.iloc[0]['volume'] == 100
    assert result.value.iloc[0]['amount'] is None
    assert calls == [('https://www.alphavantage.co/query', {'function': 'TIME_SERIES_DAILY',
        'symbol': 'AAPL', 'outputsize': 'full', 'apikey': 'synthetic'}, 4.5)]
    no_admissions(store)


def test_factory_keeps_environment_error_mode_and_probe_cache_isolation(monkeypatch, store):
    store.save_provider_config('alphavantage', {'requestId': 'fixture',
        'credentials': {'method': 'api_key', 'values': {'apiKey': 'synthetic'}}})
    snapshot = store.provider_credential_snapshot('alphavantage')
    calls = []
    monkeypatch.setattr('data_provider.alphavantage_fetcher.AlphaVantageFetcher',
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(options=kwargs))
    assert SOURCE.factory(ReadContext(None, replace(snapshot, source='environment'))).options['strict_errors'] is False
    runtime = ThesisLedgerProviderRuntime(store)
    first = runtime._adapter('alphavantage', snapshot, market_v3_route_key=KEY)
    assert runtime._adapter('alphavantage', snapshot, market_v3_route_key=KEY) is first
    probe = runtime._adapter('alphavantage', snapshot, cache=False, probe=True, market_v3_route_key=KEY)
    assert probe is not first and runtime.adapters['alphavantage'] is first
    assert calls == [{'api_key': 'synthetic', 'strict_errors': False},
        {'api_key': 'synthetic', 'strict_errors': True}, {'api_key': 'synthetic', 'strict_errors': True}]


@pytest.mark.parametrize('payload,code', [({'Note': 'fixture'}, 'quota_unavailable'),
    ({'Information': 'fixture'}, 'quota_unavailable'), ({}, 'not_covered'),
    ({'Time Series (Daily)': []}, 'not_covered'), ([], 'invalid_response')])
def test_payload_failure_remains_classified(monkeypatch, payload, code):
    monkeypatch.setattr('data_provider.multimarket_exact_daily.requests.get', lambda *args, **kwargs:
        SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload))
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.daily(request(), KEY, TARGET, ReadContext(SimpleNamespace(_api_key='synthetic')))
    assert error.value.code == code


@pytest.mark.parametrize('status,code', [(401, 'permission_denied'), (403, 'permission_denied'),
    (429, 'quota_unavailable'), (503, 'upstream_failure')])
def test_http_failure_uses_original_error_boundary(monkeypatch, status, code):
    calls = []
    def get(*args, **kwargs):
        calls.append(1)
        response = requests.Response()
        response.status_code = status
        def fail():
            raise requests.HTTPError('fixture', response=response)
        return SimpleNamespace(raise_for_status=fail)
    monkeypatch.setattr('data_provider.multimarket_exact_daily.requests.get', get)
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.daily(request(), KEY, TARGET, ReadContext(SimpleNamespace(_api_key='synthetic')))
    assert error.value.code == code and calls == [1]
