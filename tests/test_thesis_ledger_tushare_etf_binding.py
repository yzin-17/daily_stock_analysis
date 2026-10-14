"""Tushare ETF 原执行入口、实际工厂及来源分页证明回归。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime, ThesisLedgerGatewayError
from tests.test_configured_market_routes import store, apply, no_admissions
from tests.test_thesis_ledger_market_v3_pagination import sample

KEY = {'kind':'bar', 'market':'CN', 'assetType':'ETF', 'capability':'DAILY_BAR', 'timeframe':'1d', 'adjustment':'none'}
TARGET = {'providerId':'tushare', 'upstreamSource':'tushare'}


def request(**changes):
    return ThesisLedgerDataRequest('DAILY_BAR', '159516.SZ', instrument_type='ETF', timeframe='1d',
        adjustment='none', start='2024-01-01', end='2025-01-02', **changes)


def configured(store):
    store.save_provider_config('tushare', {'requestId':'synthetic', 'enabled':True,
        'credentials':{'method':'token', 'values':{'token':'synthetic'}}})
    assert apply(store,KEY,TARGET)['effective']['routes'][0]['targets'][0]['eligible']
    return ThesisLedgerProviderRuntime(store)


def test_actual_factory_two_sdk_partitions_one_execution_preserves_units_and_budget(monkeypatch,store):
    monkeypatch.delenv('TUSHARE_HTTP_URL',raising=False)
    calls=[]
    def post(url, *, json, timeout, allow_redirects):
        calls.append((json,timeout,allow_redirects))
        params=json['params']
        return SimpleNamespace(status_code=200,text=__import__('json').dumps({'code':0,'data':{
            'fields':['ts_code','trade_date','open','high','low','close','vol','amount'],
            'items':[[params['ts_code'],params['start_date'],4,5,3,4,12,23]]}}))
    monkeypatch.setattr('data_provider.tushare_fetcher.requests.post',post)
    runtime=configured(store)
    result=runtime.execute_market_bars_v3(request(),KEY)
    assert result.provider == 'tushare' and result.upstream_source == 'tushare'
    assert len(calls)==2 and [x[0]['params']['start_date'] for x in calls] == ['20240101','20250101']
    assert [x[0]['params']['end_date'] for x in calls] == ['20241231','20250102']
    for payload,timeout,redirects in calls:
        assert payload['api_name']=='fund_daily' and payload['params']['ts_code']=='159516.SZ'
        assert 0 < timeout <=4.5 and redirects is False
    assert result.value['volume'].tolist()==[1200,1200] and result.value['amount'].tolist()==[23000,23000]
    assert result.value.attrs['thesis_ledger_v3_pagination']['pagesFetched']==2
    assert result.value.attrs['fundDailyRetrieval']['tradingCalendarVerified'] is False
    no_admissions(store)


def test_unique_binding_and_unchanged_adapter_source_revisions():
    from src.services.thesis_ledger_market_v3_adapters import _GATED_ROUTE_ADJUSTMENTS
    from src.services.thesis_ledger_market_v3_revisions import _MARKET_V3_ADAPTER_REVISIONS
    binding=CAPABILITY_SOURCES.resolve(KEY,TARGET)
    assert binding.verify_transport and binding.gated
    assert CAPABILITY_SOURCES.source('tushare').cache_verified_daily_factory is True
    assert binding.adapter_revision=='dsa-v3-etf-tushare-fund-daily-none-adapter-v1'
    assert binding.source_revision==binding.protocol=='tushare-fund-daily-date-partitions-366-v1'
    assert ('ETF','tushare','tushare') not in _GATED_ROUTE_ADJUSTMENTS
    assert ('ETF','tushare','tushare') not in _MARKET_V3_ADAPTER_REVISIONS


@pytest.mark.parametrize('field,value',[('pagesFetched',1),('pagesFetched',True),('continuationPending',True),
    ('status','partial'),('protocol','unknown'),('requestedStart','2024-01-02'),('maximumRows',5000)])
def test_bad_source_header_is_not_repaired_by_common_executor(store,field,value):
    frame=sample()
    frame.attrs['thesis_ledger_v3_pagination'][field]=value
    runtime=configured(store);calls=[]
    def read(*args,**kwargs):
        calls.append((args,kwargs));return frame
    runtime.adapters['tushare']=SimpleNamespace(get_daily_data_for_source=read)
    with pytest.raises(ThesisLedgerGatewayError) as error:
        runtime.execute_market_bars_v3(request(),KEY)
    assert error.value.code=='invalid_response' and len(calls)==1
    assert frame.attrs['thesis_ledger_v3_pagination'][field]==value


@pytest.mark.parametrize('mutation',['missing','digest','coverage','unit','rows','window'])
def test_partition_validation_owns_raw_source_metadata(mutation):
    frame=sample();retrieval=frame.attrs['fundDailyRetrieval']
    if mutation=='missing':
        del frame.attrs['fundDailyRetrieval']
    elif mutation=='digest':
        retrieval['revision']='0'*64
    elif mutation=='coverage':
        retrieval['tradingCalendarVerified']=True
    elif mutation=='unit':
        retrieval['nativeAmountUnit']='CNY'
    else:
        retrieval['partitions'][1]['rows' if mutation=='rows' else 'start']=0 if mutation=='rows' else '2025-01-02'
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.daily(request(),KEY,TARGET,
            ReadContext(SimpleNamespace(get_daily_data_for_source=lambda *args,**kwargs:frame)))
    assert error.value.code=='invalid_response'


def test_source_execution_count_is_distinct_from_sdk_partitions_and_input_is_not_mutated():
    frame=sample();before=deepcopy(frame.attrs);calls=[]
    def read(*args,**kwargs):calls.append((args,kwargs));return frame
    result=CAPABILITY_SOURCES.daily(request(),KEY,TARGET,ReadContext(SimpleNamespace(get_daily_data_for_source=read)))
    assert result.pages_fetched==2 and len(calls)==1 and frame.attrs==before
    assert calls[0][0]==('159516.SZ','tushare') and calls[0][1]['timeout_seconds']==4.5


def test_wrong_exchange_is_not_stripped_before_sdk_identity_validation(monkeypatch,store):
    runtime=configured(store);calls=[]
    monkeypatch.setattr('data_provider.tushare_fetcher.requests.post',lambda *args,**kwargs:calls.append(True))
    bad=ThesisLedgerDataRequest('DAILY_BAR','159516.SH',instrument_type='ETF',timeframe='1d',adjustment='none',
                               start='2024-01-01',end='2025-01-02')
    with pytest.raises(ThesisLedgerGatewayError):runtime.execute_market_bars_v3(bad,KEY)
    assert not calls
