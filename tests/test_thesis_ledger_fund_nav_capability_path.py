"""最新净值标准合同与两种实际 SDK 接缝；完整读取后选点。"""

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest
from pydantic import ValidationError

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_registry import CapabilityRegistry, ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_fund_nav_contract_v1 import CONTRACT, LatestNavRequest, LatestNavResult, NavPoint
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerGatewayError, ThesisLedgerProviderRuntime, ThesisLedgerDataGateway
from tests.test_configured_market_routes import store, apply, no_admissions


KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'MUTUAL_FUND', 'capability': 'FUND_NAV'}


def configured(store, provider):
    target = {'providerId': provider, 'upstreamSource': 'eastmoney'}
    assert apply(store, KEY, target)['effective']['routes'][0]['targets'][0]['eligible']
    return ThesisLedgerProviderRuntime(store)


def test_akshare_actual_factory_and_native_columns_produce_one_standard_point(monkeypatch, store):
    import akshare as ak
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame({'净值日期': ['2026-01-03', '2026-01-01'], '单位净值': [1.3, 1.1]})
    monkeypatch.setattr(ak, 'fund_open_fund_info_em', read)
    result = configured(store, 'akshare').execute_request(ThesisLedgerDataRequest('FUND_NAV', '000001.OF'))
    assert calls == [{'symbol': '000001', 'indicator': '单位净值走势'}]
    assert isinstance(result.value, LatestNavResult) and result.value.point.unit_nav == 1.3
    assert result.value.point.nav_date == '2026-01-03T00:00:00+00:00'
    assert result.value.publication_visibility == 'unverified' and result.value.date_basis == 'valuation_date'
    assert result.value.symbol == '000001.OF' and result.value.upstream_source == 'eastmoney'
    no_admissions(store)


def test_efinance_actual_factory_reads_all_sdk_pages_before_latest(monkeypatch, store):
    from tests.test_eastmoney_fund_nav import install, page
    from data_provider.efinance_fetcher import EfinanceFetcher
    logical = []
    original = EfinanceFetcher.get_fund_nav_history
    def read(self, code):
        logical.append(code)
        return original(self, code)
    monkeypatch.setattr(EfinanceFetcher, 'get_fund_nav_history', read)
    calls = install(monkeypatch, [page(['2026-01-03', '2026-01-02']), page(['2026-01-01'])])
    result = configured(store, 'efinance').execute_request(ThesisLedgerDataRequest('FUND_NAV', '000001'))
    assert logical == ['000001'] and [call['data']['pageIndex'] for call in calls] == ['1', '2']
    assert result.value.point.nav_date == '2026-01-03T00:00:00+00:00' and result.value.point.unit_nav == 1.2
    no_admissions(store)


def test_bad_later_page_cannot_publish_good_latest_from_first_page(monkeypatch, store):
    from tests.test_eastmoney_fund_nav import install, page
    calls = install(monkeypatch, [page(['2026-01-03', '2026-01-02']), page([], total=3)])
    with pytest.raises(ThesisLedgerGatewayError):
        configured(store, 'efinance').execute_request(ThesisLedgerDataRequest('FUND_NAV', '000001.OF'))
    assert len(calls) == 2
    no_admissions(store)


@pytest.mark.parametrize('bad', [0, -1, float('nan'), True, None])
def test_bad_nonlatest_row_is_not_hidden_by_selecting_latest(bad):
    adapter = SimpleNamespace(get_fund_nav_history=lambda _: pd.DataFrame(
        {'日期': ['2026-01-01', '2026-01-03'], '单位净值': [bad, 1.3]}))
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.query(CONTRACT, LatestNavRequest(symbol='000001.OF'), KEY,
            {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}, ReadContext(adapter))
    assert error.value.code == 'invalid_response'


@pytest.mark.parametrize('options', [{'start': '2026-01-01'}, {'end': '2026-01-02'}, {'limit': 1},
    {'timeframe': '1d'}, {'adjustment': 'none'}, {'parameters': {'sdkName': 'anything'}}])
