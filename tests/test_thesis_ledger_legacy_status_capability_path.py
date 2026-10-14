"""旧聚合七目标标准合同、原证据/未知与真实工厂闭环回归。"""

from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_legacy_status_contract_v1 import CONTRACT, StatusRequest, StatusResult
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime, ProviderCallError
from src.services.thesis_ledger_trading_status import OfficialTradingStatusProvider, DATA_PATH, RISK_DATA_PATH, LIFECYCLE_PATHS
from src.services.thesis_ledger_multimarket_rules import SOURCES, BASE, MultimarketRuleProvider
from src.services.thesis_ledger_baostock_status_source import read_baostock_status_response
from src.services.thesis_ledger_status_cache import HistoricalStatusCache
from tests.test_configured_market_routes import store, apply, no_admissions
from tests.test_thesis_ledger_baostock_status_routes import request as bao_request, value as bao_value
from tests.test_thesis_ledger_baostock_lifecycle import normalize, frame as basic_frame

NOW=datetime(2026,10,12,tzinfo=timezone.utc)
ROWS=list(CAPABILITY_SOURCES.routes('HISTORICAL_TRADING_STATUS'))
DOCUMENT_ROWS=[row for row in ROWS if row[1]['providerId']!='baostock']


def wire(key,target):
    if key['market']=='CN':
        source=target['upstreamSource']
        path=LIFECYCLE_PATHS.get(source,RISK_DATA_PATH if source=='cn-stock-risk-price-limits' else DATA_PATH)
    else:
        path=BASE/SOURCES[(key['market'],key['capability'],target['providerId'],target['upstreamSource'])]
    pack=json.loads(path.read_text())
    return {'contractVersion':3,'consumer':'thesis-ledger','requestId':'legacy-status-standard',
        'routeKey':key,'routeTarget':{**target,'routeIndex':0},'symbol':pack['symbol'],'range':pack['coverage']}


def outer(payload,**changes):
    kwargs=dict(instrument_type='STOCK',start=payload['range']['start'],end=payload['range']['end'],
                request_id=payload['requestId'],parameters=payload)
    kwargs.update(changes)
    return ThesisLedgerDataRequest('HISTORICAL_TRADING_STATUS',payload['symbol'],**kwargs)


@pytest.mark.parametrize('key,target,binding',DOCUMENT_ROWS)
def test_actual_document_factory_preserves_all_original_fields_and_unknowns(store,key,target,binding):
    from src.services.thesis_ledger_current_data_route import _DATA_ADAPTERS,current_data_adapter_revisions
    payload=wire(key,target);apply(store,key,target)
    runtime=ThesisLedgerProviderRuntime(store);adapter=runtime._adapter(target['providerId'])
    cls=OfficialTradingStatusProvider if key['market']=='CN' else MultimarketRuleProvider
    assert isinstance(adapter,cls)
    adapter.clock=lambda:NOW
    expected=cls(clock=lambda:NOW).read(payload)
    result=runtime.execute_request(outer(payload))
    assert result.value==expected and not result.fallback_used and result.route_index==0
    assert (key['market'],'STOCK',key['capability'],target['providerId'],target['upstreamSource']) not in _DATA_ADAPTERS
    assert current_data_adapter_revisions(key,target)['sourceRevision']==binding.source_revision
    if key['market']!='CN':
        assert {item['state'] for item in result.value['days']}=={'unknown'}
        assert result.value['provenance']['granularity']=='intraday'
    no_admissions(store)


def test_actual_baostock_factory_retains_cached_structured_and_separate_basic_dates(monkeypatch,store,tmp_path):
    payload=bao_request();target={k:payload['routeTarget'][k] for k in ('providerId','upstreamSource')}
    apply(store,payload['routeKey'],target)
    calls=[]
    raw={**bao_value(),'basicLifecycle':normalize(basic_frame(out='2024-10-10'))}
    monkeypatch.setattr('src.services.thesis_ledger_baostock_status_source.live_daily_status',lambda *args:calls.append(args) or raw)
    runtime=ThesisLedgerProviderRuntime(store)
    adapter=runtime._adapter('baostock')
    adapter._historical_status_cache=HistoricalStatusCache(tmp_path)
    result=runtime.execute_request(outer(payload)).value
    again=runtime.execute_request(outer({**payload,'requestId':'cached-new-request'})).value
    assert len(calls)==1 and again['requestId']=='cached-new-request'
    assert result['days']==again['days'] and result['basicLifecycle']==again['basicLifecycle']
    assert result['basicLifecycle']['outDate']=='2024-10-10' and 'lastTradingDate' not in result['basicLifecycle']
    life_ref=result['basicLifecycle']['sourceRecordId']
    assert len(result['sourceRecords'])==2 and all(life_ref not in day.get('sourceRecordIds',[]) for day in result['days'])
    assert result['structuredCoverage']['complete'] is False and result['days'][1]['state']=='unknown'
    no_admissions(store)


