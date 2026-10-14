"""Tushare现金标准合同、精确时钟和原凭据缓存的实际闭环。"""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_event_cash_contract_v1 import CONTRACT
from src.services.thesis_ledger_event_time_v1 import instant
from src.services.tushare_fund_identity_evidence import _instant as native_instant
from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot
from tests.test_thesis_ledger_tushare_events_v3 import state, call, EVENT_KEY, TUSHARE_TARGET


def standard(wire):
    result = deepcopy(wire)
    result['identityEvidence'] = result.pop('tushareIdentityEvidence')
    return result


def test_common_cash_contract_and_original_wire_roundtrip(state):
    wire = call(state)
    binding = CAPABILITY_SOURCES.resolve(EVENT_KEY, TUSHARE_TARGET)
    assert binding.contract is CONTRACT
    value = CONTRACT.validate_result(standard(wire))
    assert binding.wire_project(value) == wire
    assert value.facts[0].strategyVisibility.kind == 'conservative-day'
    assert value.facts[0].strategyVisibility.visibleDate == '2025-02-02'
    assert value.facts[0].cashAmount == '0.01512345678901234567890123456789'
    assert wire['providerRevision'] != wire['admission']['sourceRevision']
    state.source.assert_called_once()


def test_actual_provider_cache_reuses_snapshot_then_rotates_constructor(state, monkeypatch):
    from data_provider import tushare_fetcher as native
    constructor = Mock(wraps=native.TushareFetcher)
    monkeypatch.setattr(native, 'TushareFetcher', constructor)
    call(state)
    first = state.runtime.adapters['tushare']
    call(state)
    assert state.runtime.adapters['tushare'] is first and first._call_count == 2
    constructor.assert_called_once()
    state.config.tushare_token = 'synthetic-rotated-cache-token'
    snapshot = state.store.provider_credential_snapshot('tushare')
    state.store.record_route_admission_v3(**{**state.options,
        'credential_revision': _provider_credential_revision_from_snapshot(snapshot)})
    state.request['catalogRevision'] = state.runtime.market_route_catalog_v3()['catalogRevision']
    call(state)
    assert state.runtime.adapters['tushare'] is not first
    assert constructor.call_count == 2 and state.source.call_count == 3
    assert state.runtime.adapters['tushare']._call_count == 1


@pytest.mark.parametrize('value', ['1970-01-01T00:00:00Z', '1969-12-31T23:59:59.1Z',
    '2030-01-01T08:00:00.000000001+08:00', '2030-01-01T00:00:00.'+'1'*80+'Z',
    '2030-01-01T00:00:00.'+'9'*1024+'Z'])
def test_standard_clock_equals_original_exact_clock_without_truncation(value):
    assert instant(value) == native_instant(value)


def test_one_nanosecond_validity_is_not_truncated(state):
    raw = standard(call(state))
    raw['fetchedAt'] = '2030-01-01T00:00:00Z'
    raw['admission']['validUntil'] = '2030-01-01T00:00:00.000000001Z'
    value = CONTRACT.validate_result(raw)
    assert value.admission.validUntil == raw['admission']['validUntil']
    raw['fetchedAt'] = raw['admission']['validUntil']
    with pytest.raises(ValueError):
        CONTRACT.validate_result(raw)


@pytest.mark.parametrize('field', ['validFrom', 'recordedAt', 'availableAt'])
def test_one_nanosecond_future_clock_fails_closed(state, field):
    raw = standard(call(state))
    raw['fetchedAt'] = raw['dataAsOf'] = '2030-01-01T00:00:00Z'
    later = '2030-01-01T00:00:00.000000001Z'
    if field == 'availableAt':
        raw['facts'][0][field] = later
    else:
        raw['admission'][field] = later
    with pytest.raises(ValueError):
        CONTRACT.validate_result(raw)


def test_visibility_model_rejects_unknown_or_explicit_null(state):
    raw = standard(call(state))
    raw['facts'][0]['strategyVisibility'] = {'kind': 'inferred', 'visibleDate': '2025-02-01'}
    with pytest.raises(ValueError):
        CONTRACT.validate_result(raw)
    raw['facts'][0]['strategyVisibility'] = None
    with pytest.raises(ValueError):
        CONTRACT.validate_result(raw)
