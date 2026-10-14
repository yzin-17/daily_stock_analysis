"""公告精确诊断的来源所有权及迁移前行为合同。"""
from types import SimpleNamespace
import json

import pytest

from data_provider.thesis_ledger_sources import official_states, official_dividends
from src.services.thesis_ledger_exact_probe_read import read_exact_probe
from src.services.thesis_ledger_provider_runtime import ProviderCallError
from src.services.thesis_ledger_trading_status import TradingStatusSourceError


def state_path(source):
    path = official_states.RISK_DATA_PATH if source == official_states.RISK_UPSTREAM_SOURCE else official_states.DATA_PATH
    return official_states.LIFECYCLE_PATHS.get(source, path)


@pytest.mark.parametrize('binding', official_states.SOURCE.bindings)
def test_actual_notice_status_check_uses_each_bound_contract(binding):
    path = state_path(binding.upstream_source)
    before = path.read_bytes()
    pack = json.loads(before)
    runtime = SimpleNamespace(store=SimpleNamespace(effective_policy_v3=lambda: {'revision': 23}))
    args = (runtime, binding.key.route_key(),
            {'providerId': 'official-states', 'upstreamSource': binding.upstream_source}, None)
    assert read_exact_probe(*args) == {'symbol': pack['symbol'], 'start': pack['coverage']['start'], 'end': pack['coverage']['end']}
    assert path.read_bytes() == before


@pytest.mark.parametrize('binding', official_states.LEGACY_BINDINGS)
def test_status_source_owns_path_request_error_and_byte_fence(binding, tmp_path, monkeypatch):
    path = tmp_path / 'selected.json'
    path.write_text(json.dumps({'symbol': '000995.SZ', 'coverage': {'start': '2023-08-16', 'end': '2023-08-21'}}))
    monkeypatch.setattr(official_states, 'DATA_PATH', path)
    monkeypatch.setattr(official_states, 'RISK_DATA_PATH', path)
    monkeypatch.setattr(official_states, 'LIFECYCLE_PATHS', {key: path for key in official_states.LIFECYCLE_PATHS})
    calls = []
    class Reader:
        def __init__(self, selected):
            assert selected == path
        def read(self, request):
            calls.append(request)
    monkeypatch.setattr(official_states, 'OfficialTradingStatusProvider', Reader)
    key = binding.key.route_key()
    target = {'providerId': 'official-states', 'upstreamSource': binding.upstream_source}
    sample = read_exact_probe(SimpleNamespace(), key, target, None)
    assert len(calls) == 1 and calls[0]['routeKey'] == key
    assert calls[0]['routeTarget'] == {**target, 'routeIndex': 0} and calls[0]['requestId'] == 'source-check'
    assert sample['symbol'] == '000995.SZ'
    original = TradingStatusSourceError('not_covered', '原错误')
    def failed(self, request):
        raise original
    monkeypatch.setattr(Reader, 'read', failed)
    with pytest.raises(TradingStatusSourceError) as error:
        read_exact_probe(SimpleNamespace(), key, target, None)
    assert error.value is original
    def changed(self, request):
        path.write_text('{}')
    monkeypatch.setattr(Reader, 'read', changed)
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(SimpleNamespace(), key, target, None)
    assert error.value.code == 'admission_invalid'


@pytest.mark.parametrize('revision', [None, 0, True, '23'])
def test_independent_notice_check_rejects_missing_or_invalid_real_revision(revision, monkeypatch):
    def forbidden(*args):
        raise AssertionError('无有效政策修订不得创建数据读取器')
    monkeypatch.setattr(official_states, 'OfficialTradingStatusProvider', forbidden)
    binding = official_states.BINDINGS[0]
    runtime = SimpleNamespace(store=SimpleNamespace(effective_policy_v3=lambda: {'revision': revision}))
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(runtime, binding.key.route_key(),
                         {'providerId': 'official-states', 'upstreamSource': binding.upstream_source}, None)
    assert error.value.code == 'control_rejected'


