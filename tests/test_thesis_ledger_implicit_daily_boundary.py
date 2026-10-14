"""通用日线保留原控制拒绝；显式V3入口独立执行。"""

import pytest

from src.services.thesis_ledger_control import ControlContractError, ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import (
    ThesisLedgerDataGateway, ThesisLedgerDataRequest, ThesisLedgerGatewayError, ThesisLedgerProviderRuntime,
)


def policy():
    return {'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'implicit-boundary',
            'revision': 1, 'enabled': True, 'routes': [{'key': {
                'kind': 'bar', 'market': 'CN', 'assetType': 'ETF', 'capability': 'DAILY_BAR',
                'timeframe': '1d', 'adjustment': 'none'},
                'targets': [{'providerId': 'tencent', 'upstreamSource': 'tencent'}]}]}


@pytest.mark.parametrize('configured', [False, True])
@pytest.mark.parametrize('timeframe', [None, '1d', '1h'])
@pytest.mark.parametrize('entry', ['runtime', 'gateway'])
def test_implicit_daily_never_calls_factory_or_sdk(tmp_path, monkeypatch, configured, timeframe, entry):
    store = ThesisLedgerControlStore(str(tmp_path / 'implicit.sqlite'))
    if configured:
        store.apply_policy_v3(policy())
    runtime = ThesisLedgerProviderRuntime(store)
    monkeypatch.setattr(runtime, '_adapter', lambda *args, **kwargs: pytest.fail('通用日线不能构造工厂'))
    monkeypatch.setattr(runtime, '_execute_with_metadata', lambda *args, **kwargs: pytest.fail('不能进入来源执行'))
    request = ThesisLedgerDataRequest('DAILY_BAR', '510300.SH', instrument_type='ETF', timeframe=timeframe)
    call = runtime.execute_request if entry == 'runtime' else ThesisLedgerDataGateway(runtime).fetch
    with pytest.raises(ThesisLedgerGatewayError) as error:
        call(request)
    if timeframe == '1h':
        assert error.value.code == 'unsupported_capability'
        assert str(error.value) == '当前日线合同只支持 1d bars'
    else:
        assert error.value.code == 'NO_ELIGIBLE_PROVIDER'
        assert str(error.value) == ('当前 Control 未配置对应能力路由' if configured else '当前 Control 路由策略不可用')


def test_control_schema_rejects_data_kind_daily_before_policy_apply(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / 'invalid.sqlite'))
    invalid = policy()
    invalid['routes'][0]['key'] = {'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': 'DAILY_BAR'}
    with pytest.raises(ControlContractError):
        store.apply_policy_v3(invalid)
    assert store.effective_policy_v3() is None
