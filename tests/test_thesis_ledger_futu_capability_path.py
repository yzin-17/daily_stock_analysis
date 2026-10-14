"""Futu 原工厂、隔离协议与标准日线闭环；执行次数不冒充 SDK 页数。"""

from contextlib import redirect_stdout
import io
from importlib.util import find_spec
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.thesis_ledger_sources.futu import SOURCE, PROTOCOL
from data_provider.futu_market import FutuMarketAdapter, futu_security
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions
from tests.test_futu_daily import row, sdk_for


def configure(store, port='11111'):
    store.save_provider_config('futu', {'requestId': 'synthetic', 'enabled': True,
        'credentials': {'method': 'opend', 'values': {'host': 'localhost', 'port': port}}})


def query(symbol, asset, adjustment):
    return ThesisLedgerDataRequest('DAILY_BAR', symbol, instrument_type=asset, timeframe='1d',
        adjustment=adjustment, start='2026-05-18', end='2026-05-19')


def worker_transport(monkeypatch, sdk):
    from data_provider.futu_query_worker import main
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        output = io.StringIO()
        monkeypatch.setattr(sys, 'stdin', io.StringIO(kwargs['input']))
        with redirect_stdout(output):
            main()
        return SimpleNamespace(returncode=0, stdout=output.getvalue())
    sdk.__spec__ = find_spec('futu')
    monkeypatch.setitem(sys.modules, 'futu', sdk)
    monkeypatch.setattr(subprocess, 'run', run)
    return calls


def test_twelve_bindings_have_one_registration_and_unchanged_revisions():
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    from src.services.thesis_ledger_native_daily import NATIVE_DAILY_ROUTES
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
    targets = [(k, t, gated) for k, t, gated in exact_route_inventory()
        if t['providerId'] == 'futu' and k['capability'] == 'DAILY_BAR']
    daily_bindings = [b for b in SOURCE.bindings if b.key.capability == 'DAILY_BAR']
    assert len(targets) == len(daily_bindings) == 12 and all(gated for _, _, gated in targets)
    assert not NATIVE_DAILY_ROUTES
    assert SOURCE.credentials == (('opend', ('host', 'port')),)
    assert SOURCE.dependency_modules == ('futu',) and SOURCE.factory_scope == 'provider'
    for binding in daily_bindings:
        key, target = binding.key.route_key(), {'providerId': 'futu', 'upstreamSource': 'futu'}
        assert CAPABILITY_SOURCES.resolve(key, target) is binding
        revisions = market_v3_current_route_revisions(key, target, PROVIDER_MANIFESTS['futu'], credential_version=1)
        assert revisions['adapterRevision'] == f"dsa-v3-{key['market'].lower()}-{key['assetType'].lower()}-futu-{key['adjustment']}-exact-daily-v1"
        assert revisions['sourceRevision'] == PROTOCOL


@pytest.mark.parametrize('market,asset,symbol', [('HK', 'STOCK', '00700.HK'), ('HK', 'ETF', '02800.HK'),
    ('US', 'STOCK', 'AAPL.US'), ('US', 'ETF', 'SPY.US')])
