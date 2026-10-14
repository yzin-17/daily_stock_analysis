"""公开单位由来源投影，原HTTP失败时机和未知保持。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.thesis_ledger import router_v3
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_quote_units_contract import QuotePublicUnits
from data_provider.thesis_ledger_sources.hithink import SOURCE
import api.thesis_ledger_quote_units as units_api


def quote(source='hithink/a-share-prices-snapshot', units=None):
    return SimpleNamespace(price=10.0, pre_close=9.0, open_price=9.5, high=10.5, low=9.0, volume=None, amount=None,
        fetched_at='2026-10-12T00:00:00Z', provider_timestamp=None, source=source, units=units)


def client(monkeypatch, value, provider='hithink'):
    calls = []
    def read(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return SimpleNamespace(data=value, provider=provider, fallback_used=False)
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'quote-unit-test-token')
    monkeypatch.setattr('src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway',
                        lambda: SimpleNamespace(quote=read))
    app = FastAPI()
    app.include_router(router_v3, prefix='/api/v3')
    return TestClient(app), calls


@pytest.mark.parametrize(('source', 'volume', 'turnover'), [('a-share-prices-snapshot', 'share', 'CNY'),
                                                         ('fund-market-snapshot', 'unknown', 'unknown')])
def test_original_units_are_projected_after_one_gateway_read(monkeypatch, source, volume, turnover):
    raw = {'price_currency': 'CNY', 'volume': volume, 'turnover_currency': turnover}
    value = quote('hithink/' + source, raw)
    api, calls = client(monkeypatch, value)
    with api:
        result = api.get('/api/v3/thesis-ledger/market/quote', params={'symbol': '600519.SH'},
                         headers={'Authorization': 'Bearer quote-unit-test-token'})
    assert result.status_code == 200, result.text
    assert result.json()['units'] == {'priceCurrency': 'CNY', 'volume': volume, 'turnoverCurrency': turnover}
    assert len(calls) == 1 and value.units is raw


@pytest.mark.parametrize('bad', [None, {}, {'price_currency': 'CNY', 'volume': 'unknown', 'turnover_currency': 'unknown'},
                               {'price_currency': 'CNY', 'volume': 'share', 'turnover_currency': 'CNY', 'sdkExtra': True}])
def test_bad_stock_units_preserve_original_http_error_without_another_read(monkeypatch, bad):
    api, calls = client(monkeypatch, quote(units=bad))
    with api:
        result = api.get('/api/v3/thesis-ledger/market/quote', params={'symbol': '600519.SH'},
                         headers={'Authorization': 'Bearer quote-unit-test-token'})
    assert result.status_code == 502
    detail = result.json()['detail']
    assert set(detail) == {'contractVersion', 'code', 'message', 'requestId', 'diagnosticId'}
    assert detail['contractVersion'] == 3 and detail['code'] == 'upstream_invalid_response'
    assert detail['message'] == 'HiThink 报价单位合同无效'
    assert detail['requestId'] and detail['diagnosticId'] == detail['requestId']
    assert len(calls) == 1


def test_undeclared_source_keeps_original_omission(monkeypatch):
    api, calls = client(monkeypatch, quote(units={'sdkData': 'opaque'}), provider='tencent')
    with api:
        result = api.get('/api/v3/thesis-ledger/market/quote', params={'symbol': '600519.SH'},
                         headers={'Authorization': 'Bearer quote-unit-test-token'})
    assert result.status_code == 200 and 'units' not in result.json() and len(calls) == 1


def test_new_source_only_declares_projection_and_uses_original_http(monkeypatch, tmp_path):
    from src.services.thesis_ledger_capability_registry import SourceDefinition, QuoteBinding, ExactKey
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    from src.services.provider_manifest import build_provider_manifest
    from src.services.thesis_ledger_control import ThesisLedgerControlStore, PROVIDER_MANIFESTS
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime, ThesisLedgerDataGateway
    seen, calls = [], []
    value = quote('tencent', {'priceTag': 'CNY', 'quantityTag': 'share', 'valueTag': 'CNY'})
    def read(request, context):
        calls.append(request.symbol)
        return value
    def project(context):
        assert context.upstream_source == 'tencent'
        assert not hasattr(context, 'runtime') and not hasattr(context, 'adapter')
        seen.append(context)
        return QuotePublicUnits(priceCurrency=context.value.units['priceTag'], volume=context.value.units['quantityTag'],
                                turnoverCurrency=context.value.units['valueTag'])
    def manifest():
        value = build_provider_manifest('unit-source', '隔离单位来源', {'REALTIME_QUOTE': ('STOCK',)},
            upstream_sources=(('tencent', '隔离报价上游'),), configuration_mode='built_in')
        value['upstreamSources'][0]['capabilities'] = {'REALTIME_QUOTE': ['STOCK']}
        return value
    source = SourceDefinition('unit-source',
        manifest,
        lambda context: object(), (QuoteBinding(ExactKey('CN', 'STOCK', 'REALTIME_QUOTE'), 'tencent',
            'unit-source-adapter-v1', 'unit-source-contract-v1', read),), quote_public_units=project,
        quote_source_aliases=(('tencent', 'tencent'),))
    registry = CapabilityRegistry(CAPABILITY_SOURCES.sources + (source,))
    monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, '_bindings', registry._bindings)
    monkeypatch.setitem(PROVIDER_MANIFESTS, 'unit-source', source.manifest())
    store = ThesisLedgerControlStore(str(tmp_path / 'unit-source.sqlite'))
    store.apply_policy_v3({'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'unit-source-policy',
        'revision': 1, 'enabled': True, 'routes': [{'key': source.bindings[0].key.route_key(),
        'targets': [{'providerId': 'unit-source', 'upstreamSource': 'tencent'}]}]})
    gateway = ThesisLedgerDataGateway(ThesisLedgerProviderRuntime(store))
    monkeypatch.setattr('src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway', lambda: gateway)
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'quote-unit-test-token')
    app = FastAPI()
    app.include_router(router_v3, prefix='/api/v3')
    with TestClient(app) as api:
        result = api.get('/api/v3/thesis-ledger/market/quote', params={'symbol': '600519.SH'},
                         headers={'Authorization': 'Bearer quote-unit-test-token'})
    assert result.status_code == 200, result.text
    assert result.json()['provider'] == 'unit-source' and result.json()['units']['volume'] == 'share'
    assert len(calls) == 1 and len(seen) == 1


@pytest.mark.parametrize('value', [None, {}, {'priceCurrency': 'CNY', 'volume': 'share', 'turnoverCurrency': 'CNY'}])
def test_untyped_result_is_rejected_after_gateway(monkeypatch, value):
    registry = CapabilityRegistry((replace(SOURCE, provider_id='unit-source', quote_public_units=lambda ctx: value),))
    monkeypatch.setattr(units_api, 'CAPABILITY_SOURCES', registry)
    api, calls = client(monkeypatch, quote(), provider='unit-source')
    with api:
        result = api.get('/api/v3/thesis-ledger/market/quote', params={'symbol': '600519.SH'},
                         headers={'Authorization': 'Bearer quote-unit-test-token'})
    assert result.status_code == 502 and result.json()['detail']['code'] == 'upstream_invalid_response'
    assert len(calls) == 1


def test_schema_and_registration_reject_invalid_units():
    with pytest.raises(ValidationError):
        QuotePublicUnits(priceCurrency='CNY', volume='lot', turnoverCurrency='CNY')
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCE, quote_public_units=True),))


def test_wrong_actual_source_preserves_unit_error(monkeypatch):
    api, calls = client(monkeypatch, quote('hithink/unknown',
        {'price_currency': 'CNY', 'volume': 'share', 'turnover_currency': 'CNY'}))
    with api:
        result = api.get('/api/v3/thesis-ledger/market/quote', params={'symbol': '600519.SH'},
                         headers={'Authorization': 'Bearer quote-unit-test-token'})
    assert result.status_code == 502 and result.json()['detail']['message'] == 'HiThink 报价单位合同无效'
    assert len(calls) == 1


def test_constructed_invalid_units_cannot_bypass_schema(monkeypatch):
    invalid = QuotePublicUnits.model_construct(priceCurrency='CNY', volume='lot', turnoverCurrency='CNY')
    registry = CapabilityRegistry((replace(SOURCE, provider_id='unit-source', quote_public_units=lambda ctx: invalid),))
    monkeypatch.setattr(units_api, 'CAPABILITY_SOURCES', registry)
    api, calls = client(monkeypatch, quote(), provider='unit-source')
    with api:
        result = api.get('/api/v3/thesis-ledger/market/quote', params={'symbol': '600519.SH'},
                         headers={'Authorization': 'Bearer quote-unit-test-token'})
    assert result.status_code == 502 and result.json()['detail']['code'] == 'upstream_invalid_response'
    assert len(calls) == 1