def test_independent_notice_check_uses_standard_request_result_and_actual_epoch(monkeypatch):
    from dataclasses import replace
    from src.services.thesis_ledger_capability_registry import CapabilityRegistry
    binding = official_states.BINDINGS[0]
    calls = []
    def read(query, context):
        calls.append(query)
        return binding.read(query, context)
    replaced = replace(binding, read=read)
    bindings = (replaced, *official_states.BINDINGS[1:])
    monkeypatch.setattr(official_states, 'BINDINGS', bindings)
    source = replace(official_states.SOURCE, bindings=bindings + official_states.LEGACY_BINDINGS)
    monkeypatch.setattr('src.services.thesis_ledger_exact_probe_read.CAPABILITY_SOURCES', CapabilityRegistry((source,)))
    target = {'providerId': 'official-states', 'upstreamSource': binding.upstream_source}
    runtime = SimpleNamespace(store=SimpleNamespace(effective_policy_v3=lambda: {'revision': 23}))
    read_exact_probe(runtime, binding.key.route_key(), target, None)
    assert len(calls) == 1 and calls[0].policyRevision == 23
    assert calls[0].routeKey.model_dump() == binding.key.route_key()
    revisions = iter([{'revision': 23}, {'revision': 24}])
    runtime.store.effective_policy_v3 = lambda: next(revisions)
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(runtime, binding.key.route_key(), target, None)
    assert error.value.code == 'stale_revision'
    def invalid_result(query, context):
        return {}
    malformed = replace(binding, read=invalid_result)
    monkeypatch.setattr(official_states, 'BINDINGS', (malformed, *official_states.BINDINGS[1:]))
    runtime.store.effective_policy_v3 = lambda: {'revision': 23}
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(runtime, binding.key.route_key(), target, None)
    assert error.value.code == 'invalid_response'


def test_independent_notice_check_keeps_byte_fence_after_valid_standard_result(tmp_path, monkeypatch):
    from dataclasses import replace
    path = tmp_path / 'notice.json'
    path.write_bytes(official_states.DATA_PATH.read_bytes())
    monkeypatch.setattr(official_states, 'DATA_PATH', path)
    binding = official_states.BINDINGS[0]
    def changed(query, context):
        result = binding.read(query, context)
        path.write_bytes(path.read_bytes() + b' ')
        return result
    monkeypatch.setattr(official_states, 'BINDINGS', (replace(binding, read=changed), *official_states.BINDINGS[1:]))
    runtime = SimpleNamespace(store=SimpleNamespace(effective_policy_v3=lambda: {'revision': 23}))
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(runtime, binding.key.route_key(),
                         {'providerId': 'official-states', 'upstreamSource': binding.upstream_source}, None)
    assert error.value.code == 'admission_invalid'


def test_notice_cash_original_pack_is_read_only():
    path = official_dividends.DATA_PATH
    before = path.read_bytes()
    pack = json.loads(before)
    binding = official_dividends.SOURCE.bindings[0]
    result = read_exact_probe(SimpleNamespace(), binding.key.route_key(),
                             {'providerId': 'official-dividends', 'upstreamSource': binding.upstream_source}, None)
    assert result == {'symbol': pack['symbol'], 'start': pack['coverage']['start'], 'end': pack['coverage']['end']}
    assert path.read_bytes() == before


def test_notice_cash_source_retains_utc_clock_and_byte_fence(tmp_path, monkeypatch):
    from datetime import datetime
    path = tmp_path / 'notice.json'
    path.write_text(json.dumps({'symbol': '510300.SH', 'coverage': {'start': '2023-01-03', 'end': '2023-02-01'}}))
    monkeypatch.setattr(official_dividends, 'DATA_PATH', path)
    calls = []
    class Reader:
        def read(self, *args):
            calls.append(args)
    monkeypatch.setattr(official_dividends, 'OfficialNoticeDividendProvider', Reader)
    binding = official_dividends.SOURCE.bindings[0]
    args = (SimpleNamespace(), binding.key.route_key(),
            {'providerId': 'official-dividends', 'upstreamSource': binding.upstream_source}, None)
    read_exact_probe(*args)
    assert len(calls) == 1 and calls[0][:3] == ('510300.SH', '2023-01-03', '2023-02-01')
    assert datetime.fromisoformat(calls[0][3]).utcoffset().total_seconds() == 0
    def changed(self, *args):
        path.write_text('{}')
    monkeypatch.setattr(Reader, 'read', changed)
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(*args)
    assert error.value.code == 'admission_invalid'