@pytest.mark.parametrize('mutation',['unknown_reference','coarse_without_record','precise_without_document','gap','coverage_complete','coverage_st','basic_day_ref','basic_method','future_observation'])
def test_structured_and_basic_evidence_cannot_claim_other_roles(mutation):
    payload=bao_request();raw_input={**bao_value(),'basicLifecycle':normalize(basic_frame())}
    raw=read_baostock_status_response(SimpleNamespace(),payload,reader=lambda *args:raw_input,clock=lambda:NOW)
    if mutation=='unknown_reference':raw['days'][1]['sourceRecordIds']=[raw['sourceRecords'][0]['id']]
    elif mutation=='coarse_without_record':del raw['days'][0]['sourceRecordIds']
    elif mutation=='precise_without_document':raw['days'][0]['listingPhase']='listed'
    elif mutation=='gap':raw['days'].pop()
    elif mutation=='coverage_complete':raw['structuredCoverage']['complete']=True
    elif mutation=='coverage_st':raw['structuredCoverage']['stUnknownDates']=[]
    elif mutation=='basic_day_ref':raw['days'][0]['sourceRecordIds']=[raw['basicLifecycle']['sourceRecordId']]
    elif mutation=='basic_method':raw['sourceRecords'][1]['queryMethod']='invented-method'
    else:raw['sourceRecords'][0]['observedAt']='2027-01-01T00:00:00+00:00'
    target={k:payload['routeTarget'][k] for k in ('providerId','upstreamSource')}
    monkey_adapter=SimpleNamespace()
    from unittest.mock import patch
    with patch('src.services.thesis_ledger_baostock_status_source.read_baostock_status_response',return_value=raw):
        with pytest.raises(CapabilityContractError) as error:
            CAPABILITY_SOURCES.query(CONTRACT,StatusRequest.model_validate(payload),payload['routeKey'],target,ReadContext(monkey_adapter))
    assert error.value.code=='invalid_response'


@pytest.mark.parametrize('mutation',['timezone','daily_granularity','false_trading'])
def test_intraday_source_must_keep_partial_day_and_market_clock(mutation):
    key,target,_=next(row for row in DOCUMENT_ROWS if row[0]['market']=='HK')
    payload=wire(key,target);raw=MultimarketRuleProvider(clock=lambda:NOW).read(payload)
    if mutation=='timezone':raw['provenance']['timezone']='Asia/Shanghai'
    elif mutation=='daily_granularity':raw['provenance']['granularity']='1d'
    else:
        # 此来源适配器保留原盘中状态语义，不能把仅盘中记录升级为全天可交易。
        raw['days'][0]['state']='trading'
    with pytest.raises(CapabilityContractError):
        CAPABILITY_SOURCES.query(CONTRACT,StatusRequest.model_validate(payload),key,target,ReadContext(SimpleNamespace(read=lambda _:raw)))


@pytest.mark.parametrize('change',['extra','window','id','index'])
def test_request_contract_rejects_bad_input_before_any_source_read(store,change):
    key,target,_=DOCUMENT_ROWS[0];payload=wire(key,target);apply(store,key,target)
    calls=[];runtime=ThesisLedgerProviderRuntime(store,adapters={target['providerId']:SimpleNamespace(read=lambda _:calls.append(True))})
    options={}
    if change=='extra':payload['sdk_method']='arbitrary'
    elif change=='window':options['end']='1900-01-01'
    elif change=='id':options['request_id']='different'
    else:payload['routeTarget'].pop('routeIndex')
    with pytest.raises(ProviderCallError) as error:runtime.execute_request(outer(payload,**options))
    assert error.value.code=='invalid_request' and not calls
