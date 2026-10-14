"""东财拆分标准闭环与原映射逐字段对照，不更改比例或日期。"""

from dataclasses import replace
import json
from unittest.mock import Mock

import pytest

from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_event_split_contract_v1 import CONTRACT
from src.services.thesis_ledger_event_execution_context_v1 import EventExecutionContext
from src.services.thesis_ledger_event_v3 import execute_event_request, EventV3Error
from src.services.thesis_ledger_split_event_reader_v3 import read_mapped_split_events
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_event_v3_adapters import SPLIT_KEY, SPLIT_REVISIONS, EVENT_TARGET
from tests.test_thesis_ledger_split_event_control_v3 import setup
from tests.test_eastmoney_fund_split_reader import page, ROW


def rebind(runtime, request, path, ratio, record_date):
    bundle = json.loads(path.read_bytes())
    bundle['mappings'][0]['sourceRatioPerUnit'] = ratio
    bundle['mappings'][0]['recordDate'] = record_date
    ref = MappingEvidenceStore(path.parent).put(json.dumps(bundle).encode())
    runtime.store.record_route_admission_v3(
        key=SPLIT_KEY, target=EVENT_TARGET, evidence_ref=ref, evidence_sha256=ref[7:],
        scope_symbols=[request['symbol']], scope_date_from='2025-10-01', scope_date_to='2025-10-31',
        valid_from='2020-01-01T00:00:00Z', valid_until='2099-01-01T00:00:00Z', recorded_by='isolated-test',
        adapter_revision=SPLIT_REVISIONS['adapterRevision'], source_revision=SPLIT_REVISIONS['sourceRevision'],
        credential_revision=SPLIT_REVISIONS['credentialRevision'],
    )
    request['catalogRevision'] = runtime.market_route_catalog_v3()['catalogRevision']


@pytest.mark.parametrize('ratio,record_date', [('2', '2025-10-17'), ('1', None), ('0.5', '2025-10-17'), ('1.123456789012345678901', None)])
def test_actual_mapped_reader_facts_and_bytes_equal_original(setup, ratio, record_date):
    runtime, request, reader, path = setup
    rebind(runtime, request, path, ratio, record_date)
    reader.return_value['observations'][0]['sourceRatioPerUnit'] = ratio
    admission = runtime._current_market_v3_admission(SPLIT_KEY, EVENT_TARGET)
    original = read_mapped_split_events(runtime.store.database_path, request, admission, reader=reader)
    reader.reset_mock()
    result = execute_event_request(runtime, request, reader=reader)
    assert result['facts'] == original['facts'] and result['dateMappingEvidence'] == original['dateMappingEvidence']
    assert result['providerRevision'] == original['providerRevision']
    assert result['facts'][0]['ratio'] == ratio and result['facts'][0]['type'] == 'SPLIT'
    assert ('recordDate' in result['facts'][0]) == (record_date is not None)
    reader.assert_called_once_with(request['symbol'], start='2025-10-17', end='2025-10-20')
    binding = CAPABILITY_SOURCES.resolve(SPLIT_KEY, EVENT_TARGET)
    assert binding.contract is CONTRACT
    assert binding.wire_project(CONTRACT.validate_result(result)) == result
    assert result['coverage'] == {'complete': False, 'reason': 'historical_coverage_unverified'}
    assert 'strategyVisibility' not in result['facts'][0]


def test_original_paging_reaches_standard_route_without_sdk_factory(setup, monkeypatch):
    from data_provider import eastmoney_fund_split_reader as native
    from src.services import thesis_ledger_event_v3 as execution
    runtime, request, _, _ = setup
    calls, factories = [], []
    source = CAPABILITY_SOURCES.source('akshare')
    monkeypatch.setattr(execution, 'CAPABILITY_SOURCES', CapabilityRegistry((replace(source, factory=lambda _c: factories.append(True)),)))

    def read_page(session, **options):
        calls.append(options)
        number = options['page']
        return page([ROW if number == 1 else ['000001', *ROW[1:]]], (2, 1, number))

    monkeypatch.setattr(native, 'read_event_page', read_page)
    result = execute_event_request(runtime, request)
    assert [item['page'] for item in calls] == [1, 2] and factories == []
    assert all(item['data_type'] == '9' for item in calls)
    assert result['facts'][0]['effectiveDate'] == '2025-10-20'
    assert result['facts'][0]['recordDate'] == '2025-10-17'
    assert result['providerRevision'].startswith('mapped-split:')
    assert result['coverage']['complete'] is False


def test_empty_source_observations_remain_unknown(setup):
    runtime, request, reader, _ = setup
    reader.return_value['observations'] = []
    result = execute_event_request(runtime, request, reader=reader)
    assert result['facts'] == [] and result['coverage']['complete'] is False
    assert result['dateMappingEvidence']['ref'] == result['admission']['evidenceRef']
    reader.assert_called_once()


@pytest.mark.parametrize('mutation', ['date', 'record', 'ratio', 'hash', 'proof_role', 'duplicate'])
def test_bad_standard_output_rejected_with_one_source_read(setup, monkeypatch, mutation):
    import data_provider.eastmoney_split_capability as source
    runtime, request, reader, _ = setup
    original = source.read_mapped_split_events

    def corrupt(*args, **kwargs):
        raw = original(*args, **kwargs)
        if mutation == 'date':
            raw['facts'][0]['effectiveDate'] = '2025-10-21'
        elif mutation == 'record':
            raw['facts'][0]['recordDate'] = '2025-10-16'
        elif mutation == 'ratio':
            raw['facts'][0]['ratio'] = '3'
        elif mutation == 'hash':
            raw['dateMappingEvidence']['content'] += '\n'
        elif mutation == 'proof_role':
            raw['dateMappingEvidence']['content'] = '{}'
        else:
            raw['facts'].append(dict(raw['facts'][0]))
        return raw

    monkeypatch.setattr(source, 'read_mapped_split_events', corrupt)
    with pytest.raises(EventV3Error) as error:
        execute_event_request(runtime, request, reader=reader)
    assert error.value.code == 'invalid_response'
    reader.assert_called_once()


def test_bad_parameters_have_zero_lifecycle_calls(setup):
    _, payload, *_ = setup
    calls = Mock(side_effect=AssertionError('不得读取'))
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.query(CONTRACT, {**payload, 'sdk_method': 'arbitrary'}, SPLIT_KEY, EVENT_TARGET,
                                 EventExecutionContext(calls, calls))
    assert error.value.code == 'invalid_request'
    calls.assert_not_called()
