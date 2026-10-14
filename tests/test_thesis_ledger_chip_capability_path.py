"""筹码摘要实际 SDK 工厂、不同算法输入及标准消费回归。"""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_chip_summary_contract_v1 import CONTRACT, ChipRequest
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime, ThesisLedgerGatewayError
from tests.test_configured_market_routes import store, apply, no_admissions

KEY = ChipRequest(symbol='600519.SH').route_key()


def configure(store, provider='akshare'):
    if provider == 'tushare':
        store.save_provider_config(provider, {'requestId':'synthetic', 'enabled':True,
            'credentials':{'method':'token', 'values':{'token':'synthetic'}}})
    apply(store, KEY, {'providerId':provider, 'upstreamSource':'eastmoney' if provider == 'akshare' else provider})
    return ThesisLedgerProviderRuntime(store)


def frame():
    return pd.DataFrame({'日期':['2026-07-01'], '平均成本':[10.0], '获利比例':[0.25],
        '90成本-低':[9.0], '90成本-高':[11.0], '90集中度':[0.1],
        '70成本-低':[9.5], '70成本-高':[10.5], '70集中度':[0.05]})


def test_ak_actual_factory_keeps_single_sdk_call_and_proven_ratio_units(monkeypatch, store):
    import akshare as ak
    from data_provider.akshare_fetcher import AkshareFetcher
    calls, limits = [], []
    monkeypatch.setattr(AkshareFetcher, '_enforce_rate_limit', lambda self:limits.append(True))
    monkeypatch.setattr(AkshareFetcher, '_set_random_user_agent', lambda self:None)
    def read(**kwargs):
        calls.append(kwargs)
        return frame()
    monkeypatch.setattr(ak, 'stock_cyq_em', read)
    runtime = configure(store)
    value = runtime.execute_request(ThesisLedgerDataRequest('CHIP_SUMMARY', '600519.SH')).value
    assert calls == [{'symbol':'600519'}] and limits == [True]
    assert value.profit_ratio == 0.25 and value.concentration90 == 0.1 and value.range70 == (9.5,10.5)
    assert value.source_reported_date == '2026-07-01' and value.currency == 'CNY' and value.quality == 'estimated'
    assert value.methodology == 'eastmoney-price-estimate-v1' and value.identity_basis == 'request_bound'
    no_admissions(store)


@pytest.mark.parametrize('empty', [False, True])
@pytest.mark.parametrize('cache_source', ['control', 'environment'])
def test_tushare_actual_factory_preserves_calendar_cyq_daily_algorithm_and_cache(monkeypatch, store, empty, cache_source):
    from data_provider.tushare_fetcher import TushareFetcher
    monkeypatch.delenv('TUSHARE_HTTP_URL', raising=False)
    monkeypatch.setattr(TushareFetcher, '_get_china_now', lambda self:datetime(2026,7,1,19,1,tzinfo=ZoneInfo('Asia/Shanghai')))
    calls = []
    def post(url, *, json, timeout, allow_redirects):
        calls.append((json,timeout,allow_redirects))
        name = json['api_name']
        if name == 'trade_cal':
            fields, items = ['cal_date','is_open'], [['20260701',1],['20260630',1]]
        elif name == 'cyq_chips':
            fields, items = ['price','percent'], ([] if empty else [[9.0,25.0],[10.0,50.0],[11.0,25.0]])
        elif name == 'daily':
            fields, items = ['close'], [[10.0]]
        else:
            pytest.fail('不应调用其他 SDK 接口')
        return SimpleNamespace(status_code=200,text=__import__('json').dumps({'code':0,'data':{'fields':fields,'items':items}}))
    monkeypatch.setattr('data_provider.tushare_fetcher.requests.post',post)
    runtime = configure(store,'tushare')
    snapshot = store.provider_credential_snapshot('tushare')
    if cache_source == 'environment':
        from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
        snapshot = ProviderCredentialSnapshot.create('tushare','environment','token',
            {'token':'synthetic','httpUrl':'http://api.tushare.pro'},snapshot.config_version,snapshot.credential_version)
        original_snapshot = store.provider_credential_snapshot
        monkeypatch.setattr(store,'provider_credential_snapshot',lambda provider:snapshot if provider == 'tushare' else original_snapshot(provider))
    normal = runtime._adapter('tushare', snapshot=snapshot)
    if empty:
        with pytest.raises(ThesisLedgerGatewayError) as error:
            runtime.execute_request(ThesisLedgerDataRequest('CHIP_SUMMARY','600519.SH'))
        assert error.value.code == 'upstream_unavailable'
    else:
        value = runtime.execute_request(ThesisLedgerDataRequest('CHIP_SUMMARY','600519.SH')).value
        assert value.average_cost == 10 and value.profit_ratio == 0.75 and value.concentration90 == 0.1
        assert value.range90 == (9.0,11.0) and value.source_reported_date == '2026-07-01'
        assert value.methodology == 'tushare-cyq-estimate-v1'
    assert [x[0]['api_name'] for x in calls] == ['trade_cal','cyq_chips'] + ([] if empty else ['daily'])
    for payload,timeout,redirects in calls:
        assert timeout == 30 and redirects is False and payload['token'] == 'synthetic'
        if payload['api_name'] != 'trade_cal':
            assert payload['params'] == {'ts_code':'600519.SH','start_date':'20260701','end_date':'20260701'}
    after = runtime._adapter('tushare', snapshot=snapshot)
    assert (after is normal) == (cache_source == 'environment')
    no_admissions(store)


