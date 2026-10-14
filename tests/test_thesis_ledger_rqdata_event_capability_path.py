"""RQ标准绑定合同与实际账号/身份执行接缝；现金和拆分不混用。"""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import CapabilityRegistry, EventBinding
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_event_cash_contract_v1 import CONTRACT as CASH_CONTRACT
from src.services.thesis_ledger_event_split_contract_v1 import CONTRACT as SPLIT_CONTRACT
from src.services.thesis_ledger_event_execution_context_v1 import EventExecutionContext
from src.services.thesis_ledger_event_v3 import execute_event_request, EventV3Error
from src.services.thesis_ledger_event_v3_adapters import EVENT_KEY, SPLIT_KEY, RQDATA_TARGET
from tests.test_thesis_ledger_rqdata_events_v3 import state, controlled_reader


@pytest.mark.parametrize('key,contract', [(EVENT_KEY, CASH_CONTRACT), (SPLIT_KEY, SPLIT_CONTRACT)])
def test_same_contract_and_original_identity_wire_roundtrip(state, monkeypatch, key, contract):
    _, runtime, admit, request, content, _ = state
    admit(key)
    read = controlled_reader(monkeypatch)
    payload = request(key)
    result = execute_event_request(runtime, payload)
    binding = CAPABILITY_SOURCES.resolve(key, RQDATA_TARGET)
    assert isinstance(binding, EventBinding) and binding.contract is contract
    standardized = contract.validate_result(result)
    assert binding.wire_project(standardized) == result
    assert standardized.identityEvidence.content.encode() == content
    assert result['providerRevision'] != result['admission']['sourceRevision']
    assert result['coverage'] == {'complete': False, 'reason': 'historical_coverage_unverified'}
    assert 'strategyVisibility' not in result['facts'][0]
    read.assert_called_once()
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.resolve(key, RQDATA_TARGET, 'subscribe')
    assert error.value.code == 'unsupported_capability'


@pytest.mark.parametrize('key,mutation', [(EVENT_KEY, 'currency'), (EVENT_KEY, 'hash'),
    (SPLIT_KEY, 'ratio'), (SPLIT_KEY, 'orientation'), (SPLIT_KEY, 'cash'),
    (SPLIT_KEY, 'identity'), (SPLIT_KEY, 'future'), (SPLIT_KEY, 'duplicate')])
def test_corrupted_standardized_output_is_rejected_after_one_original_sdk(state, monkeypatch, key, mutation):
    import data_provider.rqdata_event_capability as source
    _, runtime, admit, request, *_ = state
    admit(key)
    read = controlled_reader(monkeypatch)
    original = source.read_rqdata_mapped_fund_event

    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        fact = result['facts'][0]
        if mutation == 'currency':
            fact['currency'] = 'USD'
        elif mutation == 'hash':
            result['identityEvidence']['content'] += '\n'
        elif mutation == 'ratio':
            fact['ratio'] = '0'
        elif mutation == 'orientation':
            fact['type'] = 'REVERSE_SPLIT'
        elif mutation == 'cash':
            fact['cashAmount'] = '1'
        elif mutation == 'identity':
            fact['provider'] = 'akshare'
        elif mutation == 'future':
            fact['availableAt'] = '2099-01-01T00:00:00Z'
        else:
            result['facts'].append(dict(fact))
        return result

    monkeypatch.setattr(source, 'read_rqdata_mapped_fund_event', corrupt)
    with pytest.raises(EventV3Error) as error:
        execute_event_request(runtime, request(key))
    assert error.value.code == 'invalid_response'
    read.assert_called_once()


@pytest.mark.parametrize('key,contract', [(EVENT_KEY, CASH_CONTRACT), (SPLIT_KEY, SPLIT_CONTRACT)])
def test_invalid_parameters_never_read_control_credentials_or_sdk(state, key, contract):
    _, _, _, request, *_ = state
    payload = {**request(key), 'sdk_method': 'arbitrary'}
    control = Mock(side_effect=AssertionError('不应开始来源生命周期'))
    context = EventExecutionContext(control, control)
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.query(contract, payload, key, RQDATA_TARGET, context)
    assert error.value.code == 'invalid_request'
    control.assert_not_called()


