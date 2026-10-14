"""HiThink ETF 标准绑定、源元信息、原工厂及安全错误投影。"""

import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime, ProviderCallError
from tests.test_configured_market_routes import store, apply, no_admissions

KEY={'kind':'bar','market':'CN','assetType':'ETF','capability':'DAILY_BAR','timeframe':'1d','adjustment':'qfq'}
TARGET={'providerId':'hithink','upstreamSource':'fund-market-historical'}


def stable_policy(store):
    return {key:value for key,value in store.effective_policy_v3().items() if key!='appliedAt'}


def request():
    return ThesisLedgerDataRequest('DAILY_BAR','159516.SZ',instrument_type='ETF',timeframe='1d',adjustment='qfq',
                                  start='2026-05-18',end='2026-05-20')


def configured(store):
    store.save_provider_config('hithink',{'requestId':'synthetic','enabled':True,
        'credentials':{'method':'api_key','values':{'apiKey':'synthetic'}}})
    assert apply(store,KEY,TARGET)['effective']['routes'][0]['targets'][0]['eligible']
    return ThesisLedgerProviderRuntime(store)


def test_single_binding_preserves_all_source_contract_facts_without_central_registration():
    from src.services.thesis_ledger_market_v3_adapters import _GATED_ROUTE_ADJUSTMENTS
    from src.services.thesis_ledger_market_v3_revisions import _MARKET_V3_ADAPTER_REVISIONS
    from src.services.thesis_ledger_market_v3_facts import market_source_contract_v3
    binding=CAPABILITY_SOURCES.resolve(KEY,TARGET)
    assert binding.adapter_revision=='dsa-v3-etf-hithink-fund-market-historical-qfq-adapter-v3'
    assert binding.source_revision=='dsa-hithink-etf-request-contract-v1'
    assert binding.protocol=='hithink-etf-single-response-local-v1'
    assert ('ETF','hithink','fund-market-historical') not in _GATED_ROUTE_ADJUSTMENTS
    assert ('ETF','hithink','fund-market-historical') not in _MARKET_V3_ADAPTER_REVISIONS
    facts=market_source_contract_v3('ETF','hithink','fund-market-historical')
    assert facts==binding.source_contract
    assert facts['upstreamPaginationVerified'] is False and facts['upstreamCoverageVerified'] is False
    assert facts['maximumRowsKnown'] is False and facts['upstreamDataRevision'] is None
    assert facts['adjustmentAlgorithmRevision'] is None and facts['requestWindowMaximumYears']==5
    facts['maximumRowsKnown']=True
    assert binding.source_contract['maximumRowsKnown'] is False
    assert not hasattr(ThesisLedgerProviderRuntime,'_fetch_hithink_etf_bars')


@pytest.mark.parametrize('code,retryable',[('quota_unavailable',True),('permission_denied',False),('invalid_response',False)])
def test_actual_factory_native_error_preserves_code_retryability_diagnostics_and_one_call(monkeypatch,store,code,retryable):
    from src.services.thesis_ledger_hithink_etf import HiThinkETFAdapterError
    calls=[]
    def fail(**kwargs):
        calls.append(kwargs)
        raise HiThinkETFAdapterError(code,retryable=retryable,diagnostics={'reason':'synthetic-safe-reason'})
    monkeypatch.setattr('src.services.thesis_ledger_hithink_etf.fetch_hithink_etf_daily_bars',fail)
    runtime=configured(store)
    policy=stable_policy(store)
    with pytest.raises(ProviderCallError) as error:
        runtime.execute_market_bars_v3(request(),KEY)
    assert error.value.code==code and error.value.retryable is retryable
    assert error.value.diagnostics=={'reason':'synthetic-safe-reason'}
    assert len(calls)==1 and calls[0]['api_key']=='synthetic' and calls[0]['timeout_seconds']==4.5
    assert calls[0]['calendar_evidence'].symbol=='159516.SZ'
    assert stable_policy(store)==policy
    no_admissions(store)


@pytest.mark.parametrize('status,code',[(403,'permission_denied'),(429,'quota_unavailable'),(503,'upstream_failure')])
def test_interactive_source_errors_are_not_recaught_as_invalid_response(monkeypatch,store,status,code):
    from types import SimpleNamespace
    monkeypatch.setattr('data_provider.hithink_interactive_daily.requests.get',
                        lambda *args,**kwargs:SimpleNamespace(status_code=status))
    runtime=configured(store)
    query=ThesisLedgerDataRequest('DAILY_BAR','510300.SH',instrument_type='ETF',timeframe='1d',adjustment='qfq',
        start='2020-01-02',end='2020-01-02',parameters={'interactive_chart':True})
    with pytest.raises(ProviderCallError) as error:
        runtime.execute_market_bars_v3(query,KEY)
    assert error.value.code==code
    no_admissions(store)


def test_exact_probe_uses_same_binding_and_independent_snapshot_factory(monkeypatch,store):
    from types import SimpleNamespace
    from src.services.thesis_ledger_exact_probe_read import read_exact_probe, probe_sample
    from tests.test_thesis_ledger_hithink_stock import _item, _date_ms
    runtime=configured(store)
    symbol,start,end=probe_sample(KEY)
    calls=[]
    def get(url,**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200,json=lambda:{'code':0,'data':{
            'thscode':symbol,'interval':'1d','adjust':None,'timestamp':_date_ms(end),'item':[_item(end)]}})
    monkeypatch.setattr('data_provider.hithink_interactive_daily.requests.get',get)
    snapshot=store.provider_credential_snapshot('hithink')
    policy=stable_policy(store);versions=dict(runtime._adapter_versions)
    assert read_exact_probe(runtime,KEY,TARGET,snapshot)=={'symbol':symbol,'start':start,'end':end}
    assert len(calls)==1 and calls[0]['headers']['X-api-key']=='synthetic' and calls[0]['timeout']==4.5
    assert runtime._adapter_versions==versions and stable_policy(store)==policy
    no_admissions(store)