@pytest.mark.parametrize('adjustment', ['none', 'qfq', 'hfq'])
def test_twelve_targets_run_original_factory_worker_and_two_sdk_pages(monkeypatch, store, market, asset, symbol, adjustment):
    configure(store)
    code, _, _ = futu_security(symbol)
    sdk, ctx = sdk_for([(0, pd.DataFrame([row(code=code)]), b'next'),
        (0, pd.DataFrame([row('2026-05-19', code=code)]), None)])
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{'code': code, 'stock_type': asset}])
    calls = worker_transport(monkeypatch, sdk)
    key = {'kind': 'bar', 'market': market, 'assetType': asset, 'capability': 'DAILY_BAR', 'timeframe': '1d', 'adjustment': adjustment}
    target = {'providerId': 'futu', 'upstreamSource': 'futu'}
    assert apply(store, key, target)['effective']['routes'][0]['targets'][0]['eligible']
    result = ThesisLedgerProviderRuntime(store).execute_market_bars_v3(query(symbol, asset, adjustment), key)
    assert result.value['date'].tolist() == ['2026-05-18', '2026-05-19']
    assert result.value['volume'].tolist() == [0, 0] and result.value['amount'].tolist() == [None, None]
    ctx.get_stock_basicinfo.assert_called_once_with(market, stock_type=asset, code_list=[code])
    assert result.provider == result.upstream_source == 'futu'
    assert len(calls) == 1 and ctx.request_history_kline.call_count == 2
    command, options = calls[0]
    assert command == [sys.executable, '-m', 'data_provider.futu_query_worker']
    assert options['timeout'] == 4.5 and options['check'] is False
    assert options['capture_output'] is True and options['text'] is True
    assert options['cwd'] == Path(__file__).resolve().parents[1]
    assert json.loads(options['input']) == {'host': 'localhost', 'port': 11111, 'operation': 'daily',
        'symbol': symbol, 'instrumentType': asset, 'adjustment': adjustment, 'start': '2026-05-18', 'end': '2026-05-19'}
    pages = ctx.request_history_kline.call_args_list
    assert pages[0].kwargs['page_req_key'] is None and pages[1].kwargs['page_req_key'] == b'next'
    assert all(c.args == (code,) and c.kwargs['autype'] == ('NONE' if adjustment == 'none' else adjustment.upper())
        and c.kwargs['max_count'] == 1000 and c.kwargs['extended_time'] is False for c in pages)
    assert result.value.attrs['thesis_ledger_v3_pagination']['pagesFetched'] == 1
    assert result.value.attrs['thesis_ledger_v3_pagination']['protocol'] == PROTOCOL
    ctx.close.assert_called_once()
    no_admissions(store)


@pytest.mark.parametrize('failure,code', [
    ('permission', 'upstream_failure'), ('cursor', 'invalid_response'),
    ('identity', 'invalid_response'), ('window', 'invalid_response'), ('ceiling', 'insufficient_coverage'),
])
def test_late_sdk_failure_releases_and_returns_original_error_without_prefix(monkeypatch, store, failure, code):
    configure(store)
    if failure == 'ceiling':
        pages = [(0, pd.DataFrame([row()]), str(n).encode()) for n in range(64)]
    else:
        second = (0, pd.DataFrame([row('2026-05-19')]), None)
        if failure == 'permission':
            second = (-1, 'no permission', None)
        elif failure == 'cursor':
            second = (0, second[1], b'next')
        elif failure == 'identity':
            second = (0, pd.DataFrame([row('2026-05-19', code='HK.00147')]), None)
        elif failure == 'window':
            second = (0, pd.DataFrame([row('2026-05-20')]), None)
        pages = [(0, pd.DataFrame([row()]), b'next'), second]
    sdk, ctx = sdk_for(pages)
    calls = worker_transport(monkeypatch, sdk)
    key = SOURCE.bindings[0].key.route_key()
    apply(store, key, {'providerId': 'futu', 'upstreamSource': 'futu'})
    with pytest.raises(ProviderCallError) as error:
        ThesisLedgerProviderRuntime(store).execute_market_bars_v3(query('00700.HK', 'STOCK', 'none'), key)
    assert error.value.code == code and len(calls) == 1
    assert ctx.request_history_kline.call_count == (64 if failure == 'ceiling' else 2)
    ctx.close.assert_called_once()
    no_admissions(store)


@pytest.mark.parametrize('failure,code', [('timeout', 'timeout'), ('invalid-json', 'invalid_response'),
    ('process-exit', 'upstream_failure'), ('sdk-missing', 'dependency_missing')])
def test_original_process_errors_are_preserved_and_not_retried(monkeypatch, store, failure, code):
    from data_provider.futu_market import RESULT_PREFIX
    configure(store)
    calls = []
    def run(command, **kwargs):
        calls.append(kwargs)
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        if failure == 'invalid-json':
            return SimpleNamespace(returncode=0, stdout=RESULT_PREFIX + '{broken')
        if failure == 'process-exit':
            return SimpleNamespace(returncode=1, stdout='')
        return SimpleNamespace(returncode=0, stdout=RESULT_PREFIX + json.dumps({'error': code, 'message': 'synthetic'}))
    monkeypatch.setattr(subprocess, 'run', run)
    key = SOURCE.bindings[0].key.route_key()
    apply(store, key, {'providerId': 'futu', 'upstreamSource': 'futu'})
    with pytest.raises(ProviderCallError) as error:
        ThesisLedgerProviderRuntime(store).execute_market_bars_v3(query('00700.HK', 'STOCK', 'none'), key)
    assert error.value.code == code and len(calls) == 1 and calls[0]['timeout'] == 4.5
    no_admissions(store)


