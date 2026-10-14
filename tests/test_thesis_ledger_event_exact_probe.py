"""事件精确诊断的来源绑定与原凭据/准入生命周期合同。"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from data_provider.thesis_ledger_sources import rqdata, hithink, tushare, akshare
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_event_execution_context_v1 import EventProbeReadContext
from src.services.thesis_ledger_exact_probe_read import read_exact_probe
from src.services.thesis_ledger_provider_runtime import ProviderCallError


def fixture_runtime(tmp_path, provider='rqdata'):
    snapshot = ProviderCredentialSnapshot.create(provider, 'control', 'username_password',
        {'username': 'isolated-user', 'password': 'isolated-secret'}, 3, 2)
    admission = {'admissionState': 'admitted', 'scopeSymbols': ['510300.SH'],
                 'scopeDateFrom': '2023-01-03', 'scopeDateTo': '2023-02-01'}
    store = SimpleNamespace(database_path=str(tmp_path / 'data.db'), provider_credential_snapshot=lambda _: snapshot)
    runtime = SimpleNamespace(store=store, _current_market_v3_admission=lambda key, target: deepcopy(admission))
    return runtime, snapshot, admission


@pytest.mark.parametrize('binding', rqdata.SOURCE.bindings)
def test_rq_probe_binding_keeps_original_mapped_reader_options(binding, tmp_path, monkeypatch):
    assert callable(binding.probe_read)
    runtime, snapshot, admission = fixture_runtime(tmp_path)
    key = binding.key.route_key()
    target = {'providerId': 'rqdata', 'upstreamSource': binding.upstream_source}
    calls = []
    def mapped(kind, symbol, **options):
        calls.append((kind, symbol, options))
    monkeypatch.setattr('src.services.thesis_ledger_rqdata_mapped_read.read_rqdata_mapped_fund_event', mapped)
    sample = read_exact_probe(runtime, key, target, snapshot)
    assert sample == {'symbol': '510300.SH', 'start': '2023-01-03', 'end': '2023-02-01'}
    assert len(calls) == 1 and calls[0][:2] == ('split' if binding.key.capability == 'SPLIT_EVENT' else 'dividend', '510300.SH')
    options = calls[0][2]
    assert options['database_path'] == runtime.store.database_path and options['timeout_seconds'] == 4.5
    assert options['start'] == sample['start'] and options['end'] == sample['end']
    assert datetime.fromisoformat(options['data_as_of']).utcoffset().total_seconds() == 0
    assert options['read_credentials']() == snapshot and options['read_admission']() == admission
    assert callable(options['read_master_key']) and 'build_factory' not in options
    assert set(options) == {'database_path', 'start', 'end', 'data_as_of', 'read_admission',
                            'read_credentials', 'read_master_key', 'timeout_seconds'}


@pytest.mark.parametrize('problem', ['draft', 'unadmitted', 'no_symbols', 'bad_window'])
def test_event_probe_rejects_before_source_read(problem, tmp_path, monkeypatch):
    runtime, snapshot, admission = fixture_runtime(tmp_path)
    binding = rqdata.SOURCE.bindings[0]
    def forbidden(*args, **kwargs):
        raise AssertionError('未核定事件检查不得读取来源')
    monkeypatch.setattr('src.services.thesis_ledger_rqdata_mapped_read.read_rqdata_mapped_fund_event', forbidden)
    if problem == 'draft': snapshot = replace(snapshot, config_version=4)
    elif problem == 'unadmitted': admission['admissionState'] = 'revoked'
    elif problem == 'no_symbols': admission['scopeSymbols'] = []
    else: admission['scopeDateFrom'] = '2023-13-01'
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(runtime, binding.key.route_key(), {'providerId': 'rqdata', 'upstreamSource': 'rqdata'}, snapshot)
    assert error.value.code == ('admission_invalid' if problem == 'bad_window' else 'not_admitted')


def test_event_probe_rechecks_admission_and_does_not_hide_original_exception(tmp_path, monkeypatch):
    runtime, snapshot, admission = fixture_runtime(tmp_path)
    binding = rqdata.SOURCE.bindings[0]
    args = (runtime, binding.key.route_key(), {'providerId': 'rqdata', 'upstreamSource': 'rqdata'}, snapshot)
    def changed(*args, **kwargs): admission['scopeSymbols'] = ['159516.SZ']
    monkeypatch.setattr('src.services.thesis_ledger_rqdata_mapped_read.read_rqdata_mapped_fund_event', changed)
    with pytest.raises(ProviderCallError) as error: read_exact_probe(*args)
    assert error.value.code == 'admission_invalid'
    original = TimeoutError('原预算错误')
    def failed(*args, **kwargs): raise original
    monkeypatch.setattr('src.services.thesis_ledger_rqdata_mapped_read.read_rqdata_mapped_fund_event', failed)
    with pytest.raises(TimeoutError) as error: read_exact_probe(*args)
    assert error.value is original


def test_new_event_provider_declares_probe_without_public_provider_branch(tmp_path, monkeypatch):
    calls = []
    def read(context):
        assert isinstance(context, EventProbeReadContext) and not hasattr(context, 'runtime')
        calls.append(context)
        with pytest.raises(TypeError): context.admission['admissionState'] = 'changed'
    binding = replace(rqdata.SOURCE.bindings[0], probe_read=read)
    source = replace(rqdata.SOURCE, provider_id='isolated-event-source', bindings=(binding,),
                     manifest=lambda: {'providerId': 'isolated-event-source'})
    registry = CapabilityRegistry((source,))
    assert registry.manifests()['isolated-event-source']['providerId'] == source.provider_id
    monkeypatch.setattr('src.services.thesis_ledger_exact_evidence_probe.CAPABILITY_SOURCES', registry)
    runtime, snapshot, admission = fixture_runtime(tmp_path, source.provider_id)
    result = read_exact_probe(runtime, binding.key.route_key(),
                             {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source}, snapshot)
    assert len(calls) == 1 and result['symbol'] == admission['scopeSymbols'][0]
    with pytest.raises(ValueError): CapabilityRegistry((replace(source, bindings=(replace(binding, probe_read='invalid'),)),))
    wrong = replace(binding, probe_read=lambda context: {'raw': '不应透传'})
    monkeypatch.setattr('src.services.thesis_ledger_exact_evidence_probe.CAPABILITY_SOURCES',
                        CapabilityRegistry((replace(source, bindings=(wrong,)),)))
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(runtime, binding.key.route_key(),
                         {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source}, snapshot)
    assert error.value.code == 'invalid_response'


@pytest.mark.parametrize('source,reader', [
    (hithink.SOURCE, 'src.services.thesis_ledger_hithink_mapped_dividend_read.read_hithink_mapped_fund_dividends'),
    (tushare.SOURCE, 'src.services.thesis_ledger_tushare_mapped_read.read_tushare_mapped_fund_dividends'),
])
def test_mapped_cash_probe_keeps_budget_and_tushare_isolated_factory(source, reader, tmp_path, monkeypatch):
    binding = next(item for item in source.bindings if item.key.capability == 'CASH_DISTRIBUTION')
    assert callable(binding.probe_read)
    runtime, snapshot, admission = fixture_runtime(tmp_path, source.provider_id)
    calls, factories = [], []
    adapter = object()
    def factory(provider, **options):
        factories.append((provider, options))
        return adapter
    runtime._adapter = factory
    def mapped(symbol, **options):
        calls.append((symbol, options))
        assert symbol == '510300.SH' and options['timeout_seconds'] == 4.5
        assert options['read_credentials']() == snapshot and options['read_admission']() == admission
        assert options['database_path'] == runtime.store.database_path
        assert datetime.fromisoformat(options['data_as_of']).utcoffset().total_seconds() == 0
        if source.provider_id == 'tushare': assert options['build_fetcher'](snapshot) is adapter
        else: assert 'build_fetcher' not in options
    monkeypatch.setattr(reader, mapped)
    result = read_exact_probe(runtime, binding.key.route_key(),
        {'providerId': source.provider_id, 'upstreamSource': binding.upstream_source}, snapshot)
    assert len(calls) == 1 and result['start'] == admission['scopeDateFrom'] and result['end'] == admission['scopeDateTo']
    if source.provider_id == 'tushare':
        assert factories == [('tushare', {'snapshot': snapshot, 'cache': False, 'probe': True})]
    else: assert factories == []


@pytest.mark.parametrize('capability', ['CASH_DISTRIBUTION', 'SPLIT_EVENT'])
def test_eastmoney_event_probe_keeps_distinct_readers_and_page_boundary(capability, tmp_path, monkeypatch):
    binding = next(item for item in akshare.SOURCE.bindings if item.key.capability == capability)
    assert callable(binding.probe_read)
    runtime, snapshot, admission = fixture_runtime(tmp_path, 'akshare')
    calls = []
    if capability == 'CASH_DISTRIBUTION':
        def cash(symbol, **options): calls.append((symbol, options))
        monkeypatch.setattr('data_provider.eastmoney_fund_dividend_reader.fetch_fund_dividend_observations', cash)
    else:
        def split(path, request, evidence): calls.append((path, request, evidence))
        monkeypatch.setattr('src.services.thesis_ledger_split_event_reader_v3.read_mapped_split_events', split)
    result = read_exact_probe(runtime, binding.key.route_key(),
        {'providerId': 'akshare', 'upstreamSource': binding.upstream_source}, snapshot)
    assert len(calls) == 1
    if capability == 'CASH_DISTRIBUTION':
        assert calls == [('510300.SH', {'start': result['start'], 'end': result['end'], 'max_pages': 2})]
    else:
        assert calls == [(runtime.store.database_path, result, admission)]
