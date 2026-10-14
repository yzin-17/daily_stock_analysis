"""通用连接诊断的迁移基线：原工厂、参数、凭据读取及草稿零写入。"""

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerProviderRuntime


CASES = [('tushare', 'REALTIME_QUOTE'), ('tickflow', 'REALTIME_QUOTE'),
         ('finnhub', 'DAILY_BAR'), ('alphavantage', 'DAILY_BAR')]


def snapshot(provider):
    return ProviderCredentialSnapshot.create(provider, 'environment', 'api_key',
        {'apiKey': 'isolated-probe-key'}, config_version=0, credential_version=0)


def executor(provider, adapter=None):
    reads, health = [], []
    supplied = snapshot(provider)
    store = SimpleNamespace(database_path='isolated-probe.db',
        provider_credential_snapshot=lambda name: reads.append(name) or supplied,
        record_health=lambda *args, **kwargs: health.append((args, kwargs)))
    runtime = ThesisLedgerProviderRuntime(store, adapters={} if adapter is None else {provider: adapter},
                                         clock=lambda: 0)
    return runtime, supplied, reads, health


def wire_reader(monkeypatch, runtime, provider, capability, *, failure=None):
    calls = []
    frame = pd.DataFrame([dict(date='2026-01-02', open=10, high=12, low=9,
                              close=11, volume=2, amount=22)])

    def quote(adapter, name, symbol, asset, upstream, error_type=None):
        calls.append(('quote', adapter, name, symbol, asset, upstream))
        if failure is not None:
            raise failure
        return SimpleNamespace(price=11)

    def daily(symbol, **options):
        calls.append(('daily', symbol, options))
        if failure is not None:
            raise failure
        return frame, 'original-source'

    adapter = SimpleNamespace(get_daily_data=daily)
    monkeypatch.setattr(runtime, '_realtime_quote', quote)
    from src.services import thesis_ledger_quote_dispatch
    monkeypatch.setattr(thesis_ledger_quote_dispatch, 'realtime_quote', quote)
    return adapter, calls, frame


def assert_read(calls, provider, capability, adapter):
    if capability == 'REALTIME_QUOTE':
        assert calls == [('quote', adapter, provider, '600519.SH', 'STOCK', provider)]
    else:
        assert calls == [('daily', 'AAPL.US', {'days': 5, 'strict': True})]


@pytest.mark.parametrize('provider,capability', CASES)
def test_injected_probe_skips_snapshot_and_keeps_original_parameters(monkeypatch, provider, capability):
    runtime, supplied, reads, health = executor(provider)
    adapter, calls, _ = wire_reader(monkeypatch, runtime, provider, capability)
    runtime.adapters[provider] = adapter
    original = runtime._adapter
    adapter_calls = []

    def traced(*args, **kwargs):
        adapter_calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, '_adapter', traced)
    assert runtime.smoke(provider, capability, draft_probe=True)['readOnly'] is True
    assert adapter_calls == [((provider, None), {'cache': False, 'probe': True})]
    assert_read(calls, provider, capability, adapter)
    assert reads == health == []
    assert runtime.adapters == {provider: adapter} and runtime._adapter_versions == {}


@pytest.mark.parametrize('provider,capability', CASES)
@pytest.mark.parametrize('supplied_snapshot', [False, True])
def test_probe_factory_uses_one_snapshot_and_never_publishes_cache(
        monkeypatch, provider, capability, supplied_snapshot):
    runtime, supplied, reads, health = executor(provider)
    adapter, calls, _ = wire_reader(monkeypatch, runtime, provider, capability)
    contexts = []
    source = CAPABILITY_SOURCES.source(provider)
    replacement = replace(source, factory=lambda context: contexts.append(context) or adapter)
    registry = CapabilityRegistry(tuple(replacement if item.provider_id == provider else item
                                        for item in CAPABILITY_SOURCES.sources))
    monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, '_bindings', registry._bindings)
    # 原查询缓存存在时，probe仍另建只读工厂；不替换已发布实例及版本。
    old = object()
    runtime.adapters[provider] = old
    runtime._adapter_versions[provider] = supplied
    options = {'draft_probe': True}
    if supplied_snapshot:
        options['credential_snapshot'] = supplied
    runtime.smoke(provider, capability, **options)
    context, = contexts
    assert context.snapshot is supplied and context.adapter is None
    assert context.probe is True and context.timeout_seconds == 5
    assert context.database_path == 'isolated-probe.db'
    assert reads == ([] if supplied_snapshot else [provider]) and health == []
    assert runtime.adapters == {provider: old} and runtime._adapter_versions == {provider: supplied}
    assert_read(calls, provider, capability, adapter)


@pytest.mark.parametrize('provider,capability', CASES)
@pytest.mark.parametrize('failure,code,message,retryable', [
    (ProviderCallError('permission_denied', '原权限错误'), 'permission_denied', '原权限错误', False),
    (TimeoutError('private upstream detail'), 'network_failure', 'Provider 暂时不可用', True),
])
def test_probe_failure_preserves_classification_and_draft_zero_writes(
        monkeypatch, provider, capability, failure, code, message, retryable):
    runtime, supplied, reads, health = executor(provider)
    adapter, calls, _ = wire_reader(monkeypatch, runtime, provider, capability, failure=failure)
    runtime.adapters[provider] = adapter
    with pytest.raises(ProviderCallError) as caught:
        runtime.smoke(provider, capability, credential_snapshot=supplied)
    assert (caught.value.code, str(caught.value), caught.value.retryable) == (code, message, retryable)
    assert len(calls) == 1 and reads == health == []


