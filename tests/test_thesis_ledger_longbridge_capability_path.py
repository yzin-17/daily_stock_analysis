"""Longbridge 实际工厂/执行器、OAuth CAS 与 probe 只读隔离回归。"""

from datetime import date, datetime
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from data_provider.longbridge_fetcher import LongbridgeFetcher
from data_provider.thesis_ledger_sources.longbridge import SOURCE, PROTOCOL
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.provider_oauth_contract import OAuthStateError
from src.services.provider_oauth_runtime import build_page_oauth
from src.services.provider_oauth_store import ProviderOAuthStore
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_control import PROVIDER_MANIFESTS, _encrypt_secret
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions


def legacy(token='synthetic', source='control', values=None):
    if values is None:
        values = {'appKey': 'synthetic-key', 'appSecret': 'synthetic-secret', 'accessToken': token}
    return ProviderCredentialSnapshot.create('longbridge', source, 'legacy', values, 1, 1)


def request(symbol='AAPL.US', asset='STOCK', adjustment='none'):
    return ThesisLedgerDataRequest('DAILY_BAR', symbol, instrument_type=asset, timeframe='1d',
        adjustment=adjustment, start='2026-05-18', end='2026-05-18')


def test_eight_bindings_revisions_and_credential_methods():
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    from src.services.thesis_ledger_native_daily import NATIVE_DAILY_ROUTES
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    from src.services.provider_credentials import credential_schema
    routes = [(key, target, gated) for key, target, gated in exact_route_inventory()
        if target['providerId'] == 'longbridge' and key['capability'] == 'DAILY_BAR']
    assert len(routes) == 8 and all(gated for _, _, gated in routes)
    assert not any(provider == 'longbridge' for _, _, provider, _ in NATIVE_DAILY_ROUTES)
    assert SOURCE.factory_scope == 'provider' and SOURCE.allow_empty_environment_credentials is True
    schema = credential_schema('longbridge')
    assert [method['method'] for method in schema['methods']] == ['legacy', 'oauth']
    assert 'tokenJson' not in json.dumps(schema)
    for binding in (b for b in SOURCE.bindings if b.key.capability == 'DAILY_BAR'):
        key, target = binding.key.route_key(), {'providerId': 'longbridge', 'upstreamSource': 'longbridge'}
        assert CAPABILITY_SOURCES.resolve(key, target) is binding
        revisions = market_v3_current_route_revisions(key, target, PROVIDER_MANIFESTS['longbridge'], credential_version=1)
        assert revisions['adapterRevision'] == f"dsa-v3-{key['market'].lower()}-{key['assetType'].lower()}-longbridge-{key['adjustment']}-exact-daily-v1"
        assert revisions['sourceRevision'] == PROTOCOL and binding.maximum_rows is None


@pytest.mark.parametrize('market,asset,symbol', [('HK', 'STOCK', '00700.HK'), ('HK', 'ETF', '02800.HK'),
    ('US', 'STOCK', 'AAPL.US'), ('US', 'ETF', 'SPY.US')])
@pytest.mark.parametrize('adjustment', ['none', 'qfq'])
@pytest.mark.parametrize('clock', ['aware', 'naive', 'epoch'])
def test_actual_factory_control_runtime_sdk_projection(monkeypatch, store, market, asset, symbol, adjustment, clock):
    from longbridge.openapi import Period, AdjustType
    local = datetime(2026, 5, 18, 16, tzinfo=ZoneInfo('Asia/Hong_Kong' if market == 'HK' else 'America/New_York'))
    timestamp = local.astimezone(ZoneInfo('UTC'))
    if clock == 'naive':
        timestamp = local.replace(tzinfo=None)
    elif clock == 'epoch':
        timestamp = int(local.timestamp())
    candle = SimpleNamespace(timestamp=timestamp, open=1., high=1.2, low=.9, close=1.1, volume=100.)
    calls = []
    def read(*args):
        calls.append(args)
        return [candle]
    monkeypatch.setattr(LongbridgeFetcher, '_get_ctx', lambda self: SimpleNamespace(history_candlesticks_by_date=read))
    store.save_provider_config('longbridge', {'requestId': 'fixture', 'credentials': {'method': 'legacy',
        'values': {'appKey': 'synthetic-key', 'appSecret': 'synthetic-secret', 'accessToken': 'synthetic'}}})
    key = {'kind': 'bar', 'market': market, 'assetType': asset, 'capability': 'DAILY_BAR', 'timeframe': '1d', 'adjustment': adjustment}
    assert apply(store, key, {'providerId': 'longbridge', 'upstreamSource': 'longbridge'})['effective']['routes'][0]['targets'][0]['eligible']
    runtime = ThesisLedgerProviderRuntime(store)
    result = runtime.execute_market_bars_v3(request(symbol, asset, adjustment), key)
    assert result.value['date'].tolist() == ['2026-05-18']
    assert result.value.iloc[0]['amount'] is None and result.value.iloc[0]['volume'] == 100
    assert calls == [(symbol, Period.Day, AdjustType.NoAdjust if adjustment == 'none' else AdjustType.ForwardAdjust,
        date(2026, 5, 18), date(2026, 5, 18))]
    assert result.provider == 'longbridge' and result.upstream_source == 'longbridge' and not result.fallback_used
    assert runtime.adapters['longbridge']._explicit_access_token == 'synthetic'
    assert result.value.attrs['thesis_ledger_v3_pagination']['protocol'] == PROTOCOL
    no_admissions(store)


