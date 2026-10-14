"""Tushare 两目标、原工厂预算/缓存及真实 HTTP 执行接缝回归。"""

import json as json_module
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.base import RateLimitError
from data_provider.thesis_ledger_sources.tushare import SOURCE, PROTOCOL
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyQuery, ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions


def snapshot(token='synthetic', endpoint='https://fixture.example/api'):
    return ProviderCredentialSnapshot.create('tushare', 'environment', 'token',
        {'token': token, 'httpUrl': endpoint}, 0, 0)


def request(symbol='600519.SH'):
    return ThesisLedgerDataRequest('DAILY_BAR', symbol, instrument_type='STOCK', timeframe='1d',
        adjustment='none', start='2026-05-18', end='2026-05-18')


def test_original_revisions_and_single_registration(store):
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    from src.services.thesis_ledger_native_daily import NATIVE_DAILY_ROUTES
    from src.services.thesis_ledger_control import PROVIDER_MANIFESTS, _provider_credential_revision_from_snapshot
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    targets = [(key, target, gated) for key, target, gated in exact_route_inventory()
        if target['providerId'] == 'tushare' and key['assetType'] == 'STOCK' and key['capability'] == 'DAILY_BAR']
    assert len(targets) == 2 and {key['market'] for key, _, _ in targets} == {'CN', 'HK'}
    assert all(key['adjustment'] == 'none' and gated for key, _, gated in targets)
    assert not any(provider == 'tushare' for _, _, provider, _ in NATIVE_DAILY_ROUTES)
    assert SOURCE.factory_scope == 'provider' and SOURCE.require_credential_revision_for_cache is True
    for binding in (b for b in SOURCE.bindings if b.key.capability == 'DAILY_BAR' and b.key.asset_type == 'STOCK'):
        key, target = binding.key.route_key(), {'providerId': 'tushare', 'upstreamSource': 'tushare'}
        assert CAPABILITY_SOURCES.resolve(key, target) is binding
        revision = market_v3_current_route_revisions(key, target, PROVIDER_MANIFESTS['tushare'],
            credential_revision=_provider_credential_revision_from_snapshot(snapshot()))
        assert revision['adapterRevision'] == f"dsa-v3-{key['market'].lower()}-stock-tushare-none-exact-daily-v1"
        assert revision['sourceRevision'] == PROTOCOL and binding.maximum_rows is None


@pytest.mark.parametrize('probe,timeout', [(False, 30), (True, 5)])
@pytest.mark.parametrize('key_kind', ['CN', 'HK', 'ETF', 'INDEX', 'quote'])
def test_factory_budget_and_pinning_for_migrated_and_legacy_capabilities(store, probe, timeout, key_kind):
    key = SOURCE.bindings[0].key.route_key()
    if key_kind == 'HK':
        key = {**key, 'market': 'HK'}
    elif key_kind in {'ETF', 'INDEX'}:
        key = {**key, 'assetType': key_kind}
    elif key_kind == 'quote':
        key = {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'REALTIME_QUOTE'}
    adapter = ThesisLedgerProviderRuntime(store)._adapter('tushare', snapshot(), cache=False, probe=probe,
        market_v3_route_key=key)
    assert adapter._request_timeout == timeout and adapter._api._timeout == timeout
    assert adapter._api._source_pinned is True and adapter._api._api_url == 'https://fixture.example/api'


def test_original_cache_retains_counter_and_rotates_token_endpoint(store):
    runtime = ThesisLedgerProviderRuntime(store)
    key = SOURCE.bindings[0].key.route_key()
    first = runtime._adapter('tushare', snapshot(), market_v3_route_key=key)
    first._call_count = 17
    assert runtime._adapter('tushare', snapshot(), market_v3_route_key=key) is first
    assert runtime._adapter('tushare', snapshot(), market_v3_route_key=SOURCE.bindings[1].key.route_key())._call_count == 17
    second = runtime._adapter('tushare', snapshot(token='synthetic-rotated'), market_v3_route_key=key)
    assert second is not first and first._token == 'synthetic'
    third = runtime._adapter('tushare', snapshot(token='synthetic-rotated', endpoint='https://other.example/api'), market_v3_route_key=key)
    assert third is not second and third._api._api_url == 'https://other.example/api'
    probe = runtime._adapter('tushare', snapshot(token='synthetic-rotated', endpoint='https://other.example/api'),
        market_v3_route_key=key, cache=False, probe=True)
    assert probe is not third and probe._request_timeout == 5 and runtime.adapters['tushare'] is third