def test_provider_factory_cache_rotation_and_probe_keep_lot_cache_isolated(store):
    configure(store)
    runtime = ThesisLedgerProviderRuntime(store)
    key = SOURCE.bindings[0].key.route_key()
    first = runtime._adapter('futu', market_v3_route_key=key)
    assert isinstance(first, FutuMarketAdapter) and first.port == 11111
    first._cache['synthetic'] = object()
    assert runtime._adapter('futu', market_v3_route_key=SOURCE.bindings[-1].key.route_key()) is first
    probe = runtime._adapter('futu', market_v3_route_key=key, cache=False, probe=True)
    assert probe is not first and not probe._cache and runtime.adapters['futu'] is first
    configure(store, '11112')
    replacement = runtime._adapter('futu', market_v3_route_key=key)
    assert replacement is not first and replacement.port == 11112 and not replacement._cache
    assert 'synthetic' in first._cache


@pytest.mark.parametrize('market,asset,symbol', [('HK', 'STOCK', '00700.HK'), ('US', 'ETF', 'SPY.US')])
def test_read_only_exact_probe_uses_same_binding_and_does_not_publish_adapter(monkeypatch, store, market, asset, symbol):
    from src.services.thesis_ledger_exact_probe_read import read_exact_probe
    configure(store)
    snapshot = store.provider_credential_snapshot('futu')
    key = next(b.key.route_key() for b in SOURCE.bindings
        if b.key.market == market and b.key.asset_type == asset and b.key.adjustment == 'qfq')
    code, _, _ = futu_security(symbol)
    sdk, ctx = sdk_for([(0, pd.DataFrame([row(code=code)]), b'next'),
        (0, pd.DataFrame([row('2026-05-19', code=code)]), None)])
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{'code': code, 'stock_type': asset}])
    calls = worker_transport(monkeypatch, sdk)
    monkeypatch.setattr('src.services.thesis_ledger_exact_probe_read.probe_sample',
        lambda key: (symbol, '2026-05-18', '2026-05-19'))
    runtime = ThesisLedgerProviderRuntime(store)
    normal = runtime._adapter('futu', market_v3_route_key=key)
    result = read_exact_probe(runtime, key, {'providerId': 'futu', 'upstreamSource': 'futu'}, snapshot)
    assert result == {'symbol': symbol, 'start': '2026-05-18', 'end': '2026-05-19'}
    assert len(calls) == 1 and calls[0][1]['timeout'] == 4.5
    assert ctx.request_history_kline.call_count == 2
    ctx.close.assert_called_once()
    assert runtime.adapters['futu'] is normal
    assert store.provider_credential_snapshot('futu') == snapshot
    no_admissions(store)


def test_exact_probe_preserves_later_page_error_and_releases(monkeypatch, store):
    from src.services.thesis_ledger_exact_probe_read import read_exact_probe
    configure(store)
    snapshot = store.provider_credential_snapshot('futu')
    sdk, ctx = sdk_for([(0, pd.DataFrame([row()]), b'next'), (-1, 'no permission', None)])
    calls = worker_transport(monkeypatch, sdk)
    monkeypatch.setattr('src.services.thesis_ledger_exact_probe_read.probe_sample',
        lambda key: ('00700.HK', '2026-05-18', '2026-05-19'))
    runtime = ThesisLedgerProviderRuntime(store)
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(runtime, SOURCE.bindings[0].key.route_key(),
            {'providerId': 'futu', 'upstreamSource': 'futu'}, snapshot)
    assert error.value.code == 'upstream_failure' and len(calls) == 1
    assert not runtime.adapters
    ctx.close.assert_called_once()
    assert store.provider_credential_snapshot('futu') == snapshot
    no_admissions(store)


def test_retired_native_inventory_cannot_be_restored(monkeypatch):
    from scripts import check_thesis_ledger_capability_boundaries as boundary
    monkeypatch.setattr(boundary, 'NATIVE_DAILY_ROUTES', {('CN', 'STOCK', 'other', 'other'): ('none',)})
    assert any('退役 Native 日线库存不得重新登记' in error for error in boundary.check())
