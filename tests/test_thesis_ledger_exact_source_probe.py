"""精确诊断按来源绑定声明执行；无供应商前缀也可扩展。"""
from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from data_provider.thesis_ledger_sources import official_rules, official_hk_rules, official_us_rules
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_capability_contracts import ExactProbeReadContext
from src.services.thesis_ledger_exact_probe_read import read_exact_probe
from src.services.thesis_ledger_provider_runtime import ProviderCallError


def test_new_provider_exact_probe_uses_single_binding_without_runtime_factory(monkeypatch):
    calls = []
    def read(context):
        calls.append(context)
        with pytest.raises(TypeError):
            context.target['providerId'] = 'changed'
        return {'symbol': '600519.SH', 'start': '2023-08-28', 'end': '2023-09-01'}
    source = replace(official_rules.SOURCE, provider_id='isolated-fee-source', exact_probe_read=read)
    registry = CapabilityRegistry((source,))
    monkeypatch.setattr('src.services.thesis_ledger_exact_probe_read.CAPABILITY_SOURCES', registry)
    key = source.bindings[0].key.route_key()
    target = {'providerId': source.provider_id, 'upstreamSource': source.bindings[0].upstream_source}
    result = read_exact_probe(SimpleNamespace(), key, target, None)
    assert result['symbol'] == '600519.SH' and len(calls) == 1
    assert not hasattr(calls[0], 'runtime') and isinstance(calls[0], ExactProbeReadContext)


@pytest.mark.parametrize('archived', [False, True])
def test_cn_fees_keep_path_original_reader_and_byte_fence(tmp_path, monkeypatch, archived):
    path = tmp_path / 'selected.json'
    path.write_text(json.dumps({'coverage': {'start': '2023-08-28', 'end': '2023-09-01'}}))
    monkeypatch.setattr(official_rules, 'DATA_PATH', path)
    archive = tmp_path / 'archive.json'
    archive.write_bytes(path.read_bytes())
    monkeypatch.setattr(official_rules, 'ARCHIVE_DATA_PATH', archive)
    requests = []
    class Reader:
        def __init__(self, selected):
            assert selected == (archive if archived else path)
        def read(self, request):
            requests.append(request)
    monkeypatch.setattr(official_rules, 'OfficialPublicFeeProvider', Reader)
    binding = official_rules.SOURCE.bindings[int(archived)]
    key = binding.key.route_key()
    target = {'providerId': 'official-rules', 'upstreamSource': binding.upstream_source}
    result = read_exact_probe(SimpleNamespace(), key, target, None)
    assert result == {'symbol': '600519.SH', 'start': '2023-08-28', 'end': '2023-09-01'}
    assert requests[0]['routeKey'] == key and requests[0]['routeTarget'] == {**target, 'routeIndex': 0}
    assert requests[0]['requestId'] == 'source-check'
    def changed(self, request):
        (archive if archived else path).write_text('{}')
    monkeypatch.setattr(Reader, 'read', changed)
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(SimpleNamespace(), key, target, None)
    assert error.value.code == 'admission_invalid'


def test_exact_probe_declaration_and_output_contract_are_strict(monkeypatch):
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(official_rules.SOURCE, exact_probe_read='invalid'),))
    source = replace(official_rules.SOURCE, exact_probe_read=lambda ctx: {'symbol': '600519.SH'})
    monkeypatch.setattr('src.services.thesis_ledger_exact_probe_read.CAPABILITY_SOURCES', CapabilityRegistry((source,)))
    binding = source.bindings[0]
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(SimpleNamespace(), binding.key.route_key(),
                         {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source}, None)
    assert error.value.code == 'invalid_response'


@pytest.mark.parametrize('binding', official_rules.SOURCE.bindings)
def test_real_local_fee_pack_is_read_without_modification(binding):
    path = official_rules.ARCHIVE_DATA_PATH if binding.upstream_source == official_rules.ARCHIVE_SOURCE else official_rules.DATA_PATH
    before = path.read_bytes()
    pack = json.loads(before)
    result = read_exact_probe(SimpleNamespace(), binding.key.route_key(),
                             {'providerId': 'official-rules', 'upstreamSource': binding.upstream_source}, None)
    assert result['start'] == pack['coverage']['start'] and result['end'] == pack['coverage']['end']
    assert path.read_bytes() == before


@pytest.mark.parametrize('source', [official_hk_rules.SOURCE, official_us_rules.SOURCE])
def test_multimarket_exact_probe_is_owned_by_source(source, tmp_path, monkeypatch):
    module = official_hk_rules if source.provider_id == 'official-hk-rules' else official_us_rules
    path = tmp_path / 'selected.json'
    path.write_text(json.dumps({'symbol': 'GME.US', 'coverage': {'start': '2024-05-01', 'end': '2024-05-02'}}))
    requests = []
    selected = []
    def resolve(key, target):
        assert isinstance(key, dict)
        selected.append((key, target))
        return path
    monkeypatch.setattr(module, 'multimarket_rule_path', resolve)
    class Reader:
        def __init__(self, data_path):
            assert data_path == path
        def read(self, request):
            requests.append(request)
    monkeypatch.setattr(module, 'MultimarketRuleProvider', Reader)
    for binding in source.bindings:
        key = binding.key.route_key()
        target = {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source}
        sample = read_exact_probe(SimpleNamespace(), key, target, None)
        assert sample == {'symbol': 'GME.US', 'start': '2024-05-01', 'end': '2024-05-02'}
        assert selected[-1] == (key, target)
        assert requests[-1] == {'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'source-check',
            'symbol': 'GME.US', 'range': {'start': '2024-05-01', 'end': '2024-05-02'},
            'routeKey': key, 'routeTarget': {**target, 'routeIndex': 0}}
    assert len(requests) == len(source.bindings)
    from src.services.thesis_ledger_public_fees import PublicFeeSourceError
    from src.services.thesis_ledger_trading_status import TradingStatusSourceError
    for binding in source.bindings:
        error_type = PublicFeeSourceError if binding.key.capability == 'HISTORICAL_PUBLIC_FEES' else TradingStatusSourceError
        original = error_type('insufficient_coverage', '原读取错误')
        def failed(self, request):
            raise original
        monkeypatch.setattr(Reader, 'read', failed)
        with pytest.raises(error_type) as error:
            read_exact_probe(SimpleNamespace(), binding.key.route_key(),
                             {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source}, None)
        assert error.value is original
    def changed(self, request):
        path.write_text('{}')
    monkeypatch.setattr(Reader, 'read', changed)
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(SimpleNamespace(), key, target, None)
    assert error.value.code == 'admission_invalid'


@pytest.mark.parametrize('source,binding', [(source, binding)
    for source in (official_hk_rules.SOURCE, official_us_rules.SOURCE) for binding in source.bindings])
def test_multimarket_repository_pack_keeps_original_exact_sample(source, binding):
    module = official_hk_rules if source.provider_id == 'official-hk-rules' else official_us_rules
    key = binding.key.route_key()
    target = {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source}
    path = module.multimarket_rule_path(key, target)
    before = path.read_bytes()
    pack = json.loads(before)
    sample = read_exact_probe(SimpleNamespace(), key, target, None)
    assert sample == {'symbol': pack['symbol'], 'start': pack['coverage']['start'], 'end': pack['coverage']['end']}
    assert path.read_bytes() == before
