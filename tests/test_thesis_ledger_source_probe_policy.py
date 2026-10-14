"""来源只读连接诊断沿原SDK、预算与draft零写入合同。"""

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.thesis_ledger_sources.tencent import SOURCE
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, SourceProbePolicy
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerProviderRuntime


def snapshot(provider='hithink', values=None):
    return ProviderCredentialSnapshot.create(provider, 'environment', 'api_key',
        {'apiKey': 'synthetic-probe-key'} if values is None else values, config_version=0, credential_version=0)


def runtime():
    records = []
    reads = []
    store = SimpleNamespace(record_health=lambda *args, **kwargs: records.append((args, kwargs)),
        provider_credential_snapshot=lambda provider: reads.append(provider) or snapshot(provider))
    return ThesisLedgerProviderRuntime(store, clock=lambda: 0), records, reads


@pytest.mark.parametrize('capability', ['REALTIME_QUOTE', 'DAILY_BAR', 'CASH_DISTRIBUTION'])
@pytest.mark.parametrize('failure', [False, True])
def test_hithink_probe_preserves_original_sdk_and_budget(monkeypatch, capability, failure):
    from src.services import thesis_ledger_hithink_probe as sdk
    calls = []

    def quote(**options):
        def fetch(symbol, asset):
            calls.append(('quote', options, symbol, asset))
            if failure:
                raise sdk.HiThinkQuoteError('permission_denied', 'synthetic')
        return SimpleNamespace(fetch_quote=fetch)

    def daily(key, **options):
        def fetch(*args, **kwargs):
            calls.append(('daily', key, options, args, kwargs))
            if failure:
                raise sdk.HiThinkStockError('permission_denied', 'synthetic')
            return pd.DataFrame([{'date': '2020-01-02'}])
        return SimpleNamespace(fetch_daily_bars=fetch)

    def cash(symbol, **options):
        calls.append(('cash', symbol, options))
        if failure:
            raise sdk.HiThinkFundDividendReadError('permission_denied')
    monkeypatch.setattr(sdk, 'HiThinkSnapshotAdapter', quote)
    monkeypatch.setattr(sdk, 'HiThinkStockHistoricalAdapter', daily)
    monkeypatch.setattr(sdk, 'fetch_hithink_fund_dividends', cash)
    executor, records, reads = runtime()
    monkeypatch.setattr(executor, '_adapter', lambda *args, **kwargs: pytest.fail('HiThink原检查不构造通用工厂'))
    if failure:
        with pytest.raises(ProviderCallError) as error:
            executor.smoke('hithink', capability, credential_snapshot=snapshot())
        assert error.value.code == 'permission_denied' and str(error.value) == 'HiThink 连接测试未通过'
        assert error.value.retryable is False
    else:
        result = executor.smoke('hithink', capability, credential_snapshot=snapshot())
        assert result == {'status': 'healthy', 'attempted': True, 'readOnly': True, 'latencyMs': 0}
    assert records == [] and reads == [] and len(calls) == 1
    if capability == 'REALTIME_QUOTE':
        assert calls[0] == ('quote', {'api_key': 'synthetic-probe-key'}, '600519.SH', 'STOCK')
    elif capability == 'DAILY_BAR':
        assert calls[0] == ('daily', 'synthetic-probe-key', {'timeout_seconds': 4.5},
            ('600519.SH', '2020-01-02', '2020-01-02', 'qfq'),
            {'expected_sessions': ['2020-01-02'], 'calendar_revision': 'connection-probe-single-session-v1'})
    else:
        assert calls[0] == ('cash', '510300.SH', {'fund_type': 'exchange',
            'api_key': 'synthetic-probe-key', 'timeout_seconds': 4.5})


@pytest.mark.parametrize('capability,values,code,message', [
    ('DAILY_BAR', {}, 'not_configured', 'HiThink API Key 未配置'),
    ('UNKNOWN', {'apiKey': 'synthetic-probe-key'}, 'unsupported', 'HiThink 不支持该连接测试能力'),
])
def test_probe_errors_preserved_without_draft_writes(capability, values, code, message):
    executor, records, reads = runtime()
    with pytest.raises(ProviderCallError) as error:
        executor.smoke('hithink', capability, credential_snapshot=snapshot(values=values))
    assert error.value.code == code and str(error.value) == message
    assert records == [] and reads == []


def test_legacy_helper_and_stored_snapshot_error_lifecycle_preserved():
    from src.services.thesis_ledger_hithink_probe import probe_hithink_capability
    with pytest.raises(ProviderCallError, match='HiThink 不支持该连接测试能力'):
        probe_hithink_capability('UNKNOWN', snapshot())
    executor, records, reads = runtime()
    with pytest.raises(ProviderCallError) as error:
        executor.smoke('hithink', 'UNKNOWN')
    assert error.value.code == 'unsupported'
    assert reads == ['hithink']
    assert len(records) == 1 and records[0][0] == ('hithink', 'UNKNOWN', 'STOCK')
    assert records[0][1]['error_code'] == 'unsupported'


@pytest.mark.parametrize('invalid', [object(), SourceProbePolicy(None),
    SourceProbePolicy(lambda context: None, capabilities=('UNKNOWN',)),
    SourceProbePolicy(lambda context: None, capabilities=('REALTIME_QUOTE', 'REALTIME_QUOTE')),
    SourceProbePolicy(lambda context: None, capabilities=([],)),
    SourceProbePolicy(lambda context: None, adapter_required='true'),
    SourceProbePolicy(lambda context: None, (('REALTIME_QUOTE', 'ETF'), ('REALTIME_QUOTE', 'ETF'))),
    SourceProbePolicy(lambda context: None, (('CASH_DISTRIBUTION', 'STOCK'),))])
def test_invalid_probe_declaration_rejected(invalid):
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCE, probe_policy=invalid),))


@pytest.mark.parametrize('failure', [False, True])
def test_new_provider_reuses_probe_policy_and_original_health_lifecycle(monkeypatch, failure):
    provider = 'isolated-probe-provider'
    calls = []

    def read(context):
        calls.append(context)
        if failure:
            raise CapabilityContractError('permission_denied', '隔离诊断权限不足')
    source = replace(SOURCE, provider_id=provider,
        probe_policy=SourceProbePolicy(read, (('REALTIME_QUOTE', 'ETF'),)))
    registry = CapabilityRegistry((*CAPABILITY_SOURCES.sources, source))
    monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, '_bindings', registry._bindings)
    monkeypatch.setitem(PROVIDER_MANIFESTS, provider, {**SOURCE.manifest(), 'providerId': provider})
    executor, records, reads = runtime()
    monkeypatch.setattr(executor, '_adapter', lambda *args, **kwargs: pytest.fail('声明诊断无需公共供应商分支'))
    supplied = snapshot(provider)
    if failure:
        with pytest.raises(ProviderCallError) as error:
            executor.smoke(provider, ' realtime_quote ', credential_snapshot=supplied, draft_probe=False)
        assert error.value.code == 'permission_denied' and str(error.value) == '隔离诊断权限不足'
    else:
        assert executor.smoke(provider, ' realtime_quote ', credential_snapshot=supplied,
                              draft_probe=False)['readOnly'] is True
    context, = calls
    assert context.snapshot is supplied and context.capability == 'REALTIME_QUOTE'
    assert context.sample_symbol == '600519'
    assert reads == [] and len(records) == 1
    assert records[0][0] == (provider, 'REALTIME_QUOTE', 'ETF')
    assert records[0][1]['state'] == ('degraded' if failure else 'healthy')