def test_missing_hmac_keeps_declared_revisions_but_does_not_grant_current(state):
    source = CAPABILITY_SOURCES.source('rqdata')
    assert source.manifest()['capabilities'] == {}
    assert source.credentials == (('username_password', ('username', 'password')),)
    for binding in source.bindings:
        assert binding.adapter_revision and binding.source_revision
        assert binding.revision_resolver(None) is None
        assert binding.revision_resolver('not-required') is None
        assert binding.revision_resolver('hmac-sha256-v1:'+'a'*64)['sourceRevision'] == binding.source_revision


def test_invalid_admission_callback_is_rejected():
    source = CAPABILITY_SOURCES.source('rqdata')
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(source, bindings=(replace(source.bindings[0], current_admission='sdk_passthrough'),)),))


@pytest.mark.parametrize('key', [EVENT_KEY, SPLIT_KEY])
@pytest.mark.parametrize('change', ['credentials', 'admission'])
def test_original_http_classifies_rq_guard_rejection_without_publishing_late_facts(state, monkeypatch, key, change):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.thesis_ledger_events_v3 import router
    store, runtime, admit, request, *_ = state
    admit(key)

    def mutate():
        if change == 'credentials':
            store.save_provider_config('rqdata', {'requestId': 'synthetic-guard-rotation',
                'credentials': {'method': 'username_password',
                    'values': {'username': 'new-user', 'password': 'synthetic-sensitive-password'}}})
        else:
            store.revoke_route_admission_v3(key=key, target=RQDATA_TARGET, reason='synthetic-guard-revoke')

    read = controlled_reader(monkeypatch, change=mutate)
    monkeypatch.setattr('src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime', lambda: runtime)
    app = FastAPI()
    app.include_router(router, prefix='/api/v3')
    response = TestClient(app).post('/api/v3/thesis-ledger/market/events', json=request(key),
        headers={'Authorization': 'Bearer synthetic-data-token'})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'not_admitted'
    assert 'facts' not in response.json()
    assert 'synthetic-sensitive-password' not in response.text
    read.assert_called_once()


@pytest.mark.parametrize('provider', ['rqdata', 'fixture-rq-credentials'])
def test_password_bytes_and_new_provider_rules_are_owned_by_source(monkeypatch, provider):
    from src.services import provider_credentials as control
    source = replace(CAPABILITY_SOURCES.source('rqdata'), provider_id=provider)
    monkeypatch.setattr(control, 'CAPABILITY_SOURCES', CapabilityRegistry((source,)))
    monkeypatch.setitem(control.PROVIDER_CREDENTIAL_DEFINITIONS, provider,
                        control.PROVIDER_CREDENTIAL_DEFINITIONS['rqdata'])
    patch = control.parse_credential_patch(provider, {'method': 'username_password',
        'values': {'username': ' synthetic-user ', 'password': ' synthetic password '}})
    assert patch.values == {'username': 'synthetic-user', 'password': ' synthetic password '}
    assert control.parse_credential_patch(provider, {'method': 'username_password',
        'values': {'password': ' \t '}}).values['password'] == ''


def test_bad_source_credential_normalizer_is_rejected_without_persistence(monkeypatch):
    from src.services import provider_credentials as control
    source = replace(CAPABILITY_SOURCES.source('rqdata'), normalize_credential_value=lambda _f, _v: 42)
    monkeypatch.setattr(control, 'CAPABILITY_SOURCES', CapabilityRegistry((source,)))
    with pytest.raises(ValueError, match='必须为字符串'):
        control.parse_credential_patch('rqdata', {'method': 'username_password', 'values': {'password': 'synthetic'}})