@pytest.mark.parametrize('provider', ['finnhub', 'alphavantage'])
@pytest.mark.parametrize('bad_result,code', [(None, 'not_covered'),
    ((pd.DataFrame(),), 'invalid_response'), (pd.DataFrame([{'date': '2026-01-02'}]), 'invalid_response')])
def test_strict_daily_probe_keeps_original_response_validation(monkeypatch, provider, bad_result, code):
    adapter = SimpleNamespace(get_daily_data=lambda *args, **kwargs: bad_result)
    runtime, _, reads, health = executor(provider, adapter)
    with pytest.raises(ProviderCallError) as caught:
        runtime.smoke(provider, 'DAILY_BAR', draft_probe=True)
    assert caught.value.code == code and reads == health == []


@pytest.mark.parametrize('provider', ['tushare', 'tickflow'])
@pytest.mark.parametrize('failure', [None, 'permission_denied', 'invalid_response'])
def test_source_probe_uses_original_exact_quote_binding_and_health(monkeypatch, provider, failure):
    calls = []
    source = CAPABILITY_SOURCES.source(provider)
    adapter = object()

    def read(query, context):
        calls.append((query, context))
        if failure == 'permission_denied':
            raise CapabilityContractError(failure, '原精确绑定权限错误', retryable=True)
        return SimpleNamespace(price=11, pre_close=10, open_price=10, high=12,
                               low=9 if failure is None else 20)

    replacement = replace(source, bindings=tuple(replace(binding, read=read)
        if binding.key.capability == 'REALTIME_QUOTE' else binding for binding in source.bindings))
    registry = CapabilityRegistry(tuple(replacement if item.provider_id == provider else item
                                        for item in CAPABILITY_SOURCES.sources))
    monkeypatch.setattr(CAPABILITY_SOURCES, 'sources', registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, '_bindings', registry._bindings)
    runtime, _, reads, health = executor(provider, adapter)
    if failure is None:
        runtime.smoke(provider, 'REALTIME_QUOTE')
    else:
        with pytest.raises(ProviderCallError) as caught:
            runtime.smoke(provider, 'REALTIME_QUOTE')
        assert caught.value.code == failure
        assert caught.value.retryable is (failure == 'permission_denied')
    query, context = calls[0]
    assert len(calls) == 1 and query.symbol == '600519.SH' and query.instrument_type == 'STOCK'
    assert context.adapter is adapter and reads == []
    assert len(health) == 1 and health[0][0] == (provider, 'REALTIME_QUOTE', 'STOCK')
    assert health[0][1]['state'] == ('healthy' if failure is None else 'degraded')


@pytest.mark.parametrize('provider', ['tushare', 'tickflow'])
def test_unmigrated_daily_probe_keeps_generic_sdk_path(provider):
    calls = []
    frame = pd.DataFrame([dict(date='2026-01-02', open=10, high=12, low=9,
                              close=11, volume=2, amount=22)])
    adapter = SimpleNamespace(get_daily_data=lambda symbol, **options:
                              calls.append((symbol, options)) or frame)
    runtime, _, reads, health = executor(provider, adapter)
    runtime.smoke(provider, 'DAILY_BAR', draft_probe=True)
    assert calls == [('600519', {'days': 5})] and reads == health == []


@pytest.mark.parametrize('changes,message', [
    ({'open': True}, 'Provider 响应字段 open 非法'),
    ({'close': float('inf')}, 'Provider 响应字段 close 非法'),
    ({'volume': -1}, 'Provider 响应字段 volume 非法'),
    ({'amount': 'bad'}, 'Provider 响应字段 amount 非法'),
    ({'high': 10}, 'Provider Bar 最高价非法'),
    ({'low': 11}, 'Provider Bar 最低价非法'),
])
def test_daily_owned_validation_keeps_runtime_safe_error(changes, message):
    from src.services.thesis_ledger_daily_probe_validation import validate_bars
    frame = pd.DataFrame([{**dict(date='2026-01-02', open=10, high=12, low=9,
                                 close=11, volume=2, amount=22), **changes}])
    with pytest.raises(CapabilityContractError) as owned:
        validate_bars(frame)
    with pytest.raises(ProviderCallError) as legacy:
        ThesisLedgerProviderRuntime._validate_bars(frame)
    assert (owned.value.code, str(owned.value)) == ('invalid_response', message)
    assert (legacy.value.code, str(legacy.value)) == ('invalid_response', message)


def test_daily_validation_preserves_frame_identity_order_and_nullable_volume():
    from src.services.thesis_ledger_daily_probe_validation import daily_frame, validate_bars
    frame = pd.DataFrame([dict(date=day, open=10, high=12, low=9, close=11,
                              volume=None, amount=float('nan'))
                          for day in ['2026-01-03', '2026-01-02']])
    assert daily_frame((frame, 'source')) is frame
    assert validate_bars(frame) is frame
    assert ThesisLedgerProviderRuntime._daily_frame((frame, 'source')) is frame
    assert ThesisLedgerProviderRuntime._validate_bars(frame) is frame
    assert frame.date.tolist() == ['2026-01-03', '2026-01-02']
    duplicate = pd.concat([frame.iloc[:1], frame.iloc[:1]])
    with pytest.raises(CapabilityContractError, match='Provider Bar 时间重复或缺失'):
        validate_bars(duplicate)
