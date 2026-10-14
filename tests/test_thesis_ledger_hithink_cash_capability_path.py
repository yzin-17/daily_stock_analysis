"""HiThink现金标准模型与原SDK/身份/日期/单位接缝。"""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_event_cash_contract_v1 import CONTRACT
from src.services.thesis_ledger_event_execution_context_v1 import EventExecutionContext
from src.services.thesis_ledger_event_time_v1 import instant
from src.services.hithink_fund_identity_evidence import _instant as native_instant
from src.services.thesis_ledger_event_v3 import execute_event_request, EventV3Error
from tests.test_thesis_ledger_hithink_events_v3 import state, HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET


def test_original_own_reader_matches_common_cash_contract_and_wire(state):
    state['admit']()
    request = state['request']()
    result = execute_event_request(state['runtime'], request, reader=state['fetch'])
    normalized = deepcopy(result)
    normalized['identityEvidence'] = normalized.pop('hithinkIdentityEvidence')
    binding = CAPABILITY_SOURCES.resolve(HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET)
    assert binding.contract is CONTRACT
    standard = CONTRACT.validate_result(normalized)
    assert binding.wire_project(standard) == result
    assert standard.facts[0].cashAmount == '0.088'
    assert standard.facts[0].recordDate == '2025-06-17' and standard.facts[0].effectiveDate == '2025-06-18'
    assert standard.facts[0].paymentDate == '2025-06-27'
    assert 'strategyVisibility' not in result['facts'][0]
    assert result['providerRevision'] != result['admission']['sourceRevision']
    assert result['coverage']['complete'] is False
    state['fetch'].assert_called_once()
    assert 0 < state['fetch'].call_args.kwargs['timeout_seconds'] <= 30


@pytest.mark.parametrize('mutation', ['currency', 'hash', 'clock', 'visibility', 'amount', 'duplicate'])
def test_bad_source_standardization_rejected_after_single_original_fetch(state, monkeypatch, mutation):
    from src.services import thesis_ledger_hithink_event_v3 as original
    state['admit']()
    response = original._response

    def corrupt(*args, **kwargs):
        raw = response(*args, **kwargs)
        fact = raw['facts'][0]
        if mutation == 'currency':
            fact['currency'] = 'USD'
        elif mutation == 'hash':
            raw['hithinkIdentityEvidence']['content'] += '\n'
        elif mutation == 'clock':
            fact['availableAt'] = '2099-01-01T00:00:00.000000001Z'
        elif mutation == 'visibility':
            fact['strategyVisibility'] = {'kind': 'conservative-day', 'visibleDate': '2025-06-01'}
        elif mutation == 'amount':
            fact['cashAmount'] = 0.088
        else:
            raw['facts'].append(deepcopy(fact))
        return raw

    monkeypatch.setattr(original, '_response', corrupt)
    with pytest.raises(EventV3Error) as error:
        execute_event_request(state['runtime'], state['request'](), reader=state['fetch'])
    assert error.value.code == 'invalid_response'
    state['fetch'].assert_called_once()


def test_bad_parameters_do_not_enter_control_or_source(state):
    calls = Mock(side_effect=AssertionError('不得读取'))
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.query(CONTRACT, {**state['request'](), 'sdk_method': 'arbitrary'},
            HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET, EventExecutionContext(calls, calls))
    assert error.value.code == 'invalid_request'
    calls.assert_not_called()


@pytest.mark.parametrize('value', ['1970-01-01T08:00:00+08:00', '2026-01-01T00:00:00.000000001Z',
    '2026-01-01T00:00:00.'+'5'*1024+'Z'])
def test_shared_clock_preserves_original_hithink_precision(value):
    assert instant(value) == native_instant(value)
