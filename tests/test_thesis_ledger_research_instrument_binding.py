"""有界身份/单位复用原事实入口，来源差异不进入公共分派。"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib

import pytest
from pydantic import ValidationError

from data_provider.thesis_ledger_sources.yfinance import SOURCE
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_dependency_facts import DependencyFactError, instrument_facts_response
from src.services import thesis_ledger_dependency_facts as facts
from src.services import thesis_ledger_reference_identity as identity
from src.services.thesis_ledger_research_instrument_contract import ResearchInstrumentFact, ResearchInstrumentPolicy

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def key(market='HK'):
    return {'kind': 'bar', 'market': market, 'assetType': 'STOCK', 'capability': 'DAILY_BAR', 'timeframe': '1d', 'adjustment': 'none'}


def fact():
    return {'symbol': '00147.HK', 'market': 'HK', 'instrumentType': 'STOCK', 'currency': 'HKD',
            'lotSize': '5000', 'tickSize': '0.001', 'tradable': False,
            'executionRules': {'status': 'unavailable', 'reason': '原研究观察'},
            'provider': 'new-units', 'providerRevision': 'original-source-revision',
            'occurredAt': '2026-10-08T08:57:12.937458+00:00', 'availableAt': '2026-10-08T08:57:12.937458+00:00'}


def read(symbol='00147.HK', market='HK', target=None, start='2025-08-06', end='2025-08-11', data_as_of=NOW):
    return instrument_facts_response(symbol, data_as_of, start, end, start, end, [], instrument_type='STOCK',
                                    route_key=key(market), route_target=target, identity_only=True, market=market)


@pytest.mark.parametrize(('market', 'module'), [('HK', 'data_provider.yahoo_hk_share_window'), ('US', 'data_provider.yahoo_share_window')])
def test_original_sources_keep_complete_fact_and_original_error(market, module):
    source = importlib.import_module(module)
    pack, _, observed, revision = source.load_share_window()
    symbol, window = pack['symbol'], pack['coverage']
    target = {'providerId': 'yfinance', 'upstreamSource': source.SOURCE, 'routeIndex': 0}
    assert identity.validate_instrument_request(symbol, market, 'STOCK', lambda s: s, True, 'none', 'yfinance', source.SOURCE) == symbol
    result = read(symbol, market, target, window['start'], window['end'])
    expected = source.share_window_instrument_fact(symbol, window['start'], window['end'], NOW)
    assert result['facts'] == [expected] and result['providerRevision'] == revision
    assert result['provider'] == source.SOURCE and result['status'] == 'supported'
    assert result['coverage'] == {**window, 'complete': True}
    with pytest.raises(DependencyFactError) as rejected:
        read(symbol, market, target, window['start'], window['end'], observed - timedelta(seconds=1))
    assert rejected.value.code == 'not_covered'


def new_source(monkeypatch, reader):
    from src.services.provider_manifest import build_provider_manifest
    binding = next(b for k, t, b in CapabilityRegistry((SOURCE,)).routes() if k == key())
    binding = replace(binding, upstream_source='new-units', research_instrument_policy=ResearchInstrumentPolicy(
        supports_symbol=lambda symbol: symbol == '00147.HK', read=reader))
    def factory(context):
        raise AssertionError('身份事实读取不得构建价格SDK工厂')
    registry = CapabilityRegistry((replace(SOURCE, provider_id='new-source', factory=factory,
        manifest=lambda: build_provider_manifest('new-source', '隔离单位来源', {'DAILY_BAR': ('STOCK',)},
            markets=('HK',), upstream_sources=(('new-units', '隔离单位观察'),)), bindings=(binding,)),))
    assert registry.manifests()['new-source']['providerId'] == 'new-source'
    monkeypatch.setattr(identity, 'CAPABILITY_SOURCES', registry)
    monkeypatch.setattr(facts, 'CAPABILITY_SOURCES', registry)
    return {'providerId': 'new-source', 'upstreamSource': 'new-units', 'routeIndex': 0}


def test_new_source_only_declares_policy_and_uses_original_fact_entry(monkeypatch):
    calls = []

    def reader(request):
        assert request.symbol == '00147.HK' and request.market == 'HK'
        assert request.start == '2025-08-06' and request.data_as_of == NOW
        assert not hasattr(request, 'adapter') and not hasattr(request, 'runtime')
        with pytest.raises(ValidationError):
            request.symbol = 'changed'
        calls.append(request)
        return ResearchInstrumentFact.model_validate(fact())

    target = new_source(monkeypatch, reader)
    assert identity.validate_instrument_request('00147.HK', 'HK', 'STOCK', lambda s: s, True, 'none', 'new-source', 'new-units') == '00147.HK'
    result = read(target=target)
    assert result['facts'] == [fact()] and result['provider'] == 'new-units'
    assert len(calls) == 1


@pytest.mark.parametrize('mutation', ['symbol', 'market', 'currency', 'provider', 'clock'])
def test_request_source_and_clock_mismatch_rejected(monkeypatch, mutation):
    value = fact()
    if mutation == 'symbol': value['symbol'] = '00700.HK'
    elif mutation == 'market': value.update(market='US', currency='USD')
    elif mutation == 'currency': value['currency'] = 'USD'
    elif mutation == 'provider': value['provider'] = 'other-units'
    else: value['availableAt'] = '2027-01-01T00:00:00Z'
    target = new_source(monkeypatch, lambda request: ResearchInstrumentFact.model_construct(**value))
    with pytest.raises(DependencyFactError) as rejected:
        read(target=target)
    assert rejected.value.code == 'invalid_response'


@pytest.mark.parametrize(('field', 'value'), [('lotSize', '0'), ('tickSize', '-0.001'), ('lotSize', 5000),
    ('lotSize', None), ('tradable', True), ('tradable', 0), ('availableAt', '2026-10-08T08:57:12'),
    ('executionRules', {'status': 'supported'}), ('providerRevision', '')])
def test_bad_fact_does_not_gain_research_units(field, value):
    with pytest.raises(ValidationError):
        ResearchInstrumentFact.model_validate({**fact(), field: value})


def test_paired_unknown_units_stay_unknown():
    value = ResearchInstrumentFact.model_validate({**fact(), 'lotSize': None, 'tickSize': None})
    assert value.model_dump()['lotSize'] is None and value.model_dump()['tickSize'] is None


@pytest.mark.parametrize('target', [None, {'providerId': 'yfinance', 'upstreamSource': 'yahoo-gme-share-window'},
                                  {'providerId': 'other', 'upstreamSource': 'yahoo-hk-share-window'}])
def test_missing_or_wrong_price_source_rejected(target):
    with pytest.raises(DependencyFactError) as rejected:
        read(target=target)
    assert rejected.value.code == 'unsupported_capability'


def test_untyped_result_is_not_dto_passthrough(monkeypatch):
    target = new_source(monkeypatch, lambda request: fact())
    with pytest.raises(DependencyFactError) as rejected:
        read(target=target)
    assert rejected.value.code == 'invalid_response'


def test_rejected_symbol_does_not_read_source(monkeypatch):
    def forbidden(request):
        raise AssertionError('身份拒绝不得读取')
    target = new_source(monkeypatch, forbidden)
    with pytest.raises(DependencyFactError) as rejected:
        read(symbol='00700.HK', target=target)
    assert rejected.value.code == 'not_covered'


def test_submicrosecond_source_clock_is_not_truncated(monkeypatch):
    value = {**fact(), 'availableAt': '2026-10-12T00:00:00.000000001Z'}
    target = new_source(monkeypatch, lambda request: ResearchInstrumentFact.model_validate(value))
    with pytest.raises(DependencyFactError) as rejected:
        read(target=target)
    assert rejected.value.code == 'invalid_response'


@pytest.mark.parametrize(('market', 'module'), [('HK', 'data_provider.yahoo_hk_share_window'), ('US', 'data_provider.yahoo_share_window')])
def test_authenticated_original_api_reads_original_fact_without_policy_write(monkeypatch, market, module):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.thesis_ledger import router_v3
    source = importlib.import_module(module)
    pack, _, _, _ = source.load_share_window()
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'research-fact-test-token')
    app = FastAPI()
    app.include_router(router_v3, prefix='/api/v3')
    params = {'symbol': pack['symbol'], 'market': market, 'instrumentType': 'STOCK',
              'dataAsOf': NOW.isoformat(), **pack['coverage'], 'executionStart': pack['coverage']['start'],
              'executionEnd': pack['coverage']['end'], 'barAdjustment': 'none', 'barProviderId': 'yfinance',
              'barUpstreamSource': source.SOURCE, 'barRouteIndex': 0, 'identityOnly': 'true'}
    endpoint = '/api/v3/thesis-ledger/backtest/instrument-facts'
    with TestClient(app) as client:
        assert client.get(endpoint, params=params).status_code == 401
        response = client.get(endpoint, params=params, headers={'Authorization': 'Bearer research-fact-test-token'})
        assert response.status_code == 200, response.text
        assert response.json()['facts'] == [source.share_window_instrument_fact(
            pack['symbol'], pack['coverage']['start'], pack['coverage']['end'], NOW)]
        missing = {name: value for name, value in params.items() if name != 'barRouteIndex'}
        assert client.get(endpoint, params=missing, headers={'Authorization': 'Bearer research-fact-test-token'}).status_code == 422


def test_registration_rejects_malformed_policy():
    binding = SOURCE.bindings[0]
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCE, bindings=(replace(binding, research_instrument_policy=True),)),))
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCE, bindings=(replace(binding, research_instrument_policy=ResearchInstrumentPolicy(
            supports_symbol=True, read=lambda request: None)),)),))
