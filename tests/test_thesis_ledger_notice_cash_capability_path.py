"""公告现金标准路径：真实入口、动态修订和语义隔离。"""

from copy import deepcopy
from dataclasses import replace

import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import CapabilityRegistry, EventBinding
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_event_cash_contract_v1 import CONTRACT, CashRequest, CashResult
from src.services.thesis_ledger_event_execution_context_v1 import EventExecutionContext
from src.services.thesis_ledger_event_v3 import _admitted_state, execute_event_request, EventV3Error
from src.services.thesis_ledger_notice_dividends import OfficialNoticeDividendProvider
from src.services.thesis_ledger_market_v3_adapters import basic_market_price_route
from tests.test_thesis_ledger_notice_events_v3 import configured_client, admit, request, KEY, TARGET


def test_standard_path_keeps_original_wire_bytes_and_single_execution(configured_client):
    _, runtime, store = configured_client
    admit(store)
    payload = request(runtime)
    calls = []
    security = []
    provider = OfficialNoticeDividendProvider()

    def read(*args):
        calls.append(args)
        return provider.read(*args)

    wire = execute_event_request(runtime, payload, reader=read, check_security=lambda: security.append(True))
    assert len(calls) == 1 and security == [True]
    binding = CAPABILITY_SOURCES.resolve(KEY, TARGET)
    assert isinstance(binding, EventBinding) and not basic_market_price_route(KEY, TARGET)
    normalized = {**wire, 'evidence': [{'role': 'cashPublication', **wire['noticeDividendEvidence']}]}
    del normalized['noticeDividendEvidence']
    result = CashResult.model_validate(normalized)
    assert binding.wire_project(result) == wire
    assert result.facts[0].cashAmount == '0.064'
    assert not hasattr(EventExecutionContext(lambda: provider, lambda: {}), 'runtime')


@pytest.mark.parametrize('mutation', ['unit', 'clock', 'source', 'hash', 'revision', 'scope', 'duplicate'])
def test_standard_result_rejects_corrupted_semantics(configured_client, mutation):
    _, runtime, store = configured_client
    admit(store)
    payload = request(runtime)
    wire = execute_event_request(runtime, payload)
    raw = {**wire, 'evidence': [{'role': 'cashPublication', **wire.pop('noticeDividendEvidence')}]}
    raw.pop('noticeDividendEvidence', None)
    if mutation == 'unit':
        raw['facts'][0]['cashAmount'] = 0.064
    elif mutation == 'clock':
        raw['facts'][0]['availableAt'] = '2027-01-01T00:00:00+00:00'
    elif mutation == 'source':
        raw['facts'][0]['provider'] = 'other'
    elif mutation == 'hash':
        raw['evidence'][0]['content'] += '\n'
    elif mutation == 'revision':
        raw['admission']['sourceRevision'] = 'invented'
    elif mutation == 'scope':
        raw['admission']['scopeSymbols'] = ['000001.SZ']
    else:
        raw['facts'].append(deepcopy(raw['facts'][0]))
    source = CAPABILITY_SOURCES.source(TARGET['providerId'])
    binding = source.bindings[0]
    isolated = CapabilityRegistry((replace(source, bindings=(replace(binding, read=lambda _q, _c: raw),)),))
    with pytest.raises(CapabilityContractError) as error:
        isolated.query(CONTRACT, CashRequest.model_validate(payload), KEY, TARGET, None)
    assert error.value.code == 'invalid_response'


def test_security_failure_is_preserved_after_original_lifecycle(configured_client):
    _, runtime, store = configured_client
    admit(store)

    def security():
        raise EventV3Error('security_changed')

    with pytest.raises(EventV3Error) as error:
        execute_event_request(runtime, request(runtime), check_security=security)
    assert error.value.code == 'security_changed'


def test_parameter_rejection_has_zero_source_reads(configured_client):
    _, runtime, store = configured_client
    admit(store)
    payload = request(runtime)
    payload['sdk_method'] = 'arbitrary'
    calls = []
    context = EventExecutionContext(None, lambda: _admitted_state(runtime, payload), reader_override=lambda *args: calls.append(args))
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.query(CONTRACT, payload, KEY, TARGET, context)
    assert error.value.code == 'invalid_request' and not calls


def test_missing_admission_never_constructs_source(configured_client, monkeypatch):
    from src.services import thesis_ledger_event_v3 as execution
    _, runtime, _ = configured_client
    calls = []
    source = CAPABILITY_SOURCES.source(TARGET['providerId'])
    registry = CapabilityRegistry((replace(source, factory=lambda _c: calls.append(True)),))
    monkeypatch.setattr(execution, 'CAPABILITY_SOURCES', registry)
    with pytest.raises(EventV3Error):
        execute_event_request(runtime, request(runtime))
    assert not calls


def test_unavailable_package_stays_registered_without_fake_revision(monkeypatch, tmp_path):
    from src.services import thesis_ledger_notice_dividends as notices
    monkeypatch.setattr(notices, 'DATA_PATH', tmp_path / 'missing.json')
    source = CAPABILITY_SOURCES.source(TARGET['providerId'])
    binding = source.bindings[0]
    registry = CapabilityRegistry((source,))
    assert registry.resolve(KEY, TARGET) is binding
    assert binding.revision_resolver(None) is None
    assert binding.source_revision is None and binding.adapter_revision is None
    with pytest.raises(CapabilityContractError) as error:
        registry.resolve(KEY, TARGET, 'subscribe')
    assert error.value.code == 'unsupported_capability'


def test_original_covered_empty_window_stays_empty_and_complete(configured_client):
    _, runtime, store = configured_client
    admit(store)
    payload = {**request(runtime), 'end': '2023-01-13'}
    result = execute_event_request(runtime, payload)
    assert result['facts'] == [] and result['coverage']['complete'] is True
    assert result['noticeDividendEvidence']['ref'] == result['admission']['evidenceRef']


@pytest.mark.parametrize('native_code, expected', [('invalid_response', 'invalid_response'), ('unavailable', 'upstream_failure')])
def test_native_source_error_semantics_remain(configured_client, native_code, expected):
    from src.services.thesis_ledger_notice_dividends import NoticeDividendError
    _, runtime, store = configured_client
    admit(store)

    def read(*args):
        raise NoticeDividendError(native_code, '隔离来源错误样例')

    with pytest.raises(EventV3Error) as error:
        execute_event_request(runtime, request(runtime), reader=read)
    assert error.value.code == expected