@pytest.mark.parametrize('options', [{'start':'2026-01-01'}, {'end':'2026-01-03'}, {'limit':1},
    {'timeframe':'1d'}, {'adjustment':'none'}, {'parameters':{'provider_method':'x'}}])
def test_invalid_parameters_do_not_request_sdk_data(store,options):
    runtime = configure(store)
    calls=[]
    runtime.adapters['akshare']=SimpleNamespace(get_chip_distribution=lambda _:calls.append(True))
    with pytest.raises(ThesisLedgerGatewayError) as error:
        runtime.execute_request(ThesisLedgerDataRequest('CHIP_SUMMARY','600519.SH',**options))
    assert error.value.code == 'invalid_request' and not calls


@pytest.mark.parametrize('changes', [{'profit_ratio':50.0}, {'avg_cost':True}, {'avg_cost':float('nan')},
    {'cost_90_low':12.0}, {'code':'000001'}, {'date':'bad-date'}])
def test_adapter_rejects_bad_units_identity_clock_or_range_without_guessing(changes):
    raw = dict(code='600519',date='2026-07-01',avg_cost=10,profit_ratio=0.25,cost_70_low=9.5,cost_70_high=10.5,
               concentration_70=0.05,cost_90_low=9,cost_90_high=11,concentration_90=0.1)
    raw.update(changes)
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.query(CONTRACT,ChipRequest(symbol='600519.SH'),KEY,
            {'providerId':'akshare','upstreamSource':'eastmoney'},
            ReadContext(SimpleNamespace(get_chip_distribution=lambda _:SimpleNamespace(**raw))))
    assert error.value.code == 'invalid_response'


def test_original_api_standard_projection_keeps_fields_and_date_boundary(monkeypatch,store):
    import akshare as ak
    from data_provider.akshare_fetcher import AkshareFetcher
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataGateway
    from api.thesis_ledger import _real_chip
    monkeypatch.setattr(AkshareFetcher,'_enforce_rate_limit',lambda self:None)
    monkeypatch.setattr(AkshareFetcher,'_set_random_user_agent',lambda self:None)
    monkeypatch.setattr(ak,'stock_cyq_em',lambda **kwargs:frame())
    gateway = ThesisLedgerDataGateway(configure(store))
    monkeypatch.setattr('src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway',lambda:gateway)
    body = _real_chip('600519.SH')
    assert set(body) == {'version','symbol','averageCost','profitRatio','range70','range90','concentration',
                        'provider','fallbackUsed','engineVersion','calculatedAt'}
    assert body['profitRatio'] == 0.25 and body['range90'] == [9,11]
    assert body['calculatedAt'].startswith('2026-07-01T00:00:00')


@pytest.mark.parametrize('mode',['exact','draft'])
def test_probe_preserves_independent_factory_cache_and_readonly_policy(monkeypatch,store,mode):
    import akshare as ak
    import data_provider.akshare_fetcher as sdk
    from src.services.thesis_ledger_exact_probe_read import read_exact_probe
    original=sdk.AkshareFetcher
    factories=[]
    def build():
        adapter=original();factories.append(adapter);return adapter
    monkeypatch.setattr(sdk,'AkshareFetcher',build)
    monkeypatch.setattr(original,'_enforce_rate_limit',lambda self:None)
    monkeypatch.setattr(original,'_set_random_user_agent',lambda self:None)
    monkeypatch.setattr(ak,'stock_cyq_em',lambda **kwargs:frame())
    runtime=ThesisLedgerProviderRuntime(store)
    normal=runtime._adapter('akshare');versions=dict(runtime._adapter_versions)
    policy=store.effective_policy_v3();snapshot=store.provider_credential_snapshot('akshare')
    if mode == 'exact':
        assert read_exact_probe(runtime,KEY,{'providerId':'akshare','upstreamSource':'eastmoney'},snapshot) == {'symbol':'600519.SH'}
    else:
        assert runtime.smoke('akshare','CHIP_SUMMARY',credential_snapshot=snapshot)['readOnly']
    assert len(factories)==2 and factories[1] is not normal and runtime.adapters['akshare'] is normal
    assert runtime._adapter_versions == versions and store.effective_policy_v3() == policy
    no_admissions(store)
