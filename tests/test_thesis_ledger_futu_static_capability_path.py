"""Futu 四项当前资料经原工厂/worker/HTTP，保留整数、未知与错误。"""

from datetime import datetime
import json
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.thesis_ledger_static_info import build_static_info_router
from data_provider.thesis_ledger_sources.futu import SOURCE
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions
from tests.test_futu_static_info import static_sdk
from tests.test_thesis_ledger_futu_capability_path import configure, worker_transport


def test_four_static_bindings_keep_catalog_and_admission_semantics():
    from src.services.thesis_ledger_current_data_route import _DATA_ADAPTERS, current_data_adapter_revisions
    from src.services.thesis_ledger_price_route_access import configured_data_route
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    bindings = [b for b in SOURCE.bindings if b.key.capability == 'SECURITY_STATIC_INFO']
    targets = [(k, t, gated) for k, t, gated in exact_route_inventory()
        if k['capability'] == 'SECURITY_STATIC_INFO']
    assert len(bindings) == len(targets) == 4 and all(gated for _, _, gated in targets)
    for binding in bindings:
        key, target = binding.key.route_key(), {'providerId': 'futu', 'upstreamSource': 'futu'}
        assert binding.admission_required is False and configured_data_route(key, target)
        assert (key['market'], key['assetType'], 'SECURITY_STATIC_INFO', 'futu', 'futu') not in _DATA_ADAPTERS
        assert current_data_adapter_revisions(key, target) == {
            'adapterRevision': 'futu-security-static-info-v1', 'sourceRevision': 'futu-single-static-info-v1',
            'credentialRevision': 'not-required'}


@pytest.mark.parametrize('market,asset,symbol,code', [('HK', 'STOCK', '00700.HK', 'HK.00700'),
    ('HK', 'ETF', '02800.HK', 'HK.02800'), ('US', 'STOCK', 'AAPL.US', 'US.AAPL'), ('US', 'ETF', 'SPY.US', 'US.SPY')])
def test_four_targets_run_control_factory_worker_and_typed_http(monkeypatch, store, market, asset, symbol, code):
    configure(store)
    sdk, ctx = static_sdk(code=code, stock_type=asset)
    calls = worker_transport(monkeypatch, sdk)
    key = {'kind': 'data', 'market': market, 'assetType': asset, 'capability': 'SECURITY_STATIC_INFO'}
    target = {'providerId': 'futu', 'upstreamSource': 'futu'}
    assert apply(store, key, target)['effective']['routes'][0]['targets'][0]['eligible']
    runtime = ThesisLedgerProviderRuntime(store)
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'synthetic-static-contract')
    app = FastAPI(); app.include_router(build_static_info_router(lambda: runtime))
    payload = {'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'static-contract', 'symbol': symbol, 'assetType': asset}
    with TestClient(app) as client:
        response = client.post('/market/security-static-info', json=payload,
            headers={'authorization': 'Bearer synthetic-static-contract'})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['securityId'] == '9007199254740993' and value['sharesPerLot'] == '100'
    assert value['symbol'] == symbol and value['market'] == market and value['assetType'] == asset
    assert value['delisted'] is False and value['historicalChanges'] == 'unverified'
    assert value['provider'] == 'futu' and datetime.fromisoformat(value['observedAt']).utcoffset() is not None
    assert value['routeTarget'] == {'providerId': 'futu', 'upstreamSource': 'futu', 'routeIndex': 0}
    assert len(calls) == 1 and calls[0][1]['timeout'] == 4.5
    assert json.loads(calls[0][1]['input']) == {'host': 'localhost', 'port': 11111,
        'operation': 'static-info', 'symbol': symbol, 'instrumentType': asset}
    ctx.get_stock_basicinfo.assert_called_once_with(market, stock_type=asset, code_list=[code])
    ctx.close.assert_called_once()
    no_admissions(store)


def test_missing_fields_remain_unknown_in_standard_result(monkeypatch, store):
    configure(store)
    sdk, ctx = static_sdk(name='', stock_id=None, lot_size=None, listing_date='', delisting=None, exchange_type=None)
    worker_transport(monkeypatch, sdk)
    key = {'kind': 'data', 'market': 'HK', 'assetType': 'STOCK', 'capability': 'SECURITY_STATIC_INFO'}
    apply(store, key, {'providerId': 'futu', 'upstreamSource': 'futu'})
    value = ThesisLedgerProviderRuntime(store).execute_request(
        ThesisLedgerDataRequest('SECURITY_STATIC_INFO', '00700.HK', instrument_type='STOCK')).value
    assert all(value[f] is None for f in ('name', 'securityId', 'sharesPerLot', 'listingDate', 'delisted', 'exchange'))
    assert value['historicalChanges'] == 'unverified'
    ctx.close.assert_called_once()


def test_timeout_keeps_retryable_flag_without_hidden_retry(monkeypatch, store):
    configure(store)
    calls = []
    def run(command, **options):
        calls.append(options)
        raise subprocess.TimeoutExpired(command, options['timeout'])
    monkeypatch.setattr(subprocess, 'run', run)
    key = {'kind': 'data', 'market': 'HK', 'assetType': 'STOCK', 'capability': 'SECURITY_STATIC_INFO'}
    apply(store, key, {'providerId': 'futu', 'upstreamSource': 'futu'})
    with pytest.raises(ProviderCallError) as error:
        ThesisLedgerProviderRuntime(store).execute_request(
            ThesisLedgerDataRequest('SECURITY_STATIC_INFO', '00700.HK', instrument_type='STOCK'))
    assert error.value.code == 'timeout' and error.value.retryable is True
    assert len(calls) == 1 and calls[0]['timeout'] == 4.5
    no_admissions(store)