def test_legacy_cache_rotation_probe_and_environment_discovery(monkeypatch, store):
    runtime = ThesisLedgerProviderRuntime(store)
    key = SOURCE.bindings[0].key.route_key()
    first = runtime._adapter('longbridge', legacy(), market_v3_route_key=key)
    first._ctx = object()
    assert runtime._adapter('longbridge', legacy(), market_v3_route_key=SOURCE.bindings[-1].key.route_key()) is first
    second = runtime._adapter('longbridge', legacy('synthetic-rotated'), market_v3_route_key=key)
    assert second is not first and second._ctx is None and first._ctx is not None
    assert runtime._adapter('longbridge', legacy('synthetic-rotated'), cache=False, probe=True, market_v3_route_key=key) is not second
    assert runtime.adapters['longbridge'] is second
    environment = runtime._adapter('longbridge', legacy(source='environment', values={}), cache=False, market_v3_route_key=key)
    assert environment._explicit_credentials is False
    with pytest.raises(ProviderCallError) as error:
        runtime._adapter('longbridge', legacy(values={}), cache=False, market_v3_route_key=key)
    assert error.value.code == 'not_configured'


def test_oauth_factory_uses_original_cas_store_and_probe_never_persists(monkeypatch, store):
    repository = ProviderOAuthStore(store.database_path, encrypt=_encrypt_secret)
    token = json.dumps({'client_id': 'synthetic-client', 'access_token': 'synthetic', 'refresh_token': 'synthetic-refresh', 'expires_at': 1900000000})
    session, _ = repository.create('synthetic-client')
    repository.save_authorized_token(session['sessionId'], token)
    frozen = store.provider_credential_snapshot('longbridge')
    calls = []
    class Builder:
        def __init__(self, client_id, **kwargs):
            assert client_id == 'synthetic-client' and kwargs['allow_authorization'] is False
            self.save = kwargs['on_token_save']
        def build(self, refuse):
            self.refuse = refuse
            return self
    def build(snapshot, path, *, read_only):
        calls.append((snapshot, path, read_only))
        return build_page_oauth(snapshot, path, read_only=read_only, builder_factory=lambda: Builder)
    monkeypatch.setattr('src.services.provider_oauth_runtime.build_page_oauth', build)
    runtime = ThesisLedgerProviderRuntime(store)
    key = SOURCE.bindings[0].key.route_key()
    normal = runtime._adapter('longbridge', frozen, market_v3_route_key=key)
    assert runtime._adapter('longbridge', frozen, market_v3_route_key=key) is normal
    probe = runtime._adapter('longbridge', frozen, cache=False, probe=True, market_v3_route_key=key)
    before = store.provider_credential_snapshot('longbridge')
    probe._explicit_oauth_token.save(token)
    assert store.provider_credential_snapshot('longbridge') == before
    with pytest.raises(OAuthStateError, match='OAUTH_REAUTH_REQUIRED'):
        probe._explicit_oauth_token.refuse('https://fixture.example/authorize')
    normal._explicit_oauth_token.save(token)
    changed = store.provider_credential_snapshot('longbridge')
    assert changed.credential_version == before.credential_version + 1
    replaced = runtime._adapter('longbridge', changed, market_v3_route_key=key)
    assert replaced is not normal and runtime.adapters['longbridge'] is replaced
    with pytest.raises(OAuthStateError, match='CONFIG_CHANGED'):
        replaced._explicit_oauth_token.save(token)
        normal._explicit_oauth_token.save(token)
    assert calls[0] == (frozen, store.database_path, False) and calls[1] == (frozen, store.database_path, True)


@pytest.mark.parametrize('failure,code', [('credential', 'credential_missing'), ('empty', 'not_covered')])
def test_source_error_boundary_keeps_credential_and_empty_distinct(failure, code):
    adapter = SimpleNamespace(_get_ctx=lambda: None if failure == 'credential' else SimpleNamespace(history_candlesticks_by_date=lambda *args: []))
    binding = next(binding for binding in SOURCE.bindings if binding.key.market == 'US' and binding.key.asset_type == 'STOCK' and binding.key.adjustment == 'none')
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.daily(request(), binding.key.route_key(), {'providerId': 'longbridge', 'upstreamSource': 'longbridge'}, ReadContext(adapter))
    assert error.value.code == code
