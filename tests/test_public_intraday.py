"""公开分钟上游的字段、空值及富途独立输出通道回归。"""
import json
from types import SimpleNamespace
import pandas as pd
import pytest
from data_provider.tencent_intraday import project_payload
from src.services.thesis_ledger_minute_contract_v1 import MinuteRequest, correlate
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def query(symbol='510300.SH', asset='ETF', period='5m'):
    return MinuteRequest(symbol=symbol,instrument_type=asset,start='2026-11-06',end='2026-11-06',timeframe=period)


def payload():
    return {'code':0,'data':{'sh510300':{'qt':{'sh510300':['1','名称','510300']},
        'm5':[['202611061500','4.124','4.125','4.126','4.12','0',{},'9.667']]}}}


def test_tencent_uses_official_ohlc_order_and_no_invented_amount():
    value=project_payload(payload(),query())
    value=correlate(query(),value,{'upstreamSource':'tencent'})
    assert value.rows[0].close==4.125 and value.rows[0].open==4.124
    assert value.rows[0].amount is None and value.rows[0].volume==0 and value.rows[0].complete is None
    assert value.fieldUnits['volume']=='hand'


@pytest.mark.parametrize('case',['identity','period','ohlc','timestamp'])
def test_tencent_rejects_changed_source_identity_or_fields(case):
    value=payload()
    if case=='identity':value['data']['sh510300']['qt']['sh510300'][2]='600519'
    elif case=='period':value['data']['sh510300']['m60']=value['data']['sh510300'].pop('m5')
    elif case=='ohlc':value['data']['sh510300']['m5'][0][3]='1'
    else:value['data']['sh510300']['m5'][0][0]='bad'
    with pytest.raises(CapabilityContractError):project_payload(value,query())


def test_sina_preserves_unknown_units_and_does_not_fetch_daily(monkeypatch):
    from data_provider.sina_intraday import read_minute
    calls=[]
    values=[{'day':'2026-11-06 15:00:00','open':'4.124','close':'4.124','high':'4.124','low':'4.124','volume':'0'}]
    def get(url,**kwargs):
        calls.append((url,kwargs));return SimpleNamespace(text='callback=('+json.dumps(values)+');',raise_for_status=lambda:None)
    monkeypatch.setattr('data_provider.sina_intraday.requests.get',get)
    value=read_minute(query(period='60m'),SimpleNamespace(timeout_seconds=4.5))
    assert len(calls)==1 and calls[0][1]['params']['scale']=='60' and calls[0][1]['params']['symbol']=='sh510300'
    assert value.rows[0].amount is None and value.fieldUnits['volume']=='unknown'


def test_futu_sdk_noise_is_kept_outside_json_channel(monkeypatch,capsys):
    import sys
    import data_provider.futu_query_worker as worker
    monkeypatch.setitem(sys.modules,'futu',SimpleNamespace())
    request={'host':'127.0.0.1','port':11111,'operation':'minute'}
    monkeypatch.setattr(sys,'stdin',__import__('io').StringIO(json.dumps(request)))
    def read(*args):
        print('SDK connection log')
        frame=pd.DataFrame([{'timestamp':'2026-11-06T10:30:00+08:00','close':4.124}]);frame.attrs['session']='regular';return frame
    monkeypatch.setattr(worker,'read_futu_minute',read)
    worker.main();captured=capsys.readouterr()
    assert captured.err.strip()=='SDK connection log'
    assert len(captured.out.splitlines())==1
    result=json.loads(captured.out.removeprefix(worker.RESULT_PREFIX))
    assert result['rows'][0]['close']==4.124