def test_missing_credential_revision_never_publishes_or_hits_cached_factory(monkeypatch, store):
    runtime = ThesisLedgerProviderRuntime(store)
    key = SOURCE.bindings[0].key.route_key()
    cached = runtime._adapter('tushare', snapshot(), market_v3_route_key=key)
    monkeypatch.delenv('THESIS_LEDGER_DSA_SECRET_KEY')
    monkeypatch.delenv('DSA_SECRET_KEY', raising=False)
    first = runtime._adapter('tushare', snapshot(), market_v3_route_key=key)
    second = runtime._adapter('tushare', snapshot(), market_v3_route_key=key)
    assert first is not second and first is not cached and runtime.adapters['tushare'] is cached


@pytest.mark.parametrize('market,symbol,endpoint', [('CN', '600519.SH', 'daily'), ('HK', '00700.HK', 'hk_daily')])
def test_actual_factory_control_runtime_http_and_single_response(monkeypatch, store, market, symbol, endpoint):
    monkeypatch.delenv('TUSHARE_HTTP_URL', raising=False)
    calls = []
    def post(url, *, json, timeout, allow_redirects):
        calls.append((url, json, timeout, allow_redirects))
        return SimpleNamespace(status_code=200, text=json_module.dumps({'code': 0, 'data': {
            'fields': ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol'],
            'items': [[symbol, '20260518', 1., 1.2, .9, 1.1, 100.]],
        }}))
    monkeypatch.setattr('data_provider.tushare_fetcher.requests.post', post)
    store.save_provider_config('tushare', {'requestId': 'fixture',
        'credentials': {'method': 'token', 'values': {'token': 'synthetic'}}})
    key = {**SOURCE.bindings[0].key.route_key(), 'market': market}
    target = {'providerId': 'tushare', 'upstreamSource': 'tushare'}
    assert apply(store, key, target)['effective']['routes'][0]['targets'][0]['eligible']
    result = ThesisLedgerProviderRuntime(store).execute_market_bars_v3(request(symbol), key)
    assert result.provider == 'tushare' and not result.fallback_used
    assert result.value['date'].tolist() == ['2026-05-18'] and result.value.iloc[0]['amount'] is None
    assert len(calls) == 1 and calls[0][0] == 'http://api.tushare.pro'
    assert calls[0][1] == {'api_name': endpoint, 'token': 'synthetic', 'fields': '',
        'params': {'ts_code': symbol, 'start_date': '20260518', 'end_date': '20260518'}}
    assert 0 < calls[0][2] <= 4.5 and calls[0][3] is False
    pagination = result.value.attrs['thesis_ledger_v3_pagination']
    assert pagination['pagesFetched'] == 1 and pagination['protocol'] == PROTOCOL
    no_admissions(store)


@pytest.mark.parametrize('failure,code', [('empty', 'not_covered'), ('missing-symbol', 'invalid_response'),
    ('wrong-symbol', 'invalid_response')])
def test_identity_and_empty_failures_remain_classified(failure, code):
    raw = pd.DataFrame({'ts_code': ['600519.SH'], 'trade_date': ['20260518'],
        'open': [1.], 'high': [1.2], 'low': [.9], 'close': [1.1]})
    if failure == 'empty':
        raw = raw.iloc[0:0]
    elif failure == 'missing-symbol':
        raw = raw.drop(columns='ts_code')
    elif failure == 'wrong-symbol':
        raw['ts_code'] = '000001.SZ'
    adapter = SimpleNamespace(_token='synthetic', _api=object(), _minute_start=None,
        _check_rate_limit=lambda: None, _build_api_client=lambda _: SimpleNamespace(query=lambda *a, **kw: raw))
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.daily(request(), SOURCE.bindings[0].key.route_key(),
            {'providerId': 'tushare', 'upstreamSource': 'tushare'}, ReadContext(adapter))
    assert error.value.code == code


def test_rate_limit_fails_without_http_or_wait(monkeypatch, store):
    adapter = ThesisLedgerProviderRuntime(store)._adapter('tushare', snapshot(), cache=False)
    adapter._call_count = adapter.rate_limit_per_minute
    adapter._minute_start = time.time()
    monkeypatch.setattr('data_provider.tushare_fetcher.requests.post', lambda *a, **kw: pytest.fail('限流不应请求'))
    monkeypatch.setattr('data_provider.tushare_fetcher.time.sleep', lambda *a: pytest.fail('精确读取不应等待额度'))
    with pytest.raises(RateLimitError):
        SOURCE.bindings[0].read(DailyQuery.from_request(request()), ReadContext(adapter))
