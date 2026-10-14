"""Futu 四项精确报价标准绑定：原 worker、时钟、量额与错误隔离。"""

import json

import pandas as pd
import pytest

from data_provider.thesis_ledger_sources.futu import SOURCE
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions
from tests.test_futu_market import sdk_for
from tests.test_thesis_ledger_futu_capability_path import configure, worker_transport


def test_four_quote_bindings_replace_central_entries_with_identical_revisions():
    from src.services.thesis_ledger_current_data_route import _DATA_ADAPTERS, current_data_adapter_revisions
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    bindings = [b for b in SOURCE.bindings if b.key.capability == 'REALTIME_QUOTE']
    targets = [(k, t, gated) for k, t, gated in exact_route_inventory()
        if t['providerId'] == 'futu' and k['capability'] == 'REALTIME_QUOTE']
    assert len(bindings) == len(targets) == 4
    for binding in bindings:
        key, target = binding.key.route_key(), {'providerId': 'futu', 'upstreamSource': 'futu'}
        assert CAPABILITY_SOURCES.resolve(key, target) is binding
        assert (key['market'], key['assetType'], 'REALTIME_QUOTE', 'futu', 'futu') not in _DATA_ADAPTERS
        assert current_data_adapter_revisions(key, target) == {
            'adapterRevision': f"futu-{key['market'].lower()}-{key['assetType'].lower()}-exact-quote-v1",
            'sourceRevision': 'futu-direct-quote-v1', 'credentialRevision': 'not-required'}


@pytest.mark.parametrize('market,asset,symbol,code,expected', [
    ('HK', 'STOCK', '00700.HK', 'HK.00700', '2026-07-01T02:00:00+00:00'),
    ('HK', 'ETF', '02800.HK', 'HK.02800', '2026-07-01T02:00:00+00:00'),
    ('US', 'STOCK', 'AAPL.US', 'US.AAPL', '2026-07-01T14:00:00+00:00'),
    ('US', 'ETF', 'SPY.US', 'US.SPY', '2026-07-01T14:00:00+00:00'),
])
def test_four_quotes_run_control_factory_worker_single_security_and_standard_clock(
        monkeypatch, store, market, asset, symbol, code, expected):
    configure(store)
    sdk, ctx = sdk_for(code, asset)
    calls = worker_transport(monkeypatch, sdk)
    key = {'kind': 'data', 'market': market, 'assetType': asset, 'capability': 'REALTIME_QUOTE'}
    target = {'providerId': 'futu', 'upstreamSource': 'futu'}
    assert apply(store, key, target)['effective']['routes'][0]['targets'][0]['eligible']
    result = ThesisLedgerProviderRuntime(store).execute_request(ThesisLedgerDataRequest('REALTIME_QUOTE', symbol, instrument_type=asset))
    value = result.value
    assert value.price == 12 and value.pre_close == 11 and value.open_price == 11 and value.high == 13 and value.low == 10
    assert value.volume == 0 and value.amount is None and value.units is None
    assert value.provider_timestamp == expected and value.fetched_at != expected
    assert value.source == result.provider == result.upstream_source == 'futu'
    assert len(calls) == 1 and calls[0][1]['timeout'] == 4.5
    assert json.loads(calls[0][1]['input']) == {'host': 'localhost', 'port': 11111,
        'operation': 'quote', 'symbol': symbol, 'instrumentType': asset}
    ctx.get_market_snapshot.assert_called_once_with([code])
    ctx.close.assert_called_once()
    no_admissions(store)


@pytest.mark.parametrize('changes,expected', [({'update_time': None, 'volume': None}, None),
    ({'update_time': '2026-01-06 10:00:00'}, '2026-01-06T15:00:00+00:00')])
def test_missing_clock_and_volume_stay_unknown_and_winter_clock_is_source_time(monkeypatch, store, changes, expected):
    configure(store)
    sdk, ctx = sdk_for('US.AAPL', **changes)
    calls = worker_transport(monkeypatch, sdk)
    key = {'kind': 'data', 'market': 'US', 'assetType': 'STOCK', 'capability': 'REALTIME_QUOTE'}
    apply(store, key, {'providerId': 'futu', 'upstreamSource': 'futu'})
    result = ThesisLedgerProviderRuntime(store).execute_request(ThesisLedgerDataRequest('REALTIME_QUOTE', 'AAPL.US', instrument_type='STOCK'))
    assert result.value.provider_timestamp == expected and result.value.fetched_at
    if 'volume' in changes:
        assert result.value.volume is None
    assert len(calls) == 1
    ctx.close.assert_called_once()


@pytest.mark.parametrize('failure,code', [('identity', 'invalid_response'), ('permission', 'upstream_failure'),
    ('bad-clock', 'invalid_response'), ('bad-price', 'invalid_response')])
def test_quote_errors_release_without_hidden_retry_or_other_source(monkeypatch, store, failure, code):
    configure(store)
    changes = {}
    if failure == 'bad-clock':
        changes['update_time'] = 'bad'
    elif failure == 'bad-price':
        changes['last_price'] = True
    sdk, ctx = sdk_for(**changes)
    if failure == 'identity':
        ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{'code': 'HK.00700', 'stock_type': 'ETF'}])
    elif failure == 'permission':
        ctx.get_market_snapshot.return_value = -1, 'no permission'
    calls = worker_transport(monkeypatch, sdk)
    key = {'kind': 'data', 'market': 'HK', 'assetType': 'STOCK', 'capability': 'REALTIME_QUOTE'}
    apply(store, key, {'providerId': 'futu', 'upstreamSource': 'futu'})
    with pytest.raises(ProviderCallError) as error:
        ThesisLedgerProviderRuntime(store).execute_request(ThesisLedgerDataRequest('REALTIME_QUOTE', '00700.HK', instrument_type='STOCK'))
    assert error.value.code == code and len(calls) == 1
    ctx.close.assert_called_once()
    no_admissions(store)


def test_wrong_upstream_cannot_fall_through_to_sdk(monkeypatch, store):
    configure(store)
    runtime = ThesisLedgerProviderRuntime(store)
    adapter = runtime._adapter('futu')
    monkeypatch.setattr(adapter, 'get_realtime_quote', lambda *args, **kwargs: pytest.fail('不匹配的来源不能读取'))
    with pytest.raises(ProviderCallError) as error:
        runtime._realtime_quote(adapter, 'futu', '00700.HK', 'STOCK', 'other')
    assert error.value.code == 'unsupported_source'


def test_exact_quote_probe_uses_standard_symbol_and_preserves_cached_adapter(monkeypatch, store):
    from src.services.thesis_ledger_exact_probe_read import read_exact_probe
    configure(store)
    snapshot = store.provider_credential_snapshot('futu')
    sdk, ctx = sdk_for()
    calls = worker_transport(monkeypatch, sdk)
    monkeypatch.setattr('src.services.thesis_ledger_exact_probe_read.probe_sample',
        lambda key: ('00700.HK', '2026-07-01', '2026-07-01'))
    runtime = ThesisLedgerProviderRuntime(store)
    normal = runtime._adapter('futu')
    key = {'kind': 'data', 'market': 'HK', 'assetType': 'STOCK', 'capability': 'REALTIME_QUOTE'}
    assert read_exact_probe(runtime, key, {'providerId': 'futu', 'upstreamSource': 'futu'}, snapshot) == {'symbol': '00700.HK'}
    assert json.loads(calls[0][1]['input'])['symbol'] == '00700.HK'
    assert runtime.adapters['futu'] is normal and store.provider_credential_snapshot('futu') == snapshot
    ctx.close.assert_called_once()
    no_admissions(store)