def test_latest_parameters_are_rejected_before_sdk_data_call(store, options):
    calls = []
    runtime = configured(store, 'akshare')
    runtime.adapters['akshare'] = SimpleNamespace(get_fund_nav_history=lambda _: calls.append(True))
    with pytest.raises(ThesisLedgerGatewayError) as error:
        runtime.execute_request(ThesisLedgerDataRequest('FUND_NAV', '000001.OF', **options))
    assert error.value.code == 'invalid_request' and calls == []


def test_new_provider_reuses_model_and_result_identity_without_public_branch():
    point = NavPoint(reported_date='2026-01-03', nav_date='2026-01-03T00:00:00+00:00', unit_nav=1.2)
    def read(query, context):
        return LatestNavResult(symbol=query.symbol, point=point, provider='fixture-nav', upstream_source='fixture',
                               observed_at='2026-01-04T00:00:00+00:00')
    binding = QueryBinding(CONTRACT, ExactKey('CN', 'MUTUAL_FUND', 'FUND_NAV'), 'fixture', 'adapter-v1', 'source-v1', read)
    registry = CapabilityRegistry((SourceDefinition('fixture-nav', lambda: {}, lambda _: object(), (binding,)),))
    target = {'providerId': 'fixture-nav', 'upstreamSource': 'fixture'}
    assert registry.query(CONTRACT, LatestNavRequest(symbol='000001'), KEY, target, ReadContext(None)).point == point
    with pytest.raises(CapabilityContractError) as error:
        registry.query(CONTRACT, LatestNavRequest(symbol='000001'), {**KEY, 'assetType': 'STOCK'}, target, ReadContext(None))
    assert error.value.code == 'unsupported_capability'
    wrong = replace(binding, read=lambda q, c: read(q, c).model_copy(update={'symbol': '000002.OF'}))
    wrong_registry = CapabilityRegistry((SourceDefinition('fixture-nav', lambda: {}, lambda _: object(), (wrong,)),))
    with pytest.raises(CapabilityContractError) as error:
        wrong_registry.query(CONTRACT, LatestNavRequest(symbol='000001'), KEY, target, ReadContext(None))
    assert error.value.code == 'invalid_response'


def test_modified_typed_result_cannot_bypass_result_schema():
    point = NavPoint(reported_date='2026-01-03', nav_date='2026-01-03T00:00:00+00:00', unit_nav=1.2)
    result = LatestNavResult(symbol='000001.OF', point=point, provider='fixture', upstream_source='fixture', observed_at='2026-01-04T00:00:00Z')
    poisoned = result.model_copy(update={'point': point.model_copy(update={'unit_nav': float('nan')})})
    with pytest.raises(ValidationError):
        CONTRACT.validate_result(poisoned)


def test_actual_runtime_and_original_http_keep_wire_shape(monkeypatch, store):
    import akshare as ak
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.thesis_ledger import router_v3
    monkeypatch.setattr(ak, 'fund_open_fund_info_em', lambda **kwargs: pd.DataFrame(
        {'净值日期': ['2026-01-03', '2026-01-01'], '单位净值': [1.3, 1.1]}))
    gateway = ThesisLedgerDataGateway(configured(store, 'akshare'))
    monkeypatch.setattr('src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway', lambda: gateway)
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN', 'synthetic-nav-token')
    monkeypatch.delenv('THESIS_LEDGER_FIXTURE_MODE', raising=False)
    app = FastAPI()
    app.include_router(router_v3)
    with TestClient(app) as client:
        response = client.get('/thesis-ledger/market/fund-nav', params={'symbol': '000001.OF'}, headers={'Authorization': 'Bearer synthetic-nav-token'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {'version', 'symbol', 'unitNav', 'navDate', 'provider', 'fetchedAt', 'freshness', 'fallbackUsed'}
    assert body['unitNav'] == 1.3 and body['navDate'] == '2026-01-03T00:00:00+00:00'
    no_admissions(store)


def test_models_keep_unverified_visibility_and_positive_nav_schema():
    assert LatestNavRequest(symbol='000001').symbol == '000001.OF'
    with pytest.raises(ValidationError):
        LatestNavRequest(symbol='600519.SH')
    with pytest.raises(ValidationError):
        NavPoint(reported_date='2026-01-03', nav_date='2026-01-03T00:00:00Z', unit_nav=True)
    assert LatestNavResult.model_json_schema()['properties']['publication_visibility']['const'] == 'unverified'
