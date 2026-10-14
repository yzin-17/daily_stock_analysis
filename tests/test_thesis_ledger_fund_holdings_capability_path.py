"""持仓真实工厂、年份回看、标准转换及旧摘要/API 的合同测试。"""

from dataclasses import replace
from datetime import datetime
import hashlib
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from pydantic import ValidationError

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_registry import CapabilityRegistry, ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_fund_holdings_contract_v1 import CONTRACT, Holding, HoldingsRequest, HoldingsResult, holdings_digest
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataGateway, ThesisLedgerDataRequest, ThesisLedgerGatewayError, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions

KEY = {'kind':'data', 'market':'CN', 'assetType':'MUTUAL_FUND', 'capability':'FUND_HOLDINGS'}


def configured(store):
    apply(store, KEY, {'providerId':'akshare', 'upstreamSource':'eastmoney'})
    return ThesisLedgerProviderRuntime(store)


def frame(year, *, bad=False):
    return pd.DataFrame({'股票代码':['600519', '000001'], '股票名称':['贵州茅台', '平安银行'],
        '占净值比例':[8.0, 101.0 if bad else 12.0], '季度':[f'{year}年2季度']*2})


@pytest.mark.parametrize('empty', [False, True])
def test_actual_factory_years_standard_weights_observation_and_legacy_digest(monkeypatch, store, empty):
    import akshare as ak
    year = datetime.now(ZoneInfo('Asia/Shanghai')).year
    calls = []
    def read(symbol, date):
        calls.append((symbol, date))
        if empty and int(date) == year:
            return pd.DataFrame()
        return frame(int(date))
    monkeypatch.setattr(ak, 'fund_portfolio_hold_em', read)
    result = configured(store).execute_request(ThesisLedgerDataRequest('FUND_HOLDINGS', '000001.OF'))
    expected_year = year-1 if empty else year
    assert calls == [('000001', str(y)) for y in ([year, year-1] if empty else [year])]
    assert result.value.report_period == f'{expected_year}-Q2'
    expected = [{'symbol':'000001.SZ','name':'平安银行','weight':0.12}, {'symbol':'600519.SH','name':'贵州茅台','weight':0.08}]
    assert [holding.model_dump() for holding in result.value.holdings] == expected
    assert result.value.evidence_version == hashlib.sha256(json.dumps(expected, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert result.value.source_query_year == expected_year and result.value.source_observed_at
    assert result.value.disclosure_date is None and sum(h.weight for h in result.value.holdings) == 0.2
    no_admissions(store)


@pytest.mark.parametrize('bad', ['year', 'weight'])
def test_nonempty_bad_source_never_reads_previous_year(monkeypatch, store, bad):
    import akshare as ak
    year = datetime.now(ZoneInfo('Asia/Shanghai')).year
    calls=[]
    def read(symbol,date):
        calls.append((symbol,date))
        return frame(year-1 if bad == 'year' else year, bad=bad == 'weight')
    monkeypatch.setattr(ak, 'fund_portfolio_hold_em', read)
    with pytest.raises(ThesisLedgerGatewayError):
        configured(store).execute_request(ThesisLedgerDataRequest('FUND_HOLDINGS', '000001.OF'))
    assert calls == [('000001',str(year))]
    no_admissions(store)


@pytest.mark.parametrize('options',[{'start':'2026-01-01'}, {'end':'2026-01-03'}, {'limit':1},
    {'timeframe':'1d'}, {'adjustment':'none'}, {'parameters':{'year':2026}}])
def test_current_parameters_rejected_without_sdk_data_request(store, options):
    calls=[]
    runtime=configured(store)
    runtime.adapters['akshare']=SimpleNamespace(get_fund_holdings=lambda _:calls.append(True))
    with pytest.raises(ThesisLedgerGatewayError) as error:
        runtime.execute_request(ThesisLedgerDataRequest('FUND_HOLDINGS','000001.OF',**options))
    assert error.value.code == 'invalid_request' and calls == []


def test_original_http_uses_standard_result_without_claiming_disclosure(monkeypatch, store):
    import akshare as ak
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.thesis_ledger import router_v3
    year=datetime.now(ZoneInfo('Asia/Shanghai')).year
    monkeypatch.setattr(ak, 'fund_portfolio_hold_em', lambda **kwargs:frame(year))
    gateway=ThesisLedgerDataGateway(configured(store))
    monkeypatch.setattr('src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway',lambda:gateway)
    monkeypatch.setenv('THESIS_LEDGER_DSA_TOKEN','synthetic-holdings-token')
    monkeypatch.delenv('THESIS_LEDGER_FIXTURE_MODE',raising=False)
    app=FastAPI(); app.include_router(router_v3)
    with TestClient(app) as client:
        response=client.get('/thesis-ledger/market/fund-holdings',params={'symbol':'000001.OF'},headers={'Authorization':'Bearer synthetic-holdings-token'})
    assert response.status_code == 200, response.text
    body=response.json()
    assert set(body) == {'version','fundSymbol','reportPeriod','disclosureDate','provider','fetchedAt','evidenceVersion','fallbackUsed','holdings'}
    assert body['disclosureDate'] is None and body['holdings'][0]['weight'] == 0.12
    no_admissions(store)


def test_second_source_structured_basis_points_reuses_contract_without_business_branch():
    raw={'positions':[{'ticker':'MSFT.US','title':'Microsoft','basisPoints':2000}]}
    def read(request,context):
        positions=tuple(Holding(symbol=p['ticker'],name=p['title'],weight=p['basisPoints']/10000) for p in raw['positions'])
        return HoldingsResult(symbol=request.symbol,report_period='2026-Q2',disclosure_date=None,holdings=positions,
            provider='fixture-holdings',upstream_source='fixture',observed_at='2026-07-01T00:00:00Z',evidence_version=holdings_digest(positions))
    binding=QueryBinding(CONTRACT,ExactKey('CN','MUTUAL_FUND','FUND_HOLDINGS'),'fixture','adapter-v1','source-v1',read)
    source=SourceDefinition('fixture-holdings',lambda:{},lambda _:object(),(binding,))
    registry=CapabilityRegistry((source,))
    target={'providerId':'fixture-holdings','upstreamSource':'fixture'}
    value=registry.query(CONTRACT,HoldingsRequest(symbol='000001'),KEY,target,ReadContext(None))
    assert value.holdings[0].weight == 0.2 and value.holdings[0].symbol == 'MSFT.US'
    wrong=replace(binding,read=lambda r,c:read(r,c).model_copy(update={'symbol':'000002.OF'}))
    with pytest.raises(CapabilityContractError):
        CapabilityRegistry((replace(source,bindings=(wrong,)),)).query(CONTRACT,HoldingsRequest(symbol='000001'),KEY,target,ReadContext(None))


def test_schema_revalidates_malformed_instances_and_does_not_normalize_weights():
    positions=(Holding(symbol='600519.SH',name='贵州茅台',weight=0.08),)
    value=HoldingsResult(symbol='000001.OF',report_period='2026-Q2',disclosure_date=None,holdings=positions,
        provider='fixture',upstream_source='fixture',observed_at='2026-07-01T00:00:00Z',evidence_version=holdings_digest(positions))
    assert sum(holding.weight for holding in value.holdings) == 0.08
    with pytest.raises(ValidationError):
        CONTRACT.validate_result(value.model_copy(update={'holdings':(positions[0].model_copy(update={'weight':float('nan')}),)}))
